from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
COMMANDS_JS = (REPO_ROOT / "static" / "commands.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
PANELS_JS = (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")
SESSIONS_JS = (REPO_ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
MESSAGES_JS = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")
UI_JS = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
BOOT_JS = (REPO_ROOT / "static" / "boot.js").read_text(encoding="utf-8")
TAIJI_HOME_JS = (REPO_ROOT / "static" / "taiji-home.js").read_text(encoding="utf-8")


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
    assert "if(!wantsNewWindow&&typeof switchPanel==='function')await switchPanel('chat');" in fn_body
    assert "/api/chat/start" not in fn_body


def test_writeflow_explicit_new_window_branch_remains_compat_only():
    fn_start = COMMANDS_JS.index("async function sendWriteflowAction")
    fn_body = COMMANDS_JS[fn_start : COMMANDS_JS.index("function cmdWriteflow", fn_start)]

    assert "const wantsNewWindow=body.new_window===true||body.open_new_window===true;" in fn_body
    assert "if(wantsNewWindow&&typeof window!=='undefined'&&typeof window.open==='function')" in fn_body
    assert "window.open('about:blank','_blank')" in fn_body
    assert "if(wantsNewWindow&&S.session&&S.session.session_id)" in fn_body
    assert "await loadSession(previousSid)" in fn_body
    assert "await switchPanel('writing')" in fn_body


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
    assert "新的聊天任务" in INDEX_HTML
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
    for text in ("写作专家中心", "选择一个写作专家团", "搜索专家团", "聊天任务"):
        assert text in visible_panel + main
    for removed in ("项目名", "轻量写作", "基于当前需求开始", "继续", "调整要求", "查看状态", "更多操作", "专家团运行中"):
        assert removed not in visible_panel + main


def test_writeflow_expert_center_interactions_are_chat_first():
    fn_start = PANELS_JS.index("async function summonWriteflowTeam")
    fn_body = PANELS_JS[fn_start : PANELS_JS.index("function _writeflowModeLabel", fn_start)]

    assert "let WRITEFLOW_TEAMS = []" in PANELS_JS
    assert "function openWriteflowTeamModal" in PANELS_JS
    assert "function summonWriteflowTeam" in PANELS_JS
    assert "data-example-prompt" in PANELS_JS
    assert "modalPrompt.value = prompt" in PANELS_JS
    assert "sendExpertTeamAction({" in fn_body
    assert "sendWriteflowAction({" not in fn_body
    assert "team_id: team.id" in fn_body
    assert "new_session: true" in fn_body
    assert "open_new_window" not in fn_body
    assert "new_window" not in fn_body
    assert "summon_only: false" in fn_body


def test_writeflow_team_modal_uses_guided_studio_layout_without_new_actions():
    fn_start = PANELS_JS.index("function openWriteflowTeamModal")
    fn_body = PANELS_JS[fn_start : PANELS_JS.index("function closeWriteflowTeamModal", fn_start)]

    for expected in (
        "writeflow-modal-shell",
        "writeflow-modal-overview",
        "writeflow-modal-content",
        "writeflow-modal-guides",
        "writeflow-modal-template-list",
        "writeflow-modal-prompt-card",
        "writeflow-modal-footer-main",
    ):
        assert expected in fn_body
        assert expected in STYLE_CSS

    assert "<h4>能力介绍</h4>" in fn_body
    assert "<h4>团队成员</h4>" in fn_body
    assert "<h4>试试这样问我</h4>" in fn_body
    assert "writeflowTeamPrompt" in fn_body
    assert "summonWriteflowTeam()" in fn_body
    assert "save" not in fn_body.lower()
    assert "copy" not in fn_body.lower()


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
    assert "专家团运行" in COMMANDS_JS
    assert "type:'writeflow'" in COMMANDS_JS
    assert "members:visualMembers" in COMMANDS_JS
    assert "artifacts:visualArtifacts" in COMMANDS_JS
    assert "window._pendingWriteflowStatusCard" in COMMANDS_JS

    messages_js = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    assert "window._pendingWriteflowStatusCard" in messages_js
    assert "renderWriteflowStatusDock(pending.card)" in messages_js


def test_session_opening_paths_force_chat_view_for_deep_links():
    assert "async function openChatSession" in SESSIONS_JS
    assert "function _forceChatSessionPanel" in SESSIONS_JS
    assert "layoutEl.classList.remove('writing-center-mode')" in SESSIONS_JS
    assert "await switchPanel('chat')" in SESSIONS_JS
    assert "await openChatSession(saved,{skipLoad:true})" in BOOT_JS
    assert "openChatSessionFn(sid)" in TAIJI_HOME_JS
    assert "openChatSession(sid)" in UI_JS
    assert "await openChatSession(s.session_id)" in SESSIONS_JS
    assert "await openChatSession(seg.session_id)" in SESSIONS_JS
    assert "await openChatSession(child.session_id)" in SESSIONS_JS


def test_taiji_recent_sessions_use_display_titles_for_rows_and_search():
    assert "function taijiSessionDisplayTitle(session)" in TAIJI_HOME_JS
    assert "function taijiSessionSearchText(session)" in TAIJI_HOME_JS
    assert "session&&session.display_title" in TAIJI_HOME_JS
    assert "session&&session.writeflow_title" in TAIJI_HOME_JS
    assert "if(!taijiSessionSearchText(session).includes(q)) return false;" in TAIJI_HOME_JS
    assert "const title=escapeHtml(taijiSessionDisplayTitle(session));" in TAIJI_HOME_JS
    assert "const fullTitle=escapeHtml(taijiSessionFullTitle(session));" in TAIJI_HOME_JS
    assert 'class="taiji-session-title"' in TAIJI_HOME_JS
    assert "title=\"${fullTitle}\" aria-label=\"${fullTitle}\"" in TAIJI_HOME_JS
    assert "请【[^】]+】接手这个写作任务" in TAIJI_HOME_JS
    assert r"/^召唤[^：:\n]{0,64}专家团[：:]\s*/" in TAIJI_HOME_JS
    assert "taijiCompactTopic(displayTitle)" in TAIJI_HOME_JS
    display_start = TAIJI_HOME_JS.index("function taijiSessionDisplayTitle(session)")
    display_body = TAIJI_HOME_JS[display_start : TAIJI_HOME_JS.index("function taijiSessionFullTitle", display_start)]
    assert "return `${label||taijiWriteflowTeamLabel(session,rawTitle)}｜${topic||'写作项目'}`;" not in display_body
    assert "return topic||taijiCompactTopic(label)||'写作项目';" in display_body
    assert "return topic||'写作项目';" in display_body
    assert "session.title||'未命名会话'" not in TAIJI_HOME_JS
    assert ".taiji-session-card .taiji-session-title{" in STYLE_CSS
    assert ":root[data-skin] .taiji-home-shell .taiji-session-card .taiji-session-title{" in STYLE_CSS
    assert "text-overflow:ellipsis!important;" in STYLE_CSS


def test_new_chat_clears_writeflow_status_dock():
    fn_start = SESSIONS_JS.index("async function newSession")
    fn_body = SESSIONS_JS[fn_start : SESSIONS_JS.index("async function loadSession", fn_start)]
    load_start = SESSIONS_JS.index("async function loadSession")
    load_body = SESSIONS_JS[load_start : SESSIONS_JS.index("function _forceChatSessionPanel", load_start)]

    assert "function _resetWriteflowDockForSessionChange" in SESSIONS_JS
    assert "_resetWriteflowDockForSessionChange('new-session-start')" in fn_body
    assert "_resetWriteflowDockForSessionChange('new-session-ready')" in fn_body
    assert "_resetWriteflowDockForSessionChange('load-session')" in load_body
    assert "window._pendingWriteflowStatusCard=null" in SESSIONS_JS
    assert "_stopWriteflowStatusRefresh()" in SESSIONS_JS
    assert "_removeWriteflowStatusCardsFromMessages()" in SESSIONS_JS
    assert "clearWriteflowStatusDock()" in SESSIONS_JS
    assert "_resetWriteflowDockForSessionChange('new-chat-empty-focus')" in BOOT_JS
    assert "_resetWriteflowDockForSessionChange('new-chat-shortcut-empty-focus')" in BOOT_JS


def test_taiji_shell_breakpoint_keeps_electron_1024_in_desktop_shell():
    assert "@media (min-width:901px)" in STYLE_CSS
    assert "@media (max-width:900px)" in STYLE_CSS
    assert "@media (max-width:1023px)" not in STYLE_CSS
    assert "@media (min-width:1024px)" not in STYLE_CSS[STYLE_CSS.index("/* Taiji desktop home shell"):]
    assert ".taiji-home-shell{" in STYLE_CSS
    assert ".taiji-home-shell{\n    display:none!important;\n  }" in STYLE_CSS


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
    assert "status-card-writeflow-mini" in ui_js
    assert "status-card-writeflow-mini-avatar" in ui_js
    assert "bottom-dock-v1-expanded" in ui_js
    assert "compact-v2-expanded" in ui_js
    assert "_statusCardWriteflowLegacyStorageKey" in ui_js
    assert "_statusCardStateClass(member.status)==='running'" in ui_js
    assert "card.querySelectorAll('.status-card-writeflow-toggle').forEach" in ui_js
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
    assert ".status-card-writeflow-mini" in STYLE_CSS
    assert ".status-card-writeflow.is-collapsed .status-card-writeflow-mini{display:grid;}" in STYLE_CSS
    assert ".status-card-writeflow.is-collapsed .status-card-writeflow-head,.status-card-writeflow.is-collapsed .status-card-writeflow-body{display:none;}" in STYLE_CSS
    assert ".writeflow-status-dock .status-card-writeflow.is-collapsed{max-height:72px;" in STYLE_CSS
    assert ".writeflow-status-dock .status-card-writeflow.is-expanded{max-height:min(38vh,420px);" in STYLE_CSS
    assert ".status-card-writeflow-artifact-actions" in STYLE_CSS
    assert ".writeflow-status-dock" in STYLE_CSS
    assert "function _isExpertTeamStatusCard" in ui_js
    assert "renderExpertTeamWorkspacePanel(card)" in ui_js
    assert "else clearExpertTeamWorkspacePanel();" in ui_js
    assert ".taiji-home-shell.taiji-expert-team-active #writeflowStatusDock .status-card-writeflow.is-expanded" in STYLE_CSS
    assert ".taiji-home-shell.taiji-expert-team-active #writeflowStatusDock .status-card-expert-bottom-body" in STYLE_CSS
    assert ".taiji-home-shell.taiji-expert-team-active .expert-team-workspace-panel{display:none!important;}" in STYLE_CSS


def test_writeflow_status_card_hydrates_into_composer_dock_on_session_load():
    assert "async function _hydrateWriteflowStatusCardForSession" in SESSIONS_JS
    assert "/api/writeflow/run?session_id=" in SESSIONS_JS
    assert "data&&data.run&&data.run.session_id===sid" in SESSIONS_JS
    assert "/api/writeflow/runs?session_id=" not in SESSIONS_JS
    assert "recover=1" not in SESSIONS_JS
    assert "||runs[0]" not in SESSIONS_JS
    assert "_writeflowStatusCardFromRun(run,data)" in SESSIONS_JS
    assert "renderWriteflowStatusDock(card)" in SESSIONS_JS
    assert "const _WRITEFLOW_STATUS_REFRESH_MS = 5000" in SESSIONS_JS
    assert "function _scheduleWriteflowStatusRefresh" in SESSIONS_JS
    assert "setInterval(()=>{" in SESSIONS_JS
    assert "refreshWriteflowStatusDockForActiveSession()" in MESSAGES_JS
    assert "_removeWriteflowStatusCardsFromMessages()" in SESSIONS_JS
    assert "function _isWriteflowHydrationForActiveSession" in SESSIONS_JS
    assert "!_isWriteflowHydrationForActiveSession(sid)" in SESSIONS_JS
    assert "await _hydrateWriteflowStatusCardForSession(sid)" in SESSIONS_JS
    assert "renderWriteflowStatusDock(pending.card)" in MESSAGES_JS
    assert "_statusCard:pending.card" not in MESSAGES_JS
    assert "sourceSessionId:run.session_id||''" in COMMANDS_JS
    assert "run.display_tasks" in COMMANDS_JS
    assert "run.display_tasks" in UI_JS
    assert "run.display_progress||run.progress" in UI_JS
    assert "if(!sourceSid||!activeSid||sourceSid!==activeSid)" in UI_JS
    assert "dock.dataset.writeflowSourceSessionId=sourceSid;" in UI_JS
    taiji_dock_start = STYLE_CSS.index(".taiji-home-shell #writeflowStatusDock{")
    taiji_dock_block = STYLE_CSS[taiji_dock_start : STYLE_CSS.index(".taiji-home-shell #composerWrap", taiji_dock_start)]
    assert "display:block!important;" in taiji_dock_block
    assert ".taiji-home-shell #writeflowStatusDock[hidden]" in taiji_dock_block
    assert "display:none!important;\n  }\n  .taiji-home-shell #composerWrap" not in taiji_dock_block


def test_writeflow_styles_use_defined_theme_variables():
    writeflow_css = STYLE_CSS[STYLE_CSS.index("/* Writing workflow panel */") :]
    writeflow_css = writeflow_css[: writeflow_css.index("/* Token usage badge")]

    assert "var(--panel-bg)" not in writeflow_css
    assert "var(--hover)" not in writeflow_css
    assert "var(--hover-bg)" in writeflow_css
