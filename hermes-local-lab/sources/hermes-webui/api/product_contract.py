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
    "refresh": {"id": "refresh", "label": "刷新任务状态"},
    "start_new": {"id": "start_new", "label": "重新发起任务"},
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
        "message": "本次操作未完成，已保存的会话、任务规格和结果不会丢失。请稍后重试。",
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
    "provider_authorization_failed": {
        "title": "API Key 无效或已失效",
        "message": "当前模型服务拒绝了 API Key，可能已停用、删除或过期。会话内容已保留。",
        "actions": ("open_model_settings", "export_diagnostics"),
        "retryable": False,
    },
    "provider_account_unavailable": {
        "title": "模型服务账户不可用",
        "message": "当前账户的余额、额度或套餐不可用。请检查 Provider 账户或切换模型。",
        "actions": ("open_model_settings", "export_diagnostics"),
        "retryable": False,
    },
    "provider_rate_limited": {
        "title": "模型服务暂时繁忙",
        "message": "任务规格和已有结果已保留。请稍后由你重试当前阶段，系统不会自动重复调用。",
        "actions": ("retry", "export_diagnostics"),
        "retryable": True,
    },
    "provider_timeout": {
        "title": "模型服务响应超时",
        "message": "任务规格和已有结果已保留。本次状态会先完成对账，请刷新后再决定是否重试。",
        "actions": ("refresh", "retry", "export_diagnostics"),
        "retryable": True,
    },
    "provider_model_unavailable": {
        "title": "当前模型不可用",
        "message": "模型可能已删除、停用或当前账户无权访问。请切换或重新配置模型。",
        "actions": ("open_model_settings", "export_diagnostics"),
        "retryable": False,
    },
    "provider_network_unavailable": {
        "title": "无法连接模型服务",
        "message": "请检查网络、代理和模型服务地址后重试。",
        "actions": ("retry", "export_diagnostics"),
        "retryable": True,
    },
    "provider_service_unavailable": {
        "title": "模型服务暂不可用",
        "message": "模型服务返回异常或正在过载，请稍后重试。",
        "actions": ("retry", "export_diagnostics"),
        "retryable": True,
    },
    "model_input_too_large": {
        "title": "输入内容过大",
        "message": "请缩减输入或附件，或新建对话后再试。",
        "actions": ("start_new", "export_diagnostics"),
        "retryable": False,
    },
    "provider_content_blocked": {
        "title": "模型服务拒绝了当前内容",
        "message": "请调整输入、缩小范围或切换模型后再试。",
        "actions": ("open_model_settings", "export_diagnostics"),
        "retryable": False,
    },
    "provider_request_rejected": {
        "title": "模型服务拒绝了请求",
        "message": "当前请求不符合模型服务协议或限制。请调整输入或切换模型。",
        "actions": ("open_model_settings", "export_diagnostics"),
        "retryable": False,
    },
    "gateway_authentication_failed": {
        "title": "本地任务服务认证失败",
        "message": "请重启应用。这不是 Provider API Key 问题，无需修改模型密钥。",
        "actions": ("restart_app", "export_diagnostics"),
        "retryable": False,
    },
    "session_persistence_failed": {
        "title": "错误状态未能保存",
        "message": "会话的待处理状态已保留，请导出诊断并重启应用后再试。",
        "actions": ("restart_app", "export_diagnostics"),
        "retryable": False,
    },
    "model_output_invalid": {
        "title": "生成结果格式异常",
        "message": "任务规格和资料已保留，这份异常结果未被采用。请由你重新生成当前阶段。",
        "actions": ("regenerate", "export_diagnostics"),
        "retryable": True,
    },
    "expert_team_content_blocked": {
        "title": "阶段内容需要处理",
        "message": "本次生成内容已保留，但存在必须处理的阻断项。请查看问题后重新生成当前阶段。",
        "actions": ("open_result", "regenerate", "export_diagnostics"),
        "retryable": True,
    },
    "expert_team_evidence_required": {
        "title": "研究依据需要补充",
        "message": "本次阶段结果、任务规格和现有资料已保留。当前冻结规格中的依据不足，直接重试不会增加资料。请重新发起任务，补充资料或缩小研究范围后再生成。",
        "actions": ("open_result", "start_new", "export_diagnostics"),
        "retryable": False,
    },
    "expert_team_state_conflict": {
        "title": "任务状态已更新",
        "message": "当前任务已被另一个请求推进，你的草稿和已有结果已保留。请刷新后按最新状态操作。",
        "actions": ("refresh", "export_diagnostics"),
        "retryable": True,
    },
    "expert_team_in_progress": {
        "title": "当前阶段正在处理",
        "message": "同一阶段已有生成任务在运行，已有进度和结果不会丢失。请等待状态更新，不要重复发起。",
        "actions": ("refresh", "export_diagnostics"),
        "retryable": True,
    },
    "expert_team_not_found": {
        "title": "未找到当前专家团任务",
        "message": "当前会话中未找到这项任务，其他任务和已有交付文件已保留、不受影响。请返回会话列表重新打开。",
        "actions": ("refresh", "export_diagnostics"),
        "retryable": False,
    },
    "expert_team_source_invalid": {
        "title": "资料校验未通过",
        "message": "任务规格和已添加资料已保留，本次未调用模型。资料数量或完整性与当前任务合同不一致，请重新发起任务并按页面提示添加资料。",
        "actions": ("start_new", "export_diagnostics"),
        "retryable": False,
    },
    "document_render_failed": {
        "title": "DOCX 生成未完成",
        "message": "已确认的正文已保留，无需重做内容。请只重新生成并检查 DOCX。",
        "actions": ("retry", "open_result", "export_diagnostics"),
        "retryable": True,
    },
    "document_open_failed": {
        "title": "未能打开交付文件",
        "message": "交付文件已保留，不会丢失。请重试，或打开文件夹后使用 WPS/Word 打开。",
        "actions": ("retry", "export_diagnostics"),
        "retryable": True,
    },
    "delivery_copy_failed": {
        "title": "副本保存未完成",
        "message": "原交付文件已保留，不会丢失。请选择另一个可写目录后重试。",
        "actions": ("retry", "export_diagnostics"),
        "retryable": True,
    },
    "diagnostics_unavailable": {
        "title": "运行检查暂不可用",
        "message": "暂时无法完成运行检查，请稍后重试。",
        "actions": ("retry", "restart_app"),
        "retryable": True,
    },
    "unknown_error": {
        "title": "操作未能完成",
        "message": "本次操作未完成，已保存的任务规格和结果不会丢失。请刷新后重试或导出诊断。",
        "actions": ("refresh", "retry", "export_diagnostics"),
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
