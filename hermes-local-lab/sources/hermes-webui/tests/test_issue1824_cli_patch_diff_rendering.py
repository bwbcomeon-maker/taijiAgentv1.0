import json
import re
import sqlite3
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
COMPACT_UI = re.sub(r"\s+", "", UI_JS)


def test_cli_raw_result_diff_helpers_are_not_part_of_public_ui():
    """The browser must not reconstruct tool output or diffs from history."""
    for helper in ("_cliToolResultText", "_cliLooksLikePatchDiff", "_cliToolResultSnippet"):
        assert f"function {helper}" not in UI_JS


def test_cli_tool_fallback_does_not_promote_apply_patch_args_or_results():
    """Historical cards consume the server-owned public lifecycle projection."""
    start = UI_JS.index("if(!S.busy && (!S.toolCalls||!S.toolCalls.length))")
    end = UI_JS.index("if(!S.busy){", start + 1)
    block = UI_JS[start:end]
    for forbidden in ("function.arguments", "fn.arguments", "resultSnippet", "patchSnippet", "snippet:", "is_diff:", "args:"):
        assert forbidden not in block
    for required in ("name", "status", "summary", "assistant_msg_idx"):
        assert required in block


def test_public_tool_cards_do_not_expose_diff_expanders_or_raw_detail_helpers():
    for dead_helper in (
        "_toolArgPreviewValue",
        "_toolArgPreviewKeyIsHidden",
        "_formatToolArgPreview",
        "_snippetLooksLikeDiff",
        "_colorDiffLines",
        "_toggleToolDiff",
    ):
        assert f"function {dead_helper}" not in UI_JS
    assert "Show diff" not in UI_JS


def _function_source(src: str, name: str) -> str:
    match = re.search(rf"function\s+{re.escape(name)}\s*\(", src)
    assert match, f"{name}() not found"
    brace = src.find("{", match.end())
    assert brace != -1, f"{name}() has no body"
    depth = 1
    i = brace + 1
    in_string = None
    escaped = False
    in_line_comment = False
    in_block_comment = False
    while i < len(src) and depth:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < len(src) else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_string:
                in_string = None
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch in "'\"`":
            in_string = ch
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"{name}() body did not close"
    return src[match.start() : i]


def test_rendered_apply_patch_tool_card_keeps_raw_diff_out_of_public_html():
    """Legacy raw fields supplied client-side are ignored by the card renderer."""
    function_names = ["_toolCardPreviewText", "buildToolCard"]
    functions = "\n".join(_function_source(UI_JS, name) for name in function_names)
    script = textwrap.dedent(
        f"""
        function esc(s){{return String(s||'').replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));}}
        function li(){{return '';}}
        function toolIcon(){{return '';}}
        function _toolDisplayName(tc){{return tc.name||'tool';}}
        const document={{
          createElement(){{return {{className:'', innerHTML:''}};}}
        }};
        {functions}

        const row = buildToolCard({{
          name: 'apply_patch',
          status: 'completed',
          summary: 'Patch completed',
          snippet: '-old\\n+new',
          result: 'private result',
          is_diff: true,
          args: {{patch: '*** Begin Patch'}},
          done: true
        }});
        process.stdout.write(JSON.stringify({{html: row.innerHTML}}));
        """
    )
    proc = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    payload = json.loads(proc.stdout)
    html = payload["html"]
    assert "-old" not in html
    assert "+new" not in html
    assert "private result" not in html
    assert "Begin Patch" not in html
    assert "Show diff" not in html
    assert "Patch completed" in html


def _make_state_db(path: Path) -> None:
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: app.py",
            "@@",
            "-old",
            "+new",
            "*** End Patch",
        ]
    )
    tool_calls = [
        {
            "id": "call_patch",
            "type": "function",
            "function": {
                "name": "apply_patch",
                "arguments": json.dumps({"patch": patch}),
            },
        }
    ]
    conn = sqlite3.Connection(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT,
                tool_call_id TEXT,
                tool_calls TEXT,
                tool_name TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO messages (session_id, role, content, timestamp, tool_calls)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("issue1824", "assistant", "", "2026-01-01T00:00:01Z", json.dumps(tool_calls)),
        )
        conn.execute(
            """
            INSERT INTO messages (session_id, role, content, timestamp, tool_call_id, tool_name)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "issue1824",
                "tool",
                json.dumps({"output": "Success"}),
                "2026-01-01T00:00:02Z",
                "call_patch",
                "apply_patch",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_cli_session_reader_preserves_apply_patch_metadata(tmp_path, monkeypatch):
    """The API payload should keep tool_calls/tool rows for the UI renderer."""
    _make_state_db(tmp_path / "state.db")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    import api.profiles
    from api.models import get_cli_session_messages

    monkeypatch.setattr(api.profiles, "get_active_hermes_home", lambda: str(tmp_path))

    messages = get_cli_session_messages("issue1824")
    assert [m["role"] for m in messages] == ["assistant", "tool"]

    assistant = messages[0]
    assert assistant["tool_calls"][0]["function"]["name"] == "apply_patch"
    args = json.loads(assistant["tool_calls"][0]["function"]["arguments"])
    assert "*** Begin Patch" in args["patch"]
    assert "-old" in args["patch"]
    assert "+new" in args["patch"]

    tool = messages[1]
    assert tool["tool_call_id"] == "call_patch"
    assert tool["tool_name"] == "apply_patch"
    assert tool["name"] == "apply_patch"
    assert json.loads(tool["content"])["output"] == "Success"
