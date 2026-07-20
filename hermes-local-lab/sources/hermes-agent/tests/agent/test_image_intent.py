from __future__ import annotations

import hashlib
import json
import threading

import pytest


@pytest.mark.parametrize(
    "text",
    [
        "帮我生成一张水墨山水图",
        "帮我生成一张可爱的猫",
        "生成一张写实人物",
        "画一只戴宇航员头盔的猫",
        "请绘制一幅产品宣传插画",
        "Generate an image of a quiet library at night.",
        "Draw a corgi riding a bicycle.",
        "Create a poster for a summer jazz festival.",
        "Generate a watercolor landscape.",
        "Create a watercolor of a cat.",
        "Generate pixel art of a cat.",
        "Create concept art for a game.",
        "Generate a 3D render of a house.",
        "生成水墨山水",
        "生成一个赛博朋克场景",
        "画猫",
        "帮我画猫",
        "绘制猫",
        "画水墨山水",
    ],
)
def test_explicit_generation_intent_is_routed_to_one_image_task(text):
    from agent.image_intent import ImageIntentAction, decide_image_intent

    decision = decide_image_intent(text)

    assert decision.action is ImageIntentAction.GENERATE
    assert decision.prompt == text
    assert decision.clarification is None
    assert decision.reason_code == "explicit_image_generation"


@pytest.mark.parametrize(
    ("text", "reason_code"),
    [
        ("图片生成模型要怎么配置？", "image_generation_configuration"),
        ("阿里云生图的 API Key 在哪里设置？", "image_generation_configuration"),
        ("How do I configure the image generation provider?", "image_generation_configuration"),
        ("这张图里有什么？", "image_understanding"),
        ("请分析这张图片并总结文字", "image_understanding"),
        ("Describe what is happening in this photo.", "image_understanding"),
        ("不要生成图片，只给我一段文案", "image_generation_negated"),
        ("别画图，先讨论思路", "image_generation_negated"),
        ("你不应该生成图片", "image_generation_negated"),
        ("不能生成图片，只解释原因", "image_generation_negated"),
        ("避免生成图片，先讨论方案", "image_generation_negated"),
        ("Do not generate an image; explain the concept instead.", "image_generation_negated"),
        ("It shouldn't generate an image.", "image_generation_negated"),
        ("把下面这段话翻译成英文", "no_image_generation_intent"),
        ("为什么刚才生成了图片？", "no_image_generation_intent"),
        ("Why did you generate an image?", "no_image_generation_intent"),
        ("Did you generate an image?", "no_image_generation_intent"),
        ("Draw a comparison between the two plans.", "no_image_generation_intent"),
        ("Draw an analogy from this example.", "no_image_generation_intent"),
        ("帮我生成一个报告", "no_image_generation_intent"),
        ("请制作一份季度汇报", "no_image_generation_intent"),
        ("创作一首七言绝句", "no_image_generation_intent"),
        ("生成一个 Excel 表格", "no_image_generation_intent"),
        ("帮我画出这段代码的重点", "no_image_generation_intent"),
        ("生成图片的 API Key 在哪里设置？", "image_generation_configuration"),
        ("生成图片要用哪个模型？", "image_generation_configuration"),
        ("Create a report about image generation.", "no_image_generation_intent"),
        ("Create a plan for image generation.", "no_image_generation_intent"),
        ("Make a list of image generation providers.", "no_image_generation_intent"),
        ("Generate documentation for image creation.", "no_image_generation_intent"),
        ("生成一份关于图片生成技术的报告", "no_image_generation_intent"),
        ("制作图片生成方案", "no_image_generation_intent"),
        (
            "Create a provider comparison for image generation.",
            "no_image_generation_intent",
        ),
        (
            "Make a presentation about image creation.",
            "no_image_generation_intent",
        ),
        ("生成一个 UUID", "no_image_generation_intent"),
        ("生成测试数据", "no_image_generation_intent"),
        ("生成随机密码", "no_image_generation_intent"),
        ("生成 SQL 查询", "no_image_generation_intent"),
        ("制作一个压缩包", "no_image_generation_intent"),
        ("生成一份图片生成技术报告给领导", "no_image_generation_intent"),
        ("制作图片生成方案并附三条建议", "no_image_generation_intent"),
        ("Create an image generation policy.", "no_image_generation_intent"),
        ("Create an image generation API.", "no_image_generation_intent"),
        ("Create an image generation workflow.", "no_image_generation_intent"),
        ("Create an image generation system.", "no_image_generation_intent"),
        ("Draw a salary from the account.", "no_image_generation_intent"),
        (
            "Draw a random sample from the dataset.",
            "no_image_generation_intent",
        ),
        (
            "Illustrate an example with numbers.",
            "no_image_generation_intent",
        ),
        ("Paint a room blue.", "no_image_generation_intent"),
        ("画重点", "no_image_generation_intent"),
        ("绘制测试用例", "no_image_generation_intent"),
        ("绘制一份合同", "no_image_generation_intent"),
        ("Draw a bath for the baby.", "no_image_generation_intent"),
        ("Draw the curtains.", "no_image_generation_intent"),
        ("Paint the town red.", "no_image_generation_intent"),
        ("画大饼", "no_image_generation_intent"),
        ("Create an image upload component.", "no_image_generation_intent"),
        ("Create an image classifier.", "no_image_generation_intent"),
        ("Design a cover letter.", "no_image_generation_intent"),
        ("制作一个图片上传组件", "no_image_generation_intent"),
        ("帮我做一个图片上传组件", "no_image_generation_intent"),
        ("帮我弄一个图片上传组件", "no_image_generation_intent"),
        ("帮我弄一个图片分类器", "no_image_generation_intent"),
        ("帮我做一个图片搜索组件", "no_image_generation_intent"),
        ("帮我做一个图片生成服务", "no_image_generation_intent"),
        ("请开发一个图片分类器", "no_image_generation_intent"),
    ],
)
def test_clear_non_generation_requests_never_start_an_image_task(text, reason_code):
    from agent.image_intent import ImageIntentAction, decide_image_intent

    decision = decide_image_intent(text)

    assert decision.action is ImageIntentAction.PASS_THROUGH
    assert decision.reason_code == reason_code
    assert decision.prompt is None
    assert decision.clarification is None


