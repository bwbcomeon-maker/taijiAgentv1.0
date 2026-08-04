#!/usr/bin/env python3
"""Validate the release-contact identity approved in the formal source tree."""

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path


SCHEMA = "taiji-approved-maintainer/v1"
FIELDS = {"schema", "maintainer"}
MAINTAINER_RE = re.compile(
    r"^[^<>\x00-\x1f\x7f]+ <[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}>$"
)
PLACEHOLDER_RE = re.compile(
    r"example\.(?:com|org|net|invalid)|@localhost(?:[>.]|$)|\.invalid>",
    re.IGNORECASE,
)


class MaintainerError(ValueError):
    pass


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MaintainerError("approved maintainer contains duplicate field: {}".format(key))
        result[key] = value
    return result


def load_descriptor(path):
    path = Path(path)
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise MaintainerError("O_NOFOLLOW is unavailable")
    flags |= nofollow
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MaintainerError("cannot safely open approved maintainer: {}".format(exc))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise MaintainerError("approved maintainer must be a regular file")
        if before.st_nlink != 1:
            raise MaintainerError("approved maintainer must have exactly one hard link")
        if stat.S_IMODE(before.st_mode) & 0o022:
            raise MaintainerError("approved maintainer must not be group/other writable")
        if before.st_size <= 0 or before.st_size > 4096:
            raise MaintainerError("approved maintainer has an unsafe size")
        payload = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if len(payload) != before.st_size or identity_before != identity_after:
            raise MaintainerError("approved maintainer changed while being read")
    finally:
        os.close(descriptor)

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaintainerError("approved maintainer is not valid UTF-8 JSON: {}".format(exc))
    if not isinstance(value, dict):
        raise MaintainerError("approved maintainer must be an object")
    actual_fields = set(value)
    if actual_fields != FIELDS:
        missing = sorted(FIELDS - actual_fields)
        unknown = sorted(actual_fields - FIELDS)
        parts = []
        if missing:
            parts.append("missing fields: {}".format(", ".join(missing)))
        if unknown:
            parts.append("unknown fields: {}".format(", ".join(unknown)))
        raise MaintainerError("approved maintainer {}".format("; ".join(parts)))
    if value["schema"] != SCHEMA:
        raise MaintainerError("unsupported approved maintainer schema")
    maintainer = value["maintainer"]
    if not isinstance(maintainer, str) or maintainer != maintainer.strip():
        raise MaintainerError("approved maintainer identity must be a trimmed string")
    if len(maintainer) > 320 or not MAINTAINER_RE.fullmatch(maintainer):
        raise MaintainerError("approved maintainer identity has an invalid format")
    if PLACEHOLDER_RE.search(maintainer):
        raise MaintainerError("approved maintainer identity contains a placeholder address")
    return maintainer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True)
    parser.add_argument("--expect")
    parser.add_argument("--print", action="store_true", dest="print_identity")
    args = parser.parse_args(argv)

    maintainer = load_descriptor(args.file)
    if args.expect is not None and args.expect != maintainer:
        raise MaintainerError("candidate maintainer does not match the approved identity")
    if args.print_identity:
        print(maintainer)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MaintainerError as exc:
        print("Approved maintainer validation failed: {}".format(exc), file=sys.stderr)
        sys.exit(1)
