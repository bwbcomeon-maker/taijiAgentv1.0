#!/usr/bin/env python3
"""
Browser smoke for the expert-team artifact CTA.

This covers the failure mode that static tests missed: the "查看产物" button can
call a JS function while the user still sees no visible result.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


PORT = int(os.getenv("EXPERT_TEAM_SMOKE_PORT", "8797"))
BASE = f"http://127.0.0.1:{PORT}"
VIEWPORTS = [(1440, 900), (1280, 720), (1024, 720)]


def _wait_for_health(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=2) as res:
                if res.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def _prepare_expert_team_session(page, workspace):
    page.evaluate(
        """
        async ({workspace}) => {
          document.documentElement.dataset.taijiDesktop = '1';
          document.documentElement.dataset.skin = 'taiji-light-glass';
          window._uiVisibility = Object.assign({}, window._uiVisibility || {}, {
            composer: {
              profile: true,
              workspace_files: true,
              workspace_switcher: true,
              model: true,
              reasoning: true,
              toolsets: true,
              quota: true,
            },
          });
          if (typeof applyUiVisibility === 'function') applyUiVisibility();

          const response = await fetch('/api/session/new', {
            method: 'POST',
            credentials: 'include',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({workspace}),
          });
          if (!response.ok) {
            throw new Error(`session/new failed ${response.status}: ${await response.text()}`);
          }
          const payload = await response.json();
          if (!payload.session || !payload.session.session_id) {
            throw new Error(`session/new returned no session: ${JSON.stringify(payload)}`);
          }
          S.session = payload.session;
          S.messages = [];
          if (typeof syncTopbar === 'function') syncTopbar();
          if (typeof renderMessages === 'function') renderMessages();
          if (typeof switchPanel === 'function') await switchPanel('chat');
          if (typeof loadDir === 'function') {
            try { await loadDir('.'); } catch (_) {}
          }
        }
        """,
        {"workspace": workspace},
    )


def _render_smoke_card(page, done=False):
    page.evaluate(
        """
        ({done}) => {
          const base = {
            type: 'expert-team',
            kind: 'expert_team',
            runId: 'expert-team-artifact-smoke',
            sessionId: S.session.session_id,
            sourceSessionId: S.session.session_id,
            team: {id: 'content-creator-team', title: '内容创作专家团'},
            subtitle: '围绕企业为什么需要本地 AI Agent 工作台做一篇公众号长文',
            status: done ? 'done' : 'waiting_user',
            statusLabel: done ? '已完成' : '待确认',
            phase: done ? '交付' : '需求确认',
            phases: ['需求确认', '生成初稿', '打磨发布', '交付'],
            progress: {done: done ? 4 : 0, total: 4},
            members: [
              {id: 'flow', name: '流程编排', status: done ? 'done' : 'waiting'},
              {id: 'writer', name: '文案创作专家', status: done ? 'done' : 'idle'},
              {id: 'image', name: '配图专家', status: done ? 'done' : 'idle'},
              {id: 'review', name: '审稿润色', status: done ? 'done' : 'idle'},
            ],
            tasks: [
              {id: 'direction', title: '需求确认', worker_name: '流程编排', status: done ? 'done' : 'waiting', status_label: done ? '完成' : '待确认'},
              {id: 'draft', title: '撰写公众号长文', worker_name: '文案创作专家', status: done ? 'done' : 'idle', status_label: done ? '完成' : '待执行'},
              {id: 'image', title: '生成封面和文中配图', worker_name: '配图专家', status: done ? 'done' : 'idle', status_label: done ? '完成' : '待执行'},
              {id: 'delivery', title: '交付整理', worker_name: '审稿润色', status: done ? 'done' : 'idle', status_label: done ? '完成' : '待执行'},
            ],
            artifacts: done ? [
              {
                id: 'draft',
                label: '专家团生成结果',
                path: 'articles/expert-team-smoke.md',
                kind: 'md',
                exists: true,
                download_name: '专家团生成结果.md',
              },
            ] : [],
            questions: done ? [] : [
              {id: 'audience', title: '目标读者和发布口径', type: 'text', status: 'pending', required: true},
            ],
          };
          renderWriteflowStatusDock(base);
        }
        """,
        {"done": done},
    )


def _render_multi_question_card(page, answered_first=False):
    page.evaluate(
        """
        ({answeredFirst}) => {
          const questions = [
            {
              id: 'topic',
              title: '文章主题和核心观点',
              type: 'text',
              status: answeredFirst ? 'answered' : 'pending',
              answer: answeredFirst ? '本地优先 AI 助理' : '',
              required: true,
            },
            {
              id: 'audience',
              title: '目标读者和发布口径',
              type: 'text',
              status: 'pending',
              required: true,
            },
          ];
          const base = {
            type: 'expert-team',
            kind: 'expert_team',
            runId: 'expert-team-multi-question-smoke',
            sessionId: S.session.session_id,
            sourceSessionId: S.session.session_id,
            team: {id: 'content-creator-team', title: '内容创作专家团'},
            subtitle: '确认后保持展开验收',
            status: 'waiting_user',
            statusLabel: '待确认',
            phase: '需求确认',
            phases: ['需求确认', '生成初稿', '打磨发布', '交付'],
            progress: {done: answeredFirst ? 1 : 0, total: 4},
            members: [
              {id: 'flow', name: '流程编排', status: 'waiting'},
              {id: 'writer', name: '文案创作专家', status: 'idle'},
              {id: 'image', name: '配图专家', status: 'idle'},
              {id: 'review', name: '审稿润色', status: 'idle'},
            ],
            tasks: [
              {id: 'direction', title: '需求确认', worker_name: '流程编排', status: 'waiting', status_label: '待确认'},
              {id: 'draft', title: '撰写公众号长文', worker_name: '文案创作专家', status: 'idle', status_label: '待执行'},
              {id: 'image', title: '生成封面和文中配图', worker_name: '配图专家', status: 'idle', status_label: '待执行'},
              {id: 'delivery', title: '交付整理', worker_name: '审稿润色', status: 'idle', status_label: '待执行'},
            ],
            artifacts: [],
            questions,
          };
          renderWriteflowStatusDock(base);
        }
        """,
        {"answeredFirst": answered_first},
    )


def _render_action_smoke_card(page, state):
    page.evaluate(
        """
        ({state}) => {
          const isError = state === 'error';
          const isRunning = state === 'running';
          const base = {
            type: 'expert-team',
            kind: 'expert_team',
            runId: `expert-team-action-${state}`,
            sessionId: S.session.session_id,
            sourceSessionId: S.session.session_id,
            team: {id: 'content-creator-team', title: '内容创作专家团'},
            subtitle: '专家团动作入口验收',
            status: isError ? 'error' : 'running',
            statusLabel: isError ? '执行异常' : '执行中',
            phase: isError ? '生成初稿' : '打磨发布',
            phases: ['需求确认', '生成初稿', '打磨发布', '交付'],
            progress: {done: isError ? 1 : 2, total: 4},
            actions: {
              can_answer: false,
              can_resume: false,
              can_cancel: isRunning,
              can_retry: isError,
              can_open_artifact: false,
            },
            health: {
              needs_resume: false,
              active_stream_id: isRunning ? 'stream-action-smoke' : '',
              last_error: isError ? '未检测到可交付结果' : '',
            },
            members: [
              {id: 'flow', name: '流程编排', status: isError ? '执行异常' : '监督中'},
              {id: 'writer', name: '文案创作专家', status: isError ? '执行异常' : '执行中'},
              {id: 'image', name: '配图专家', status: '待命'},
              {id: 'review', name: '审稿润色', status: '待命'},
            ],
            tasks: [
              {id: 'direction', title: '需求确认', worker_name: '流程编排', status: 'done', status_label: '完成'},
              {id: 'draft', title: '撰写公众号长文', worker_name: '文案创作专家', status: isError ? 'error' : 'running', status_label: isError ? '执行异常' : '执行中'},
              {id: 'image', title: '生成封面和文中配图', worker_name: '配图专家', status: 'pending', status_label: '待执行'},
              {id: 'delivery', title: '交付整理', worker_name: '审稿润色', status: 'pending', status_label: '待执行'},
            ],
            artifacts: [],
            questions: [],
          };
          renderWriteflowStatusDock(base);
        }
        """,
        {"state": state},
    )


def _dock_geometry(page):
    return page.evaluate(
        """
        () => {
          const dock = document.getElementById('writeflowStatusDock');
          const box = document.querySelector('#composerWrap .composer-box');
          const topPanel = document.getElementById('expertTeamWorkspacePanel');
          const card = dock && dock.querySelector('.status-card-writeflow');
          const dockRect = dock && dock.getBoundingClientRect();
          const boxRect = box && box.getBoundingClientRect();
          const active = document.querySelector('.taiji-home-shell')?.classList.contains('taiji-expert-team-active');
          return {
            active: Boolean(active),
            collapsed: Boolean(card && card.classList.contains('is-collapsed')),
            expanded: Boolean(card && card.classList.contains('is-expanded')),
            topPanelExists: Boolean(topPanel),
            dockAboveComposer: Boolean(dockRect && boxRect && dockRect.bottom <= boxRect.top + 1),
            dockVisible: Boolean(dockRect && dockRect.width > 0 && dockRect.height > 0),
            composerVisible: Boolean(boxRect && boxRect.width > 0 && boxRect.height > 0),
            dockBottom: dockRect ? Math.round(dockRect.bottom) : 0,
            composerTop: boxRect ? Math.round(boxRect.top) : 0,
          };
        }
        """
    )


def _question_state(page):
    return page.evaluate(
        """
        () => {
          const input = document.querySelector('.status-card-expert-question.pending textarea');
          const active = document.activeElement === input;
          const card = document.querySelector('#writeflowStatusDock .status-card-writeflow');
          return {
            exists: Boolean(input),
            active,
            value: input ? input.value : '',
            expanded: Boolean(card && card.classList.contains('is-expanded')),
            collapsed: Boolean(card && card.classList.contains('is-collapsed')),
          };
        }
        """
    )


def _confirmation_workflow_state(page):
    return page.evaluate(
        """
        () => {
          const body = document.querySelector('.expert-team-panel-expanded-body');
          const workspace = document.querySelector('.expert-team-confirmation-workspace');
          const phases = document.querySelector('.expert-team-panel-phases');
          const priority = document.querySelector('.expert-team-panel-priority-grid');
          const current = document.querySelector('.status-card-expert-question.pending.is-current');
          const input = current && current.querySelector('[data-expert-team-answer-input]');
          const button = current && current.querySelector('.status-card-expert-question-submit');
          const rectFor = (node) => node ? node.getBoundingClientRect() : null;
          const bodyRect = rectFor(body);
          const workspaceRect = rectFor(workspace);
          const phasesRect = rectFor(phases);
          const priorityRect = rectFor(priority);
          const inputRect = rectFor(input);
          const buttonRect = rectFor(button);
          const visibleInBody = (rect) => Boolean(
            rect && bodyRect &&
            rect.width > 0 &&
            rect.height > 0 &&
            rect.top >= bodyRect.top - 1 &&
            rect.bottom <= bodyRect.bottom + 1
          );
          return {
            workspaceText: workspace ? workspace.textContent.replace(/\\s+/g, ' ').trim() : '',
            currentText: current ? current.textContent.replace(/\\s+/g, ' ').trim() : '',
            buttonText: button ? button.textContent.replace(/\\s+/g, ' ').trim() : '',
            buttonDisabled: Boolean(button && button.disabled),
            buttonBusy: Boolean(button && button.getAttribute('aria-busy') === 'true'),
            buttonName: button ? (button.getAttribute('aria-label') || button.textContent || '').replace(/\\s+/g, ' ').trim() : '',
            inputAria: input ? input.getAttribute('aria-label') || '' : '',
            inputValue: input ? input.value : '',
            inputActive: document.activeElement === input,
            workspaceBeforePhases: Boolean(workspaceRect && phasesRect && workspaceRect.top <= phasesRect.top),
            workspaceBeforePriority: Boolean(workspaceRect && priorityRect && workspaceRect.top <= priorityRect.top),
            workspaceVisible: visibleInBody(workspaceRect),
            inputVisible: visibleInBody(inputRect),
            buttonVisible: Boolean(buttonRect && buttonRect.width > 0 && buttonRect.height > 0),
          };
        }
        """
    )


def _dock_scroll_state(page):
    return page.evaluate(
        """
        () => {
          const card = document.querySelector('#writeflowStatusDock .status-card-writeflow');
          const inner = document.querySelector('#writeflowStatusDock .status-card-expert-bottom-body .expert-team-panel-inner');
          const overflowY = (node) => node ? getComputedStyle(node).overflowY : '';
          const createsScrollbar = (node) => {
            if (!node) return false;
            const y = overflowY(node);
            return (y === 'auto' || y === 'scroll') && node.scrollHeight > node.clientHeight + 1;
          };
          return {
            cardOverflowY: overflowY(card),
            innerOverflowY: overflowY(inner),
            cardCreatesScrollbar: createsScrollbar(card),
            innerCreatesScrollbar: createsScrollbar(inner),
          };
        }
        """
    )


def _dispatch_click(page, selector):
    return page.evaluate(
        """
        (selector) => {
          const node = document.querySelector(selector);
          if (!node) return false;
          node.dispatchEvent(new MouseEvent('click', {
            bubbles: true,
            cancelable: true,
            view: window,
          }));
          return true;
        }
        """,
        selector,
    )


def _dock_a11y_state(page):
    return page.evaluate(
        """
        () => {
          const visible = (node) => {
            if (!node) return false;
            const rect = node.getBoundingClientRect();
            const style = getComputedStyle(node);
            return rect.width > 0 && rect.height > 0 &&
              style.display !== 'none' && style.visibility !== 'hidden';
          };
          const nameFor = (node) => (
            node && (
              node.getAttribute('aria-label') ||
              node.getAttribute('title') ||
              (node.textContent || '').trim()
            ) || ''
          ).replace(/\\s+/g, ' ').trim();
          const selectors = {
            dockMain: '.status-card-expert-dock-summary',
            collapse: '.expert-team-panel-hide',
            confirm: '.status-card-expert-question-submit',
            artifact: '.expert-team-panel-artifact-open, .expert-team-panel-priority-card.artifact button',
          };
          const names = {};
          const visibleFlags = {};
          for (const [key, selector] of Object.entries(selectors)) {
            const node = document.querySelector(`#writeflowStatusDock ${selector}`);
            names[key] = nameFor(node);
            visibleFlags[key] = visible(node);
          }
          const focusables = Array.from(document.querySelectorAll(
            'a[href],button,textarea,input,select,[tabindex]:not([tabindex="-1"])'
          )).filter((node) => !node.disabled && visible(node));
          const dockIndex = focusables.findIndex((node) => node.closest('#writeflowStatusDock'));
          const composerIndex = focusables.findIndex((node) => node.closest('#composerWrap .composer-box'));
          return {
            names,
            visible: visibleFlags,
            dockBeforeComposer: dockIndex >= 0 && composerIndex >= 0 && dockIndex < composerIndex,
            dockIndex,
            composerIndex,
          };
        }
        """
    )


def _expert_action_state(page):
    return page.evaluate(
        """
        () => {
          const visible = (node) => {
            if (!node) return false;
            const rect = node.getBoundingClientRect();
            const style = getComputedStyle(node);
            return rect.width > 0 && rect.height > 0 &&
              style.display !== 'none' && style.visibility !== 'hidden';
          };
          const nameFor = (node) => (
            node && (
              node.getAttribute('aria-label') ||
              node.getAttribute('title') ||
              (node.textContent || '').trim()
            ) || ''
          ).replace(/\\s+/g, ' ').trim();
          const cancel = document.querySelector('#writeflowStatusDock .expert-team-panel-cancel');
          const retry = document.querySelector('#writeflowStatusDock .expert-team-panel-retry');
          return {
            cancelVisible: visible(cancel),
            cancelName: nameFor(cancel),
            retryVisible: visible(retry),
            retryName: nameFor(retry),
          };
        }
        """
    )


def _artifact_result_visible(page):
    return page.evaluate(
        """
        () => {
          const preview = document.getElementById('previewArea');
          const previewPath = document.getElementById('previewPathText');
          const rightPanel = document.querySelector('.rightpanel');
          const rightRect = rightPanel && rightPanel.getBoundingClientRect();
          const rightStyle = rightPanel && getComputedStyle(rightPanel);
          const previewVisible = Boolean(
            preview &&
            preview.classList.contains('visible') &&
            previewPath &&
            previewPath.textContent.includes('expert-team-smoke.md') &&
            rightPanel &&
            rightPanel.dataset.uiVisibilityHidden !== '1' &&
            rightStyle &&
            rightStyle.display !== 'none' &&
            rightStyle.visibility !== 'hidden' &&
            rightRect &&
            rightRect.width > 0 &&
            rightRect.height > 0
          );
          const artifactFocused = Boolean(document.querySelector('.expert-team-panel-artifact-focus'));
          return {
            previewVisible,
            artifactFocused,
            previewPath: previewPath ? previewPath.textContent : '',
          };
        }
        """
    )


def _execution_row_titles(page):
    return page.evaluate(
        """
        () => Array.from(document.querySelectorAll('.expert-team-panel-execution-main strong'))
          .map((node) => (node.textContent || '').trim())
        """
    )


def _execution_rows_visible(page):
    return page.evaluate(
        """
        () => {
          const body = document.querySelector('.expert-team-panel-expanded-body');
          const bodyRect = body && body.getBoundingClientRect();
          const rows = Array.from(document.querySelectorAll('.expert-team-panel-execution-row'));
          return rows.map((row) => {
            const rect = row.getBoundingClientRect();
            const title = row.querySelector('.expert-team-panel-execution-main strong');
            return {
              title: title ? (title.textContent || '').trim() : '',
              visible: Boolean(
                bodyRect &&
                rect.width > 0 &&
                rect.height > 0 &&
                rect.top >= bodyRect.top - 1 &&
                rect.bottom <= bodyRect.bottom + 1
              ),
              top: Math.round(rect.top),
              bottom: Math.round(rect.bottom),
              bodyBottom: bodyRect ? Math.round(bodyRect.bottom) : 0,
            };
          });
        }
        """
    )


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SETUP FAIL: playwright not installed", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    server_py = repo_root / "server.py"
    if not server_py.exists():
        print(f"SETUP FAIL: server.py not found at {server_py}", file=sys.stderr)
        return 2

    state_dir = Path(tempfile.mkdtemp(prefix="taiji-expert-artifact-smoke-"))
    workspace = state_dir / "workspace"
    artifact = workspace / "articles" / "expert-team-smoke.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# 专家团生成结果\n\n这是浏览器点击验收文件。\n", encoding="utf-8")

    output_dir = repo_root.parents[2] / "output" / "playwright"
    output_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    for key in list(env):
        if key.endswith("_API_KEY"):
            env.pop(key, None)
    env.update({
        "HERMES_WEBUI_PORT": str(PORT),
        "HERMES_WEBUI_HOST": "127.0.0.1",
        "HERMES_WEBUI_STATE_DIR": str(state_dir / "state"),
        "HERMES_HOME": str(state_dir / "home"),
        "HERMES_BASE_HOME": str(state_dir / "home"),
        "HERMES_WEBUI_DEFAULT_WORKSPACE": str(workspace),
        "HERMES_WEBUI_SKIP_ONBOARDING": "1",
        "HERMES_WEBUI_AGENT_DIR": str(state_dir / "no-agent"),
    })

    log_path = state_dir / "server.log"
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(server_py)],
        cwd=repo_root,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_for_health(timeout=30):
            log.flush()
            print("SETUP FAIL: server did not become healthy", file=sys.stderr)
            print(log_path.read_text(encoding="utf-8")[-2000:], file=sys.stderr)
            return 2

        failures = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            for width, height in VIEWPORTS:
                ctx = browser.new_context(
                    base_url=BASE,
                    viewport={"width": width, "height": height},
                    bypass_csp=True,
                )
                page = ctx.new_page()
                page.goto("/", wait_until="domcontentloaded")
                page.wait_for_selector(".taiji-main-workspace", timeout=10000)
                _prepare_expert_team_session(page, str(workspace))
                _render_smoke_card(page, done=False)
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-collapsed .status-card-expert-dock-summary", timeout=10000)
                compact = _dock_geometry(page)
                if not (compact["active"] and compact["collapsed"] and compact["dockVisible"] and compact["composerVisible"] and compact["dockAboveComposer"] and not compact["topPanelExists"]):
                    failures.append(f"{width}x{height}: compact dock geometry failed {compact!r}")
                page.click("#writeflowStatusDock .status-card-expert-dock-summary")
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-expanded .status-card-expert-question.pending textarea", timeout=10000)
                try:
                    page.wait_for_function(
                        """
                        () => document.activeElement ===
                          document.querySelector('#writeflowStatusDock .status-card-expert-question.pending textarea')
                        """,
                        timeout=1200,
                    )
                except Exception:
                    pass
                pending_state = _question_state(page)
                if not (pending_state["expanded"] and pending_state["exists"] and pending_state["active"]):
                    failures.append(f"{width}x{height}: pending dock did not expand/focus textarea {pending_state!r}")
                confirmation_state = _confirmation_workflow_state(page)
                if not (
                    confirmation_state["workspaceVisible"] and
                    confirmation_state["inputVisible"] and
                    confirmation_state["buttonVisible"] and
                    confirmation_state["inputActive"] and
                    confirmation_state["workspaceBeforePhases"] and
                    confirmation_state["workspaceBeforePriority"] and
                    "需要你确认 1/1" in confirmation_state["workspaceText"] and
                    "确认此项并继续" in confirmation_state["workspaceText"] and
                    confirmation_state["buttonText"] == "请先填写" and
                    confirmation_state["buttonDisabled"] and
                    "目标读者和发布口径" in confirmation_state["inputAria"]
                ):
                    failures.append(f"{width}x{height}: pending confirmation workspace not primary/actionable {confirmation_state!r}")
                pending_a11y = _dock_a11y_state(page)
                if not (
                    pending_a11y["names"]["dockMain"] and
                    pending_a11y["names"]["collapse"] and
                    "请先填写" in pending_a11y["names"]["confirm"] and
                    pending_a11y["dockBeforeComposer"]
                ):
                    failures.append(f"{width}x{height}: pending dock a11y failed {pending_a11y!r}")
                page.fill(".status-card-expert-question.pending textarea", "面向企业管理者，口径偏正式。")
                filled_confirmation_state = _confirmation_workflow_state(page)
                if not (
                    filled_confirmation_state["buttonText"] == "确认此项并继续" and
                    not filled_confirmation_state["buttonDisabled"] and
                    "确认此项并继续" in filled_confirmation_state["buttonName"]
                ):
                    failures.append(f"{width}x{height}: confirmation button did not become a clear primary action {filled_confirmation_state!r}")
                _render_smoke_card(page, done=False)
                restored_state = _question_state(page)
                if restored_state["value"] != "面向企业管理者，口径偏正式。" or not restored_state["active"]:
                    failures.append(f"{width}x{height}: input state not preserved after rerender {restored_state!r}")
                restored_confirmation_state = _confirmation_workflow_state(page)
                if restored_confirmation_state["buttonText"] != "确认此项并继续" or restored_confirmation_state["buttonDisabled"]:
                    failures.append(f"{width}x{height}: restored confirmation state did not keep the submit affordance {restored_confirmation_state!r}")

                _render_multi_question_card(page, answered_first=False)
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-collapsed .status-card-expert-dock-summary", timeout=10000)
                page.click("#writeflowStatusDock .status-card-expert-dock-summary")
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-expanded .status-card-expert-question.pending textarea", timeout=10000)
                page.fill(".status-card-expert-question.pending textarea", "本地优先 AI 助理")
                _render_multi_question_card(page, answered_first=True)
                try:
                    page.wait_for_function(
                        """
                        () => {
                          const card = document.querySelector('#writeflowStatusDock .status-card-writeflow');
                          const pending = document.querySelector('#writeflowStatusDock .status-card-expert-question.pending textarea');
                          return card && card.classList.contains('is-expanded') && document.activeElement === pending;
                        }
                        """,
                        timeout=1200,
                    )
                except Exception:
                    pass
                after_answer_state = _question_state(page)
                if not (after_answer_state["expanded"] and after_answer_state["exists"] and after_answer_state["active"]):
                    failures.append(f"{width}x{height}: dock did not stay open and focus next question {after_answer_state!r}")
                after_answer_confirmation = _confirmation_workflow_state(page)
                if not (
                    "需要你确认 2/2" in after_answer_confirmation["workspaceText"] and
                    "目标读者和发布口径" in after_answer_confirmation["inputAria"] and
                    after_answer_confirmation["buttonDisabled"]
                ):
                    failures.append(f"{width}x{height}: next confirmation item was not promoted after answer {after_answer_confirmation!r}")
                scroll_state = _dock_scroll_state(page)
                if scroll_state["cardOverflowY"] != "auto" or scroll_state["innerOverflowY"] != "visible" or scroll_state["innerCreatesScrollbar"]:
                    failures.append(f"{width}x{height}: dock should use one outer scrollbar {scroll_state!r}")
                pending_screenshot = output_dir / f"expert-team-dock-pending-expanded-{width}x{height}.png"
                page.screenshot(path=str(pending_screenshot), full_page=True)
                page.click("#writeflowStatusDock .status-card-expert-question.pending textarea")
                textarea_click_state = _question_state(page)
                if not textarea_click_state["expanded"]:
                    failures.append(f"{width}x{height}: textarea click should not collapse expert dock {textarea_click_state!r}")
                page.click("#writeflowStatusDock .expert-team-panel-execution-row")
                row_click_state = _dock_geometry(page)
                if not row_click_state["expanded"]:
                    failures.append(f"{width}x{height}: execution row click should not collapse expert dock {row_click_state!r}")
                if not _dispatch_click(page, "#writeflowStatusDock .expert-team-panel-inner"):
                    failures.append(f"{width}x{height}: blank inner click target missing")
                inner_blank_state = _dock_geometry(page)
                if not inner_blank_state["collapsed"]:
                    failures.append(f"{width}x{height}: inner blank click did not return to compact dock {inner_blank_state!r}")
                page.click("#writeflowStatusDock .status-card-expert-dock-summary")
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-expanded .status-card-expert-question.pending textarea", timeout=10000)
                if not _dispatch_click(page, "#writeflowStatusDock"):
                    failures.append(f"{width}x{height}: outer dock blank click target missing")
                outer_blank_state = _dock_geometry(page)
                if not outer_blank_state["collapsed"]:
                    failures.append(f"{width}x{height}: outer blank click did not return to compact dock {outer_blank_state!r}")
                page.click("#writeflowStatusDock .status-card-expert-dock-summary")
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-expanded .status-card-expert-question.pending textarea", timeout=10000)
                if not _dispatch_click(page, ".taiji-main-workspace"):
                    failures.append(f"{width}x{height}: workspace blank click target missing")
                workspace_blank_state = _dock_geometry(page)
                if not workspace_blank_state["collapsed"]:
                    failures.append(f"{width}x{height}: workspace blank click did not return to compact dock {workspace_blank_state!r}")
                page.click("#writeflowStatusDock .status-card-expert-dock-summary")
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-expanded .status-card-expert-question.pending textarea", timeout=10000)
                page.click(".expert-team-panel-hide")
                collapsed_state = _dock_geometry(page)
                if not collapsed_state["collapsed"]:
                    failures.append(f"{width}x{height}: collapse button did not return to compact dock {collapsed_state!r}")

                _render_action_smoke_card(page, "running")
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-collapsed .status-card-expert-dock-summary", timeout=10000)
                page.click("#writeflowStatusDock .status-card-expert-dock-summary")
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-expanded .expert-team-panel-cancel", timeout=10000)
                cancel_action = _expert_action_state(page)
                if not (cancel_action["cancelVisible"] and "停止生成" in cancel_action["cancelName"]):
                    failures.append(f"{width}x{height}: cancel action not discoverable {cancel_action!r}")

                _render_action_smoke_card(page, "error")
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-collapsed .status-card-expert-dock-summary", timeout=10000)
                page.click("#writeflowStatusDock .status-card-expert-dock-summary")
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-expanded .expert-team-panel-retry", timeout=10000)
                retry_action = _expert_action_state(page)
                if not (retry_action["retryVisible"] and "重新尝试" in retry_action["retryName"]):
                    failures.append(f"{width}x{height}: retry action not discoverable {retry_action!r}")

                _render_smoke_card(page, done=True)
                page.evaluate(
                    """
                    () => {
                      const card = document.querySelector('#writeflowStatusDock .status-card-writeflow');
                      if (window.hideExpertTeamWorkspacePanel && card) window.hideExpertTeamWorkspacePanel(card);
                    }
                    """
                )
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-collapsed .status-card-expert-dock-summary", timeout=10000)
                done_compact = _dock_geometry(page)
                if not (done_compact["collapsed"] and done_compact["dockAboveComposer"]):
                    failures.append(f"{width}x{height}: completed compact dock geometry failed {done_compact!r}")
                page.click("#writeflowStatusDock .status-card-expert-dock-summary")
                try:
                    page.wait_for_function(
                        """
                        () => {
                          const p = document.getElementById('previewPathText');
                          return (p && p.textContent.includes('expert-team-smoke.md')) ||
                            Boolean(document.querySelector('.expert-team-panel-artifact-focus'));
                        }
                        """,
                        timeout=7000,
                    )
                except Exception:
                    pass
                compact_result = _artifact_result_visible(page)
                if not (compact_result["previewVisible"] or compact_result["artifactFocused"]):
                    failures.append(
                        f"{width}x{height}: compact artifact action produced no visible feedback "
                        f"(previewPath={compact_result['previewPath']!r})"
                    )
                page.evaluate(
                    """
                    () => focusExpertTeamBottomDock(
                      document.querySelector('#writeflowStatusDock .status-card-expert-dock-summary')
                    )
                    """
                )
                page.wait_for_selector("#writeflowStatusDock .status-card-writeflow.is-expanded .expert-team-panel-priority-card.artifact button", timeout=10000)
                done_a11y = _dock_a11y_state(page)
                if not (
                    done_a11y["names"]["dockMain"] and
                    done_a11y["names"]["collapse"] and
                    done_a11y["names"]["artifact"] and
                    done_a11y["dockBeforeComposer"]
                ):
                    failures.append(f"{width}x{height}: completed dock a11y failed {done_a11y!r}")
                done_screenshot = output_dir / f"expert-team-dock-done-expanded-{width}x{height}.png"
                page.screenshot(path=str(done_screenshot), full_page=True)
                titles = _execution_row_titles(page)
                expected_titles = ["需求确认", "撰写公众号长文", "生成封面和文中配图", "交付整理"]
                if titles[:4] != expected_titles:
                    failures.append(
                        f"{width}x{height}: execution rows mismatch "
                        f"(expected={expected_titles!r}, actual={titles[:4]!r})"
                    )
                visible_rows = _execution_rows_visible(page)
                if len(visible_rows) < 4 or not all(row["visible"] for row in visible_rows[:4]):
                    failures.append(f"{width}x{height}: execution rows not fully visible {visible_rows[:4]!r}")
                page.click(".expert-team-panel-priority-card.artifact button")
                try:
                    page.wait_for_function(
                        """
                        () => {
                          const p = document.getElementById('previewPathText');
                          return (p && p.textContent.includes('expert-team-smoke.md')) ||
                            Boolean(document.querySelector('.expert-team-panel-artifact-focus'));
                        }
                        """,
                        timeout=7000,
                    )
                except Exception:
                    pass
                result = _artifact_result_visible(page)
                screenshot = output_dir / f"expert-team-artifact-click-{width}x{height}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                if not (result["previewVisible"] or result["artifactFocused"]):
                    failures.append(
                        f"{width}x{height}: no visible artifact feedback "
                        f"(previewPath={result['previewPath']!r})"
                    )
                else:
                    print(f"OK  {width}x{height} artifact click visible -> {screenshot}")
                ctx.close()
            browser.close()

        if failures:
            print("EXPERT TEAM ARTIFACT SMOKE FAILED", file=sys.stderr)
            print("\n".join(failures), file=sys.stderr)
            return 1
        print("EXPERT TEAM ARTIFACT SMOKE PASSED")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        shutil.rmtree(state_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
