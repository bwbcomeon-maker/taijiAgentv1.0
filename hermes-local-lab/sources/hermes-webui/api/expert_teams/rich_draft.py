"""Rich draft contract for plan-like expert-team outputs."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


PLAN_LIKE_MATERIAL_TYPES = {"plan", "research_report"}
PLAN_LIKE_TASK_IDS = {"draft"}
MIN_TABLES = 2
MIN_FIGURES = 1


def is_rich_draft_required(material_type: str, task_id: str, team_id: str = "") -> bool:
    material = str(material_type or "").strip()
    task = str(task_id or "").strip()
    return material in PLAN_LIKE_MATERIAL_TYPES and task in PLAN_LIKE_TASK_IDS


def markdown_table_count(text: str) -> int:
    lines = (text or "").splitlines()
    count = 0
    for index in range(len(lines) - 1):
        header = lines[index].strip()
        separator = lines[index + 1].strip()
        if "|" not in header or "|" not in separator:
            continue
        if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", separator):
            count += 1
    return count


def figure_reference_count(text: str) -> int:
    raw = text or ""
    markdown_images = len(re.findall(r"!\[[^\]]+\]\([^)]+\)", raw))
    figure_sections = len(re.findall(r"(架构图|流程图|用例图|部署图|路线图|图示设计|图示说明)", raw))
    return max(markdown_images, figure_sections)


def validate_rich_draft_text(text: str) -> dict:
    tables = markdown_table_count(text)
    figures = figure_reference_count(text)
    missing = []
    if tables < MIN_TABLES:
        missing.append(f"至少 {MIN_TABLES} 个 Markdown 表格")
    if figures < MIN_FIGURES:
        missing.append("至少 1 个架构图、流程图、用例图或图示引用")
    if missing:
        return {
            "status": "rewrite_required",
            "violations": [],
            "missing_sections": missing,
            "message": "方案类初稿必须在生成阶段包含表格和图示，请重新生成富内容初稿：" + "、".join(missing) + "。",
        }
    return {"status": "pass", "violations": [], "missing_sections": [], "message": ""}


def _safe_slug(value: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "-", str(value or "").strip())
    raw = raw.strip("-_")
    return raw[:48] or "rich-draft"


def _architecture_svg(title: str) -> str:
    safe_title = str(title or "方案总体架构图").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    boxes = [
        ("用户入口", 80, 110),
        ("智能编排", 260, 110),
        ("业务系统", 440, 110),
        ("数据台账", 260, 250),
        ("监督闭环", 440, 250),
    ]
    rects = []
    for label, x, y in boxes:
        rects.append(f'<rect x="{x}" y="{y}" width="130" height="54" rx="8" fill="#E8F3FF" stroke="#2F6BFF"/>')
        rects.append(f'<text x="{x + 65}" y="{y + 34}" text-anchor="middle" font-size="16" fill="#1F2937">{label}</text>')
    arrows = [
        '<path d="M210 137 L260 137" stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>',
        '<path d="M390 137 L440 137" stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>',
        '<path d="M325 164 L325 250" stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>',
        '<path d="M390 277 L440 277" stroke="#475569" stroke-width="2" marker-end="url(#arrow)"/>',
    ]
    return "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" width="680" height="380" viewBox="0 0 680 380">',
            '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L6,3 z" fill="#475569"/></marker></defs>',
            '<rect width="680" height="380" fill="#FFFFFF"/>',
            f'<text x="340" y="52" text-anchor="middle" font-size="22" font-weight="700" fill="#111827">{safe_title}</text>',
            *rects,
            *arrows,
            '<text x="340" y="344" text-anchor="middle" font-size="14" fill="#64748B">该图由富内容初稿阶段生成，供后续模板套用直接消费。</text>',
            "</svg>",
        ]
    )


def build_rich_draft_package(workspace: Path, run: dict, output: dict) -> dict:
    workspace_path = Path(workspace)
    root = workspace_path / ".taiji" / "rich-drafts" / _safe_slug(str(run.get("run_id") or "run"))
    assets_dir = root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    title = str(output.get("title") or run.get("title") or "方案初稿")
    content = str(output.get("content") or "").strip()
    draft_path = root / "draft.md"
    draft_path.write_text(content + "\n", encoding="utf-8")

    svg_path = assets_dir / "architecture.svg"
    svg_path.write_text(_architecture_svg(title), encoding="utf-8")

    manifest_path = root / "draft.manifest.json"
    package = {
        "version": 1,
        "kind": "rich_draft",
        "title": title,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "draft_path": str(draft_path.relative_to(workspace_path)),
        "manifest_path": str(manifest_path.relative_to(workspace_path)),
        "table_count": markdown_table_count(content),
        "figure_count": max(figure_reference_count(content), 1),
        "assets": [
            {
                "id": "architecture",
                "kind": "diagram",
                "title": "总体架构图",
                "path": str(svg_path.relative_to(workspace_path)),
                "status": "generated",
            }
        ],
    }
    manifest_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return package
