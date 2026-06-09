#!/usr/bin/env python3
"""Sync packaged non-secret Taiji product config into the active user config."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception as exc:  # pragma: no cover - runtime guard
    raise SystemExit(f"PyYAML is required to sync packaged config: {exc}") from exc


_SENSITIVE_KEYS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "private_key",
    "wechat",
    "weixin",
    "corpsecret",
)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _assert_template_has_no_secrets(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).strip().lower()
            if any(marker in key_text for marker in _SENSITIVE_KEYS):
                raise SystemExit(f"Packaged config template contains sensitive key: {path}{key}")
            _assert_template_has_no_secrets(child, f"{path}{key}.")
        return
    if isinstance(value, list):
        for idx, child in enumerate(value):
            _assert_template_has_no_secrets(child, f"{path}{idx}.")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _fill_missing(target: dict[str, Any], template: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key in template and _is_empty(target.get(key)):
            target[key] = template[key]


def sync_packaged_config(template_path: Path, target_path: Path) -> bool:
    template_path = template_path.expanduser().resolve()
    target_path = target_path.expanduser().resolve()
    if template_path == target_path or not template_path.exists():
        return False

    template_config = _load_yaml(template_path)
    _assert_template_has_no_secrets(template_config)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not target_path.exists() or target_path.stat().st_size == 0:
        shutil.copyfile(template_path, target_path)
        return True

    target_config = _load_yaml(target_path)
    changed = False

    template_webui = _dict(template_config.get("webui"))
    template_visibility = _dict(template_webui.get("feature_visibility"))
    if template_visibility:
        target_webui = dict(_dict(target_config.get("webui")))
        if target_webui.get("feature_visibility") != template_visibility:
            target_webui["feature_visibility"] = template_visibility
            target_config["webui"] = target_webui
            changed = True

    template_model = _dict(template_config.get("model"))
    if template_model:
        target_model = dict(_dict(target_config.get("model")))
        before = dict(target_model)
        _fill_missing(target_model, template_model, ("provider", "default", "base_url"))
        if target_model != before:
            target_config["model"] = target_model
            changed = True

    template_image = _dict(template_config.get("image_gen"))
    if template_image:
        target_image = dict(_dict(target_config.get("image_gen")))
        before = dict(target_image)
        _fill_missing(target_image, template_image, ("provider", "model"))
        if "use_gateway" in template_image and "use_gateway" not in target_image:
            target_image["use_gateway"] = template_image["use_gateway"]
        if target_image != before:
            target_config["image_gen"] = target_image
            changed = True

    if changed:
        target_path.write_text(
            yaml.safe_dump(target_config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    return changed


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: sync-packaged-config.py TEMPLATE_CONFIG TARGET_CONFIG", file=sys.stderr)
        return 2
    sync_packaged_config(Path(argv[1]), Path(argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
