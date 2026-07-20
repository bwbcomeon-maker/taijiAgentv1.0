"""Pure, conservative intent classification for image generation requests."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence


class ImageIntentAction(str, Enum):
    GENERATE = "generate"
    CLARIFY = "clarify"
    PASS_THROUGH = "pass_through"


@dataclass(frozen=True)
class ImageIntentClarification:
    question: str
    choices: tuple[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "choices": list(self.choices),
        }


@dataclass(frozen=True)
class ImageIntentDecision:
    action: ImageIntentAction
    reason_code: str
    prompt: str | None = None
    clarification: ImageIntentClarification | None = None


_REPEAT_REQUEST_RE = re.compile(
    r"^\s*(?:"
    r"再(?:来|生成|做|画|出)(?:一张|一个|一幅|一版)?"
    r"|another\s+one"
    r"|one\s+more(?:\s+(?:image|picture|photo|version))?"
    r")\s*(?:[,，]\s*please)?\s*[。.!！?？]?\s*$",
    re.IGNORECASE,
)
_CHINESE_VISUAL_NOUN = (
    r"(?:图片|图像|照片|海报|插画|配图|头像|封面|视觉稿|效果图|"
    r"示意图|流程图|架构图|思维导图|图标|标志|logo)"
)
_CHINESE_NON_VISUAL_OBJECT = (
    r"(?:报告|汇报|表格|图表|Excel|文档|文件|PPT|幻灯片|清单|摘要|"
    r"代码|诗歌|绝句|文章|文案|方案|演示文稿|演示|对比分析|重点|"
    r"测试用例|合同|协议|大饼|测试数据|随机密码|SQL查询|压缩包|UUID|"
    r"上传组件|组件|上传器|分类器|接口|服务|流水线|教程|信件|"
    r"求职信|系统|插件|代码库|策略|政策|SDK|API)"
)
_CHINESE_GENERATION_VERB_RE = re.compile(
    r"(?:生成|绘制|创作|制作|设计|画|做|弄|开发|实现|编写|创建|搭建)",
    re.IGNORECASE,
)
_CHINESE_GENERATE_RE = re.compile(
    r"(?:"
    r"(?:帮我|给我|请|麻烦)?"
    r"(?:生成|绘制|创作|制作)"
    r"(?:[^,，;；。.!！？?\n]{0,24})"
    + _CHINESE_VISUAL_NOUN
    + r"|(?:帮我|给我|请|麻烦)?"
    r"(?:生成|绘制|创作|制作)"
    r"(?:一张|一幅|一版)"
    r"[^,，;；。.!！？?\n]{0,24}图"
    r"|(?:帮我|给我|请|麻烦)?"
    r"(?:生成|绘制|创作|制作)"
    r"(?:一张|一幅|一版)"
    r"(?![^,，;；。.!！？?\n]{0,24}"
    + _CHINESE_NON_VISUAL_OBJECT
    + r")"
    r"[^,，;；。.!！？?\n]{1,24}"
    r"|(?:帮我|给我|请|麻烦)?"
    r"画(?:一张|一幅|一版)"
    r"(?!出)"
    r"|(?:帮我|给我|请|麻烦)?"
    r"画(?:一只|一个|一些|个|只)"
    r"[^,，;；。.!！？?\n]{0,20}"
    r"(?:猫|狗|柯基|兔|鸟|熊猫|人物|女孩|男孩|机器人|龙|独角兽)"
    r"|(?:帮我|给我|请|麻烦)?画"
    r"[^,，;；。.!！？?\n]{0,20}"
    + _CHINESE_VISUAL_NOUN
    + r"|(?:帮我|给我|请|麻烦)?"
    r"(?:生成|绘制|创作|制作|设计)"
    r"(?:一张|一幅|一版|一个)?"
    r"[^,，;；。.!！？?\n]{0,16}"
    r"(?:水墨(?:山水|画)|油画|素描|像素(?:画|艺术)|概念艺术|"
    r"3D渲染|三维渲染|赛博朋克场景)"
    + r")",
    re.IGNORECASE,
)
_ENGLISH_DRAW_RE = re.compile(
    r"\b(?:draw|paint|illustrate)\s+(?:me\s+)?(?:an?|the|some)\s+"
    r"(?:(?:cute|small|large|tiny|happy|sad|realistic|stylized|"
    r"cinematic|fantasy)\s+){0,3}"
    r"(?:cat|kitten|dog|puppy|corgi|rabbit|bird|panda|person|woman|man|"
    r"girl|boy|robot|dragon|unicorn|character)\b"
    r"(?=\s*(?:$|[,.!?;]|riding\b|wearing\b|sitting\b|standing\b|"
    r"flying\b|holding\b|with\b|in\b|on\b|at\b|by\b|under\b|over\b|"
    r"beside\b|of\b|for\b))",
    re.IGNORECASE,
)
_ENGLISH_DRAW_NON_VISUAL_RE = re.compile(
    r"(?:"
    r"\bdraw\s+(?:me\s+)?(?:an?|the|some)?\s*"
    r"(?:salary|(?:random\s+)?sample|conclusion|comparison|analogy|"
    r"lesson|inference|distinction|parallel|attention|card|bath|curtains?)\b"
    r"|\billustrate\s+(?:me\s+)?(?:an?|the|some)?\s*"
    r"(?:example|point|calculation|dataset|numbers?)\b"
    r"|\bpaint\s+(?:me\s+)?(?:an?|the|some)?\s*"
    r"(?:room|wall|door|ceiling|fence|town)\b"
    r")",
    re.IGNORECASE,
)
_CHINESE_DRAW_RE = re.compile(
    r"^\s*(?:帮我|给我|请|麻烦)?"
    r"(?:画(?!出)|绘制)"
    r"[^,，;；。.!！？?\n]{0,24}"
    r"(?:猫|狗|柯基|兔|鸟|熊猫|人物|人像|女孩|男孩|机器人|龙|"
    r"独角兽|山水|风景)\s*$",
    re.IGNORECASE,
)
_ENGLISH_GENERATE_RE = re.compile(
    r"\b(?:generate|create|make|render|design)\s+"
    r"(?:me\s+)?(?:(?:an?|the|some|this|that)\s+)?"
    r"(?:(?!(?:about|for|on|of|regarding|concerning|report|plan|"
    r"comparison|presentation|proposal|analysis|documentation|guide)\b)"
    r"[a-z0-9][a-z0-9_-]*\s+){0,4}"
    r"\b(?:image|picture|photo|poster|illustration|cover|avatar|graphic)\b"
    r"(?=\s*(?:$|[,.!?;]|of\b|for\b|with\b|showing\b|depicting\b|"
    r"featuring\b|in\b|on\b|at\b|by\b|under\b|over\b|beside\b))",
    re.IGNORECASE,
)
_ENGLISH_NON_VISUAL_OBJECT_RE = re.compile(
    r"\b(?:generate|create|make|render|design)\s+"
    r"(?:me\s+)?(?:(?:an?|the|some|this|that)\s+)?"
    r"(?:(?!(?:image|picture|photo|poster|illustration|cover|avatar|"
    r"graphic)\b)[a-z0-9][a-z0-9_-]*\s+){0,4}"
    r"(?:report|plan|list|table|spreadsheet|documentation|docs|tutorial|"
    r"guide|article|essay|poem|code|summary|checklist|comparison|"
    r"presentation|deck|proposal|specification|analysis|benchmark)\b"
    r"|"
    r"\b(?:generate|create|make|render|design)\s+"
    r"(?:me\s+)?(?:(?:an?|the|some|this|that)\s+)?"
    r"(?:image|picture|photo)\s+(?:generation|creation)\s+"
    r"(?:report|plan|list|table|documentation|tutorial|guide|analysis|"
    r"policy|api|workflow|system|pipeline|service|architecture)\b"
    r"|"
    r"\b(?:generate|create|make|render|design)\s+"
    r"(?:me\s+)?(?:(?:an?|the|some|this|that)\s+)?"
    r"(?:image|picture|photo|cover|graphic|illustration)\s+"
    r"(?:(?:upload|generation|creation|search|indexing)\s+)?"
    r"(?:component|uploader|classifier|search|index|api|sdk|pipeline|"
    r"service|course|letter|system|workflow|plugin|policy)\b",
    re.IGNORECASE,
)
_ENGLISH_VISUAL_MEDIUM_RE = re.compile(
    r"\b(?:generate|create|make|render|design|draw|paint|illustrate)\s+"
    r"(?:me\s+)?(?:(?:an?|the|some|this|that)\s+)?"
    r"(?:(?:new|original|custom|detailed|simple|stylized|cinematic)\s+){0,3}"
    r"(?:image|picture|photo|poster|illustration|cover|avatar|graphic|"
    r"watercolou?r\s+(?:landscape|portrait|painting|scene|artwork)|"
    r"watercolou?r|painting|sketch|drawing|portrait|landscape|scene|"
    r"artwork|logo|icon|diagram|infographic|mockup|wallpaper|sticker|"
    r"comic|storyboard|photograph|pixel\s+art|concept\s+art|"
    r"(?:3d|three[- ]dimensional)\s+render|digital\s+art|line\s+art|"
    r"vector\s+art)\b"
    r"(?=\s*(?:$|[,.!?;]|of\b|for\b|with\b|showing\b|depicting\b|"
    r"featuring\b|in\b|on\b|at\b|by\b|under\b|over\b|beside\b))",
    re.IGNORECASE,
)
_IMAGE_NOUN_RE = re.compile(
    r"(?:图片|图像|海报|插画|配图|头像|封面|视觉稿"
    r"|\bimage\b|\bpicture\b|\bphoto\b|\bposter\b|\billustration\b"
    r"|\bcover(?:\s+image)?\b|\bavatar\b|\bgraphic\b)",
    re.IGNORECASE,
)
_ENGLISH_METAPHOR_OR_TUTORIAL_RE = re.compile(
    r"(?:"
    r"\b(?:draw\s+a\s+conclusion|paint\s+a\s+clear\s+picture)\b"
    r"|\b(?:tutorial|documentation|example|command|quote|quoted)\b"
    r".{0,80}\b(?:draw|paint|generate|create|make|render)\b"
    r"|\b(?:show|tell|explain|teach)\s+me\s+how\s+to\b"
    r".{0,80}\b(?:draw|paint|generate|create|make|render)\b"
    r")",
    re.IGNORECASE,
)
_NON_PROVIDER_VISUAL_MODALITY = (
    r"(?:mermaid|plantuml|graphviz|svg|html\s*/\s*css|html|css|"
    r"ascii\s+art|ascii\s*字符画|字符画)"
)
_ENGLISH_NON_PROVIDER_OUTPUT_RE = re.compile(
    r"\b(?:in|as|using|with)\s+(?:an?\s+)?"
    + _NON_PROVIDER_VISUAL_MODALITY
    + r"\b"
    r"|"
    r"\b(?:generate|create|make|render|design|draw|paint|illustrate)\b"
    r".{0,80}\b"
    + _NON_PROVIDER_VISUAL_MODALITY
    + r"\s+(?:diagram|icon|image|graphic|file|code|markup|art)\b",
    re.IGNORECASE,
)
_CHINESE_NON_PROVIDER_OUTPUT_RE = re.compile(
    r"(?:用|使用|以|采用)\s*"
    + _NON_PROVIDER_VISUAL_MODALITY
    + r"|(?:的|为|成|输出为|保存为|导出为)\s*"
    + _NON_PROVIDER_VISUAL_MODALITY
    + r"|(?:生成|绘制|创作|制作|设计|画)"
    r"[^,，;；。.!！？?\n]{0,16}"
    + _NON_PROVIDER_VISUAL_MODALITY
    + r"[^,，;；。.!！？?\n]{0,8}"
    + _CHINESE_VISUAL_NOUN,
    re.IGNORECASE,
)

_TASK_GATE_TTL_SECONDS = 60 * 60
_TASK_GATE_CAPACITY = 2048


@dataclass
class _TaskGate:
    owner_token: str
    allow_generation: bool
    claimed: bool
    closed: bool
    touched_at: float


_TASK_GATE_LOCK = threading.RLock()
_TASK_GATES: "OrderedDict[str, _TaskGate]" = OrderedDict()
_IMAGE_GATE_CONTEXT: ContextVar[tuple[str, str] | None] = ContextVar(
    "image_generation_gate_context",
    default=None,
)


def _decision(
    action: ImageIntentAction,
    reason_code: str,
    *,
    prompt: str | None = None,
    clarification: ImageIntentClarification | None = None,
) -> ImageIntentDecision:
    return ImageIntentDecision(
        action=action,
        reason_code=reason_code,
        prompt=prompt,
        clarification=clarification,
    )


def _structured_clarification(text: str) -> ImageIntentClarification:
    if re.search(r"[\u3400-\u9fff]", text):
        return ImageIntentClarification(
            question="你希望我直接生成图片，还是先帮你讨论或整理图片方案？",
            choices=("直接生成图片", "先讨论图片方案"),
        )
    return ImageIntentClarification(
        question=(
            "Would you like me to generate the image now, "
            "or discuss the image plan first?"
        ),
        choices=("Generate the image", "Discuss the image plan"),
    )


def _decode_tool_result(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, Mapping) else {}


def _immediately_previous_turn(
    previous_turn_messages: Sequence[Mapping[str, Any]] | None,
) -> list[Mapping[str, Any]]:
    messages = [
        item
        for item in (previous_turn_messages or ())
        if isinstance(item, Mapping)
    ]
    last_user = -1
    for index, message in enumerate(messages):
        if message.get("role") == "user":
            last_user = index
    return messages[last_user + 1:] if last_user >= 0 else messages


def _validated_local_image_result(result: Mapping[str, Any]) -> bool:
    from agent.image_gen_provider import (
        _images_cache_dir,
        validated_cache_image_ref,
    )

    direct_path = str(result.get("image") or "").strip()
    if direct_path:
        verified = validated_cache_image_ref(direct_path)
        image_ref = str(result.get("image_ref") or "").strip()
        digest = str(result.get("sha256") or "").strip().lower()
        return bool(
            verified
            and re.fullmatch(r"[0-9a-f]{64}", digest)
            and verified == (image_ref, digest)
        )
    image_ref = str(result.get("image_ref") or "").strip()
    digest = str(result.get("sha256") or "").strip().lower()
    if (
        not image_ref
        or image_ref != image_ref.rsplit("/", 1)[-1]
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        return False
    verified = validated_cache_image_ref(str(_images_cache_dir() / image_ref))
    return bool(verified and verified == (image_ref, digest))


def previous_turn_image_generation_prompt(
    previous_turn_messages: Sequence[Mapping[str, Any]] | None,
) -> str | None:
    """Return the adjacent turn's prompt only after a usable image success."""
    messages = _immediately_previous_turn(previous_turn_messages)
    image_prompts: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, Mapping):
                continue
            function = tool_call.get("function")
            if not isinstance(function, Mapping):
                continue
            if str(function.get("name") or "") != "image_generate":
                continue
            call_id = str(tool_call.get("id") or "").strip()
            try:
                arguments = json.loads(str(function.get("arguments") or "{}"))
            except (TypeError, ValueError):
                arguments = {}
            prompt = (
                str(arguments.get("prompt") or "").strip()
                if isinstance(arguments, Mapping)
                else ""
            )
            if call_id and prompt:
                image_prompts[call_id] = prompt

    if not image_prompts:
        return None
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "tool":
            continue
        call_id = str(message.get("tool_call_id") or "").strip()
        if call_id not in image_prompts:
            continue
        name = str(message.get("name") or message.get("tool_name") or "").strip()
        if name and name != "image_generate":
            continue
        result = _decode_tool_result(message.get("content"))
        if result.get("success") is True and _validated_local_image_result(result):
            return image_prompts[call_id]
    return None


