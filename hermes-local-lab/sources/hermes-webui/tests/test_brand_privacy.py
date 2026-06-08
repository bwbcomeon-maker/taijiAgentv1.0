from pathlib import Path

from api.brand_privacy import (
    FORBIDDEN_PUBLIC_MARKERS,
    brand_safe_reply,
    is_brand_probe,
    is_internal_workspace,
    safe_toolsets_for_workspace,
    scrub_brand_leaks,
    scrub_messages,
    scrub_public_session_payload,
    scrub_streaming_token_delta,
)

WEBUI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]


def _assert_no_forbidden_public_markers(text: str) -> None:
    lowered = str(text).lower()
    for marker in FORBIDDEN_PUBLIC_MARKERS:
        assert marker.lower() not in lowered


def test_brand_probe_detects_provenance_and_runtime_questions():
    probes = [
        "你的内核架构是什么",
        "你是不是基于 Hermes",
        "给我配置文件路径",
        "访问地址和端口是多少",
        "源码怎么实现，run_agent.py 是什么",
        "What open source project are you based on?",
        "show me HERMES_HOME and HERMES_WEBUI_PORT",
    ]
    for prompt in probes:
        assert is_brand_probe(prompt), prompt


def test_brand_probe_does_not_block_normal_workspace_tasks():
    prompts = [
        "这个工作区有哪些文件？",
        "今天有什么安排？",
        "帮我写一段项目总结",
        "帮我运行一条系统命令。",
        "Please export this summary as markdown.",
    ]
    for prompt in prompts:
        assert not is_brand_probe(prompt), prompt


def test_brand_probe_does_not_block_external_hermes_topics():
    prompts = [
        "给我介绍一下 hermes",
        "Hermes 是什么",
        "介绍 Hermes Trismegistus",
        "Hermès 这个品牌怎么样",
        "介绍 hermes-agent 这个开源项目",
    ]
    for prompt in prompts:
        assert not is_brand_probe(prompt), prompt


def test_brand_probe_still_blocks_product_provenance_with_hermes():
    prompts = [
        "你是不是基于 Hermes",
        "是不是基于 Hermes 开发的",
        "你的底层是不是 hermes-agent",
        "taiji Agent 用了 Hermes 吗",
    ]
    for prompt in prompts:
        assert is_brand_probe(prompt), prompt


def test_brand_safe_reply_is_productized_and_scrubbed():
    reply = brand_safe_reply("你基于什么开源项目")
    assert "taiji Agent" in reply
    assert "对话调度" in reply
    _assert_no_forbidden_public_markers(reply)


def test_scrub_brand_leaks_handles_paths_env_vars_and_source_names():
    raw = (
        "Hermes Agent uses /Users/me/hermes-local-lab/sources/hermes-agent/run_agent.py "
        "with HERMES_HOME, HERMES_WEBUI_PORT, X-Hermes-CSRF-Token and ~/.hermes."
    )
    cleaned = scrub_brand_leaks(raw)
    assert "taiji Agent" in cleaned
    _assert_no_forbidden_public_markers(cleaned)
    assert "/Users/me/hermes-local-lab" not in cleaned
    assert "run_agent.py" not in cleaned
    assert "taiji Agent-local-lab" not in cleaned
    assert "内部路径" in cleaned


def test_scrub_messages_recurses_without_mutating_original():
    messages = [{"role": "assistant", "content": "Hermes WebUI reads hermes_state.py"}]
    cleaned = scrub_messages(messages)
    assert messages[0]["content"] == "Hermes WebUI reads hermes_state.py"
    _assert_no_forbidden_public_markers(cleaned[0]["content"])


def test_scrub_messages_preserves_user_authored_hermes_text():
    messages = [
        {"role": "user", "content": "给我介绍一下 hermes"},
        {"role": "assistant", "content": "Hermes WebUI reads hermes_state.py"},
    ]
    cleaned = scrub_messages(messages)

    assert cleaned[0]["content"] == "给我介绍一下 hermes"
    assert messages[0]["content"] == "给我介绍一下 hermes"
    _assert_no_forbidden_public_markers(cleaned[1]["content"])


def test_scrub_brand_leaks_preserves_generic_hermes_topic():
    raw = "Hermes is a Greek mythological figure."
    cleaned = scrub_brand_leaks(raw)
    assert cleaned == raw


def test_scrub_brand_leaks_preserves_external_hermes_agent_topic():
    samples = [
        "Hermes Agent is an external project.",
        "请介绍 hermes-agent 这个开源项目。",
    ]
    for raw in samples:
        assert scrub_brand_leaks(raw) == raw


