from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
COMMANDS_JS = (REPO_ROOT / "static" / "commands.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
PANELS_JS = (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")
SESSIONS_JS = (REPO_ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
MESSAGES_JS = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")
UI_JS = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")


def test_writeflow_slash_command_and_autocomplete_are_registered():
    assert "name:'writeflow'" in COMMANDS_JS
    assert "cmdWriteflow" in COMMANDS_JS
    assert "writeflow:{desc:" in COMMANDS_JS
    assert "'start','status','next','redo','skip','export','style','extract','开始','状态','继续','返工','跳过','导出','风格','提取'" in COMMANDS_JS
    assert "'继续':'next'" in COMMANDS_JS


def test_writeflow_compose_feeds_existing_send_chain():
    fn_start = COMMANDS_JS.index("async function sendWriteflowAction")
    fn_body = COMMANDS_JS[fn_start : COMMANDS_JS.index("function cmdWriteflow", fn_start)]

    assert "api('/api/writeflow/compose'" in fn_body
    assert "await send();" in fn_body
    assert "body.summon_only" in fn_body
    assert "_queueWriteflowStatusCard(data)" in fn_body
    assert "wantsNewSession" in fn_body
    assert "wantsNewWindow" in fn_body
    assert "await newSession(wantsNewSession)" in fn_body
    assert "window.open('about:blank','_blank')" in fn_body
    assert "await loadSession(previousSid)" in fn_body
    assert "await switchPanel('writing')" in fn_body
    assert "/api/chat/start" not in fn_body


def test_writing_expert_center_is_wired_to_rail_and_loader():
    assert 'data-panel="writing"' in INDEX_HTML
    assert 'id="panelWriting"' in INDEX_HTML
    assert 'id="mainWriting"' in INDEX_HTML
    assert 'id="writeflowTeamGrid"' in INDEX_HTML
    assert 'id="writeflowTeamSearch"' in INDEX_HTML
    assert 'id="writeflowCatalogStatus"' in INDEX_HTML
    assert 'id="writeflowTeamModal"' in INDEX_HTML
    assert 'id="writeflowRuns"' not in INDEX_HTML
    assert 'id="writeflowProjects"' not in INDEX_HTML
    assert "写作专家中心" in INDEX_HTML
    assert "独立写作对话" in INDEX_HTML
    assert "召唤" in PANELS_JS
    assert "确定方向" in PANELS_JS
    assert "生成初稿" in PANELS_JS
    assert "打磨发布" in PANELS_JS
    assert "_writeflowApplyServerTeams(data.teams)" in PANELS_JS
    assert "const WRITEFLOW_FALLBACK_TEAM" in PANELS_JS
    assert "if (nextPanel === 'writing') await loadWriteflow();" in PANELS_JS
    assert "writing-center-mode" in PANELS_JS
    assert "openFile(rel)" in PANELS_JS


def test_writing_expert_center_primary_copy_is_chinese_and_clean():
    panel = INDEX_HTML[INDEX_HTML.index('id="panelWriting"') : INDEX_HTML.index('<!-- Skills panel -->')]
    visible_panel = panel.split('>', 1)[1]
    main = INDEX_HTML[INDEX_HTML.index('id="mainWriting"') : INDEX_HTML.index('id="mainWorkspaces"')]

    visible_markup = visible_panel + main
    for text in (">Writing<", ">Project name<", ">Ask mode<", ">Start<", ">Next<", ">Redo<", ">Skip<", ">Export<", ">Style<", ">Extract<"):
        assert text not in visible_markup
    for text in ("写作专家中心", "选择一个写作专家团", "搜索专家团", "独立对话"):
        assert text in visible_panel + main
    for removed in ("项目名", "轻量写作", "基于当前需求开始", "继续", "调整要求", "查看状态", "更多操作", "专家团运行中"):
        assert removed not in visible_panel + main


def test_writeflow_expert_center_interactions_are_chat_first():
    assert "let WRITEFLOW_TEAMS = []" in PANELS_JS
    assert "function openWriteflowTeamModal" in PANELS_JS
    assert "function summonWriteflowTeam" in PANELS_JS
    assert "data-example-prompt" in PANELS_JS
    assert "modalPrompt.value = prompt" in PANELS_JS
    assert "sendWriteflowAction({" in PANELS_JS
    assert "team_id: team.id" in PANELS_JS
    assert "new_session: true" in PANELS_JS
    assert "open_new_window: true" in PANELS_JS
    assert "summon_only: false" in PANELS_JS


def test_writeflow_team_run_ui_is_kept_out_of_writing_catalog():
    assert 'id="writeflowRuns"' not in INDEX_HTML
    assert 'id="writeflowProjects"' not in INDEX_HTML
    assert ".layout.writing-center-mode .sidebar{display:none;}" in STYLE_CSS
    assert "main.main.showing-writing > #mainWriting{display:flex;overflow-y:auto;}" in STYLE_CSS


def test_writeflow_team_run_helpers_remain_available_for_chat_status():
    assert "function _writeflowRunCards" in PANELS_JS
    assert "data-writeflow-run" in PANELS_JS
    assert "任务列表 · ${done}/${total || 2} 已完成" in PANELS_JS
    assert "function _writeflowRunMembers" in PANELS_JS
    assert "function _writeflowTaskRows" in PANELS_JS
    assert "file_changes" in PANELS_JS
    assert "illustration_prompts" not in PANELS_JS
    assert ".writeflow-runs" in STYLE_CSS
    assert ".writeflow-run-progress" in STYLE_CSS
    assert ".writeflow-task-list" in STYLE_CSS
    assert ".writeflow-artifact.placeholder" in STYLE_CSS


def test_writeflow_chat_flow_uses_status_card_for_team_events():
    assert "function _writeflowStatusCardFromCompose" in COMMANDS_JS
    assert "专家团队运行" in COMMANDS_JS
    assert "type:'writeflow'" in COMMANDS_JS
    assert "members:visualMembers" in COMMANDS_JS
    assert "artifacts:visualArtifacts" in COMMANDS_JS
    assert "window._pendingWriteflowStatusCard" in COMMANDS_JS

    messages_js = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    assert "window._pendingWriteflowStatusCard" in messages_js
    assert "renderWriteflowStatusDock(pending.card)" in messages_js


def test_writeflow_status_card_is_visual_team_board():
    ui_js = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")

    assert 'id="writeflowStatusDock"' in INDEX_HTML
    assert "function _writeflowStatusCardFromRun" in ui_js
    assert "function _statusCardWriteflowHtml" in ui_js
    assert "function renderWriteflowStatusDock" in ui_js
    assert "function clearWriteflowStatusDock" in ui_js
    assert "return _statusCardWriteflowHtml(card,'')" in ui_js
    assert "data-status-card-kind=\"writeflow\"" in ui_js
    assert "status-card-writeflow-members" in ui_js
    assert "status-card-writeflow-avatar" in ui_js
    assert "status-card-writeflow-phases" in ui_js
    assert "status-card-writeflow-artifacts" in ui_js
    assert "function toggleWriteflowStatusCard" in ui_js
    assert "function openWriteflowArtifact" in ui_js
    assert "function downloadWriteflowArtifact" in ui_js
    assert "status-card-writeflow-toggle" in ui_js
    assert "status-card-writeflow-compact" in ui_js
    assert "status-card-writeflow-detail" in ui_js
    assert "data-writeflow-artifact-path" in ui_js
    assert "data-writeflow-artifact-download" in ui_js
    assert "reference_artifacts" in ui_js
    assert "历史参考材料" in ui_js
    assert "本轮产物" in ui_js
    assert ">打开<" in ui_js
    assert ">下载<" in ui_js
    assert "is-collapsed" in ui_js

    assert ".status-card-writeflow-members" in STYLE_CSS
    assert ".status-card-writeflow-member.running" in STYLE_CSS
    assert ".status-card-writeflow-avatar" in STYLE_CSS
    assert ".status-card-writeflow-phases" in STYLE_CSS
    assert ".status-card-writeflow-artifacts" in STYLE_CSS
    assert ".status-card-writeflow-toggle" in STYLE_CSS
    assert ".status-card-writeflow-compact" in STYLE_CSS
    assert ".status-card-writeflow-artifact-actions" in STYLE_CSS
    assert ".writeflow-status-dock" in STYLE_CSS
    assert ".status-card-writeflow.is-collapsed .status-card-writeflow-overview" in STYLE_CSS
    assert ".status-card-writeflow.is-collapsed .status-card-writeflow-detail" in STYLE_CSS


def test_writeflow_status_card_hydrates_into_composer_dock_on_session_load():
    assert "async function _hydrateWriteflowStatusCardForSession" in SESSIONS_JS
    assert "/api/writeflow/runs?session_id=" in SESSIONS_JS
    assert "data.session_run" in SESSIONS_JS
    assert "runs.find(item=>item&&item.session_id===sid)" in SESSIONS_JS
    assert "||runs[0]" not in SESSIONS_JS
    assert "_writeflowStatusCardFromRun(run,data)" in SESSIONS_JS
    assert "renderWriteflowStatusDock(card)" in SESSIONS_JS
    assert "const _WRITEFLOW_STATUS_REFRESH_MS = 5000" in SESSIONS_JS
    assert "function _scheduleWriteflowStatusRefresh" in SESSIONS_JS
    assert "setInterval(()=>{" in SESSIONS_JS
    assert "refreshWriteflowStatusDockForActiveSession()" in MESSAGES_JS
    assert "_removeWriteflowStatusCardsFromMessages()" in SESSIONS_JS
    assert "await _hydrateWriteflowStatusCardForSession(sid)" in SESSIONS_JS
    assert "renderWriteflowStatusDock(pending.card)" in MESSAGES_JS
    assert "_statusCard:pending.card" not in MESSAGES_JS
    assert "sourceSessionId:run.session_id||''" in COMMANDS_JS
    assert "sourceSid&&activeSid&&sourceSid!==activeSid" in UI_JS


def test_writeflow_styles_use_defined_theme_variables():
    writeflow_css = STYLE_CSS[STYLE_CSS.index("/* Writing workflow panel */") :]
    writeflow_css = writeflow_css[: writeflow_css.index("/* Token usage badge")]

    assert "var(--panel-bg)" not in writeflow_css
    assert "var(--hover)" not in writeflow_css
    assert "var(--hover-bg)" in writeflow_css