def previous_turn_had_successful_image_generate(
    previous_turn_messages: Sequence[Mapping[str, Any]] | None,
) -> bool:
    """Compatibility predicate backed by the stricter adjacent-turn check."""
    return bool(previous_turn_image_generation_prompt(previous_turn_messages))


def _looks_negated(text: str) -> bool:
    clauses = re.split(r"[,，;；。.!！？?\n]+", text)
    for clause in clauses:
        lowered = clause.lower()
        chinese = re.search(
            r"(?:不要|别|无需|不需要|禁止|不能|不该|不应该|"
            r"避免|请勿|不得).{0,10}"
            r"(?:生成|画|绘制|制作|创作)"
            r".{0,8}(?:图|图片|图像|海报|插画)?",
            clause,
        )
        english = re.search(
            r"\b(?:do\s+not|don't|dont|never|no\s+need\s+to|"
            r"should\s+not|shouldn't|shouldnt|must\s+not|"
            r"can't|cant|cannot|avoid)\b"
            r".{0,24}"
            r"\b(?:generate|create|draw|paint|render|make)\b",
            lowered,
        )
        if chinese or english:
            return True
    return False


def _looks_configuration(text: str) -> bool:
    lowered = text.lower()
    image_generation_topic = bool(
        re.search(
            r"(?:生图|图片生成|图像生成|"
            r"(?:生成|制作|绘制).{0,4}(?:图片|图像))",
            text,
        )
        or re.search(r"\bimage\s+generation\b", lowered)
    )
    if not image_generation_topic:
        return False
    return bool(
        re.search(
            r"(?:怎么|如何|怎样|配置|设置|密钥|模型|服务商|供应商|授权)",
            text,
        )
        or re.search(
            r"\b(?:how|configure|configuration|setup|settings?|provider|"
            r"api\s*key|credential|model|authorization)\b",
            lowered,
        )
    )