@pytest.mark.parametrize(
    "text",
    [
        "Draw a diagram in Mermaid.",
        "Create an icon in SVG.",
        "Create an SVG icon.",
        "Draw a cat in ASCII art.",
        "Illustrate a cat with HTML/CSS.",
        "Render the architecture as PlantUML.",
        "用 Mermaid 绘制一个流程图",
        "请用 Graphviz 生成架构图",
        "画一只猫的 SVG",
        "画一只猫的 ASCII 字符画",
        "生成一个 SVG 图标",
        "绘制一张 Mermaid 流程图",
        "制作一个 HTML/CSS 海报",
    ],
)
def test_explicit_non_provider_output_modality_never_calls_image_provider(text):
    from agent.image_intent import ImageIntentAction, decide_image_intent

    decision = decide_image_intent(text)

    assert decision.action is ImageIntentAction.PASS_THROUGH
    assert decision.reason_code == "non_provider_visual_output"
    assert decision.prompt is None
    assert decision.clarification is None


@pytest.mark.parametrize(
    "text",
    [
        "画蛇添足是什么意思？",
        "这段话刻画了怎样的人物？",
    ],
)
def test_chinese_idioms_and_description_words_do_not_generate(text):
    from agent.image_intent import ImageIntentAction, decide_image_intent

    decision = decide_image_intent(text)

    assert decision.action is ImageIntentAction.PASS_THROUGH
    assert decision.reason_code == "no_image_generation_intent"


@pytest.mark.parametrize(
    "text",
    [
        "不要分析，生成一张图片",
        "不要修改文字，给我生成一张配图",
        "不要生成旧版图片，改为生成一张新版海报",
        "Don't explain; create an image of a cat.",
        "Do not generate the old design; create an image of the new one.",
        "根据这张图生成一张海报",
        "分析这张图并生成一张海报",
        "用我刚配置的图片生成模型生成一张猫图",
    ],
)
def test_explicit_generation_wins_when_other_clause_is_negated_or_contextual(text):
    from agent.image_intent import ImageIntentAction, decide_image_intent

    decision = decide_image_intent(text)

    assert decision.action is ImageIntentAction.GENERATE
    assert decision.reason_code == "explicit_image_generation"
    assert decision.prompt == text


@pytest.mark.parametrize(
    "text",
    [
        "我想要一张猫咪海报",
        "给我一个产品封面",
        "I want a poster for the launch.",
        "Can you give me a cover image?",
    ],
)
def test_ambiguous_image_request_asks_one_structured_clarification(text):
    from agent.image_intent import ImageIntentAction, decide_image_intent

    first = decide_image_intent(text)
    repeated = decide_image_intent(text, clarification_already_requested=True)

    assert first.action is ImageIntentAction.CLARIFY
    assert first.reason_code == "ambiguous_image_request"
    assert first.clarification is not None
    assert first.clarification.question.strip()
    assert len(first.clarification.choices) == 2
    assert first.clarification.to_dict() == {
        "question": first.clarification.question,
        "choices": list(first.clarification.choices),
    }
    assert repeated.action is ImageIntentAction.PASS_THROUGH
    assert repeated.reason_code == "ambiguous_image_request_already_clarified"
    assert repeated.clarification is None


@pytest.mark.parametrize(
    "text",
    [
        "Draw a horse.",
        "Draw an elephant.",
        "Draw a tree.",
        "Draw a house.",
        "Draw a car.",
        "Paint a castle.",
        "Could you please draw a horse?",
        "Would you draw a horse?",
        "I'd like you to draw a horse.",
        "Please could you draw a horse?",
        "Could you kindly draw a horse?",
        "Draw a mermaid in watercolor.",
        "画一匹马",
        "画一朵花",
        "画一棵树",
        "画一辆车",
        "画一座城堡",
        "能帮我画一匹马吗",
        "可以画一朵花吗",
        "请帮我画一匹马",
        "麻烦帮我画一朵花",
        "请给我画一棵树",
    ],
)
def test_unknown_creative_object_asks_before_any_provider_call(text):
    from agent.image_intent import ImageIntentAction, decide_image_intent

    first = decide_image_intent(text)
    repeated = decide_image_intent(text, clarification_already_requested=True)

    assert first.action is ImageIntentAction.CLARIFY
    assert first.reason_code == "ambiguous_creative_request"
    assert first.clarification is not None
    assert repeated.action is ImageIntentAction.PASS_THROUGH
    assert repeated.reason_code == "ambiguous_creative_request_already_clarified"


