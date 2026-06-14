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
VIEWPORTS = [(1440, 900), (1280, 760), (1024, 720)]


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


def _inject_expert_team_card(page, workspace):
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

          renderExpertTeamWorkspacePanel({
            type: 'expert-team',
            kind: 'expert_team',
            runId: 'expert-team-artifact-smoke',
            sessionId: S.session.session_id,
            sourceSessionId: S.session.session_id,
            team: {id: 'content-creator-team', title: '内容创作专家团'},
            subtitle: '围绕企业为什么需要本地 AI Agent 工作台做一篇公众号长文',
            status: 'done',
            statusLabel: '已完成',
            phase: '交付',
            phases: ['需求确认', '生成初稿', '打磨发布', '交付'],
            progress: {done: 4, total: 4},
            members: [
              {id: 'flow', name: '流程编排', status: 'done'},
              {id: 'writer', name: '文案创作专家', status: 'done'},
              {id: 'image', name: '配图专家', status: 'done'},
              {id: 'review', name: '审稿润色', status: 'done'},
            ],
            tasks: [
              {id: 'direction', title: '需求确认', worker_name: '流程编排', status: 'done', status_label: '完成'},
              {id: 'draft', title: '撰写公众号长文', worker_name: '文案创作专家', status: 'done', status_label: '完成'},
              {id: 'image', title: '生成封面和文中配图', worker_name: '配图专家', status: 'done', status_label: '完成'},
              {id: 'delivery', title: '交付整理', worker_name: '审稿润色', status: 'done', status_label: '完成'},
            ],
            artifacts: [
              {
                id: 'draft',
                label: '专家团生成结果',
                path: 'articles/expert-team-smoke.md',
                kind: 'md',
                exists: true,
                download_name: '专家团生成结果.md',
              },
            ],
            questions: [],
          });
        }
        """,
        {"workspace": workspace},
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
                _inject_expert_team_card(page, str(workspace))
                page.wait_for_selector(".expert-team-panel-priority-card.artifact button", timeout=10000)
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
