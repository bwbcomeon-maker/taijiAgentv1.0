#!/usr/bin/env python3
"""Load the fixed Linux amd64 DEB compatibility policy without host overrides."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
from pathlib import Path
from typing import Any


SCHEMA = "taiji-linux-compatibility-policy/v1"
POLICY_ID = "taiji-linux-amd64-deb-v1"

TOP_LEVEL_FIELDS = {
    "schema", "policy_id", "package", "architecture", "os_families",
    "minimum_supported", "system_capabilities", "debian", "elf",
}

EXPECTED_PACKAGE = {
    "name": "taiji-agent",
    "architecture": "amd64",
    "install_root": "/opt/taiji-agent",
    "maintainer": "Taiji Agent Product Team <noreply@localhost>",
}
EXPECTED_ARCHITECTURE = {"uname_machine": ["x86_64"], "dpkg": ["amd64"]}
EXPECTED_OS_FAMILIES = [
    {"family": "kylin", "ids": ["kylin"]},
    {"family": "uos", "ids": ["uos"]},
    {"family": "openkylin", "ids": ["openkylin"]},
]
EXPECTED_MINIMUM_SUPPORTED = {"glibc": "2.31", "kernel": "4.19.0"}
EXPECTED_SYSTEM_CAPABILITIES = {
    "commands": ["/usr/bin/apt-get", "/usr/bin/dpkg", "/usr/bin/systemctl"],
    "desktop_session_dirs": ["/usr/share/xsessions", "/usr/share/wayland-sessions"],
    "loopback_path": "/sys/class/net/lo",
    "install_root_parent": "/opt",
    "disk_headroom_mib": 6144,
}
EXPECTED_DEBIAN = {"depends": ["ca-certificates", "libc6 (>= 2.31)"]}
EXPECTED_ELECTRON_DISTRIBUTION = {
    "version": "39.8.10",
    "archive_sha256": "92e8b031fa5327c78a972279fd75fc8503fcd1773401809f4557e4de583eabd1",
    "elf_files": {
        "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron": {
            "soname": None,
            "sha256": "c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d",
            "allowed_host_path_literals": [
                "/home/privacy/",
                "/tmp/__v8_gc__",
                "/tmp/foo.js",
                "/tmp/node-repl-sock",
                "/tmp/perfetto-consumer",
                "/tmp/perfetto-producer",
                "/workspace/workspace.js",
            ],
        },
        "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/chrome-sandbox": {
            "soname": None,
            "sha256": "b80ae15c6479c7feaba09d0be5baa7dc5e9f6c4a8318ad810ab2403bd19c1556",
            "allowed_host_path_literals": [],
        },
        "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/chrome_crashpad_handler": {
            "soname": None,
            "sha256": "7a24f3d83dbe3374c6369dd812f45333b4cf4f37ba2f6183692367f6aa5218ae",
            "allowed_host_path_literals": [],
        },
        "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/libEGL.so": {
            "soname": "libEGL.so",
            "sha256": "6cfd87c370d8d54b091d92e37ee8557746e07fc811835f91aa0e2f1498874eec",
            "allowed_host_path_literals": [],
        },
        "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/libGLESv2.so": {
            "soname": "libGLESv2.so",
            "sha256": "4cc6eed2c6bd7780c610d2af3d7222ffe14e1d4b06774a995b592f5b19d89f74",
            "allowed_host_path_literals": [],
        },
        "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/libffmpeg.so": {
            "soname": "libffmpeg.so",
            "sha256": "201277a3add9103e67152e0a351368412ab1ef021297261032d08c130748a72c",
            "allowed_host_path_literals": ["/tmp/%sXXXXXX"],
        },
        "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/libvulkan.so.1": {
            "soname": "libvulkan.so.1",
            "sha256": "0469f4bec7fc7b850961110e2ab403535ac31521184ac57a76be3960dc7b046c",
            "allowed_host_path_literals": [
                "/build/linux/debian_bullseye_amd64-sysroot/usr/include",
                "/build/linux/debian_bullseye_amd64-sysroot/usr/include/X11",
                "/build/linux/debian_bullseye_amd64-sysroot/usr/include/x86_64-linux-gnu/bits",
                "/build/linux/debian_bullseye_amd64-sysroot/usr/include/x86_64-linux-gnu/bits/types",
                "/build/linux/debian_bullseye_amd64-sysroot/usr/include/x86_64-linux-gnu/sys",
                "/build/linux/debian_bullseye_amd64-sysroot/usr/include/xcb",
            ],
        },
        "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/libvk_swiftshader.so": {
            "soname": "libvk_swiftshader.so",
            "sha256": "f29e848cde25445ed9f0b157b4fd0caaf8818cec096bd2e175a52c1d7c0f2ea2",
            "allowed_host_path_literals": [],
        },
    },
}
EXPECTED_ELF = {
    "maximum_symbol_versions": {"GLIBC": "2.31", "GLIBCXX": "3.4.28", "CXXABI": "1.3.12"},
    "private_library_dir": "/opt/taiji-agent/runtime/lib",
    "allowed_private_sonames": [
        "libasound.so.2", "libatk-1.0.so.0", "libatk-bridge-2.0.so.0", "libatspi.so.0",
        "libcairo.so.2", "libcups.so.2", "libexpat.so.1", "libfontconfig.so.1",
        "libgio-2.0.so.0", "libglib-2.0.so.0", "libgobject-2.0.so.0", "libgtk-3.so.0",
        "libfreebl3.so", "libfreeblpriv3.so", "libnspr4.so", "libnss3.so",
        "libnssckbi.so", "libnssdbm3.so", "libnsspem.so", "libnssutil3.so",
        "libpango-1.0.so.0",
        "libpangocairo-1.0.so.0", "libpangoft2-1.0.so.0", "libplc4.so", "libplds4.so",
        "libsecret-1.so.0", "libuuid.so.1", "libX11.so.6", "libX11-xcb.so.1",
        "libxcb.so.1", "libXcomposite.so.1", "libXdamage.so.1", "libXext.so.6",
        "libXfixes.so.3", "libxkbcommon.so.0", "libXrandr.so.2", "libXrender.so.1",
        "libXshmfence.so.1", "libXss.so.1", "libXtst.so.6",
        "libXau.so.6", "libXcursor.so.1", "libXdmcp.so.6", "libXi.so.6",
        "libXinerama.so.1", "libavahi-client.so.3", "libavahi-common.so.3", "libbsd.so.0",
        "libcairo-gobject.so.2", "libdatrie.so.1", "libepoxy.so.0", "libffi.so.7",
        "libfreetype.so.6", "libfribidi.so.0", "libgdk-3.so.0", "libgdk_pixbuf-2.0.so.0",
        "libgmodule-2.0.so.0", "libgraphite2.so.3", "libharfbuzz.so.0", "libpcre.so.3",
        "libpixman-1.so.0", "libpng16.so.16", "libsmime3.so", "libsoftokn3.so",
        "libsqlite3.so.0", "libthai.so.0",
        "libwayland-client.so.0", "libwayland-cursor.so.0", "libwayland-egl.so.1",
        "libxcb-render.so.0", "libxcb-shm.so.0",
    ],
    "required_system_sonames": [
        "libdbus-1.so.3", "libdrm.so.2", "libgbm.so.1", "libGL.so.1", "libEGL.so.1", "libGLX.so.0",
        "libgcrypt.so.20", "libgnutls.so.30", "libgssapi_krb5.so.2", "libmount.so.1",
        "libselinux.so.1", "libudev.so.1", "libgcc_s.so.1", "libstdc++.so.6",
        "libz.so.1", "libcrypt.so.1",
    ],
    "forbidden_bundled_sonames": [
        "ld-linux-x86-64.so.2", "libc.so.6", "libdl.so.2", "libm.so.6", "libpthread.so.0",
        "librt.so.1", "libpam.so.0", "libsystemd.so.0", "libdbus-1.so.3", "libdrm.so.2",
        "libgbm.so.1", "libGL.so.1", "libEGL.so.1", "libGLX.so.0", "libgcrypt.so.20",
        "libgnutls.so.30", "libgssapi_krb5.so.2", "libmount.so.1", "libselinux.so.1",
        "libudev.so.1", "libgcc_s.so.1", "libstdc++.so.6", "libz.so.1", "libcrypt.so.1",
    ],
    "allowed_runpaths": ["$ORIGIN", "$ORIGIN/../lib", "/opt/taiji-agent/runtime/lib"],
    "electron_distribution": EXPECTED_ELECTRON_DISTRIBUTION,
}


class PolicyError(ValueError):
    """Raised when the checked-in contract is not the exact canonical policy."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PolicyError("compatibility policy contains duplicate field: {}".format(key))
        value[key] = item
    return value


