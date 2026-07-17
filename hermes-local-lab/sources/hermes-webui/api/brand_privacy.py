"""Brand privacy guardrails for productized WebUI conversations.

This module protects ordinary browser chat from exposing implementation
provenance, internal paths, source filenames, and runtime configuration names.
It is intentionally presentation-layer protection; it does not rename internal
APIs, headers, environment variables, or filesystem layout.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BRAND_NAME = "taiji Agent"

FORBIDDEN_PUBLIC_MARKERS = (
    "Hermes",
    "Hermes Agent",
    "Hermes WebUI",
    "Web UI",
    "hermes-agent",
    "hermes-webui",
    "NousResearch/hermes-agent",
    "run_agent.py",
    "cli.py",
    "conversation_loop.py",
    "prompt_builder.py",
    "gateway/run.py",
    "AIAgent",
    "hermes_state.py",
    "model_tools.py",
    "tools/registry.py",
    "HERMES_HOME",
    "HERMES_WEBUI_",
    "X-Hermes-CSRF-Token",
    "127.0.0.1",
    "localhost",
    "Nous Research",
    "Hermes Web UI Contributors",
    "agent-runtime",
    "web-runtime",
    "OpenClaw",
    "claw",
    "hermes claw",
    "claw.pyc",
    "API 网关",
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

_CLAW_TOPIC_RE = re.compile(r"(?i)(?:\bclaw\b|openclaw|hermes\s+claw)")

_SELF_REFERENCE_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"你自己|您自己|你自身|您自身|"
    r"(?:你|您)的(?:内部|本机|本地|源码|源代码|实现|运行时|runtime|配置|环境变量|"
    r"端口|地址|桌面服务|服务地址|访问地址|访问方式|启动命令|安装路径|部署路径|日志|进程|许可证|版权|底层|内核)|"
    r"(?:说|告诉).{0,8}你(?:到底)?是不是(?:自研|基于|拿|用|使用)|"
    r"你(?:到底)?是不是(?:自研|基于|拿|用|使用)|"
    r"(?:你|您)(?:本地|本机)(?:服务|端口|地址|访问|进程|配置|日志)|"
    r"(?:通过|使用).{0,16}(?:web|网页|浏览器).{0,16}访问(?:你|您)|"
    r"(?:你|您)(?:这个|这套|当前的)(?:产品|系统|助手|智能体)|"
    r"当前(?:产品|系统|助手|智能体)|这个(?:产品|系统|助手|智能体)|"
    r"本(?:产品|系统|助手|智能体)|taiji\s*Agent|太极智能体|太极\s*Agent|"
    r"\byourself\b|\byour\s+(?:internal|local|runtime|source\s+code|implementation|"
    r"configuration|environment|port|service|installation|deployment|logs?|process|license|copyright)|"
    r"\bare\s+you\s+(?:based|built|running)\b|"
    r"\bthis\s+(?:product|system|assistant|agent|webui)\b|"
    r"\bcurrent\s+(?:product|system|assistant|agent|webui)\b"
    r")"
)

_PROVENANCE_LINK_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"是不是基于|基于.*(?:hermes|Hermes)|用了.*(?:hermes|Hermes)|"
    r"(?:hermes|Hermes|claw|OpenClaw).*(?:开发|底层|上游|来源|框架|实现|源码)|"
    r"(?:用了|使用|依赖).{0,20}(?:claw|OpenClaw)|"
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
    r"(?:逻辑架构|文件情况|目录结构|包结构|模块结构|底层架构|技术架构|核心架构|完整架构|架构信息|架构|原理)|"
    r"(?:怎么(?:开发|实现|运行)|如何(?:开发|实现|运行)|开发出来|实现原理|底层原理|工作原理|你的原理|框架吗|用.*框架)|"
    r"(?:site-packages|dist-info|pyc|源码快照|安装目录|运行目录|runtime\s+home)"
    r")"
)

_RUNTIME_ACCESS_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"端口|监听|访问地址|本地地址|服务地址|服务入口|本机服务|桌面服务|"
    r"浏览器.{0,24}(?:打开|访问|进入)|地址栏|直连|健康检查|验证.{0,20}服务|"
    r"(?:web|网页|界面).{0,24}(?:打开|访问|进入|入口|方式)|"
    r"localhost|127\.0\.0\.1|url|"
    r"配置文件|配置项|环境变量|启动命令|部署路径|安装路径|日志路径|日志文件|"
    r"进程|pid|ps\s+|/proc/|cmdline|environ|runtime|HERMES_[A-Z0-9_]*"
    r")"
)

_SENSITIVE_INTERNAL_OBJECT_RE = re.compile(
    r"(?is)(?:"
    r"内部路径|内部目录|运行路径|运行目录|运行时路径|运行时目录|"
    r"配置文件(?:路径|目录)?|安装路径|安装目录|部署路径|部署目录|"
    r"\blog(?:s)?\s+(?:path|directory)\b|"
    r"\b(?:internal|runtime|configuration|config|installation|deployment)\s+"
    r"(?:path|directory|file)\b"
    r")"
)

_DISCLOSURE_ACCESS_INTENT_RE = re.compile(
    r"(?is)(?:"
    r"是什么|在哪里|位置|告诉|说出|给我|发给|披露|公开|提供|交代|"
    r"列出|展示|查看|读取|扫描|遍历|访问|打开|进入|"
    r"\b(?:what|where|show|tell|reveal|list|read|scan|browse|access|open)\b"
    r")"
)

_LOCAL_SERVICE_ACCESS_RE = re.compile(
    r"(?is)"
    r"(?:"
    r"https?://(?:127\.0\.0\.1|localhost|\[?::1\]?)(?::\d{1,5})?[^ \n\r\t`'\"<>]*|"
    r"(?:127\.0\.0\.1|localhost)(?::\d{1,5})|"
    r"\b(?:curl|wget)\s+https?://[^ \n\r\t`'\"<>]*|"
    r"(?:端口|port)\s*(?:是|为|:|=|is)?\s*\d{2,5}|"
    r"(?:API\s*网关|Web\s*UI|Web\s*界面|后端服务|后台服务).{0,80}\b\d{2,5}\b|"
    r"(?:访问方式|服务入口|地址栏|打开|访问|进入).{0,40}:\d{2,5}|"
    r"(?:浏览器|地址栏).{0,24}(?:打开|访问|进入|复制|粘贴)|"
    r"(?:服务|server|web\s*service).{0,40}(?:监听|listen|地址|url|端口|port|入口)"
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
    r"(?:hermes|claw|OpenClaw).{0,80}(?:命令|框架|迁移|实现|架构|源码|底层)|"
    r"(?:AIAgent|conversation_loop\.py|prompt_builder\.py|cli\.py|gateway/run\.py)|"
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
    r"run_agent\.py|cli\.py|conversation_loop\.py|prompt_builder\.py|gateway/run\.py|"
    r"AIAgent|hermes_state\.py|model_tools\.py|tools/registry\.py|"
    r"HERMES_HOME|HERMES_WEBUI_[A-Z0-9_]*|X-Hermes-CSRF-Token|"
    r"~/.hermes|/[^ \n\r\t`'\"<>]*hermes[^ \n\r\t`'\"<>]*|"
    r"(?:API\s*网关|Web\s*UI|后端服务).{0,80}\b\d{2,5}\b|"
    r"(?:访问方式|服务入口|地址栏|打开|访问|进入).{0,40}:\d{2,5}|"
    r"(?:claw|OpenClaw).{0,80}(?:框架|迁移|hermes|项目|依赖)|"
    r"(?:基于|改自|二开|换皮|底层|上游|来源|built\s+on|based\s+on).{0,40}"
    r"(?:Hermes|hermes[-_ ]?agent)|"
    r"(?:Hermes|hermes[-_ ]?agent).{0,40}"
    r"(?:改造|二开|换皮|底层|上游|来源|不是(?:完全)?自研|非自研)"
    r")"
)

_LOCAL_SERVICE_SAFE_TEXT = "请从桌面应用入口启动和使用 taiji Agent。内部访问方式不在普通对话中提供。"

_SELF_SCAN_RE = re.compile(
    r"(?is)(?:你自己|您自己|你自身|您自身|太极自身|太极(?:智能体|\s*Agent)自身|当前(?:产品|系统|助手|智能体)|这个(?:产品|系统|助手|智能体)|本(?:产品|系统|助手|智能体)|taiji\s*Agent|太极智能体|yourself|your own|this product|this system|this assistant|this agent)"
)

_GENERAL_FILE_TASK_RE = re.compile(
    r"(?:这个工作区有哪些文件|列出(?:当前)?文件|浏览文件|打开文件|读取这个文件|帮我写|帮我总结|今天有什么安排|运行一条系统命令)"
)

_INTERNAL_PATH_PARTS = {
    "taiji-agent",
    "hermes-local-lab",
    "hermes-agent",
    "hermes-webui",
    ".hermes",
}

_SAFE_TOOLSETS_FOR_INTERNAL_WORKSPACE = ("clarify", "todo", "web")
_BRAND_STREAM_HOLD_CHARS = 96
_STREAM_CREDENTIAL_MASK = "[REDACTED]"
_STREAM_CREDENTIAL_START_PATTERNS = (
    re.compile(r"(?i)(?<![A-Za-z0-9])(?:authorization\s*:\s*)?bearer\s+"),
    re.compile(r"(?<![A-Za-z0-9])sk[-_]"),
    re.compile(r"(?<![A-Za-z0-9])eyJ"),
    re.compile(
        r"(?i)(?<![A-Za-z0-9_])[A-Z][A-Z0-9_]{0,63}"
        r"(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET|PASSWORD|PRIVATE_KEY)\s*=\s*"
    ),
)
_STREAM_CREDENTIAL_VALUE_CHAR_RE = re.compile(r"[A-Za-z0-9._~+/=-]")
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

_PUBLIC_TOOL_EVENTS = {"tool", "tool_complete", "tool.started", "tool.completed"}
_PUBLIC_LOCAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:])(?:~/(?:\.[^\s/]+|[^\s/]+)|"
    r"/(?:Users|home|private|tmp|opt|var|etc|root|usr|mnt|srv|Applications)"
    r"(?:/[^\s`'\"<>]+)+)",
    re.IGNORECASE,
)

_PUBLIC_SESSION_FIELDS = (
    "session_id", "title", "display_title", "writeflow_title",
    "writeflow_team_id", "workspace", "model", "model_provider",
    "message_count", "actual_message_count", "user_message_count",
    "created_at", "updated_at", "last_message_at", "pinned", "archived",
    "project_id", "project_name", "profile", "default_hidden",
    "input_tokens", "output_tokens",
    "estimated_cost", "cache_read_tokens", "cache_write_tokens",
    "cache_hit_percent", "personality", "context_length", "threshold_tokens",
    "last_prompt_tokens", "compression_anchor_visible_idx",
    "compression_anchor_summary", "pre_compression_snapshot", "context_engine",
    "compression_anchor_engine", "compression_anchor_mode", "parent_session_id",
    "_lineage_root_id", "_lineage_tip_id", "_parent_lineage_root_id",
    "_compression_segment_count", "relationship_type",
    "active_stream_id", "pending_user_message", "pending_started_at",
    "has_pending_user_message", "is_cli_session", "source_tag", "raw_source",
    "session_source", "source_label", "read_only", "enabled_toolsets",
    "is_streaming", "_messages_truncated", "_messages_offset",
)

_PUBLIC_MESSAGE_SCALAR_FIELDS = (
    "role", "content", "timestamp", "_ts", "type", "message_id", "id",
    "name", "tool_call_id", "tool_use_id", "duration_seconds", "_error",
    "error_type", "is_error", "reasoning", "reasoning_content", "thinking",
    "provider_details", "provider_details_label", "text", "status", "summary",
    "done", "tid", "assistant_msg_idx", "_turnDuration", "_turnTps",
)

_PUBLIC_METADATA_FIELDS = {
    "id", "type", "kind", "code", "name", "title", "label", "description",
    "summary", "status", "state", "phase", "message", "reason", "headline",
    "provider", "requested_provider", "used_provider", "model",
    "requested_model", "used_model", "model_changed", "has_failover", "routing",
    "engine", "mode", "automatic", "capability", "quality_status", "template_id",
    "template", "templates", "examples", "actions", "choices", "choices_offered",
    "question", "created_at", "updated_at", "started_at", "ended_at", "expires_at",
    "duration", "duration_seconds", "count", "index", "total", "visible_idx",
    "role", "timestamp", "content", "text", "read_only", "enabled", "ok",
}

_PUBLIC_USAGE_FIELDS = (
    "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
    "cache_write_tokens", "cache_hit_percent", "estimated_cost", "context_length",
    "last_prompt_tokens", "threshold_tokens", "tps", "tps_available", "estimated",
    "session_id", "duration_seconds",
)


@dataclass(frozen=True)
class BrandSafetyDecision:
    action: str
    risk: str = "normal"
    safe_reply: str = ""
    reason: str = ""
    taint_session: bool = False
    requires_full_buffer: bool = False
    block_tools: bool = False
    replace_output: bool = False
    used_privacy_context: bool = False


class BrandSafetyPolicy:
    """Classify ordinary WebUI prompts into public chat brand-safety actions."""

    def classify_prompt(
        self,
        text: str,
        *,
        privacy_context: dict | None = None,
        adjacent_text: str = "",
    ) -> BrandSafetyDecision:
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

        internal_marker = bool(
            _STRONG_INTERNAL_MARKER_RE.search(value)
            or _HERMES_TOPIC_RE.search(value)
            or _CLAW_TOPIC_RE.search(value)
        )
        provenance = bool(_IDENTITY_PROVENANCE_RE.search(value) or _PROVENANCE_LINK_RE.search(value))
        implementation = bool(_IMPLEMENTATION_INSPECTION_RE.search(value))
        runtime = bool(_RUNTIME_ACCESS_RE.search(value))
        license_probe = bool(_LICENSE_PROBE_RE.search(value))
        try:
            context_remaining = int((privacy_context or {}).get("remaining_turns") or 0)
        except (AttributeError, TypeError, ValueError):
            context_remaining = 0
        context_active = bool(
            isinstance(privacy_context, dict)
            and context_remaining > 0
            and str(privacy_context.get("risk_type") or "").strip()
            and str(privacy_context.get("source_turn_id") or "").strip()
        )
        # The explicit context record is authoritative. ``adjacent_text`` is
        # accepted so callers can document the immediately preceding turn, but
        # it never creates context on its own and therefore cannot extend risk
        # beyond the single persisted turn budget.
        adjacent_target = bool(
            context_active
            and adjacent_text
            and (
                _SELF_REFERENCE_RE.search(str(adjacent_text))
                or _SELF_SCAN_RE.search(str(adjacent_text))
                or _STRONG_INTERNAL_MARKER_RE.search(str(adjacent_text))
            )
        )
        direct_target = self_referential or internal_marker
        has_internal_target = direct_target or context_active or adjacent_target
        contextual_only = context_active and not direct_target

        sensitive_internal_object = bool(_SENSITIVE_INTERNAL_OBJECT_RE.search(value))
        disclosure_or_access = bool(_DISCLOSURE_ACCESS_INTENT_RE.search(value))
        if has_internal_target and sensitive_internal_object and disclosure_or_access:
            return self._safe(
                "runtime_access",
                "internal path or configuration disclosure request",
                contextual=contextual_only,
            )

        if license_probe and has_internal_target:
            return self._safe("license", "license or copyright probe", contextual=contextual_only)
        if implementation and has_internal_target:
            return self._safe(
                "implementation_inspection",
                "implementation inspection probe",
                contextual=contextual_only,
            )
        if runtime and has_internal_target:
            return self._safe("runtime_access", "runtime access probe", contextual=contextual_only)
        if provenance and has_internal_target:
            return self._safe(
                "identity_provenance",
                "identity or provenance probe",
                contextual=contextual_only,
            )

        if _BRAND_PROBE_RE.search(value):
            if _GENERAL_FILE_TASK_RE.search(value) and not re.search(
                r"(?i)(hermes|源码|源代码|内核|底层|配置文件|环境变量|端口|访问地址|localhost|127\.0\.0\.1|github|版权|许可证|license|自研|开源)",
                value,
            ):
                return BrandSafetyDecision(action="allow")
            if has_internal_target:
                return self._safe(
                    "identity_provenance",
                    "generic brand probe",
                    contextual=contextual_only,
                )

        return BrandSafetyDecision(action="allow")

    def validate_output(self, text: str) -> BrandSafetyDecision:
        value = str(text or "")
        if not value.strip():
            return BrandSafetyDecision(action="allow")
        if _is_external_hermes_topic(value):
            return BrandSafetyDecision(action="allow")
        if _LOCAL_SERVICE_ACCESS_RE.search(value):
            return BrandSafetyDecision(
                action="replace_output",
                risk="runtime_access",
                safe_reply=brand_safe_reply(value, risk="runtime_access"),
                reason="local service access detail in output",
                taint_session=True,
                requires_full_buffer=True,
                block_tools=True,
                replace_output=True,
            )
        if _SEMANTIC_BRAND_LEAK_RE.search(value) or _contains_forbidden_public_detail(value):
            return BrandSafetyDecision(
                action="replace_output",
                risk="output_leak",
                safe_reply=brand_safe_reply(value),
                reason="forbidden public detail in output",
                taint_session=True,
                requires_full_buffer=True,
                block_tools=True,
                replace_output=True,
            )
        return BrandSafetyDecision(action="allow")

    @staticmethod
    def _safe(risk: str, reason: str, *, contextual: bool = False) -> BrandSafetyDecision:
        return BrandSafetyDecision(
            action="safe_reply",
            risk=risk,
            safe_reply=brand_safe_reply("", risk=risk),
            reason=reason,
            taint_session=not contextual,
            requires_full_buffer=True,
            block_tools=True,
            used_privacy_context=contextual,
        )


_BRAND_SAFETY_POLICY = BrandSafetyPolicy()


def _is_external_hermes_topic(value: str) -> bool:
    text = str(value or "")
    if not _HERMES_TOPIC_RE.search(text):
        return False
    if _SELF_REFERENCE_RE.search(text) or _SELF_SCAN_RE.search(text):
        return False
    if _IMPLICIT_PRODUCT_LINK_RE.search(text) or _PROVENANCE_LINK_RE.search(text):
        return False
    return bool(
        re.search(r"(?is)(?:介绍|是什么|what\s+is|tell\s+me\s+about|external|开源项目)", text)
    )


def is_brand_probe(text: str) -> bool:
    """Return True for prompts attempting to reveal internal provenance."""
    return _BRAND_SAFETY_POLICY.classify_prompt(text).action == "safe_reply"


def classify_brand_safety_prompt(
    text: str,
    *,
    privacy_context: dict | None = None,
    adjacent_text: str = "",
) -> BrandSafetyDecision:
    """Classify a user prompt for ordinary public chat brand safety."""
    return _BRAND_SAFETY_POLICY.classify_prompt(
        text,
        privacy_context=privacy_context,
        adjacent_text=adjacent_text,
    )


def brand_safety_validate(text: str) -> BrandSafetyDecision:
    """Validate a completed user-visible answer before display/persistence."""
    return _BRAND_SAFETY_POLICY.validate_output(text)


def brand_safe_reply(user_text: str = "", *, risk: str | None = None) -> str:
    """Return a productized answer for sensitive provenance probes."""
    risk = risk or classify_brand_safety_prompt(user_text).risk
    if risk == "runtime_access":
        return _LOCAL_SERVICE_SAFE_TEXT
    return (
        "taiji Agent 由太极智能体项目维护交付。内部实现、来源、第三方组件、"
        "源码结构和合规材料不在普通对话中公开。你可以继续让我处理业务任务、"
        "文档、文件、计划或专家团协作。"
    )


def _public_visible_text(text: Any) -> Any:
    """Return a safe whole-field replacement for public UI surfaces."""
    if not isinstance(text, str):
        try:
            visible = json.dumps(text, ensure_ascii=False, sort_keys=True)
        except Exception:
            visible = str(text)
        decision = brand_safety_validate(visible)
        if decision.action == "replace_output":
            return decision.safe_reply
        return scrub_brand_leaks(text)
    decision = brand_safety_validate(text)
    if decision.action == "replace_output":
        return decision.safe_reply
    return scrub_brand_leaks(text)


def _mask_public_sensitive_text(text: Any, *, hide_local_paths: bool = False) -> Any:
    """Mask credentials and, for operational summaries, local filesystem paths."""
    if isinstance(text, str):
        value = re.sub(
            r"(?i)(?<![A-Za-z0-9])(?:authorization\s*:\s*)?bearer\s+"
            r"[A-Za-z0-9._~+/=-]+",
            "[REDACTED]",
            text,
        )
        try:
            from api.helpers import _redact_text

            value = _redact_text(value, _enabled=True)
        except Exception:
            value = re.sub(
                r"(?i)\b(?:sk-|ghp_|hf_)[A-Za-z0-9_-]{12,}\b",
                "[REDACTED]",
                value,
            )
        if hide_local_paths:
            value = _PUBLIC_LOCAL_PATH_RE.sub("内部路径", value)
        return value
    if isinstance(text, list):
        return [
            _mask_public_sensitive_text(item, hide_local_paths=hide_local_paths)
            for item in text
        ]
    if isinstance(text, tuple):
        return tuple(
            _mask_public_sensitive_text(item, hide_local_paths=hide_local_paths)
            for item in text
        )
    if isinstance(text, dict):
        return {
            key: _mask_public_sensitive_text(item, hide_local_paths=hide_local_paths)
            for key, item in text.items()
        }
    return copy.deepcopy(text)


def _public_tool_projection(payload: Any, *, event_name: str | None = None) -> dict:
    """Project a tool lifecycle payload onto the public event contract."""
    source = payload if isinstance(payload, dict) else {}
    function = source.get("function") if isinstance(source.get("function"), dict) else {}
    completed = bool(source.get("done")) or str(event_name or "") in {"tool_complete", "tool.completed"}
    event_type = str(source.get("event_type") or ("tool.completed" if completed else "tool.started"))
    status = source.get("status")
    if status in (None, "") and ("done" in source or completed):
        status = "completed" if completed else "running"
    # summary is an explicit producer-owned public field. Never promote
    # preview/snippet/result into it: those legacy fields may contain raw
    # arguments, command output, or provider payloads.
    summary = source.get("summary") if isinstance(source.get("summary"), str) else ""
    cleaned: dict[str, Any] = {"event_type": event_type}
    if "name" in source or source.get("tool") or function.get("name"):
        cleaned["name"] = _public_visible_text(
            source.get("name") or source.get("tool") or function.get("name")
        )
    if status not in (None, ""):
        cleaned["status"] = _mask_public_sensitive_text(status, hide_local_paths=True)
    if source.get("duration") is not None:
        cleaned["duration"] = source.get("duration")
    if summary:
        cleaned["summary"] = _mask_public_sensitive_text(
            _public_visible_text(summary),
            hide_local_paths=True,
        )
    if "is_error" in source or source.get("error") is not None:
        cleaned["is_error"] = bool(source.get("is_error") or source.get("error"))
    if source.get("tid") or source.get("tool_call_id") or source.get("id"):
        cleaned["tid"] = str(source.get("tid") or source.get("tool_call_id") or source.get("id"))
    assistant_msg_idx = source.get("assistant_msg_idx")
    if isinstance(assistant_msg_idx, int) and not isinstance(assistant_msg_idx, bool):
        cleaned["assistant_msg_idx"] = assistant_msg_idx
    if isinstance(source.get("done"), bool):
        cleaned["done"] = source["done"]
    return cleaned


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


def _public_metadata_projection(value: Any) -> Any:
    """Project known display metadata without carrying operational dictionaries."""
    if isinstance(value, dict):
        projected = {}
        for key, item in value.items():
            if key not in _PUBLIC_METADATA_FIELDS:
                continue
            projected[key] = _public_metadata_projection(item)
        return projected
    if isinstance(value, (list, tuple)):
        return [_public_metadata_projection(item) for item in value]
    if isinstance(value, str):
        return _mask_public_sensitive_text(_public_visible_text(value), hide_local_paths=True)
    return copy.deepcopy(value)


def _safe_relative_ref(value: Any, *, workspace: str | None = None) -> str:
    """Return a workspace-relative browser reference, never an absolute path."""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    if raw.startswith("//") or re.match(r"^[A-Za-z]:/", raw):
        return ""
    try:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            if not workspace:
                return ""
            workspace_root = Path(workspace).expanduser().resolve()
            resolved = candidate.resolve()
            if not resolved.is_relative_to(workspace_root):
                return ""
            return resolved.relative_to(workspace_root).as_posix()
        parts = Path(raw).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            return ""
        return Path(*parts).as_posix()
    except Exception:
        return ""


def _safe_attachment_ref(value: Any, *, session_id: str) -> str:
    """Return one basename scoped to the current session attachment inbox."""
    raw = str(value or "").strip()
    if not raw or not session_id:
        return ""
    try:
        path = Path(raw).expanduser()
        if path.is_absolute():
            from api.upload import _session_attachment_dir

            root = _session_attachment_dir(str(session_id)).resolve()
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                return ""
            relative = resolved.relative_to(root)
            if len(relative.parts) != 1:
                return ""
            raw = relative.name
        if "/" in raw or "\\" in raw or raw in {".", ".."}:
            return ""
        return Path(raw).name if Path(raw).name == raw else ""
    except Exception:
        return ""


def _cross_platform_basename(value: Any) -> str:
    """Return a filename without trusting the host OS path flavour."""
    raw = str(value or "").strip()
    if not raw or "\x00" in raw:
        return ""
    normalized = raw.replace("\\", "/")
    if normalized.endswith("/"):
        return ""
    name = normalized.rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."} or re.fullmatch(r"[A-Za-z]:", name):
        return ""
    return name


def public_attachment_projection(value: Any, *, session_id: str) -> dict:
    """Project one attachment for reload/retry without exposing its local path."""
    source = value if isinstance(value, dict) else {"name": value}
    projected: dict[str, Any] = {}
    for key in ("name", "filename", "mime"):
        raw = source.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        public_value = _cross_platform_basename(raw) if key in {"name", "filename"} else raw
        if not public_value:
            continue
        projected[key] = _mask_public_sensitive_text(
            public_value,
            hide_local_paths=True,
        )
    size = source.get("size")
    if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
        projected["size"] = size
    if isinstance(source.get("is_image"), bool):
        projected["is_image"] = source["is_image"]
    ref = _safe_attachment_ref(
        source.get("ref") or source.get("path") or source.get("filename") or source.get("name"),
        session_id=session_id,
    )
    masked_ref = _mask_public_sensitive_text(ref, hide_local_paths=True) if ref else ""
    if ref and masked_ref == ref:
        projected["ref"] = ref
        projected.setdefault("name", ref)
    return projected


def _public_template_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = {}
    for key in ("id", "name", "version", "description", "label", "quality_status"):
        if key in source and isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _mask_public_sensitive_text(source.get(key), hide_local_paths=True)
    return projected


def _public_action_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = {}
    for key in ("id", "type", "label", "kind", "title", "description", "status"):
        if key in source and isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _mask_public_sensitive_text(source.get(key), hide_local_paths=True)
    return projected


def _public_status_member_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = {}
    for key in ("id", "name", "role", "skill", "status", "status_label", "image"):
        if key in source and isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _mask_public_sensitive_text(source.get(key), hide_local_paths=True)
    return projected


def _public_status_artifact_projection(value: Any, *, workspace: str | None) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = {}
    for key in (
        "id", "label", "kind", "status", "download_name", "note",
        "stale_reason", "change_type", "task_id", "visible_scope",
    ):
        if key in source and isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _mask_public_sensitive_text(source.get(key), hide_local_paths=True)
    for key in ("placeholder", "exists", "openable", "current_run_owned"):
        if isinstance(source.get(key), bool):
            projected[key] = source[key]
    path = _safe_relative_ref(source.get("path"), workspace=workspace)
    if path:
        projected["path"] = path
    elif source.get("path"):
        projected["openable"] = False
        projected["exists"] = False
    return projected


def _public_status_task_projection(value: Any, *, workspace: str | None) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = {}
    for key in (
        "id", "title", "phase", "worker_id", "worker_name", "status",
        "status_label", "statusText", "description",
    ):
        if key in source and isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _mask_public_sensitive_text(source.get(key), hide_local_paths=True)
    if isinstance(source.get("artifacts"), list):
        projected["artifacts"] = [
            ref for ref in (
                _safe_relative_ref(item.get("path") if isinstance(item, dict) else item, workspace=workspace)
                for item in source["artifacts"]
            ) if ref
        ]
    return projected


def _public_status_question_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = {}
    for key in (
        "id", "title", "type", "placeholder", "answer", "status",
        "confirmationGroup", "kind", "description", "sourceTaskId",
    ):
        if key in source and isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _mask_public_sensitive_text(source.get(key), hide_local_paths=True)
    if isinstance(source.get("required"), bool):
        projected["required"] = source["required"]
    if isinstance(source.get("options"), list):
        projected["options"] = [
            _mask_public_sensitive_text(item, hide_local_paths=True)
            for item in source["options"]
            if isinstance(item, (str, int, float, bool))
        ]
    return projected


def _public_card_text(value: Any) -> Any:
    return _mask_public_sensitive_text(_public_visible_text(value), hide_local_paths=True)


def _public_card_action_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = _public_action_projection(source)
    if isinstance(source.get("disabled"), bool):
        projected["disabled"] = source["disabled"]
    if isinstance(source.get("disabledReason"), str):
        projected["disabledReason"] = _public_card_text(source["disabledReason"])
    return projected


def _public_expert_draft_identity_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected: dict[str, Any] = {}
    for key in ("stageAttempt", "artifactAttempt", "executionAttempt", "briefRevision"):
        if isinstance(source.get(key), (int, float)) and not isinstance(source.get(key), bool):
            projected[key] = source[key]
    for key in ("reviewId", "officeReviewId"):
        if isinstance(source.get(key), str):
            projected[key] = _public_card_text(source[key])
    return projected


def _public_expert_result_projection(value: Any) -> dict:
    """Project one visible expert-team result, never its storage/runtime metadata."""
    source = value if isinstance(value, dict) else {}
    projected: dict[str, Any] = {}
    for key in (
        "id", "task_id", "stage_id", "artifact_type", "title", "label", "phase",
        "status", "summary", "preview", "content", "note", "updated_at", "worker_id",
    ):
        if isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _public_card_text(source[key])
    for key in (
        "content_length", "contentLength", "revision_count", "stage_attempt",
        "blocking_count",
    ):
        if isinstance(source.get(key), (int, float)) and not isinstance(source.get(key), bool):
            projected[key] = source[key]
    for key in ("has_long_content", "hasLongContent"):
        if isinstance(source.get(key), bool):
            projected[key] = source[key]
    validation = source.get("validation")
    if isinstance(validation, dict):
        projected["validation"] = {
            key: (_public_card_text(item) if isinstance(item, str) else item)
            for key, item in validation.items()
            if key in {"status", "message", "code", "blocking_count"}
            and isinstance(item, (str, int, float, bool))
        }
    return projected


def _public_card_scalars(value: Any, fields: tuple[str, ...]) -> dict:
    """Copy an explicit scalar field set for one typed public card object."""
    source = value if isinstance(value, dict) else {}
    return {
        key: _public_card_text(source[key])
        for key in fields
        if isinstance(source.get(key), (str, int, float, bool))
    }


def _public_expert_validation_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = _public_card_scalars(source, (
        "status", "message", "code", "blocking_count", "blockingCount",
        "blocking_issue_count", "blockingIssueCount", "unresolved_warning_count",
        "unresolvedWarningCount",
    ))
    if isinstance(source.get("field_errors"), list):
        projected["field_errors"] = [
            _public_card_scalars(item, ("field", "message", "code"))
            for item in source["field_errors"] if isinstance(item, dict)
        ]
    return projected


def _public_expert_brief_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = _public_card_scalars(source, (
        "status", "revision", "originalRequest", "original_request",
        "originalRequestSummary", "original_request_summary", "originalRequestLabel",
        "exactTitle", "exact_title", "documentType", "document_type",
        "documentTypeLabel", "document_type_label", "purpose", "audience",
        "usageScenario", "usage_scenario", "additionalContext", "additional_context",
        "editable", "editPolicy", "edit_policy",
    ))
    control = source.get("documentControl") or source.get("document_control")
    if isinstance(control, dict):
        projected["documentControl"] = _public_card_scalars(control, (
            "classification", "document_version", "documentVersion", "version",
            "render_template_id", "renderTemplateId", "page_count", "pageCount",
        ))
    policy = source.get("sourcePolicySummary") or source.get("source_policy_summary")
    if isinstance(policy, dict):
        projected["sourcePolicySummary"] = _public_card_scalars(policy, (
            "mode", "label", "source_count", "sourceCount", "description",
        ))
    if isinstance(source.get("validation"), dict):
        projected["validation"] = _public_expert_validation_projection(source["validation"])
    action = source.get("viewAction") or source.get("view_action")
    if isinstance(action, dict):
        projected["viewAction"] = _public_card_action_projection(action)
    return projected


def _public_expert_gate_projection(value: Any) -> dict:
    return _public_card_scalars(value, (
        "status", "label", "reasonCode", "reason_code",
        "blockingIssueCount", "blocking_issue_count",
    ))


def _public_expert_gates_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    return {
        key: _public_expert_gate_projection(source[key])
        for key in ("content", "document", "office")
        if isinstance(source.get(key), dict)
    }


def _public_expert_office_issue_projection(value: Any) -> dict:
    return _public_card_scalars(value, (
        "issueId", "issue_id", "severity", "targetDomain", "target_domain",
        "category", "sectionId", "section_id", "blockId", "block_id",
        "logicalAssetId", "logical_asset_id", "page", "description",
        "expectedFix", "expected_fix",
    ))


def _public_expert_office_review_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = _public_card_scalars(source, (
        "reviewId", "review_id", "documentRevision", "document_revision",
        "documentSha256", "document_sha256", "canonicalSha256", "canonical_sha256",
        "status", "decision", "validity", "reviewSessionStatus",
        "review_session_status", "issueCount", "issue_count", "reviewerLabel",
        "reviewer_label",
    ))
    checklist = source.get("checklist")
    if isinstance(checklist, dict):
        checklist_fields = (
            "opened", "document_opened", "title_and_cover_match",
            "genre_and_structure_match", "content_order_correct",
            "figures_unique_and_readable", "tables_readable",
            "headers_footers_pagination", "no_placeholders_or_workflow_text",
            "citations_readable",
        )
        projected["checklist"] = _public_card_scalars(checklist, checklist_fields)
    if isinstance(source.get("issues"), list):
        projected["issues"] = [
            _public_expert_office_issue_projection(item)
            for item in source["issues"] if isinstance(item, dict)
        ]
    waived = source.get("waivedIssueIds") or source.get("waived_issue_ids")
    if isinstance(waived, list):
        projected["waivedIssueIds"] = [
            _public_card_text(item) for item in waived
            if isinstance(item, (str, int, float)) and not isinstance(item, bool)
        ]
    return projected


def _public_expert_stage_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = _public_card_scalars(source, (
        "id", "task_id", "taskId", "title", "phase", "status", "status_label",
        "statusLabel", "statusText", "description", "worker_id", "workerId",
        "worker_name", "workerName", "stage_attempt", "stageAttempt", "attempt",
    ))
    return projected


def _public_expert_pending_input_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = _public_card_scalars(source, (
        "id", "input_id", "inputId", "type", "kind", "title", "description",
        "placeholder", "status", "answer", "required",
    ))
    if isinstance(source.get("options"), list):
        projected["options"] = [
            _public_card_text(item) for item in source["options"]
            if isinstance(item, (str, int, float, bool))
        ]
    return projected


def _public_expert_timeline_event_projection(value: Any) -> dict:
    return _public_card_scalars(value, (
        "type", "title", "detail", "memberId", "member_id", "memberName",
        "member_name", "memberImage", "member_image", "at", "status",
    ))


def _public_expert_progress_projection(value: Any) -> dict:
    return _public_card_scalars(value, (
        "done", "total", "current", "current_index", "currentIndex", "is_intake",
        "isIntake", "text", "status",
    ))


def _public_expert_workspace_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = _public_card_scalars(source, ("visible", "title", "state"))
    if isinstance(source.get("currentStage"), dict):
        projected["currentStage"] = _public_expert_stage_projection(source["currentStage"])
    if isinstance(source.get("currentWorker"), dict):
        projected["currentWorker"] = _public_status_member_projection(source["currentWorker"])
    if isinstance(source.get("phases"), list):
        projected["phases"] = [
            _public_card_text(item) if isinstance(item, str)
            else _public_expert_stage_projection(item)
            for item in source["phases"] if isinstance(item, (str, dict))
        ]
    if isinstance(source.get("members"), list):
        projected["members"] = [
            _public_status_member_projection(item)
            for item in source["members"] if isinstance(item, dict)
        ]
    if isinstance(source.get("timeline"), list):
        projected["timeline"] = [
            _public_expert_timeline_event_projection(item)
            for item in source["timeline"] if isinstance(item, dict)
        ]
    if isinstance(source.get("stageResult"), dict):
        projected["stageResult"] = _public_expert_result_projection(source["stageResult"])
    if isinstance(source.get("pendingInput"), dict):
        projected["pendingInput"] = _public_expert_pending_input_projection(source["pendingInput"])
    return projected


def _public_expert_workflow_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected: dict[str, Any] = {}
    if isinstance(source.get("stages"), list):
        projected["stages"] = [
            _public_expert_stage_projection(item)
            for item in source["stages"] if isinstance(item, dict)
        ]
    if isinstance(source.get("currentStage"), dict):
        projected["currentStage"] = _public_expert_stage_projection(source["currentStage"])
    if isinstance(source.get("progress"), dict):
        projected["progress"] = _public_expert_progress_projection(source["progress"])
    return projected


def _public_expert_intake_projection(value: Any) -> dict:
    return _public_card_scalars(value, (
        "id", "status", "title", "summary", "description", "phase", "updatedAt",
        "updated_at",
    ))


def _public_expert_presentation_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected: dict[str, Any] = {}
    for key in (
        "state", "title", "statusLabel", "visibleTitle", "detail", "summary",
        "progressText", "deliveryStatus", "gateSummary", "capabilityKind",
        "capabilityLabel",
    ):
        if isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _public_card_text(source[key])
    if isinstance(source.get("primaryAction"), dict):
        projected["primaryAction"] = _public_card_action_projection(source["primaryAction"])
    if isinstance(source.get("secondaryActions"), list):
        projected["secondaryActions"] = [
            _public_card_action_projection(item)
            for item in source["secondaryActions"]
            if isinstance(item, dict)
        ]
    if isinstance(source.get("nextAction"), dict):
        projected["nextAction"] = _public_card_action_projection(source["nextAction"])
    if isinstance(source.get("result"), dict):
        projected["result"] = _public_expert_result_projection(source["result"])
    if isinstance(source.get("brief"), dict):
        projected["brief"] = _public_expert_brief_projection(source["brief"])
    if isinstance(source.get("completionGates"), dict):
        projected["completionGates"] = _public_expert_gates_projection(
            source["completionGates"]
        )
    return projected


def _public_expert_confirmation_field_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected: dict[str, Any] = {}
    for key in ("id", "name", "label", "title", "type", "placeholder", "value", "status"):
        if isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _public_card_text(source[key])
    if isinstance(source.get("required"), bool):
        projected["required"] = source["required"]
    if isinstance(source.get("options"), list):
        projected["options"] = [
            _public_card_text(item)
            for item in source["options"]
            if isinstance(item, (str, int, float, bool))
        ]
    return projected


def _public_expert_confirmation_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected: dict[str, Any] = {}
    for key in (
        "id", "type", "kind", "question_id", "questionId", "input_id", "inputId",
        "title", "description", "status", "sourceTaskId", "source_task_id",
    ):
        if isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _public_card_text(source[key])
    if isinstance(source.get("fields"), list):
        projected["fields"] = [
            _public_expert_confirmation_field_projection(item)
            for item in source["fields"]
            if isinstance(item, dict)
        ]
    return projected


def _public_expert_stage_review_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected: dict[str, Any] = {}
    for key in (
        "display_state", "displayState", "task_id", "taskId", "stage_id", "stageId",
        "review_id", "reviewId", "title", "phase", "worker_name", "workerName",
    ):
        if isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _public_card_text(source[key])
    for key in ("revision_count", "stage_attempt", "attempt"):
        if isinstance(source.get(key), (int, float)) and not isinstance(source.get(key), bool):
            projected[key] = source[key]
    for key in ("actionable", "is_final_stage", "isFinalStage"):
        if isinstance(source.get(key), bool):
            projected[key] = source[key]
    if isinstance(source.get("output"), dict):
        projected["output"] = _public_expert_result_projection(source["output"])
    return projected


def _public_expert_review_item_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected: dict[str, Any] = {}
    for key in ("id", "title", "phase", "status", "description", "note"):
        if isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _public_card_text(source[key])
    if isinstance(source.get("usedInRevision"), bool):
        projected["usedInRevision"] = source["usedInRevision"]
    return projected


def _public_status_card_projection(value: Any, *, workspace: str | None) -> dict:
    """Project the known /status and writeflow/expert-team visual card contracts."""
    source = value if isinstance(value, dict) else {}
    projected = {}
    for key in (
        "type", "kind", "title", "subtitle", "promptSummary", "sessionId",
        "runId", "sourceSessionId", "status", "statusLabel", "phase",
        "executionStatus", "executionStreamId", "currentStageId",
        "pendingInputId", "stageReviewId", "cancelRequestId", "deliveryStatus",
    ):
        if key in source and isinstance(source.get(key), (str, int, float, bool)):
            projected[key] = _mask_public_sensitive_text(source.get(key), hide_local_paths=True)
    for key in ("schemaVersion", "version"):
        if isinstance(source.get(key), int) and not isinstance(source.get(key), bool):
            projected[key] = source[key]
    for key in ("readOnly", "needsResume"):
        if isinstance(source.get(key), bool):
            projected[key] = source[key]
    if isinstance(source.get("phases"), list):
        projected["phases"] = [
            _mask_public_sensitive_text(item, hide_local_paths=True)
            for item in source["phases"] if isinstance(item, str)
        ]
    elif isinstance(source.get("phaselist"), list):
        projected["phases"] = [
            _mask_public_sensitive_text(item, hide_local_paths=True)
            for item in source["phaselist"] if isinstance(item, str)
        ]
    if isinstance(source.get("rows"), list):
        projected["rows"] = [
            {
                key: _mask_public_sensitive_text(item.get(key), hide_local_paths=True)
                for key in ("label", "value") if key in item
            }
            for item in source["rows"] if isinstance(item, dict)
        ]
    if isinstance(source.get("progress"), dict):
        projected["progress"] = {
            key: source["progress"][key]
            for key in ("done", "total")
            if isinstance(source["progress"].get(key), (int, float))
            and not isinstance(source["progress"].get(key), bool)
        }
    if isinstance(source.get("team"), dict):
        projected["team"] = {
            key: _mask_public_sensitive_text(source["team"].get(key), hide_local_paths=True)
            for key in ("id", "title", "category", "image", "status_label")
            if isinstance(source["team"].get(key), (str, int, float, bool))
        }
    for key in ("members",):
        if isinstance(source.get(key), list):
            projected[key] = [_public_status_member_projection(item) for item in source[key] if isinstance(item, dict)]
    if isinstance(source.get("tasks"), list):
        projected["tasks"] = [
            _public_status_task_projection(item, workspace=workspace)
            for item in source["tasks"] if isinstance(item, dict)
        ]
    for key in ("artifacts", "referenceArtifacts"):
        if isinstance(source.get(key), list):
            projected[key] = [
                _public_status_artifact_projection(item, workspace=workspace)
                for item in source[key] if isinstance(item, dict)
            ]
    for key in ("questions", "pendingConfirmations"):
        if isinstance(source.get(key), list):
            projected[key] = [_public_status_question_projection(item) for item in source[key] if isinstance(item, dict)]
    if isinstance(source.get("actions"), dict):
        projected["actions"] = {
            key: _public_card_action_projection(item) if isinstance(item, dict) else bool(item)
            for key, item in source["actions"].items()
            if isinstance(item, (dict, bool)) and key in {
                "primary", "secondary", "resume", "cancel", "approve", "revise",
                "can_resume", "can_cancel", "can_approve", "can_revise",
                "can_start_generation", "can_submit_stage_input", "can_retry",
                "can_approve_stage", "can_request_revision", "can_restart_stage",
            }
        }
    if isinstance(source.get("draftIdentity"), dict):
        projected["draftIdentity"] = _public_expert_draft_identity_projection(
            source["draftIdentity"]
        )
    if isinstance(source.get("presentation"), dict):
        projected["presentation"] = _public_expert_presentation_projection(
            source["presentation"]
        )
    if isinstance(source.get("primaryConfirmation"), dict):
        projected["primaryConfirmation"] = _public_expert_confirmation_projection(
            source["primaryConfirmation"]
        )
    if isinstance(source.get("pendingConfirmations"), list):
        projected["pendingConfirmations"] = [
            _public_expert_confirmation_projection(item)
            for item in source["pendingConfirmations"]
            if isinstance(item, dict)
        ]
    if isinstance(source.get("stageReview"), dict):
        projected["stageReview"] = _public_expert_stage_review_projection(
            source["stageReview"]
        )
    if isinstance(source.get("reviewItems"), list):
        projected["reviewItems"] = [
            _public_expert_review_item_projection(item)
            for item in source["reviewItems"]
            if isinstance(item, dict)
        ]
    if isinstance(source.get("stageOutputs"), list):
        projected["stageOutputs"] = [
            _public_expert_result_projection(item)
            for item in source["stageOutputs"]
            if isinstance(item, dict)
        ]
    if isinstance(source.get("brief"), dict):
        projected["brief"] = _public_expert_brief_projection(source["brief"])
    if isinstance(source.get("completionGates"), dict):
        projected["completionGates"] = _public_expert_gates_projection(
            source["completionGates"]
        )
    if isinstance(source.get("officeReview"), dict):
        projected["officeReview"] = _public_expert_office_review_projection(
            source["officeReview"]
        )
    if isinstance(source.get("nextAction"), dict):
        projected["nextAction"] = _public_card_action_projection(source["nextAction"])
    if isinstance(source.get("capability"), dict):
        projected["capability"] = _public_card_scalars(
            source["capability"], ("kind", "label")
        )
    if isinstance(source.get("artifactValidation"), dict):
        projected["artifactValidation"] = _public_expert_validation_projection(
            source["artifactValidation"]
        )
    if isinstance(source.get("workspace"), dict):
        projected["workspace"] = _public_expert_workspace_projection(source["workspace"])
    if isinstance(source.get("workflow"), dict):
        projected["workflow"] = _public_expert_workflow_projection(source["workflow"])
    if isinstance(source.get("pendingInput"), dict):
        projected["pendingInput"] = _public_expert_pending_input_projection(
            source["pendingInput"]
        )
    if isinstance(source.get("stageResult"), dict):
        projected["stageResult"] = _public_expert_result_projection(source["stageResult"])
    if isinstance(source.get("intake"), dict):
        projected["intake"] = _public_expert_intake_projection(source["intake"])
    if isinstance(source.get("timelineEvents"), list):
        projected["timelineEvents"] = [
            _public_expert_timeline_event_projection(item)
            for item in source["timelineEvents"] if isinstance(item, dict)
        ]
    return projected


def _public_docx_template_selection_projection(
    value: Any, *, workspace: str | None, session_id: str
) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = {}
    if isinstance(source.get("code"), str):
        projected["code"] = _mask_public_sensitive_text(source["code"], hide_local_paths=True)
    if isinstance(source.get("templates"), list):
        projected["templates"] = [_public_template_projection(item) for item in source["templates"] if isinstance(item, dict)]
    if isinstance(source.get("examples"), list):
        projected["examples"] = [_mask_public_sensitive_text(item, hide_local_paths=True) for item in source["examples"] if isinstance(item, str)]
    raw_source = source.get("source_path") or source.get("sourcePath")
    source_ref = _safe_relative_ref(raw_source, workspace=workspace) or _safe_attachment_ref(
        raw_source, session_id=session_id
    )
    if source_ref:
        projected["source_path"] = source_ref
    return projected


def _public_docx_template_delivery_projection(value: Any, *, workspace: str | None) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = {}
    for key in ("template_id", "quality_status"):
        if isinstance(source.get(key), str):
            projected[key] = _mask_public_sensitive_text(source[key], hide_local_paths=True)
    if isinstance(source.get("template"), dict):
        projected["template"] = _public_template_projection(source["template"])
    for key in ("document_path", "delivery_dir", "quality_report_path"):
        ref = _safe_relative_ref(source.get(key), workspace=workspace)
        if ref:
            projected[key] = ref
    return projected


def _public_docx_template_context_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = {}
    if isinstance(source.get("template_id"), str):
        projected["template_id"] = _mask_public_sensitive_text(source["template_id"], hide_local_paths=True)
    if isinstance(source.get("template"), dict):
        projected["template"] = _public_template_projection(source["template"])
    if isinstance(source.get("templates"), list):
        projected["templates"] = [_public_template_projection(item) for item in source["templates"] if isinstance(item, dict)]
    return projected


def _public_docx_figure_adjustment_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = {}
    if isinstance(source.get("code"), str):
        projected["code"] = _mask_public_sensitive_text(source["code"], hide_local_paths=True)
    if isinstance(source.get("actions"), list):
        projected["actions"] = [_public_action_projection(item) for item in source["actions"] if isinstance(item, dict)]
    if isinstance(source.get("examples"), list):
        projected["examples"] = [_mask_public_sensitive_text(item, hide_local_paths=True) for item in source["examples"] if isinstance(item, str)]
    return projected


def _public_vision_recovery_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    return {
        key: _mask_public_sensitive_text(source.get(key), hide_local_paths=True)
        for key in ("id", "type") if isinstance(source.get(key), str)
    }


def _public_message_content(value: Any, *, assistant_visible: bool = False) -> Any:
    """Keep visible text and public tool lifecycle blocks from structured content."""
    if isinstance(value, str):
        visible = _public_visible_text(value) if assistant_visible else value
        return _mask_public_sensitive_text(visible, hide_local_paths=assistant_visible)
    if not isinstance(value, list):
        return "" if value is None else _mask_public_sensitive_text(_public_visible_text(value))
    projected = []
    for part in value:
        if isinstance(part, str):
            visible = _public_visible_text(part) if assistant_visible else part
            projected.append(_mask_public_sensitive_text(
                visible, hide_local_paths=assistant_visible
            ))
            continue
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "").strip()
        if part_type in {"text", "input_text", "output_text"}:
            projected.append({
                "type": part_type,
                "text": _mask_public_sensitive_text(
                    _public_visible_text(part.get("text") or "")
                    if assistant_visible else part.get("text") or "",
                    hide_local_paths=assistant_visible,
                ),
            })
        elif part_type == "tool_use":
            projected.append({
                "type": "tool_use",
                **_public_tool_projection(part, event_name="tool"),
            })
        elif part_type == "tool_result":
            result = {"type": "tool_result"}
            if part.get("tool_use_id"):
                result["tool_use_id"] = str(part.get("tool_use_id"))
            if part.get("status"):
                result["status"] = _mask_public_sensitive_text(part.get("status"), hide_local_paths=True)
            if isinstance(part.get("summary"), str) and part.get("summary"):
                result["summary"] = _mask_public_sensitive_text(
                    _public_visible_text(part.get("summary")), hide_local_paths=True
                )
            projected.append(result)
    return projected


def public_message_projection(
    message: Any,
    *,
    workspace: str | None = None,
    session_id: str = "",
) -> dict:
    """Return the explicit public transcript contract for one message."""
    if not isinstance(message, dict):
        return {}
    role = str(message.get("role") or "").strip().lower()
    if role == "tool":
        projected = {"role": "tool", "event_type": "tool.completed"}
        tool = _public_tool_projection(message, event_name="tool_complete")
        for key in ("name", "status", "summary", "is_error", "tid", "done", "duration"):
            if key in tool:
                projected[key] = tool[key]
        if message.get("tool_call_id"):
            projected["tool_call_id"] = str(message.get("tool_call_id"))
        elif message.get("tool_use_id"):
            projected["tool_use_id"] = str(message.get("tool_use_id"))
        for key in ("timestamp", "_ts", "assistant_msg_idx"):
            if key in message:
                projected[key] = copy.deepcopy(message.get(key))
        return _mask_public_sensitive_text(projected, hide_local_paths=True)

    projected: dict[str, Any] = {}
    for key in _PUBLIC_MESSAGE_SCALAR_FIELDS:
        if key not in message:
            continue
        if key == "content":
            projected[key] = _public_message_content(
                message.get(key), assistant_visible=role != "user"
            )
        elif key in {
            "reasoning", "reasoning_content", "thinking", "provider_details",
            "provider_details_label", "text", "summary",
        }:
            projected[key] = _mask_public_sensitive_text(
                _public_visible_text(message.get(key)), hide_local_paths=True
            )
        else:
            projected[key] = copy.deepcopy(message.get(key))
    if role and "role" not in projected:
        projected["role"] = role
    if isinstance(message.get("tool_calls"), list):
        projected["tool_calls"] = [
            _public_tool_projection(item, event_name="tool")
            for item in message["tool_calls"]
            if isinstance(item, dict)
        ]
    if isinstance(message.get("attachments"), list):
        projected["attachments"] = [
            item for item in (
                public_attachment_projection(raw, session_id=session_id)
                for raw in message["attachments"]
            ) if item
        ]
    if isinstance(message.get("artifacts"), list):
        try:
            from api.artifacts import public_artifact_projection

            projected["artifacts"] = [
                public
                for public in (
                    public_artifact_projection(raw) for raw in message["artifacts"]
                )
                if public
            ]
        except Exception:
            projected["artifacts"] = []
    if isinstance(message.get("artifact_errors"), list):
        projected["artifact_errors"] = [
            "generated image could not be persisted"
            for _item in message["artifact_errors"][:10]
        ]
    if "_statusCard" in message:
        projected["_statusCard"] = _public_status_card_projection(
            message.get("_statusCard"), workspace=workspace
        )
    if "vision_recovery" in message:
        projected["vision_recovery"] = _public_vision_recovery_projection(
            message.get("vision_recovery")
        )
    if "docx_template_selection" in message:
        projected["docx_template_selection"] = _public_docx_template_selection_projection(
            message.get("docx_template_selection"),
            workspace=workspace,
            session_id=session_id,
        )
    if "docx_template_delivery" in message:
        projected["docx_template_delivery"] = _public_docx_template_delivery_projection(
            message.get("docx_template_delivery"), workspace=workspace
        )
    if "docx_source_request" in message:
        projected["docx_source_request"] = _public_docx_template_context_projection(
            message.get("docx_source_request")
        )
    if "docx_engine_workbench" in message:
        projected["docx_engine_workbench"] = _public_docx_template_context_projection(
            message.get("docx_engine_workbench")
        )
    if "docx_figure_adjustment" in message:
        projected["docx_figure_adjustment"] = _public_docx_figure_adjustment_projection(
            message.get("docx_figure_adjustment")
        )
    if "recovery_control" in message:
        projected["recovery_control"] = bool(message.get("recovery_control"))
    if isinstance(message.get("_turnUsage"), dict):
        projected["_turnUsage"] = _public_usage_projection(message.get("_turnUsage"))
    if isinstance(message.get("_gatewayRouting"), dict):
        projected["_gatewayRouting"] = _public_metadata_projection(message.get("_gatewayRouting"))
    return _mask_public_sensitive_text(projected)


def scrub_public_message(message: Any) -> Any:
    """Backward-compatible name for the strict public message projection."""
    return public_message_projection(message)


def sanitize_persisted_assistant_message(message: Any) -> Any:
    """Validate user-visible assistant/tool fields before session persistence.

    The returned copy preserves internal tool-call arguments and all unrelated
    metadata verbatim. Callers must apply it to the display transcript only,
    never to provider/context history.
    """
    if not isinstance(message, dict):
        return copy.deepcopy(message)
    role = str(message.get("role") or "").lower()
    if role == "tool":
        return public_message_projection(message)
    if role != "assistant":
        return copy.deepcopy(message)
    cleaned = copy.deepcopy(message)
    if "content" in cleaned:
        cleaned["content"] = _public_message_content(
            cleaned.get("content"), assistant_visible=True
        )
    for key in ("reasoning", "reasoning_content"):
        if key in cleaned:
            cleaned[key] = _mask_public_sensitive_text(
                _public_visible_text(cleaned.get(key)), hide_local_paths=True
            )
    if isinstance(cleaned.get("tool_calls"), list):
        cleaned["tool_calls"] = [
            _public_tool_projection(item, event_name="tool")
            for item in cleaned["tool_calls"] if isinstance(item, dict)
        ]
    return cleaned


def _public_usage_projection(value: Any) -> dict:
    source = value if isinstance(value, dict) else {}
    projected = {
        key: copy.deepcopy(source.get(key))
        for key in _PUBLIC_USAGE_FIELDS
        if key in source
    }
    if isinstance(source.get("gateway_routing"), dict):
        projected["gateway_routing"] = _public_metadata_projection(source.get("gateway_routing"))
    return _mask_public_sensitive_text(projected, hide_local_paths=True)


def public_profile_projection(value: Any) -> dict:
    """Return the explicit browser contract for one profile summary.

    Profile discovery is an internal filesystem operation.  Its public result
    is deliberately a typed allowlist so paths, config fragments, runtime
    metadata, and future internal fields cannot reach the browser by default.
    """
    source = value if isinstance(value, dict) else {}
    projected: dict[str, Any] = {}
    for key in ("name", "display_name", "label", "model", "provider"):
        if isinstance(source.get(key), str):
            projected[key] = _mask_public_sensitive_text(
                source[key], hide_local_paths=True
            )
    for key in (
        "is_default", "is_active", "gateway_running", "has_env",
    ):
        if isinstance(source.get(key), bool):
            projected[key] = source[key]
    for key in ("skill_count", "enabled_skills", "total_skills"):
        if isinstance(source.get(key), int) and not isinstance(source.get(key), bool):
            projected[key] = source[key]
    return projected


def public_session_projection(payload: Any) -> dict:
    """Return the explicit browser session contract used by GET, sync, and done."""
    if not isinstance(payload, dict):
        return {}
    cleaned: dict[str, Any] = {}
    is_worktree = bool(payload.get("is_worktree") or payload.get("worktree_path"))
    cleaned["is_worktree"] = is_worktree
    for key in _PUBLIC_SESSION_FIELDS:
        if key not in payload:
            continue
        value = payload.get(key)
        if key == "workspace":
            # A user-selected external workspace remains part of the existing
            # contract for ordinary sessions.  A worktree session's workspace
            # is the generated worktree path, so its public identity must come
            # only from the path-free worktree fields below.
            if not is_worktree and value and not is_internal_workspace(value):
                cleaned[key] = str(value)
            continue
        if key in {
            "title", "display_title", "writeflow_title", "project_name",
            "pending_user_message", "compression_anchor_summary",
        }:
            cleaned[key] = _mask_public_sensitive_text(_public_visible_text(value))
        elif key == "enabled_toolsets":
            cleaned[key] = [str(item) for item in value] if isinstance(value, list) else []
        else:
            cleaned[key] = copy.deepcopy(value)
    if is_worktree:
        label = _public_worktree_label(
            payload.get("worktree_label") or payload.get("worktree_branch")
        )
        cleaned["worktree_branch"] = label
        cleaned["worktree_label"] = label
    session_id = str(payload.get("session_id") or "")
    workspace = str(payload.get("workspace") or "") or None
    if isinstance(payload.get("messages"), list):
        cleaned["messages"] = [
            public_message_projection(item, workspace=workspace, session_id=session_id)
            for item in payload["messages"]
            if isinstance(item, dict)
        ]
    if isinstance(payload.get("pending_attachments"), list):
        cleaned["pending_attachments"] = [
            item for item in (
                public_attachment_projection(raw, session_id=session_id)
                for raw in payload["pending_attachments"]
            ) if item
        ]
    if isinstance(payload.get("tool_calls"), list):
        cleaned["tool_calls"] = [
            _public_tool_projection(call, event_name="tool")
            for call in payload["tool_calls"]
            if isinstance(call, dict)
        ]
    if isinstance(payload.get("composer_draft"), dict):
        draft_text = payload["composer_draft"].get("text")
        cleaned["composer_draft"] = (
            {"text": _mask_public_sensitive_text(draft_text)}
            if isinstance(draft_text, str)
            else {}
        )
    for key in (
        "compression_anchor_message_key", "compression_anchor_details",
        "context_engine_state", "gateway_routing", "runtime_journal",
    ):
        if isinstance(payload.get(key), dict):
            cleaned[key] = _public_metadata_projection(payload.get(key))
    if isinstance(payload.get("gateway_routing_history"), list):
        cleaned["gateway_routing_history"] = _public_metadata_projection(
            payload.get("gateway_routing_history")
        )
    attention = payload.get("attention")
    if isinstance(attention, dict):
        public_attention: dict[str, Any] = {}
        for key in ("kind", "severity"):
            if isinstance(attention.get(key), str):
                public_attention[key] = _mask_public_sensitive_text(attention[key])
        count = attention.get("count")
        if isinstance(count, int) and not isinstance(count, bool):
            public_attention["count"] = max(0, count)
        if public_attention:
            cleaned["attention"] = public_attention
    return _mask_public_sensitive_text(cleaned)


def _public_worktree_label(value: Any) -> str:
    """Return one short display label, never a filesystem path or internal prefix."""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return "Worktree"
    label = raw.rsplit("/", 1)[-1].strip()
    label = re.sub(r"(?i)^hermes(?:[-_ ]+|$)", "", label).strip("-_ ")
    if not label or label in {".", ".."} or ":" in label:
        return "Worktree"
    label = re.sub(r"[\x00-\x1f\x7f]+", "", label).strip()
    label = str(_mask_public_sensitive_text(label, hide_local_paths=True))[:80].strip()
    return label or "Worktree"


def public_worktree_status_projection(payload: Any, *, label: Any = None) -> dict:
    """Project worktree status without paths, repo roots, or upstream names."""
    source = payload if isinstance(payload, dict) else {}
    projected: dict[str, Any] = {
        "label": _public_worktree_label(label or source.get("label")),
    }
    for key in ("exists", "dirty", "locked_by_stream", "locked_by_terminal", "listed"):
        projected[key] = bool(source.get(key))
    count = source.get("untracked_count")
    projected["untracked_count"] = (
        max(0, count) if isinstance(count, int) and not isinstance(count, bool) else 0
    )
    ahead_behind = source.get("ahead_behind") if isinstance(source.get("ahead_behind"), dict) else {}
    projected["ahead_behind"] = {
        "ahead": max(0, ahead_behind.get("ahead", 0))
        if isinstance(ahead_behind.get("ahead", 0), int)
        and not isinstance(ahead_behind.get("ahead", 0), bool)
        else 0,
        "behind": max(0, ahead_behind.get("behind", 0))
        if isinstance(ahead_behind.get("behind", 0), int)
        and not isinstance(ahead_behind.get("behind", 0), bool)
        else 0,
        "available": bool(ahead_behind.get("available")),
    }
    return projected


def public_worktree_remove_projection(payload: Any) -> dict:
    """Project worktree removal onto its path-free browser result contract."""
    source = payload if isinstance(payload, dict) else {}
    warnings = source.get("warnings") if isinstance(source.get("warnings"), list) else []
    return {
        "ok": bool(source.get("ok")),
        "removed": bool(source.get("removed", source.get("ok"))),
        "warnings": [
            str(_mask_public_sensitive_text(item, hide_local_paths=True))
            for item in warnings
            if isinstance(item, str) and item.strip()
        ],
    }


def public_session_status_projection(payload: Any) -> dict:
    """Return session status without runtime-home or profile-home paths."""
    source = payload if isinstance(payload, dict) else {}
    is_worktree = bool(source.get("is_worktree") or source.get("worktree_path"))
    projected = {
        key: copy.deepcopy(source.get(key))
        for key in (
            "session_id", "title", "model", "profile", "message_count",
            "created_at", "updated_at", "agent_running", "input_tokens",
            "output_tokens", "total_tokens", "estimated_cost",
        )
        if key in source
    }
    workspace = source.get("workspace")
    if workspace and not is_worktree and not is_internal_workspace(workspace):
        projected["workspace"] = str(workspace)
    if is_worktree:
        projected["is_worktree"] = True
        projected["worktree_label"] = _public_worktree_label(
            source.get("worktree_label") or source.get("worktree_branch")
        )
    return _mask_public_sensitive_text(projected)


def public_session_search_projection(item: Any) -> dict:
    """Return a strict sidebar-search row with only its two search fields added."""
    source = item if isinstance(item, dict) else {}
    cleaned = public_session_projection(source)
    # Sidebar search rows never reconnect a live turn and therefore do not
    # carry attachment reload state.
    cleaned.pop("pending_attachments", None)
    if isinstance(source.get("match_type"), str):
        cleaned["match_type"] = _mask_public_sensitive_text(
            _public_visible_text(source.get("match_type")), hide_local_paths=True
        )
    if isinstance(source.get("match_preview"), str):
        cleaned["match_preview"] = _mask_public_sensitive_text(
            _public_visible_text(source.get("match_preview")), hide_local_paths=True
        )
    return cleaned


def scrub_public_session_payload(payload: Any) -> Any:
    """Backward-compatible name for the strict public session projection."""
    return public_session_projection(payload)


def public_approval_projection(payload: Any) -> dict:
    """Return the UI approval card contract without exposing raw commands."""
    source = payload if isinstance(payload, dict) else {}
    cleaned: dict[str, Any] = {}
    for key in (
        "approval_id", "id", "approval_type", "kind", "capability", "name",
        "status", "title", "description", "pattern_key", "pattern_keys", "choices",
    ):
        if key in source:
            cleaned[key] = _public_metadata_projection(source.get(key))
    summary = str(source.get("summary") or source.get("description") or "").strip()
    if not summary:
        summary = "受限操作需要你的授权"
    cleaned["summary"] = _mask_public_sensitive_text(
        _public_visible_text(summary), hide_local_paths=True
    )
    return cleaned


def public_event_projection(payload: Any, *, event_name: str | None = None) -> dict:
    """Project one SSE/journal event onto its explicit public wire contract."""
    event = str(event_name or "").strip()
    source = payload if isinstance(payload, dict) else {}
    if event in _PUBLIC_TOOL_EVENTS:
        return _mask_public_sensitive_text(
            _public_tool_projection(source, event_name=event), hide_local_paths=True
        )
    if event == "approval":
        return public_approval_projection(source)
    if event == "done":
        cleaned: dict[str, Any] = {}
        if isinstance(source.get("session"), dict):
            cleaned["session"] = public_session_projection(source.get("session"))
        if isinstance(source.get("usage"), dict):
            cleaned["usage"] = _public_usage_projection(source.get("usage"))
        for key in ("ephemeral", "brand_privacy", "license_blocked"):
            if key in source:
                cleaned[key] = bool(source.get(key))
        for key in ("answer", "message", "status", "title"):
            if key in source:
                cleaned[key] = _mask_public_sensitive_text(_public_visible_text(source.get(key)))
        return cleaned

    fields_by_event = {
        "submitted": (
            "source", "turn_id", "idempotency_key", "expert_team_run_id",
            "stage_id", "attempt", "execution_start_id",
        ),
        "token": ("text",),
        "reasoning": ("text",),
        "interim_assistant": ("text", "already_streamed"),
        "pending_steer_leftover": ("session_id", "text"),
        "stream_end": ("session_id",),
        "cancel": ("message", "type"),
        "warning": ("message", "type"),
        "apperror": ("message", "type", "hint", "details", "recovery_control"),
        "title": ("session_id", "title"),
        "title_status": ("session_id", "title", "status", "reason"),
        "compressing": ("session_id", "message"),
        "compressed": (
            "session_id", "old_session_id", "new_session_id",
            "continuation_session_id", "message", "engine", "mode",
        ),
        "goal": ("session_id", "state", "message", "message_key", "message_args"),
        "goal_continue": (
            "session_id", "continuation_prompt", "text", "message",
            "message_key", "message_args",
        ),
        "clarify": (
            "clarification_id", "question", "description", "choices",
            "choices_offered", "expires_at", "timeout_seconds",
        ),
    }
    if event == "metering":
        return _public_usage_projection(source)
    cleaned = {}
    for key in fields_by_event.get(event, ("session_id", "message", "text", "type", "status")):
        if key not in source:
            continue
        value = source.get(key)
        if isinstance(value, (dict, list, tuple)):
            cleaned[key] = _public_metadata_projection(value)
        elif isinstance(value, str):
            cleaned[key] = _mask_public_sensitive_text(_public_visible_text(value), hide_local_paths=True)
        else:
            cleaned[key] = copy.deepcopy(value)
    if event in {"compressed", "context_status"}:
        for key in ("usage", "details", "prefill"):
            if key not in source:
                continue
            cleaned[key] = (
                _public_usage_projection(source.get(key))
                if key == "usage"
                else _public_metadata_projection(source.get(key))
            )
    return _mask_public_sensitive_text(cleaned, hide_local_paths=True)


def public_turn_journal_event_projection(payload: Any, *, session_id: str) -> dict:
    """Project one durable turn lifecycle event onto its safe journal schema.

    The turn journal participates in recovery and migration, but it is also a
    replayable diagnostic surface.  Keep only lifecycle identity and typed
    recovery fields; never persist arbitrary metadata, workspace paths, raw
    attachment paths, credentials, or provider/tool payloads.
    """
    source = payload if isinstance(payload, dict) else {}
    cleaned: dict[str, Any] = {}
    text_fields = (
        "event", "session_id", "turn_id", "stream_id", "role", "reason",
        "model", "model_provider", "expert_team_run_id", "stage_id",
        "execution_start_id", "assistant_content_sha256", "user_content_sha256",
    )
    scalar_fields = (
        "version", "created_at", "assistant_message_index", "user_message_index",
        "attempt", "duration",
    )
    for key in text_fields:
        value = source.get(key)
        if isinstance(value, str) and value:
            cleaned[key] = _mask_public_sensitive_text(value, hide_local_paths=True)
    for key in scalar_fields:
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cleaned[key] = copy.deepcopy(value)
    if isinstance(source.get("content"), str):
        # User input remains the user's wording; this layer masks credentials
        # and local paths but deliberately does not apply brand replacement.
        cleaned["content"] = _mask_public_sensitive_text(
            source["content"], hide_local_paths=True
        )
    attachments = source.get("attachments")
    if isinstance(attachments, list):
        cleaned["attachments"] = [
            projected
            for raw in attachments
            if isinstance(raw, (dict, str))
            for projected in [public_attachment_projection(raw, session_id=session_id)]
            if projected
        ]
    return _mask_public_sensitive_text(cleaned, hide_local_paths=True)


def public_response_projection(payload: Any, *, surface: str) -> dict:
    """Project chat-sync and session-import envelopes onto their public contracts."""
    source = payload if isinstance(payload, dict) else {}
    cleaned: dict[str, Any] = {}
    if isinstance(source.get("session"), dict):
        cleaned["session"] = public_session_projection(source.get("session"))
    for key in ("ok", "imported"):
        if key in source:
            cleaned[key] = bool(source.get(key))
    for key in ("answer", "status", "message"):
        if key in source:
            cleaned[key] = _mask_public_sensitive_text(_public_visible_text(source.get(key)))
    return cleaned


def public_egress_scrub(
    payload: Any,
    *,
    surface: str = "generic",
    event_name: str | None = None,
) -> Any:
    """Scrub public response payloads with whole-message replacement.

    Unlike ``scrub_brand_leaks()``, this function is an egress gate: if a
    user-visible assistant/tool field contains a forbidden implementation or
    runtime detail, the entire visible field is replaced with one coherent
    safe reply rather than doing partial string substitutions.
    """
    if event_name:
        return public_event_projection(payload, event_name=event_name)
    if isinstance(payload, str):
        return _mask_public_sensitive_text(_public_visible_text(payload))
    if isinstance(payload, list):
        return [public_egress_scrub(item, surface=surface) for item in payload]
    if not isinstance(payload, dict):
        return copy.deepcopy(payload)

    cleaned = copy.deepcopy(payload)
    role = str(cleaned.get("role") or "").strip().lower()
    if role:
        return public_message_projection(cleaned)

    if "title" in cleaned:
        cleaned["title"] = _public_visible_text(cleaned.get("title"))
    if isinstance(cleaned.get("messages"), list):
        cleaned["messages"] = [
            public_message_projection(item)
            for item in cleaned["messages"]
            if isinstance(item, dict)
        ]
    if isinstance(cleaned.get("context_messages"), list):
        cleaned["context_messages"] = [
            public_egress_scrub(item, surface=surface)
            for item in cleaned["context_messages"]
        ]
    if isinstance(cleaned.get("tool_calls"), list):
        next_tool_calls = []
        for call in cleaned["tool_calls"]:
            if not isinstance(call, dict):
                next_tool_calls.append(copy.deepcopy(call))
                continue
            next_tool_calls.append(_public_tool_projection(call, event_name="tool"))
        cleaned["tool_calls"] = next_tool_calls
    if isinstance(cleaned.get("session"), dict):
        cleaned["session"] = public_session_projection(cleaned.get("session"))
    for key in ("result", "payload", "data", "license", "diagnostics", "health"):
        if key in cleaned:
            cleaned[key] = public_egress_scrub(cleaned.get(key), surface=surface)
    for key in (
        "answer",
        "content",
        "error",
        "message",
        "warning",
        "details",
        "snippet",
        "preview",
        "text",
        "stdout",
        "stderr",
        "log",
        "logs",
    ):
        if key in cleaned:
            cleaned[key] = _public_visible_text(cleaned.get(key))
    return _mask_public_sensitive_text(cleaned)


def scrub_public_export_payload(payload: Any) -> Any:
    """Build the explicit, user-portable public session export contract."""
    if not isinstance(payload, dict):
        return {"export_schema_version": 1, "messages": []}
    allowed_fields = (
        "session_id", "title", "model", "model_provider", "created_at",
        "updated_at", "pinned", "archived", "project_id", "profile",
        "personality",
    )
    cleaned = {"export_schema_version": 1}
    for key in allowed_fields:
        if key in payload:
            cleaned[key] = copy.deepcopy(payload.get(key))

    public_message_fields = (
        "role", "content", "timestamp", "_ts", "type", "message_id", "id",
        "name", "duration_seconds", "_error", "error_type", "is_error",
        "reasoning", "reasoning_content", "provider_details",
        "provider_details_label", "preview", "snippet", "text",
    )
    exported_messages = []
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        projected = scrub_public_message(message)
        public_message = {
            key: copy.deepcopy(projected.get(key))
            for key in public_message_fields
            if key in projected
        }
        if isinstance(projected.get("tool_calls"), list):
            public_message["tool_calls"] = copy.deepcopy(projected["tool_calls"])
        exported_messages.append(public_message)
    cleaned["messages"] = exported_messages
    if isinstance(payload.get("tool_calls"), list):
        cleaned["tool_calls"] = [
            _public_tool_projection(call, event_name="tool")
            for call in payload["tool_calls"]
            if isinstance(call, dict)
        ]
    return _mask_public_sensitive_text(
        public_egress_scrub(cleaned, surface="export"),
        hide_local_paths=True,
    )


def scrub_messages(messages: Any) -> Any:
    """Return a scrubbed deep copy of session messages/history."""
    if isinstance(messages, list):
        return [scrub_public_message(item) for item in messages]
    return scrub_public_message(messages)


def scrub_local_service_access(value: Any) -> Any:
    """Scrub local service URLs, ports, and direct browser access hints."""
    if isinstance(value, str):
        return _scrub_local_service_access_text(value)
    if isinstance(value, list):
        return [scrub_local_service_access(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_local_service_access(item) for item in value)
    if isinstance(value, dict):
        return {key: scrub_local_service_access(item) for key, item in value.items()}
    return value



def is_internal_workspace(path: str | Path | None) -> bool:
    """Return True for source/runtime directories that ordinary chat should not inspect."""
    if not path:
        return False
    try:
        candidate = Path(str(path)).expanduser().resolve(strict=False)
    except Exception:
        candidate = Path(str(path)).expanduser()
    internal_roots: list[Path] = []
    for env_name in ("TAIJI_RUNTIME_HOME", "HERMES_WEBUI_STATE_DIR", "HERMES_HOME"):
        configured_root = os.environ.get(env_name)
        if not configured_root:
            continue
        try:
            internal_root = Path(configured_root).expanduser().resolve(strict=False)
        except Exception:
            internal_root = Path(configured_root).expanduser()
        internal_roots.append(internal_root)
        if candidate == internal_root:
            return True
    configured_workspace = os.environ.get("HERMES_WEBUI_DEFAULT_WORKSPACE")
    if configured_workspace:
        try:
            default_workspace = Path(configured_workspace).expanduser().resolve(strict=False)
        except Exception:
            default_workspace = Path(configured_workspace).expanduser()
        if candidate == default_workspace and any(
            default_workspace == root or root in default_workspace.parents
            for root in internal_roots
        ):
            return True
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


def scrub_streaming_token_delta(delta: str, tail_ref: list[Any], *, final: bool = False) -> str:
    """Scrub streams consistently across arbitrary provider chunk sizes.

    The look-ahead window retains raw text. Re-feeding an already scrubbed and
    often shorter suffix loses its original boundary, causing repeated
    replacements, omissions, or unsafe fragments.
    """
    current = tail_ref[0] if tail_ref else ""
    if isinstance(current, dict):
        pending = str(current.get("pending") or "")
        last_replacement = str(current.get("last_replacement") or "")
        credential_active = bool(current.get("credential_active"))
        credential_quote = str(current.get("credential_quote") or "")
        credential_value_started = bool(current.get("credential_value_started"))
        credential_escape = bool(current.get("credential_escape"))
    else:
        pending = str(current or "")
        last_replacement = ""
        credential_active = False
        credential_quote = ""
        credential_value_started = False
        credential_escape = False

    emitted: list[str] = []
    for char in str(delta or ""):
        if credential_active:
            if not credential_value_started:
                if char.isspace():
                    continue
                if char in {'"', "'"}:
                    credential_quote = char
                    credential_value_started = True
                    credential_escape = False
                    continue
                credential_value_started = True
            if credential_quote:
                if credential_escape:
                    credential_escape = False
                    continue
                if char == "\\":
                    credential_escape = True
                    continue
                if char == credential_quote:
                    credential_active = False
                    credential_quote = ""
                    credential_value_started = False
                    credential_escape = False
                continue
            if _STREAM_CREDENTIAL_VALUE_CHAR_RE.fullmatch(char):
                continue
            credential_active = False
            credential_value_started = False

        pending += char
        credential_match = None
        for pattern in _STREAM_CREDENTIAL_START_PATTERNS:
            match = pattern.search(pending)
            if match is not None and (
                credential_match is None or match.start() < credential_match.start()
            ):
                credential_match = match
        if credential_match is not None:
            safe_prefix = str(
                _mask_public_sensitive_text(
                    scrub_brand_leaks(pending[:credential_match.start()]),
                    hide_local_paths=True,
                )
            )
            if safe_prefix and safe_prefix != last_replacement:
                emitted.append(safe_prefix)
            emitted.append(_STREAM_CREDENTIAL_MASK)
            pending = ""
            last_replacement = ""
            credential_active = True
            credential_quote = ""
            credential_value_started = False
            credential_escape = False
            continue
        if len(pending) <= _BRAND_STREAM_HOLD_CHARS:
            continue
        cleaned = str(
            _mask_public_sensitive_text(
                scrub_brand_leaks(pending),
                hide_local_paths=True,
            )
        )
        if cleaned != pending:
            if cleaned != last_replacement:
                emitted.append(cleaned)
            last_replacement = cleaned
            pending = ""
            continue
        emitted.append(pending[0])
        last_replacement = ""
        pending = pending[1:]

    if final:
        if pending:
            cleaned = str(
                _mask_public_sensitive_text(
                    scrub_brand_leaks(pending),
                    hide_local_paths=True,
                )
            )
            if cleaned != last_replacement:
                emitted.append(cleaned)
        if tail_ref:
            tail_ref[0] = ""
        return "".join(emitted)

    if tail_ref:
        tail_ref[0] = {
            "pending": pending,
            "last_replacement": last_replacement,
            "credential_active": credential_active,
            "credential_quote": credential_quote,
            "credential_value_started": credential_value_started,
            "credential_escape": credential_escape,
        }
    return "".join(emitted)


def _scrub_text(text: str) -> str:
    result = str(text or "")
    if _is_external_hermes_topic(result):
        return result
    if _LOCAL_SERVICE_ACCESS_RE.search(result):
        return _LOCAL_SERVICE_SAFE_TEXT
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
    if _LOCAL_SERVICE_ACCESS_RE.search(value):
        return True
    if _SEMANTIC_BRAND_LEAK_RE.search(value):
        return True
    return bool(_FORBIDDEN_OUTPUT_DETAIL_RE.search(value))


def _scrub_local_service_access_text(text: str) -> str:
    value = str(text or "")
    if not _LOCAL_SERVICE_ACCESS_RE.search(value):
        return value
    return _LOCAL_SERVICE_SAFE_TEXT
