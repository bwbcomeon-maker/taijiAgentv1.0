import json
import os
import socket
import threading
from pathlib import Path

import pytest

from agent.transports import image_generation_gate_bridge as gate_module
from agent.transports.image_generation_gate_bridge import (
    ImageGenerationGateBridge,
    consume_image_generation_gate_lease,
)


def _consume(bridge):
    return consume_image_generation_gate_lease(
        path=bridge.path,
        bridge_id=bridge.bridge_id,
        public_key=bridge.public_key,
    )


def _raw_authorization(bridge, challenge):
    return gate_module._exchange_with_broker(
        socket_path=Path(bridge.path) / "broker.sock",
        request={
            "schema_version": 4,
            "action": "authorize_image_generation",
            "bridge_id": bridge.bridge_id,
            "challenge": challenge,
        },
    )


def _replace_broker_with_one_response(bridge, response):
    socket_path = Path(bridge.path) / "broker.sock"
    socket_path.unlink()
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    replacement.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    replacement.listen(1)
    ready = threading.Event()

    def serve():
        ready.set()
        connection, _address = replacement.accept()
        with connection:
            connection.recv(8192)
            connection.sendall(
                json.dumps(response, separators=(",", ":")).encode()
                + b"\n"
            )
        replacement.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    assert ready.wait(timeout=1)
    return worker


def test_cancelled_session_cannot_remove_or_claim_next_session_authorization():
    old_bridge = ImageGenerationGateBridge()
    new_bridge = ImageGenerationGateBridge()
    try:
        old_turn = old_bridge.arm(
            task_id="session-1",
            turn_id="turn-old",
            owner_token="stream-old",
            allow_generation=True,
            ttl_seconds=60,
        )
        new_turn = new_bridge.arm(
            task_id="session-1",
            turn_id="turn-new",
            owner_token="stream-new",
            allow_generation=True,
            ttl_seconds=60,
        )
        old_bridge.disarm(old_turn)

        stale, stale_error = _consume(old_bridge)
        assert stale is None
        assert stale_error == "image_generation_not_requested"

        # Models the old worker finally block running after the replacement
        # session has armed its own independent broker.
        old_bridge.disarm(old_turn)
        current, current_error = _consume(new_bridge)
        assert current_error is None
        assert current is not None
        assert current.handle == new_turn.handle
        assert current.turn_id == "turn-new"
        assert current.owner_token == "stream-new"
    finally:
        old_bridge.close()
        new_bridge.close()


def test_denied_turn_never_creates_file_authorization():
    bridge = ImageGenerationGateBridge()
    try:
        with pytest.raises(ValueError, match="must not create"):
            bridge.arm(
                task_id="session-1",
                turn_id="turn-denied",
                owner_token="stream-denied",
                allow_generation=False,
                ttl_seconds=60,
            )

        root = Path(bridge.path)
        assert list(root.glob("*.json")) == []
        assert {path.name for path in root.iterdir()} == {"broker.sock"}
        denied, error = _consume(bridge)
        assert denied is None
        assert error == "image_generation_not_requested"
    finally:
        bridge.close()


def test_unused_allow_authorization_cannot_replay_after_disarm():
    bridge = ImageGenerationGateBridge()
    try:
        old_turn = bridge.arm(
            task_id="session-1",
            turn_id="turn-old-unused",
            owner_token="stream-old",
            allow_generation=True,
            ttl_seconds=60,
        )
        # There is no copyable lease/active file. Revocation changes only
        # trusted parent memory and the epoch used in signed live responses.
        assert list(Path(bridge.path).glob("*.json")) == []
        bridge.disarm(old_turn)

        replay, replay_error = _consume(bridge)
        assert replay is None
        assert replay_error == "image_generation_not_requested"
    finally:
        bridge.close()


