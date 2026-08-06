#!/usr/bin/env python3
"""Validate the Taiji product icon chain without third-party dependencies."""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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


def parse_png(data: bytes, label: str, expected_size: int | None = None) -> None:
    if data[:8] != PNG_SIGNATURE:
        fail(f"{label} is not a PNG")
    if len(data) < 33:
        fail(f"{label} is truncated")
    length = struct.unpack(">I", data[8:12])[0]
    if data[12:16] != b"IHDR" or length != 13:
        fail(f"{label} has no valid first IHDR chunk")
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", data[16:29]
    )
    if expected_size is not None and (width, height) != (expected_size, expected_size):
        fail(f"{label} must be {expected_size}x{expected_size}, got {width}x{height}")
    if bit_depth != 8 or color_type != 6:
        fail(f"{label} must be 8-bit RGBA PNG (bit_depth=8,color_type=6)")
    if (compression, filtering, interlace) != (0, 0, 0):
        fail(f"{label} uses unsupported PNG encoding flags")
    if b"IEND" not in data:
        fail(f"{label} has no IEND chunk")


def parse_ico(data: bytes, label: str) -> None:
    if len(data) < 6 or struct.unpack("<H", data[:2])[0] != 0 or struct.unpack("<H", data[2:4])[0] != 1:
        fail(f"{label} has an invalid ICO header")
    count = struct.unpack("<H", data[4:6])[0]
    if count < 1 or len(data) < 6 + count * 16:
        fail(f"{label} has no complete icon directory")
    for index in range(count):
        entry = data[6 + index * 16 : 22 + index * 16]
        width = entry[0] or 256
        height = entry[1] or 256
        payload_size, payload_offset = struct.unpack("<II", entry[8:16])
        if width != height or width not in {16, 24, 32, 48, 64, 128, 192, 256, 512}:
            fail(f"{label} has an invalid icon size at entry {index}: {width}x{height}")
        if payload_offset + payload_size > len(data):
            fail(f"{label} has an out-of-range payload at entry {index}")
    if PNG_SIGNATURE not in data:
        fail(f"{label} must contain a PNG-backed icon payload")


def validate(args: argparse.Namespace) -> str:
    web_static = Path(args.web_static).resolve()
    install_icons = Path(args.install_icons).resolve()
    resource_icon = Path(args.resource_icon).resolve()
    source_icon = web_static / "assets/taiji/logo/logo-mark-icon.png"
    source_bytes = require_regular(source_icon, "canonical icon source")
    parse_png(source_bytes, "canonical icon source", 512)

    for size in (32, 48, 64, 128, 192, 256, 512):
        path = web_static / f"favicon-{size}.png"
        data = require_regular(path, f"Web favicon {size}")
        parse_png(data, f"Web favicon {size}", size)

    ico = web_static / "favicon.ico"
    parse_ico(require_regular(ico, "Web favicon ICO"), "Web favicon ICO")

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

    for relative in ("index.html", "manifest.json", "sw.js"):
        path = web_static / relative
        if path.exists() and "favicon.svg" in path.read_text(encoding="utf-8"):
            fail(f"legacy SVG favicon reference remains in {path}")

    return hashlib.sha256(
        b"".join(
            (web_static / f"favicon-{size}.png").read_bytes()
            for size in (32, 48, 64, 128, 192, 256, 512)
        )
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-static", required=True)
    parser.add_argument("--install-icons", required=True)
    parser.add_argument("--resource-icon", required=True)
    args = parser.parse_args()
    try:
        digest = validate(args)
    except (IconValidationError, OSError, UnicodeError) as exc:
        print(f"[FAIL] icon validation: {exc}", file=sys.stderr)
        return 1
    print(f"[OK] Product icon chain is consistent (icon_set_sha256={digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