def _looks_configuration_question(text: str) -> bool:
    if not _looks_configuration(text):
        return False
    lowered = text.lower()
    return bool(
        re.search(
            r"(?:怎么|如何|怎样|哪里|在哪|哪个|什么|是否|吗|呢|？)",
            text,
        )
        or re.search(
            r"\b(?:how|where|which|what|why|do\s+i|should\s+i)\b",
            lowered,
        )
    )


def _looks_image_understanding(text: str) -> bool:
    lowered = text.lower()
    chinese = re.search(
        r"(?:这张图|这幅图|图片里|图中|看图|识图|"
        r"(?:分析|描述|识别|读取|总结).{0,10}(?:图|图片|图像))",
        text,
    )
    english = re.search(
        r"(?:"
        r"\bwhat\b.{0,50}\b(?:image|picture|photo)\b"
        r"|\b(?:describe|analy[sz]e|inspect|read|understand)\b"
        r".{0,50}\b(?:image|picture|photo)\b"
        r")",
        lowered,
    )
    return bool(chinese or english)


def _chinese_generated_output_is_non_visual(text: str) -> bool:
    verb = _CHINESE_GENERATION_VERB_RE.search(text)
    if verb is None:
        return False
    tail = text[verb.end():]
    non_visual = list(
        re.finditer(_CHINESE_NON_VISUAL_OBJECT, tail, re.IGNORECASE)
    )
    if not non_visual:
        return False
    visual = list(re.finditer(_CHINESE_VISUAL_NOUN, tail, re.IGNORECASE))
    return not visual or non_visual[-1].start() > visual[-1].start()


