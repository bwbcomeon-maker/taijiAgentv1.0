"""
Image Generation Provider ABC
=============================

Defines the pluggable-backend interface for image generation. Providers register
instances via ``PluginContext.register_image_gen_provider()``; the active one
(selected via ``image_gen.provider`` in ``config.yaml``) services every
``image_generate`` tool call.

Providers live in ``<repo>/plugins/image_gen/<name>/`` (built-in, auto-loaded
as ``kind: backend``) or ``~/.hermes/plugins/image_gen/<name>/`` (user, opt-in
via ``plugins.enabled``).

Response shape
--------------
All providers return a dict that :func:`success_response` / :func:`error_response`
produce. The tool wrapper JSON-serializes it. Keys:

    success        bool
    image          str | None       URL or absolute file path
    model          str              provider-specific model identifier
    prompt         str              echoed prompt
    aspect_ratio   str              "landscape" | "square" | "portrait"
    provider       str              provider name (for diagnostics)
    error          str              only when success=False
    error_type     str              only when success=False
"""

from __future__ import annotations

import abc
import base64
import datetime
import http.client
import hashlib
import ipaddress
import logging
import os
import socket
import ssl
import stat
import struct
import uuid
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


VALID_ASPECT_RATIOS: Tuple[str, ...] = ("landscape", "square", "portrait")
DEFAULT_ASPECT_RATIO = "landscape"


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------


