from __future__ import annotations

import base64
import hashlib
import os
import socket
import stat
import struct
import zlib
from pathlib import Path

import pytest


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
JPEG_2PX = base64.b64decode(
    "/9j/4AAQSkZJRgABAgAAAQABAAD//gAQTGF2YzYyLjI4LjEwMQD/2wBDAAgEBAQEBAUFBQUF"
    "BQYGBgYGBgYGBgYGBgYHBwcICAgHBwcGBgcHCAgICAkJCQgICAgJCQoKCgwMCwsODg4RERT/"
    "xABMAAEBAAAAAAAAAAAAAAAAAAAABgEBAQAAAAAAAAAAAAAAAAAABgcQAQAAAAAAAAAAAAAAAA"
    "AAAAARAQAAAAAAAAAAAAAAAAAAAAD/wAARCAACAAIDASIAAhEAAxEA/9oADAMBAAIRAxEAPwCL"
    "AE1/f//Z"
)
GIF_1PX = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")
WEBP_1PX = base64.b64decode("UklGRh4AAABXRUJQVlA4TBEAAAAvAAAAAAfQ//73v/+BiOh/AAA=")
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
    output = bytearray(PNG_1PX[:8])
    offset = 8
    while offset < len(PNG_1PX):
        length = int.from_bytes(PNG_1PX[offset:offset + 4], "big")
        kind = PNG_1PX[offset + 4:offset + 8]
        data = PNG_1PX[offset + 8:offset + 8 + length]
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
    output = bytearray(PNG_1PX[:8])
    offset = 8
    while offset < len(PNG_1PX):
        length = int.from_bytes(PNG_1PX[offset:offset + 4], "big")
        kind = PNG_1PX[offset + 4:offset + 8]
        payload = PNG_1PX[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            payload = struct.pack(">II", width, height) + payload[8:]
        output += _png_chunk(kind, payload)
        offset += 12 + length
    return bytes(output)


def _jpeg_with_dimensions(width: int, height: int) -> bytes:
    output = bytearray(JPEG_2PX)
    sof = output.index(b"\xff\xc0")
    output[sof + 5:sof + 7] = height.to_bytes(2, "big")
    output[sof + 7:sof + 9] = width.to_bytes(2, "big")
    return bytes(output)


class _Response:
    def __init__(self, *, status=200, headers=None, body=PNG_1PX):
        self.status_code = status
        self.headers = headers or {"Content-Type": "image/png"}
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code), response=self)

    def iter_content(self, chunk_size=65536):
        del chunk_size
        yield self._body

    def close(self):
        return None


def _public_resolver(host, port, *args, **kwargs):
    del host, args, kwargs
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


