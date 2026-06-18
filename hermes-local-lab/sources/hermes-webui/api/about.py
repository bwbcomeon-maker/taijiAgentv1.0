"""Developer-editable product About copy.

Edit ABOUT_DESCRIPTION before building a release package. Linux packaging
compiles WebUI Python sourceless and removes .py files, so installed users do
not get a runtime copy editor.
"""

from __future__ import annotations


# Developer edit point: change this text before packaging.
ABOUT_DESCRIPTION = (
    "乾元版 v0.1.7743 © 太极计算机股份有限公司，版权所有。"
)


def get_about_payload() -> dict:
    """Return the single About description shown by the Settings page."""
    return {"description": ABOUT_DESCRIPTION.strip()}
