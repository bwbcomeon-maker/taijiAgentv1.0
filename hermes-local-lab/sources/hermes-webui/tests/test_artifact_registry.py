import base64
import hashlib
import io
import json
import multiprocessing
import os
import queue
import struct
import threading
import time
import urllib.parse
import zlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
JPEG_2X2 = base64.b64decode(
    "/9j/4AAQSkZJRgABAgAAAQABAAD//gAQTGF2YzYyLjI4LjEwMQD/2wBDAAgEBAQEBAUFBQUF"
    "BQYGBgYGBgYGBgYGBgYHBwcICAgHBwcGBgcHCAgICAkJCQgICAgJCQoKCgwMCwsODg4RERT/"
    "xABMAAEBAAAAAAAAAAAAAAAAAAAABgEBAQAAAAAAAAAAAAAAAAAABgcQAQAAAAAAAAAAAAAAAA"
    "AAAAARAQAAAAAAAAAAAAAAAAAAAAD/wAARCAACAAIDASIAAhEAAxEA/9oADAMBAAIRAxEAPwCL"
    "AE1/f//Z"
)
GIF_1X1 = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
)
WEBP_1X1 = base64.b64decode(
    "UklGRh4AAABXRUJQVlA4TBEAAAAvAAAAAAfQ//73v/+BiOh/AAA="
)
JPEG_RESTART_420 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIf"
    "IiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7"
    "Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCAAQACADASIA"
    "AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAA"
    "F9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6"
    "Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqr"
    "KztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEB"
    "AQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcR"
    "MiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpj"
    "ZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyM"
    "nK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/90ABAAB/9oADAMBAAIRAxEAPwDlaKKK+dP2Y//"
    "Q5WiiivnT9mP/2Q=="
)


def _png_with_replaced_idat(payload: bytes) -> bytes:
    output = bytearray(PNG_1X1[:8])
    offset = 8
    while offset < len(PNG_1X1):
        length = int.from_bytes(PNG_1X1[offset:offset + 4], "big")
        kind = PNG_1X1[offset + 4:offset + 8]
        data = PNG_1X1[offset + 8:offset + 8 + length]
        if kind == b"IDAT":
            data = payload
        output += len(data).to_bytes(4, "big") + kind + data
        output += (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")
        offset += 12 + length
    return bytes(output)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        len(payload).to_bytes(4, "big")
        + kind
        + payload
        + (zlib.crc32(kind + payload) & 0xFFFFFFFF).to_bytes(4, "big")
    )


def _indexed_png(*, palette: bytes | None, palette_after_idat: bool = False) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 1, 3, 0, 0, 0)
    idat = _png_chunk(b"IDAT", zlib.compress(b"\x00\x00"))
    plte = _png_chunk(b"PLTE", palette) if palette is not None else b""
    chunks = idat + plte if palette_after_idat else plte + idat
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + chunks + _png_chunk(b"IEND", b"")