def _looks_explicit_generation(text: str) -> bool:
    for clause in re.split(r"[,，;；。.!！？?\n]+", text):
        candidate = clause.strip()
        if not candidate or _looks_negated(candidate):
            continue
        lowered = candidate.lower()
        if _ENGLISH_METAPHOR_OR_TUTORIAL_RE.search(lowered):
            continue
        if _ENGLISH_DRAW_NON_VISUAL_RE.search(lowered):
            continue
        if _looks_configuration_question(candidate):
            continue
        if _chinese_generated_output_is_non_visual(candidate):
            continue
        if _ENGLISH_NON_VISUAL_OBJECT_RE.search(candidate):
            continue
        if re.search(
            r"(?:为什么|为何|怎么会|谁让你).{0,20}"
            r"(?:生成|画|绘制|制作|创作)",
            candidate,
        ):
            continue
        if re.search(
            r"\b(?:why\s+did|did|have|has)\s+(?:you|it|we|they)\b"
            r".{0,24}\b(?:generate|create|draw|paint|render|make)\b",
            lowered,
        ):
            continue
        if re.search(
            r"(?:怎么|如何|怎样).{0,12}(?:生成|画|绘制|制作|创作)",
            candidate,
        ):
            continue
        if re.search(
            r"(?:是什么意思|什么含义|怎么理解|如何理解|指什么)\s*$",
            candidate,
        ):
            continue
        if re.search(
            r"\bhow\s+(?:do\s+i|can\s+i|to)\b.{0,40}"
            r"\b(?:generate|create|draw|paint|render|make)\b",
            lowered,
        ):
            continue
        chinese_candidate = re.sub(
            r"(?:图片|图像)生成(?:模型|服务|配置|能力)",
            "",
            candidate,
        )
        if (
            _CHINESE_GENERATE_RE.search(chinese_candidate)
            or _CHINESE_DRAW_RE.search(chinese_candidate)
            or _ENGLISH_DRAW_RE.search(candidate)
            or _ENGLISH_VISUAL_MEDIUM_RE.search(candidate)
            or _ENGLISH_GENERATE_RE.search(candidate)
        ):
            return True
    return False


