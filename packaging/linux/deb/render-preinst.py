#!/usr/bin/env python3
"""Render the fixed, policy-based DEB compatibility preinst."""

import argparse
import os
import shlex
import sys
import tempfile
from pathlib import Path


LINUX_PACKAGING_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LINUX_PACKAGING_DIR))

from compatibility_policy import PolicyError, load_and_validate, canonical_sha256  # noqa: E402


TOKENS = {
    "@@TAIJI_POLICY_ID@@": lambda policy: policy["policy_id"],
    "@@TAIJI_POLICY_SHA256@@": canonical_sha256,
    "@@TAIJI_PACKAGE_NAME@@": lambda policy: policy["package"]["name"],
    "@@TAIJI_INSTALL_ROOT@@": lambda policy: policy["package"]["install_root"],
    "@@TAIJI_UNAME_ARCH@@": lambda policy: " ".join(policy["architecture"]["uname_machine"]),
    "@@TAIJI_DPKG_ARCH@@": lambda policy: " ".join(policy["architecture"]["dpkg"]),
    "@@TAIJI_OS_IDS@@": lambda policy: " ".join(
        item for family in policy["os_families"] for item in family["ids"]
    ),
    "@@TAIJI_GLIBC_MIN@@": lambda policy: policy["minimum_supported"]["glibc"],
    "@@TAIJI_KERNEL_MIN@@": lambda policy: policy["minimum_supported"]["kernel"],
    "@@TAIJI_COMMANDS@@": lambda policy: " ".join(policy["system_capabilities"]["commands"]),
    "@@TAIJI_DESKTOP_DIRS@@": lambda policy: " ".join(
        policy["system_capabilities"]["desktop_session_dirs"]
    ),
    "@@TAIJI_LOOPBACK_PATH@@": lambda policy: policy["system_capabilities"]["loopback_path"],
    "@@TAIJI_INSTALL_ROOT_PARENT@@": lambda policy: policy["system_capabilities"]["install_root_parent"],
    "@@TAIJI_DISK_HEADROOM_MIB@@": lambda policy: str(
        policy["system_capabilities"]["disk_headroom_mib"]
    ),
}


def render(template_text: str, policy: dict) -> str:
    rendered = template_text
    for token, getter in TOKENS.items():
        count = rendered.count(token)
        if count != 1:
            raise PolicyError(
                "preinst template must contain {} exactly once; found {}".format(token, count)
            )
        rendered = rendered.replace(token, shlex.quote(str(getter(policy))))
    if "@@TAIJI_" in rendered:
        raise PolicyError("preinst template contains an unknown policy token")
    if not rendered.startswith("#!/bin/bash -p\n"):
        raise PolicyError("rendered preinst must use privileged fixed /bin/bash")
    return rendered


def write_atomic(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".{}-".format(path.name), dir=str(path.parent)
    )
    try:
        os.fchmod(descriptor, 0o755)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    policy = load_and_validate(args.policy)
    rendered = render(args.template.read_text(encoding="utf-8"), policy)
    write_atomic(args.output, rendered)
    print("Rendered policy-based preinst: {}".format(policy["policy_id"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, PolicyError) as exc:
        print("Cannot render policy-based preinst: {}".format(exc), file=sys.stderr)
        sys.exit(1)