def _require_exact_fields(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError("{} must be an object".format(label))
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        parts = []
        if missing:
            parts.append("missing fields: {}".format(", ".join(missing)))
        if unknown:
            parts.append("unknown fields: {}".format(", ".join(unknown)))
        raise PolicyError("{} has {}".format(label, "; ".join(parts)))
    return value


def _require_exact_value(value: Any, expected: Any, label: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise PolicyError("{} does not match the fixed compatibility contract".format(label))


def _validate_exact_object(value: Any, expected: dict[str, Any], label: str) -> None:
    _require_exact_fields(value, set(expected), label)
    for field, expected_value in expected.items():
        _require_exact_value(value[field], expected_value, "{}.{}".format(label, field))


def _validate(policy: dict[str, Any]) -> None:
    _require_exact_fields(policy, TOP_LEVEL_FIELDS, "compatibility policy")
    _require_exact_value(policy["schema"], SCHEMA, "schema")
    _require_exact_value(policy["policy_id"], POLICY_ID, "policy_id")
    _validate_exact_object(policy["package"], EXPECTED_PACKAGE, "package")
    _validate_exact_object(policy["architecture"], EXPECTED_ARCHITECTURE, "architecture")
    _require_exact_value(policy["os_families"], EXPECTED_OS_FAMILIES, "os_families")
    _validate_exact_object(policy["minimum_supported"], EXPECTED_MINIMUM_SUPPORTED, "minimum_supported")
    _validate_exact_object(policy["system_capabilities"], EXPECTED_SYSTEM_CAPABILITIES, "system_capabilities")
    _validate_exact_object(policy["debian"], EXPECTED_DEBIAN, "debian")
    _validate_exact_object(policy["elf"], EXPECTED_ELF, "elf")

    elf = policy["elf"]
    private = set(elf["allowed_private_sonames"])
    required_system = set(elf["required_system_sonames"])
    forbidden = set(elf["forbidden_bundled_sonames"])
    if private & (required_system | forbidden):
        raise PolicyError("private SONAMEs must not overlap system or forbidden SONAMEs")
    if not required_system <= forbidden:
        raise PolicyError("required system SONAMEs must be forbidden from bundling")


def canonical_bytes(policy: dict[str, Any]) -> bytes:
    return (json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def canonical_sha256(policy: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(policy)).hexdigest()


def load_and_validate(path: Path) -> dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise PolicyError("cannot read compatibility policy: {}".format(exc)) from exc
    try:
        policy = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyError("compatibility policy is not valid UTF-8 JSON: {}".format(exc)) from exc
    if not isinstance(policy, dict):
        raise PolicyError("compatibility policy must be an object")
    _validate(policy)
    if raw != canonical_bytes(policy):
        raise PolicyError("compatibility policy must use canonical JSON bytes")
    return policy


def render_debian_depends(policy: dict[str, Any]) -> str:
    _validate(policy)
    return ", ".join(policy["debian"]["depends"])


def shell_exports(policy: dict[str, Any]) -> dict[str, str]:
    _validate(policy)
    package = policy["package"]
    electron = policy["elf"]["electron_distribution"]
    return {
        "TAIJI_POLICY_ID": policy["policy_id"],
        "TAIJI_POLICY_SHA256": canonical_sha256(policy),
        "TAIJI_PACKAGE_NAME": package["name"],
        "TAIJI_PACKAGE_ARCHITECTURE": package["architecture"],
        "TAIJI_INSTALL_ROOT": package["install_root"],
        "TAIJI_PACKAGE_MAINTAINER": package["maintainer"],
        "TAIJI_DEBIAN_DEPENDS": render_debian_depends(policy),
        "TAIJI_GLIBC_MIN": policy["minimum_supported"]["glibc"],
        "TAIJI_KERNEL_MIN": policy["minimum_supported"]["kernel"],
        "TAIJI_PRIVATE_LIBRARY_DIR": policy["elf"]["private_library_dir"],
        "TAIJI_REQUIRED_SYSTEM_SONAMES": " ".join(policy["elf"]["required_system_sonames"]),
        "TAIJI_ELECTRON_VERSION": electron["version"],
        "TAIJI_ELECTRON_ARCHIVE_SHA256": electron["archive_sha256"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate the fixed policy")
    validate.add_argument("--policy", required=True, type=Path)
    output = validate.add_mutually_exclusive_group()
    output.add_argument("--print-id", action="store_true")
    output.add_argument("--print-sha256", action="store_true")
    output.add_argument("--print-maintainer", action="store_true")
    output.add_argument("--print-depends", action="store_true")
    output.add_argument("--print-shell", action="store_true")
    args = parser.parse_args(argv)

    policy = load_and_validate(args.policy)
    if args.print_id:
        print(policy["policy_id"])
    elif args.print_sha256:
        print(canonical_sha256(policy))
    elif args.print_maintainer:
        print(policy["package"]["maintainer"])
    elif args.print_depends:
        print(render_debian_depends(policy))
    elif args.print_shell:
        for key, value in shell_exports(policy).items():
            print("{}={}".format(key, shlex.quote(value)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PolicyError as exc:
        print("Compatibility policy validation failed: {}".format(exc), file=sys.stderr)
        sys.exit(1)
