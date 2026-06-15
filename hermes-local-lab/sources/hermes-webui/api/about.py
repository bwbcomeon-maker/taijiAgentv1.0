"""Developer-editable product About copy.

This file is the single source for the Settings > About text. Edit it before
building a release package; Linux packaging compiles WebUI Python sourceless
and removes .py files, so installed users do not get a runtime copy editor.
"""

from __future__ import annotations

from copy import deepcopy


ABOUT_COPY = {
    "product_name": "太极 Agent",
    "copyright_owner": "太极 Agent 项目组",
    "description": "太极 Agent 是面向本地工作流的智能体工作台，用于对话协作、专家团执行、文档与项目辅助处理。",
    "license_notice": "本产品仅在授权范围内使用，相关模型、插件和第三方组件遵循各自许可协议。",
    "highlights": [
        "本地优先：会话、工作区和运行状态围绕本机环境组织。",
        "协作增强：支持多轮对话、专家团流程和长文档辅助处理。",
        "可控交付：版本信息和产品说明随发行包固化，避免普通用户误改。",
    ],
    "developer_note": "关于页文案由开发人员在源码 api/about.py 中维护；重新打包后随产品版本固化。",
}


def get_about_payload(
    *,
    webui_version: str | None = None,
    agent_version: str | None = None,
) -> dict:
    """Return the About payload with current version labels."""
    payload = deepcopy(ABOUT_COPY)
    if webui_version is None or agent_version is None:
        try:
            from api.updates import AGENT_VERSION, WEBUI_VERSION

            if webui_version is None:
                webui_version = WEBUI_VERSION
            if agent_version is None:
                agent_version = AGENT_VERSION
        except Exception:
            if webui_version is None:
                webui_version = "unknown"
            if agent_version is None:
                agent_version = "not detected"
    payload["webui_version"] = str(webui_version or "unknown")
    payload["agent_version"] = str(agent_version or "not detected")
    return payload
