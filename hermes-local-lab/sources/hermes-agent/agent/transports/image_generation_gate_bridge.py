"""Live parent-process image-generation authorization for Codex MCP tools.

Codex app-server launches ``hermes-tools`` in a separate MCP process.  A
filesystem lease is not an authorization boundary against another process
running as the same OS user: that process can copy and restore an unused,
still-valid lease after the parent has revoked the turn.

This bridge therefore keeps the authoritative turn state and Ed25519 private
key only in the trusted parent process.  The MCP child sends an internally
generated random challenge to a local Unix socket for every image call.  The
parent atomically consumes the active turn and signs a response bound to the
bridge, challenge, generation epoch, owner, and a very short expiry.  The MCP
child receives only the socket path, bridge id, and non-secret public key.
None of the capability identity enters model input or a public tool schema.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import secrets
import socket
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


IMAGE_GENERATION_GATE_BRIDGE_ENV = (
    "HERMES_IMAGE_GENERATION_GATE_BRIDGE"
)
IMAGE_GENERATION_GATE_BRIDGE_ID_ENV = (
    "HERMES_IMAGE_GENERATION_GATE_BRIDGE_ID"
)
IMAGE_GENERATION_GATE_PUBLIC_KEY_ENV = (
    "HERMES_IMAGE_GENERATION_GATE_PUBLIC_KEY"
)

_BRIDGE_SCHEMA_VERSION = 4
_BROKER_ACTION = "authorize_image_generation"
_BROKER_SOCKET_FILENAME = "broker.sock"
_MAX_BROKER_BYTES = 8 * 1024
_BROKER_IO_TIMEOUT_SECONDS = 1.0
_BROKER_RESPONSE_TTL_SECONDS = 1.0
_HANDLE_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_CHALLENGE_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_FIELDS = {
    "schema_version",
    "action",
    "bridge_id",
    "challenge",
}
_SIGNED_RESPONSE_FIELDS = (
    "schema_version",
    "bridge_id",
    "challenge",
    "response_id",
    "status",
    "epoch",
    "handle",
    "task_id",
    "turn_id",
    "owner_token",
    "allow_generation",
    "issued_at",
    "expires_at",
)
_VALID_RESPONSE_STATUSES = {
    "authorized",
    "denied",
    "duplicate",
    "expired",
}
_CONSUMED_RESPONSES: set[tuple[str, str, str]] = set()
_CONSUMED_RESPONSES_LOCK = threading.Lock()


@dataclass(frozen=True)
class ImageGenerationGateLease:
    """One verified, live-broker authorization consumed by the MCP child."""

    bridge_id: str
    handle: str
    lease_id: str
    epoch: int
    task_id: str
    turn_id: str
    owner_token: str
    allow_generation: bool
    expires_at: float


@dataclass(frozen=True)
class ImageGenerationGateTurnHandle:
    """Opaque identity used by the trusted parent for handle-specific revoke."""

    handle: str
    epoch: int
    task_id: str
    turn_id: str
    owner_token: str


@dataclass
class _ActiveTurn:
    handle: str
    epoch: int
    task_id: str
    turn_id: str
    owner_token: str
    expires_at: float
    consumed: bool = False


class ImageGenerationGateBridge:
    """Own a live, single-use authorization broker for one Codex session."""

    def __init__(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="hermes-image-gate-"))
        os.chmod(root, 0o700)
        self._root = root
        self._bridge_id = uuid.uuid4().hex
        self._private_key = Ed25519PrivateKey.generate()
        public_key_bytes = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._public_key = base64.b64encode(public_key_bytes).decode("ascii")
        self._socket_path = root / _BROKER_SOCKET_FILENAME
        self._state_lock = threading.Lock()
        self._active: _ActiveTurn | None = None
        self._epoch = 0
        self._closed = False
        self._stop_event = threading.Event()
        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._server_socket.bind(str(self._socket_path))
            os.chmod(self._socket_path, 0o600)
            self._server_socket.listen(8)
            self._server_socket.settimeout(0.1)
        except Exception:
            self._server_socket.close()
            try:
                self._socket_path.unlink()
            except FileNotFoundError:
                pass
            self._root.rmdir()
            raise
        self._server_thread = threading.Thread(
            target=self._serve,
            name=f"image-gate-{self._bridge_id[:8]}",
            daemon=True,
        )
        self._server_thread.start()

    @property
    def path(self) -> str:
        return str(self._root)

    @property
    def bridge_id(self) -> str:
        return self._bridge_id

    @property
    def public_key(self) -> str:
        """Return the non-secret verifier pinned in the MCP child."""
        return self._public_key

    @property
    def env(self) -> dict[str, str]:
        return {
            IMAGE_GENERATION_GATE_BRIDGE_ENV: self.path,
            IMAGE_GENERATION_GATE_BRIDGE_ID_ENV: self.bridge_id,
            IMAGE_GENERATION_GATE_PUBLIC_KEY_ENV: self.public_key,
        }

    def arm(
        self,
        *,
        task_id: str,
        turn_id: str,
        owner_token: str,
        allow_generation: bool,
        ttl_seconds: float,
    ) -> ImageGenerationGateTurnHandle:
        """Publish one in-memory allow generation; denied turns publish none."""
        if not allow_generation:
            raise ValueError(
                "denied image turns must not create an authorization"
            )
        normalized_task = str(task_id or "").strip()
        normalized_turn = str(turn_id or "").strip()
        normalized_owner = str(owner_token or "").strip()
        if not normalized_task or not normalized_turn or not normalized_owner:
            raise ValueError("image generation gate identity is required")
        ttl = float(ttl_seconds)
        if not math.isfinite(ttl):
            raise ValueError("image generation gate TTL must be finite")

        with self._state_lock:
            if self._closed:
                raise RuntimeError("image generation gate bridge is closed")
            self._epoch += 1
            handle = uuid.uuid4().hex
            active = _ActiveTurn(
                handle=handle,
                epoch=self._epoch,
                task_id=normalized_task,
                turn_id=normalized_turn,
                owner_token=normalized_owner,
                expires_at=time.time() + ttl,
            )
            self._active = active
        return ImageGenerationGateTurnHandle(
            handle=handle,
            epoch=active.epoch,
            task_id=normalized_task,
            turn_id=normalized_turn,
            owner_token=normalized_owner,
        )

    def disarm(
        self,
        turn_handle: ImageGenerationGateTurnHandle | str,
    ) -> None:
        """Revoke only the named generation; a stale cleanup cannot clear new."""
        handle = (
            turn_handle.handle
            if isinstance(turn_handle, ImageGenerationGateTurnHandle)
            else str(turn_handle or "").strip()
        )
        if not _HANDLE_PATTERN.fullmatch(handle):
            return
        with self._state_lock:
            if self._active is not None and self._active.handle == handle:
                self._epoch += 1
                self._active = None

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._epoch += 1
            self._active = None
        self._stop_event.set()
        self._server_socket.close()
        self._server_thread.join(timeout=2.0)
        try:
            self._socket_path.unlink()
        except FileNotFoundError:
            pass
        except IsADirectoryError:
            try:
                self._socket_path.rmdir()
            except OSError:
                pass
        except OSError:
            pass
        try:
            for leftover in self._root.iterdir():
                try:
                    leftover.unlink()
                except IsADirectoryError:
                    try:
                        leftover.rmdir()
                    except OSError:
                        pass
                except OSError:
                    pass
        except FileNotFoundError:
            return
        try:
            self._root.rmdir()
        except OSError:
            pass

    def _serve(self) -> None:
        while not self._stop_event.is_set():
            try:
                connection, _address = self._server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(_BROKER_IO_TIMEOUT_SECONDS)
                try:
                    request = _receive_bounded_json(connection)
                    response = self._authorize_request(request)
                    connection.sendall(
                        _encode_broker_message(response)
                    )
                except (OSError, TypeError, ValueError):
                    # Malformed or interrupted callers get no signed material.
                    continue

    def _authorize_request(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if set(request) != _REQUEST_FIELDS:
            raise ValueError("image gate broker request fields are invalid")
        challenge = str(request.get("challenge") or "").strip()
        if (
            request.get("schema_version") != _BRIDGE_SCHEMA_VERSION
            or request.get("action") != _BROKER_ACTION
            or request.get("bridge_id") != self._bridge_id
            or not _CHALLENGE_PATTERN.fullmatch(challenge)
        ):
            raise ValueError("image gate broker request is invalid")

        now = time.time()
        with self._state_lock:
            active = self._active
            if self._closed or active is None:
                status = "denied"
                active = None
            elif active.expires_at <= now:
                self._epoch += 1
                self._active = None
                status = "expired"
                active = None
            elif active.consumed:
                status = "duplicate"
            else:
                # This is the single authoritative consume. It lives in the
                # parent, so deleting a client-side marker or restarting MCP
                # cannot re-open the turn.
                active.consumed = True
                status = "authorized"

            response = {
                "schema_version": _BRIDGE_SCHEMA_VERSION,
                "bridge_id": self._bridge_id,
                "challenge": challenge,
                "response_id": uuid.uuid4().hex,
                "status": status,
                "epoch": active.epoch if active is not None else self._epoch,
                "handle": active.handle if active is not None else "",
                "task_id": active.task_id if active is not None else "",
                "turn_id": active.turn_id if active is not None else "",
                "owner_token": (
                    active.owner_token if active is not None else ""
                ),
                "allow_generation": status == "authorized",
                "issued_at": now,
                "expires_at": (
                    min(
                        now + _BROKER_RESPONSE_TTL_SECONDS,
                        active.expires_at,
                    )
                    if status == "authorized" and active is not None
                    else now + _BROKER_RESPONSE_TTL_SECONDS
                ),
            }
            response["signature"] = base64.b64encode(
                self._private_key.sign(
                    _canonical_broker_response(response)
                )
            ).decode("ascii")
            return response


def _owned_with_exact_mode(path_stat: os.stat_result, mode: int) -> bool:
    if hasattr(os, "getuid") and path_stat.st_uid != os.getuid():
        return False
    return stat.S_IMODE(path_stat.st_mode) == mode


def _validate_private_broker_path(root: Path) -> Path:
    if not root.is_absolute():
        raise ValueError("bridge path must be absolute")
    root_stat = root.lstat()
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or not _owned_with_exact_mode(root_stat, 0o700)
    ):
        raise PermissionError("bridge directory is not private")
    socket_path = root / _BROKER_SOCKET_FILENAME
    socket_stat = socket_path.lstat()
    if (
        not stat.S_ISSOCK(socket_stat.st_mode)
        or not _owned_with_exact_mode(socket_stat, 0o600)
    ):
        raise PermissionError("bridge broker is not a private socket")
    return socket_path


def _encode_broker_message(value: dict[str, Any]) -> bytes:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > _MAX_BROKER_BYTES:
        raise ValueError("image gate broker message is too large")
    return payload + b"\n"


def _receive_bounded_json(connection: socket.socket) -> dict[str, Any]:
    payload = bytearray()
    while len(payload) <= _MAX_BROKER_BYTES:
        chunk = connection.recv(min(4096, _MAX_BROKER_BYTES + 1))
        if not chunk:
            break
        payload.extend(chunk)
        if b"\n" in chunk:
            break
    if len(payload) > _MAX_BROKER_BYTES:
        raise ValueError("image gate broker message is too large")
    line = bytes(payload).split(b"\n", 1)[0]
    raw: Any = json.loads(line.decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("image gate broker message is invalid")
    return raw


def _canonical_broker_response(value: dict[str, Any]) -> bytes:
    signed_value = {
        field: value[field]
        for field in _SIGNED_RESPONSE_FIELDS
    }
    return json.dumps(
        signed_value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _decode_public_key(value: str) -> tuple[str, Ed25519PublicKey]:
    try:
        raw = base64.b64decode(str(value).encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError("bridge public key is invalid") from exc
    if len(raw) != 32:
        raise ValueError("bridge public key is invalid")
    normalized = base64.b64encode(raw).decode("ascii")
    return normalized, Ed25519PublicKey.from_public_bytes(raw)


def _exchange_with_broker(
    *,
    socket_path: Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(_BROKER_IO_TIMEOUT_SECONDS)
    try:
        client.connect(str(socket_path))
        client.sendall(_encode_broker_message(request))
        return _receive_bounded_json(client)
    finally:
        client.close()


def _validate_broker_response(
    raw: dict[str, Any],
    *,
    expected_bridge_id: str,
    expected_challenge: str,
    verification_key: Ed25519PublicKey,
    now: float,
) -> tuple[ImageGenerationGateLease | None, str | None]:
    if set(raw) != {*_SIGNED_RESPONSE_FIELDS, "signature"}:
        raise ValueError("image gate broker response fields are invalid")
    signature_value = raw.get("signature")
    try:
        signature = base64.b64decode(
            str(signature_value).encode("ascii"),
            validate=True,
        )
        if len(signature) != 64:
            raise ValueError("image gate broker signature is invalid")
        verification_key.verify(
            signature,
            _canonical_broker_response(raw),
        )
    except (
        InvalidSignature,
        UnicodeEncodeError,
        ValueError,
    ) as exc:
        raise ValueError("image gate broker signature is invalid") from exc

    bridge_id = str(raw.get("bridge_id") or "").strip()
    challenge = str(raw.get("challenge") or "").strip()
    response_id = str(raw.get("response_id") or "").strip()
    status = str(raw.get("status") or "").strip()
    handle = str(raw.get("handle") or "").strip()
    task_id = str(raw.get("task_id") or "").strip()
    turn_id = str(raw.get("turn_id") or "").strip()
    owner_token = str(raw.get("owner_token") or "").strip()
    epoch = raw.get("epoch")
    issued_at_raw = raw.get("issued_at")
    expires_at_raw = raw.get("expires_at")
    allow_generation = raw.get("allow_generation")
    if (
        raw.get("schema_version") != _BRIDGE_SCHEMA_VERSION
        or bridge_id != expected_bridge_id
        or challenge != expected_challenge
        or not _HANDLE_PATTERN.fullmatch(response_id)
        or status not in _VALID_RESPONSE_STATUSES
        or isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or epoch < 0
        or isinstance(issued_at_raw, bool)
        or isinstance(expires_at_raw, bool)
        or not isinstance(allow_generation, bool)
    ):
        raise ValueError("image gate broker response is invalid")
    issued_at = float(issued_at_raw)
    expires_at = float(expires_at_raw)
    if (
        not math.isfinite(issued_at)
        or not math.isfinite(expires_at)
        or expires_at <= issued_at
        or expires_at - issued_at
        > _BROKER_RESPONSE_TTL_SECONDS + 0.05
        or expires_at <= now
    ):
        return None, "image_generation_gate_bridge_expired"

    if status == "duplicate":
        return None, "duplicate_generation_this_turn"
    if status == "expired":
        return None, "image_generation_gate_bridge_expired"
    if status != "authorized":
        return None, "image_generation_not_requested"
    if (
        not allow_generation
        or not _HANDLE_PATTERN.fullmatch(handle)
        or not task_id
        or not turn_id
        or not owner_token
    ):
        raise ValueError("image gate broker authorization is invalid")

    return (
        ImageGenerationGateLease(
            bridge_id=bridge_id,
            handle=handle,
            lease_id=response_id,
            epoch=epoch,
            task_id=task_id,
            turn_id=turn_id,
            owner_token=owner_token,
            allow_generation=True,
            expires_at=expires_at,
        ),
        None,
    )


def consume_image_generation_gate_lease(
    *,
    path: str | None = None,
    bridge_id: str | None = None,
    public_key: str | None = None,
    now: float | None = None,
) -> tuple[ImageGenerationGateLease | None, str | None]:
    """Request and single-consume the parent's current live authorization."""
    raw_path = str(
        path
        if path is not None
        else os.environ.get(IMAGE_GENERATION_GATE_BRIDGE_ENV, "")
    ).strip()
    if not raw_path:
        return None, "image_generation_gate_bridge_missing"
    expected_bridge_id = str(
        bridge_id
        if bridge_id is not None
        else os.environ.get(IMAGE_GENERATION_GATE_BRIDGE_ID_ENV, "")
    ).strip()
    raw_public_key = str(
        public_key
        if public_key is not None
        else os.environ.get(IMAGE_GENERATION_GATE_PUBLIC_KEY_ENV, "")
    ).strip()
    if (
        not _HANDLE_PATTERN.fullmatch(expected_bridge_id)
        or not raw_public_key
    ):
        return None, "image_generation_gate_bridge_insecure"

    try:
        normalized_public_key, verification_key = _decode_public_key(
            raw_public_key
        )
        socket_path = _validate_private_broker_path(Path(raw_path))
        challenge = secrets.token_hex(32)
        response = _exchange_with_broker(
            socket_path=socket_path,
            request={
                "schema_version": _BRIDGE_SCHEMA_VERSION,
                "action": _BROKER_ACTION,
                "bridge_id": expected_bridge_id,
                "challenge": challenge,
            },
        )
        current_time = time.time() if now is None else float(now)
        lease, error = _validate_broker_response(
            response,
            expected_bridge_id=expected_bridge_id,
            expected_challenge=challenge,
            verification_key=verification_key,
            now=current_time,
        )
    except FileNotFoundError:
        return None, "image_generation_gate_bridge_missing"
    except (OSError, TypeError, ValueError):
        return None, "image_generation_gate_bridge_insecure"
    if error is not None or lease is None:
        return None, error or "image_generation_gate_bridge_insecure"

    consumed_key = (
        normalized_public_key,
        lease.bridge_id,
        lease.lease_id,
    )
    with _CONSUMED_RESPONSES_LOCK:
        if consumed_key in _CONSUMED_RESPONSES:
            return None, "duplicate_generation_this_turn"
        _CONSUMED_RESPONSES.add(consumed_key)
    return lease, None


__all__ = [
    "IMAGE_GENERATION_GATE_BRIDGE_ENV",
    "IMAGE_GENERATION_GATE_BRIDGE_ID_ENV",
    "IMAGE_GENERATION_GATE_PUBLIC_KEY_ENV",
    "ImageGenerationGateBridge",
    "ImageGenerationGateLease",
    "ImageGenerationGateTurnHandle",
    "consume_image_generation_gate_lease",
]