def _looks_non_visual_generation_request(text: str) -> bool:
    for clause in re.split(r"[,，;；。.!！？?\n]+", text):
        candidate = clause.strip()
        if not candidate:
            continue
        if _chinese_generated_output_is_non_visual(candidate):
            return True
        if _ENGLISH_DRAW_NON_VISUAL_RE.search(candidate):
            return True
        if _ENGLISH_NON_VISUAL_OBJECT_RE.search(candidate):
            return True
    return False


def _looks_explicit_non_provider_visual_output(text: str) -> bool:
    """Detect an explicit text/code/vector output medium before paid routing."""
    if not (
        _ENGLISH_NON_PROVIDER_OUTPUT_RE.search(text)
        or _CHINESE_NON_PROVIDER_OUTPUT_RE.search(text)
    ):
        return False
    return bool(
        re.search(
            r"(?:生成|绘制|创作|制作|设计|画)|"
            r"\b(?:generate|create|make|render|design|draw|paint|illustrate)\b",
            text,
            re.IGNORECASE,
        )
    )


def _looks_ambiguous_image_request(text: str) -> bool:
    if not _IMAGE_NOUN_RE.search(text):
        return False
    return bool(
        re.search(r"(?:想要|需要|给我|帮我|来一|弄一)", text)
        or re.search(
            r"\b(?:i\s+(?:want|need)|give\s+me|can\s+you|could\s+you)\b",
            text,
            re.IGNORECASE,
        )
    )


