#!/usr/bin/env python3
"""Validate the Taiji product icon chain without third-party dependencies."""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import zlib
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_CRITICAL_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
MAX_DECOMPRESSED_ICON_BYTES = 64 * 1024 * 1024
EXPECTED_TAIJI_FAVICON_ICO_SHA256 = (
    "ef0a443472b28993c5884997f9e056458cb387769d1f0560c1fcb4842fda7ef2"
)


class IconValidationError(RuntimeError):
    pass


def fail(message: str) -> "NoReturn":
    raise IconValidationError(message)


def require_regular(path: Path, label: str) -> bytes:
    if not path.exists() or not path.is_file() or path.is_symlink():
        fail(f"{label} is missing or not a regular non-symlink file: {path}")
    try:
        mode = path.stat().st_mode & 0o777
    except OSError as exc:
        fail(f"cannot stat {label}: {path}: {exc}")
    if mode != 0o644:
        fail(f"{label} must be mode 0644, got {mode:04o}: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        fail(f"cannot read {label}: {path}: {exc}")


def _chunk_name(chunk_type: bytes) -> str:
    return chunk_type.decode("ascii", errors="backslashreplace")


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _decode_rgba_scanlines(
    filtered: bytes,
    width: int,
    height: int,
    label: str,
) -> bytes:
    bytes_per_pixel = 4
    row_width = width * bytes_per_pixel
    encoded_row_width = row_width + 1
    previous = bytearray(row_width)
    decoded = bytearray()
    for row_index in range(height):
        row_offset = row_index * encoded_row_width
        filter_type = filtered[row_offset]
        encoded = filtered[row_offset + 1 : row_offset + encoded_row_width]
        current = bytearray(row_width)
        for index, value in enumerate(encoded):
            left = current[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            above = previous[index]
            upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth_predictor(left, above, upper_left)
            else:  # guarded before decoding; keep this helper fail-closed
                fail(f"{label} has invalid PNG filter byte {filter_type} at row {row_index}")
            current[index] = (value + predictor) & 0xFF
        decoded.extend(current)
        previous = current
    return bytes(decoded)


def parse_png(
    data: bytes,
    label: str,
    expected_size: int | None = None,
) -> tuple[int, int, bytes]:
    if not data.startswith(PNG_SIGNATURE):
        fail(f"{label} is not a PNG")
    if len(data) < len(PNG_SIGNATURE) + 12:
        fail(f"{label} is truncated")

    offset = len(PNG_SIGNATURE)
    chunk_index = 0
    ihdr: tuple[int, int, int, int, int, int, int] | None = None
    idat_parts: list[bytes] = []
    idat_closed = False
    seen_plte = False
    seen_iend = False

    while offset < len(data):
        if len(data) - offset < 12:
            fail(f"{label} has a truncated PNG chunk header")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            fail(f"{label} has a truncated {_chunk_name(chunk_type)} chunk")
        if len(chunk_type) != 4 or any(
            byte not in range(ord("A"), ord("Z") + 1)
            and byte not in range(ord("a"), ord("z") + 1)
            for byte in chunk_type
        ):
            fail(f"{label} has an invalid PNG chunk type")
        if chr(chunk_type[2]).islower():
            fail(f"{label} has an invalid reserved bit in {_chunk_name(chunk_type)}")

        payload = data[offset + 8 : offset + 8 + length]
        stored_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        calculated_crc = zlib.crc32(chunk_type)
        calculated_crc = zlib.crc32(payload, calculated_crc) & 0xFFFFFFFF
        if stored_crc != calculated_crc:
            fail(f"{label} has an invalid CRC in {_chunk_name(chunk_type)}")

        if chunk_type == b"IHDR":
            if chunk_index != 0 or ihdr is not None or length != 13:
                fail(f"{label} must contain exactly one first IHDR chunk")
            ihdr = struct.unpack(">IIBBBBB", payload)
        elif ihdr is None:
            fail(f"{label} has a chunk before IHDR")
        elif chunk_type == b"PLTE":
            if seen_plte or idat_parts:
                fail(f"{label} has an invalid PLTE chunk order")
            seen_plte = True
        elif chunk_type == b"IDAT":
            if idat_closed:
                fail(f"{label} IDAT chunks must be contiguous")
            idat_parts.append(payload)
        elif chunk_type == b"IEND":
            if seen_iend or length != 0:
                fail(f"{label} must contain exactly one empty IEND chunk")
            if not idat_parts:
                fail(f"{label} has IEND before IDAT")
            seen_iend = True
            if chunk_end != len(data):
                fail(f"{label} has trailing data after IEND")
        else:
            if chunk_type[0] in range(ord("A"), ord("Z") + 1) and chunk_type not in PNG_CRITICAL_CHUNKS:
                fail(f"{label} has unsupported critical chunk {_chunk_name(chunk_type)}")
            if idat_parts:
                idat_closed = True

        offset = chunk_end
        chunk_index += 1
        if seen_iend:
            break

    if ihdr is None:
        fail(f"{label} has no IHDR chunk")
    if not idat_parts:
        fail(f"{label} has no IDAT chunk")
    if not seen_iend:
        fail(f"{label} has no terminal IEND chunk")

    width, height, bit_depth, color_type, compression, filtering, interlace = ihdr
    if width == 0 or height == 0 or width > 0x7FFFFFFF or height > 0x7FFFFFFF:
        fail(f"{label} has invalid PNG dimensions {width}x{height}")
    if expected_size is not None and (width, height) != (expected_size, expected_size):
        fail(f"{label} must be {expected_size}x{expected_size}, got {width}x{height}")
    if bit_depth != 8 or color_type != 6:
        fail(f"{label} must be 8-bit RGBA PNG (bit_depth=8,color_type=6)")
    if (compression, filtering, interlace) != (0, 0, 0):
        fail(f"{label} uses unsupported PNG encoding flags")

    row_length = 1 + width * 4
    expected_length = row_length * height
    if expected_length > MAX_DECOMPRESSED_ICON_BYTES:
        fail(f"{label} decompressed image exceeds the icon safety limit")
    decompressor = zlib.decompressobj()
    try:
        pixels = decompressor.decompress(b"".join(idat_parts), expected_length + 1)
        if decompressor.unconsumed_tail:
            fail(f"{label} IDAT expands beyond the expected RGBA8 image length")
        pixels += decompressor.flush()
    except zlib.error as exc:
        fail(f"{label} has invalid compressed IDAT data: {exc}")
    if not decompressor.eof:
        fail(f"{label} has truncated compressed IDAT data")
    if decompressor.unused_data:
        fail(f"{label} has trailing compressed data in IDAT")
    if len(pixels) != expected_length:
        fail(
            f"{label} IDAT has unexpected RGBA8 length: "
            f"expected {expected_length}, got {len(pixels)}"
        )
    for row in range(height):
        filter_byte = pixels[row * row_length]
        if filter_byte > 4:
            fail(f"{label} has invalid PNG filter byte {filter_byte} at row {row}")
    return width, height, _decode_rgba_scanlines(pixels, width, height, label)


def parse_ico(data: bytes, label: str) -> dict[int, bytes]:
    if len(data) < 6 or struct.unpack("<HH", data[:4]) != (0, 1):
        fail(f"{label} has an invalid ICO header")
    count = struct.unpack("<H", data[4:6])[0]
    directory_end = 6 + count * 16
    if count < 1 or len(data) < directory_end:
        fail(f"{label} has no complete icon directory")
    payload_ranges: list[tuple[int, int, int, int]] = []
    for index in range(count):
        entry = data[6 + index * 16 : 22 + index * 16]
        width = entry[0] or 256
        height = entry[1] or 256
        payload_size, payload_offset = struct.unpack("<II", entry[8:16])
        if width != height or width not in {16, 24, 32, 48, 64, 128, 192, 256}:
            fail(f"{label} has an invalid icon size at entry {index}: {width}x{height}")
        payload_end = payload_offset + payload_size
        if payload_size == 0 or payload_offset < directory_end or payload_end > len(data):
            fail(f"{label} has an out-of-range payload at entry {index}")
        payload_ranges.append((payload_offset, payload_end, index, width))

    previous_end = directory_end
    for payload_offset, payload_end, index, _ in sorted(payload_ranges):
        if payload_offset < previous_end:
            fail(f"{label} has overlapping payloads at entry {index}")
        previous_end = payload_end

    decoded_entries: dict[int, bytes] = {}
    for payload_offset, payload_end, index, width in payload_ranges:
        payload = data[payload_offset:payload_end]
        if not payload.startswith(PNG_SIGNATURE):
            fail(f"{label} entry {index} must be a PNG-backed icon payload")
        if width in decoded_entries:
            fail(f"{label} has duplicate PNG icon size {width}x{width}")
        _, _, pixels = parse_png(payload, f"{label} PNG entry {index}", width)
        decoded_entries[width] = pixels
    return decoded_entries


def validate(args: argparse.Namespace) -> str:
    web_static = Path(args.web_static).resolve()
    install_icons = Path(args.install_icons).resolve()
    resource_icon = Path(args.resource_icon).resolve()
    source_icon = web_static / "assets/taiji/logo/logo-mark-icon.png"
    source_bytes = require_regular(source_icon, "canonical icon source")
    parse_png(source_bytes, "canonical icon source", 512)

    web_icons: dict[int, bytes] = {}
    for size in (32, 48, 64, 128, 192, 256, 512):
        path = web_static / f"favicon-{size}.png"
        data = require_regular(path, f"Web favicon {size}")
        parse_png(data, f"Web favicon {size}", size)
        web_icons[size] = data

    ico = web_static / "favicon.ico"
    ico_bytes = require_regular(ico, "Web favicon ICO")
    ico_pixels = parse_ico(ico_bytes, "Web favicon ICO")
    ico_sha256 = hashlib.sha256(ico_bytes).hexdigest()
    if ico_sha256 != EXPECTED_TAIJI_FAVICON_ICO_SHA256:
        fail("Web favicon ICO is not the hash-pinned Taiji product icon")
    for size in ico_pixels:
        if size not in web_icons:
            fail(f"Web favicon ICO size {size} has no canonical Web favicon PNG")

    if (web_static / "favicon-512.png").read_bytes() != source_bytes:
        fail("Web favicon-512.png must be byte-identical to canonical icon source")
    resource_bytes = require_regular(resource_icon, "installed resource icon")
    parse_png(resource_bytes, "installed resource icon", 512)
    if resource_bytes != source_bytes:
        fail("installed resource icon must be byte-identical to canonical icon source")

    for size in (32, 48, 64, 128, 192, 256, 512):
        path = install_icons / f"{size}x{size}/apps/taiji-agent.png"
        data = require_regular(path, f"hicolor icon {size}")
        parse_png(data, f"hicolor icon {size}", size)
        if data != web_icons[size]:
            fail(f"hicolor icon {size} must be byte-identical to Web favicon {size}")

    for relative in ("index.html", "manifest.json", "sw.js"):
        path = web_static / relative
        if path.exists() and "favicon.svg" in path.read_text(encoding="utf-8"):
            fail(f"legacy SVG favicon reference remains in {path}")

    return hashlib.sha256(
        b"taiji-product-icon-set-v2\0"
        + b"".join(web_icons[size] for size in (32, 48, 64, 128, 192, 256, 512))
        + ico_bytes
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-static", required=True)
    parser.add_argument("--install-icons", required=True)
    parser.add_argument("--resource-icon", required=True)
    parser.add_argument("--print-digest", action="store_true")
    args = parser.parse_args()
    try:
        digest = validate(args)
    except (IconValidationError, OSError, UnicodeError) as exc:
        print(f"[FAIL] icon validation: {exc}", file=sys.stderr)
        return 1
    if args.print_digest:
        print(digest)
    else:
        print(f"[OK] Product icon chain is consistent (icon_set_sha256={digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
