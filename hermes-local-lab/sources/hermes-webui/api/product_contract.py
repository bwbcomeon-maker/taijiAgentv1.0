"""Stable, public-safe product error contracts for the desktop application.

Raw exceptions are deliberately not accepted by :func:`build_product_error`.
Desktop callers receive a small allowlisted envelope while detailed failures
remain in local server logs.
"""

from __future__ import annotations

import re
import secrets
import logging
from typing import Final, Mapping


ERROR_SCHEMA: Final = "taiji.product.error.v1"
_INCIDENT_RE = re.compile(r"^inc-[0-9a-f]{12,32}$")
_MAX_PUBLIC_TEXT = 240
_PRODUCT_ERROR_LOGGER = logging.getLogger("taiji.product_error")

_RECOVERY_ACTIONS: Final = {
    "retry": {"id": "retry", "label": "重试"},
    "restart_app": {"id": "restart_app", "label": "重启应用"},
    "open_model_settings": {"id": "open_model_settings", "label": "打开模型配置"},
    "open_security_settings": {"id": "open_security_settings", "label": "打开安全设置"},
    "open_license": {"id": "open_license", "label": "打开授权管理"},
    "regenerate": {"id": "regenerate", "label": "重新生成"},
    "open_result": {"id": "open_result", "label": "查看文档成果"},
    "open_office_review": {"id": "open_office_review", "label": "打开 Office 验收"},
    "export_diagnostics": {"id": "export_diagnostics", "label": "导出诊断"},
}

_ERROR_CATALOG: Final = {
    "agent_unavailable": {
        "title": "本地服务暂不可用",
        "message": "太极智能体尚未准备完成，请稍后重试。",
        "actions": ("retry", "restart_app", "export_diagnostics"),
        "retryable": True,
    },
    "backend_unavailable": {
        "title": "本地服务暂不可用",
        "message": "太极智能体的本地服务尚未准备完成，请稍后重试。",
        "actions": ("retry", "restart_app", "export_diagnostics"),
        "retryable": True,
    },
    "gateway_unavailable": {
        "title": "本地任务服务暂不可用",
        "message": "本地任务服务尚未准备完成，请稍后重试。",
        "actions": ("retry", "restart_app", "export_diagnostics"),
        "retryable": True,
    },
    "model_configuration_required": {
        "title": "模型配置待完成",
        "message": "请先完成模型配置，再重新执行此操作。",
        "actions": ("open_model_settings", "export_diagnostics"),
        "retryable": False,
    },
    "permission_denied": {
        "title": "当前操作未获授权",
        "message": "请检查安全模式或联系管理员确认操作权限。",
        "actions": ("open_security_settings", "export_diagnostics"),
        "retryable": False,
    },
    "license_blocked": {
        "title": "授权状态需要处理",
        "message": "当前授权不可用，请先在授权管理中完成处理。",
        "actions": ("open_license", "export_diagnostics"),
        "retryable": False,
    },
    "artifact_generation_failed": {
        "title": "文档生成未完成",
        "message": "文档成果未能生成，请重试或重新生成。",
        "actions": ("retry", "regenerate", "export_diagnostics"),
        "retryable": True,
    },
    "office_review_required": {
        "title": "文档仍待办公软件复核",
        "message": "请在 WPS 或 Word 中检查文档后，再确认交付结果。",
        "actions": ("open_office_review", "export_diagnostics"),
        "retryable": False,
    },
    "diagnostics_unavailable": {
        "title": "安全诊断暂不可用",
        "message": "暂时无法生成安全诊断，请稍后重试。",
        "actions": ("retry", "restart_app"),
        "retryable": True,
    },
    "unknown_error": {
        "title": "操作未能完成",
        "message": "应用遇到暂时性问题，请重试或导出诊断。",
        "actions": ("retry", "export_diagnostics"),
        "retryable": True,
    },
}


def _new_incident_id() -> str:
    return f"inc-{secrets.token_hex(6)}"


