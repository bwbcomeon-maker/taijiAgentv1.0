"""Fail-closed rollout policy for new expert-team contract-v1 runs."""

from __future__ import annotations

import logging
import os

from .contracts import ContractError, EXPERT_TEAM_CONTRACT_V1


logger = logging.getLogger(__name__)

ROLLOUT_ENV = "TAIJI_EXPERT_TEAM_CONTRACT_V1_ROLLOUT"
ROLLOUT_CONFIG_KEY = "expert_team_contract_v1_rollout"
_VALID_MODES = frozenset({"off", "pilot"})
_PILOT_COMBINATIONS = (
    ("content-creator-team", "work_report", "work_report"),
    ("deep-research-team", "research_report", "research_report"),
)


def _load_config() -> dict:
    try:
        from api.config import _get_config_path, _load_yaml_config_file

        path = _get_config_path()
        return _load_yaml_config_file(path) if path.exists() else {}
    except Exception:
        logger.warning("Unable to read expert-team rollout config; rollout remains off", exc_info=True)
        return {}


def _warning(source: str) -> dict:
    return {
        "code": "invalid_rollout_value",
        "source": source,
        "message": "专家团企业合同试点配置无效，已安全关闭",
    }


def resolve_contract_rollout(*, config_data: dict | None = None) -> dict:
    """Resolve the sole server-owned rollout status without normalizing typos."""
    config = config_data if isinstance(config_data, dict) else _load_config()
    if ROLLOUT_ENV in os.environ:
        raw = os.environ.get(ROLLOUT_ENV)
        source = "environment"
    elif ROLLOUT_CONFIG_KEY in config:
        raw = config.get(ROLLOUT_CONFIG_KEY)
        source = "config_yaml"
    else:
        raw = "off"
        source = "default"

    warnings = []
    if not isinstance(raw, str) or raw not in _VALID_MODES:
        mode = "off"
        warnings.append(_warning(source))
        logger.warning(
            "Invalid expert-team contract rollout value from %s; rollout remains off",
            source,
        )
    else:
        mode = raw

    allowed = []
    if mode == "pilot":
        allowed = [
            {
                "team_id": team_id,
                "document_type": document_type,
                "intake_example_id": intake_example_id,
                "capability": "enterprise_contract_pilot",
                "label": "企业合同试点",
            }
            for team_id, document_type, intake_example_id in _PILOT_COMBINATIONS
        ]
    return {
        "mode": mode,
        "effective_mode": mode,
        "effective_source": source,
        "contract_version": EXPERT_TEAM_CONTRACT_V1,
        "allowed_combinations": allowed,
        "document_types": [item["document_type"] for item in allowed],
        "warnings": warnings,
    }


def enforce_new_contract_rollout(*, team_id: str, document_type: str, intake_example_id: str) -> dict:
    """Authorize one new v1 run before any run state is allocated or written."""
    status = resolve_contract_rollout()
    if status["effective_mode"] != "pilot":
        raise ContractError(
            "contract_rollout_disabled",
            "contract_version",
            "专家团企业合同试点当前未启用",
        )
    requested = (str(team_id or "").strip(), str(document_type or "").strip(), str(intake_example_id or "").strip())
    if requested not in _PILOT_COMBINATIONS:
        raise ContractError(
            "document_type_not_in_pilot",
            "document_type",
            "当前专家团与文种组合尚未进入企业合同试点",
        )
    return status