def _successful_image_turn(
    *,
    image_ref: str = "kite.png",
    sha256: str = "a" * 64,
) -> list[dict]:
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "image-call",
                    "type": "function",
                    "function": {
                        "name": "image_generate",
                        "arguments": json.dumps({"prompt": "a red kite"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "image-call",
            "name": "image_generate",
            "content": json.dumps(
                {
                    "success": True,
                    "image_ref": image_ref,
                    "sha256": sha256,
                }
            ),
        },
    ]


@pytest.mark.parametrize("text", ["再来一张", "再生成一个", "Another one.", "One more image, please."])
def test_repeat_request_generates_only_after_previous_successful_image_tool(
    text, monkeypatch, tmp_path,
):
    from agent import image_gen_provider
    from agent.image_intent import ImageIntentAction, decide_image_intent

    cache = tmp_path / "cache"
    cache.mkdir()
    image = cache / "kite.png"
    image.write_bytes(b"validated-image")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    monkeypatch.setattr(image_gen_provider, "_images_cache_dir", lambda: cache)
    monkeypatch.setattr(
        image_gen_provider,
        "validate_image_bytes",
        lambda _raw: ("image/png", "png", 1, 1),
    )
    after_success = decide_image_intent(
        text,
        previous_turn_messages=_successful_image_turn(sha256=digest),
    )
    without_success = decide_image_intent(text, previous_turn_messages=[])

    assert after_success.action is ImageIntentAction.GENERATE
    assert after_success.reason_code == "repeat_previous_image_generation"
    assert after_success.prompt == "a red kite"
    assert without_success.action is ImageIntentAction.CLARIFY
    assert without_success.reason_code == "repeat_without_previous_image_generation"


@pytest.mark.parametrize(
    "text",
    [
        "再做一个报告",
        "再来一个表格",
        "One more thing: write a report.",
        "Another one for the quarterly report.",
    ],
)
def test_repeat_prefix_with_non_visual_tail_never_reuses_previous_image(
    text, monkeypatch, tmp_path
):
    from agent import image_gen_provider
    from agent.image_intent import ImageIntentAction, decide_image_intent

    cache = tmp_path / "cache"
    cache.mkdir()
    image = cache / "kite.png"
    image.write_bytes(b"validated-image")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    monkeypatch.setattr(image_gen_provider, "_images_cache_dir", lambda: cache)
    monkeypatch.setattr(
        image_gen_provider,
        "validate_image_bytes",
        lambda _raw: ("image/png", "png", 1, 1),
    )

    decision = decide_image_intent(
        text,
        previous_turn_messages=_successful_image_turn(sha256=digest),
    )

    assert decision.action is ImageIntentAction.PASS_THROUGH
    assert decision.reason_code == "no_image_generation_intent"


def test_repeat_prefix_with_new_visual_request_uses_new_prompt_not_previous(
    monkeypatch, tmp_path
):
    from agent import image_gen_provider
    from agent.image_intent import ImageIntentAction, decide_image_intent

    cache = tmp_path / "cache"
    cache.mkdir()
    image = cache / "kite.png"
    image.write_bytes(b"validated-image")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    monkeypatch.setattr(image_gen_provider, "_images_cache_dir", lambda: cache)
    monkeypatch.setattr(
        image_gen_provider,
        "validate_image_bytes",
        lambda _raw: ("image/png", "png", 1, 1),
    )

    decision = decide_image_intent(
        "再生成一张蓝色海报",
        previous_turn_messages=_successful_image_turn(sha256=digest),
    )

    assert decision.action is ImageIntentAction.GENERATE
    assert decision.reason_code == "explicit_image_generation"
    assert decision.prompt == "再生成一张蓝色海报"


@pytest.mark.parametrize(
    "previous_turn",
    [
        [
            {
                "role": "tool",
                "tool_call_id": "image-call",
                "name": "image_generate",
                "content": json.dumps({"success": False, "error": "failed"}),
            }
        ],
        [
            {
                "role": "tool",
                "tool_call_id": "other-call",
                "name": "vision_analyze",
                "content": json.dumps({"success": True}),
            }
        ],
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "image-call",
                        "type": "function",
                        "function": {"name": "image_generate", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "different-call",
                "name": "image_generate",
                "content": json.dumps({"success": True}),
            },
        ],
    ],
)
def test_repeat_request_rejects_failed_wrong_or_unpaired_tool_results(previous_turn):
    from agent.image_intent import ImageIntentAction, decide_image_intent

    decision = decide_image_intent(
        "再来一张",
        previous_turn_messages=previous_turn,
    )

    assert decision.action is ImageIntentAction.CLARIFY
    assert decision.reason_code == "repeat_without_previous_image_generation"


