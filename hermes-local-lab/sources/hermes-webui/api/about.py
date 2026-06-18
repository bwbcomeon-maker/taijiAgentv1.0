"""Developer-editable product About copy.

Edit ABOUT_DESCRIPTION before building a release package. Linux packaging
compiles WebUI Python sourceless and removes .py files, so installed users do
not get a runtime copy editor.
"""

from __future__ import annotations


# Developer edit point: change this text before packaging.
ABOUT_DESCRIPTION = (
    "太极智能体 桌面版 是面向本地工作流的智能体工作台，用于对话协作、专家团执行、"
    "文档与项目辅助处理等场景。当前产品由太极计算机股份有限公司Agent项目组维护，"
    "乾元版 v0.1.7743 © 太极计算机股份有限公司，版权所有。"
    "本产品仅在授权范围内使用，相关模型、插件及第三方组件遵循各自许可协议。"
)


def get_about_payload() -> dict:
    """Return the single About description shown by the Settings page."""
    return {"description": ABOUT_DESCRIPTION.strip()}
