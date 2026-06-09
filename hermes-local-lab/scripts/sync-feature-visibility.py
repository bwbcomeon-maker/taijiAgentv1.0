#!/usr/bin/env python3
"""Compatibility wrapper for the packaged Taiji product config sync."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_sync_module():
    script = Path(__file__).with_name("sync-packaged-config.py")
    spec = importlib.util.spec_from_file_location("taiji_sync_packaged_config", script)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Unable to load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sync_feature_visibility(template_path: Path, target_path: Path) -> bool:
    module = _load_sync_module()
    return bool(module.sync_packaged_config(template_path, target_path))


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: sync-feature-visibility.py TEMPLATE_CONFIG TARGET_CONFIG", file=sys.stderr)
        return 2
    sync_feature_visibility(Path(argv[1]), Path(argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