def test_image_intent_module_does_not_produce_capability_route_events():
    import inspect

    import agent.image_intent as image_intent

    source = inspect.getsource(image_intent)
    forbidden = "capability" + "_route"

    assert forbidden not in source
    assert "build_" + forbidden + "_event" not in source


@pytest.mark.parametrize(
    "text",
    [
        "Draw a conclusion from the evidence.",
        "Paint a clear picture of the market.",
        'The tutorial says "draw an image", but explain the command only.',
        "Can you show me how to create an image without generating it?",
    ],
)
def test_english_metaphors_quotes_and_tutorials_do_not_generate(text):
    from agent.image_intent import ImageIntentAction, decide_image_intent

    decision = decide_image_intent(text)

    assert decision.action is ImageIntentAction.PASS_THROUGH


def test_repeat_requires_the_immediately_previous_turn_and_valid_cache(
    monkeypatch, tmp_path,
):
    from agent import image_gen_provider
    from agent.image_intent import ImageIntentAction, decide_image_intent

    cache = tmp_path / "cache"
    cache.mkdir()
    image = cache / "kite.png"
    image.write_bytes(b"validated-image")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    monkeypatch.setattr(image_gen_provider, "_images_cache_dir", lambda: cache)
    monkeypatch.setattr(
        image_gen_provider,
        "validate_image_bytes",
        lambda _raw: ("image/png", "png", 1, 1),
    )
    successful_old_turn = [
        {"role": "user", "content": "draw a kite"},
        *_successful_image_turn(image_ref=image.name, sha256=digest),
        {"role": "assistant", "content": "done"},
    ]
    stale_history = [
        *successful_old_turn,
        {"role": "user", "content": "now discuss typography"},
        {"role": "assistant", "content": "ok"},
    ]

    recent = decide_image_intent(
        "再来一张",
        previous_turn_messages=successful_old_turn,
    )
    stale = decide_image_intent(
        "再来一张",
        previous_turn_messages=stale_history,
    )

    assert recent.action is ImageIntentAction.GENERATE
    assert recent.prompt == "a red kite"
    assert stale.action is ImageIntentAction.CLARIFY


def test_success_true_with_remote_url_or_missing_digest_is_not_usable():
    from agent.image_intent import ImageIntentAction, decide_image_intent

    for result in (
        {"success": True},
        {"success": True, "image": "https://cdn.example.test/private.png"},
        {"success": True, "image_ref": "kite.png"},
    ):
        turn = _successful_image_turn()
        turn[-1]["content"] = json.dumps(result)
        decision = decide_image_intent(
            "再来一张",
            previous_turn_messages=turn,
        )
        assert decision.action is ImageIntentAction.CLARIFY


def test_direct_image_path_must_still_match_tool_bound_name_and_digest(
    monkeypatch, tmp_path
):
    from agent import image_gen_provider
    from agent.image_intent import ImageIntentAction, decide_image_intent

    cache = tmp_path / "cache"
    cache.mkdir()
    image = cache / "kite.png"
    image.write_bytes(b"validated-image")
    monkeypatch.setattr(image_gen_provider, "_images_cache_dir", lambda: cache)
    monkeypatch.setattr(
        image_gen_provider,
        "validate_image_bytes",
        lambda _raw: ("image/png", "png", 1, 1),
    )
    turn = _successful_image_turn()
    turn[-1]["content"] = json.dumps(
        {
            "success": True,
            "image": str(image),
            "image_ref": image.name,
            "sha256": "0" * 64,
        }
    )

    decision = decide_image_intent(
        "再来一张",
        previous_turn_messages=turn,
    )

    assert decision.action is ImageIntentAction.CLARIFY


def test_unbound_ready_artifact_cannot_validate_an_image_tool_success():
    from agent.image_intent import ImageIntentAction, decide_image_intent

    turn = _successful_image_turn()
    turn[-1]["content"] = json.dumps({"success": True})
    turn.append(
        {
            "role": "assistant",
            "artifacts": [
                {
                    "artifact_id": "unrelated",
                    "kind": "image",
                    "status": "ready",
                    "sha256": "a" * 64,
                }
            ],
        }
    )

    decision = decide_image_intent(
        "再来一张",
        previous_turn_messages=turn,
    )

    assert decision.action is ImageIntentAction.CLARIFY


def test_clarification_requires_explicit_generate_choice_or_generate_intent():
    from agent.image_intent import prompt_from_image_clarification

    original = "我想要一张猫咪海报"
    assert prompt_from_image_clarification(
        {"user_response": "直接生成图片"},
        original_prompt=original,
    ) == original
    assert prompt_from_image_clarification(
        {"user_response": "Generate an image of a blue fox"},
        original_prompt=original,
    ) == "Generate an image of a blue fox"
    for payload in (
        {},
        {"error": "timeout"},
        {"user_response": ""},
        {"user_response": "先讨论图片方案"},
        {"user_response": "一只蓝色狐狸"},
    ):
        assert (
            prompt_from_image_clarification(
                payload,
                original_prompt=original,
            )
            is None
        )


