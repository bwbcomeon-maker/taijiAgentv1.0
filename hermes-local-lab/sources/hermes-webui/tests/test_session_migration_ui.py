from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[1]
HTML = (ROOT / "static" / "index.html").read_text("utf-8")
BOOT = (ROOT / "static" / "boot.js").read_text("utf-8")
PANELS = (ROOT / "static" / "panels.js").read_text("utf-8")
UI = (ROOT / "static" / "ui.js").read_text("utf-8")
NODE = shutil.which("node")


class IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.nodes[attributes["id"]] = (tag, attributes)


def _nodes():
    parser = IdParser()
    parser.feed(HTML)
    return parser.nodes


def _extract_js_function(source, name):
    start = source.index(f"function {name}(")
    opening = source.index("{", start)
    depth = 0
    quote = None
    escaped = False
    for index in range(opening, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated JavaScript function: {name}")


def _migration_outcomes(cases):
    source = _extract_js_function(BOOT, "_legacyMigrationApplyOutcome")
    driver = f"""
{source}
const cases = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(cases.map(item =>
  _legacyMigrationApplyOutcome(item.fresh, item.applied)
)));
"""
    completed = subprocess.run(
        [NODE, "-e", driver, json.dumps(cases)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_bundle_and_plain_json_actions_are_visible_distinct_and_accessible():
    nodes = _nodes()
    for element_id in (
        "btnExportBundle", "btnExportJSON", "btnImportBundle", "btnImportJSON"
    ):
        tag, attrs = nodes[element_id]
        assert tag == "button"
        assert attrs.get("aria-label")
    assert "资源包 ZIP（含图片）" in HTML
    assert "兼容 JSON（仅文本）" in HTML
    assert "导入资源包 ZIP（含图片）" in HTML
    assert "导入兼容 JSON（仅文本）" in HTML
    tag, attrs = nodes["importBundleFileInput"]
    assert tag == "input" and "application/zip" in attrs["accept"]


def test_legacy_repair_card_is_discoverable_only_after_audit_and_announces_status():
    nodes = _nodes()
    _tag, card = nodes["legacyMigrationCard"]
    assert "hidden" in card
    assert card.get("role") == "region"
    _tag, status = nodes["legacyMigrationStatus"]
    assert status.get("role") == "status"
    assert status.get("aria-live") == "polite"
    _tag, toast = nodes["toast"]
    assert toast.get("role") == "status"
    assert toast.get("aria-live") == "polite"
    assert toast.get("aria-atomic") == "true"
    for element_id in ("btnAuditLegacySessions", "btnApplyLegacyMigration"):
        assert _nodes()[element_id][1].get("aria-label")


def test_bundle_upload_uses_raw_blob_fetch_and_never_generic_json_api_helper():
    assert "api/session/export-bundle" in BOOT
    assert "response.blob()" in BOOT
    assert "api/session/import-bundle" in BOOT
    assert "'Content-Type':'application/zip'" in BOOT
    assert "body:file" in BOOT
    bundle_block = BOOT[BOOT.index("async function importSessionBundle"):BOOT.index(
        "async function loadLegacyMigrationAudit"
    )]
    assert "api('/api/session/import-bundle'" not in bundle_block
    assert "30 * 1024 * 1024" in bundle_block


def test_migration_flow_shows_dry_run_then_requires_explicit_safe_confirmation():
    assert "loadLegacyMigrationAudit()" in PANELS
    assert "api('/api/session/migration/audit')" in BOOT
    assert "showConfirmDialog" in BOOT
    assert "focusCancel:true" in BOOT
    assert "confirm:true" in BOOT
    assert "先创建完整本地备份" in BOOT
    assert "不可追回" in BOOT
    assert "backup_path" not in BOOT


def test_migration_report_renders_safe_counts_without_internal_paths():
    for field in ("scanned", "modified", "skipped", "failed"):
        assert f"report.{field}" in BOOT
    assert "backup_created" in BOOT
    assert "report.quarantine_count" in BOOT
    assert "隔离待人工处理" in BOOT
    assert "legacyMigrationResult" in BOOT
    assert "report.backup_path" not in BOOT
    assert "report.needs_repair!==true" in BOOT
    assert "legacyMigrationBadge" in BOOT and "'已修复'" in BOOT and "'无需修复'" in BOOT
    assert "legacyMigrationTitle" in BOOT
    assert "'旧会话修复已完成'" in BOOT and "'旧会话无需修复'" in BOOT


def test_confirmation_escape_is_consumed_before_settings_shortcuts():
    start = UI.index("if(e.key==='Escape')", UI.index("function _ensureAppDialogBindings"))
    escape_block = UI[start:UI.index("return;", start)]
    assert "stopImmediatePropagation" in escape_block


def test_apply_awaits_a_fresh_authoritative_audit_before_rendering_success():
    start = BOOT.index("$('btnApplyLegacyMigration').onclick=async()=>")
    block = BOOT[start:BOOT.index("$('btnImportJSON').onclick", start)]
    apply_call = block.index("api('/api/session/migration/apply'")
    audit_call = block.index("await loadLegacyMigrationAudit()", apply_call)
    assert audit_call > apply_call
    assert "_renderLegacyMigrationReport(fresh,{forceVisible:true})" not in block
    render_call = block.index("_legacyMigrationPostApplyReport(fresh,applied)", audit_call)
    assert render_call > audit_call
    projection = BOOT[BOOT.index("function _legacyMigrationPostApplyReport"):BOOT.index(
        "function _renderLegacyMigrationReport"
    )]
    assert "needs_repair:fresh&&fresh.needs_repair===true" in projection
    assert "items:" not in projection


def test_busy_apply_has_bounded_request_and_retryable_safe_prompt():
    start = BOOT.index("$('btnApplyLegacyMigration').onclick=async()=>")
    block = BOOT[start:BOOT.index("$('btnImportJSON').onclick", start)]

    assert "timeoutMs:35000" in block
    assert "migration_state_busy" in block
    assert "当前仍有会话任务正在收尾，请稍后重试。" in block


@pytest.mark.skipif(NODE is None, reason="node is required")
def test_apply_outcome_rejects_failed_rollback_and_dirty_fresh_audit():
    cases = [
        {
            "fresh": {"needs_repair": True, "quarantine_count": 0},
            "applied": {"failed": 1, "items": [
                {"code": "migration_failed", "reason": "batch_rolled_back"}
            ]},
        },
        {
            "fresh": {"needs_repair": True, "quarantine_count": 1},
            "applied": {"failed": 1, "items": [
                {"code": "migration_failed", "reason": "rollback_incomplete"}
            ]},
        },
        {
            "fresh": {"needs_repair": True, "quarantine_count": 0},
            "applied": {"failed": 0, "items": []},
        },
        {
            "fresh": {"needs_repair": True, "quarantine_count": 2},
            "applied": {"failed": 0, "items": []},
        },
    ]
    failed, incomplete, dirty, quarantined = _migration_outcomes(cases)

    assert failed["kind"] == "error"
    assert failed["toast_type"] == "error"
    assert failed["success"] is False
    assert "完成" not in failed["toast"]
    assert "回滚" in failed["status"]

    assert incomplete["kind"] == "error"
    assert incomplete["toast_type"] == "error"
    assert incomplete["success"] is False
    assert "回滚未完整" in incomplete["status"]
    assert "完成" not in incomplete["toast"]

    for outcome in (dirty, quarantined):
        assert outcome["kind"] == "warning"
        assert outcome["toast_type"] == "warning"
        assert outcome["success"] is False
        assert "完成" not in outcome["toast"]
        assert "待处理" in outcome["status"]


@pytest.mark.skipif(NODE is None, reason="node is required")
def test_apply_outcome_allows_success_only_for_clean_zero_failure_audit():
    clean, clean_with_backup = _migration_outcomes([
        {
            "fresh": {"needs_repair": False, "quarantine_count": 0},
            "applied": {"failed": 0, "backup_created": False, "items": []},
        },
        {
            "fresh": {"needs_repair": False, "quarantine_count": 0},
            "applied": {"failed": 0, "backup_created": True, "items": []},
        },
    ])
    for outcome in (clean, clean_with_backup):
        assert outcome["kind"] == "success"
        assert outcome["toast_type"] == "success"
        assert outcome["success"] is True
        assert "完成" in outcome["toast"] or "无需修复" in outcome["toast"]
    assert "已创建本地备份" in clean_with_backup["toast"]