def _png_with_dimensions(width: int, height: int) -> bytes:
    output = bytearray(PNG_1X1[:8])
    offset = 8
    while offset < len(PNG_1X1):
        length = int.from_bytes(PNG_1X1[offset:offset + 4], "big")
        kind = PNG_1X1[offset + 4:offset + 8]
        payload = PNG_1X1[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            payload = struct.pack(">II", width, height) + payload[8:]
        output += _png_chunk(kind, payload)
        offset += 12 + length
    return bytes(output)


def _jpeg_with_dimensions(width: int, height: int) -> bytes:
    output = bytearray(JPEG_2X2)
    sof = output.index(b"\xff\xc0")
    output[sof + 5:sof + 7] = height.to_bytes(2, "big")
    output[sof + 7:sof + 9] = width.to_bytes(2, "big")
    return bytes(output)


def _multiprocess_register_worker(root, source, barrier, index):
    from threading import BrokenBarrierError
    from api.artifacts import ArtifactRegistry

    class BarrierRegistry(ArtifactRegistry):
        def _load_manifest(self, session_id):
            manifest = super()._load_manifest(session_id)
            try:
                barrier.wait(timeout=2)
            except BrokenBarrierError:
                pass
            return manifest

    BarrierRegistry(Path(root)).register_image_file(
        "same-session", f"turn-{index}", f"tool-{index}", Path(source)
    )


class _Handler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.body = io.BytesIO()
        self.wfile = self.body
        self.client_address = ("127.0.0.1", 12345)

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        return None


def test_registry_persists_atomic_manifest_and_public_projection(tmp_path):
    from api.artifacts import ArtifactRegistry

    source = tmp_path / "生成 图片.png"
    source.write_bytes(PNG_1X1)
    root = tmp_path / "web" / "artifacts"
    registry = ArtifactRegistry(root)

    public = registry.register_image_file(
        session_id="session-a",
        source_turn_id="turn-a",
        source_tool_call_id="tool-a",
        source_path=source,
        name=source.name,
    )

    assert set(public) == {
        "artifact_id", "kind", "mime", "name", "size", "sha256", "status"
    }
    assert public["name"] == "生成 图片.png"
    assert public["size"] == len(PNG_1X1)
    assert public["sha256"] == hashlib.sha256(PNG_1X1).hexdigest()
    assert "storage_path" not in public
    assert not list((root / "session-a").glob("*.tmp"))

    manifest = json.loads((root / "session-a" / "manifest.json").read_text("utf-8"))
    record = manifest["artifacts"][0]
    assert record["owner_session_id"] == "session-a"
    assert record["source_turn_id"] == "turn-a"
    assert record["source_tool_call_id"] == "tool-a"
    assert Path(record["storage_path"]).parent == root / "session-a"

    restarted = ArtifactRegistry(root)
    target = restarted.authorize("session-a", public["artifact_id"])
    assert target.read_bytes() == PNG_1X1
    with pytest.raises(PermissionError):
        restarted.authorize("session-b", public["artifact_id"])


def test_concurrent_registry_instances_do_not_lose_manifest_entries(tmp_path):
    from api.artifacts import ArtifactRegistry

    source = tmp_path / "x.png"
    source.write_bytes(PNG_1X1)
    root = tmp_path / "artifacts"

    def register(index):
        return ArtifactRegistry(root).register_image_file(
            "same-session", f"turn-{index}", f"tool-{index}", source
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(register, range(20)))

    manifest = json.loads((root / "same-session" / "manifest.json").read_text("utf-8"))
    assert len(manifest["artifacts"]) == 20
    assert {row["artifact_id"] for row in rows} == {
        row["artifact_id"] for row in manifest["artifacts"]
    }


def test_multiprocess_manifest_append_is_locked_and_leaves_no_orphans(tmp_path):
    source = tmp_path / "x.png"
    source.write_bytes(PNG_1X1)
    root = tmp_path / "artifacts"
    method = "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
    context = multiprocessing.get_context(method)
    process_count = 6
    barrier = context.Barrier(process_count)
    processes = [
        context.Process(
            target=_multiprocess_register_worker,
            args=(str(root), str(source), barrier, index),
        )
        for index in range(process_count)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    manifest = json.loads(
        (root / "same-session" / "manifest.json").read_text("utf-8")
    )
    records = manifest["artifacts"]
    files = [
        path for path in (root / "same-session").iterdir()
        if path.name != "manifest.json" and not path.name.startswith(".")
    ]
    assert len(records) == process_count
    assert len(files) == process_count
    assert {Path(row["storage_path"]).name for row in records} == {
        path.name for path in files
    }


def test_register_is_idempotent_for_same_session_turn_and_tool(tmp_path):
    from api.artifacts import ArtifactRegistry

    source = tmp_path / "x.png"
    source.write_bytes(PNG_1X1)
    registry = ArtifactRegistry(tmp_path / "artifacts")
    first = registry.register_image_file("session-a", "turn-a", "tool-a", source)
    second = registry.register_image_file("session-a", "turn-a", "tool-a", source)

    assert second == first
    manifest = json.loads(
        (tmp_path / "artifacts" / "session-a" / "manifest.json").read_text("utf-8")
    )
    assert len(manifest["artifacts"]) == 1


def test_compensation_tracks_only_new_artifacts_and_never_deletes_existing(tmp_path):
    from api.artifacts import ArtifactRegistry, ingest_image_artifact_candidates

    cache = tmp_path / "cache" / "images"
    cache.mkdir(parents=True)
    source = cache / "generated.png"
    source.write_bytes(PNG_1X1)
    registry = ArtifactRegistry(
        tmp_path / "artifacts", allowed_source_roots=[cache]
    )
    candidates = [{
        "tool_name": "image_generate",
        "tool_call_id": "tool-a",
        "structured_result": {
            "success": True,
            "image_ref": source.name,
            "sha256": hashlib.sha256(PNG_1X1).hexdigest(),
        },
    }]

    first, first_errors, first_created = ingest_image_artifact_candidates(
        registry,
        session_id="session-a",
        turn_id="turn-a",
        candidates=candidates,
        owner_run_id="run-a",
        return_created_ids=True,
    )
    registry.commit_artifacts(
        "session-a", first_created, owner_run_id="run-a"
    )
    second, second_errors, second_created = ingest_image_artifact_candidates(
        registry,
        session_id="session-a",
        turn_id="turn-a",
        candidates=candidates,
        owner_run_id="run-b",
        return_created_ids=True,
    )

    assert first_errors == second_errors == []
    assert first == second
    assert first_created == {first[0]["artifact_id"]}
    assert second_created == set()
    assert registry.discard_pending_artifacts(
        "session-a", second_created, owner_run_id="run-b"
    ) == 0
    assert registry.authorize("session-a", first[0]["artifact_id"]).data == PNG_1X1


def test_pending_owner_conflict_cleanup_then_retry_commit_is_safe(tmp_path):
    from api.artifacts import (
        ArtifactConflictError,
        ArtifactRegistry,
    )

    source = tmp_path / "x.png"
    source.write_bytes(PNG_1X1)
    registry = ArtifactRegistry(tmp_path / "artifacts")
    barrier = threading.Barrier(2)
    b_observed_conflict = threading.Event()
    a_cleaned = threading.Event()
    results = {}
    failures = []

    def owner_a():
        try:
            public, owned = registry.register_image_file(
                "session-a", "turn-a", "tool-a", source,
                owner_run_id="run-a", _include_pending_owner=True,
            )
            results["a"] = (public, owned)
            barrier.wait(timeout=5)
            assert b_observed_conflict.wait(timeout=5)
            assert registry.discard_pending_artifacts(
                "session-a", {public["artifact_id"]}, owner_run_id="run-a"
            ) == 1
            a_cleaned.set()
        except Exception as exc:
            failures.append(exc)

    def owner_b():
        try:
            barrier.wait(timeout=5)
            with pytest.raises(ArtifactConflictError):
                registry.register_image_file(
                    "session-a", "turn-a", "tool-a", source,
                    owner_run_id="run-b", _include_pending_owner=True,
                )
            b_observed_conflict.set()
            assert a_cleaned.wait(timeout=5)
            public, owned = registry.register_image_file(
                "session-a", "turn-a", "tool-a", source,
                owner_run_id="run-b", _include_pending_owner=True,
            )
            assert owned is True
            registry.commit_artifacts(
                "session-a", {public["artifact_id"]}, owner_run_id="run-b"
            )
            results["b"] = public
        except Exception as exc:
            failures.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda fn: fn(), (owner_a, owner_b)))

    assert failures == []
    assert registry.authorize(
        "session-a", results["b"]["artifact_id"]
    ).data == PNG_1X1


def test_committed_artifact_is_reused_and_old_owner_cannot_compensate_it(tmp_path):
    from api.artifacts import ArtifactRegistry

    source = tmp_path / "x.png"
    source.write_bytes(PNG_1X1)
    registry = ArtifactRegistry(tmp_path / "artifacts")
    first, first_owned = registry.register_image_file(
        "session-a", "turn-a", "tool-a", source,
        owner_run_id="run-a", _include_pending_owner=True,
    )
    assert first_owned is True
    registry.commit_artifacts(
        "session-a", {first["artifact_id"]}, owner_run_id="run-a"
    )

    second, second_owned = registry.register_image_file(
        "session-a", "turn-a", "tool-a", source,
        owner_run_id="run-b", _include_pending_owner=True,
    )

    assert second == first
    assert second_owned is False
    assert registry.discard_pending_artifacts(
        "session-a", {first["artifact_id"]}, owner_run_id="run-a"
    ) == 0
    assert registry.authorize("session-a", first["artifact_id"]).data == PNG_1X1


def test_same_owner_pending_retry_is_idempotent_and_committable(tmp_path):
    from api.artifacts import ArtifactRegistry

    source = tmp_path / "x.png"
    source.write_bytes(PNG_1X1)
    registry = ArtifactRegistry(tmp_path / "artifacts")
    first, first_owned = registry.register_image_file(
        "session-a", "turn-a", "tool-a", source,
        owner_run_id="run-a", _include_pending_owner=True,
    )
    second, second_owned = registry.register_image_file(
        "session-a", "turn-a", "tool-a", source,
        owner_run_id="run-a", _include_pending_owner=True,
    )

    assert first == second
    assert first_owned is second_owned is True
    registry.commit_artifacts(
        "session-a", {first["artifact_id"]}, owner_run_id="run-a"
    )
    assert registry.authorize("session-a", first["artifact_id"]).data == PNG_1X1