def test_task_gate_allows_one_call_isolates_tasks_and_cleans_up():
    from agent.image_intent import (
        begin_image_generation_task,
        claim_image_generation,
        cleanup_image_generation_task,
    )

    owner_a = "owner-a"
    owner_b = "owner-b"
    begin_image_generation_task(
        "task-a", allow_generation=True, owner_token=owner_a
    )
    begin_image_generation_task(
        "task-b", allow_generation=True, owner_token=owner_b
    )
    assert claim_image_generation("task-a", owner_token=owner_a) is None
    assert (
        claim_image_generation("task-a", owner_token=owner_a)
        == "duplicate_generation_this_turn"
    )
    assert claim_image_generation("task-b", owner_token=owner_b) is None
    assert cleanup_image_generation_task("task-a", owner_token=owner_a)
    begin_image_generation_task(
        "task-a", allow_generation=True, owner_token=owner_a
    )
    assert (
        claim_image_generation("task-a", owner_token=owner_a)
        == "duplicate_generation_this_turn"
    )
    assert cleanup_image_generation_task("task-a", owner_token=owner_a)
    assert cleanup_image_generation_task("task-b", owner_token=owner_b)


def test_task_gate_concurrent_claim_has_exactly_one_winner():
    from agent.image_intent import (
        begin_image_generation_task,
        claim_image_generation,
        cleanup_image_generation_task,
    )

    task_id = "task-concurrent"
    owner = "owner-concurrent"
    begin_image_generation_task(
        task_id, allow_generation=True, owner_token=owner
    )
    barrier = threading.Barrier(12)
    results: list[str | None] = []

    def claim():
        barrier.wait()
        results.append(claim_image_generation(task_id, owner_token=owner))

    threads = [threading.Thread(target=claim) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    cleanup_image_generation_task(task_id, owner_token=owner)

    assert results.count(None) == 1
    assert results.count("duplicate_generation_this_turn") == 11


def test_task_gate_requires_identity_and_initialized_owner():
    from agent.image_intent import claim_image_generation

    assert (
        claim_image_generation("", owner_token="owner")
        == "image_generation_gate_identity_missing"
    )
    assert (
        claim_image_generation("turn-a", owner_token="")
        == "image_generation_gate_identity_missing"
    )
    assert (
        claim_image_generation("turn-a", owner_token="owner")
        == "image_generation_gate_not_initialized"
    )


def test_task_gate_owner_cas_prevents_stale_begin_and_cleanup():
    from agent.image_intent import (
        begin_image_generation_task,
        claim_image_generation,
        cleanup_image_generation_task,
    )

    turn_id = "turn-owner-cas"
    owner = "owner-current"
    stale_owner = "owner-stale"

    assert (
        begin_image_generation_task(
            turn_id,
            allow_generation=True,
            owner_token=owner,
        )
        is None
    )
    assert claim_image_generation(turn_id, owner_token=owner) is None
    assert (
        begin_image_generation_task(
            turn_id,
            allow_generation=True,
            owner_token=stale_owner,
        )
        == "image_generation_gate_owner_mismatch"
    )
    assert (
        cleanup_image_generation_task(turn_id, owner_token=stale_owner)
        is False
    )
    assert (
        claim_image_generation(turn_id, owner_token=owner)
        == "duplicate_generation_this_turn"
    )

    assert cleanup_image_generation_task(turn_id, owner_token=owner) is True
    assert (
        claim_image_generation(turn_id, owner_token=owner)
        == "image_generation_task_closed"
    )

    # A retry of the same user turn may reopen the lease but must preserve the
    # fact that Provider I/O was already claimed.
    assert (
        begin_image_generation_task(
            turn_id,
            allow_generation=True,
            owner_token=owner,
        )
        is None
    )
    assert (
        claim_image_generation(turn_id, owner_token=owner)
        == "duplicate_generation_this_turn"
    )
    assert cleanup_image_generation_task(turn_id, owner_token=owner) is True


def test_task_gate_prunes_expired_entries_and_enforces_capacity(monkeypatch):
    from agent import image_intent

    image_intent._TASK_GATES.clear()
    monkeypatch.setattr(image_intent, "_TASK_GATE_CAPACITY", 3)
    monkeypatch.setattr(image_intent, "_TASK_GATE_TTL_SECONDS", 10)
    now = [100.0]
    monkeypatch.setattr(image_intent.time, "monotonic", lambda: now[0])

    for task_id in ("task-a", "task-b", "task-c", "task-d"):
        image_intent.begin_image_generation_task(
            task_id,
            allow_generation=True,
            owner_token=f"owner-{task_id}",
        )

    assert list(image_intent._TASK_GATES) == ["task-b", "task-c", "task-d"]

    now[0] = 111.0
    image_intent.begin_image_generation_task(
        "task-fresh",
        allow_generation=True,
        owner_token="owner-task-fresh",
    )

    assert list(image_intent._TASK_GATES) == ["task-fresh"]
    image_intent.cleanup_image_generation_task(
        "task-fresh",
        owner_token="owner-task-fresh",
    )


def test_task_gate_capacity_does_not_reset_an_existing_claim(monkeypatch):
    from agent import image_intent

    image_intent._TASK_GATES.clear()
    monkeypatch.setattr(image_intent, "_TASK_GATE_CAPACITY", 2)

    image_intent.begin_image_generation_task(
        "turn-preserved",
        allow_generation=True,
        owner_token="owner-preserved",
    )
    assert (
        image_intent.claim_image_generation(
            "turn-preserved",
            owner_token="owner-preserved",
        )
        is None
    )
    image_intent.begin_image_generation_task(
        "turn-other",
        allow_generation=True,
        owner_token="owner-other",
    )

    assert (
        image_intent.begin_image_generation_task(
            "turn-preserved",
            allow_generation=True,
            owner_token="owner-preserved",
        )
        is None
    )
    assert (
        image_intent.claim_image_generation(
            "turn-preserved",
            owner_token="owner-preserved",
        )
        == "duplicate_generation_this_turn"
    )


def test_task_gate_capacity_evicts_unclaimed_entries_before_claimed_facts(
    monkeypatch,
):
    from agent import image_intent

    image_intent._TASK_GATES.clear()
    monkeypatch.setattr(image_intent, "_TASK_GATE_CAPACITY", 2)

    assert (
        image_intent.begin_image_generation_task(
            "turn-claimed",
            allow_generation=True,
            owner_token="owner-claimed",
        )
        is None
    )
    assert (
        image_intent.claim_image_generation(
            "turn-claimed",
            owner_token="owner-claimed",
        )
        is None
    )
    assert (
        image_intent.begin_image_generation_task(
            "turn-unclaimed",
            allow_generation=True,
            owner_token="owner-unclaimed",
        )
        is None
    )

    assert (
        image_intent.begin_image_generation_task(
            "turn-new",
            allow_generation=True,
            owner_token="owner-new",
        )
        is None
    )
    assert list(image_intent._TASK_GATES) == ["turn-claimed", "turn-new"]
    assert (
        image_intent.claim_image_generation(
            "turn-claimed",
            owner_token="owner-claimed",
        )
        == "duplicate_generation_this_turn"
    )


def test_task_gate_capacity_fails_closed_when_all_entries_are_claimed(
    monkeypatch,
):
    from agent import image_intent

    image_intent._TASK_GATES.clear()
    monkeypatch.setattr(image_intent, "_TASK_GATE_CAPACITY", 2)

    for suffix in ("a", "b"):
        assert (
            image_intent.begin_image_generation_task(
                f"turn-{suffix}",
                allow_generation=True,
                owner_token=f"owner-{suffix}",
            )
            is None
        )
        assert (
            image_intent.claim_image_generation(
                f"turn-{suffix}",
                owner_token=f"owner-{suffix}",
            )
            is None
        )

    assert (
        image_intent.begin_image_generation_task(
            "turn-over-capacity",
            allow_generation=True,
            owner_token="owner-over-capacity",
        )
        == "image_generation_gate_capacity_exhausted"
    )
    assert (
        image_intent.claim_image_generation(
            "turn-over-capacity",
            owner_token="owner-over-capacity",
        )
        == "image_generation_gate_not_initialized"
    )
    assert (
        image_intent.claim_image_generation(
            "turn-a",
            owner_token="owner-a",
        )
        == "duplicate_generation_this_turn"
    )


def test_image_tool_gate_blocks_second_provider_dispatch(monkeypatch):
    from agent import image_runtime
    from agent.image_intent import (
        begin_image_generation_task,
        cleanup_image_generation_task,
    )
    from tools import image_generation_tool

    snapshot = {
        "schema_version": 1,
        "fingerprint": "image-fingerprint",
        "_authorization_generation": "image-generation",
        "status": "verified",
        "available": True,
        "provider": "test-provider",
        "model": "test-model",
    }
    provider_calls: list[str] = []
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_a, **_kw: dict(snapshot),
    )
    monkeypatch.setattr(
        image_runtime,
        "build_capability_route_decision",
        lambda *_a, **_kw: object(),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_capture_image_gen_request_binding",
        lambda **_kw: object(),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_dispatch_to_plugin_provider",
        lambda *_a, **_kw: (
            provider_calls.append("provider")
            or json.dumps({"success": True, "image": "/cache/image.png"})
        ),
    )

    owner = "owner-task-one"
    begin_image_generation_task(
        "task-one",
        allow_generation=True,
        owner_token=owner,
    )
    first = json.loads(
        image_generation_tool._handle_image_generate(
            {"prompt": "draw one"},
            image_generation_task_id="task-one",
            image_generation_gate_owner=owner,
            caller_capability_fingerprint=snapshot["fingerprint"],
            caller_capability_generation=snapshot[
                "_authorization_generation"
            ],
        )
    )
    second = json.loads(
        image_generation_tool._handle_image_generate(
            {"prompt": "draw two"},
            image_generation_task_id="task-one",
            image_generation_gate_owner=owner,
            caller_capability_fingerprint=snapshot["fingerprint"],
            caller_capability_generation=snapshot[
                "_authorization_generation"
            ],
        )
    )
    cleanup_image_generation_task("task-one", owner_token=owner)

    assert first["success"] is True
    assert second["error_code"] == "duplicate_generation_this_turn"
    assert provider_calls == ["provider"]


