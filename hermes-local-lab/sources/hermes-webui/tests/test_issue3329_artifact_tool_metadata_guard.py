"""Regression: collectSessionArtifacts() tolerates malformed tool_calls entries (#3329).

#3329 taught the Artifacts tab to read structured tool metadata from messages
(OpenAI `tool_calls` + Anthropic `tool_use` content blocks) in addition to
text-mined diff fences. The pre-release Codex regression gate flagged that the
OpenAI `tool_calls` loop dereferenced ``tc.function`` with no null/type guard,
so a session whose persisted ``message.tool_calls`` array contained a null or
non-object entry (truncated/corrupt sidecar, partial stream) would throw
"Cannot read properties of null", aborting artifact collection for the whole
session. The Anthropic `tool_use` loop already had a ``if(!block ...) continue``
guard; this test pins the symmetric guard on the OpenAI loop.

Drives the ACTUAL collectSessionArtifacts() from static/workspace.js via node
(with its private helpers + a stubbed ``S``) so it cannot drift from a mirror.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WORKSPACE_JS = (REPO / "static" / "workspace.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _extract_fn(name: str) -> str:
    start = WORKSPACE_JS.index(f"function {name}(")
    brace = WORKSPACE_JS.index("{", start)
    depth = 0
    for i in range(brace, len(WORKSPACE_JS)):
        c = WORKSPACE_JS[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return WORKSPACE_JS[start:i + 1]
    raise AssertionError(f"function {name} did not close")


def _collect_via_node(messages):
    # Pull in the function under test plus the helpers it transitively calls.
    import re
    consts = []
    for name in (
        "ARTIFACT_IGNORE_RE", "ARTIFACT_DIRECT_PATH_RE",
        "ARTIFACT_MUTATION_TOOLS",
    ):
        m = re.search(rf"const {name} = .*?;", WORKSPACE_JS)
        assert m, f"{name} not found"
        consts.append(m.group(0))
    fns = "\n".join(consts) + "\n" + "\n".join(
        _extract_fn(n)
        for n in (
            "_normalizeArtifactPath",
            "_artifactCandidatesFromText",
            "_artifactCandidatesFromToolCall",
            "collectSessionArtifacts",
        )
    )
    driver = (
        "const S = { toolCalls: [], messages: JSON.parse(process.argv[1]), "
        "session: { workspace: '/ws' } };\n"
        + fns + "\n"
        + "const out = collectSessionArtifacts();\n"
        + "process.stdout.write(JSON.stringify(out.map(x => x.path)));\n"
    )
    r = subprocess.run(
        [NODE, "-e", driver, json.dumps(messages)],
        capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 0, f"node threw (the #3329 guard regressed): {r.stderr}"
    return json.loads(r.stdout)


def test_malformed_tool_calls_entries_do_not_throw():
    """A null / non-object tool_calls entry must be skipped, not throw."""
    messages = [
        {"tool_calls": [
            None,
            "not-an-object",
            42,
            {"function": {"name": "edit_file", "arguments": json.dumps({"path": "real.py"})}},
        ]},
    ]
    # The assertion that matters is that node returns 0 (no throw). The valid
    # entry's path extraction depends on _artifactCandidatesFromToolCall's
    # argument heuristics, which we don't pin here — only the no-throw guarantee.
    paths = _collect_via_node(messages)
    assert isinstance(paths, list)


def test_guard_present_in_source():
    """Pin the explicit guard so it can't be silently removed."""
    fn = _extract_fn("collectSessionArtifacts")
    assert "if(!tc || typeof tc !== 'object') continue;" in fn, (
        "OpenAI tool_calls loop must guard malformed entries before "
        "dereferencing tc.function (#3329 Codex regression-gate finding)"
    )


def test_public_completed_artifact_path_is_listed_but_failed_path_is_not():
    messages = [{"tool_calls": [
        {
            "name": "write_file",
            "artifact_path": "成果.md",
            "status": "completed",
            "done": True,
            "is_error": False,
        },
        {
            "name": "write_file",
            "artifact_path": "失败.md",
            "status": "failed",
            "done": True,
            "is_error": True,
        },
    ]}]

    assert _collect_via_node(messages) == ["成果.md"]


def test_anthropic_public_artifact_path_is_listed_after_windowed_reload():
    messages = [{
        "role": "assistant",
        "content": [{
            "type": "tool_use",
            "id": "anthropic-1",
            "name": "write_file",
            "artifact_path": "报告/成果.md",
            "status": "completed",
            "done": True,
            "is_error": False,
        }],
    }]

    assert _collect_via_node(messages) == ["报告/成果.md"]


def test_public_artifacts_ignore_private_metadata_and_assistant_only_diffs():
    messages = [
        {
            "role": "assistant",
            "content": "```diff\n--- a/model_only.md\n+++ b/model_only.md\n```",
            "tool_calls": [
                {
                    "name": "write_file",
                    "status": "failed",
                    "is_error": True,
                    "args": {"path": "failed.md"},
                },
                {
                    "name": "write_file",
                    "status": "cancelled",
                    "args": {"path": "cancelled.md"},
                },
                {
                    "name": "patch",
                    "status": "failed",
                    "is_error": True,
                    "result": "```diff\n--- a/failed.py\n+++ b/failed.py\n```",
                },
                {
                    "name": "write_file",
                    "status": "running",
                    "done": True,
                    "artifact_path": "running.md",
                },
            ],
        }
    ]

    assert _collect_via_node(messages) == []