def _strip_creative_request_prefix(text: str) -> str:
    """Remove repeatable politeness/request prefixes without touching the action."""
    candidate = text.strip()
    while candidate:
        updated = re.sub(
            r"^(?:可以帮我|能帮我|能不能|请你|帮我|给我|麻烦|"
            r"能否|你能|可以|请)[\s,，]*",
            "",
            candidate,
            count=1,
        )
        if updated != candidate:
            candidate = updated
            continue
        updated = re.sub(
            r"^(?:please|kindly)\b[\s,]*",
            "",
            candidate,
            count=1,
            flags=re.IGNORECASE,
        )
        if updated != candidate:
            candidate = updated
            continue
        updated = re.sub(
            r"^(?:can|could|would|will)\s+you\b[\s,]*",
            "",
            candidate,
            count=1,
            flags=re.IGNORECASE,
        )
        if updated != candidate:
            candidate = updated
            continue
        updated = re.sub(
            r"^i(?:'d|\s+would)\s+like\s+you\s+to\b[\s,]*"
            r"|^i\s+(?:want|need)\s+you\s+to\b[\s,]*",
            "",
            candidate,
            count=1,
            flags=re.IGNORECASE,
        )
        if updated == candidate:
            break
        candidate = updated
    return candidate


def _looks_ambiguous_creative_request(text: str) -> bool:
    for clause in re.split(r"[,，;；。.!！？?\n]+", text):
        candidate = clause.strip()
        if not candidate:
            continue
        if re.search(
            r"(?:是什么意思|什么含义|怎么理解|如何理解|指什么)\s*$",
            candidate,
        ):
            continue
        normalized = _strip_creative_request_prefix(candidate)
        if re.match(r"^(?:画(?!出)|绘制)\S+", normalized):
            return True
        if re.match(
            r"^(?:draw|paint|illustrate)\b",
            normalized,
            re.IGNORECASE,
        ):
            return True
    return False


