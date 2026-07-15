"""Trusted model-data policy validation for expert-team document briefs."""

from __future__ import annotations

from datetime import datetime


def load_model_policy_registry() -> dict:
    """Load the server-owned policy registry from the active config.yaml."""
    try:
        from api.config import _get_config_path, _load_yaml_config_file

        path = _get_config_path()
        config = _load_yaml_config_file(path) if path.exists() else {}
    except Exception:
        return {}
    registry = config.get("expert_team_model_data_policies") if isinstance(config, dict) else {}
    return registry if isinstance(registry, dict) else {}


def _error(field: str, code: str, message: str) -> dict:
    return {"field": field, "code": code, "message": message}


def validate_model_policy_reference(brief: dict, *, model_policy_registry: dict, now: str) -> dict:
    """Return a safe validation result; never echo provider credentials or endpoints."""
    handling = brief.get("data_handling") if isinstance(brief.get("data_handling"), dict) else {}
    control = brief.get("document_control") if isinstance(brief.get("document_control"), dict) else {}
    policy_id = str(handling.get("model_policy_id") or "").strip()
    policy = model_policy_registry.get(policy_id) if isinstance(model_policy_registry, dict) else None
    denied = {
        "authorized": False,
        "policy_id": policy_id,
        "label": "",
        "field_errors": [_error("data_handling.model_policy_id", "data_egress_not_authorized", "当前文档未配置可用的企业模型数据策略")],
    }
    if not policy_id or not isinstance(policy, dict):
        return denied

    required_lists = (
        "allowed_classifications",
        "provider_ids",
        "deployment_ids",
        "trust_zones",
        "retention_modes",
        "allowed_source_kinds",
    )
    if any(not isinstance(policy.get(key), list) or not policy.get(key) for key in required_lists):
        return denied
    if not str(policy.get("approval_ref") or "").strip():
        return denied
    try:
        expires_at = datetime.fromisoformat(str(policy.get("expires_at") or ""))
        checked_at = datetime.fromisoformat(str(now))
        if expires_at <= checked_at:
            return denied
    except (TypeError, ValueError):
        return denied

    classification = str(control.get("classification") or "").strip()
    if classification not in policy["allowed_classifications"]:
        return denied
    if bool(handling.get("requires_zero_retention")) and "zero_retention" not in policy["retention_modes"]:
        return denied
    if policy.get("training_opt_out_required") is not True:
        return denied
    if classification in {"restricted", "custom"} and any(
        "*" in policy[key] for key in ("provider_ids", "deployment_ids", "trust_zones")
    ):
        return denied

    return {
        "authorized": True,
        "policy_id": policy_id,
        "label": str(policy.get("label") or policy_id),
        "field_errors": [],
    }


def authorize_actual_provider(
    brief: dict,
    *,
    provider_context: dict,
    model_policy_registry: dict,
    now: str,
) -> dict:
    """Authorize the provider/deployment selected by the gateway, not the UI hint.

    The returned value is deliberately audit-safe: endpoints, credentials and
    arbitrary provider metadata are never copied into it.
    """
    reference = validate_model_policy_reference(
        brief,
        model_policy_registry=model_policy_registry,
        now=now,
    )
    if not reference.get("authorized"):
        return reference

    handling = brief.get("data_handling") if isinstance(brief.get("data_handling"), dict) else {}
    source_policy = brief.get("source_policy") if isinstance(brief.get("source_policy"), dict) else {}
    policy_id = str(handling.get("model_policy_id") or "").strip()
    policy = model_policy_registry[policy_id]
    provider_id = str(provider_context.get("provider_id") or "").strip()
    deployment_id = str(provider_context.get("deployment_id") or "").strip()
    trust_zone = str(provider_context.get("trust_zone") or "").strip()
    retention_mode = str(provider_context.get("retention_mode") or "").strip()
    source_kinds = {
        str(item.get("kind") or "").strip()
        for item in source_policy.get("source_refs") or []
        if isinstance(item, dict) and str(item.get("kind") or "").strip()
    }

    checks = (
        provider_id in policy["provider_ids"],
        deployment_id in policy["deployment_ids"],
        trust_zone in policy["trust_zones"],
        retention_mode in policy["retention_modes"],
        provider_context.get("training_opt_out") is True,
        provider_context.get("preserves_message_roles") is True,
        provider_context.get("supports_tools_disabled") is True,
        source_kinds.issubset(set(policy["allowed_source_kinds"])),
    )
    if not all(checks):
        return {
            "authorized": False,
            "policy_id": policy_id,
            "label": str(policy.get("label") or policy_id),
            "field_errors": [
                _error(
                    "data_handling.model_policy_id",
                    "data_egress_not_authorized",
                    "当前网关实际使用的模型部署不满足文档数据策略",
                )
            ],
        }

    return {
        "authorized": True,
        "policy_id": policy_id,
        "provider_id": provider_id,
        "deployment_id": deployment_id,
        "trust_zone": trust_zone,
        "retention_mode": retention_mode,
        "preserves_message_roles": True,
        "tools_disabled": True,
    }
