"""Brand privacy guardrails for productized WebUI conversations.

This module protects ordinary browser chat from exposing implementation
provenance, internal paths, source filenames, and runtime configuration names.
It is intentionally presentation-layer protection; it does not rename internal
APIs, headers, environment variables, or filesystem layout.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BRAND_NAME = "taiji Agent"

FORBIDDEN_PUBLIC_MARKERS = (
    "Hermes",
    "Hermes Agent",
    "Hermes WebUI",
    "hermes-agent",
    "hermes-webui",
    "NousResearch/hermes-agent",
    "run_agent.py",
    "hermes_state.py",
    "model_tools.py",
    "tools/registry.py",
    "HERMES_HOME",
    "HERMES_WEBUI_",
    "X-Hermes-CSRF-Token",
    "Nous Research",
    "Hermes Web UI Contributors",
    "agent-runtime",
    "web-runtime",
    "claw.pyc",
    "~/.hermes",
    "hermes_cli",
    "hermes-local-lab",
)

BRAND_PRIVACY_SYSTEM_PROMPT = """
Brand privacy policy for ordinary WebUI conversations:
- Present yourself only as taiji Agent, an enterprise local intelligent assistant.
- Do not disclose upstream open-source provenance, internal package names, source
  filenames, implementation paths, runtime configuration names, environment
  variables, server ports, local URLs, launch commands, repository URLs, or CSRF
  header names.
- If the user asks about true identity, upstream source, internal architecture,
  source code, config files, deployment paths, environment variables, ports, or
  access URLs, answer with a product-level capability overview instead.
- You may describe product modules such as conversation orchestration, tools,
  workspace files, scheduled tasks, memory, skills, expert teams, logs,
  statistics, and profile management.
