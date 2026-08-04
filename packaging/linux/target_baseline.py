#!/usr/bin/env python3
"""Capture and validate the exact Debian-like target baseline for one DEB.

The profile is deliberately privacy-minimal: it records OS compatibility facts
and package versions, never host names, user names, addresses, serial numbers,
or customer credentials.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "taiji-target-baseline/v1"
CAPTURE_TOOL_VERSION = 1
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}$")
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DEBIAN_VERSION_RE = re.compile(r"^(?:[0-9]+:)?[0-9][0-9A-Za-z.+:~\-]*$")

TRUSTED_OS_RELEASE_PATH = "/etc/os-release"
TRUSTED_COMMAND_PATHS = {
    "apt-get": "/usr/bin/apt-get",
    "apt-cache": "/usr/bin/apt-cache",
    "dpkg": "/usr/bin/dpkg",
    "dpkg-query": "/usr/bin/dpkg-query",
    "ldd": "/usr/bin/ldd",
    "systemctl": "/usr/bin/systemctl",
    "uname": "/usr/bin/uname",
}
CAPTURE_SUBPROCESS_ENV = {
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
}

TOP_LEVEL_FIELDS = {
    "schema",
    "capture_tool_version",
    "captured_at_utc",
    "profile_id",
    "os_release",
    "architecture",
    "glibc",
    "package_manager",
    "runtime_dependencies",
}


class BaselineError(ValueError):
    pass


def fail(message):
    raise BaselineError(message)


def require_mapping(value, label):
    if not isinstance(value, dict):
        fail("{} must be an object".format(label))
    return value


def require_exact_fields(value, expected, label):
    mapping = require_mapping(value, label)
    actual = set(mapping)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        fail("{} is missing fields: {}".format(label, ", ".join(missing)))
    if unknown:
        fail("{} has unknown fields: {}".format(label, ", ".join(unknown)))
    return mapping


def require_string(value, label, *, allow_empty=False, max_length=512):
    if not isinstance(value, str):
        fail("{} must be a string".format(label))
    if not allow_empty and not value:
        fail("{} must not be empty".format(label))
    if len(value) > max_length or any(ord(char) < 32 for char in value):
        fail("{} contains unsafe text".format(label))
    return value


def load_dependency_names(path):
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        fail("cannot read dependency contract {}: {}".format(path, exc))
    names = []
    for line_number, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not PACKAGE_RE.fullmatch(stripped):
            fail("invalid dependency name at line {}: {}".format(line_number, stripped))
        names.append(stripped)
    if not names:
        fail("dependency contract is empty")
    if names != sorted(set(names)):
        fail("dependency contract must be sorted and contain unique package names")
    return names


def dependency_contract_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_trusted_system_entity(path, label, *, executable=False):
    """Return a fixed system path after checking its root-owned final entity."""

    requested = Path(path)
    if not requested.is_absolute():
        fail("{} must use an absolute trusted system path".format(label))
    try:
        link_metadata = requested.lstat()
        resolved = requested.resolve(strict=True)
        metadata = resolved.stat()
    except (OSError, RuntimeError) as exc:
        fail("trusted system entity is unavailable for {}: {}".format(label, exc))
    if link_metadata.st_uid != 0 or metadata.st_uid != 0:
        fail("trusted system entity is not root-owned: {}".format(label))
    if not stat.S_ISREG(metadata.st_mode):
        fail("trusted system entity is not a regular file: {}".format(label))
    if metadata.st_mode & 0o022:
        fail("trusted system entity is group/world writable: {}".format(label))
    if executable and not metadata.st_mode & 0o111:
        fail("trusted system entity is not executable: {}".format(label))
    return str(requested)


def slug_part(value):
    lowered = value.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned or "unknown"


def compute_profile_id(profile):
    os_release = profile["os_release"]
    architecture = profile["architecture"]
    runtime_dependencies = profile["runtime_dependencies"]
    identity = {
        "schema": profile["schema"],
        "os_release": os_release,
        "architecture": architecture,
        "glibc_version": profile["glibc"]["version"],
        "dependency_contract_sha256": runtime_dependencies["contract_sha256"],
        "packages": sorted(
            [
                {
                    "name": item["name"],
                    "version": item["version"],
                    "architecture": item["architecture"],
                }
                for item in runtime_dependencies["packages"]
            ],
            key=lambda item: item["name"],
        ),
    }
    canonical = json.dumps(
        identity, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()[:12]
    prefix = "{}-{}-{}".format(
        slug_part(os_release["id"]),
        slug_part(os_release["version_id"]),
        slug_part(architecture["dpkg"]),
    )
    room = 63 - len(digest) - 1
    return "{}-{}".format(prefix[:room].rstrip("-"), digest)


def parse_captured_at(value):
    text = require_string(value, "captured_at_utc", max_length=64)
    if not text.endswith("Z"):
        fail("captured_at_utc must use UTC Z notation")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        fail("captured_at_utc is not a valid ISO-8601 timestamp")
    if parsed.tzinfo is None:
        fail("captured_at_utc must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def validate_profile(profile, depends_path, *, max_age_days=None, now=None):
    profile = require_exact_fields(profile, TOP_LEVEL_FIELDS, "target baseline")
    if profile["schema"] != SCHEMA:
        fail("unsupported target baseline schema")
    if type(profile["capture_tool_version"]) is not int:
        fail("capture_tool_version must be an integer")
    if profile["capture_tool_version"] != CAPTURE_TOOL_VERSION:
        fail("unsupported capture_tool_version")

    captured_at = parse_captured_at(profile["captured_at_utc"])
    now = now or datetime.now(timezone.utc)
    if captured_at > now.replace(microsecond=0):
        fail("captured_at_utc is in the future")
    if max_age_days is not None and (now - captured_at).total_seconds() > max_age_days * 86400:
        fail("target baseline capture is older than {} days".format(max_age_days))

    os_release = require_exact_fields(
        profile["os_release"],
        {"id", "id_like", "version_id", "variant_id", "build_id"},
        "os_release",
    )
    for key in ("id", "version_id"):
        value = require_string(os_release[key], "os_release.{}".format(key), max_length=128)
        if not SAFE_ID_RE.fullmatch(value):
            fail("os_release.{} contains unsupported characters".format(key))
    for key in ("variant_id", "build_id"):
        value = require_string(
            os_release[key],
            "os_release.{}".format(key),
            allow_empty=True,
            max_length=128,
        )
        if value and not SAFE_ID_RE.fullmatch(value):
            fail("os_release.{} contains unsupported characters".format(key))
    if not isinstance(os_release["id_like"], list) or not all(
        isinstance(item, str) and SAFE_ID_RE.fullmatch(item)
        for item in os_release["id_like"]
    ):
        fail("os_release.id_like must be a list of safe strings")

    architecture = require_exact_fields(
        profile["architecture"], {"uname_machine", "dpkg"}, "architecture"
    )
    uname_machine = require_string(
        architecture["uname_machine"], "architecture.uname_machine", max_length=32
    )
    dpkg_arch = require_string(architecture["dpkg"], "architecture.dpkg", max_length=32)
    if uname_machine != "x86_64" or dpkg_arch != "amd64":
        fail("target baseline must be amd64/x86_64")

    glibc = require_exact_fields(profile["glibc"], {"version", "banner"}, "glibc")
    glibc_version = require_string(glibc["version"], "glibc.version", max_length=32)
    if not VERSION_RE.fullmatch(glibc_version):
        fail("glibc.version must be a dotted numeric version")
    require_string(glibc["banner"], "glibc.banner", max_length=512)

    package_manager = require_exact_fields(
        profile["package_manager"], {"format", "commands"}, "package_manager"
    )
    if package_manager["format"] != "deb":
        fail("package_manager.format must be deb")
    commands = require_exact_fields(
        package_manager["commands"],
        {"apt-get", "apt-cache", "dpkg", "systemctl"},
        "package_manager.commands",
    )
    for command, available in commands.items():
        if type(available) is not bool:
            fail("package_manager.commands.{} must be boolean".format(command))
        if not available:
            fail("required target command is unavailable: {}".format(command))

    dependencies = require_exact_fields(
        profile["runtime_dependencies"],
        {"contract_sha256", "packages"},
        "runtime_dependencies",
    )
    contract_hash = require_string(
        dependencies["contract_sha256"],
        "runtime_dependencies.contract_sha256",
        max_length=64,
    )
    if not SHA256_RE.fullmatch(contract_hash):
        fail("runtime_dependencies.contract_sha256 must be lowercase SHA-256")
    actual_contract_hash = dependency_contract_sha256(depends_path)
    if contract_hash != actual_contract_hash:
        fail("dependency contract hash does not match the release source")

    expected_names = load_dependency_names(depends_path)
    packages = dependencies["packages"]
    if not isinstance(packages, list):
        fail("runtime_dependencies.packages must be an array")
    package_names = []
    for index, item in enumerate(packages):
        label = "runtime_dependencies.packages[{}]".format(index)
        item = require_exact_fields(
            item, {"name", "status", "version", "architecture"}, label
        )
        name = require_string(item["name"], label + ".name", max_length=128)
        if not PACKAGE_RE.fullmatch(name):
            fail("{}.name is invalid".format(label))
        package_names.append(name)
        status = require_string(item["status"], label + ".status", max_length=64)
        if status != "install ok installed":
            fail("dependency {} is not installed".format(name))
        version = require_string(item["version"], label + ".version", max_length=256)
        if not DEBIAN_VERSION_RE.fullmatch(version):
            fail("{}.version is not a safe Debian version".format(label))
        package_arch = require_string(
            item["architecture"], label + ".architecture", max_length=32
        )
        if package_arch not in ("amd64", "all"):
            fail("dependency {} has unsupported architecture {}".format(name, package_arch))
    if sorted(package_names) != expected_names or len(package_names) != len(set(package_names)):
        fail("target dependency set does not match the release dependency contract")

    profile_id = require_string(profile["profile_id"], "profile_id", max_length=63)
    if not PROFILE_ID_RE.fullmatch(profile_id):
        fail("profile_id contains unsupported characters")
    expected_profile_id = compute_profile_id(profile)
    if profile_id != expected_profile_id:
        fail("profile_id does not match the captured compatibility facts")
    return profile


def parse_os_release(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        if key not in {"ID", "ID_LIKE", "VERSION_ID", "VARIANT_ID", "BUILD_ID"}:
            continue
        try:
            parts = shlex.split(raw_value, posix=True)
        except ValueError as exc:
            fail("cannot parse /etc/os-release {}: {}".format(key, exc))
        values[key] = " ".join(parts)
    if not values.get("ID") or not values.get("VERSION_ID"):
        fail("/etc/os-release must contain ID and VERSION_ID")
    return {
        "id": values["ID"],
        "id_like": values.get("ID_LIKE", "").split(),
        "version_id": values["VERSION_ID"],
        "variant_id": values.get("VARIANT_ID", ""),
        "build_id": values.get("BUILD_ID", ""),
    }


def run_text(command):
    if not command:
        fail("capture command must not be empty")
    executable = validate_trusted_system_entity(
        command[0], "capture command {}".format(command[0]), executable=True
    )
    fixed_command = [executable] + [str(argument) for argument in command[1:]]
    result = subprocess.run(
        fixed_command,
        text=True,
        capture_output=True,
        check=False,
        env=CAPTURE_SUBPROCESS_ENV,
    )
    if result.returncode != 0:
        fail(
            "command failed ({}): {}".format(
                " ".join(fixed_command), result.stderr.strip()
            )
        )
    return result.stdout.strip()


def capture_profile(depends_path, os_release_path=TRUSTED_OS_RELEASE_PATH):
    if os.fspath(os_release_path) != TRUSTED_OS_RELEASE_PATH:
        fail("capture must read the fixed /etc/os-release identity")
    dependency_names = load_dependency_names(depends_path)
    trusted_os_release = validate_trusted_system_entity(
        TRUSTED_OS_RELEASE_PATH, "target OS identity"
    )
    trusted_commands = {
        name: validate_trusted_system_entity(
            path, "target command {}".format(name), executable=True
        )
        for name, path in TRUSTED_COMMAND_PATHS.items()
    }
    os_release = parse_os_release(trusted_os_release)
    dpkg_arch = run_text([trusted_commands["dpkg"], "--print-architecture"])
    ldd_output = run_text([trusted_commands["ldd"], "--version"])
    ldd_lines = ldd_output.splitlines()
    if not ldd_lines:
        fail("ldd --version returned no version banner")
    ldd_banner = ldd_lines[0]
    versions = re.findall(r"(?<![0-9])([0-9]+\.[0-9]+(?:\.[0-9]+)*)", ldd_banner)
    if not versions:
        fail("cannot determine glibc version from ldd --version")

    packages = []
    for name in dependency_names:
        output = run_text(
            [
                trusted_commands["dpkg-query"],
                "-W",
                "-f=${Status}\t${Version}\t${Architecture}",
                name,
            ]
        )
        fields = output.split("\t")
        if len(fields) != 3:
            fail("dpkg-query returned malformed metadata for {}".format(name))
        packages.append(
            {
                "name": name,
                "status": fields[0],
                "version": fields[1],
                "architecture": fields[2],
            }
        )

    profile = {
        "schema": SCHEMA,
        "capture_tool_version": CAPTURE_TOOL_VERSION,
        "captured_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "profile_id": "pending",
        "os_release": os_release,
        "architecture": {
            "uname_machine": run_text([trusted_commands["uname"], "-m"]),
            "dpkg": dpkg_arch,
        },
        "glibc": {"version": versions[-1], "banner": ldd_banner},
        "package_manager": {
            "format": "deb",
            "commands": {
                command: True
                for command in ("apt-get", "apt-cache", "dpkg", "systemctl")
            },
        },
        "runtime_dependencies": {
            "contract_sha256": dependency_contract_sha256(depends_path),
            "packages": packages,
        },
    }
    profile["profile_id"] = compute_profile_id(profile)
    return validate_profile(profile, depends_path, max_age_days=1)


def render_versioned_depends(profile, depends_path, *, max_age_days=None):
    validated = validate_profile(
        profile,
        depends_path,
        max_age_days=max_age_days,
    )
    packages = sorted(
        validated["runtime_dependencies"]["packages"],
        key=lambda item: item["name"],
    )
    return ", ".join(
        "{} (>= {})".format(item["name"], item["version"]) for item in packages
    )


def write_bytes_atomic(output_path, payload):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".{}-".format(output_path.name), dir=str(output_path.parent)
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def write_profile_atomic(profile, output_path):
    output_path = Path(output_path)
    if not output_path.name or any(ord(character) < 32 for character in output_path.name):
        fail("target baseline output filename contains unsafe text")
    payload = (json.dumps(profile, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    write_bytes_atomic(output_path, payload)
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = output_path.with_name(output_path.name + ".sha256")
    sidecar_payload = "{}  {}\n".format(digest, output_path.name).encode("utf-8")
    write_bytes_atomic(sidecar, sidecar_payload)


def load_profile(path):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail("cannot read target baseline {}: {}".format(path, exc))
    return value


def shell_exports(profile):
    values = {
        "TAIJI_BASELINE_PROFILE_ID": profile["profile_id"],
        "TAIJI_BASELINE_OS_ID": profile["os_release"]["id"],
        "TAIJI_BASELINE_OS_VERSION_ID": profile["os_release"]["version_id"],
        "TAIJI_BASELINE_OS_VARIANT_ID": profile["os_release"]["variant_id"],
        "TAIJI_BASELINE_OS_BUILD_ID": profile["os_release"]["build_id"],
        "TAIJI_BASELINE_GLIBC_MIN": profile["glibc"]["version"],
    }
    return "".join(
        "{}={}\n".format(key, shlex.quote(value)) for key, value in values.items()
    )


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="capture the current target")
    capture.add_argument("--depends-file", required=True)
    capture.add_argument("--output", required=True)

    validate = subparsers.add_parser("validate", help="validate a captured target")
    validate.add_argument("--profile", required=True)
    validate.add_argument("--depends-file", required=True)
    validate.add_argument("--max-age-days", type=int)
    validate.add_argument("--print-shell", action="store_true")

    render_depends = subparsers.add_parser(
        "render-depends", help="render target-bound Debian dependency floors"
    )
    render_depends.add_argument("--profile", required=True)
    render_depends.add_argument("--depends-file", required=True)
    render_depends.add_argument("--max-age-days", type=int)
    return parser


def main(argv=None):
    args = make_parser().parse_args(argv)
    if args.command == "capture":
        profile = capture_profile(args.depends_file)
        write_profile_atomic(profile, args.output)
        print("Captured target baseline: {}".format(args.output))
        print("Profile ID: {}".format(profile["profile_id"]))
        return 0
    if args.command == "render-depends":
        profile = load_profile(args.profile)
        print(
            render_versioned_depends(
                profile,
                args.depends_file,
                max_age_days=args.max_age_days,
            )
        )
        return 0
    profile = load_profile(args.profile)
    validate_profile(
        profile,
        args.depends_file,
        max_age_days=args.max_age_days,
    )
    if args.print_shell:
        sys.stdout.write(shell_exports(profile))
    else:
        print("Validated target baseline: {}".format(profile["profile_id"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaselineError as exc:
        print("Target baseline validation failed: {}".format(exc), file=sys.stderr)
        sys.exit(1)
