#!/usr/bin/env python3
"""Sync packaged non-secret Taiji product config into the active user config."""

from __future__ import annotations

import copy
import os
import sys
from io import StringIO
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception as exc:  # pragma: no cover - runtime guard
    raise SystemExit(f"PyYAML is required to sync packaged config: {exc}") from exc


_LAB_ROOT = Path(__file__).resolve().parent.parent
_agent_roots = [
    Path(os.environ["TAIJI_AGENT_AGENT_DIR"]).expanduser()
    if os.environ.get("TAIJI_AGENT_AGENT_DIR")
    else None,
    _LAB_ROOT / "sources" / "hermes-agent",
    _LAB_ROOT / "runtime" / "agent",
]
for _agent_root in _agent_roots:
    if (
        _agent_root is not None
        and (_agent_root / "agent" / "provider_credentials.py").is_file()
    ):
        sys.path.insert(0, str(_agent_root))
        break

try:
    from agent.image_gen_verification import (
        reconcile_capability_config_epochs,
    )
    from agent.provider_credentials import (
        mutate_config_strict,
        seed_config_payload_strict,
    )
except Exception as exc:  # pragma: no cover - installation guard
    raise SystemExit(
        f"Taiji canonical config writer is unavailable: {exc}"
    ) from exc


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
    target_path = target_path.expanduser()
    if not target_path.is_absolute():
        target_path = Path.cwd() / target_path
    if (
        template_path == target_path.resolve(strict=False)
        or not template_path.exists()
    ):
        return False

    template_config = _load_yaml(template_path)
    _assert_template_has_no_secrets(template_config)

    target_path.parent.mkdir(parents=True, exist_ok=True)

    if not target_path.exists():
        seeded_config = copy.deepcopy(template_config)
        reconcile_capability_config_epochs({}, seeded_config)
        try:
            from ruamel.yaml import YAML

            yaml_rt = YAML(typ="rt")
            yaml_rt.preserve_quotes = True
            yaml_rt.allow_unicode = True
            rendered = yaml_rt.load(
                template_path.read_text(encoding="utf-8")
            )
            for metadata_key in (
                "_taiji_capability_epochs",
                "_taiji_profile_incarnation",
            ):
                if metadata_key in seeded_config:
                    rendered[metadata_key] = copy.deepcopy(
                        seeded_config[metadata_key]
                    )
            stream = StringIO()
            yaml_rt.dump(rendered, stream)
            seed_config_payload_strict(
                stream.getvalue().encode("utf-8"),
                config_path=target_path,
            )
            return True
        except FileExistsError:
            # Another canonical writer won the first-write race. Merge the
            # packaged defaults into that current file below.
            pass

    changed = False

    def merge_packaged_defaults(current: dict[str, Any]) -> None:
        nonlocal changed
        target_config = copy.deepcopy(current)
        if not target_config:
            target_config = copy.deepcopy(template_config)
        else:
            if (
                "provider_credentials" in template_config
                and "provider_credentials" not in target_config
            ):
                target_config["provider_credentials"] = copy.deepcopy(
                    template_config.get("provider_credentials") or []
                )

            template_webui = _dict(template_config.get("webui"))
            template_visibility = _dict(
                template_webui.get("feature_visibility")
            )
            if template_visibility:
                target_webui = dict(_dict(target_config.get("webui")))
                if (
                    target_webui.get("feature_visibility")
                    != template_visibility
                ):
                    target_webui["feature_visibility"] = copy.deepcopy(
                        template_visibility
                    )
                    target_config["webui"] = target_webui

            template_model = _dict(template_config.get("model"))
            if template_model:
                target_model = dict(_dict(target_config.get("model")))
                _fill_missing(
                    target_model,
                    template_model,
                    ("provider", "default", "base_url"),
                )
                target_config["model"] = target_model

            template_image = _dict(template_config.get("image_gen"))
            if template_image:
                target_image = dict(_dict(target_config.get("image_gen")))
                _fill_missing(
                    target_image,
                    template_image,
                    ("provider", "model"),
                )
                if (
                    "use_gateway" in template_image
                    and "use_gateway" not in target_image
                ):
                    target_image["use_gateway"] = template_image[
                        "use_gateway"
                    ]
                target_config["image_gen"] = target_image

        changed = target_config != current
        if not changed:
            return
        reconcile_capability_config_epochs(current, target_config)
        current.clear()
        current.update(target_config)

    mutate_config_strict(
        merge_packaged_defaults,
        config_path=target_path,
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