def test_parent_broker_single_consume_survives_client_marker_deletion():
    bridge = ImageGenerationGateBridge()
    try:
        bridge.arm(
            task_id="session-1",
            turn_id="turn-current",
            owner_token="stream-current",
            allow_generation=True,
            ttl_seconds=60,
        )
        first, first_error = _consume(bridge)
        assert first_error is None
        assert first is not None

        # No client-side claim file exists to delete. The authoritative
        # consumed bit lives in the parent process.
        assert list(Path(bridge.path).glob("*.claimed")) == []
        duplicate, duplicate_error = _consume(bridge)
        assert duplicate is None
        assert duplicate_error == "duplicate_generation_this_turn"
    finally:
        bridge.close()


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("allow_generation", False),
        ("turn_id", "turn-forged"),
        ("expires_at", 4_102_444_800.0),
    ],
)
def test_tampered_live_response_fails_signature(
    monkeypatch,
    field,
    forged_value,
):
    bridge = ImageGenerationGateBridge()
    try:
        challenge = "a" * 64
        bridge.arm(
            task_id="session-1",
            turn_id="turn-current",
            owner_token="stream-current",
            allow_generation=True,
            ttl_seconds=60,
        )
        response = _raw_authorization(bridge, challenge)
        response[field] = forged_value
        worker = _replace_broker_with_one_response(bridge, response)
        monkeypatch.setattr(
            gate_module.secrets,
            "token_hex",
            lambda _size: challenge,
        )

        forged, forged_error = _consume(bridge)
        worker.join(timeout=1)

        assert forged is None
        assert forged_error == "image_generation_gate_bridge_insecure"
    finally:
        bridge.close()


def test_saved_signed_response_cannot_replay_for_new_mcp_challenge(monkeypatch):
    bridge = ImageGenerationGateBridge()
    try:
        old_challenge = "a" * 64
        bridge.arm(
            task_id="session-1",
            turn_id="turn-old",
            owner_token="stream-old",
            allow_generation=True,
            ttl_seconds=60,
        )
        old_response = _raw_authorization(bridge, old_challenge)
        bridge.disarm(old_response["handle"])
        worker = _replace_broker_with_one_response(bridge, old_response)
        monkeypatch.setattr(
            gate_module.secrets,
            "token_hex",
            lambda _size: "b" * 64,
        )

        replay, replay_error = _consume(bridge)
        worker.join(timeout=1)

        assert replay is None
        assert replay_error == "image_generation_gate_bridge_insecure"
    finally:
        bridge.close()


def test_old_session_response_and_socket_cannot_authorize_new_session(
    monkeypatch,
):
    old_bridge = ImageGenerationGateBridge()
    new_bridge = ImageGenerationGateBridge()
    try:
        challenge = "a" * 64
        old_bridge.arm(
            task_id="session-1",
            turn_id="turn-old",
            owner_token="stream-old",
            allow_generation=True,
            ttl_seconds=60,
        )
        old_response = _raw_authorization(old_bridge, challenge)
        new_bridge.arm(
            task_id="session-1",
            turn_id="turn-new",
            owner_token="stream-new",
            allow_generation=True,
            ttl_seconds=60,
        )
        worker = _replace_broker_with_one_response(
            new_bridge,
            old_response,
        )
        monkeypatch.setattr(
            gate_module.secrets,
            "token_hex",
            lambda _size: challenge,
        )

        injected, injected_error = _consume(new_bridge)
        worker.join(timeout=1)

        assert injected is None
        assert injected_error == "image_generation_gate_bridge_insecure"
    finally:
        old_bridge.close()
        new_bridge.close()


def test_handle_specific_disarm_leaves_newer_authorization_intact():
    bridge = ImageGenerationGateBridge()
    try:
        old_turn = bridge.arm(
            task_id="session-1",
            turn_id="turn-old",
            owner_token="stream-old",
            allow_generation=True,
            ttl_seconds=60,
        )
        new_turn = bridge.arm(
            task_id="session-1",
            turn_id="turn-new",
            owner_token="stream-new",
            allow_generation=True,
            ttl_seconds=60,
        )

        bridge.disarm(old_turn)
        lease, error = _consume(bridge)
        assert error is None
        assert lease is not None
        assert lease.handle == new_turn.handle
    finally:
        bridge.close()
