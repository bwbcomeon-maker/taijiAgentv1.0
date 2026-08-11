#!/usr/bin/env python3
"""Execute the candidate DEB preinst against six controlled negative fixtures."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent


def _source_or_sibling(source_relative: str, sibling_name: str) -> Path:
    source = ROOT / source_relative
    return source if source.is_file() else SCRIPT_DIR / sibling_name


DEFAULT_POLICY = _source_or_sibling(
    "packaging/linux/compatibility-policy.json",
    "compatibility-policy.json",
)
DEFAULT_MATRIX = _source_or_sibling(
    "packaging/linux/certification-matrix.json",
    "certification-matrix.json",
)
POLICY_HELPER = _source_or_sibling(
    "packaging/linux/compatibility_policy.py",
    "compatibility_policy.py",
)
ENVIRONMENT_CONTRACT = _source_or_sibling(
    "tools/taiji-desktop-acceptance/assemble-target-evidence.py",
    "assemble-target-evidence.py",
)
PREINST_TEMPLATE = ROOT / "packaging/linux/deb/preinst"
PREINST_RENDERER = ROOT / "packaging/linux/deb/render-preinst.py"
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")
MAX_DEB_BYTES = 1024 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_CONTROL_BYTES = 32 * 1024 * 1024
MAX_PREINST_BYTES = 2 * 1024 * 1024
BUSINESS_DATA_SCOPE_ID = "taiji-user-and-install-state-v1"
BUSINESS_DATA_INVENTORY_BASENAME = "business-data-inventory.json"
PROTECTED_BUSINESS_PATHS = (
    "home/customer/.config/taiji-agent",
    "home/customer/.config/taiji-agent-desktop",
    "home/customer/.local/share/taiji-agent",
    "home/customer/.local/share/taiji-agent-desktop",
    "home/customer/.local/state/taiji-agent",
    "home/customer/.local/state/taiji-agent-desktop",
    "home/customer/.cache/taiji-agent",
    "home/customer/.cache/taiji-agent-desktop",
    "opt/taiji-agent",
)


class NegativeEvidenceError(ValueError):
    """Raised when controlled negative evidence cannot be produced safely."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NegativeEvidenceError(f"JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _read_regular(path: Path, label: str, *, maximum: int) -> bytes:
    if not path.is_absolute():
        raise NegativeEvidenceError(f"{label} path must be absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise NegativeEvidenceError(f"cannot stat {label}: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or metadata.st_nlink != 1:
        raise NegativeEvidenceError(f"{label} must be a single-link regular file")
    if metadata.st_size <= 0 or metadata.st_size > maximum:
        raise NegativeEvidenceError(f"{label} size is outside the accepted range")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise NegativeEvidenceError(f"cannot read {label}: {path}") from exc
    if len(payload) != metadata.st_size:
        raise NegativeEvidenceError(f"{label} changed while being read")
    return payload


def _parse_json(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NegativeEvidenceError(f"{label} is not strict UTF-8 JSON") from exc
    if type(value) is not dict:
        raise NegativeEvidenceError(f"{label} must be a JSON object")
    return value


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise NegativeEvidenceError(f"cannot load required contract: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _extract_candidate_preinst(deb_payload: bytes) -> bytes:
    stream = io.BytesIO(deb_payload)
    if stream.read(8) != b"!<arch>\n":
        raise NegativeEvidenceError("candidate DEB is not a Debian ar archive")
    control_archives: list[tuple[str, bytes]] = []
    while True:
        header = stream.read(60)
        if header == b"":
            break
        if len(header) != 60 or header[58:60] != b"`\n":
            raise NegativeEvidenceError("candidate DEB has an invalid ar member header")
        try:
            name = header[:16].decode("ascii").strip().rstrip("/")
            size = int(header[48:58].decode("ascii").strip())
        except (UnicodeDecodeError, ValueError) as exc:
            raise NegativeEvidenceError("candidate DEB ar member metadata is invalid") from exc
        if size < 0 or size > MAX_DEB_BYTES:
            raise NegativeEvidenceError("candidate DEB ar member size is invalid")
        member_payload = stream.read(size)
        if len(member_payload) != size:
            raise NegativeEvidenceError("candidate DEB ar member is truncated")
        if size % 2 and len(stream.read(1)) != 1:
            raise NegativeEvidenceError("candidate DEB ar padding is truncated")
        if name.startswith("control.tar"):
            if size > MAX_CONTROL_BYTES:
                raise NegativeEvidenceError("candidate DEB control archive is too large")
            control_archives.append((name, member_payload))
    if len(control_archives) != 1:
        raise NegativeEvidenceError("candidate DEB must contain exactly one control archive")
    try:
        with tarfile.open(fileobj=io.BytesIO(control_archives[0][1]), mode="r:*") as archive:
            candidates = [
                member
                for member in archive.getmembers()
                if member.name in {"preinst", "./preinst"}
            ]
            if len(candidates) != 1:
                raise NegativeEvidenceError("candidate DEB must contain exactly one preinst")
            member = candidates[0]
            if not member.isfile() or member.issym() or member.islnk():
                raise NegativeEvidenceError("candidate DEB preinst must be a regular file")
            if member.size <= 0 or member.size > MAX_PREINST_BYTES or member.mode & 0o111 == 0:
                raise NegativeEvidenceError("candidate DEB preinst mode or size is invalid")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise NegativeEvidenceError("candidate DEB preinst cannot be read")
            payload = extracted.read(MAX_PREINST_BYTES + 1)
    except (tarfile.TarError, OSError) as exc:
        raise NegativeEvidenceError("candidate DEB control archive cannot be parsed") from exc
    if len(payload) != member.size or len(payload) > MAX_PREINST_BYTES or b"\x00" in payload:
        raise NegativeEvidenceError("candidate DEB preinst content is invalid")
    return payload


def _shell_assignment(script: str, name: str) -> str:
    matches = re.findall(rf"^{re.escape(name)}=(.+)$", script, flags=re.MULTILINE)
    if len(matches) != 1:
        raise NegativeEvidenceError(f"candidate preinst must define {name} exactly once")
    try:
        values = shlex.split(matches[0], posix=True)
    except ValueError as exc:
        raise NegativeEvidenceError(f"candidate preinst {name} assignment is invalid") from exc
    if len(values) != 1:
        raise NegativeEvidenceError(f"candidate preinst {name} assignment is invalid")
    return values[0]


def _load_identity(
    deb: Path,
    manifest_path: Path,
    policy_path: Path,
    matrix_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes, Any]:
    deb_payload = _read_regular(deb, "candidate DEB", maximum=MAX_DEB_BYTES)
    manifest = _parse_json(
        _read_regular(manifest_path, "candidate manifest", maximum=MAX_JSON_BYTES),
        "candidate manifest",
    )
    _read_regular(policy_path, "compatibility policy", maximum=MAX_JSON_BYTES)
    matrix = _parse_json(
        _read_regular(matrix_path, "certification matrix", maximum=MAX_JSON_BYTES),
        "certification matrix",
    )
    policy_helper = _load_module(POLICY_HELPER, "taiji_negative_policy_contract")
    try:
        policy = policy_helper.load_and_validate(policy_path)
        policy_sha256 = policy_helper.canonical_sha256(policy)
    except Exception as exc:
        raise NegativeEvidenceError(f"compatibility policy is invalid: {exc}") from exc
    deb_sha256 = _sha256_bytes(deb_payload)
    required = {
        "schema": "taiji-package-manifest/v3",
        "package": "taiji-agent",
        "architecture": "amd64",
        "deb_basename": deb.name,
        "deb_sha256": deb_sha256,
        "compatibility_policy_id": policy["policy_id"],
        "compatibility_policy_sha256": policy_sha256,
    }
    for key, expected in required.items():
        if manifest.get(key) != expected:
            raise NegativeEvidenceError(f"candidate manifest {key} does not match the supplied artifact")
    source_commit = manifest.get("source_commit")
    version = manifest.get("version")
    if type(source_commit) is not str or not COMMIT_RE.fullmatch(source_commit):
        raise NegativeEvidenceError("candidate manifest source_commit is invalid")
    if type(version) is not str or not VERSION_RE.fullmatch(version):
        raise NegativeEvidenceError("candidate manifest version is invalid")
    contract = _load_module(ENVIRONMENT_CONTRACT, "taiji_negative_environment_contract")
    try:
        contract.validate_certification_matrix(matrix)
    except Exception as exc:
        raise NegativeEvidenceError(f"certification matrix is invalid: {exc}") from exc
    preinst = _extract_candidate_preinst(deb_payload)
    renderer = _load_module(PREINST_RENDERER, "taiji_negative_preinst_renderer")
    try:
        template = _read_regular(
            PREINST_TEMPLATE.resolve(),
            "canonical preinst template",
            maximum=MAX_PREINST_BYTES,
        ).decode("utf-8")
        canonical_preinst = renderer.render(template, policy).encode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise NegativeEvidenceError("canonical preinst cannot be rendered") from exc
    if preinst != canonical_preinst:
        raise NegativeEvidenceError(
            "candidate preinst differs from the canonical rendered source; refusing to execute it"
        )
    try:
        preinst_text = preinst.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NegativeEvidenceError("candidate preinst must be UTF-8") from exc
    if _shell_assignment(preinst_text, "TAIJI_POLICY_ID") != policy["policy_id"]:
        raise NegativeEvidenceError("candidate preinst policy ID does not match the manifest")
    if _shell_assignment(preinst_text, "TAIJI_POLICY_SHA256") != policy_sha256:
        raise NegativeEvidenceError("candidate preinst policy hash does not match the manifest")
    return manifest, policy, matrix, preinst, contract


def _write_executable(path: Path, payload: str) -> None:
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o755)


def _prepare_fixture(root: Path, policy: dict[str, Any]) -> Path:
    for relative in (
        "etc",
        "usr/lib",
        "usr/bin",
        "usr/share/xsessions",
        "usr/lib/x86_64-linux-gnu",
        "sys/class/net/lo",
        "opt",
        "home/customer/.local/share/taiji-agent",
    ):
        (root / relative).mkdir(parents=True, mode=0o755, exist_ok=True)
    os_release = root / "usr/lib/os-release"
    os_release.write_text(
        'ID="kylin"\nID_LIKE="debian"\nVERSION_ID="controlled-negative-fixture-v1"\n',
        encoding="utf-8",
    )
    os_release.chmod(0o644)
    (root / "etc/os-release").symlink_to("../usr/lib/os-release")
    for command in ("apt-get", "dpkg", "systemctl"):
        _write_executable(root / "usr/bin" / command, "#!/bin/sh\nexit 0\n")
    for soname in policy["elf"]["required_system_sonames"]:
        (root / "usr/lib/x86_64-linux-gnu" / soname).write_bytes(b"fixture\n")
    (root / ".taiji-disk-headroom-mib").write_text("65536\n", encoding="ascii")
    business_data = root / "home/customer/.local/share/taiji-agent"
    (business_data / "conversations.json").write_text(
        '{"conversations":[{"id":"preserve-me"}]}\n',
        encoding="utf-8",
    )
    return root / "etc/os-release"


def _directory_manifest_sha256(root: Path) -> str:
    root_metadata = root.lstat()
    entries: list[str] = [f"D\0.\0{root_metadata.st_mode & 0o777:o}"]
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if path.is_symlink():
            entries.append(f"L\0{relative}\0{os.readlink(path)}")
        elif path.is_dir():
            entries.append(f"D\0{relative}\0{metadata.st_mode & 0o777:o}")
        elif path.is_file():
            entries.append(f"F\0{relative}\0{metadata.st_mode & 0o777:o}\0{_sha256_bytes(path.read_bytes())}")
        else:
            raise NegativeEvidenceError("business data fixture contains an unsupported file type")
    return _sha256_bytes("\n".join(entries).encode("utf-8"))


def _protected_path_sha256(fixture_root: Path, relative: str) -> str:
    path = fixture_root / relative
    if path.is_symlink():
        return _sha256_bytes(("L\0" + relative + "\0" + os.readlink(path)).encode("utf-8"))
    if not path.exists():
        return _sha256_bytes(("A\0" + relative).encode("utf-8"))
    if path.is_file():
        metadata = path.lstat()
        return _sha256_bytes(
            (
                "F\0%s\0%o\0%s"
                % (relative, metadata.st_mode & 0o777, _sha256_bytes(path.read_bytes()))
            ).encode("utf-8")
        )
    if path.is_dir():
        return _directory_manifest_sha256(path)
    raise NegativeEvidenceError("protected business data path has an unsupported file type")


def _protected_business_inventory(fixture_root: Path) -> list[dict[str, str]]:
    return [
        {"path": relative, "sha256": _protected_path_sha256(fixture_root, relative)}
        for relative in PROTECTED_BUSINESS_PATHS
    ]


def _inventory_digest(entries: list[dict[str, str]]) -> str:
    return _sha256_bytes(_canonical_json({"entries": entries}))


def _remove_commands(root: Path, _policy: dict[str, Any]) -> None:
    (root / "usr/bin/apt-get").unlink()
    (root / "usr/bin/dpkg").unlink()


def _remove_runtime(root: Path, policy: dict[str, Any]) -> None:
    soname = policy["elf"]["required_system_sonames"][0]
    (root / "usr/lib/x86_64-linux-gnu" / soname).unlink()


def _remove_desktop(root: Path, _policy: dict[str, Any]) -> None:
    (root / "usr/share/xsessions").rmdir()


def _no_mutation(_root: Path, _policy: dict[str, Any]) -> None:
    return


SCENARIOS: dict[str, dict[str, Any]] = {
    "arm-blocked": {
        "arch": "arm64",
        "glibc": "2.31",
        "effective_uid_offset": 0,
        "mutate": _no_mutation,
        "observed": "architecture=arm64",
    },
    "rpm-only-blocked": {
        "arch": "amd64",
        "glibc": "2.31",
        "effective_uid_offset": 0,
        "mutate": _remove_commands,
        "observed": "apt-get/dpkg=absent",
    },
    "glibc-below-min-blocked": {
        "arch": "amd64",
        "glibc": "2.30",
        "effective_uid_offset": 0,
        "mutate": _no_mutation,
        "observed": "glibc=2.30",
    },
    "missing-core-capability-blocked": {
        "arch": "amd64",
        "glibc": "2.31",
        "effective_uid_offset": 0,
        "mutate": _remove_runtime,
        "observed": "required-runtime-soname=absent",
    },
    "no-admin-blocked": {
        "arch": "amd64",
        "glibc": "2.31",
        "effective_uid_offset": 1,
        "mutate": _no_mutation,
        "observed": "effective-uid=non-owner",
    },
    "no-graphical-desktop-blocked": {
        "arch": "amd64",
        "glibc": "2.31",
        "effective_uid_offset": 0,
        "mutate": _remove_desktop,
        "observed": "desktop-session-dir=absent",
    },
}


def _run_preflight(
    preinst: Path,
    fixture_root: Path,
    os_release: Path,
    scenario: dict[str, Any],
    result_path: Path,
) -> subprocess.CompletedProcess[str]:
    expected_owner_uid = os.getuid()
    effective_uid = expected_owner_uid + int(scenario["effective_uid_offset"])
    command = (
        'source "$1"; '
        'TAIJI_TEST_EFFECTIVE_UID="$9"; '
        'id() { if [ "${1:-}" = "-u" ]; then printf "%s" "$TAIJI_TEST_EFFECTIVE_UID"; '
        'else command id "$@"; fi; }; '
        'verify_compatibility "$2" "$3" "$4" "$5" "$6" "$7" "$8"'
    )
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            command,
            "taiji-negative-preflight",
            str(preinst),
            str(os_release),
            str(scenario["arch"]),
            str(scenario["glibc"]),
            "5.10.0",
            str(fixture_root),
            str(expected_owner_uid),
            str(result_path),
            str(effective_uid),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": "/nonexistent",
            "LC_ALL": "C",
            "LANG": "C",
        },
    )


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_new(path: Path, payload: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise NegativeEvidenceError(f"failed writing {path}")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def produce(args: argparse.Namespace) -> Path:
    if not CHALLENGE_RE.fullmatch(args.challenge or ""):
        raise NegativeEvidenceError("challenge must be 64-128 lowercase hexadecimal characters")
    if not args.output.is_absolute() or args.output.exists() or args.output.is_symlink():
        raise NegativeEvidenceError("output must be a new absolute directory")
    if args.output.parent.is_symlink() or not args.output.parent.is_dir():
        raise NegativeEvidenceError("output parent must be an existing real directory")
    manifest, policy, matrix, preinst_payload, contract = _load_identity(
        args.deb,
        args.manifest,
        args.policy,
        args.matrix,
    )
    boundaries = {item["id"]: item for item in matrix["negative_boundaries"]}
    if set(boundaries) != set(SCENARIOS):
        raise NegativeEvidenceError("certification matrix negative boundaries do not match the producer")
    output_temp = Path(tempfile.mkdtemp(prefix=f".{args.output.name}.tmp-", dir=args.output.parent))
    work_temp = Path(tempfile.mkdtemp(prefix=".taiji-negative-work-", dir=args.output.parent))
    published = False
    try:
        os.chmod(output_temp, 0o700)
        os.chmod(work_temp, 0o700)
        preinst_path = work_temp / "candidate-preinst"
        _write_new(preinst_path, preinst_payload, mode=0o700)
        session_id = secrets.token_hex(16)
        for category_id in sorted(boundaries):
            boundary = boundaries[category_id]
            scenario = SCENARIOS[category_id]
            fixture_root = work_temp / f"fixture-{category_id}"
            fixture_root.mkdir(mode=0o755)
            os_release = _prepare_fixture(fixture_root, policy)
            mutate = scenario["mutate"]
            if not callable(mutate):
                raise NegativeEvidenceError(f"negative scenario {category_id} is invalid")
            mutate(fixture_root, policy)
            protected_before = _protected_business_inventory(fixture_root)
            business_before = _inventory_digest(protected_before)
            result_path = fixture_root / "var/lib/taiji-agent/preflight.json"
            completed = _run_preflight(
                preinst_path,
                fixture_root,
                os_release,
                scenario,
                result_path,
            )
            if completed.returncode == 0 or not result_path.is_file():
                raise NegativeEvidenceError(f"negative scenario {category_id} did not block")
            preflight_payload = _read_regular(
                result_path.resolve(),
                f"negative preflight result {category_id}",
                maximum=MAX_JSON_BYTES,
            )
            preflight = _parse_json(preflight_payload, f"negative preflight result {category_id}")
            expected_code = boundary["stable_error_code"]
            if preflight.get("error_code") != expected_code:
                raise NegativeEvidenceError(
                    f"negative scenario {category_id} returned error code {preflight.get('error_code')!r}, expected {expected_code}"
                )
            if preflight.get("failed_capabilities") != [expected_code]:
                raise NegativeEvidenceError(f"negative scenario {category_id} did not isolate exactly one error boundary")
            protected_after = _protected_business_inventory(fixture_root)
            business_after = _inventory_digest(protected_after)
            if business_before != business_after:
                raise NegativeEvidenceError(f"negative scenario {category_id} mutated protected business data")
            preflight_sha256 = _sha256_bytes(preflight_payload)
            business_inventory = {
                "schema": "taiji-business-data-inventory/v1",
                "scope_id": BUSINESS_DATA_SCOPE_ID,
                "protected_paths": list(PROTECTED_BUSINESS_PATHS),
                "before": protected_before,
                "after": protected_after,
                "unchanged": True,
            }
            business_inventory_payload = _canonical_json(business_inventory)
            business_inventory_sha256 = _sha256_bytes(business_inventory_payload)
            category_dir = output_temp / category_id
            category_dir.mkdir(mode=0o700)
            _write_new(category_dir / "preflight-result.json", preflight_payload)
            _write_new(
                category_dir / BUSINESS_DATA_INVENTORY_BASENAME,
                business_inventory_payload,
            )
            machine_fingerprint = _sha256_bytes(
                (args.challenge + "\0" + manifest["deb_sha256"] + "\0" + category_id + "\0" + scenario["observed"]).encode("utf-8")
            )
            record = {
                "schema": "taiji-linux-environment-evidence/v2",
                "category_id": category_id,
                "category_kind": "negative",
                "compatibility": "BLOCKED",
                "source_commit": manifest["source_commit"],
                "version": manifest["version"],
                "architecture": "amd64",
                "deb_basename": manifest["deb_basename"],
                "deb_sha256": manifest["deb_sha256"],
                "compatibility_policy_id": policy["policy_id"],
                "compatibility_policy_sha256": manifest["compatibility_policy_sha256"],
                "os_id": "kylin",
                "os_version": "controlled-negative-fixture-v1",
                "desktop_environment": "none" if category_id == "no-graphical-desktop-blocked" else "UKUI",
                "security_facts": {
                    "business_data_mutation": False,
                    "business_data_before_sha256": business_before,
                    "business_data_after_sha256": business_after,
                    "business_data_scope_id": BUSINESS_DATA_SCOPE_ID,
                    "business_data_inventory_sha256": business_inventory_sha256,
                    "boundary": boundary["boundary"],
                    "observed_value": scenario["observed"],
                    "stable_error_code": expected_code,
                    "execution_environment": "controlled-root-fixture-v1",
                    "preflight_result_sha256": preflight_sha256,
                },
                "checks": {"preflight": "BLOCKED"},
                "attachments": [
                    {"basename": "preflight-result.json", "sha256": preflight_sha256},
                    {
                        "basename": BUSINESS_DATA_INVENTORY_BASENAME,
                        "sha256": business_inventory_sha256,
                    },
                ],
                "challenge_nonce": args.challenge,
                "acceptance_session_id": session_id,
                "machine_fingerprint_sha256": machine_fingerprint,
            }
            try:
                contract.validate_negative_preflight_attachment(record, matrix, preflight_payload)
                contract.validate_negative_business_data_attachment(
                    record,
                    matrix,
                    business_inventory_payload,
                )
            except Exception as exc:
                raise NegativeEvidenceError(f"negative evidence contract rejected {category_id}: {exc}") from exc
            _write_new(category_dir / "environment-evidence.json", _canonical_json(record))
        os.rename(output_temp, args.output)
        published = True
        return args.output
    finally:
        if not published and output_temp.exists():
            shutil.rmtree(output_temp)
        if work_temp.exists():
            shutil.rmtree(work_temp)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deb", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--challenge", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        output = produce(parse_args(argv))
    except (NegativeEvidenceError, OSError, subprocess.SubprocessError) as exc:
        print(f"negative-evidence-producer-failed\t{exc}", file=sys.stderr)
        return 1
    print(f"negative-evidence-produced\t{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
