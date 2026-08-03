"""Trusted model-data policy validation for expert-team document briefs."""

from __future__ import annotations

import re
from datetime import datetime


RESEARCH_PUBLIC_QUERY_POLICY = {
    "policy_id": "research-public-query/v1",
    "version": 1,
    "authorization_basis": "user_initiated_standalone_research",
    "trust_zone": "public_web",
    "projection_version": "research-public-topic/v1",
}
_RESEARCH_INTERNAL_TERMS = (
    "我司",
    "本公司",
    "内部合同",
    "客户报价",
    "续约风险",
    "未公开",
    "保密",
    "敏感",
    "项目代号",
    "客户",
    "合同",
    "报价",
    "回款",
    "商业关系",
)
_RESEARCH_PUBLIC_TOPIC_TERMS = (
    "本地优先",
    "AI 助理",
    "人工智能",
    "企业办公",
    "部署成本",
    "行业趋势",
    "技术架构",
    "数据安全",
    "开源软件",
    "大语言模型",
    "生成式人工智能",
    "知识管理",
    "办公自动化",
)


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


def authorize_research_public_query(run: dict, query: str) -> dict:
    """Authorize one public research query from immutable server-owned policy."""
    from api.helpers import _redact_text

    profile = run.get("launch_profile_snapshot") if isinstance(run.get("launch_profile_snapshot"), dict) else {}
    policy = profile.get("research_query_egress_policy")
    denied = {
        "authorized": False,
        "reason_code": "data_egress_not_authorized",
        "safe_reason": "当前研究任务未绑定可用的公共查询外发策略",
    }
    if not isinstance(policy, dict) or policy != RESEARCH_PUBLIC_QUERY_POLICY:
        return denied
    if (
        str(run.get("launch_profile_id") or "") != "research-report"
        or str(run.get("product_mode") or "") != "standalone"
        or profile.get("research_contract_version") != "research-report/v2"
    ):
        return denied

    text = str(query or "").strip()
    original_request = str(run.get("prompt") or "").strip()
    control = (run.get("document_brief") or {}).get("document_control") or {}
    classification = str(control.get("classification") or "").strip().lower()
    def blocked_by_dlp(value: str) -> bool:
        return bool(
            not value
            or _redact_text(value, _enabled=True) != value
            or re.search(r"(?:^|\s)(?:file://|~[/\\]|/[A-Za-z0-9_.-]+/|[A-Za-z]:[/\\])", value)
            or re.search(r"https?://[^\s/@:]+:[^\s/@]+@", value, flags=re.IGNORECASE)
            or re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", value, flags=re.IGNORECASE)
            or re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", value)
            or re.search(r"(?<!\d)\d{8,}(?!\d)", value)
            or any(term in value for term in _RESEARCH_INTERNAL_TERMS)
        )

    original_topics = tuple(term for term in _RESEARCH_PUBLIC_TOPIC_TERMS if term in original_request)
    direction_topics = tuple(term for term in _RESEARCH_PUBLIC_TOPIC_TERMS if term in text)
    blocked = bool(
        classification in {"restricted", "custom", "private", "confidential"}
        or blocked_by_dlp(original_request)
        or blocked_by_dlp(text)
        or not original_topics
        or not direction_topics
        or not set(original_topics).intersection(direction_topics)
    )
    if blocked:
        return {
            "authorized": False,
            "reason_code": "policy_blocked",
            "safe_reason": "研究查询命中公共外发的数据防泄漏规则",
        }
    return {
        "authorized": True,
        "safe_query": " ".join(direction_topics),
        **RESEARCH_PUBLIC_QUERY_POLICY,
        "reason_code": "",
        "safe_reason": "",
    }


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