def test_image_tool_gate_blocks_definite_non_generation_before_provider_io(
    monkeypatch,
):
    from agent import image_runtime
    from agent.image_intent import (
        begin_image_generation_task,
        cleanup_image_generation_task,
    )
    from tools import image_generation_tool

    snapshot = {
        "schema_version": 1,
        "fingerprint": "image-fingerprint",
        "_authorization_generation": "image-generation",
        "status": "verified",
        "available": True,
        "provider": "test-provider",
        "model": "test-model",
    }
    provider_calls: list[str] = []
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda *_a, **_kw: dict(snapshot),
    )
    monkeypatch.setattr(
        image_runtime,
        "build_capability_route_decision",
        lambda *_a, **_kw: object(),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_capture_image_gen_request_binding",
        lambda **_kw: object(),
    )
    monkeypatch.setattr(
        image_generation_tool,
        "_dispatch_to_plugin_provider",
        lambda *_a, **_kw: provider_calls.append("provider"),
    )

    owner = "owner-task-blocked"
    begin_image_generation_task(
        "task-blocked",
        allow_generation=False,
        owner_token=owner,
    )
    result = json.loads(
        image_generation_tool._handle_image_generate(
            {"prompt": "should not run"},
            image_generation_task_id="task-blocked",
            image_generation_gate_owner=owner,
            caller_capability_fingerprint=snapshot["fingerprint"],
            caller_capability_generation=snapshot[
                "_authorization_generation"
            ],
        )
    )
    cleanup_image_generation_task("task-blocked", owner_token=owner)

    assert result["error_code"] == "image_generation_not_requested"
    assert provider_calls == []