def test_commit_artifacts_is_all_or_nothing_for_missing_or_wrong_owner(tmp_path):
    from api.artifacts import ArtifactConflictError, ArtifactRegistry

    registry = ArtifactRegistry(tmp_path / "artifacts")
    first, _ = registry.register_image_bytes(
        "session-a", "turn-a", "tool-a", PNG_1X1,
        owner_run_id="run-a", _include_pending_owner=True,
    )
    second, _ = registry.register_image_bytes(
        "session-a", "turn-a", "tool-b", PNG_1X1,
        owner_run_id="run-a", _include_pending_owner=True,
    )
    manifest_path = tmp_path / "artifacts" / "session-a" / "manifest.json"

    with pytest.raises(ArtifactConflictError):
        registry.commit_artifacts(
            "session-a", {first["artifact_id"], "missing-id"}, owner_run_id="run-a"
        )
    with pytest.raises(ArtifactConflictError):
        registry.commit_artifacts(
            "session-a", {first["artifact_id"], second["artifact_id"]},
            owner_run_id="run-b",
        )

    states = {
        row["artifact_id"]: row["commit_state"]
        for row in json.loads(manifest_path.read_text("utf-8"))["artifacts"]
    }
    assert states == {
        first["artifact_id"]: "pending",
        second["artifact_id"]: "pending",
    }


def test_commit_manifest_failure_leaves_every_artifact_pending(tmp_path, monkeypatch):
    from api.artifacts import ArtifactRegistry

    registry = ArtifactRegistry(tmp_path / "artifacts")
    public, _ = registry.register_image_bytes(
        "session-a", "turn-a", "tool-a", PNG_1X1,
        owner_run_id="run-a", _include_pending_owner=True,
    )
    monkeypatch.setattr(
        registry, "_save_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("manifest failed")),
    )

    with pytest.raises(OSError, match="manifest failed"):
        registry.commit_artifacts(
            "session-a", {public["artifact_id"]}, owner_run_id="run-a"
        )

    manifest = json.loads(
        (tmp_path / "artifacts" / "session-a" / "manifest.json").read_text("utf-8")
    )
    assert manifest["artifacts"][0]["commit_state"] == "pending"


def test_registry_rejects_corrupt_oversize_and_symlink_images(tmp_path):
    from api.artifacts import ArtifactRegistry, ArtifactValidationError

    registry = ArtifactRegistry(tmp_path / "artifacts", max_bytes=64)
    corrupt = tmp_path / "bad.png"
    corrupt.write_bytes(b"not an image")
    with pytest.raises(ArtifactValidationError):
        registry.register_image_file("s", "t", "c", corrupt)

    oversize = tmp_path / "huge.png"
    oversize.write_bytes(PNG_1X1 + b"x" * 100)
    with pytest.raises(ArtifactValidationError):
        registry.register_image_file("s", "t", "c", oversize)

    source = tmp_path / "real.png"
    source.write_bytes(PNG_1X1)
    link = tmp_path / "link.png"
    link.symlink_to(source)
    with pytest.raises(ArtifactValidationError):
        registry.register_image_file("s", "t", "c", link)


@pytest.mark.parametrize("payload", [PNG_1X1, JPEG_2X2])
def test_image_validation_accepts_complete_decodable_fixtures(payload):
    from api.artifacts import validate_image_bytes

    assert validate_image_bytes(payload)[2:] == (1, 1) or validate_image_bytes(payload)[2:] == (2, 2)


@pytest.mark.parametrize("payload", [GIF_1X1, WEBP_1X1])
def test_image_validation_rejects_formats_without_portable_pixel_decoder(payload):
    from api.artifacts import ArtifactValidationError, validate_image_bytes

    with pytest.raises(ArtifactValidationError, match="unsupported"):
        validate_image_bytes(payload)


@pytest.mark.parametrize(
    "payload",
    [
        _png_with_replaced_idat(b"CRC-correct-but-not-zlib"),
        JPEG_2X2[:-3] + JPEG_2X2[-2:],
    ],
)
def test_image_validation_rejects_invalid_pixel_streams(payload):
    from api.artifacts import ArtifactValidationError, validate_image_bytes

    with pytest.raises(ArtifactValidationError):
        validate_image_bytes(payload)


def test_png_indexed_color_requires_valid_palette_before_idat():
    from api.artifacts import ArtifactValidationError, validate_image_bytes

    assert validate_image_bytes(_indexed_png(palette=b"\x00\x00\x00\xff\xff\xff"))[2:] == (1, 1)
    for payload in (
        _indexed_png(palette=None),
        _indexed_png(palette=b"\x00\x00\x00\xff\xff\xff", palette_after_idat=True),
        _indexed_png(palette=b"\x00\x00\x00" * 3),
        _indexed_png(palette=b"\x00\x00"),
    ):
        with pytest.raises(ArtifactValidationError):
            validate_image_bytes(payload)


def test_jpeg_requires_complete_nonzero_dqt_and_valid_component_selector():
    from api.artifacts import ArtifactValidationError, validate_image_bytes

    dqt = JPEG_2X2.index(b"\xff\xdb")
    sof = JPEG_2X2.index(b"\xff\xc0")
    missing = JPEG_2X2[:dqt] + b"\xff\xfe" + JPEG_2X2[dqt + 2:]
    truncated = bytearray(JPEG_2X2)
    length = int.from_bytes(truncated[dqt + 2:dqt + 4], "big")
    truncated[dqt + 2:dqt + 4] = (length - 1).to_bytes(2, "big")
    zero_value = bytearray(JPEG_2X2)
    zero_value[dqt + 5] = 0
    bad_selector = bytearray(JPEG_2X2)
    bad_selector[sof + 12] = 4
    for payload in (missing, bytes(truncated), bytes(zero_value), bytes(bad_selector)):
        with pytest.raises(ArtifactValidationError):
            validate_image_bytes(payload)


def test_webui_rejects_dimensions_before_png_inflate_or_jpeg_entropy(monkeypatch):
    import api.artifacts as artifacts

    monkeypatch.setattr(
        artifacts.zlib, "decompressobj",
        lambda: (_ for _ in ()).throw(AssertionError("inflater must not run")),
    )
    with pytest.raises(artifacts.ArtifactValidationError, match="dimensions"):
        artifacts.validate_image_bytes(
            _png_with_dimensions(65_535, 65_535),
            max_pixels=1_000_000,
            max_decoded_bytes=4_000_000,
        )
    monkeypatch.setattr(
        artifacts, "_decode_jpeg_entropy_segment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("entropy decoder must not run")
        ),
    )
    with pytest.raises(artifacts.ArtifactValidationError, match="dimensions"):
        artifacts.validate_image_bytes(
            _jpeg_with_dimensions(65_535, 65_535),
            max_pixels=1_000_000,
            max_decoded_bytes=4_000_000,
        )