def _safe_incident_id(value: object) -> str:
    candidate = str(value or "").strip()
    return candidate if _INCIDENT_RE.fullmatch(candidate) else _new_incident_id()


def safe_public_text(value: object) -> str:
    """Return a short single-line string with common local secrets removed.

    This helper is a last-resort guard for product-facing copy. Product error
    envelopes should still prefer fixed catalog text over exception messages.
    """

    text = str(value or "")
    sensitive_keys = (
        r"password|passwd|passphrase|api[_-]?key|api[_-]?token|"
        r"access[_-]?token|secret|token"
    )
    substitutions = (
        (
            rf'(?i)"(?:{sensitive_keys})"\s*:\s*'
            r'(?:(?:"(?:\\.|[^"\\])*")|(?:\'(?:\\.|[^\'\\])*\')|[^,\s}\]]+)',
            "[已隐藏敏感配置]",
        ),
        (
            rf"(?i)'(?:{sensitive_keys})'\s*:\s*"
            r"(?:(?:'(?:\\.|[^'\\])*')|(?:\"(?:\\.|[^\"\\])*\")|[^,\s}\]]+)",
            "[已隐藏敏感配置]",
        ),
        (r"(?i)authorization\s*:\s*bearer\s+[^\s,;]+", "[已隐藏认证信息]"),
        (r"(?i)\bbearer\s+[^\s,;]+", "[已隐藏认证信息]"),
        (
            rf"(?i)\b(?:{sensitive_keys})\b\s*[:=]\s*"
            r"(?:(?:\"(?:\\.|[^\"\\])*\")|(?:'(?:\\.|[^'\\])*')|[^\n,;}}\]]+)",
            "[已隐藏敏感配置]",
        ),
        (r"\b[A-Z][A-Z0-9_]{2,}\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\n,;]+)", "[已隐藏环境配置]"),
        (r"(?i)\bsk-[A-Za-z0-9_-]{3,}", "[已隐藏密钥]"),
        (r"(?i)hermes", "内部服务"),
        (r"(?is)\btraceback\b.*", "[已隐藏内部错误]"),
        (r"(?i)(?<![A-Za-z0-9_])[A-Z]:\\[^\n,;]+", "[已隐藏本地路径]"),
        (r"(?<![A-Za-z0-9_:])/(?!/)[^\n,;]+", "[已隐藏本地路径]"),
    )
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text)
    text = " ".join(text.split()).strip()
    if not text:
        text = "详细信息已隐藏。"
    if len(text) > _MAX_PUBLIC_TEXT:
        text = text[: _MAX_PUBLIC_TEXT - 1].rstrip() + "…"
    return text


def build_product_error(code: object, *, incident_id: object = None) -> dict:
    """Build an allowlisted product error envelope."""

    safe_code = str(code or "").strip()
    if safe_code not in _ERROR_CATALOG:
        safe_code = "unknown_error"
    spec = _ERROR_CATALOG[safe_code]
    return {
        "schema": ERROR_SCHEMA,
        "code": safe_code,
        "title": spec["title"],
        "message": spec["message"],
        "recovery_actions": [dict(_RECOVERY_ACTIONS[action]) for action in spec["actions"]],
        "incident_id": _safe_incident_id(incident_id),
        "retryable": bool(spec["retryable"]),
    }


def attach_product_error(
    payload: Mapping[str, object] | None,
    code: object,
    *,
    incident_id: object = None,
) -> dict:
    """Preserve a legacy payload while adding the stable product envelope."""

    result = dict(payload or {})
    envelope = build_product_error(code, incident_id=incident_id)
    result["product_error"] = envelope
    # Every user-visible incident identifier must also exist in local logs.
    # Keep this record deliberately small and allowlisted; detailed exceptions
    # remain the responsibility of the emitting module's existing logger.
    _PRODUCT_ERROR_LOGGER.warning(
        "product_error code=%s incident_id=%s",
        envelope["code"],
        envelope["incident_id"],
    )
    return result