@pytest.mark.parametrize("blocked_ip", [
    "127.0.0.1", "10.0.0.8", "169.254.169.254", "::1", "fc00::1",
])
def test_remote_image_url_blocks_private_and_metadata_addresses(tmp_path, monkeypatch, blocked_ip):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from agent.image_gen_provider import save_url_image

    calls = []

    def resolver(host, port, *args, **kwargs):
        del host, args, kwargs
        family = socket.AF_INET6 if ":" in blocked_ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (blocked_ip, port))]

    def request_get(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("network must not be reached for a blocked address")

    with pytest.raises(ValueError, match="unsafe image URL"):
        save_url_image(
            "https://images.example.test/a.png",
            resolver=resolver,
            request_get=request_get,
        )
    assert calls == []


def test_remote_image_redirect_is_revalidated_before_following(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from agent.image_gen_provider import save_url_image

    calls = []

    def resolver(host, port, *args, **kwargs):
        del args, kwargs
        ip = "127.0.0.1" if host == "internal.test" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    def request_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(status=302, headers={"Location": "http://internal.test/secret.png"})

    with pytest.raises(ValueError, match="unsafe image URL"):
        save_url_image(
            "https://images.example.test/start",
            resolver=resolver,
            request_get=request_get,
        )
    assert len(calls) == 1
    assert calls[0][1].get("allow_redirects") is False


def test_remote_image_rejects_bad_scheme_credentials_decimal_and_mixed_dns(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from agent.image_gen_provider import save_url_image

    def mixed_resolver(host, port, *args, **kwargs):
        del host, args, kwargs
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.10.10.10", port)),
        ]

    for url in (
        "file:///tmp/image.png",
        "https://user:password@images.example.test/a.png",
        "http://2130706433/a.png",
        "http://[::1]/a.png",
        "https://images.example.test/a.png",
    ):
        with pytest.raises(ValueError, match="unsafe image URL"):
            save_url_image(
                url,
                resolver=mixed_resolver,
                request_get=lambda *_args, **_kwargs: pytest.fail("network reached"),
            )


def test_remote_image_follows_relative_redirect_after_each_safe_check(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from agent.image_gen_provider import save_url_image

    calls = []

    def request_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/start"):
            return _Response(status=302, headers={"Location": "/final.png"})
        return _Response()

    path = save_url_image(
        "https://images.example.test/start",
        resolver=_public_resolver,
        request_get=request_get,
    )
    assert path.read_bytes() == PNG_1PX
    assert [url for url, _kwargs in calls] == [
        "https://images.example.test/start",
        "https://images.example.test/final.png",
    ]


def test_remote_image_rejects_mime_magic_mismatch_without_partial_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from agent.image_gen_provider import save_url_image, _images_cache_dir

    def request_get(url, **kwargs):
        del url, kwargs
        return _Response(headers={"Content-Type": "image/png"}, body=b"<html>not image</html>")

    with pytest.raises(ValueError, match="image format"):
        save_url_image(
            "https://images.example.test/a.png",
            resolver=_public_resolver,
            request_get=request_get,
        )
    assert not list(_images_cache_dir().glob("*"))


def test_base64_image_enforces_decode_size_magic_and_dimensions(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from agent.image_gen_provider import save_b64_image

    with pytest.raises(ValueError, match="base64"):
        save_b64_image("%%%", max_bytes=1024)
    with pytest.raises(ValueError, match="exceeds"):
        save_b64_image(base64.b64encode(PNG_1PX).decode(), max_bytes=8)
    with pytest.raises(ValueError, match="image format"):
        save_b64_image(base64.b64encode(b"not an image").decode(), max_bytes=1024)

    path = save_b64_image(
        base64.b64encode(PNG_1PX).decode(), max_bytes=1024, max_pixels=4
    )
    assert path.read_bytes() == PNG_1PX


def test_gateway_image_envelope_rejects_url_base64_and_non_cache_paths(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from gateway.platforms.api_server import _structured_tool_result_for_gateway

    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_1PX)
    for image in (
        "https://secret.example/image.png?token=CANARY",
        "data:image/png;base64," + base64.b64encode(PNG_1PX).decode("ascii"),
        str(outside),
    ):
        assert _structured_tool_result_for_gateway(
            "image_generate", {"success": True, "image": image}
        ) is None


def test_gateway_image_envelope_preserves_tool_bound_name_and_digest(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from gateway.platforms.api_server import _structured_tool_result_for_gateway

    image = tmp_path / "home" / "cache" / "images" / "generated.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG_1PX)
    for structured_result in (
        {
            "success": True,
            "image": str(image),
            "image_ref": "different.png",
            "sha256": hashlib.sha256(PNG_1PX).hexdigest(),
        },
        {
            "success": True,
            "image": str(image),
            "image_ref": image.name,
            "sha256": "0" * 64,
        },
    ):
        assert (
            _structured_tool_result_for_gateway(
                "image_generate",
                structured_result,
            )
            is None
        )
    digest = hashlib.sha256(PNG_1PX).hexdigest()
    assert _structured_tool_result_for_gateway(
        "image_generate",
        {
            "success": True,
            "image": str(image),
            "image_ref": image.name,
            "sha256": digest,
        },
    ) == {
        "success": True,
        "image_ref": image.name,
        "sha256": digest,
    }


def test_image_cache_files_are_private_and_parent_directory_is_fsynced(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    import agent.image_gen_provider as provider

    fsync_kinds: list[str] = []
    real_fsync = os.fsync

    def observing_fsync(descriptor: int):
        mode = os.fstat(descriptor).st_mode
        fsync_kinds.append(
            "directory" if stat.S_ISDIR(mode) else "regular"
        )
        return real_fsync(descriptor)

    monkeypatch.setattr(provider.os, "fsync", observing_fsync)
    path = provider.save_b64_image(base64.b64encode(PNG_1PX).decode("ascii"))

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "regular" in fsync_kinds
    assert "directory" in fsync_kinds


@pytest.mark.parametrize("payload", [PNG_1PX, JPEG_2PX])
def test_agent_image_validator_accepts_complete_decodable_fixtures(payload):
    from agent.image_gen_provider import validate_image_bytes

    assert validate_image_bytes(payload)[0].startswith("image/")


@pytest.mark.parametrize("payload", [GIF_1PX, WEBP_1PX])
def test_agent_rejects_formats_without_portable_pixel_decoder(payload):
    from agent.image_gen_provider import validate_image_bytes

    with pytest.raises(ValueError, match="unsupported"):
        validate_image_bytes(payload)


def test_agent_download_contract_declares_only_supported_formats():
    from agent.image_gen_provider import (
        SUPPORTED_IMAGE_ACCEPT,
        _URL_IMAGE_CONTENT_TYPES,
        validate_image_bytes,
    )

    assert SUPPORTED_IMAGE_ACCEPT == "image/png,image/jpeg"
    assert set(_URL_IMAGE_CONTENT_TYPES) == {
        "image/png", "image/jpeg", "image/jpg"
    }
    for payload in (GIF_1PX, WEBP_1PX):
        with pytest.raises(
            ValueError, match="supported formats are PNG and baseline JPEG"
        ):
            validate_image_bytes(payload)


@pytest.mark.parametrize(
    "payload",
    [
        _png_with_replaced_idat(b"CRC-correct-but-not-zlib"),
        JPEG_2PX[:-3] + JPEG_2PX[-2:],
    ],
)
def test_agent_rejects_invalid_pixel_streams(payload):
    from agent.image_gen_provider import validate_image_bytes

    with pytest.raises(ValueError):
        validate_image_bytes(payload)


def test_agent_png_indexed_color_requires_valid_palette_before_idat():
    from agent.image_gen_provider import validate_image_bytes

    assert validate_image_bytes(_indexed_png(palette=b"\x00\x00\x00\xff\xff\xff"))[2:] == (1, 1)
    for payload in (
        _indexed_png(palette=None),
        _indexed_png(palette=b"\x00\x00\x00\xff\xff\xff", palette_after_idat=True),
        _indexed_png(palette=b"\x00\x00\x00" * 3),
        _indexed_png(palette=b"\x00\x00"),
    ):
        with pytest.raises(ValueError):
            validate_image_bytes(payload)


def test_agent_jpeg_requires_complete_nonzero_dqt_and_valid_component_selector():
    from agent.image_gen_provider import validate_image_bytes

    dqt = JPEG_2PX.index(b"\xff\xdb")
    sof = JPEG_2PX.index(b"\xff\xc0")
    missing = JPEG_2PX[:dqt] + b"\xff\xfe" + JPEG_2PX[dqt + 2:]
    truncated = bytearray(JPEG_2PX)
    length = int.from_bytes(truncated[dqt + 2:dqt + 4], "big")
    truncated[dqt + 2:dqt + 4] = (length - 1).to_bytes(2, "big")
    zero_value = bytearray(JPEG_2PX)
    zero_value[dqt + 5] = 0
    bad_selector = bytearray(JPEG_2PX)
    bad_selector[sof + 12] = 4
    for payload in (missing, bytes(truncated), bytes(zero_value), bytes(bad_selector)):
        with pytest.raises(ValueError):
            validate_image_bytes(payload)


def test_agent_rejects_dimensions_before_png_inflate_or_jpeg_entropy(monkeypatch):
    import agent.image_gen_provider as provider

    monkeypatch.setattr(
        provider.zlib, "decompressobj",
        lambda: (_ for _ in ()).throw(AssertionError("inflater must not run")),
    )
    with pytest.raises(ValueError, match="dimensions"):
        provider.validate_image_bytes(
            _png_with_dimensions(65_535, 65_535),
            max_pixels=1_000_000,
            max_decoded_bytes=4_000_000,
        )

    monkeypatch.setattr(
        provider, "_decode_jpeg_entropy_segment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("entropy decoder must not run")
        ),
    )
    with pytest.raises(ValueError, match="dimensions"):
        provider.validate_image_bytes(
            _jpeg_with_dimensions(65_535, 65_535),
            max_pixels=1_000_000,
            max_decoded_bytes=4_000_000,
        )


def test_agent_rejects_decoded_byte_limit_before_decoders(monkeypatch):
    import agent.image_gen_provider as provider

    monkeypatch.setattr(
        provider.zlib, "decompressobj",
        lambda: (_ for _ in ()).throw(AssertionError("inflater must not run")),
    )
    with pytest.raises(ValueError, match="decoded"):
        provider.validate_image_bytes(
            _png_with_dimensions(100, 100),
            max_pixels=20_000,
            max_decoded_bytes=100,
        )
    monkeypatch.setattr(
        provider, "_decode_jpeg_entropy_segment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("entropy decoder must not run")
        ),
    )
    with pytest.raises(ValueError, match="decoded"):
        provider.validate_image_bytes(
            JPEG_2PX, max_pixels=100, max_decoded_bytes=4
        )


def test_verified_cache_reference_binds_opaque_name_to_same_fd_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from agent.image_gen_provider import validated_cache_image_ref

    image = tmp_path / "home" / "cache" / "images" / "generated.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG_1PX)
    assert validated_cache_image_ref(str(image)) == (
        "generated.png", hashlib.sha256(PNG_1PX).hexdigest()
    )


def test_agent_jpeg_validator_handles_420_sampling_and_restart_markers():
    from agent.image_gen_provider import validate_image_bytes

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
def test_agent_jpeg_validator_rejects_progressive_and_bad_restart_sequence(payload):
    from agent.image_gen_provider import validate_image_bytes

    with pytest.raises(ValueError):
        validate_image_bytes(payload)


@pytest.mark.parametrize(
    "payload",
    [
        PNG_1PX[:-12],
        PNG_1PX[:-1] + bytes([PNG_1PX[-1] ^ 1]),
        JPEG_2PX[:-2],
        GIF_1PX[:-1],
        WEBP_1PX[:-1],
    ],
)
def test_agent_image_validator_rejects_truncated_or_corrupt_structures(payload):
    from agent.image_gen_provider import validate_image_bytes

    with pytest.raises(ValueError):
        validate_image_bytes(payload)
