"""Developer-editable product About copy.

This file is the single source for the Settings > About text. Edit it before
building a release package; Linux packaging compiles WebUI Python sourceless
and removes .py files, so installed users do not get a runtime copy editor.
"""

from __future__ import annotations

from copy import deepcopy


ABOUT_COPY = {
    "menu_label": "关于",
    "title": "关于",
    "subtitle": "产品版本、版权归属和发行说明。",
    "success_status": "关于信息已随当前版本固化。",
    # value=None means "use the detected build version". Set value to a string
    # before packaging when a release needs fixed manually maintained text.
    "version_items": [
        {
            "id": "webui",
            "label": "WebUI",
            "separator": ": ",
            "source": "webui_version",
            "value": None,
            "fallback": "unknown",
        },
        {
            "id": "agent",
            "label": "Agent",
            "separator": ": ",
            "source": "agent_version",
            "value": None,
            "fallback": "not detected",
        },
    ],
    "sections": [
        {
            "id": "product_name",
            "label": "产品名称",
            "kind": "heading",
            "body": "太极 Agent",
        },
        {
            "id": "description",
            "label": "产品说明",
            "kind": "text",
            "body": "太极 Agent 是面向本地工作流的智能体工作台，用于对话协作、专家团执行、文档与项目辅助处理。",
        },
        {
            "id": "highlights",
            "label": "主要能力",
            "kind": "list",
            "items": [
                "本地优先：会话、工作区和运行状态围绕本机环境组织。",
                "协作增强：支持多轮对话、专家团流程和长文档辅助处理。",
                "可控交付：版本信息和产品说明随发行包固化，避免普通用户误改。",
            ],
        },
        {
            "id": "copyright_license",
            "label": "版权与许可",
            "kind": "paragraphs",
            "paragraphs": [
                "版权所有 © 2026 太极 Agent 项目组。保留所有权利。",
                "本产品仅在授权范围内使用，相关模型、插件和第三方组件遵循各自许可协议。",
            ],
        },
        {
            "id": "maintenance",
            "label": "维护方式",
            "kind": "paragraphs",
            "paragraphs": [
                "关于页文案由开发人员在源码 api/about.py 中维护；重新打包后随产品版本固化。",
            ],
        },
    ],
}


def _detect_versions(
    *,
    webui_version: str | None,
    agent_version: str | None,
) -> tuple[str, str]:
    if webui_version is None or agent_version is None:
        try:
            from api.updates import AGENT_VERSION, WEBUI_VERSION

            if webui_version is None:
                webui_version = WEBUI_VERSION
            if agent_version is None:
                agent_version = AGENT_VERSION
        except Exception:
            pass
    return str(webui_version or "unknown"), str(agent_version or "not detected")


def _resolve_version_items(
    payload: dict,
    *,
    webui_version: str,
    agent_version: str,
) -> None:
    source_values = {
        "webui_version": webui_version,
        "agent_version": agent_version,
    }
    for item in payload.get("version_items", []):
        fallback = str(item.get("fallback") or "")
        configured_value = item.get("value")
        if configured_value is None:
            value = source_values.get(str(item.get("source") or ""), fallback)
        else:
            value = configured_value
        value = str(value or fallback)
        label = str(item.get("label") or "")
        separator = str(item.get("separator") or "")
        item["value"] = value
        item["display_text"] = f"{label}{separator}{value}" if label else value


def get_about_payload(
    *,
    webui_version: str | None = None,
    agent_version: str | None = None,
) -> dict:
    """Return the About payload exactly shaped for the Settings page."""
    payload = deepcopy(ABOUT_COPY)
    resolved_webui, resolved_agent = _detect_versions(
        webui_version=webui_version,
        agent_version=agent_version,
    )
    _resolve_version_items(
        payload,
        webui_version=resolved_webui,
        agent_version=resolved_agent,
    )
    payload["webui_version"] = resolved_webui
    payload["agent_version"] = resolved_agent
    return payload
