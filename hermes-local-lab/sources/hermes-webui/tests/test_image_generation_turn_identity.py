from __future__ import annotations

from pathlib import Path


def test_stream_and_self_heal_share_one_unique_image_turn_lease():
    source = (
        Path(__file__).resolve().parents[1] / "api" / "streaming.py"
    ).read_text(encoding="utf-8")

    initial_start = source.index("result = agent.run_conversation(")
    initial_end = source.index("if cancel_event.is_set():", initial_start)
    initial_call = source[initial_start:initial_end]

    heal_start = source.index("_heal_result = agent.run_conversation(")
    heal_end = source.index("_heal_all_msgs =", heal_start)
    heal_call = source[heal_start:heal_end]

    fallback_heal_start = source.index(
        "_heal_result = _heal_agent.run_conversation("
    )
    fallback_heal_end = source.index(
        "# Retry succeeded",
        fallback_heal_start,
    )
    fallback_heal_call = source[
        fallback_heal_start:fallback_heal_end
    ]

    for call in (initial_call, heal_call, fallback_heal_call):
        assert "image_turn_id=turn_envelope.turn_id" in call
        assert "image_gate_owner=stream_id" in call