def test_deterministic_router_allows_exactly_one_provider_claim():
    from agent.conversation_loop import _run_deterministic_image_intent
    from agent.image_intent import (
        claim_image_generation,
        cleanup_image_generation_task,
    )

    class _Agent:
        valid_tool_names = {"image_generate", "clarify"}

        @staticmethod
        def _build_assistant_message(response, finish_reason):
            return {
                "role": "assistant",
                "finish_reason": finish_reason,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.arguments,
                        },
                    }
                    for call in response.tool_calls
                ],
            }

        @staticmethod
        def _execute_tool_calls(
            response,
            messages,
            effective_task_id,
            _iteration,
        ):
            assert response.tool_calls[0].name == "image_generate"
            assert (
                claim_image_generation(
                    effective_task_id,
                    owner_token=owner,
                )
                is None
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": response.tool_calls[0].id,
                    "name": "image_generate",
                    "content": json.dumps(
                        {"success": True, "image": "/cache/image.png"}
                    ),
                }
            )

    task_id = "direct-router-one-call"
    owner = "direct-router-one-call-owner"
    messages: list[dict] = []
    try:
        _run_deterministic_image_intent(
            _Agent(),
            original_user_message="生成一张蓝色狐狸图片",
            conversation_history=[],
            messages=messages,
            effective_task_id=task_id,
            image_turn_id=task_id,
            image_gate_owner=owner,
        )

        assert len(messages) == 2
        assert messages[0]["tool_calls"][0]["function"]["name"] == "image_generate"
        assert (
            claim_image_generation(task_id, owner_token=owner)
            == "duplicate_generation_this_turn"
        )
    finally:
        cleanup_image_generation_task(task_id, owner_token=owner)


def test_no_intent_router_closes_provider_gate():
    from agent.conversation_loop import _run_deterministic_image_intent
    from agent.image_intent import (
        claim_image_generation,
        cleanup_image_generation_task,
    )

    class _Agent:
        valid_tool_names = {"image_generate"}

    turn_id = "no-intent-turn"
    owner = "no-intent-owner"
    try:
        _run_deterministic_image_intent(
            _Agent(),
            original_user_message="把下面这段话翻译成英文",
            conversation_history=[],
            messages=[],
            effective_task_id="session-a",
            image_turn_id=turn_id,
            image_gate_owner=owner,
        )

        assert (
            claim_image_generation(turn_id, owner_token=owner)
            == "image_generation_not_requested"
        )
    finally:
        cleanup_image_generation_task(turn_id, owner_token=owner)