def decide_image_intent(
    text: str,
    *,
    previous_turn_messages: Sequence[Mapping[str, Any]] | None = None,
    clarification_already_requested: bool = False,
) -> ImageIntentDecision:
    """Classify one user turn without executing tools or emitting events."""
    prompt = str(text or "").strip()
    if not prompt:
        return _decision(
            ImageIntentAction.PASS_THROUGH,
            "no_image_generation_intent",
        )

    explicit_generation = _looks_explicit_generation(prompt)
    if _looks_negated(prompt) and not explicit_generation:
        return _decision(
            ImageIntentAction.PASS_THROUGH,
            "image_generation_negated",
        )
    if (
        _ENGLISH_METAPHOR_OR_TUTORIAL_RE.search(prompt)
        and not explicit_generation
    ):
        return _decision(
            ImageIntentAction.PASS_THROUGH,
            "no_image_generation_intent",
        )

    if _looks_explicit_non_provider_visual_output(prompt):
        return _decision(
            ImageIntentAction.PASS_THROUGH,
            "non_provider_visual_output",
        )

    if _REPEAT_REQUEST_RE.search(prompt):
        previous_prompt = previous_turn_image_generation_prompt(
            previous_turn_messages
        )
        if previous_prompt:
            return _decision(
                ImageIntentAction.GENERATE,
                "repeat_previous_image_generation",
                prompt=previous_prompt,
            )
        if clarification_already_requested:
            return _decision(
                ImageIntentAction.PASS_THROUGH,
                "repeat_without_previous_image_generation_already_clarified",
            )
        return _decision(
            ImageIntentAction.CLARIFY,
            "repeat_without_previous_image_generation",
            clarification=_structured_clarification(prompt),
        )

    if explicit_generation:
        return _decision(
            ImageIntentAction.GENERATE,
            "explicit_image_generation",
            prompt=prompt,
        )

    if _looks_configuration_question(prompt):
        return _decision(
            ImageIntentAction.PASS_THROUGH,
            "image_generation_configuration",
        )
    if _looks_image_understanding(prompt):
        return _decision(
            ImageIntentAction.PASS_THROUGH,
            "image_understanding",
        )
    if _looks_non_visual_generation_request(prompt):
        return _decision(
            ImageIntentAction.PASS_THROUGH,
            "no_image_generation_intent",
        )
    if _looks_configuration(prompt):
        return _decision(
            ImageIntentAction.PASS_THROUGH,
            "image_generation_configuration",
        )

    if _looks_ambiguous_creative_request(prompt):
        if clarification_already_requested:
            return _decision(
                ImageIntentAction.PASS_THROUGH,
                "ambiguous_creative_request_already_clarified",
            )
        return _decision(
            ImageIntentAction.CLARIFY,
            "ambiguous_creative_request",
            clarification=_structured_clarification(prompt),
        )

    if _looks_ambiguous_image_request(prompt):
        if clarification_already_requested:
            return _decision(
                ImageIntentAction.PASS_THROUGH,
                "ambiguous_image_request_already_clarified",
            )
        return _decision(
            ImageIntentAction.CLARIFY,
            "ambiguous_image_request",
            clarification=_structured_clarification(prompt),
        )

    return _decision(
        ImageIntentAction.PASS_THROUGH,
        "no_image_generation_intent",
    )


def prompt_from_image_clarification(
    payload: Mapping[str, Any] | None,
    *,
    original_prompt: str,
) -> str | None:
    """Accept only an explicit image-generation choice or explicit command."""
    if not isinstance(payload, Mapping) or payload.get("error"):
        return None
    response = str(payload.get("user_response") or "").strip()
    if not response:
        return None
    normalized = response.casefold()
    if normalized in {
        "直接生成图片",
        "生成图片",
        "generate the image",
        "generate image",
    }:
        prompt = str(original_prompt or "").strip()
        return prompt or None
    if normalized in {
        "先讨论图片方案",
        "讨论图片方案",
        "discuss the image plan",
        "discuss image plan",
    }:
        return None
    decision = decide_image_intent(response, clarification_already_requested=True)
    return (
        decision.prompt
        if decision.action is ImageIntentAction.GENERATE
        else None
    )


def build_image_generate_response(prompt: str):
    """Build one ordinary tool response for the existing executor path."""
    from agent.transports.types import NormalizedResponse, ToolCall

    return NormalizedResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id=f"image-generate-{uuid.uuid4().hex}",
                name="image_generate",
                arguments=json.dumps({"prompt": prompt}, ensure_ascii=False),
            )
        ],
        finish_reason="tool_calls",
    )


def build_image_clarify_response(clarification: ImageIntentClarification):
    """Build one ordinary clarify response with two fixed, structured choices."""
    from agent.transports.types import NormalizedResponse, ToolCall

    return NormalizedResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id=f"image-clarify-{uuid.uuid4().hex}",
                name="clarify",
                arguments=json.dumps(
                    clarification.to_dict(),
                    ensure_ascii=False,
                ),
            )
        ],
        finish_reason="tool_calls",
    )


def _prune_task_gates(now: float) -> None:
    expired = [
        task_id
        for task_id, gate in _TASK_GATES.items()
        if now - gate.touched_at > _TASK_GATE_TTL_SECONDS
    ]
    for task_id in expired:
        _TASK_GATES.pop(task_id, None)