def test_webui_rejects_decoded_byte_limit_before_decoders(monkeypatch):
    import api.artifacts as artifacts

    monkeypatch.setattr(
        artifacts.zlib, "decompressobj",
        lambda: (_ for _ in ()).throw(AssertionError("inflater must not run")),
    )
    with pytest.raises(artifacts.ArtifactValidationError, match="decoded"):
        artifacts.validate_image_bytes(
            _png_with_dimensions(100, 100),
            max_pixels=20_000,
            max_decoded_bytes=100,
        )
    monkeypatch.setattr(
        artifacts, "_decode_jpeg_entropy_segment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("entropy decoder must not run")
        ),
    )
    with pytest.raises(artifacts.ArtifactValidationError, match="decoded"):
        artifacts.validate_image_bytes(
            JPEG_2X2, max_pixels=100, max_decoded_bytes=4
        )


def test_jpeg_validation_handles_420_sampling_and_restart_markers():
    from api.artifacts import validate_image_bytes

    assert b"\xff\xdd" in JPEG_RESTART_420
    assert b"\xff\xd0" in JPEG_RESTART_420
    assert validate_image_bytes(JPEG_RESTART_420)[2:] == (32, 16)


@pytest.mark.parametrize(
    "payload",
    [
        JPEG_RESTART_420.replace(b"\xff\xc0", b"\xff\xc2", 1),
        JPEG_RESTART_420.replace(b"\xff\xd0", b"\xff\xd1", 1),
    ],
)
def test_jpeg_validation_rejects_progressive_and_bad_restart_sequence(payload):
    from api.artifacts import ArtifactValidationError, validate_image_bytes

    with pytest.raises(ArtifactValidationError):
        validate_image_bytes(payload)


@pytest.mark.parametrize(
    "payload",
    [
        PNG_1X1[:-12],
        PNG_1X1[:-1] + bytes([PNG_1X1[-1] ^ 1]),
        JPEG_2X2[:-2],
        GIF_1X1[:-1],
        WEBP_1X1[:-1],
    ],
)
def test_image_validation_rejects_truncated_or_corrupt_structures(payload):
    from api.artifacts import ArtifactValidationError, validate_image_bytes

    with pytest.raises(ArtifactValidationError):
        validate_image_bytes(payload)


def test_register_reads_verified_source_from_same_nofollow_descriptor(
    tmp_path, monkeypatch
):
    from api.artifacts import ArtifactRegistry

    source = tmp_path / "source.png"
    evil = tmp_path / "evil.png"
    source.write_bytes(PNG_1X1)
    evil.write_bytes(PNG_1X1 + b"SWAPPED")
    original_read_bytes = Path.read_bytes

    def swap_before_path_reopen(path):
        if path == source:
            source.unlink()
            source.symlink_to(evil)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", swap_before_path_reopen)
    registry = ArtifactRegistry(tmp_path / "artifacts")
    public = registry.register_image_file("session-a", "turn-a", "tool-a", source)
    manifest = json.loads(
        (tmp_path / "artifacts" / "session-a" / "manifest.json").read_text("utf-8")
    )
    assert public["sha256"] == hashlib.sha256(PNG_1X1).hexdigest()
    assert manifest["artifacts"][0]["size"] == len(PNG_1X1)


def test_registry_retires_session_for_seven_days_then_cleans(tmp_path):
    from api.artifacts import ArtifactRegistry

    source = tmp_path / "x.png"
    source.write_bytes(PNG_1X1)
    registry = ArtifactRegistry(tmp_path / "artifacts")
    public = registry.register_image_file("session-a", "turn-a", "tool-a", source)

    retired = registry.retire_session("session-a", now=1000.0)
    assert retired is not None and retired.exists()
    with pytest.raises(FileNotFoundError):
        registry.authorize("session-a", public["artifact_id"])
    assert registry.cleanup_retired(now=1000.0 + 6 * 86400) == 0
    assert retired.exists()
    assert registry.cleanup_retired(now=1000.0 + 8 * 86400) == 1
    assert not retired.exists()


def test_retire_metadata_failure_moves_session_back_atomically(tmp_path, monkeypatch):
    import api.artifacts as artifacts

    source = tmp_path / "x.png"
    source.write_bytes(PNG_1X1)
    registry = artifacts.ArtifactRegistry(tmp_path / "artifacts")
    public = registry.register_image_file("session-a", "turn-a", "tool-a", source)
    original_atomic_write = artifacts._atomic_write

    def fail_retire_metadata(path, data):
        if path.name == ".retired.json":
            raise OSError("retire metadata fsync failed")
        return original_atomic_write(path, data)

    monkeypatch.setattr(artifacts, "_atomic_write", fail_retire_metadata)
    with pytest.raises(OSError, match="metadata"):
        registry.retire_session("session-a")

    assert (tmp_path / "artifacts" / "session-a").exists()
    assert not list((tmp_path / "artifacts" / ".trash").glob("session-a--*"))
    assert registry.authorize("session-a", public["artifact_id"]).read_bytes() == PNG_1X1


def test_registered_artifact_survives_25_hour_cache_cleanup(tmp_path, monkeypatch):
    from api.artifacts import ArtifactRegistry
    from gateway.platforms import base

    cache = tmp_path / "cache" / "images"
    cache.mkdir(parents=True)
    source = cache / "old image.png"
    source.write_bytes(PNG_1X1)
    old = time.time() - 25 * 3600
    os.utime(source, (old, old))
    registry = ArtifactRegistry(
        tmp_path / "artifacts", allowed_source_roots=[cache]
    )
    public = registry.register_image_file("s", "t", "c", source)
    monkeypatch.setattr(base, "IMAGE_CACHE_DIR", cache)

    assert base.cleanup_image_cache(max_age_hours=24) == 1
    assert not source.exists()
    assert registry.authorize("s", public["artifact_id"]).read_bytes() == PNG_1X1


def test_authorize_rejects_missing_corrupt_traversal_and_symlink_targets(tmp_path):
    from api.artifacts import ArtifactRegistry, ArtifactValidationError

    source = tmp_path / "source.png"
    source.write_bytes(PNG_1X1)
    registry = ArtifactRegistry(tmp_path / "artifacts")
    first = registry.register_image_file("s", "t", "c", source)
    target = registry.authorize("s", first["artifact_id"])
    target.unlink()
    with pytest.raises(FileNotFoundError):
        registry.authorize("s", first["artifact_id"])

    second = registry.register_image_file("s", "t2", "c2", source)
    target = registry.authorize("s", second["artifact_id"])
    target.write_bytes(b"corrupt")
    with pytest.raises(FileNotFoundError):
        registry.authorize("s", second["artifact_id"])

    with pytest.raises(ArtifactValidationError):
        registry.authorize("s", "../manifest.json")

    third = registry.register_image_file("s", "t3", "c3", source)
    target = registry.authorize("s", third["artifact_id"])
    target.unlink()
    target.symlink_to(source)
    with pytest.raises(PermissionError):
        registry.authorize("s", third["artifact_id"])