def test_router_execution_failure_never_reopens_claimed_gate():
    from agent.conversation_loop import _run_deterministic_image_intent
    from agent.image_intent import (
        claim_image_generation,
        cleanup_image_generation_task,
    )

    class _Agent:
        valid_tool_names = {"image_generate"}

        @staticmethod
        def _build_assistant_message(_response, _finish_reason):
            return {"role": "assistant", "tool_calls": []}

        @staticmethod
        def _execute_tool_calls(
            _response,
            _messages,
            effective_task_id,
            _iteration,
        ):
            assert (
                claim_image_generation(
                    effective_task_id,
                    owner_token=owner,
                )
                is None
            )
            raise RuntimeError("provider boundary failed")

    task_id = "direct-router-failure"
    owner = "direct-router-failure-owner"
    try:
        _run_deterministic_image_intent(
            _Agent(),
            original_user_message="生成一张蓝色狐狸图片",
            conversation_history=[],
            messages=[],
            effective_task_id=task_id,
            image_turn_id=task_id,
            image_gate_owner=owner,
        )

        assert (
            claim_image_generation(task_id, owner_token=owner)
            == "duplicate_generation_this_turn"
        )
    finally:
        cleanup_image_generation_task(task_id, owner_token=owner)


def test_run_conversation_finally_closes_image_gate_on_early_exception(
    monkeypatch,
):
    from agent import conversation_loop
    from agent.image_intent import (
        begin_image_generation_task,
        claim_image_generation,
    )

    turn_id = "early-return-turn"
    owner = "early-return-owner"

    def fail_early(*_args, **kwargs):
        assert (
            begin_image_generation_task(
                kwargs["image_turn_id"],
                allow_generation=True,
                owner_token=kwargs["image_gate_owner"],
            )
            is None
        )
        raise RuntimeError("early failure")

    monkeypatch.setattr(
        conversation_loop,
        "_run_conversation_impl",
        fail_early,
    )

    with pytest.raises(RuntimeError, match="early failure"):
        conversation_loop.run_conversation(
            object(),
            "generate an image",
            task_id="session-a",
            image_turn_id=turn_id,
            image_gate_owner=owner,
        )

    assert (
        claim_image_generation(turn_id, owner_token=owner)
        == "image_generation_task_closed"
    )


def test_router_classifier_failure_fails_closed(monkeypatch):
    from agent import image_intent
    from agent.conversation_loop import _run_deterministic_image_intent
    from agent.image_intent import (
        claim_image_generation,
        cleanup_image_generation_task,
    )

    monkeypatch.setattr(
        image_intent,
        "decide_image_intent",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            RuntimeError("classifier failed")
        ),
    )

    class _Agent:
        valid_tool_names = {"image_generate"}

    task_id = "direct-router-fail-closed"
    owner = "direct-router-fail-closed-owner"
    try:
        _run_deterministic_image_intent(
            _Agent(),
            original_user_message="ordinary request",
            conversation_history=[],
            messages=[],
            effective_task_id=task_id,
            image_turn_id=task_id,
            image_gate_owner=owner,
        )

        assert (
            claim_image_generation(task_id, owner_token=owner)
            == "image_generation_not_requested"
        )
    finally:
        cleanup_image_generation_task(task_id, owner_token=owner)


def test_codex_app_server_bypass_precedes_deterministic_image_router():
    import inspect

    from agent.conversation_loop import _run_conversation_impl

    source = inspect.getsource(_run_conversation_impl)

    assert source.index('agent.api_mode == "codex_app_server"') < source.index(
        "_run_deterministic_image_intent("
    )
    codex_branch = source[
        source.index('agent.api_mode == "codex_app_server"'):
        source.index("_run_deterministic_image_intent(")
    ]
    assert "conversation_history=conversation_history" in codex_branch
    assert "image_turn_id=image_turn_id" in codex_branch
    assert "image_gate_owner=image_gate_owner" in codex_branch


@pytest.mark.parametrize(
    ("prompt", "expected_allow"),
    [
        ("生成一张蓝色狐狸图片", True),
        ("解释一下这段代码", False),
    ],
)
def test_codex_app_server_turn_forwards_fail_closed_image_lease(
    prompt,
    expected_allow,
):
    from types import SimpleNamespace

    from agent.codex_runtime import run_codex_app_server_turn

    class _Session:
        def __init__(self):
            self.calls = []

        def run_turn(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                final_text="done",
                projected_messages=[],
                tool_iterations=0,
                interrupted=False,
                error=None,
                turn_id="codex-turn-001",
                thread_id="codex-thread-001",
                should_retire=False,
            )

    class _Agent:
        def __init__(self):
            self._codex_session = _Session()
            self._iters_since_skill = 0
            self._skill_nudge_interval = 0
            self.valid_tool_names = set()

        def _sync_external_memory_for_turn(self, **_kwargs):
            return None

    agent = _Agent()
    result = run_codex_app_server_turn(
        agent,
        user_message=prompt,
        original_user_message=prompt,
        messages=[{"role": "user", "content": prompt}],
        conversation_history=[],
        effective_task_id="effective-task-001",
        image_turn_id="image-turn-001",
        image_gate_owner="image-owner-001",
    )

    assert result["completed"] is True
    assert agent._codex_session.calls == [
            {
                "user_input": prompt,
                "image_generation_task_id": "effective-task-001",
                "image_generation_turn_id": "image-turn-001",
                "image_generation_gate_owner": "image-owner-001",
                "allow_image_generation": expected_allow,
            }
    ]
