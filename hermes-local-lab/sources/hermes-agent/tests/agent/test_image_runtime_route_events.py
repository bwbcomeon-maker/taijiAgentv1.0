from __future__ import annotations

import threading

from agent import image_runtime


def _verified_snapshot(
    *,
    diagnostic_id: str = "diag-a",
    authorization_generation: int | None = None,
) -> dict[str, object]:
    state: dict[str, object] = {
        "schema_version": 1,
        "fingerprint": "fp-a",
        "status": "verified",
        "checked_at": "2026-07-17T00:00:00Z",
        "diagnostic_id": diagnostic_id,
    }
    if authorization_generation is not None:
        state["authorization_generation"] = authorization_generation
    private_generation = image_runtime.verification_authorization_generation(
        state,
        expected_fingerprint="fp-a",
        capability="image_generation",
    )
    return {
        "schema_version": 1,
        "fingerprint": "fp-a",
        "status": "verified",
        "available": True,
        "reason_code": "",
        "provider": "custom:demo",
        "model": "image-v1",
        "_authorization_generation": private_generation,
    }


def test_authorization_generation_detects_persisted_aba_without_public_leak():
    first = _verified_snapshot(diagnostic_id="diag-a")
    second = _verified_snapshot(diagnostic_id="diag-b")
    third = _verified_snapshot(
        diagnostic_id="diag-a",
        authorization_generation=3,
    )

    assert first["_authorization_generation"] != second["_authorization_generation"]
    assert first["_authorization_generation"] != third["_authorization_generation"]

    decision = image_runtime.build_capability_route_decision(
        "image_generation",
        snapshot=third,
        route="provider",
        tool_call_id="call-a",
    )
    assert decision.authorization_generation == third["_authorization_generation"]

    projected = image_runtime.project_capability_route_decision(decision)
    assert "_authorization_generation" not in projected
    assert "authorization_generation" not in projected
    assert "authorization_fingerprint" not in projected


def test_route_event_scope_emits_once_only_at_provider_io(monkeypatch):
    snapshot = _verified_snapshot(authorization_generation=7)
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda _capability="image_generation": dict(snapshot),
    )
    decision = image_runtime.build_capability_route_decision(
        "image_generation",
        snapshot=snapshot,
        route="provider",
        tool_call_id="call-a",
    )
    events: list[tuple[tuple[object, ...], dict[str, object]]] = []

    with image_runtime.capability_route_event_scope(
        lambda *args, **kwargs: events.append((args, kwargs)),
        tool_call_id="call-a",
    ):
        assert events == []
        assert image_runtime.emit_capability_route_event_at_provider_io(decision)
        assert not image_runtime.emit_capability_route_event_at_provider_io(decision)

    assert len(events) == 1
    args, kwargs = events[0]
    assert args == (
        "capability_route",
        "image_generate",
        None,
        None,
    )
    assert kwargs["tool_call_id"] == "call-a"
    assert kwargs["route_event"] == image_runtime.project_capability_route_decision(
        decision
    )
    serialized = repr(events)
    assert "_authorization_generation" not in serialized
    assert "authorization_fingerprint" not in serialized


def test_route_event_scope_blocks_stale_or_non_provider_decisions(monkeypatch):
    snapshot = _verified_snapshot(authorization_generation=11)
    stale = dict(snapshot)
    stale["_authorization_generation"] = "new-generation"
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda _capability="image_generation": dict(stale),
    )
    provider_decision = image_runtime.build_capability_route_decision(
        "image_generation",
        snapshot=snapshot,
        route="provider",
        tool_call_id="call-a",
    )
    blocked_decision = image_runtime.build_capability_route_decision(
        "image_generation",
        snapshot=snapshot,
        route="blocked",
        tool_call_id="call-a",
    )
    events: list[object] = []

    assert not image_runtime.emit_capability_route_event_at_provider_io(
        provider_decision
    )
    with image_runtime.capability_route_event_scope(
        lambda *args, **kwargs: events.append((args, kwargs)),
        tool_call_id="call-a",
    ):
        assert not image_runtime.emit_capability_route_event_at_provider_io(
            blocked_decision
        )
        assert not image_runtime.emit_capability_route_event_at_provider_io(
            provider_decision
        )

    assert events == []


def test_route_event_scope_is_request_local_under_concurrency(monkeypatch):
    snapshot = _verified_snapshot(authorization_generation=13)
    monkeypatch.setattr(
        image_runtime,
        "verification_runtime_snapshot",
        lambda _capability="image_generation": dict(snapshot),
    )
    barrier = threading.Barrier(2)
    observed: dict[str, list[str]] = {"call-a": [], "call-b": []}

    def worker(tool_call_id: str) -> None:
        decision = image_runtime.build_capability_route_decision(
            "image_generation",
            snapshot=snapshot,
            route="provider",
            tool_call_id=tool_call_id,
        )
        with image_runtime.capability_route_event_scope(
            lambda *_args, **kwargs: observed[tool_call_id].append(
                str(kwargs["route_event"]["tool_call_id"])
            ),
            tool_call_id=tool_call_id,
        ):
            barrier.wait()
            assert image_runtime.emit_capability_route_event_at_provider_io(
                decision
            )
            assert not image_runtime.emit_capability_route_event_at_provider_io(
                decision
            )

    first = threading.Thread(target=worker, args=("call-a",))
    second = threading.Thread(target=worker, args=("call-b",))
    first.start()
    second.start()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert observed == {"call-a": ["call-a"], "call-b": ["call-b"]}