class ImageGenProvider(abc.ABC):
    """Abstract base class for an image generation backend.

    Subclasses must implement :meth:`generate`. Everything else has sane
    defaults — override only what your provider needs.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Stable short identifier used in ``image_gen.provider`` config.

        Lowercase, no spaces. Examples: ``fal``, ``openai``, ``replicate``.
        """

    @property
    def display_name(self) -> str:
        """Human-readable label shown in ``hermes tools``. Defaults to ``name.title()``."""
        return self.name.title()

    def is_available(self) -> bool:
        """Return True when this provider can service calls.

        Typically checks for a required API key. Default: True
        (providers with no external dependencies are always available).
        """
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        """Return catalog entries for ``hermes tools`` model picker.

        Each entry::

            {
                "id": "gpt-image-1.5",               # required
                "display": "GPT Image 1.5",          # optional; defaults to id
                "speed": "~10s",                     # optional
                "strengths": "...",                  # optional
                "price": "$...",                     # optional
            }

        Default: empty list (provider has no user-selectable models).
        """
        return []

    def get_setup_schema(self) -> Dict[str, Any]:
        """Return provider metadata for the ``hermes tools`` picker.

        Used by ``tools_config.py`` to inject this provider as a row in
        the Image Generation provider list. Shape::

            {
                "name": "OpenAI",                     # picker label
                "badge": "paid",                      # optional short tag
                "tag": "One-line description...",     # optional subtitle
                "env_vars": [                         # keys to prompt for
                    {"key": "OPENAI_API_KEY",
                     "prompt": "OpenAI API key",
                     "url": "https://platform.openai.com/api-keys"},
                ],
            }

        Default: minimal entry derived from ``display_name``. Override to
        expose API key prompts and custom badges.
        """
        return {
            "name": self.display_name,
            "badge": "",
            "tag": "",
            "env_vars": [],
        }

    def default_model(self) -> Optional[str]:
        """Return the default model id, or None if not applicable."""
        models = self.list_models()
        if models:
            return models[0].get("id")
        return None

    @abc.abstractmethod
    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Generate an image.

        Implementations should return the dict from :func:`success_response`
        or :func:`error_response`. ``kwargs`` may contain forward-compat
        parameters future versions of the schema will expose — implementations
        should ignore unknown keys.
        """


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def resolve_aspect_ratio(value: Optional[str]) -> str:
    """Clamp an aspect_ratio value to the valid set, defaulting to landscape.

    Invalid values are coerced rather than rejected so the tool surface is
    forgiving of agent mistakes.
    """
    if not isinstance(value, str):
        return DEFAULT_ASPECT_RATIO
    v = value.strip().lower()
    if v in VALID_ASPECT_RATIOS:
        return v
    return DEFAULT_ASPECT_RATIO


def _images_cache_dir() -> Path:
    """Return ``$HERMES_HOME/cache/images/``, creating parents as needed."""
    from hermes_constants import get_hermes_home

    path = get_hermes_home() / "cache" / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_b64_image(
    b64_data: str,
    *,
    prefix: str = "image",
    extension: str = "png",
    max_bytes: int = 25 * 1024 * 1024,
    max_pixels: int = 40_000_000,
) -> Path:
    """Decode base64 image data and write it under ``$HERMES_HOME/cache/images/``.

    Returns the absolute :class:`Path` to the saved file.

    Filename format: ``<prefix>_<YYYYMMDD_HHMMSS>_<short-uuid>.<ext>``.
    """
    if not isinstance(b64_data, str):
        raise ValueError("invalid base64 image")
    # Reject before decoding when the encoded payload is already guaranteed to
    # exceed the binary cap. This prevents a malicious response from forcing an
    # unbounded temporary allocation.
    if len(b64_data) > ((max_bytes + 2) // 3) * 4 + 8:
        raise ValueError("Image exceeds size limit")
    try:
        raw = base64.b64decode(b64_data, validate=True)
    except Exception as exc:
        raise ValueError("invalid base64 image") from exc
    mime, actual_extension, _width, _height = validate_image_bytes(
        raw, max_bytes=max_bytes, max_pixels=max_pixels
    )
    del mime
    requested_extension = str(extension or "").lower().lstrip(".")
    if requested_extension and requested_extension not in {
        actual_extension, "jpeg" if actual_extension == "jpg" else actual_extension,
    }:
        raise ValueError("Image extension does not match image format")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    path = _images_cache_dir() / f"{prefix}_{ts}_{short}.{actual_extension}"
    _atomic_cache_write(path, raw)
    return path


# Extension inference for save_url_image — keep small and explicit.  We don't
# want to import mimetypes for a handful of formats every image_gen provider
# actually returns, and we never want to inherit a content-type that points
# at HTML or JSON when the API gives us a degenerate response.
_URL_IMAGE_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
}
SUPPORTED_IMAGE_ACCEPT = "image/png,image/jpeg"


def _png_format(
    data: bytes, *, max_pixels: int, max_decoded_bytes: int
) -> Tuple[str, str, int, int]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("invalid image format")
    offset = 8
    width = height = 0
    saw_ihdr = saw_idat = saw_iend = saw_plte = False
    bit_depth = color_type = 0
    idat_parts: list[bytes] = []
    expected_size = row_bytes = channels = 0
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("invalid PNG structure")
        length = int.from_bytes(data[offset:offset + 4], "big")
        chunk_type = data[offset + 4:offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise ValueError("invalid PNG structure")
        chunk_data = data[offset + 8:offset + 8 + length]
        expected_crc = int.from_bytes(data[offset + 8 + length:chunk_end], "big")
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            raise ValueError("invalid PNG CRC")
        if not saw_ihdr:
            if chunk_type != b"IHDR" or length != 13:
                raise ValueError("invalid PNG IHDR")
            width, height = struct.unpack(">II", chunk_data[:8])
            bit_depth, color_type = chunk_data[8], chunk_data[9]
            if chunk_data[10:12] != b"\x00\x00" or chunk_data[12] != 0:
                raise ValueError("unsupported PNG encoding")
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if bit_depth not in valid_depths.get(color_type, set()):
                raise ValueError("unsupported PNG encoding")
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise ValueError("Image dimensions exceed limit")
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
            row_bytes = (width * channels * bit_depth + 7) // 8
            expected_size = (row_bytes + 1) * height
            if expected_size > max_decoded_bytes:
                raise ValueError("Image decoded size exceeds limit")
            saw_ihdr = True
        elif chunk_type == b"IHDR":
            raise ValueError("duplicate PNG IHDR")
        if chunk_type == b"PLTE":
            if saw_plte or saw_idat or color_type in {0, 4}:
                raise ValueError("invalid PNG palette")
            if not chunk_data or len(chunk_data) % 3 or len(chunk_data) > 256 * 3:
                raise ValueError("invalid PNG palette")
            if color_type == 3 and len(chunk_data) // 3 > 1 << bit_depth:
                raise ValueError("PNG palette exceeds bit depth")
            saw_plte = True
        if chunk_type == b"IDAT":
            saw_idat = True
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            if length != 0 or chunk_end != len(data):
                raise ValueError("invalid PNG IEND")
            saw_iend = True
        offset = chunk_end
    if not (saw_ihdr and saw_idat and saw_iend):
        raise ValueError("incomplete PNG")
    if color_type == 3 and not saw_plte:
        raise ValueError("indexed PNG is missing palette")
    inflater = zlib.decompressobj()
    try:
        pixels = inflater.decompress(b"".join(idat_parts), expected_size)
        while inflater.unconsumed_tail:
            pending = inflater.unconsumed_tail
            extra = inflater.decompress(pending, 1)
            if extra or inflater.unconsumed_tail == pending:
                raise ValueError("invalid PNG pixel stream")
        flushed = inflater.flush(max(1, expected_size - len(pixels)))
        if len(pixels) + len(flushed) > expected_size:
            raise ValueError("invalid PNG pixel stream")
        pixels += flushed
    except zlib.error as exc:
        raise ValueError("invalid PNG pixel stream") from exc
    if (
        len(pixels) != expected_size
        or not inflater.eof
        or inflater.unused_data
        or inflater.unconsumed_tail
    ):
        raise ValueError("invalid PNG pixel stream")

    bytes_per_pixel = max(1, (channels * bit_depth + 7) // 8)
    previous = bytearray(row_bytes)
    cursor = 0
    for _row in range(height):
        filter_type = pixels[cursor]
        cursor += 1
        if filter_type > 4:
            raise ValueError("invalid PNG scanline filter")
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


def _gif_skip_sub_blocks(data: bytes, offset: int) -> int:
    while True:
        if offset >= len(data):
            raise ValueError("incomplete GIF block")
        size = data[offset]
        offset += 1
        if size == 0:
            return offset
        if offset + size > len(data):
            raise ValueError("incomplete GIF block")
        offset += size


def _gif_format(data: bytes) -> Tuple[str, str, int, int]:
    if not data.startswith((b"GIF87a", b"GIF89a")) or len(data) < 13:
        raise ValueError("invalid image format")
    width, height = struct.unpack("<HH", data[6:10])
    packed = data[10]
    offset = 13
    if packed & 0x80:
        offset += 3 * (2 ** ((packed & 0x07) + 1))
    if offset > len(data):
        raise ValueError("incomplete GIF color table")
    saw_image = False
    while offset < len(data):
        marker = data[offset]
        offset += 1
        if marker == 0x3B:
            if not saw_image or offset != len(data):
                raise ValueError("invalid GIF trailer")
            return "image/gif", "gif", width, height
        if marker == 0x21:
            if offset >= len(data):
                raise ValueError("incomplete GIF extension")
            offset += 1  # extension label
            offset = _gif_skip_sub_blocks(data, offset)
            continue
        if marker != 0x2C or offset + 9 > len(data):
            raise ValueError("invalid GIF block")
        descriptor = data[offset:offset + 9]
        offset += 9
        if descriptor[8] & 0x80:
            offset += 3 * (2 ** ((descriptor[8] & 0x07) + 1))
        if offset >= len(data):
            raise ValueError("incomplete GIF image")
        offset += 1  # LZW minimum code size
        offset = _gif_skip_sub_blocks(data, offset)
        saw_image = True
    raise ValueError("missing GIF trailer")


def _webp_format(data: bytes) -> Tuple[str, str, int, int]:
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("invalid image format")
    if int.from_bytes(data[4:8], "little") + 8 != len(data):
        raise ValueError("invalid WebP RIFF length")
    offset = 12
    dimensions: Tuple[int, int] | None = None
    saw_image = False
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("incomplete WebP chunk")
        chunk_type = data[offset:offset + 4]
        length = int.from_bytes(data[offset + 4:offset + 8], "little")
        body_start = offset + 8
        body_end = body_start + length
        padded_end = body_end + (length & 1)
        if body_end > len(data) or padded_end > len(data):
            raise ValueError("incomplete WebP chunk")
        body = data[body_start:body_end]
        if chunk_type == b"VP8X" and length >= 10:
            dimensions = (
                int.from_bytes(body[4:7], "little") + 1,
                int.from_bytes(body[7:10], "little") + 1,
            )
        elif chunk_type == b"VP8L" and length >= 5 and body[0] == 0x2F:
            bits = int.from_bytes(body[1:5], "little")
            dimensions = ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
            saw_image = True
        elif chunk_type == b"VP8 " and length >= 10 and body[3:6] == b"\x9d\x01\x2a":
            dimensions = (
                int.from_bytes(body[6:8], "little") & 0x3FFF,
                int.from_bytes(body[8:10], "little") & 0x3FFF,
            )
            saw_image = True
        offset = padded_end
    if offset != len(data) or dimensions is None or not saw_image:
        raise ValueError("incomplete WebP")
    return "image/webp", "webp", dimensions[0], dimensions[1]


def _jpeg_quantization_tables(payload: bytes, tables: set[int]) -> None:
    offset = 0
    while offset < len(payload):
        table_info = payload[offset]
        offset += 1
        precision, table_id = table_info >> 4, table_info & 0x0F
        if precision not in {0, 1} or table_id > 3:
            raise ValueError("invalid JPEG quantization table")
        value_size = precision + 1
        table_size = 64 * value_size
        if offset + table_size > len(payload):
            raise ValueError("truncated JPEG quantization table")
        raw_values = payload[offset:offset + table_size]
        offset += table_size
        values = (
            list(raw_values)
            if value_size == 1
            else [int.from_bytes(raw_values[index:index + 2], "big") for index in range(0, table_size, 2)]
        )
        if any(value == 0 for value in values):
            raise ValueError("invalid JPEG quantization value")
        tables.add(table_id)


def _jpeg_huffman_tables(payload: bytes, tables: dict) -> None:
    offset = 0
    while offset < len(payload):
        table_info = payload[offset]
        offset += 1
        table_class, table_id = table_info >> 4, table_info & 0x0F
        if table_class not in {0, 1} or table_id > 3 or offset + 16 > len(payload):
            raise ValueError("invalid JPEG Huffman table")
        counts = payload[offset:offset + 16]
        offset += 16
        symbol_count = sum(counts)
        if not symbol_count or offset + symbol_count > len(payload):
            raise ValueError("invalid JPEG Huffman table")
        symbols = payload[offset:offset + symbol_count]
        offset += symbol_count
        mapping: dict[tuple[int, int], int] = {}
        code = symbol_index = 0
        for bit_length, count in enumerate(counts, 1):
            if code + count > 1 << bit_length:
                raise ValueError("oversubscribed JPEG Huffman table")
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
            raise ValueError("truncated JPEG entropy stream")
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
        raise ValueError("invalid JPEG Huffman code")


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
                raise ValueError("missing JPEG Huffman table")
            for _block in range(horizontal * vertical):
                dc_size = bits.symbol(dc_table)
                if dc_size > 11:
                    raise ValueError("invalid JPEG DC coefficient")
                bits.read(dc_size)
                coefficient = 1
                while coefficient < 64:
                    symbol = bits.symbol(ac_table)
                    run, size = symbol >> 4, symbol & 0x0F
                    if size == 0:
                        if run == 0:
                            break
                        if run != 15:
                            raise ValueError("invalid JPEG AC coefficient")
                        coefficient += 16
                    else:
                        if size > 10:
                            raise ValueError("invalid JPEG AC coefficient")
                        coefficient += run
                        if coefficient >= 64:
                            raise ValueError("invalid JPEG AC run")
                        bits.read(size)
                        coefficient += 1
    remaining = len(payload) * 8 - bits.position
    if remaining > 7 or (remaining and bits.read(remaining) != (1 << remaining) - 1):
        raise ValueError("invalid JPEG entropy padding")


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
            raise ValueError("missing JPEG EOI")
        marker = data[offset]
        offset += 1
        if marker == 0:
            if fill_count != 1:
                raise ValueError("invalid JPEG byte stuffing")
            parts[-1].append(0xFF)
        elif 0xD0 <= marker <= 0xD7:
            restart_markers.append(marker)
            parts.append(bytearray())
        elif marker == 0xD9:
            if offset != len(data):
                raise ValueError("invalid JPEG EOI")
            return [bytes(part) for part in parts], restart_markers
        else:
            raise ValueError("unsupported JPEG scan structure")
    raise ValueError("missing JPEG EOI")


def _jpeg_format(
    data: bytes, *, max_pixels: int, max_decoded_bytes: int
) -> Tuple[str, str, int, int]:
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("invalid image format")
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
            raise ValueError("invalid JPEG segment")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            raise ValueError("incomplete JPEG marker")
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            raise ValueError("invalid JPEG marker")
        if offset + 2 > len(data):
            raise ValueError("incomplete JPEG segment")
        length = int.from_bytes(data[offset:offset + 2], "big")
        if length < 2 or offset + length > len(data):
            raise ValueError("invalid JPEG segment length")
        payload = data[offset + 2:offset + length]
        offset += length
        if marker in all_sof and marker != 0xC0:
            raise ValueError("unsupported JPEG frame type")
        if marker == 0xC0:
            if saw_sof or len(payload) < 6 or payload[0] != 8:
                raise ValueError("invalid JPEG SOF0")
            height = int.from_bytes(payload[1:3], "big")
            width = int.from_bytes(payload[3:5], "big")
            component_count = payload[5]
            if component_count not in {1, 3, 4} or len(payload) != 6 + 3 * component_count:
                raise ValueError("invalid JPEG SOF0")
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise ValueError("Image dimensions exceed limit")
            if width * height * component_count > max_decoded_bytes:
                raise ValueError("Image decoded size exceeds limit")
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
                    raise ValueError("invalid JPEG sampling factors")
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
                raise ValueError("invalid JPEG restart interval")
            restart_interval = int.from_bytes(payload, "big")
        elif marker == 0xDA:
            if not saw_sof or len(payload) < 6:
                raise ValueError("invalid JPEG SOS")
            scan_count = payload[0]
            if len(payload) != 1 + 2 * scan_count + 3 or scan_count != len(frame_components):
                raise ValueError("unsupported JPEG scan structure")
            scan_components: list[tuple[int, int, int]] = []
            for index in range(scan_count):
                component_id = payload[1 + index * 2]
                selectors = payload[2 + index * 2]
                if component_id not in frame_components or any(row[0] == component_id for row in scan_components):
                    raise ValueError("invalid JPEG scan component")
                scan_components.append((component_id, selectors >> 4, selectors & 0x0F))
            if payload[-3:] != b"\x00\x3f\x00":
                raise ValueError("unsupported JPEG scan structure")
            if any(
                component[2] not in quantization_tables
                for component in frame_components.values()
            ):
                raise ValueError("missing JPEG quantization table")
            entropy_parts, restart_markers = _jpeg_scan_parts(data, offset)
            max_horizontal = max(value[0] for value in frame_components.values())
            max_vertical = max(value[1] for value in frame_components.values())
            mcu_columns = (width + 8 * max_horizontal - 1) // (8 * max_horizontal)
            mcu_rows = (height + 8 * max_vertical - 1) // (8 * max_vertical)
            total_mcus = mcu_columns * mcu_rows
            if restart_markers:
                if not restart_interval:
                    raise ValueError("JPEG restart marker without interval")
                for index, value in enumerate(restart_markers):
                    if value != 0xD0 + (index % 8):
                        raise ValueError("invalid JPEG restart sequence")
            expected_parts = (
                (total_mcus + restart_interval - 1) // restart_interval
                if restart_interval else 1
            )
            if len(entropy_parts) != expected_parts:
                raise ValueError("invalid JPEG restart interval")
            remaining_mcus = total_mcus
            for part in entropy_parts:
                part_mcus = min(restart_interval, remaining_mcus) if restart_interval else remaining_mcus
                _decode_jpeg_entropy_segment(
                    part, part_mcus, scan_components, frame_components, tables
                )
                remaining_mcus -= part_mcus
            if remaining_mcus:
                raise ValueError("truncated JPEG entropy stream")
            return "image/jpeg", "jpg", width, height
    raise ValueError("missing JPEG SOS")


def _image_format(
    data: bytes, *, max_pixels: int, max_decoded_bytes: int
) -> Tuple[str, str, int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _png_format(
            data, max_pixels=max_pixels, max_decoded_bytes=max_decoded_bytes
        )
    if data.startswith((b"GIF87a", b"GIF89a")):
        raise ValueError("unsupported image format: supported formats are PNG and baseline JPEG")
    if data.startswith(b"RIFF"):
        raise ValueError("unsupported image format: supported formats are PNG and baseline JPEG")
    if data.startswith(b"\xff\xd8"):
        return _jpeg_format(
            data, max_pixels=max_pixels, max_decoded_bytes=max_decoded_bytes
        )
    raise ValueError("invalid image format: supported formats are PNG and baseline JPEG")


def validate_image_bytes(
    data: bytes,
    *,
    max_bytes: int = 25 * 1024 * 1024,
    max_pixels: int = 40_000_000,
    max_decoded_bytes: int | None = None,
    declared_mime: str | None = None,
) -> Tuple[str, str, int, int]:
    if not data:
        raise ValueError("Image returned 0 bytes")
    if len(data) > max_bytes:
        raise ValueError("Image exceeds size limit")
    decoded_limit = (
        int(max_decoded_bytes)
        if max_decoded_bytes is not None else int(max_bytes) * 16
    )
    if max_pixels <= 0 or decoded_limit <= 0:
        raise ValueError("invalid image resource limits")
    mime, extension, width, height = _image_format(
        data, max_pixels=int(max_pixels), max_decoded_bytes=decoded_limit
    )
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ValueError("Image dimensions exceed limit")
    declared = str(declared_mime or "").split(";", 1)[0].strip().lower()
    if declared and declared not in {"application/octet-stream", "binary/octet-stream"}:
        if declared == "image/jpg":
            declared = "image/jpeg"
        if declared != mime:
            raise ValueError("Image MIME does not match image format")
    return mime, extension, width, height


def validated_cache_image_ref(image: Any) -> tuple[str, str] | None:
    """Return opaque basename + digest computed from the validated same-fd bytes."""
    if not isinstance(image, str) or not image.strip():
        return None
    candidate = Path(image.strip())
    if not candidate.is_absolute() or candidate.name != candidate.name.strip():
        return None
    cache_dir = _images_cache_dir()
    try:
        if candidate.parent.resolve(strict=True) != cache_dir.resolve(strict=True):
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                return None
            if metadata.st_size <= 0 or metadata.st_size > 25 * 1024 * 1024:
                return None
            chunks: list[bytes] = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    return None
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(descriptor)
        raw = b"".join(chunks)
        validate_image_bytes(raw)
    except (OSError, ValueError):
        return None
    return candidate.name, hashlib.sha256(raw).hexdigest()


def _atomic_cache_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _allow_private_image_downloads() -> bool:
    return os.getenv("HERMES_IMAGE_ALLOW_PRIVATE_NETWORK", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )


def _resolved_image_addresses(
    url: str,
    *,
    resolver: Callable[..., Any] = socket.getaddrinfo,
    url_validator: Callable[[str], bool] | None = None,
    address_validator: Callable[[str, str], bool] | None = None,
) -> Tuple[Any, list[tuple[Any, ...]]]:
    if url_validator is not None:
        try:
            if not url_validator(url):
                raise ValueError("unsafe image URL")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("unsafe image URL") from exc
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("unsafe image URL")
    if parsed.username or parsed.password:
        raise ValueError("unsafe image URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        rows = resolver(parsed.hostname, port, type=socket.SOCK_STREAM)
    except Exception as exc:
        raise ValueError("image URL could not be resolved") from exc
    usable = [tuple(row) for row in rows if len(row) > 4 and row[4]]
    addresses = {str(row[4][0]).split("%", 1)[0] for row in usable}
    if not addresses:
        raise ValueError("image URL could not be resolved")
    if address_validator is not None:
        try:
            if any(
                not address_validator(str(parsed.hostname), value)
                for value in addresses
            ):
                raise ValueError("unsafe image URL")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("unsafe image URL") from exc
    elif not _allow_private_image_downloads() and any(
        not _is_public_ip(value) for value in addresses
    ):
        raise ValueError("unsafe image URL")
    return parsed, usable


def _validate_image_url(
    url: str,
    *,
    resolver: Callable[..., Any] = socket.getaddrinfo,
    url_validator: Callable[[str], bool] | None = None,
    address_validator: Callable[[str, str], bool] | None = None,
) -> None:
    _resolved_image_addresses(
        url,
        resolver=resolver,
        url_validator=url_validator,
        address_validator=address_validator,
    )


def _validate_connected_peer(pinned_ip: str, peer_ip: str) -> None:
    """Reject DNS rebinding or an unexpected proxy after connect()."""
    try:
        pinned = ipaddress.ip_address(str(pinned_ip).split("%", 1)[0])
        peer = ipaddress.ip_address(str(peer_ip).split("%", 1)[0])
    except ValueError as exc:
        raise ValueError("invalid connected peer address") from exc
    if pinned != peer:
        raise ValueError("connected peer does not match pinned image address")


def _url_host_header(parsed: Any) -> str:
    hostname = str(parsed.hostname)
    rendered = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 443 if parsed.scheme == "https" else 80
    return rendered if parsed.port in {None, default_port} else f"{rendered}:{parsed.port}"


def _wrap_pinned_tls_socket(
    raw_socket: Any,
    hostname: str,
    *,
    context_factory: Callable[[], Any] = ssl.create_default_context,
) -> Any:
    """Wrap with the original hostname so certificate and SNI checks stay active."""
    return context_factory().wrap_socket(raw_socket, server_hostname=hostname)


def _pinned_http_get(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    resolver: Callable[..., Any],
    url_validator: Callable[[str], bool] | None = None,
    address_validator: Callable[[str, str], bool] | None = None,
) -> Tuple[int, Dict[str, str], bytes]:
    """GET through one pre-validated address, without proxies or a second DNS lookup."""
    parsed, rows = _resolved_image_addresses(
        url,
        resolver=resolver,
        url_validator=url_validator,
        address_validator=address_validator,
    )
    family, sock_type, proto, _canon, sockaddr = rows[0]
    raw_socket = socket.socket(family, sock_type, proto)
    connection: http.client.HTTPConnection | None = None
    try:
        raw_socket.settimeout(timeout)
        raw_socket.connect(sockaddr)
        pinned_ip = str(sockaddr[0]).split("%", 1)[0]
        _validate_connected_peer(pinned_ip, str(raw_socket.getpeername()[0]))
        transport_socket: Any = raw_socket
        if parsed.scheme == "https":
            transport_socket = _wrap_pinned_tls_socket(
                raw_socket, str(parsed.hostname)
            )
            _validate_connected_peer(
                pinned_ip, str(transport_socket.getpeername()[0])
            )
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        connection = http.client.HTTPConnection(str(parsed.hostname), port, timeout=timeout)
        connection.sock = transport_socket
        target = parsed.path or "/"
        if parsed.query:
            target += f"?{parsed.query}"
        connection.request(
            "GET",
            target,
            headers={
                "Host": _url_host_header(parsed),
                "Accept": SUPPORTED_IMAGE_ACCEPT,
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        declared_length = headers.get("content-length")
        if declared_length:
            try:
                if int(declared_length) > max_bytes:
                    raise ValueError(
                        f"Image exceeds {max_bytes // (1024 * 1024)}MB cap"
                    )
            except ValueError as exc:
                if "exceeds" in str(exc):
                    raise
                raise ValueError("invalid image Content-Length") from exc
        chunks: list[bytes] = []
        bytes_read = 0
        while True:
            chunk = response.read(min(64 * 1024, max_bytes + 1 - bytes_read))
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > max_bytes:
                raise ValueError(
                    f"Image exceeds {max_bytes // (1024 * 1024)}MB cap"
                )
            chunks.append(chunk)
        return response.status, headers, b"".join(chunks)
    finally:
        if connection is not None:
            connection.close()
        else:
            raw_socket.close()


def _injected_http_get(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    request_get: Callable[..., Any],
) -> Tuple[int, Dict[str, str], bytes]:
    """Compatibility seam for deterministic unit tests; never used by production."""
    response = request_get(
        url, timeout=timeout, stream=True, allow_redirects=False,
    )
    try:
        headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
        chunks: list[bytes] = []
        bytes_read = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            bytes_read += len(chunk)
            if bytes_read > max_bytes:
                raise ValueError(
                    f"Image exceeds {max_bytes // (1024 * 1024)}MB cap"
                )
            chunks.append(chunk)
        return int(response.status_code), headers, b"".join(chunks)
    finally:
        response.close()


def save_url_image(
    url: str,
    *,
    prefix: str = "image",
    timeout: float = 60.0,
    max_bytes: int = 25 * 1024 * 1024,
    max_pixels: int = 40_000_000,
    resolver: Callable[..., Any] = socket.getaddrinfo,
    request_get: Callable[..., Any] | None = None,
    max_redirects: int = 5,
    url_validator: Callable[[str], bool] | None = None,
    address_validator: Callable[[str, str], bool] | None = None,
) -> Path:
    """Download an image URL and write it under ``$HERMES_HOME/cache/images/``.

    Used by providers (xAI, fallback OpenAI) whose API returns an *ephemeral*
    URL instead of inline base64 — those URLs frequently expire before a
    downstream consumer (Telegram ``send_photo``, browser fetch) can resolve
    them, so we materialise the bytes locally at tool-completion time.
    Mirrors :func:`save_b64_image`'s shape so providers can swap in one line.

    Returns the absolute :class:`Path` to the saved file.  Raises on any
    network / HTTP / oversize / non-image-content-type error so callers can
    fall back to returning the bare URL with a clear error message.
    """
    current = str(url or "")
    if max_redirects < 0:
        raise ValueError("invalid image redirect limit")
    for redirect_count in range(max_redirects + 1):
        if request_get is None:
            status, headers, raw = _pinned_http_get(
                current,
                timeout=timeout,
                max_bytes=max_bytes,
                resolver=resolver,
                url_validator=url_validator,
                address_validator=address_validator,
            )
        else:
            _validate_image_url(
                current,
                resolver=resolver,
                url_validator=url_validator,
                address_validator=address_validator,
            )
            status, headers, raw = _injected_http_get(
                current,
                timeout=timeout,
                max_bytes=max_bytes,
                request_get=request_get,
            )
        if status in {301, 302, 303, 307, 308}:
            if redirect_count >= max_redirects:
                raise ValueError("Too many image redirects")
            location = headers.get("location")
            if not location:
                raise ValueError("Image redirect missing Location")
            current = urljoin(current, location)
            continue
        if status < 200 or status >= 300:
            raise ValueError(f"Image download HTTP {status}")
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        _mime, extension, _width, _height = validate_image_bytes(
            raw,
            max_bytes=max_bytes,
            max_pixels=max_pixels,
            declared_mime=content_type or None,
        )
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        short = uuid.uuid4().hex[:8]
        path = _images_cache_dir() / f"{prefix}_{ts}_{short}.{extension}"
        _atomic_cache_write(path, raw)
        return path
    raise ValueError("Too many image redirects")


def success_response(
    *,
    image: str,
    model: str,
    prompt: str,
    aspect_ratio: str,
    provider: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a uniform success response dict.

    ``image`` may be an HTTP URL or an absolute filesystem path (for b64
    providers like OpenAI). Callers that need to pass through additional
    backend-specific fields can supply ``extra``.
    """
    payload: Dict[str, Any] = {
        "success": True,
        "image": image,
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "provider": provider,
    }
    if extra:
        for k, v in extra.items():
            payload.setdefault(k, v)
    return payload


def error_response(
    *,
    error: str,
    error_type: str = "provider_error",
    provider: str = "",
    model: str = "",
    prompt: str = "",
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
) -> Dict[str, Any]:
    """Build a uniform error response dict."""
    return {
        "success": False,
        "image": None,
        "error": error,
        "error_type": error_type,
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "provider": provider,
    }
