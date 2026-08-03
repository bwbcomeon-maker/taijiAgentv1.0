"""Trusted model-data policy validation for expert-team document briefs."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime


RESEARCH_PUBLIC_QUERY_POLICY = {
    "policy_id": "research-public-query/v1",
    "version": 1,
    "authorization_basis": "user_initiated_standalone_research",
    "trust_zone": "public_web",
    "projection_version": "research-public-topic/v1",
}
_RESEARCH_HARD_INTERNAL_TERMS = (
    "项目代号",
)
_RESEARCH_CONFIDENTIAL_CN = ("机密", "秘密", "保密", "不公开", "未公开", "非公开", "私有", "敏感")
_RESEARCH_CONFIDENTIAL_EN = (
    "confidential",
    "private",
    "not public",
    "non-public",
    "nonpublic",
    "unpublished",
    "secret",
    "sensitive",
    "proprietary",
)
_RESEARCH_INTERNAL_CONTEXT_CN = ("内部", "我司", "本公司")
_RESEARCH_INTERNAL_CONTEXT_EN = ("internal", "our company", "our firm")
_RESEARCH_RELATION_CN = ("客户", "供应商", "合作方", "甲方", "乙方", "商业关系", "内部关系")
_RESEARCH_RELATION_EN = (
    "customer",
    "client",
    "supplier",
    "vendor",
    "counterparty",
    "business relationship",
)
_RESEARCH_PRIVATE_TRANSACTION_CN = (
    "合同报价",
    "合同定价",
    "合同价格",
    "合同金额",
    "采购报价",
    "采购定价",
    "采购价格",
    "采购金额",
    "付款条款",
    "支付条款",
    "回款",
    "账期",
    "续约",
    "应收账款",
    "应付账款",
)
_RESEARCH_PRIVATE_TRANSACTION_EN = (
    "contract pricing",
    "contract price",
    "contract value",
    "contract values",
    "procurement pricing",
    "procurement price",
    "procurement cost",
    "purchase price",
    "payment terms",
    "renewal terms",
    "renewal risk",
    "account period",
    "credit terms",
    "accounts receivable",
    "accounts payable",
)
_RESEARCH_PUBLIC_FINANCIAL_CN = (
    "营收",
    "收入",
    "利润",
    "净利润",
    "毛利",
    "市值",
    "销量",
    "市场份额",
)
_RESEARCH_PUBLIC_FINANCIAL_EN = (
    "revenue",
    "income",
    "profit",
    "earnings",
    "gross margin",
    "market cap",
    "market capitalization",
    "sales volume",
    "market share",
)
_RESEARCH_PUBLIC_CONTEXT_CN = ("公开", "年报", "官网", "官方", "上市", "公告", "财报")
_RESEARCH_PUBLIC_CONTEXT_EN = (
    "public",
    "annual report",
    "official",
    "listed",
    "filing",
    "published",
)
_RESEARCH_PUBLIC_TRANSACTION_CN = (
    "公开市场",
    "零售",
    "公开年报",
    "年报",
    "官方公告",
    "官方披露",
)
_RESEARCH_PUBLIC_TRANSACTION_EN = (
    "public market",
    "retail",
    "annual report",
    "official filing",
    "official disclosure",
)
_RESEARCH_NEGATED_PUBLIC_TRANSACTION_CN = (
    "非年报",
    "不属于年报",
    "非公开市场",
    "非零售",
)
_RESEARCH_NEGATED_PUBLIC_TRANSACTION_EN = (
    "not retail",
    "non-retail",
    "not annual report",
    "non-annual report",
    "not official filing",
    "non-public market",
)
_RESEARCH_EN_CONNECTORS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "of",
    "s",
    "the",
    "to",
}
_RESEARCH_LATIN_FUNCTION_WORDS = {
    "a",
    "an",
    "analyze",
    "and",
    "annual",
    "contract",
    "credit",
    "discount",
    "for",
    "global",
    "investigate",
    "market",
    "not",
    "of",
    "order",
    "payment",
    "please",
    "price",
    "pricing",
    "procurement",
    "public",
    "purchase",
    "quote",
    "quotation",
    "renewal",
    "report",
    "research",
    "retail",
    "study",
    "terms",
    "the",
    "trends",
    "value",
    "values",
}


def _contains_english_phrase(value: str, phrases: tuple[str, ...]) -> bool:
    lowered = value.casefold()
    return any(re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lowered) for phrase in phrases)


def _semantic_forms(value: str) -> tuple[str, list[str], str]:
    """Normalize formatting before semantic policy checks.

    The policy intentionally reasons over tokens rather than raw substrings so
    punctuation, repeated whitespace and harmless connector words cannot alter
    a private/public transaction decision.
    """
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    lowered = normalized.casefold()
    lowered = re.sub(r"\bcannot\s+be\s+treated\s+as\b", "not", lowered)
    lowered = re.sub(r"\b(?:is|are|was|were|do|does|did|has|have|had)n['’]?t\b", "not", lowered)
    english_tokens = re.findall(r"[a-z0-9]+", lowered)
    semantic_english = [
        token for token in english_tokens if token not in _RESEARCH_EN_CONNECTORS
    ]
    chinese_compact = re.sub(r"[\s\W_]+", "", normalized, flags=re.UNICODE)
    chinese_semantic = chinese_compact.replace("的", "")
    return normalized, semantic_english, chinese_semantic


def _has_token_sequence(tokens: list[str], *sequence: str) -> bool:
    width = len(sequence)
    return bool(
        width
        and any(tuple(tokens[index : index + width]) == sequence for index in range(len(tokens) - width + 1))
    )


def _concepts_within(tokens: list[str], left: set[str], right: set[str], *, window: int = 5) -> bool:
    left_positions = [index for index, token in enumerate(tokens) if token in left]
    right_positions = [index for index, token in enumerate(tokens) if token in right]
    return any(abs(left_index - right_index) <= window for left_index in left_positions for right_index in right_positions)


def _private_transaction_semantics(tokens: list[str], chinese: str) -> bool:
    english_private = bool(
        _concepts_within(
            tokens,
            {"contract"},
            {
                "pricing",
                "price",
                "value",
                "values",
                "quote",
                "quotation",
                "amount",
                "cost",
                "fee",
                "fees",
            },
        )
        or _concepts_within(
            tokens,
            {"procurement", "purchase"},
            {"pricing", "price", "cost", "value", "values", "amount"},
        )
        or _concepts_within(tokens, {"payment", "credit"}, {"term", "terms", "period"})
        or _concepts_within(tokens, {"renewal"}, {"term", "terms", "risk"})
        or _concepts_within(tokens, {"account", "accounts"}, {"receivable", "payable"})
    )
    chinese_private = bool(
        re.search(r"(?:合同|采购).{0,6}(?:报价|定价|价格|金额|账期)", chinese)
        or re.search(r"(?:报价|定价|价格|金额|账期).{0,6}(?:合同|采购)", chinese)
        or any(term in chinese for term in ("付款条款", "支付条款", "回款", "续约", "应收账款", "应付账款"))
    )
    return english_private or chinese_private


def _generic_public_pricing_topic(tokens: list[str], chinese: str) -> bool:
    """Recognize an entity-free analytical topic, not an organization transaction."""
    if chinese:
        required = all(term in chinese for term in ("合同", "治理", "定价"))
        has_topic = any(
            term in chinese
            for term in ("发展趋势", "趋势", "演变", "动态", "走势", "模式", "格局", "发展")
        )
        remainder = chinese
        for term in (
            "请研究一下", "请分析一下", "请调研一下", "研究一下", "分析一下", "调研一下",
            "公共部门", "跨境", "全球", "企业", "行业", "公共", "研究", "分析", "调研",
            "发展趋势", "趋势", "演变", "动态", "走势", "模式", "格局", "发展",
            "合同", "治理", "定价", "与", "及", "和",
        ):
            remainder = remainder.replace(term, "")
        if required and has_topic and not remainder:
            return True
    topic_terms = {
        "benchmark", "benchmarks", "development", "dynamics", "evolution", "landscape", "outlook",
        "pattern", "patterns", "trajectories", "trajectory", "trend", "trends",
    }
    allowed = {
        "analyze", "border", "contract", "cross", "enterprise", "global",
        "governance", "industry", "investigate", "please", "pricing", "public",
        "research", "saas", "sector", "study", *topic_terms,
    }
    governance_topic = bool(
        {"contract", "governance", "pricing"}.issubset(tokens)
        and any(token in topic_terms - {"benchmark", "benchmarks"} for token in tokens)
    )
    public_benchmark = bool(
        {"public", "contract", "pricing"}.issubset(tokens)
        and any(token in {"benchmark", "benchmarks"} for token in tokens)
    )
    return bool(
        (governance_topic or public_benchmark)
        and all(token in allowed for token in tokens)
        and not any(
            token in tokens
            for token in {"amount", "cost", "fee", "fees", "quote", "quotation", "value", "values"}
        )
    )


def _public_transaction_semantics(tokens: list[str], chinese: str) -> tuple[bool, bool]:
    clauses: list[list[str]] = [[]]
    for token in tokens:
        if token in {"but", "however", "yet"}:
            clauses.append([])
        else:
            clauses[-1].append(token)

    positive = False
    negated = False
    prefix_negators = {"not", "non", "no", "without", "excluding", "exclude", "never"}
    postfix_negators = {"excluded", "excluding"}
    for clause in clauses:
        english_negative = bool(
            any(
                token in {
                    "avoid", "avoided", "cannot", "disregard", "disregarded",
                    "exclude", "excluded", "excluding", "ignore", "ignored",
                    "irrelevant", "never", "no", "non", "not", "unavailable", "without",
                }
                for token in clause
            )
            or _has_token_sequence(clause, "out", "scope")
        )
        markers: list[tuple[int, int]] = []
        markers.extend((index, index) for index, token in enumerate(clause) if token == "retail")
        for left, right in (
            ("public", "market"),
            ("annual", "report"),
            ("official", "filing"),
            ("official", "disclosure"),
            ("public", "benchmark"),
            ("public", "benchmarks"),
        ):
            left_positions = [index for index, token in enumerate(clause) if token == left]
            right_positions = [index for index, token in enumerate(clause) if token == right]
            for left_index in left_positions:
                following = [index for index in right_positions if left_index <= index <= left_index + 6]
                if following:
                    markers.append((left_index, min(following)))
        for start, end in markers:
            prefix = clause[max(0, start - 5) : start]
            postfix = clause[end + 1 : end + 9]
            marker_negated = bool(
                english_negative
                or
                any(token in prefix_negators for token in prefix)
                or any(token in postfix_negators for token in postfix)
                or (
                    any(token in postfix for token in {"never", "not"})
                    and any(
                        token in postfix
                        for token in {"applicable", "apply", "included", "relevant", "use", "used"}
                    )
                )
            )
            negated = negated or marker_negated
            positive = positive or not marker_negated

    chinese_clauses = [item for item in re.split(r"(?:但是|但|然而|而)", chinese) if item]
    chinese_marker_pattern = r"(?:公开市场|零售|公开年报|年报|年度报告|官方公告|官方披露)"
    for clause in chinese_clauses:
        chinese_negative = bool(
            re.search(
                r"(?:并非|不(?:在范围|纳入|适用|能|可|应|予|作为)|无关|除外|排除|忽略|应予排除)",
                clause,
            )
        )
        for marker in re.finditer(chinese_marker_pattern, clause):
            prefix = clause[max(0, marker.start() - 12) : marker.start()]
            postfix = clause[marker.end() : marker.end() + 10]
            marker_negated = bool(
                chinese_negative
                or
                re.search(r"(?:不(?:是|属于|来自)?|不(?:应)?(?:使用|采用|适用)|非|排除)$", prefix)
                or re.match(r"(?:(?:将)?不(?:纳入|采用|使用|适用|应使用)|除外|排除)", postfix)
            )
            negated = negated or marker_negated
            positive = positive or not marker_negated
    return positive, negated


def _transaction_segments(value: str) -> list[tuple[list[str], str]]:
    """Keep public evidence markers scoped to the clause they qualify.

    Public-query authorization is intentionally conservative: punctuation and
    adversative conjunctions terminate a marker's authority. This prevents a
    harmless phrase in a later sentence from laundering a private transaction
    phrase in an earlier one.
    """
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    raw_segments = re.split(
        r"[,.!?;:\n，。！？；：()（）\[\]【】/—–]+|\b(?:but|however|yet)\b|(?:但是|但|然而)",
        normalized,
        flags=re.IGNORECASE,
    )
    segments: list[tuple[list[str], str]] = []
    for segment in raw_segments:
        if not segment.strip():
            continue
        _, english, chinese = _semantic_forms(segment)
        segments.append((english, chinese))
    return segments


def _has_uncovered_private_transaction(value: str) -> bool:
    _normalized, whole_english, whole_chinese = _semantic_forms(value)
    if _generic_public_pricing_topic(whole_english, whole_chinese):
        return False
    return any(
        _private_transaction_semantics(english, chinese)
        for english, chinese in _transaction_segments(value)
    )


def _research_semantic_classes(value: str) -> dict[str, bool]:
    normalized, semantic_english, chinese_semantic = _semantic_forms(value)
    private_transaction = _private_transaction_semantics(
        semantic_english, chinese_semantic
    )
    capitalized_tokens = [
        token
        for token in re.findall(r"\b[A-Z][A-Za-z0-9&.-]{1,}\b", normalized)
        if token.casefold() not in {"analyze", "research", "study", "investigate", "please", "global"}
    ]
    latin_tokens = re.findall(r"[A-Za-z][A-Za-z0-9&.-]*", normalized)
    unknown_latin_entity = bool(
        private_transaction
        and re.search(r"[一-鿿]", normalized)
        and any(
            len(token) >= 2 and token.casefold() not in _RESEARCH_LATIN_FUNCTION_WORDS
            for token in latin_tokens
        )
    )
    organization = bool(
        re.search(
            r"[一-鿿A-Za-z0-9·]{2,}(?:公司|集团|研究院|研究所|大学|医院|银行|基金会|协会|委员会|中心|机构|工厂|局)",
            normalized,
        )
        or bool(capitalized_tokens)
        or unknown_latin_entity
    )
    public_transaction_context, negated_public_transaction_context = (
        _public_transaction_semantics(semantic_english, chinese_semantic)
    )
    return {
        "confidential": any(term in normalized for term in _RESEARCH_CONFIDENTIAL_CN)
        or _contains_english_phrase(normalized, _RESEARCH_CONFIDENTIAL_EN),
        "internal_context": any(term in normalized for term in _RESEARCH_INTERNAL_CONTEXT_CN)
        or _contains_english_phrase(normalized, _RESEARCH_INTERNAL_CONTEXT_EN),
        "private_relation": any(term in normalized for term in _RESEARCH_RELATION_CN)
        or _contains_english_phrase(normalized, _RESEARCH_RELATION_EN),
        "private_transaction": private_transaction,
        "public_financial_metric": any(
            term in normalized for term in _RESEARCH_PUBLIC_FINANCIAL_CN
        )
        or _contains_english_phrase(normalized, _RESEARCH_PUBLIC_FINANCIAL_EN),
        "organization": organization,
        "public_context": any(term in normalized for term in _RESEARCH_PUBLIC_CONTEXT_CN)
        or _contains_english_phrase(normalized, _RESEARCH_PUBLIC_CONTEXT_EN),
        "public_transaction_context": public_transaction_context,
        "negated_public_transaction_context": negated_public_transaction_context,
    }


def _blocked_by_research_semantics(value: str) -> bool:
    classes = _research_semantic_classes(value)
    if classes["confidential"]:
        return True
    if classes["internal_context"] and (
        classes["private_relation"]
        or classes["private_transaction"]
        or classes["organization"]
    ):
        return True
    if classes["private_relation"] and (
        classes["private_transaction"] or classes["organization"]
    ):
        return True
    return _has_uncovered_private_transaction(value)


def _project_public_research_query(original_request: str) -> str:
    """Build a topic query only from the immutable user request."""
    projected = unicodedata.normalize("NFKC", str(original_request or ""))
    projected = re.sub(
        r"^\s*(?:(?:请(?:帮我)?|帮我)\s*)?(?:研究一下|分析一下|调研一下|研究|分析|调研)\s*",
        "",
        projected,
    )
    projected = re.sub(
        r"^\s*(?:please\s+)?(?:help\s+me\s+)?(?:analyze|research|study|investigate)\b(?:\s+(?:the|a|an)\b)?\s*",
        "",
        projected,
        flags=re.IGNORECASE,
    )
    projected = re.sub(r"\s*(?:并)?(?:形成|撰写|生成)(?:一份)?报告\s*$", "", projected)
    projected = re.sub(
        r"\s+(?:and\s+)?(?:write|create|generate|produce)\s+(?:a\s+)?report\s*$",
        "",
        projected,
        flags=re.IGNORECASE,
    )
    projected = re.sub(r"(?<=\S)(?=如何)", " ", projected)
    projected = re.sub(r"[与及、，,。.!！?？；;:：“”‘’()（）\[\]{}《》<>/\\|]+", " ", projected)
    return " ".join(projected.split())


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
            or any(term in value for term in _RESEARCH_HARD_INTERNAL_TERMS)
        )

    blocked = bool(
        classification in {"restricted", "custom", "private", "confidential"}
        or blocked_by_dlp(original_request)
        or blocked_by_dlp(text)
        or _blocked_by_research_semantics(original_request)
        or _blocked_by_research_semantics(text)
    )
    safe_query = _project_public_research_query(original_request)
    blocked = blocked or not safe_query
    if blocked:
        return {
            "authorized": False,
            "reason_code": "policy_blocked",
            "safe_reason": "研究查询命中公共外发的数据防泄漏规则",
        }
    return {
        "authorized": True,
        "safe_query": safe_query,
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