def test_media_endpoint_authorizes_session_and_artifact_without_path(tmp_path):
    from api import routes
    from api.artifacts import ArtifactRegistry

    root = tmp_path / "artifacts"
    source = tmp_path / "x.png"
    source.write_bytes(PNG_1X1)
    registry = ArtifactRegistry(root)
    public = registry.register_image_file("session-a", "turn-a", "tool-a", source)

    with mock.patch.object(routes, "_artifact_registry", return_value=registry), \
         mock.patch("api.auth.is_auth_enabled", lambda: False):
        ok = _Handler()
        routes._handle_media(ok, SimpleNamespace(
            path="/api/media",
            query=urllib.parse.urlencode({
                "session_id": "session-a", "artifact_id": public["artifact_id"]
            }),
        ))
        assert ok.status == 200
        assert ok.body.getvalue() == PNG_1X1

        wrong = _Handler()
        routes._handle_media(wrong, SimpleNamespace(
            path="/api/media",
            query=urllib.parse.urlencode({
                "session_id": "session-b", "artifact_id": public["artifact_id"]
            }),
        ))
        assert wrong.status in {403, 404}
        assert str(source) not in wrong.body.getvalue().decode("utf-8", errors="replace")


@pytest.mark.parametrize(
    ("download_value", "expected_disposition"),
    [(None, "inline"), ("0", "inline"), ("1", "attachment")],
)
def test_artifact_media_download_flag_controls_content_disposition(
    tmp_path, download_value, expected_disposition
):
    from api import routes
    from api.artifacts import ArtifactRegistry

    root = tmp_path / "artifacts"
    source = tmp_path / "本地验收图片.png"
    source.write_bytes(PNG_1X1)
    registry = ArtifactRegistry(root)
    public = registry.register_image_file("session-a", "turn-a", "tool-a", source)
    query = {"session_id": "session-a", "artifact_id": public["artifact_id"]}
    if download_value is not None:
        query["download"] = download_value

    with mock.patch.object(routes, "_artifact_registry", return_value=registry), \
         mock.patch("api.auth.is_auth_enabled", lambda: False):
        handler = _Handler()
        routes._handle_media(handler, SimpleNamespace(
            path="/api/media",
            query=urllib.parse.urlencode(query),
        ))

    assert handler.status == 200
    assert handler.body.getvalue() == PNG_1X1
    disposition = handler.headers["Content-Disposition"]
    assert disposition.startswith(expected_disposition + ";")
    assert "filename*=UTF-8''" in disposition


def test_media_route_serves_same_verified_bytes_when_path_is_swapped(
    tmp_path, monkeypatch
):
    from api import routes
    from api.artifacts import ArtifactRegistry

    root = tmp_path / "artifacts"
    source = tmp_path / "x.png"
    evil = tmp_path / "evil.png"
    source.write_bytes(PNG_1X1)
    evil.write_bytes(PNG_1X1 + b"SWAPPED-AFTER-AUTH")
    registry = ArtifactRegistry(root)
    public = registry.register_image_file("session-a", "turn-a", "tool-a", source)
    original_authorize = registry.authorize

    def authorize_then_swap(session_id, artifact_id):
        authorized = original_authorize(session_id, artifact_id)
        manifest = json.loads(
            (root / "session-a" / "manifest.json").read_text("utf-8")
        )
        target = Path(manifest["artifacts"][0]["storage_path"])
        target.unlink()
        target.symlink_to(evil)
        return authorized

    monkeypatch.setattr(registry, "authorize", authorize_then_swap)
    with mock.patch.object(routes, "_artifact_registry", return_value=registry), \
         mock.patch("api.auth.is_auth_enabled", lambda: False):
        handler = _Handler()
        routes._handle_media(handler, SimpleNamespace(
            path="/api/media",
            query=urllib.parse.urlencode({
                "session_id": "session-a", "artifact_id": public["artifact_id"]
            }),
        ))

    assert handler.status == 200
    assert handler.body.getvalue() == PNG_1X1


@pytest.mark.parametrize(
    ("command", "range_header", "status", "expected"),
    [
        ("GET", "bytes=0-7", 206, PNG_1X1[:8]),
        ("HEAD", "", 200, b""),
        ("GET", "bytes=9999-10000", 416, b""),
    ],
)
def test_verified_artifact_bytes_preserve_range_and_head_contract(
    command, range_header, status, expected
):
    from api import routes

    handler = _Handler()
    handler.command = command
    if range_header:
        handler.headers["Range"] = range_header

    routes._serve_verified_artifact_bytes(
        handler,
        PNG_1X1,
        "image/png",
        "image.png",
        "private, max-age=3600",
    )

    assert handler.status == status
    assert handler.body.getvalue() == expected
    if status == 206:
        assert handler.headers["Content-Range"] == f"bytes 0-7/{len(PNG_1X1)}"
        assert handler.headers["Content-Length"] == "8"
    elif status == 200:
        assert handler.headers["Content-Length"] == str(len(PNG_1X1))
    else:
        assert handler.headers["Content-Range"] == f"bytes */{len(PNG_1X1)}"


def test_public_message_keeps_only_safe_artifact_fields():
    from api.brand_privacy import public_message_projection

    projected = public_message_projection({
        "role": "assistant",
        "content": "done",
        "artifacts": [{
            "artifact_id": "art-1",
            "kind": "image",
            "mime": "image/png",
            "name": "图片.png",
            "size": 10,
            "sha256": "a" * 64,
            "status": "ready",
            "storage_path": "/Users/secret/runtime/image.png",
            "source_tool_call_id": "raw-tool",
        }],
    }, session_id="s")

    assert projected["artifacts"] == [{
        "artifact_id": "art-1",
        "kind": "image",
        "mime": "image/png",
        "name": "图片.png",
        "size": 10,
        "sha256": "a" * 64,
        "status": "ready",
    }]
    assert "/Users/secret" not in json.dumps(projected, ensure_ascii=False)


def test_image_tool_result_becomes_durable_artifact_candidate(tmp_path):
    from api.artifacts import ArtifactRegistry, ingest_image_tool_result

    source = tmp_path / "cache" / "image with space.png"
    source.parent.mkdir()
    source.write_bytes(PNG_1X1)
    result = json.dumps({"success": True, "image": str(source), "provider": "test"})

    artifacts = ingest_image_tool_result(
        ArtifactRegistry(tmp_path / "artifacts", allowed_source_roots=[source.parent]),
        session_id="session-a",
        turn_id="turn-a",
        tool_call_id="call-a",
        tool_name="image_generate",
        structured_result=result,
    )
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "image"
    assert "path" not in artifacts[0]