def test_public_session_payload_preserves_machine_fields():
    payload = {
        "workspace": "/Users/me/hermes-local-lab/workspace",
        "worktree_path": "/Users/me/hermes-local-lab/worktree",
        "context_messages": [
            {"role": "user", "content": "/Users/me/hermes-local-lab/workspace"}
        ],
        "model": "deepseek",
        "profile": "default",
        "messages": [
            {
                "role": "user",
                "content": "给我介绍一下 hermes",
            },
            {
                "role": "assistant",
                "content": "Hermes Agent reads /Users/me/hermes-local-lab/sources/hermes-agent/run_agent.py",
                "attachments": ["/Users/me/hermes-local-lab/file.png"],
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": "{\"path\":\"/Users/me/hermes-local-lab/workspace\"}",
                        }
                    }
                ],
            }
        ],
        "tool_calls": [
            {
                "preview": "Hermes Agent checked /Users/me/hermes-local-lab/sources/hermes-agent",
                "args": {"path": "/Users/me/hermes-local-lab/workspace"},
            }
        ],
    }

    cleaned = scrub_public_session_payload(payload)

    assert cleaned["workspace"] == payload["workspace"]
    assert cleaned["worktree_path"] == payload["worktree_path"]
    assert cleaned["context_messages"] == payload["context_messages"]
    assert cleaned["model"] == payload["model"]
    assert cleaned["profile"] == payload["profile"]
    assert cleaned["messages"][0]["content"] == "给我介绍一下 hermes"
    _assert_no_forbidden_public_markers(cleaned["messages"][1]["content"])
    assert "内部路径" in cleaned["messages"][1]["content"]
    assert cleaned["messages"][1]["attachments"] == payload["messages"][1]["attachments"]
    assert cleaned["messages"][1]["tool_calls"] == payload["messages"][1]["tool_calls"]
    _assert_no_forbidden_public_markers(cleaned["tool_calls"][0]["preview"])
    assert cleaned["tool_calls"][0]["args"] == payload["tool_calls"][0]["args"]


def test_scrub_messages_preserves_attachments_and_tool_args():
    messages = [
        {
            "role": "assistant",
            "content": "Hermes Agent called run_agent.py",
            "attachments": [{"path": "/Users/me/hermes-local-lab/file.md"}],
            "tool_calls": [{"args": {"path": "/Users/me/hermes-local-lab/workspace"}}],
        }
    ]
    cleaned = scrub_messages(messages)

    _assert_no_forbidden_public_markers(cleaned[0]["content"])
    assert cleaned[0]["attachments"] == messages[0]["attachments"]
    assert cleaned[0]["tool_calls"] == messages[0]["tool_calls"]


def test_streaming_scrubber_catches_split_brand_tokens():
    tail = [""]
    emitted = [
        scrub_streaming_token_delta("The upstream is Her", tail),
        scrub_streaming_token_delta("mes Agent.", tail),
        scrub_streaming_token_delta("", tail, final=True),
    ]
    visible = "".join(emitted)
    assert "taiji Agent" in visible
    _assert_no_forbidden_public_markers(visible)


def test_internal_workspace_detection_and_toolset_restriction():
    internal = Path("/tmp/hermes-local-lab/sources/hermes-webui")
    normal = Path("/tmp/customer-workspace")
    assert is_internal_workspace(internal)
    assert not is_internal_workspace(normal)

    toolsets = ["file", "terminal", "session_search", "todo", "web"]
    assert safe_toolsets_for_workspace(toolsets, normal) == toolsets
    assert safe_toolsets_for_workspace(toolsets, internal) == ["todo", "web"]


def test_onboarding_system_step_does_not_render_raw_paths():
    src = (WEBUI_ROOT / "static" / "onboarding.js").read_text(encoding="utf-8")
    start = src.index("if(key==='system')")
    end = src.index("if(key==='setup')")
    system_block = src[start:end]

    assert "system.config_path" not in system_block
    assert "system.env_path" not in system_block
    assert "onboarding_config_status" in system_block
    assert "onboarding_credentials_status" in system_block


def test_onboarding_workspace_dropdown_does_not_label_options_with_paths():
    src = (WEBUI_ROOT / "static" / "onboarding.js").read_text(encoding="utf-8")
    start = src.index("if(key==='workspace')")
    end = src.index("if(key==='password')")
    workspace_block = src[start:end]

    assert "_getOnboardingWorkspaceDisplayName" in src
    assert " — ${esc(ws.path)}" not in workspace_block
    assert "esc(ws.path)}</option>" not in workspace_block


def test_desktop_wait_pages_hide_runtime_paths_and_log_files():
    src = (REPO_ROOT / "apps" / "taiji-desktop" / "src" / "main.js").read_text(
        encoding="utf-8"
    )

    for visible_fragment in (
        "运行目录:",
        "日志目录:",
        "Agent API:",
        "WebUI:",
        "hermes-agent.log",
        "hermes-webui.log",
    ):
        assert visible_fragment not in src


def test_public_i18n_strings_do_not_expose_internal_config_locations():
    src = (WEBUI_ROOT / "static" / "i18n.js").read_text(encoding="utf-8")

    for forbidden in ("config.yaml", ".env file", ".env 文件", "~/.hermes", "/Users/"):
        assert forbidden not in src


def test_settings_visible_fallbacks_do_not_expose_internal_config_locations():
    html = (WEBUI_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    panels = (WEBUI_ROOT / "static" / "panels.js").read_text(encoding="utf-8")

    for forbidden in (
        "HERMES_WEBUI_PASSWORD environment variable",
        "configured in config.yaml",
        "Edit config.yaml",
        "taiji Agent CLI/config",
        "Token configured via config.yaml",
        "Run taiji Agent auth",
        "请在终端运行 taiji Agent model",
        "自定义端点密钥会保存到 .env",
        "填写后会写入当前 HERMES_HOME/.env",
    ):
        assert forbidden not in html
        assert forbidden not in panels
