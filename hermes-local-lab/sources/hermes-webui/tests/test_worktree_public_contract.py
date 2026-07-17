"""Public Worktree contracts must retain UI capability without filesystem paths."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from urllib.parse import urlparse

import api.routes as routes
import api.session_ops as session_ops
import api.worktrees as worktrees
import api.profiles as profiles
from api.brand_privacy import public_session_projection


WEBUI_ROOT = Path(__file__).resolve().parents[1]


class _JsonHandler:
    def __init__(self):
        self.status = None
        self.body = bytearray()
        self.wfile = self

    def send_response(self, status):
        self.status = status

    def send_header(self, *_args):
        pass

    def end_headers(self):
        pass

    def write(self, data):
        self.body.extend(data)

    def json(self):
        return json.loads(self.body.decode("utf-8"))


def test_session_projection_exposes_safe_worktree_identity_without_path():
    private_path = "/Users/private/repo/.worktrees/hermes-customer-feature"

    projected = public_session_projection({
        "session_id": "worktree-public",
        "title": "Customer task",
        "workspace": private_path,
        "worktree_path": private_path,
        "worktree_branch": "feature/customer-feature",
        "worktree_repo_root": "/Users/private/repo",
    })

    assert projected["is_worktree"] is True
    assert projected["worktree_branch"] == "customer-feature"
    assert projected["worktree_label"] == "customer-feature"
    assert "workspace" not in projected
    assert "worktree_path" not in projected
    assert "worktree_repo_root" not in projected


def test_webui_new_worktree_uses_taiji_identity_at_creation_source(monkeypatch, tmp_path):
    """The real shell prompt exposes cwd basename, so WebUI must create taiji-*."""
    captured = {}
    fake_cli = ModuleType("cli")

    def fake_setup(repo_root, **kwargs):
        captured.update(repo_root=repo_root, **kwargs)
        name_prefix = kwargs.get("name_prefix", "hermes")
        branch_prefix = kwargs.get("branch_prefix", "hermes")
        name = f"{name_prefix}-24cb58a0"
        return {
            "path": str(Path(repo_root) / ".worktrees" / name),
            "branch": f"{branch_prefix}/{name}",
            "repo_root": repo_root,
        }

    fake_cli._setup_worktree = fake_setup
    monkeypatch.setitem(sys.modules, "cli", fake_cli)

    info = worktrees._setup_agent_worktree(str(tmp_path))
    terminal_visible_identity = Path(info["path"]).name

    assert captured["name_prefix"] == "taiji"
    assert captured["branch_prefix"] == "taiji"
    assert re.search(r"(?i)hermes", terminal_visible_identity) is None
    assert re.search(r"(?i)hermes", info["branch"]) is None


def test_agent_worktree_helper_honours_product_prefix_without_changing_cli_default(
    monkeypatch, tmp_path
):
    import cli

    monkeypatch.setattr(cli.uuid, "uuid4", lambda: SimpleNamespace(hex="24cb58a0abcdef"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    product_repo = tmp_path / "product"
    legacy_repo = tmp_path / "legacy"
    product_repo.mkdir()
    legacy_repo.mkdir()

    product = cli._setup_worktree(
        str(product_repo),
        name_prefix="taiji",
        branch_prefix="taiji",
    )
    legacy = cli._setup_worktree(str(legacy_repo))

    assert Path(product["path"]).name == "taiji-24cb58a0"
    assert product["branch"] == "taiji/taiji-24cb58a0"
    assert Path(legacy["path"]).name == "hermes-24cb58a0"
    assert legacy["branch"] == "hermes/hermes-24cb58a0"


def test_non_worktree_session_keeps_explicit_customer_workspace():
    workspace = "/Users/customer/project"

    projected = public_session_projection({
        "session_id": "plain-public",
        "workspace": workspace,
    })

    assert projected["is_worktree"] is False
    assert projected["workspace"] == workspace
    assert "worktree_label" not in projected


def test_sessions_list_route_applies_public_worktree_projection(monkeypatch):
    private_path = "/Users/private/repo/.worktrees/hermes-customer-feature"
    monkeypatch.setattr(routes, "all_sessions", lambda diag=None: [{
        "session_id": "worktree-list-public",
        "title": "Customer task",
        "workspace": private_path,
        "worktree_path": private_path,
        "worktree_branch": "feature/customer-feature",
        "worktree_repo_root": "/Users/private/repo",
        "profile": "default",
        "updated_at": 1,
        "last_message_at": 1,
    }])
    monkeypatch.setattr(
        routes, "_reconcile_stale_stream_state_for_session_rows", lambda _rows: False
    )
    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": False})
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")

    handler = _JsonHandler()
    routes.handle_get(handler, urlparse("http://example.com/api/sessions"))

    assert handler.status == 200
    session = handler.json()["sessions"][0]
    assert session["is_worktree"] is True
    assert session["worktree_label"] == "customer-feature"
    assert "workspace" not in session
    assert "worktree_path" not in session
    assert "worktree_repo_root" not in session
    assert private_path not in handler.body.decode("utf-8")


def test_worktree_status_route_projects_only_safe_status(monkeypatch):
    private_path = "/Users/private/repo/.worktrees/hermes-customer-feature"
    session = SimpleNamespace(
        session_id="worktree-status",
        worktree_path=private_path,
        worktree_branch="feature/customer-feature",
    )
    monkeypatch.setattr(routes, "get_session", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(
        worktrees,
        "worktree_status_for_session",
        lambda _session: {
            "path": private_path,
            "exists": True,
            "dirty": True,
            "untracked_count": 2,
            "ahead_behind": {
                "ahead": 1,
                "behind": 3,
                "available": True,
                "upstream": "origin/private-main",
            },
            "locked_by_stream": False,
            "locked_by_terminal": True,
            "listed": True,
        },
    )

    handler = _JsonHandler()
    routes.handle_get(
        handler,
        urlparse("/api/session/worktree/status?session_id=worktree-status"),
    )

    assert handler.status == 200
    assert handler.json() == {
        "status": {
            "label": "customer-feature",
            "exists": True,
            "dirty": True,
            "untracked_count": 2,
            "ahead_behind": {"ahead": 1, "behind": 3, "available": True},
            "locked_by_stream": False,
            "locked_by_terminal": True,
            "listed": True,
        }
    }
    assert private_path not in handler.body.decode("utf-8")


def test_session_status_route_omits_worktree_workspace(monkeypatch):
    private_path = "/Users/customer/acme/.worktrees/feature-x"
    session = SimpleNamespace(
        session_id="worktree-session-status",
        title="Customer task",
        model="fixture-model",
        profile="default",
        workspace=private_path,
        personality=None,
        messages=[],
        created_at=1,
        updated_at=2,
        active_stream_id=None,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        worktree_path=private_path,
        worktree_branch="feature/customer-feature",
    )
    monkeypatch.setattr(session_ops, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "get_session", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(routes, "_clear_stale_stream_state", lambda _session: False)

    handler = _JsonHandler()
    routes.handle_get(
        handler,
        urlparse("/api/session/status?session_id=worktree-session-status"),
    )

    assert handler.status == 200
    payload = handler.json()
    assert payload["is_worktree"] is True
    assert payload["worktree_label"] == "customer-feature"
    assert "workspace" not in payload
    assert private_path not in handler.body.decode("utf-8")


def test_commands_status_uses_safe_worktree_label_not_current_directory():
    source = (WEBUI_ROOT / "static" / "commands.js").read_text(encoding="utf-8")
    block = source.split("function _statusCardFromSession(s){", 1)[1].split(
        "function cmdStatus()", 1
    )[0]

    assert "s.is_worktree" in block
    assert "s.worktree_label" in block
    assert "s.workspace||S.currentDir" in block


def test_worktree_remove_route_projects_only_outcome(monkeypatch):
    private_path = "/Users/private/repo/.worktrees/hermes-customer-feature"
    saved = []
    session = SimpleNamespace(
        session_id="worktree-remove",
        workspace=private_path,
        worktree_path=private_path,
        worktree_branch="feature/customer-feature",
        worktree_repo_root="/Users/private/repo",
        worktree_created_at=123.0,
        save=lambda: saved.append(True),
    )
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {"session_id": session.session_id, "force": False},
    )
    monkeypatch.setattr(routes, "get_session", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(
        worktrees,
        "remove_worktree_for_session",
        lambda *_args, **_kwargs: {
            "ok": True,
            "removed_path": private_path,
            "warnings": ["Worktree directory no longer exists on disk."],
        },
    )
    captured = {}
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: captured.update(
            payload=payload,
            status=status,
        ) or True,
    )

    assert routes.handle_post(
        object(),
        SimpleNamespace(path="/api/session/worktree/remove"),
    ) is True

    assert captured == {
        "payload": {
            "ok": True,
            "removed": True,
            "warnings": ["Worktree directory no longer exists on disk."],
        },
        "status": 200,
    }
    assert saved == [True]
    assert session.workspace == "/Users/private/repo"
    assert session.worktree_path is None
    assert session.worktree_branch is None
    assert session.worktree_repo_root is None
    assert session.worktree_created_at is None
    assert public_session_projection(vars(session))["is_worktree"] is False
    assert private_path not in json.dumps(captured)


def test_worktree_remove_save_failure_is_explicit_and_path_free(monkeypatch):
    private_path = "/Users/private/repo/.worktrees/hermes-customer-feature"

    def fail_save():
        raise OSError(f"cannot save {private_path}")

    session = SimpleNamespace(
        session_id="worktree-remove-save-failure",
        workspace=private_path,
        worktree_path=private_path,
        worktree_branch="feature/customer-feature",
        worktree_repo_root="/Users/private/repo",
        worktree_created_at=123.0,
        save=fail_save,
    )
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {"session_id": session.session_id, "force": False},
    )
    monkeypatch.setattr(routes, "get_session", lambda *_args, **_kwargs: session)
    monkeypatch.setattr(
        worktrees,
        "remove_worktree_for_session",
        lambda *_args, **_kwargs: {
            "ok": True,
            "removed_path": private_path,
            "warnings": [],
        },
    )
    captured = {}
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: captured.update(
            payload=payload,
            status=status,
        ) or True,
    )

    assert routes.handle_post(
        object(),
        SimpleNamespace(path="/api/session/worktree/remove"),
    ) is True

    assert captured == {
        "payload": {
            "error": "Worktree was removed, but conversation state could not be updated. Retry to repair the conversation.",
            "code": "worktree_removed_state_update_failed",
            "removed": True,
        },
        "status": 500,
    }
    assert private_path not in json.dumps(captured)


def test_sessions_js_uses_only_public_worktree_fields():
    source = (WEBUI_ROOT / "static" / "sessions.js").read_text(encoding="utf-8")

    assert "worktree_path" not in source
    assert "status.path" not in source
    assert ".is_worktree" in source
    assert "worktree_label" in source


def test_sessions_js_retains_worktree_ui_and_clears_public_state_after_remove():
    source = (WEBUI_ROOT / "static" / "sessions.js").read_text(encoding="utf-8")

    for contract in (
        "function _worktreeSessionCount(ids)",
        "session-worktree-indicator",
        "session_worktree_remove",
        "session_worktree_remove_confirm",
        "session.is_worktree=false",
        "S.session.is_worktree=false",
    ):
        assert contract in source
    remove_block = source.split("async function removeWorktree(session)", 1)[1].split(
        "async function deleteSession(", 1
    )[0]
    final_confirm = remove_block.split("const ok=await showConfirmDialog({", 1)[1].split(
        "if(!ok)return;", 1
    )[0]
    assert "focusCancel:true" in final_confirm
    assert "danger:true" in final_confirm


def test_worktree_remove_confirmation_uses_identifier_label_and_danger_token():
    i18n = (WEBUI_ROOT / "static" / "i18n.js").read_text(encoding="utf-8")
    style = (WEBUI_ROOT / "static" / "style.css").read_text(encoding="utf-8")

    simplified_chinese = i18n.split("\n  zh: {", 1)[1].split("\n  zh_TW: {", 1)[0]
    confirmation = simplified_chinese.split(
        "session_worktree_remove_confirm:", 1
    )[1].split("\n", 1)[0]
    assert "Worktree 标识：" in confirmation
    assert "路径：" not in confirmation
    assert (
        ':root[data-skin="taiji-light-glass"] .app-dialog-btn.confirm.danger'
        in style
    )


def test_worktree_frontend_never_restores_or_submits_source_workspace():
    ui = (WEBUI_ROOT / "static" / "ui.js").read_text(encoding="utf-8")

    assert "if(next.is_worktree){" in ui
    assert "delete next.workspace;" in ui
    assert "if(S.session&&S.session.is_worktree)return undefined;" in ui


def test_worktree_keeps_terminal_and_file_panel_enabled_without_public_path():
    ui = (WEBUI_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    terminal = (WEBUI_ROOT / "static" / "terminal.js").read_text(encoding="utf-8")
    commands = (WEBUI_ROOT / "static" / "commands.js").read_text(encoding="utf-8")
    panels = (WEBUI_ROOT / "static" / "panels.js").read_text(encoding="utf-8")
    boot = (WEBUI_ROOT / "static" / "boot.js").read_text(encoding="utf-8")

    assert "function sessionHasWorkspace(session=S.session)" in ui
    assert "sessionHasWorkspace()" in terminal
    assert "sessionHasWorkspace()" in commands
    assert "sessionHasWorkspace()" in panels
    assert "sessionHasWorkspace()&&panelPref" in boot


def test_terminal_start_public_response_omits_internal_workspace(monkeypatch):
    private_path = Path("/Users/private/repo/.worktrees/hermes-customer-feature")
    monkeypatch.setattr(
        routes,
        "_terminal_session_and_workspace",
        lambda _body: ("worktree-terminal", private_path),
    )
    import api.terminal as terminal

    monkeypatch.setattr(
        terminal,
        "start_terminal",
        lambda *_args, **_kwargs: SimpleNamespace(
            workspace=str(private_path),
            is_alive=lambda: True,
        ),
    )
    captured = {}
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: captured.update(
            payload=payload,
            status=status,
        ) or True,
    )

    assert routes._handle_terminal_start(object(), {"session_id": "worktree-terminal"})
    assert captured == {
        "payload": {"ok": True, "session_id": "worktree-terminal", "running": True},
        "status": 200,
    }
    assert str(private_path) not in json.dumps(captured)


def test_worktree_file_path_endpoint_never_returns_internal_path(monkeypatch):
    private_path = "/Users/private/repo/.worktrees/hermes-customer-feature"
    monkeypatch.setattr(
        routes,
        "get_session_for_file_ops",
        lambda _sid: SimpleNamespace(
            session_id="worktree-file",
            workspace=private_path,
            worktree_path=private_path,
            is_worktree=True,
        ),
    )

    handler = _JsonHandler()
    routes._handle_file_path(
        handler,
        {"session_id": "worktree-file", "path": "."},
    )

    assert handler.status == 403
    assert handler.json() == {
        "error": "Absolute paths are unavailable for Worktree sessions"
    }
    assert private_path not in handler.body.decode("utf-8")


def test_worktree_file_action_errors_never_return_internal_path(monkeypatch):
    private_path = "/Users/private/repo/.worktrees/hermes-customer-feature"
    monkeypatch.setattr(
        routes,
        "get_session_for_file_ops",
        lambda _sid: SimpleNamespace(
            session_id="worktree-file-errors",
            workspace=private_path,
            worktree_path=private_path,
            is_worktree=True,
        ),
    )

    for handler_fn in (
        routes._handle_file_reveal,
        routes._handle_file_open,
        routes._handle_file_open_vscode,
    ):
        handler = _JsonHandler()
        handler_fn(
            handler,
            {"session_id": "worktree-file-errors", "path": "missing.txt"},
        )
        assert handler.status == 404
        assert handler.json() == {"error": "File not found: missing.txt"}
        assert private_path not in handler.body.decode("utf-8")


def test_worktree_workspace_cannot_be_rebound_from_browser(monkeypatch):
    private_path = "/Users/private/repo/.worktrees/hermes-customer-feature"
    session = SimpleNamespace(
        session_id="worktree-fixed-workspace",
        workspace=private_path,
        worktree_path=private_path,
        worktree_branch="feature/customer-feature",
        is_worktree=True,
    )
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: {
            "session_id": session.session_id,
            "workspace": "/Users/private/repo",
        },
    )
    monkeypatch.setattr(routes, "get_session", lambda *_args, **_kwargs: session)

    handler = _JsonHandler()
    routes.handle_post(
        handler,
        SimpleNamespace(path="/api/session/update"),
    )

    assert handler.status == 409
    assert handler.json() == {"error": "Worktree workspace is fixed for this session"}
    assert session.workspace == private_path


def test_worktree_workspace_switcher_is_display_only():
    panels = (WEBUI_ROOT / "static" / "panels.js").read_text(encoding="utf-8")

    assert "composerChip.disabled=isWorktree||!hasWorkspace;" in panels
    toggle_block = panels.split("function toggleComposerWsDropdown()", 1)[1].split(
        "function closeWsDropdown()", 1
    )[0]
    assert "if(S.session&&S.session.is_worktree)return;" in toggle_block


def test_visible_taiji_recent_list_exposes_safe_accessible_worktree_badge():
    source = (WEBUI_ROOT / "static" / "taiji-home.js").read_text(encoding="utf-8")

    assert "worktree_path" not in source
    assert "function taijiSessionWorktreeLabel(session)" in source
    assert "session.worktree_label||session.worktree_branch" in source
    assert "session.is_worktree" in source
    assert "taiji-session-worktree" in source
    assert 'aria-label="Worktree：${worktreeLabel}"' in source


def test_visible_taiji_more_menu_reuses_safe_worktree_removal_flow():
    source = (WEBUI_ROOT / "static" / "taiji-home.js").read_text(encoding="utf-8")
    menu_block = source.split("function showSessionActionMenu(", 1)[1].split(
        "function renderProjectFilters()", 1
    )[0]

    assert "if(session.is_worktree)" in menu_block
    assert "data-taiji-session-worktree-remove" in menu_block
    assert "globalFn('removeWorktree')" in menu_block
    assert "await removeWorktreeFn(session);" in menu_block
    assert "await refreshSessions();" in menu_block
