#!/usr/bin/env python3
"""Build the deterministic models.dev fallback bundled with Hermes Agent."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SOURCE_URL = "https://models.dev/api.json"


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_snapshot(source: Path, output: Path, generated_at: str) -> None:
    providers = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(providers, dict) or not providers:
        raise ValueError("models.dev source must be a non-empty object")

    registry_bytes = _canonical_json_bytes(providers)
    document = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": SOURCE_URL,
        "registry_sha256": hashlib.sha256(registry_bytes).hexdigest(),
        "providers": providers,
    }
    payload = _canonical_json_bytes(document)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--generated-at",
        required=True,
        help="UTC timestamp describing the source snapshot, for example 2026-06-30T01:34:08Z",
    )
    args = parser.parse_args()
    build_snapshot(args.source, args.output, args.generated_at)


if __name__ == "__main__":
    main()