def test_opaque_image_ref_hash_is_verified_from_same_descriptor(tmp_path):
    from api.artifacts import (
        ArtifactRegistry,
        ArtifactValidationError,
        ingest_image_tool_result,
    )

    cache = tmp_path / "cache"
    cache.mkdir()
    source = cache / "generated.png"
    original_hash = hashlib.sha256(PNG_1X1).hexdigest()
    replacement = _png_with_replaced_idat(zlib.compress(b"\x00\xff\xff"))
    source.write_bytes(replacement)
    registry = ArtifactRegistry(
        tmp_path / "artifacts", allowed_source_roots=[cache]
    )

    with pytest.raises(ArtifactValidationError, match="hash"):
        ingest_image_tool_result(
            registry,
            session_id="session-a",
            turn_id="turn-a",
            tool_call_id="call-a",
            tool_name="image_generate",
            structured_result={
                "success": True,
                "image_ref": source.name,
                "sha256": original_hash,
            },
        )
    assert not list((tmp_path / "artifacts").glob("*/manifest.json"))


def test_imported_media_text_does_not_mint_legacy_path_authorization(tmp_path):
    from api import routes

    outside = tmp_path / "outside" / "forged.png"
    outside.parent.mkdir()
    outside.write_bytes(PNG_1X1)
    imported = SimpleNamespace(messages=[{
        "role": "assistant", "content": f"MEDIA:{outside}", "imported": True
    }])
    with mock.patch.object(routes, "get_session", return_value=imported):
        assert not routes._session_media_token_allows_image_path(
            "imported-session", outside, {"image/png"}
        )


def test_legacy_json_export_is_text_only_and_never_leaks_artifact_storage():
    from api.brand_privacy import scrub_public_export_payload

    storage_path = "/Users/secret/runtime-home/web/artifacts/session-a/image.png"
    exported = scrub_public_export_payload({
        "session_id": "session-a",
        "messages": [{
            "role": "assistant",
            "content": "generated",
            "artifacts": [{
                "artifact_id": "artifact-secret",
                "kind": "image",
                "mime": "image/png",
                "name": "image.png",
                "size": len(PNG_1X1),
                "sha256": hashlib.sha256(PNG_1X1).hexdigest(),
                "status": "ready",
                "storage_path": storage_path,
                "source_tool_call_id": "tool-secret",
            }],
        }],
    })

    serialized = json.dumps(exported, ensure_ascii=False)
    assert exported["messages"] == [{"role": "assistant", "content": "generated"}]
    assert "artifacts" not in serialized
    assert "artifact-secret" not in serialized
    assert "tool-secret" not in serialized
    assert storage_path not in serialized


def test_legacy_json_import_strips_artifacts_and_persists_untrusted_origin(
    tmp_path, monkeypatch
):
    import api.models as models
    import api.routes as routes

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    sessions = OrderedDict()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", sessions)
    monkeypatch.setattr(routes, "SESSIONS", sessions)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda _path: tmp_path)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, **_kwargs: payload)
    monkeypatch.setattr(routes, "_persist_new_session_truth", lambda session: session.save())

    forged_path = "/Users/secret/runtime-home/cache/images/forged.png"
    response = routes._handle_session_import(object(), {
        "workspace": str(tmp_path),
        "messages": [{
            "role": "assistant",
            "content": f"MEDIA:{forged_path}",
            "artifacts": [{
                "artifact_id": "forged-artifact",
                "storage_path": forged_path,
            }],
        }],
    })

    session_id = response["session"]["session_id"]
    reloaded = models.Session.load(session_id)
    assert reloaded is not None
    assert reloaded.legacy_import is True
    assert reloaded.messages[0]["_legacy_imported"] is True
    assert "artifacts" not in reloaded.messages[0]
    assert not routes._session_media_token_allows_image_path(
        session_id, Path(forged_path), {"image/png"}
    )


@pytest.mark.parametrize(
    "encoded",
    [
        "A" * 48,
        "AAAA=AAA",
    ],
)
def test_data_uri_rejects_oversize_or_invalid_padding_before_decode(
    encoded, tmp_path, monkeypatch
):
    import api.artifacts as artifacts

    registry = artifacts.ArtifactRegistry(tmp_path / "artifacts", max_bytes=16)
    decode_calls = []

    def should_not_decode(*_args, **_kwargs):
        decode_calls.append(True)
        raise AssertionError("oversize or malformed base64 reached decoder")

    monkeypatch.setattr(artifacts.base64, "b64decode", should_not_decode)
    with pytest.raises(artifacts.ArtifactValidationError):
        artifacts.ingest_image_tool_result(
            registry,
            session_id="session-a",
            turn_id="turn-a",
            tool_call_id="tool-a",
            tool_name="image_generate",
            structured_result={
                "success": True,
                "image": f"data:image/png;base64,{encoded}",
            },
        )
    assert decode_calls == []


def test_data_uri_at_valid_size_decodes_and_registers(tmp_path):
    from api.artifacts import ArtifactRegistry, ingest_image_tool_result

    registry = ArtifactRegistry(
        tmp_path / "artifacts",
        max_bytes=len(PNG_1X1),
    )
    encoded = base64.b64encode(PNG_1X1).decode("ascii")
    rows = ingest_image_tool_result(
        registry,
        session_id="session-a",
        turn_id="turn-a",
        tool_call_id="tool-a",
        tool_name="image_generate",
        structured_result={
            "success": True,
            "image": f"data:image/png;base64,{encoded}",
        },
    )

    assert len(rows) == 1
    assert registry.authorize("session-a", rows[0]["artifact_id"]).read_bytes() == PNG_1X1


