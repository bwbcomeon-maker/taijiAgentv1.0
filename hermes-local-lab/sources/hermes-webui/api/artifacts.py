"""Durable, session-owned chat artifacts.

The manifest is an internal persistence contract.  Browser-facing callers get
only :data:`PUBLIC_ARTIFACT_FIELDS`; filesystem paths never cross that boundary.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import shutil
import stat
import struct
import threading
import time
import uuid
import zlib
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any


PUBLIC_ARTIFACT_FIELDS = (
    "artifact_id", "kind", "mime", "name", "size", "sha256", "status",
)
_MANIFEST_VERSION = 1
_DEFAULT_MAX_BYTES = 25 * 1024 * 1024
_DEFAULT_MAX_PIXELS = 40_000_000
_RETENTION_SECONDS = 7 * 24 * 3600
_SAFE_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
)
_MIME_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
}
_ROOT_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}
logger = logging.getLogger(__name__)


def _migration_guarded_artifact_write(func):
    """Enter the migration barrier before any registry/file lock."""
    @wraps(func)
    def guarded(*args, **kwargs):
        from api.legacy_session_migration import legacy_migration_state_guard

        with legacy_migration_state_guard():
            return func(*args, **kwargs)
    return guarded


class ArtifactValidationError(ValueError):
    pass


class ArtifactConflictError(ArtifactValidationError):
    pass


@dataclass(frozen=True)
class AuthorizedArtifact:
    """Immutable verified payload; HTTP handlers must not reopen its path."""

    data: bytes
    mime: str
    name: str
    size: int
    _path: Path

    def read_bytes(self) -> bytes:
        return self.data

    # Compatibility helpers for internal corruption tests; serving never uses them.
    def unlink(self, *args, **kwargs) -> None:
        self._path.unlink(*args, **kwargs)

    def write_bytes(self, data: bytes) -> int:
        return self._path.write_bytes(data)

    def symlink_to(self, target: Path) -> None:
        self._path.symlink_to(target)


@contextlib.contextmanager
def _exclusive_file_lock(path: Path):
    """Cross-process exclusive lock with POSIX and Windows implementations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _read_regular_file_nofollow(path: Path, *, max_bytes: int) -> bytes:
    """Open once without following symlinks and read/validate that same fd."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        if not hasattr(os, "O_NOFOLLOW") and path.is_symlink():
            raise ArtifactValidationError("artifact path is not a regular file")
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ArtifactValidationError("artifact path is not a regular file") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ArtifactValidationError("artifact path is not a regular file")
        if metadata.st_size > max_bytes:
            raise ArtifactValidationError("image exceeds size limit")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise ArtifactValidationError("artifact file changed while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ArtifactValidationError("artifact file changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def public_artifact_projection(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        field: value[field]
        for field in PUBLIC_ARTIFACT_FIELDS
        if field in value
    }


def _safe_id(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 200 or any(char not in _SAFE_ID_CHARS for char in value):
        raise ArtifactValidationError(f"invalid {label}")
    return value


def _png_format(
    data: bytes, *, max_pixels: int, max_decoded_bytes: int
) -> tuple[str, str, int, int]:
    offset = 8
    width = height = 0
    saw_ihdr = saw_idat = saw_iend = saw_plte = False
    bit_depth = color_type = 0
    idat_parts: list[bytes] = []
    expected_size = row_bytes = channels = 0
    while offset < len(data):
        if offset + 12 > len(data):
            raise ArtifactValidationError("invalid PNG structure")
        length = int.from_bytes(data[offset:offset + 4], "big")
        chunk_type = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ArtifactValidationError("invalid PNG structure")
        payload = data[offset + 8:offset + 8 + length]
        crc = int.from_bytes(data[offset + 8 + length:end], "big")
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != crc:
            raise ArtifactValidationError("invalid PNG CRC")
        if not saw_ihdr:
            if chunk_type != b"IHDR" or length != 13:
                raise ArtifactValidationError("invalid PNG IHDR")
            width, height = struct.unpack(">II", payload[:8])
            bit_depth, color_type = payload[8], payload[9]
            if payload[10:12] != b"\x00\x00" or payload[12] != 0:
                raise ArtifactValidationError("unsupported PNG encoding")
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if bit_depth not in valid_depths.get(color_type, set()):
                raise ArtifactValidationError("unsupported PNG encoding")
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise ArtifactValidationError("Image dimensions exceed limit")
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
            row_bytes = (width * channels * bit_depth + 7) // 8
            expected_size = (row_bytes + 1) * height
            if expected_size > max_decoded_bytes:
                raise ArtifactValidationError("Image decoded size exceeds limit")
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            raise ArtifactValidationError("duplicate PNG IHDR")
        if chunk_type == b"PLTE":
            if saw_plte or saw_idat or color_type in {0, 4}:
                raise ArtifactValidationError("invalid PNG palette")
            if not payload or len(payload) % 3 or len(payload) > 256 * 3:
                raise ArtifactValidationError("invalid PNG palette")
            if color_type == 3 and len(payload) // 3 > 1 << bit_depth:
                raise ArtifactValidationError("PNG palette exceeds bit depth")
            saw_plte = True
        if chunk_type == b"IDAT":
            saw_idat = True
            idat_parts.append(payload)
        elif chunk_type == b"IEND":
            if length != 0 or end != len(data):
                raise ArtifactValidationError("invalid PNG IEND")
            saw_iend = True
        offset = end
    if not (saw_ihdr and saw_idat and saw_iend):
        raise ArtifactValidationError("incomplete PNG")
    if color_type == 3 and not saw_plte:
        raise ArtifactValidationError("indexed PNG is missing palette")
    inflater = zlib.decompressobj()
    try:
        pixels = inflater.decompress(b"".join(idat_parts), expected_size)
        while inflater.unconsumed_tail:
            pending = inflater.unconsumed_tail
            extra = inflater.decompress(pending, 1)
            if extra or inflater.unconsumed_tail == pending:
                raise ArtifactValidationError("invalid PNG pixel stream")
        flushed = inflater.flush(max(1, expected_size - len(pixels)))
        if len(pixels) + len(flushed) > expected_size:
            raise ArtifactValidationError("invalid PNG pixel stream")
        pixels += flushed
    except zlib.error as exc:
        raise ArtifactValidationError("invalid PNG pixel stream") from exc
    if (
        len(pixels) != expected_size
        or not inflater.eof
        or inflater.unused_data
        or inflater.unconsumed_tail
    ):
        raise ArtifactValidationError("invalid PNG pixel stream")

    # Reconstruct every scanline.  CRC-valid IDAT bytes are not sufficient:
    # filter bytes and dependencies on the previous row are part of the PNG
    # pixel stream contract.
    bytes_per_pixel = max(1, (channels * bit_depth + 7) // 8)
    previous = bytearray(row_bytes)
    cursor = 0
    for _row in range(height):
        filter_type = pixels[cursor]
        cursor += 1
        if filter_type > 4:
            raise ArtifactValidationError("invalid PNG scanline filter")
        encoded = pixels[cursor:cursor + row_bytes]
        cursor += row_bytes
        reconstructed = bytearray(row_bytes)
        for index, value in enumerate(encoded):
            left = reconstructed[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
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
            else:
                base = left + above - upper_left
                distances = (
                    abs(base - left), abs(base - above), abs(base - upper_left)
                )
                predictor = (left, above, upper_left)[distances.index(min(distances))]
            reconstructed[index] = (value + predictor) & 0xFF
        previous = reconstructed
    return "image/png", "png", width, height


def _gif_sub_blocks(data: bytes, offset: int) -> int:
    while True:
        if offset >= len(data):
            raise ArtifactValidationError("incomplete GIF block")
        size = data[offset]
        offset += 1
        if size == 0:
            return offset
        if offset + size > len(data):
            raise ArtifactValidationError("incomplete GIF block")
        offset += size


def _gif_format(data: bytes) -> tuple[str, str, int, int]:
    if len(data) < 13:
        raise ArtifactValidationError("invalid GIF")
    width, height = struct.unpack("<HH", data[6:10])
    offset = 13
    if data[10] & 0x80:
        offset += 3 * (2 ** ((data[10] & 7) + 1))
    if offset > len(data):
        raise ArtifactValidationError("incomplete GIF color table")
    saw_image = False
    while offset < len(data):
        marker = data[offset]
        offset += 1
        if marker == 0x3B:
            if not saw_image or offset != len(data):
                raise ArtifactValidationError("invalid GIF trailer")
            return "image/gif", "gif", width, height
        if marker == 0x21:
            if offset >= len(data):
                raise ArtifactValidationError("incomplete GIF extension")
            offset += 1
            offset = _gif_sub_blocks(data, offset)
            continue
        if marker != 0x2C or offset + 9 > len(data):
            raise ArtifactValidationError("invalid GIF block")
        descriptor = data[offset:offset + 9]
        offset += 9
        if descriptor[8] & 0x80:
            offset += 3 * (2 ** ((descriptor[8] & 7) + 1))
        if offset >= len(data):
            raise ArtifactValidationError("incomplete GIF image")
        offset += 1
        offset = _gif_sub_blocks(data, offset)
        saw_image = True
    raise ArtifactValidationError("missing GIF trailer")


def _webp_format(data: bytes) -> tuple[str, str, int, int]:
    if len(data) < 20 or data[8:12] != b"WEBP":
        raise ArtifactValidationError("invalid WebP")
    if int.from_bytes(data[4:8], "little") + 8 != len(data):
        raise ArtifactValidationError("invalid WebP RIFF length")
    offset = 12
    dimensions = None
    saw_image = False
    while offset < len(data):
        if offset + 8 > len(data):
            raise ArtifactValidationError("incomplete WebP chunk")
        kind = data[offset:offset + 4]
        length = int.from_bytes(data[offset + 4:offset + 8], "little")
        body_start = offset + 8
        body_end = body_start + length
        padded_end = body_end + (length & 1)
        if padded_end > len(data):
            raise ArtifactValidationError("incomplete WebP chunk")
        body = data[body_start:body_end]
        if kind == b"VP8X" and length >= 10:
            dimensions = (
                int.from_bytes(body[4:7], "little") + 1,
                int.from_bytes(body[7:10], "little") + 1,
            )
        elif kind == b"VP8L" and length >= 5 and body[0] == 0x2F:
            bits = int.from_bytes(body[1:5], "little")
            dimensions = ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
            saw_image = True
        elif kind == b"VP8 " and length >= 10 and body[3:6] == b"\x9d\x01\x2a":
            dimensions = (
                int.from_bytes(body[6:8], "little") & 0x3FFF,
                int.from_bytes(body[8:10], "little") & 0x3FFF,
            )
            saw_image = True
        offset = padded_end
    if dimensions is None or not saw_image or offset != len(data):
        raise ArtifactValidationError("incomplete WebP")
    return "image/webp", "webp", dimensions[0], dimensions[1]


def _jpeg_quantization_tables(payload: bytes, tables: set[int]) -> None:
    offset = 0
    while offset < len(payload):
        table_info = payload[offset]
        offset += 1
        precision, table_id = table_info >> 4, table_info & 0x0F
        if precision not in {0, 1} or table_id > 3:
            raise ArtifactValidationError("invalid JPEG quantization table")
        value_size = precision + 1
        table_size = 64 * value_size
        if offset + table_size > len(payload):
            raise ArtifactValidationError("truncated JPEG quantization table")
        raw_values = payload[offset:offset + table_size]
        offset += table_size
        values = (
            list(raw_values)
            if value_size == 1
            else [int.from_bytes(raw_values[index:index + 2], "big") for index in range(0, table_size, 2)]
        )
        if any(value == 0 for value in values):
            raise ArtifactValidationError("invalid JPEG quantization value")
        tables.add(table_id)


def _jpeg_huffman_tables(payload: bytes, tables: dict) -> None:
    offset = 0
    while offset < len(payload):
        table_info = payload[offset]
        offset += 1
        table_class, table_id = table_info >> 4, table_info & 0x0F
        if table_class not in {0, 1} or table_id > 3 or offset + 16 > len(payload):
            raise ArtifactValidationError("invalid JPEG Huffman table")
        counts = payload[offset:offset + 16]
        offset += 16
        symbol_count = sum(counts)
        if not symbol_count or offset + symbol_count > len(payload):
            raise ArtifactValidationError("invalid JPEG Huffman table")
        symbols = payload[offset:offset + symbol_count]
        offset += symbol_count
        mapping: dict[tuple[int, int], int] = {}
        code = symbol_index = 0
        for bit_length, count in enumerate(counts, 1):
            if code + count > 1 << bit_length:
                raise ArtifactValidationError("oversubscribed JPEG Huffman table")
            for _ in range(count):
                mapping[(bit_length, code)] = symbols[symbol_index]
                code += 1
                symbol_index += 1
            code <<= 1
        tables[(table_class, table_id)] = mapping


class _JpegBits:
    def __init__(self, data: bytes):
        self.data = data
        self.position = 0

    def read(self, count: int) -> int:
        if count < 0 or self.position + count > len(self.data) * 8:
            raise ArtifactValidationError("truncated JPEG entropy stream")
        value = 0
        for _ in range(count):
            byte = self.data[self.position // 8]
            value = (value << 1) | ((byte >> (7 - self.position % 8)) & 1)
            self.position += 1
        return value

    def symbol(self, table: dict[tuple[int, int], int]) -> int:
        code = 0
        for bit_length in range(1, 17):
            code = (code << 1) | self.read(1)
            symbol = table.get((bit_length, code))
            if symbol is not None:
                return symbol
        raise ArtifactValidationError("invalid JPEG Huffman code")


def _decode_jpeg_entropy_segment(
    payload: bytes,
    mcu_count: int,
    scan_components: list[tuple[int, int, int]],
    frame_components: dict[int, tuple[int, int, int]],
    tables: dict,
) -> None:
    bits = _JpegBits(payload)
    for _mcu in range(mcu_count):
        for component_id, dc_table_id, ac_table_id in scan_components:
            horizontal, vertical, _quant_table_id = frame_components[component_id]
            dc_table = tables.get((0, dc_table_id))
            ac_table = tables.get((1, ac_table_id))
            if dc_table is None or ac_table is None:
                raise ArtifactValidationError("missing JPEG Huffman table")
            for _block in range(horizontal * vertical):
                dc_size = bits.symbol(dc_table)
                if dc_size > 11:
                    raise ArtifactValidationError("invalid JPEG DC coefficient")
                bits.read(dc_size)
                coefficient = 1
                while coefficient < 64:
                    symbol = bits.symbol(ac_table)
                    run, size = symbol >> 4, symbol & 0x0F
                    if size == 0:
                        if run == 0:  # EOB
                            break
                        if run != 15:  # only ZRL is valid when size is zero
                            raise ArtifactValidationError("invalid JPEG AC coefficient")
                        coefficient += 16
                    else:
                        if size > 10:
                            raise ArtifactValidationError("invalid JPEG AC coefficient")
                        coefficient += run
                        if coefficient >= 64:
                            raise ArtifactValidationError("invalid JPEG AC run")
                        bits.read(size)
                        coefficient += 1
    remaining = len(payload) * 8 - bits.position
    if remaining > 7 or (remaining and bits.read(remaining) != (1 << remaining) - 1):
        raise ArtifactValidationError("invalid JPEG entropy padding")


def _jpeg_scan_parts(data: bytes, offset: int) -> tuple[list[bytes], list[int]]:
    parts: list[bytearray] = [bytearray()]
    restart_markers: list[int] = []
    while offset < len(data):
        value = data[offset]
        offset += 1
        if value != 0xFF:
            parts[-1].append(value)
            continue
        fill_count = 1
        while offset < len(data) and data[offset] == 0xFF:
            fill_count += 1
            offset += 1
        if offset >= len(data):
            raise ArtifactValidationError("missing JPEG EOI")
        marker = data[offset]
        offset += 1
        if marker == 0:
            if fill_count != 1:
                raise ArtifactValidationError("invalid JPEG byte stuffing")
            parts[-1].append(0xFF)
        elif 0xD0 <= marker <= 0xD7:
            restart_markers.append(marker)
            parts.append(bytearray())
        elif marker == 0xD9:
            if offset != len(data):
                raise ArtifactValidationError("invalid JPEG EOI")
            return [bytes(part) for part in parts], restart_markers
        else:
            # Multiple scans/progressive refinement cannot be verified by the
            # baseline decoder and therefore never become a ready artifact.
            raise ArtifactValidationError("unsupported JPEG scan structure")
    raise ArtifactValidationError("missing JPEG EOI")


def _jpeg_format(
    data: bytes, *, max_pixels: int, max_decoded_bytes: int
) -> tuple[str, str, int, int]:
    offset = 2
    width = height = 0
    frame_components: dict[int, tuple[int, int, int]] = {}
    quantization_tables: set[int] = set()
    tables: dict[tuple[int, int], dict[tuple[int, int], int]] = {}
    restart_interval = 0
    saw_sof = False
    all_sof = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while offset < len(data):
        if data[offset] != 0xFF:
            raise ArtifactValidationError("invalid JPEG segment")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            raise ArtifactValidationError("incomplete JPEG marker")
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            raise ArtifactValidationError("invalid JPEG marker")
        if offset + 2 > len(data):
            raise ArtifactValidationError("incomplete JPEG segment")
        length = int.from_bytes(data[offset:offset + 2], "big")
        if length < 2 or offset + length > len(data):
            raise ArtifactValidationError("invalid JPEG segment length")
        payload = data[offset + 2:offset + length]
        offset += length
        if marker in all_sof and marker != 0xC0:
            raise ArtifactValidationError("unsupported JPEG frame type")
        if marker == 0xC0:
            if saw_sof or len(payload) < 6 or payload[0] != 8:
                raise ArtifactValidationError("invalid JPEG SOF0")
            height = int.from_bytes(payload[1:3], "big")
            width = int.from_bytes(payload[3:5], "big")
            component_count = payload[5]
            if component_count not in {1, 3, 4} or len(payload) != 6 + 3 * component_count:
                raise ArtifactValidationError("invalid JPEG SOF0")
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise ArtifactValidationError("Image dimensions exceed limit")
            if width * height * component_count > max_decoded_bytes:
                raise ArtifactValidationError("Image decoded size exceeds limit")
            for index in range(component_count):
                component_id = payload[6 + index * 3]
                sampling = payload[7 + index * 3]
                quant_table_id = payload[8 + index * 3]
                horizontal, vertical = sampling >> 4, sampling & 0x0F
                if (
                    component_id in frame_components
                    or not (1 <= horizontal <= 4 and 1 <= vertical <= 4)
                    or quant_table_id > 3
                ):
                    raise ArtifactValidationError("invalid JPEG sampling factors")
                frame_components[component_id] = (
                    horizontal, vertical, quant_table_id
                )
            saw_sof = True
        elif marker == 0xDB:
            _jpeg_quantization_tables(payload, quantization_tables)
        elif marker == 0xC4:
            _jpeg_huffman_tables(payload, tables)
        elif marker == 0xDD:
            if len(payload) != 2:
                raise ArtifactValidationError("invalid JPEG restart interval")
            restart_interval = int.from_bytes(payload, "big")
        elif marker == 0xDA:
            if not saw_sof or len(payload) < 6:
                raise ArtifactValidationError("invalid JPEG SOS")
            scan_count = payload[0]
            if len(payload) != 1 + 2 * scan_count + 3 or scan_count != len(frame_components):
                raise ArtifactValidationError("unsupported JPEG scan structure")
            scan_components: list[tuple[int, int, int]] = []
            for index in range(scan_count):
                component_id = payload[1 + index * 2]
                selectors = payload[2 + index * 2]
                if component_id not in frame_components or any(row[0] == component_id for row in scan_components):
                    raise ArtifactValidationError("invalid JPEG scan component")
                scan_components.append((component_id, selectors >> 4, selectors & 0x0F))
            if payload[-3:] != b"\x00\x3f\x00":
                raise ArtifactValidationError("unsupported JPEG scan structure")
            if any(
                component[2] not in quantization_tables
                for component in frame_components.values()
            ):
                raise ArtifactValidationError("missing JPEG quantization table")
            entropy_parts, restart_markers = _jpeg_scan_parts(data, offset)
            max_horizontal = max(value[0] for value in frame_components.values())
            max_vertical = max(value[1] for value in frame_components.values())
            mcu_columns = (width + 8 * max_horizontal - 1) // (8 * max_horizontal)
            mcu_rows = (height + 8 * max_vertical - 1) // (8 * max_vertical)
            total_mcus = mcu_columns * mcu_rows
            if restart_markers:
                if not restart_interval:
                    raise ArtifactValidationError("JPEG restart marker without interval")
                for index, value in enumerate(restart_markers):
                    if value != 0xD0 + (index % 8):
                        raise ArtifactValidationError("invalid JPEG restart sequence")
            expected_parts = (
                (total_mcus + restart_interval - 1) // restart_interval
                if restart_interval else 1
            )
            if len(entropy_parts) != expected_parts:
                raise ArtifactValidationError("invalid JPEG restart interval")
            remaining_mcus = total_mcus
            for part in entropy_parts:
                part_mcus = min(restart_interval, remaining_mcus) if restart_interval else remaining_mcus
                _decode_jpeg_entropy_segment(
                    part, part_mcus, scan_components, frame_components, tables
                )
                remaining_mcus -= part_mcus
            if remaining_mcus:
                raise ArtifactValidationError("truncated JPEG entropy stream")
            return "image/jpeg", "jpg", width, height
    raise ArtifactValidationError("missing JPEG SOS")


def _image_format(
    data: bytes, *, max_pixels: int, max_decoded_bytes: int
) -> tuple[str, str, int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png_format(
            data, max_pixels=max_pixels, max_decoded_bytes=max_decoded_bytes
        )
    if data.startswith((b"GIF87a", b"GIF89a")):
        raise ArtifactValidationError(
            "unsupported image format: supported formats are PNG and baseline JPEG"
        )
    if data.startswith(b"RIFF"):
        raise ArtifactValidationError(
            "unsupported image format: supported formats are PNG and baseline JPEG"
        )
    if data.startswith(b"\xff\xd8"):
        return _jpeg_format(
            data, max_pixels=max_pixels, max_decoded_bytes=max_decoded_bytes
        )
    raise ArtifactValidationError(
        "invalid image format: supported formats are PNG and baseline JPEG"
    )


def validate_image_bytes(
    data: bytes,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    max_pixels: int = _DEFAULT_MAX_PIXELS,
    max_decoded_bytes: int | None = None,
    declared_mime: str | None = None,
) -> tuple[str, str, int, int]:
    if not data:
        raise ArtifactValidationError("empty image")
    if len(data) > max_bytes:
        raise ArtifactValidationError("image exceeds size limit")
    decoded_limit = (
        int(max_decoded_bytes)
        if max_decoded_bytes is not None else int(max_bytes) * 16
    )
    if max_pixels <= 0 or decoded_limit <= 0:
        raise ArtifactValidationError("invalid image resource limits")
    mime, extension, width, height = _image_format(
        data, max_pixels=int(max_pixels), max_decoded_bytes=decoded_limit
    )
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ArtifactValidationError("image dimensions exceed limit")
    declared = str(declared_mime or "").split(";", 1)[0].strip().lower()
    if declared and declared != mime and not (
        declared in {"image/jpg", "image/jpeg"} and mime == "image/jpeg"
    ):
        raise ArtifactValidationError("image MIME does not match image format")
    return mime, extension, width, height


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        temp.unlink(missing_ok=True)


class ArtifactRegistry:
    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        max_pixels: int = _DEFAULT_MAX_PIXELS,
        allowed_source_roots: list[Path] | None = None,
        create_root: bool = True,
    ):
        self.root = Path(root).expanduser().resolve()
        self.max_bytes = max(1, int(max_bytes))
        self.max_pixels = max(1, int(max_pixels))
        if allowed_source_roots is None:
            homes = [
                os.getenv("TAIJI_RUNTIME_HOME", ""),
                os.getenv("HERMES_HOME", ""),
            ]
            allowed_source_roots = [
                Path(home).expanduser() / "cache" / "images"
                for home in homes if home
            ]
        self.allowed_source_roots = [
            Path(path).expanduser().resolve() for path in allowed_source_roots
        ]
        if create_root:
            self.root.mkdir(parents=True, exist_ok=True)
        root_key = str(self.root)
        with _ROOT_LOCKS_GUARD:
            self._lock = _ROOT_LOCKS.setdefault(root_key, threading.RLock())

    def _session_dir(self, session_id: str) -> Path:
        return self.root / _safe_id(session_id, "session_id")

    def _manifest_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "manifest.json"

    def _lock_path(self, session_id: str) -> Path:
        return self.root / ".locks" / f"{_safe_id(session_id, 'session_id')}.lock"

    @contextlib.contextmanager
    def _session_lock(self, session_id: str):
        with self._lock:
            with _exclusive_file_lock(self._lock_path(session_id)):
                yield

    def _load_manifest(self, session_id: str) -> dict:
        path = self._manifest_path(session_id)
        if not path.exists():
            return {"version": _MANIFEST_VERSION, "session_id": session_id, "artifacts": []}
        try:
            payload = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError("artifact manifest is unreadable") from exc
        if payload.get("session_id") != session_id or not isinstance(payload.get("artifacts"), list):
            raise ArtifactValidationError("artifact manifest ownership mismatch")
        return payload

    def _save_manifest(self, session_id: str, manifest: dict) -> None:
        encoded = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        _atomic_write(self._manifest_path(session_id), encoded)

    @_migration_guarded_artifact_write
    def register_image_file(
        self,
        session_id: str,
        source_turn_id: str,
        source_tool_call_id: str,
        source_path: Path,
        *,
        name: str | None = None,
        expected_sha256: str | None = None,
        owner_run_id: str | None = None,
        _include_pending_owner: bool = False,
    ) -> Any:
        session_id = _safe_id(session_id, "session_id")
        source_turn_id = _safe_id(source_turn_id, "source_turn_id")
        source_tool_call_id = _safe_id(source_tool_call_id, "source_tool_call_id")
        source = Path(source_path).expanduser()
        data = _read_regular_file_nofollow(source, max_bytes=self.max_bytes)
        mime, extension, _width, _height = validate_image_bytes(
            data, max_bytes=self.max_bytes, max_pixels=self.max_pixels
        )
        if expected_sha256 is not None:
            expected = str(expected_sha256).strip().lower()
            if (
                len(expected) != 64
                or any(char not in "0123456789abcdef" for char in expected)
                or hashlib.sha256(data).hexdigest() != expected
            ):
                raise ArtifactValidationError("generated image hash mismatch")
        return self.register_image_bytes(
            session_id,
            source_turn_id,
            source_tool_call_id,
            data,
            mime=mime,
            extension=extension,
            name=name or source.name,
            owner_run_id=owner_run_id,
            _include_pending_owner=_include_pending_owner,
        )

    @_migration_guarded_artifact_write
    def register_image_bytes(
        self,
        session_id: str,
        source_turn_id: str,
        source_tool_call_id: str,
        data: bytes,
        *,
        mime: str | None = None,
        extension: str | None = None,
        name: str = "generated-image",
        owner_run_id: str | None = None,
        _include_pending_owner: bool = False,
    ) -> Any:
        session_id = _safe_id(session_id, "session_id")
        source_turn_id = _safe_id(source_turn_id, "source_turn_id")
        source_tool_call_id = _safe_id(source_tool_call_id, "source_tool_call_id")
        owner_run_id = (
            _safe_id(owner_run_id, "owner_run_id") if owner_run_id is not None else None
        )
        actual_mime, actual_extension, _width, _height = validate_image_bytes(
            data,
            max_bytes=self.max_bytes,
            max_pixels=self.max_pixels,
            declared_mime=mime,
        )
        if extension and str(extension).lower().lstrip(".") not in {
            actual_extension, "jpeg" if actual_extension == "jpg" else actual_extension,
        }:
            raise ArtifactValidationError("image extension does not match image format")
        safe_name = Path(str(name or f"generated-image.{actual_extension}")).name
        if not safe_name or safe_name in {".", ".."}:
            safe_name = f"generated-image.{actual_extension}"
        if Path(safe_name).suffix.lower().lstrip(".") not in {
            actual_extension, "jpeg" if actual_extension == "jpg" else actual_extension,
        }:
            safe_name = f"{Path(safe_name).stem or 'generated-image'}.{actual_extension}"
        with self._session_lock(session_id):
            manifest = self._load_manifest(session_id)
            existing = next((
                item for item in manifest["artifacts"]
                if item.get("source_turn_id") == source_turn_id
                and item.get("source_tool_call_id") == source_tool_call_id
            ), None)
            if existing is not None:
                projected = public_artifact_projection(existing)
                commit_state = str(existing.get("commit_state") or "committed")
                if commit_state == "pending":
                    if owner_run_id is None or existing.get("owner_run_id") != owner_run_id:
                        raise ArtifactConflictError(
                            "artifact is pending for another run"
                        )
                    return (
                        (projected, True)
                        if _include_pending_owner else projected
                    )
                return (
                    (projected, False)
                    if _include_pending_owner else projected
                )
            artifact_id = uuid.uuid4().hex
            session_dir = self._session_dir(session_id)
            target = session_dir / f"{artifact_id}.{actual_extension}"
            record = {
                "artifact_id": artifact_id,
                "owner_session_id": session_id,
                "source_turn_id": source_turn_id,
                "source_tool_call_id": source_tool_call_id,
                "kind": "image",
                "mime": actual_mime,
                "name": safe_name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "storage_path": str(target),
                "status": "ready",
                "commit_state": "pending" if owner_run_id else "committed",
                "owner_run_id": owner_run_id,
                "created_at": time.time(),
            }
            try:
                _atomic_write(target, data)
                manifest["artifacts"].append(record)
                self._save_manifest(session_id, manifest)
            except Exception:
                target.unlink(missing_ok=True)
                raise
        projected = public_artifact_projection(record)
        owned_pending = owner_run_id is not None
        return (
            (projected, owned_pending)
            if _include_pending_owner else projected
        )

    @_migration_guarded_artifact_write
    def commit_artifacts(
        self,
        session_id: str,
        artifact_ids: set[str],
        *,
        owner_run_id: str,
    ) -> int:
        session_id = _safe_id(session_id, "session_id")
        owner_run_id = _safe_id(owner_run_id, "owner_run_id")
        wanted = {_safe_id(item, "artifact_id") for item in artifact_ids}
        if not wanted:
            return 0
        with self._session_lock(session_id):
            manifest = self._load_manifest(session_id)
            records = {
                str(record.get("artifact_id") or ""): record
                for record in manifest["artifacts"]
                if str(record.get("artifact_id") or "") in wanted
            }
            if set(records) != wanted:
                raise ArtifactConflictError("artifact commit set is incomplete")
            for record in records.values():
                state = str(record.get("commit_state") or "committed")
                if state not in {"pending", "committed"}:
                    raise ArtifactConflictError("artifact commit state is invalid")
                if record.get("owner_run_id") != owner_run_id:
                    raise ArtifactConflictError(
                        "artifact pending owner does not match committing run"
                    )
            changed = 0
            for record in records.values():
                if str(record.get("commit_state") or "committed") == "committed":
                    continue
                record["commit_state"] = "committed"
                changed += 1
            if changed:
                self._save_manifest(session_id, manifest)
            return changed

    @_migration_guarded_artifact_write
    def discard_pending_artifacts(
        self,
        session_id: str,
        artifact_ids: set[str],
        *,
        owner_run_id: str,
    ) -> int:
        session_id = _safe_id(session_id, "session_id")
        owner_run_id = _safe_id(owner_run_id, "owner_run_id")
        wanted = {_safe_id(item, "artifact_id") for item in artifact_ids}
        if not wanted:
            return 0
        with self._session_lock(session_id):
            manifest = self._load_manifest(session_id)
            removed = [
                item for item in manifest["artifacts"]
                if str(item.get("artifact_id") or "") in wanted
                and str(item.get("commit_state") or "committed") == "pending"
                and item.get("owner_run_id") == owner_run_id
            ]
            if not removed:
                return 0
            removed_ids = {str(item.get("artifact_id") or "") for item in removed}
            manifest["artifacts"] = [
                item for item in manifest["artifacts"]
                if str(item.get("artifact_id") or "") not in removed_ids
            ]
            self._save_manifest(session_id, manifest)
            for item in removed:
                path = Path(str(item.get("storage_path") or ""))
                try:
                    if path.parent.resolve() == self._session_dir(session_id).resolve():
                        path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Failed to remove pending artifact", exc_info=True)
            return len(removed)

    def authorize(self, session_id: str, artifact_id: str) -> AuthorizedArtifact:
        session_id = _safe_id(session_id, "session_id")
        artifact_id = _safe_id(artifact_id, "artifact_id")
        with self._session_lock(session_id):
            manifest = self._load_manifest(session_id)
        record = next(
            (item for item in manifest["artifacts"] if item.get("artifact_id") == artifact_id),
            None,
        )
        if record is None:
            for manifest_path in self.root.glob("*/manifest.json"):
                if manifest_path.parent.name == session_id:
                    continue
                try:
                    other = json.loads(manifest_path.read_text("utf-8"))
                except Exception:
                    continue
                if any(item.get("artifact_id") == artifact_id for item in other.get("artifacts") or []):
                    raise PermissionError("artifact belongs to another session")
            raise FileNotFoundError("artifact not found")
        if record.get("owner_session_id") != session_id or record.get("status") != "ready":
            raise PermissionError("artifact is not available")
        if str(record.get("commit_state") or "committed") != "committed":
            raise PermissionError("artifact is not committed")
        target = Path(str(record.get("storage_path") or ""))
        session_dir = self._session_dir(session_id).resolve()
        if target.is_symlink():
            raise PermissionError("artifact path is not a regular file")
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(session_dir)
        except (OSError, ValueError) as exc:
            raise FileNotFoundError("artifact file is unavailable") from exc
        try:
            data = _read_regular_file_nofollow(resolved, max_bytes=self.max_bytes)
            if len(data) != int(record.get("size") or -1):
                raise FileNotFoundError("artifact file is damaged")
            if hashlib.sha256(data).hexdigest() != str(record.get("sha256") or ""):
                raise FileNotFoundError("artifact file is damaged")
            validate_image_bytes(
                data,
                max_bytes=self.max_bytes,
                max_pixels=self.max_pixels,
                declared_mime=str(record.get("mime") or ""),
            )
        except (ArtifactValidationError, ValueError, OSError) as exc:
            raise FileNotFoundError("artifact file is damaged") from exc
        return AuthorizedArtifact(
            data=data,
            mime=str(record.get("mime") or "application/octet-stream"),
            name=Path(str(record.get("name") or "artifact")).name,
            size=len(data),
            _path=resolved,
        )

    def generated_source_is_allowed(self, source_path: Path) -> bool:
        source = Path(source_path).expanduser()
        if source.is_symlink():
            return False
        try:
            resolved = source.resolve(strict=True)
        except OSError:
            return False
        for root in self.allowed_source_roots:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    @_migration_guarded_artifact_write
    def retire_session(self, session_id: str, *, now: float | None = None) -> Path | None:
        session_id = _safe_id(session_id, "session_id")
        with self._session_lock(session_id):
            session_dir = self._session_dir(session_id)
            if not session_dir.exists():
                return None
            retired_at = float(time.time() if now is None else now)
            trash = self.root / ".trash"
            trash.mkdir(parents=True, exist_ok=True)
            destination = trash / f"{session_dir.name}--{int(retired_at * 1000)}--{uuid.uuid4().hex[:8]}"
            os.replace(session_dir, destination)
            try:
                _atomic_write(
                    destination / ".retired.json",
                    json.dumps({"session_id": session_id, "retired_at": retired_at}).encode("utf-8"),
                )
            except Exception:
                try:
                    os.replace(destination, session_dir)
                except Exception:
                    logger.critical(
                        "Artifact retirement rollback failed for %s", session_id,
                        exc_info=True,
                    )
                raise
        return destination

    @_migration_guarded_artifact_write
    def restore_session(self, retired: Path) -> None:
        """Restore one just-retired directory when the enclosing mutation fails."""
        retired = Path(retired)
        try:
            metadata = json.loads((retired / ".retired.json").read_text("utf-8"))
            session_id = _safe_id(metadata.get("session_id"), "session_id")
        except Exception as exc:
            raise ArtifactValidationError("retired artifact metadata is unreadable") from exc
        with self._session_lock(session_id):
            destination = self._session_dir(session_id)
            if destination.exists():
                raise ArtifactValidationError("artifact session already exists")
            os.replace(retired, destination)
            (destination / ".retired.json").unlink(missing_ok=True)

    @_migration_guarded_artifact_write
    def discard_unpublished_session(self, session_id: str) -> bool:
        """Remove artifacts for a fresh session that was never published.

        Callers must use this only while rolling back creation of a new,
        unobservable session id.  Existing sessions use ``retire_session`` so
        the seven-day recovery contract remains intact.
        """
        session_id = _safe_id(session_id, "session_id")
        with self._session_lock(session_id):
            session_dir = self._session_dir(session_id)
            if not session_dir.exists():
                return False
            if session_dir.is_symlink():
                raise ArtifactValidationError("artifact session path is not a directory")
            manifest_path = session_dir / "manifest.json"
            # Delete payloads and manifest as separate, observable stages so a
            # caller can verify cleanup and quarantine a partially cleaned
            # unpublished import.  A recursive best-effort delete would hide
            # exactly which durable state survived a failure.
            for entry in sorted(session_dir.iterdir()):
                if entry == manifest_path:
                    continue
                if entry.is_dir() or entry.is_symlink():
                    raise ArtifactValidationError(
                        "unpublished artifact directory contains an invalid entry"
                    )
                entry.unlink()
            manifest_path.unlink(missing_ok=True)
            session_dir.rmdir()
            return True

    @_migration_guarded_artifact_write
    def quarantine_unpublished_session(
        self, session_id: str, *, failed_stages: list[str] | None = None
    ) -> Path:
        """Move unpublished residue outside the authorized artifact namespace."""
        session_id = _safe_id(session_id, "session_id")
        with self._session_lock(session_id):
            quarantine_root = self.root / ".quarantine"
            quarantine_root.mkdir(parents=True, exist_ok=True)
            destination = quarantine_root / (
                f"bundle-import-{session_id}-{uuid.uuid4().hex[:12]}"
            )
            source = self._session_dir(session_id)
            if source.exists():
                if source.is_symlink() or not source.is_dir():
                    raise ArtifactValidationError(
                        "unpublished artifact residue cannot be quarantined"
                    )
                os.replace(source, destination)
            else:
                destination.mkdir(parents=False, exist_ok=False)
            _atomic_write(
                destination / "quarantine.json",
                json.dumps({
                    "schema_version": 1,
                    "reason": "bundle_import_rollback_incomplete",
                    "failed_stages": sorted(set(failed_stages or [])),
                    "created_at": time.time(),
                }, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            return destination

    @_migration_guarded_artifact_write
    def rollback_registered_artifacts(
        self, session_id: str, artifact_ids: set[str]
    ) -> int:
        """Remove only artifacts created by a failed persistence transaction.

        This compensates both migration batches and chat writeback failures.
        It is intentionally narrower than session retirement: an existing
        session may already own durable artifacts that must survive rollback.
        """
        session_id = _safe_id(session_id, "session_id")
        wanted = {_safe_id(value, "artifact_id") for value in artifact_ids}
        if not wanted:
            return 0
        with self._session_lock(session_id):
            session_dir = self._session_dir(session_id)
            manifest = self._load_manifest(session_id)
            removed = [
                record for record in manifest["artifacts"]
                if str(record.get("artifact_id") or "") in wanted
            ]
            if not removed:
                return 0
            removed_ids = {str(record.get("artifact_id")) for record in removed}
            remaining = [
                record for record in manifest["artifacts"]
                if str(record.get("artifact_id") or "") not in removed_ids
            ]
            targets: list[Path] = []
            for record in removed:
                target = Path(str(record.get("storage_path") or ""))
                if (
                    target.parent.resolve() != session_dir.resolve()
                    or target.is_symlink()
                ):
                    raise ArtifactValidationError(
                        "registered artifact rollback target is invalid"
                    )
                targets.append(target)

            unlink_failures: list[Path] = []
            for target in targets:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Failed to rollback registered artifact", exc_info=True)
                    unlink_failures.append(target)

            if unlink_failures:
                quarantine_root = self.root / ".quarantine"
                quarantine_root.mkdir(parents=True, exist_ok=True)
                quarantine_dir = quarantine_root / (
                    f"migration-rollback-{session_id}-{uuid.uuid4().hex[:12]}"
                )
                quarantine_dir.mkdir(parents=False, exist_ok=False)
                try:
                    for target in unlink_failures:
                        if target.exists():
                            os.replace(target, quarantine_dir / target.name)
                    _atomic_write(
                        quarantine_dir / "quarantine.json",
                        json.dumps({
                            "schema_version": 1,
                            "reason": "migration_artifact_rollback_unlink_failed",
                            "created_at": time.time(),
                        }, ensure_ascii=False, indent=2).encode("utf-8"),
                    )
                except Exception as exc:
                    raise ArtifactValidationError(
                        "registered artifact rollback quarantine failed"
                    ) from exc

            manifest["artifacts"] = remaining
            if remaining:
                self._save_manifest(session_id, manifest)
            elif session_dir.exists() and not session_dir.is_symlink():
                self._manifest_path(session_id).unlink(missing_ok=True)
                session_dir.rmdir()

            if remaining:
                expected_files = {
                    Path(str(record.get("storage_path") or "")).name
                    for record in remaining
                }
                actual_files = {
                    entry.name for entry in session_dir.iterdir()
                    if entry.name != "manifest.json"
                }
                if actual_files != expected_files:
                    raise ArtifactValidationError(
                        "registered artifact rollback directory verification failed"
                    )
            elif session_dir.exists():
                raise ArtifactValidationError(
                    "registered artifact rollback directory verification failed"
                )

            if unlink_failures:
                raise ArtifactValidationError(
                    "registered artifact rollback required quarantine"
                )
            return len(removed)

    @_migration_guarded_artifact_write
    def cleanup_retired(self, *, now: float | None = None) -> int:
        cutoff = float(time.time() if now is None else now) - _RETENTION_SECONDS
        trash = self.root / ".trash"
        if not trash.exists():
            return 0
        removed = 0
        for entry in trash.iterdir():
            if not entry.is_dir() or entry.is_symlink():
                continue
            try:
                meta = json.loads((entry / ".retired.json").read_text("utf-8"))
                retired_at = float(meta.get("retired_at"))
            except Exception:
                continue
            if retired_at <= cutoff:
                shutil.rmtree(entry)
                removed += 1
        return removed


def _decode_structured_result(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def image_artifact_candidate_from_tool_completion(
    *,
    tool_name: str,
    tool_call_id: str,
    structured_result: Any,
    is_error: bool = False,
    allow_internal_image: bool = False,
) -> dict | None:
    """Return the private, minimal image envelope shared by all chat backends."""
    if is_error or str(tool_name or "") != "image_generate":
        return None
    payload = _decode_structured_result(structured_result)
    if payload.get("success") is not True:
        return None
    call_id = str(tool_call_id or "").strip()
    if not call_id:
        return None
    image_ref = payload.get("image_ref")
    if isinstance(image_ref, str):
        image_ref = image_ref.strip()
        digest = str(payload.get("sha256") or "").strip().lower()
        if (
            not image_ref
            or image_ref in {".", ".."}
            or Path(image_ref).name != image_ref
            or "/" in image_ref
            or "\\" in image_ref
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            return None
        safe_result = {
            "success": True, "image_ref": image_ref, "sha256": digest
        }
    elif allow_internal_image:
        image = payload.get("image")
        if not isinstance(image, str) or not image.strip():
            return None
        safe_result = {"success": True, "image": image.strip()}
    else:
        return None
    return {
        "tool_name": "image_generate",
        "tool_call_id": call_id,
        "structured_result": safe_result,
    }


def ingest_image_artifact_candidates(
    registry: ArtifactRegistry,
    *,
    session_id: str,
    turn_id: str,
    candidates: list[dict],
    owner_run_id: str | None = None,
    return_created_ids: bool = False,
) -> Any:
    """Promote each tool call at most once, regardless of callback duplication."""
    artifacts: list[dict] = []
    errors: list[str] = []
    created_ids: set[str] = set()
    seen_tool_call_ids: set[str] = set()
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        tool_call_id = str(candidate.get("tool_call_id") or "").strip()
        if not tool_call_id or tool_call_id in seen_tool_call_ids:
            continue
        seen_tool_call_ids.add(tool_call_id)
        try:
            rows, row_created_ids = ingest_image_tool_result(
                registry,
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                tool_name=str(candidate.get("tool_name") or ""),
                structured_result=candidate.get("structured_result"),
                owner_run_id=owner_run_id,
                _include_pending_owner=True,
            )
            artifacts.extend(rows)
            created_ids.update(row_created_ids)
        except Exception as exc:
            logger.warning(
                "Failed to promote generated image artifact: %s", type(exc).__name__
            )
            errors.append("generated image could not be persisted")
    if return_created_ids:
        return artifacts, errors, created_ids
    return artifacts, errors


def _decode_base64_image(encoded: str, *, max_bytes: int) -> bytes:
    """Reject malformed/oversize data before allocating its decoded payload."""
    if not isinstance(encoded, str) or not encoded:
        raise ArtifactValidationError("invalid base64 image")
    max_encoded_length = 4 * ((int(max_bytes) + 2) // 3)
    if len(encoded) > max_encoded_length:
        raise ArtifactValidationError("image exceeds size limit")
    if len(encoded) % 4:
        raise ArtifactValidationError("invalid base64 image")
    padding = len(encoded) - len(encoded.rstrip("="))
    if padding > 2 or "=" in encoded[: len(encoded) - padding if padding else None]:
        raise ArtifactValidationError("invalid base64 image")
    try:
        encoded_bytes = encoded.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ArtifactValidationError("invalid base64 image") from exc
    estimated_size = 3 * (len(encoded_bytes) // 4) - padding
    if estimated_size > max_bytes:
        raise ArtifactValidationError("image exceeds size limit")
    try:
        return base64.b64decode(encoded_bytes, validate=True)
    except Exception as exc:
        raise ArtifactValidationError("invalid base64 image") from exc


def ingest_image_tool_result(
    registry: ArtifactRegistry,
    *,
    session_id: str,
    turn_id: str,
    tool_call_id: str,
    tool_name: str,
    structured_result: Any,
    owner_run_id: str | None = None,
    _include_pending_owner: bool = False,
) -> Any:
    def _result(public: dict, owned_pending: bool):
        rows = [public]
        return (rows, {public["artifact_id"]} if owned_pending else set()) \
            if _include_pending_owner else rows

    def _empty():
        return ([], set()) if _include_pending_owner else []

    if str(tool_name or "") != "image_generate":
        return _empty()
    payload = _decode_structured_result(structured_result)
    if payload.get("success") is not True:
        return _empty()
    image_ref = payload.get("image_ref")
    if isinstance(image_ref, str) and image_ref.strip():
        image_ref = image_ref.strip()
        digest = str(payload.get("sha256") or "").strip().lower()
        if (
            image_ref in {".", ".."}
            or Path(image_ref).name != image_ref
            or "/" in image_ref
            or "\\" in image_ref
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ArtifactValidationError("invalid generated image reference")
        for root in registry.allowed_source_roots:
            source_path = root / image_ref
            if registry.generated_source_is_allowed(source_path):
                public, owned_pending = registry.register_image_file(
                    session_id,
                    turn_id,
                    tool_call_id,
                    source_path,
                    name=image_ref,
                    expected_sha256=digest,
                    owner_run_id=owner_run_id,
                    _include_pending_owner=True,
                )
                return _result(public, owned_pending)
        raise ArtifactValidationError("generated image reference is unavailable")
    image = payload.get("image")
    if not isinstance(image, str) or not image.strip():
        return _empty()
    image = image.strip()
    if image.startswith("data:image/") and ";base64," in image:
        header, encoded = image.split(",", 1)
        declared = header[5:].split(";", 1)[0]
        data = _decode_base64_image(encoded, max_bytes=registry.max_bytes)
        public, owned_pending = registry.register_image_bytes(
            session_id, turn_id, tool_call_id, data, mime=declared,
            name="generated-image",
            owner_run_id=owner_run_id,
            _include_pending_owner=True,
        )
        return _result(public, owned_pending)
    if image.startswith(("http://", "https://")):
        raise ArtifactValidationError("remote image candidates must be cached by Agent")
    source_path = Path(image)
    if not registry.generated_source_is_allowed(source_path):
        raise ArtifactValidationError("image source is outside the generated image cache")
    public, owned_pending = registry.register_image_file(
        session_id,
        turn_id,
        tool_call_id,
        source_path,
        name=source_path.name,
        owner_run_id=owner_run_id,
        _include_pending_owner=True,
    )
    return _result(public, owned_pending)
