#!/usr/bin/env python3
"""Render a fail-closed DEB preinst bound to one validated target profile."""

import argparse
import os
import shlex
import sys
import tempfile
from pathlib import Path


LINUX_PACKAGING_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LINUX_PACKAGING_DIR))

import target_baseline  # noqa: E402


TOKENS = {
    "@@TAIJI_BASELINE_PROFILE_ID@@": lambda profile: profile["profile_id"],
    "@@TAIJI_BASELINE_OS_ID@@": lambda profile: profile["os_release"]["id"],
    "@@TAIJI_BASELINE_OS_ID_LIKE@@": lambda profile: " ".join(
        profile["os_release"]["id_like"]
    ),
    "@@TAIJI_BASELINE_OS_VERSION_ID@@": lambda profile: profile["os_release"][
        "version_id"
    ],
    "@@TAIJI_BASELINE_OS_VARIANT_ID@@": lambda profile: profile["os_release"][
        "variant_id"
    ],
    "@@TAIJI_BASELINE_OS_BUILD_ID@@": lambda profile: profile["os_release"][
        "build_id"
    ],
    "@@TAIJI_BASELINE_GLIBC_MIN@@": lambda profile: profile["glibc"]["version"],
}


def render(template_text, profile):
    rendered = template_text
    for token, getter in TOKENS.items():
        count = rendered.count(token)
        if count != 1:
            raise target_baseline.BaselineError(
                "preinst template must contain {} exactly once; found {}".format(token, count)
            )
        rendered = rendered.replace(token, shlex.quote(getter(profile)))
    if "@@TAIJI_BASELINE_" in rendered:
        raise target_baseline.BaselineError("preinst template contains an unknown baseline token")
    if not rendered.startswith("#!/bin/bash -p\n"):
        raise target_baseline.BaselineError("rendered preinst must use privileged fixed /bin/bash")
    return rendered


def write_atomic(path, text):
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--depends-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-age-days", type=int, default=30)
    args = parser.parse_args(argv)

    profile = target_baseline.load_profile(args.profile)
    target_baseline.validate_profile(
        profile,
        args.depends_file,
        max_age_days=args.max_age_days,
    )
    template = Path(args.template).read_text(encoding="utf-8")
    write_atomic(args.output, render(template, profile))
    print("Rendered preinst for target profile: {}".format(profile["profile_id"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, target_baseline.BaselineError) as exc:
        print("Cannot render target-bound preinst: {}".format(exc), file=sys.stderr)
        sys.exit(1)
