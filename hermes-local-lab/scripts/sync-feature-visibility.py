#!/usr/bin/env python3
"""Sync packaged Taiji WebUI feature visibility into the active user config.

Only webui.feature_visibility is synchronized. Existing model/provider/API-key
configuration in the target config.yaml is preserved.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

try:
    import yaml
except Exception as exc:  # pragma: no cover - runtime guard
    raise SystemExit(f"PyYAML is required to sync feature visibility: {exc}") from exc


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _feature_visibility(config: dict) -> dict | None:
    webui = config.get("webui")
    if not isinstance(webui, dict):
        return None
    feature_visibility = webui.get("feature_visibility")
    return feature_visibility if isinstance(feature_visibility, dict) else None


def sync_feature_visibility(template_path: Path, target_path: Path) -> bool:
    template_path = template_path.expanduser().resolve()
    target_path = target_path.expanduser().resolve()
    if template_path == target_path:
        return False
    if not template_path.exists():
        return False

    template_config = _load_yaml(template_path)
    packaged_visibility = _feature_visibility(template_config)
    if packaged_visibility is None:
        return False

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not target_path.exists() or target_path.stat().st_size == 0:
        shutil.copyfile(template_path, target_path)
        return True

    target_config = _load_yaml(target_path)
    webui = target_config.get("webui")
    if not isinstance(webui, dict):
        webui = {}
    webui["feature_visibility"] = packaged_visibility
    target_config["webui"] = webui
    target_path.write_text(
        yaml.safe_dump(target_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: sync-feature-visibility.py TEMPLATE_CONFIG TARGET_CONFIG", file=sys.stderr)
        return 2
    sync_feature_visibility(Path(argv[1]), Path(argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