def _run_legacy_image_turn(
    monkeypatch,
    tmp_path,
    *,
    callback_mode="structured",
    result_success=True,
    cancel_after_tool=False,
    failure_mode=None,
):
    import api.config as config
    import api.models as models
    import api.profiles as profiles
    import api.streaming as streaming
    from api.models import Session
    from api.turn_envelope import TurnEnvelope

    session_dir = tmp_path / "sessions"
    session_dir.mkdir(exist_ok=True)
    sessions = OrderedDict()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "SESSIONS", sessions, raising=False)
    monkeypatch.setattr(config, "SESSION_DIR", session_dir, raising=False)
    monkeypatch.setattr(config, "SESSION_INDEX_FILE", session_dir / "_index.json", raising=False)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "web", raising=False)
    monkeypatch.setattr(streaming, "SESSION_DIR", session_dir, raising=False)
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path, raising=False)
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda _profile: tmp_path)
    monkeypatch.setattr(models, "_active_state_db_path", lambda: tmp_path / "state.db", raising=False)
    monkeypatch.setenv("TAIJI_RUNTIME_HOME", str(tmp_path))
    config.STREAMS.clear()
    config.CANCEL_FLAGS.clear()
    config.AGENT_INSTANCES.clear()
    config.SESSION_AGENT_LOCKS.clear()
    config.SESSION_AGENT_CACHE.clear()

    sid = (
        f"legacy-artifact-{callback_mode}-{int(result_success)}-"
        f"{int(cancel_after_tool)}-{failure_mode or 'ok'}"
    )
    stream_id = f"stream-{sid}"
    turn_id = f"turn-{sid}"
    source = tmp_path / "cache" / "images" / "generated.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(PNG_1X1)
    tool_result = json.dumps({
        "success": bool(result_success),
        "image": str(source),
    })
    session = Session(
        session_id=sid,
        workspace=str(tmp_path),
        model="test-model",
        active_stream_id=stream_id,
        pending_user_message="generate an image",
        pending_started_at=1.0,
    )
    session.save(touch_updated_at=False)
    sessions[sid] = session
    if failure_mode == "commit":
        from api.artifacts import ArtifactRegistry

        monkeypatch.setattr(
            ArtifactRegistry,
            "commit_artifacts",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("commit failed")),
        )
    elif failure_mode == "save":
        original_save = Session.save

        def fail_artifact_save(self, *args, **kwargs):
            if any(message.get("artifacts") for message in self.messages):
                raise OSError("session save failed")
            return original_save(self, *args, **kwargs)

        monkeypatch.setattr(Session, "save", fail_artifact_save)

    class StructuredCallbackAgent:
        def __init__(
            self,
            tool_progress_callback=None,
            tool_start_callback=None,
            tool_complete_callback=None,
            **_kwargs,
        ):
            self.session_id = sid
            self.context_compressor = None
            self.ephemeral_system_prompt = None
            self.tool_progress_callback = tool_progress_callback
            self.tool_start_callback = tool_start_callback
            self.tool_complete_callback = tool_complete_callback

        def run_conversation(self, **kwargs):
            if self.tool_start_callback:
                self.tool_start_callback("image-call", "image_generate", {"prompt": "private"})
            if self.tool_progress_callback:
                self.tool_progress_callback(
                    "tool.completed", "image_generate", None, None,
                    result=tool_result, tool_call_id="image-call",
                    is_error=not result_success,
                )
            if self.tool_complete_callback:
                self.tool_complete_callback(
                    "image-call", "image_generate", {"prompt": "private"}, tool_result
                )
            if cancel_after_tool:
                config.CANCEL_FLAGS[stream_id].set()
            return {
                "completed": True,
                "messages": [
                    {"role": "user", "content": kwargs["persist_user_message"]},
                    {"role": "assistant", "content": "image generated"},
                ],
            }

    class ProgressCallbackAgent:
        def __init__(self, tool_progress_callback=None, **_kwargs):
            self.session_id = sid
            self.context_compressor = None
            self.ephemeral_system_prompt = None
            self.tool_progress_callback = tool_progress_callback

        def run_conversation(self, **kwargs):
            self.tool_progress_callback(
                "tool.started", "image_generate", None, {"prompt": "private"}
            )
            self.tool_progress_callback(
                "tool.completed", "image_generate", None, None,
                result=tool_result, tool_call_id="image-call",
                is_error=not result_success,
            )
            if cancel_after_tool:
                config.CANCEL_FLAGS[stream_id].set()
            return {
                "completed": True,
                "messages": [
                    {"role": "user", "content": kwargs["persist_user_message"]},
                    {"role": "assistant", "content": "image generated"},
                ],
            }

    agent_class = (
        StructuredCallbackAgent if callback_mode == "structured" else ProgressCallbackAgent
    )
    monkeypatch.setattr(streaming, "_get_ai_agent", lambda: agent_class)
    monkeypatch.setattr(
        streaming, "resolve_model_provider",
        lambda *args, **kwargs: ("test-model", None, None),
    )
    monkeypatch.setattr(streaming, "get_config", lambda: {})
    monkeypatch.setattr(config, "get_config", lambda: {})
    monkeypatch.setattr(config, "_resolve_cli_toolsets", lambda *args, **kwargs: [])
    event_queue = queue.Queue()
    config.STREAMS[stream_id] = event_queue
    envelope = TurnEnvelope.create(
        turn_id=turn_id,
        session_id=sid,
        submitted_at=1.0,
        display_user_message="generate an image",
        model_messages=[],
        attachments=[],
    )
    try:
        streaming._run_agent_streaming(
            session_id=sid,
            msg_text="generate an image",
            model="test-model",
            workspace=str(tmp_path),
            stream_id=stream_id,
            attachments=[],
            turn_envelope=envelope,
        )
    finally:
        config.STREAMS.pop(stream_id, None)
    return Session.load(sid), tmp_path / "web" / "artifacts", event_queue


@pytest.mark.requires_agent_modules
@pytest.mark.parametrize("callback_mode", ["structured", "progress"])
def test_legacy_streaming_promotes_image_callback_without_media(
    callback_mode, monkeypatch, tmp_path
):
    saved, artifact_root, events = _run_legacy_image_turn(
        monkeypatch, tmp_path, callback_mode=callback_mode
    )

    assistant = next(
        message for message in reversed(saved.messages) if message.get("role") == "assistant"
    )
    assert assistant["content"] == "image generated"
    assert "MEDIA:" not in assistant["content"]
    assert len(assistant["artifacts"]) == 1
    artifact = assistant["artifacts"][0]
    assert set(artifact) == {
        "artifact_id", "kind", "mime", "name", "size", "sha256", "status"
    }
    manifest = json.loads(
        (artifact_root / saved.session_id / "manifest.json").read_text("utf-8")
    )
    assert len(manifest["artifacts"]) == 1
    assert manifest["artifacts"][0]["source_tool_call_id"] == "image-call"
    assert str(tmp_path / "cache" / "images") not in json.dumps(
        list(events.queue), ensure_ascii=False
    )

    from api.artifacts import ArtifactRegistry
    from gateway.platforms import base

    cache_source = tmp_path / "cache" / "images" / "generated.png"
    old = time.time() - 25 * 3600
    os.utime(cache_source, (old, old))
    monkeypatch.setattr(base, "IMAGE_CACHE_DIR", cache_source.parent)
    assert base.cleanup_image_cache(max_age_hours=24) == 1
    assert not cache_source.exists()
    restarted = ArtifactRegistry(artifact_root)
    assert restarted.authorize(saved.session_id, artifact["artifact_id"]).read_bytes() == PNG_1X1