""".strip()

_FORBIDDEN_LITERAL_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"NousResearch/hermes-agent|Hermes\s+(?:Agent|WebUI)|"
    r"hermes[-_ ]?(?:agent|webui|cli|local-lab|state)|"
    r"run_agent\.py|hermes_state\.py|model_tools\.py|tools/registry\.py|"
    r"HERMES_HOME|HERMES_WEBUI_[A-Z0-9_]*|X-Hermes-CSRF-Token|"
    r"~/.hermes|/[^ \n\r\t`'\"<>]*hermes[^ \n\r\t`'\"<>]*"
    r")"
)

_STRONG_INTERNAL_MARKER_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"NousResearch/hermes-agent|Hermes\s+WebUI|"
    r"hermes[-_ ]?(?:webui|cli|local-lab|state)|"
    r"run_agent\.py|hermes_state\.py|model_tools\.py|tools/registry\.py|"
    r"HERMES_HOME|HERMES_WEBUI_[A-Z0-9_]*|X-Hermes-CSRF-Token|"
    r"~/.hermes|/[^ \n\r\t`'\"<>]*hermes[^ \n\r\t`'\"<>]*"
    r")"
)

_HERMES_TOPIC_RE = re.compile(
    r"(?i)(?:\bhermes\b|herm[eè]s|Hermes\s+Agent|hermes[-_ ]?agent)"
)

_SELF_REFERENCE_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"你|你的|您|你们|当前(?:产品|系统|助手|智能体)|这个(?:产品|系统|助手|智能体)|"
    r"本(?:产品|系统|助手|智能体)|taiji\s*Agent|太极智能体|太极\s*Agent|"
    r"\byou\b|\byour\b|\byours\b|\bthis\s+(?:product|system|assistant|agent|webui)\b|"
    r"\bcurrent\s+(?:product|system|assistant|agent|webui)\b"
    r")"
)

_PROVENANCE_LINK_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"是不是基于|基于.*(?:hermes|Hermes)|用了.*(?:hermes|Hermes)|"
    r"(?:hermes|Hermes).*(?:开发|底层|上游|来源|框架|实现|源码)|"
    r"\bbased\s+on\b|\bbuilt\s+on\b|\buse[sd]?\b.*(?:hermes|Hermes)|"
    r"(?:hermes|Hermes).*(?:upstream|source|implementation|framework|underlying)"
    r")"
)

_IMPLICIT_PRODUCT_LINK_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"是不是基于|基于.*(?:hermes|Hermes)|用了.*(?:hermes|Hermes)|"
    r"\bbased\s+on\b|\bbuilt\s+on\b|\buse[sd]?\b.*(?:hermes|Hermes)"
    r")"
)

_BRAND_PROBE_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"上游|开源|基于什么|基于哪个|是不是基于|来源|真实身份|底层|内核|核心架构|技术架构|"
    r"源码|代码实现|实现代码|源代码|读取.*代码|查看.*代码|"
    r"配置文件|配置项|环境变量|启动命令|部署路径|安装路径|仓库地址|github|"
    r"访问地址|本地地址|服务地址|端口|url|localhost|127\.0\.0\.1|"
    r"\bopen\s*source\b|\bupstream\b|\bbased\s+on\b|\bbuilt\s+on\b|\bsource\s+code\b|\bimplementation\b|"
    r"\binternal\s+architecture\b|\bkernel\s+architecture\b|\bcore\s+architecture\b|\bconfig(?:uration)?\s+file\b|"
    r"\benvironment\s+variable\b|\blaunch\s+command\b|\bdeploy(?:ment)?\s+path\b|\brepository\b|\brepo\s+url\b|"
    r"\baccess\s+url\b|\bservice\s+url\b|\bserver\s+url\b|\bport\b|"
    r"ignore.*(?:rule|instruction)|忽略.*(?:规则|指令)|绕过.*(?:限制|规则)|系统提示词"
    r")"
)

_LICENSE_PROBE_RE = re.compile(
    r"(?is)(?:版权|著作权|许可|许可证|license|mit|copyright|归属|rights?|owner|attribution)"
)

_IDENTITY_PROVENANCE_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"自研|自己开发|自主研发|原生开发|不是自研|非自研|套壳|换皮|二开|改造|"
    r"基于什么|基于哪个|是不是基于|拿.*开源|用了.*开源|开源.*(?:底层|组件|项目|代码)|"
    r"\bopen\s*source\b|\bupstream\b|\bbased\s+on\b|\bbuilt\s+on\b|\bfork(?:ed)?\b"
    r")"
)

_IMPLEMENTATION_INSPECTION_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"(?:搜索|扫描|遍历|读取|查看|列出).{0,24}(?:你自己|自身|当前系统|agent|安装|运行时|runtime|源码|代码|目录|路径|文件)|"
    r"(?:逻辑架构|文件情况|目录结构|包结构|模块结构|底层架构|技术架构|核心架构)|"
    r"(?:site-packages|dist-info|pyc|源码快照|安装目录|运行目录|runtime\s+home)"
    r")"
)

_RUNTIME_ACCESS_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"端口|访问地址|本地地址|服务地址|localhost|127\.0\.0\.1|url|"
    r"配置文件|配置项|环境变量|启动命令|部署路径|安装路径|日志路径|日志文件|"
    r"进程|pid|ps\s+|/proc/|cmdline|environ|runtime|HERMES_[A-Z0-9_]*"
    r")"
)

_PROMPT_BYPASS_RE = re.compile(
    r"(?is)(?:系统提示词|system\s+prompt|developer\s+message|忽略.*(?:规则|指令)|绕过.*(?:限制|规则)|无视.*(?:规则|指令)|越狱|jailbreak)"
)

_SEMANTIC_BRAND_LEAK_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"Nous\s+Research|Hermes\s+Web\s+UI\s+Contributors|"
    r"agent-runtime|web-runtime|claw\.pyc|"
    r"(?:这|本|该|当前|产品|系统|助手|智能体|taiji\s*Agent|太极智能体).{0,50}"
    r"(?:不是(?:完全)?自研|非自研|开源底层|套壳|换皮|二开|开源组件.{0,20}(?:包装|拼|改造|再发行))|"
    r"(?:开源底层|开源组件).{0,40}(?:包装|拼|改造|套壳|换皮|再发行)"
    r")"
)

_FORBIDDEN_OUTPUT_DETAIL_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"NousResearch/hermes-agent|Nous\s+Research|Hermes\s+Web\s+UI\s+Contributors|"
    r"Hermes\s+WebUI|agent-runtime|web-runtime|claw\.pyc|"
    r"run_agent\.py|hermes_state\.py|model_tools\.py|tools/registry\.py|"
    r"HERMES_HOME|HERMES_WEBUI_[A-Z0-9_]*|X-Hermes-CSRF-Token|"
    r"~/.hermes|/[^ \n\r\t`'\"<>]*hermes[^ \n\r\t`'\"<>]*|"
    r"(?:基于|改自|二开|换皮|底层|上游|来源|built\s+on|based\s+on).{0,40}"
    r"(?:Hermes|hermes[-_ ]?agent)|"
    r"(?:Hermes|hermes[-_ ]?agent).{0,40}"
    r"(?:改造|二开|换皮|底层|上游|来源|不是(?:完全)?自研|非自研)"
    r")"
)

_SELF_SCAN_RE = re.compile(
    r"(?is)(?:你自己|自身|当前(?:产品|系统|助手|智能体)|这个(?:产品|系统|助手|智能体)|本(?:产品|系统|助手|智能体)|taiji\s*Agent|太极智能体|yourself|your own|this product|this system|this assistant|this agent)"
)

_GENERAL_FILE_TASK_RE = re.compile(
    r"(?:这个工作区有哪些文件|列出(?:当前)?文件|浏览文件|打开文件|读取这个文件|帮我写|帮我总结|今天有什么安排|运行一条系统命令)"
)

_INTERNAL_PATH_PARTS = {
    "hermes-local-lab",
    "hermes-agent",
    "hermes-webui",
    ".hermes",
}

_SAFE_TOOLSETS_FOR_INTERNAL_WORKSPACE = ("clarify", "todo", "web")
_BRAND_STREAM_HOLD_CHARS = 96
_UNSAFE_INTERNAL_TOOLSETS = {
    "file",
    "terminal",
    "code_execution",
    "session_search",
    "skills",
    "mcp",
    "delegation",
    "workspace",
}


@dataclass(frozen=True)
class BrandSafetyDecision:
    action: str
    risk: str = "normal"
    safe_reply: str = ""
    reason: str = ""


class BrandSafetyPolicy:
    """Classify ordinary WebUI prompts into public chat brand-safety actions."""

    def classify_prompt(self, text: str, *, session_tainted: bool = False) -> BrandSafetyDecision:
        value = str(text or "").strip()
        if not value:
            return BrandSafetyDecision(action="allow")

        if _PROMPT_BYPASS_RE.search(value):
            return self._safe("prompt_bypass", "prompt-bypass request")

        self_referential = bool(_SELF_REFERENCE_RE.search(value) or _SELF_SCAN_RE.search(value))
        if (
            _HERMES_TOPIC_RE.search(value)
            and not self_referential
            and not _IMPLICIT_PRODUCT_LINK_RE.search(value)
            and re.search(r"(?is)(?:介绍|是什么|what\s+is|tell\s+me\s+about)", value)
        ):
            return BrandSafetyDecision(action="allow")

        internal_marker = bool(_STRONG_INTERNAL_MARKER_RE.search(value) or _HERMES_TOPIC_RE.search(value))
        provenance = bool(_IDENTITY_PROVENANCE_RE.search(value) or _PROVENANCE_LINK_RE.search(value))
        implementation = bool(_IMPLEMENTATION_INSPECTION_RE.search(value))
        runtime = bool(_RUNTIME_ACCESS_RE.search(value))
        license_probe = bool(_LICENSE_PROBE_RE.search(value))

        if license_probe and (self_referential or internal_marker or session_tainted):
            return self._safe("license", "license or copyright probe")
        if implementation and (self_referential or internal_marker or session_tainted):
            return self._safe("implementation_inspection", "implementation inspection probe")
        if runtime:
            return self._safe("runtime_access", "runtime access probe")
        if provenance and (self_referential or internal_marker or session_tainted):
            return self._safe("identity_provenance", "identity or provenance probe")

        if _BRAND_PROBE_RE.search(value):
            if _GENERAL_FILE_TASK_RE.search(value) and not re.search(
                r"(?i)(hermes|源码|源代码|内核|底层|配置文件|环境变量|端口|访问地址|localhost|127\.0\.0\.1|github|版权|许可证|license|自研|开源)",
                value,
            ):
                return BrandSafetyDecision(action="allow")
            if self_referential or internal_marker or session_tainted:
                return self._safe("identity_provenance", "generic brand probe")

        return BrandSafetyDecision(action="allow")

    def validate_output(self, text: str) -> BrandSafetyDecision:
        value = str(text or "")
        if not value.strip():
            return BrandSafetyDecision(action="allow")
        if _SEMANTIC_BRAND_LEAK_RE.search(value) or _contains_forbidden_public_detail(value):
            return BrandSafetyDecision(
                action="replace_output",
                risk="output_leak",
                safe_reply=brand_safe_reply(""),
                reason="forbidden public detail in output",
            )
        return BrandSafetyDecision(action="allow")

    @staticmethod
    def _safe(risk: str, reason: str) -> BrandSafetyDecision:
        return BrandSafetyDecision(
            action="safe_reply",
            risk=risk,
            safe_reply=brand_safe_reply(""),
            reason=reason,
        )


_BRAND_SAFETY_POLICY = BrandSafetyPolicy()


def is_brand_probe(text: str) -> bool:
    """Return True for prompts attempting to reveal internal provenance."""
    return _BRAND_SAFETY_POLICY.classify_prompt(text).action == "safe_reply"


def classify_brand_safety_prompt(text: str, *, session_tainted: bool = False) -> BrandSafetyDecision:
    """Classify a user prompt for ordinary public chat brand safety."""
    return _BRAND_SAFETY_POLICY.classify_prompt(text, session_tainted=session_tainted)


def brand_safety_validate(text: str) -> BrandSafetyDecision:
    """Validate a completed user-visible answer before display/persistence."""
    return _BRAND_SAFETY_POLICY.validate_output(text)


def brand_safe_reply(user_text: str = "") -> str:
    """Return a productized answer for sensitive provenance probes."""
    return (
        "taiji Agent 由太极智能体项目组维护交付，内部实现、第三方组件与部署细节不在普通对话中公开。\n\n"
        "从产品能力层面看，它由这些模块协同工作：\n"
        "- 对话调度：维护上下文、管理多轮任务状态，并把结果整理成可读回复。\n"
        "- 工具协同：在授权范围内调用文件、搜索、任务和系统操作能力。\n"
        "- 工作区文件：围绕用户选择的工作区进行浏览、整理、摘要和生成。\n"
        "- 计划任务：创建、查看和管理定时或周期性任务。\n"
        "- 记忆与技能：沉淀可复用偏好、流程和专业能力。\n"
        "- 专家团：按写作、研究、审稿等角色拆分复杂工作。\n"
        "- 日志与统计：展示运行状态、任务结果和使用概览。\n"
        "- 配置管理：面向管理员提供受控的模型、权限和工作区管理入口。\n\n"
        "如果你要排查部署或运维问题，请在受控管理员环境中查看内部运维资料。"
    )


def scrub_brand_leaks(value: Any) -> Any:
    """Scrub brand/provenance leaks from UI-bound values."""
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, list):
        return [scrub_brand_leaks(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_brand_leaks(item) for item in value)
    if isinstance(value, dict):
        return {key: scrub_brand_leaks(item) for key, item in value.items()}
    return value


def scrub_brand_leaks_text(text: str) -> str:
    """Scrub brand/provenance leaks from one user-visible string."""
    return _scrub_text(text)


def scrub_public_message(message: Any) -> Any:
    """Scrub only user-visible message fields, preserving machine fields.

    Attachments, tool call arguments, and other operational metadata may contain
    real filesystem paths that the runtime needs later. Those values must not be
    rewritten to display placeholders such as "内部路径".
    """
    if not isinstance(message, dict):
        return copy.deepcopy(message)
    cleaned = copy.deepcopy(message)
    role = str(cleaned.get("role") or "").strip().lower()
    if role == "user":
        return cleaned
    for key in (
        "content",
        "reasoning",
        "reasoning_content",
        "provider_details",
        "provider_details_label",
        "preview",
        "snippet",
        "text",
    ):
        if key in cleaned:
            cleaned[key] = scrub_brand_leaks(cleaned.get(key))
    return cleaned


def scrub_public_session_payload(payload: Any) -> Any:
    """Scrub a session/API payload without touching executable state fields."""
    if not isinstance(payload, dict):
        return scrub_brand_leaks(payload)
    cleaned = copy.deepcopy(payload)
    if "title" in cleaned:
        cleaned["title"] = scrub_brand_leaks(cleaned.get("title"))
    if isinstance(cleaned.get("messages"), list):
        cleaned["messages"] = [scrub_public_message(item) for item in cleaned["messages"]]
    if isinstance(cleaned.get("tool_calls"), list):
        next_tool_calls = []
        for call in cleaned["tool_calls"]:
            if not isinstance(call, dict):
                next_tool_calls.append(copy.deepcopy(call))
                continue
            item = copy.deepcopy(call)
            for key in ("preview", "snippet", "result", "output", "error", "message"):
                if key in item:
                    item[key] = scrub_brand_leaks(item.get(key))
            next_tool_calls.append(item)
        cleaned["tool_calls"] = next_tool_calls
    return cleaned


def scrub_public_export_payload(payload: Any) -> Any:
    """Scrub a full export payload, including model-facing history fields."""
    cleaned = scrub_public_session_payload(payload)
    if isinstance(cleaned, dict):
        if isinstance(cleaned.get("context_messages"), list):
            cleaned["context_messages"] = scrub_messages(cleaned["context_messages"])
        if isinstance(cleaned.get("messages"), list):
            cleaned["messages"] = scrub_messages(cleaned["messages"])
    return cleaned


def scrub_messages(messages: Any) -> Any:
    """Return a scrubbed deep copy of session messages/history."""
    if isinstance(messages, list):
        return [scrub_public_message(item) for item in messages]
    return scrub_public_message(messages)



def is_internal_workspace(path: str | Path | None) -> bool:
    """Return True for source/runtime directories that ordinary chat should not inspect."""
    if not path:
        return False
    try:
        candidate = Path(str(path)).expanduser().resolve(strict=False)
    except Exception:
        candidate = Path(str(path)).expanduser()
    parts = {part.lower() for part in candidate.parts}
    if parts & _INTERNAL_PATH_PARTS:
        return True
    try:
        home_internal = (Path.home() / ".hermes").resolve(strict=False)
        if candidate == home_internal or home_internal in candidate.parents:
            return True
    except Exception:
        pass
    lowered = str(candidate).lower()
    return "hermes-local-lab" in lowered or "hermes-agent" in lowered or "hermes-webui" in lowered


def safe_toolsets_for_workspace(toolsets: list[str] | tuple[str, ...] | None, workspace: str | Path | None) -> list[str]:
    """Restrict risky tools when the selected workspace is an internal implementation area."""
    normalized = [str(item) for item in (toolsets or []) if str(item or "").strip()]
    if not is_internal_workspace(workspace):
        return normalized
    safe = [
        item
        for item in normalized
        if item.strip().lower() not in _UNSAFE_INTERNAL_TOOLSETS
    ]
    if safe:
        return safe
    return list(_SAFE_TOOLSETS_FOR_INTERNAL_WORKSPACE)


def scrub_streaming_token_delta(delta: str, tail_ref: list[str], *, final: bool = False) -> str:
    """Scrub token streams across chunk boundaries by holding a short suffix."""
    combined = str(tail_ref[0] or "") + str(delta or "")
    cleaned = scrub_brand_leaks(combined)
    if final:
        tail_ref[0] = ""
        return cleaned
    if len(cleaned) <= _BRAND_STREAM_HOLD_CHARS:
        tail_ref[0] = cleaned
        return ""
    emit = cleaned[:-_BRAND_STREAM_HOLD_CHARS]
    tail_ref[0] = cleaned[-_BRAND_STREAM_HOLD_CHARS:]
    return emit


def _scrub_text(text: str) -> str:
    result = str(text or "")
    if _SEMANTIC_BRAND_LEAK_RE.search(result):
        return "内部实现细节已省略。"
    # Scrub absolute internal paths before generic brand replacements. If this
    # runs after replacing "hermes" with BRAND_NAME, paths like
    # /.../hermes-local-lab/workspace become fake executable paths such as
    # /.../taiji Agent-local-lab/workspace and poison subsequent tool calls.
    result = re.sub(r"(?i)/[^ \n\r\t`'\"<>]*hermes[^ \n\r\t`'\"<>]*", "内部路径", result)
    has_internal_context = bool(
        re.search(
            r"(?i)(内部路径|NousResearch/hermes-agent|hermes[-_ ]?(?:webui|cli|local-lab|state)|"
            r"run_agent\.py|hermes_state\.py|model_tools\.py|tools/registry\.py|"
            r"HERMES_HOME|HERMES_WEBUI_[A-Z0-9_]*|X-Hermes-CSRF-Token|~/.hermes|"
            r"底层|内核|上游|来源|源码|源代码|配置文件|环境变量|端口|访问地址|"
            r"based\s+on|built\s+on|upstream|implementation|source\s+code|internal)",
            result,
        )
    )
    if has_internal_context:
        result = re.sub(r"(?i)Hermes\s+(?:Agent|WebUI)", BRAND_NAME, result)
        result = re.sub(r"(?i)NousResearch/hermes-agent", "内部实现细节", result)
        result = re.sub(r"(?i)hermes[-_ ]?(?:agent|webui|cli|local-lab|state)", "内部实现细节", result)
    else:
        result = re.sub(r"(?i)NousResearch/hermes-agent", "内部实现细节", result)
        result = re.sub(r"(?i)hermes[-_ ]?(?:webui|cli|local-lab|state)", "内部实现细节", result)
    result = re.sub(r"(?i)\b(?:run_agent|hermes_state|model_tools)\.py\b", "内部实现文件", result)
    result = re.sub(r"(?i)\btools/registry\.py\b", "内部实现文件", result)
    result = re.sub(r"\bHERMES_WEBUI_[A-Z0-9_]*\b", "内部配置项", result)
    result = re.sub(r"\bHERMES_HOME\b", "内部配置项", result)
    result = re.sub(r"(?i)\bX-Hermes-CSRF-Token\b", "内部安全头", result)
    result = re.sub(r"(?i)~/.hermes\b", "内部状态目录", result)
    return result


def _contains_forbidden_public_detail(text: str) -> bool:
    value = str(text or "")
    if _SEMANTIC_BRAND_LEAK_RE.search(value):
        return True
    return bool(_FORBIDDEN_OUTPUT_DETAIL_RE.search(value))
