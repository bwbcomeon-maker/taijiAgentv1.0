from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")


def test_history_tool_fallback_consumes_only_public_projection_fields():
    start = UI_JS.index("if(!S.busy && (!S.toolCalls||!S.toolCalls.length))")
    end = UI_JS.index("if(!S.busy){", start + 1)
    block = UI_JS[start:end]

    for forbidden in (
        "function.arguments",
        "fn.arguments",
        "resultsByTid",
        "resultSnippet",
        "patchSnippet",
        "snippet:",
        "is_diff:",
        "args:",
    ):
        assert forbidden not in block
    for required in ("name", "status", "summary", "assistant_msg_idx"):
        assert required in block


def test_render_cache_signature_ignores_raw_tool_operational_fields():
    start = UI_JS.index("function _messageRenderCacheSignature()")
    end = UI_JS.index("function _captureMessageScrollSnapshot", start)
    block = UI_JS[start:end]

    for forbidden in (
        "tc.function",
        "tc.snippet",
        "tc.args",
        "tc.is_diff",
        "m.attachments",
        "_partial_tool_calls",
    ):
        assert forbidden not in block
    for required in ("tc.name", "tc.status", "tc.summary", "tc.assistant_msg_idx"):
        assert required in block


def test_removed_cli_raw_tool_helpers_do_not_return():
    for helper in (
        "_cliToolResultText",
        "_cliToolResultSnippet",
        "_cliPatchSnippetFromArgs",
        "_cliToolCardSnippet",
        "_cliToolCardHasDiffSnippet",
    ):
        assert f"function {helper}" not in UI_JS