@pytest.mark.requires_agent_modules
@pytest.mark.parametrize(
    ("result_success", "cancel_after_tool"),
    [(False, False), (True, True)],
)
def test_legacy_streaming_failure_or_cancel_does_not_register_image(
    result_success, cancel_after_tool, monkeypatch, tmp_path
):
    saved, artifact_root, _events = _run_legacy_image_turn(
        monkeypatch,
        tmp_path,
        result_success=result_success,
        cancel_after_tool=cancel_after_tool,
    )

    assert not list(artifact_root.glob("*/manifest.json"))
    if saved is not None:
        assert all(not message.get("artifacts") for message in saved.messages)


@pytest.mark.requires_agent_modules
@pytest.mark.parametrize("failure_mode", ["commit", "save"])
def test_legacy_artifact_commit_and_session_save_have_safe_failure_order(
    failure_mode, monkeypatch, tmp_path
):
    import api.models as models

    saved, artifact_root, _events = _run_legacy_image_turn(
        monkeypatch, tmp_path, failure_mode=failure_mode
    )

    cached = models.SESSIONS.get(saved.session_id)
    assert all(not message.get("artifacts") for message in saved.messages)
    assert cached is None or all(not message.get("artifacts") for message in cached.messages)
    manifest = json.loads(
        (artifact_root / saved.session_id / "manifest.json").read_text("utf-8")
    )
    if failure_mode == "commit":
        assert manifest["artifacts"] == []
    else:
        assert len(manifest["artifacts"]) == 1
        assert manifest["artifacts"][0]["commit_state"] == "committed"


@pytest.mark.parametrize("operation", ["clear", "delete"])
def test_clear_and_delete_routes_move_registered_artifacts_to_seven_day_trash(
    operation, monkeypatch, tmp_path
):
    import api.config as config
    import api.models as models
    import api.routes as routes
    from api.artifacts import ArtifactRegistry

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    sessions = OrderedDict()
    for module in (config, models, routes):
        monkeypatch.setattr(module, "SESSION_DIR", session_dir, raising=False)
        monkeypatch.setattr(module, "SESSION_INDEX_FILE", session_dir / "_index.json", raising=False)
        monkeypatch.setattr(module, "SESSIONS", sessions, raising=False)
    state_dir = tmp_path / "web"
    monkeypatch.setattr(routes, "STATE_DIR", state_dir)
    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setattr(config, "_evict_session_agent", lambda _sid: None)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **_kwargs: True,
    )

    sid = f"artifact-route-{operation}"
    session = models.Session(
        session_id=sid,
        workspace=str(tmp_path),
        messages=[
            {"role": "user", "content": "generate"},
            {"role": "assistant", "content": "done"},
        ],
        context_messages=[
            {"role": "user", "content": "generate"},
            {"role": "assistant", "content": "done"},
        ],
    )
    session.save(skip_index=True)
    sessions[sid] = session
    source = tmp_path / "source.png"
    source.write_bytes(PNG_1X1)
    registry = ArtifactRegistry(state_dir / "artifacts")
    public = registry.register_image_file(sid, "turn-a", "tool-a", source)

    payload = json.dumps({"session_id": sid}).encode("utf-8")
    captured = {}
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, response, status=200, **_kwargs: captured.update(
            response=response, status=status
        ),
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: captured.update(
            response={"error": str(message)}, status=status
        ),
    )
    handler = SimpleNamespace(
        headers={"Content-Length": str(len(payload))},
        rfile=io.BytesIO(payload),
    )
    routes.handle_post(handler, SimpleNamespace(path=f"/api/session/{operation}"))

    assert captured["status"] == 200
    assert not (state_dir / "artifacts" / sid).exists()
    trash_entries = list((state_dir / "artifacts" / ".trash").iterdir())
    assert len(trash_entries) == 1
    retired_meta = json.loads((trash_entries[0] / ".retired.json").read_text("utf-8"))
    restarted = ArtifactRegistry(state_dir / "artifacts")
    with pytest.raises(FileNotFoundError):
        restarted.authorize(sid, public["artifact_id"])
    retired_at = float(retired_meta["retired_at"])
    assert restarted.cleanup_retired(now=retired_at + 6 * 86400) == 0
    assert trash_entries[0].exists()
    assert restarted.cleanup_retired(now=retired_at + 8 * 86400) == 1
    assert not trash_entries[0].exists()


@pytest.mark.parametrize("operation", ["clear", "delete"])
def test_clear_and_delete_retire_failure_returns_500_without_mutating_session(
    operation, monkeypatch, tmp_path
):
    import api.config as config
    import api.models as models
    import api.routes as routes
    from api.artifacts import ArtifactRegistry

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    sessions = OrderedDict()
    for module in (config, models, routes):
        monkeypatch.setattr(module, "SESSION_DIR", session_dir, raising=False)
        monkeypatch.setattr(module, "SESSION_INDEX_FILE", session_dir / "_index.json", raising=False)
        monkeypatch.setattr(module, "SESSIONS", sessions, raising=False)
    monkeypatch.setattr(config, "_evict_session_agent", lambda _sid: None)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(
        "api.state_sync.replace_webui_session_messages",
        lambda **_kwargs: True,
    )

    sid = f"artifact-retire-failure-{operation}"
    original_messages = [
        {"role": "user", "content": "generate"},
        {"role": "assistant", "content": "done"},
    ]
    session = models.Session(
        session_id=sid,
        workspace=str(tmp_path),
        messages=list(original_messages),
        context_messages=list(original_messages),
    )
    session.save(skip_index=True)
    sessions[sid] = session
    source = tmp_path / "source.png"
    source.write_bytes(PNG_1X1)
    registry = ArtifactRegistry(tmp_path / "artifacts")
    public = registry.register_image_file(sid, "turn-a", "tool-a", source)
    monkeypatch.setattr(
        registry,
        "retire_session",
        lambda _sid: (_ for _ in ()).throw(OSError("trash unavailable")),
    )
    monkeypatch.setattr(routes, "_artifact_registry", lambda: registry)

    payload = json.dumps({"session_id": sid}).encode("utf-8")
    captured = {}
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, response, status=200, **_kwargs: captured.update(
            response=response, status=status
        ),
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: captured.update(
            response={"error": str(message)}, status=status
        ),
    )
    routes.handle_post(
        SimpleNamespace(
            headers={"Content-Length": str(len(payload))},
            rfile=io.BytesIO(payload),
        ),
        SimpleNamespace(path=f"/api/session/{operation}"),
    )

    assert captured["status"] == 500
    reloaded = models.Session.load(sid)
    assert reloaded is not None
    assert reloaded.messages == original_messages
    assert sessions[sid].messages == original_messages
    assert registry.authorize(sid, public["artifact_id"]).read_bytes() == PNG_1X1