def _normalized_gate_identity(
    task_id: str,
    owner_token: str,
) -> tuple[str, str]:
    return (
        str(task_id or "").strip(),
        str(owner_token or "").strip(),
    )


@contextmanager
def image_generation_gate_scope(
    task_id: str,
    owner_token: str,
) -> Iterator[None]:
    """Bind one immutable image gate lease to tool execution context."""
    normalized_task, normalized_owner = _normalized_gate_identity(
        task_id,
        owner_token,
    )
    token = _IMAGE_GATE_CONTEXT.set(
        (normalized_task, normalized_owner)
        if normalized_task and normalized_owner
        else None
    )
    try:
        yield
    finally:
        _IMAGE_GATE_CONTEXT.reset(token)


def current_image_generation_gate() -> tuple[str, str] | None:
    return _IMAGE_GATE_CONTEXT.get()


def begin_image_generation_task(
    task_id: str,
    *,
    allow_generation: bool,
    owner_token: str = "",
) -> str | None:
    normalized, normalized_owner = _normalized_gate_identity(
        task_id,
        owner_token,
    )
    if not normalized or not normalized_owner:
        return "image_generation_gate_identity_missing"
    now = time.monotonic()
    with _TASK_GATE_LOCK:
        _prune_task_gates(now)
        gate = _TASK_GATES.get(normalized)
        if gate is None:
            while len(_TASK_GATES) >= _TASK_GATE_CAPACITY:
                evictable_task = next(
                    (
                        existing_task
                        for existing_task, existing_gate in _TASK_GATES.items()
                        if not existing_gate.claimed
                    ),
                    None,
                )
                if evictable_task is None:
                    return "image_generation_gate_capacity_exhausted"
                _TASK_GATES.pop(evictable_task, None)
            gate = _TaskGate(
                owner_token=normalized_owner,
                allow_generation=bool(allow_generation),
                claimed=False,
                closed=False,
                touched_at=now,
            )
            _TASK_GATES[normalized] = gate
        elif gate.owner_token != normalized_owner:
            return "image_generation_gate_owner_mismatch"
        else:
            # Re-entering the same logical user turn (for example WebUI
            # credential self-heal) must preserve a prior Provider claim.
            if not gate.claimed:
                gate.allow_generation = bool(allow_generation)
            gate.closed = False
            gate.touched_at = now
        _TASK_GATES.move_to_end(normalized)
    return None


def claim_image_generation(
    task_id: str,
    *,
    owner_token: str = "",
) -> str | None:
    """Atomically claim the one provider invocation allowed for a task."""
    normalized, normalized_owner = _normalized_gate_identity(
        task_id,
        owner_token,
    )
    if not normalized or not normalized_owner:
        return "image_generation_gate_identity_missing"
    now = time.monotonic()
    with _TASK_GATE_LOCK:
        _prune_task_gates(now)
        gate = _TASK_GATES.get(normalized)
        if gate is None:
            return "image_generation_gate_not_initialized"
        if gate.owner_token != normalized_owner:
            return "image_generation_gate_owner_mismatch"
        gate.touched_at = now
        _TASK_GATES.move_to_end(normalized)
        if gate.closed:
            return "image_generation_task_closed"
        if not gate.allow_generation:
            return "image_generation_not_requested"
        if gate.claimed:
            return "duplicate_generation_this_turn"
        gate.claimed = True
        return None


def cleanup_image_generation_task(
    task_id: str,
    *,
    owner_token: str = "",
) -> bool:
    normalized, normalized_owner = _normalized_gate_identity(
        task_id,
        owner_token,
    )
    if not normalized or not normalized_owner:
        return False
    with _TASK_GATE_LOCK:
        gate = _TASK_GATES.get(normalized)
        if gate is None or gate.owner_token != normalized_owner:
            return False
        gate.closed = True
        gate.touched_at = time.monotonic()
        _TASK_GATES.move_to_end(normalized)
        return True


__all__ = [
    "ImageIntentAction",
    "ImageIntentClarification",
    "ImageIntentDecision",
    "begin_image_generation_task",
    "build_image_clarify_response",
    "build_image_generate_response",
    "claim_image_generation",
    "cleanup_image_generation_task",
    "current_image_generation_gate",
    "decide_image_intent",
    "image_generation_gate_scope",
    "previous_turn_image_generation_prompt",
    "previous_turn_had_successful_image_generate",
    "prompt_from_image_clarification",
]
