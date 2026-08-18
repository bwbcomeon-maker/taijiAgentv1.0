from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import shutil
import tempfile
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "hermes-local-lab/sources/hermes-webui/static"
ICON_VALIDATOR = ROOT / "packaging/linux/validate_icon_assets.py"

ICON_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "taiji_validate_icon_assets", ICON_VALIDATOR
)
if ICON_VALIDATOR_SPEC is None or ICON_VALIDATOR_SPEC.loader is None:
    raise RuntimeError(f"cannot load icon validator: {ICON_VALIDATOR}")
ICON_VALIDATOR_MODULE = importlib.util.module_from_spec(ICON_VALIDATOR_SPEC)
ICON_VALIDATOR_SPEC.loader.exec_module(ICON_VALIDATOR_MODULE)


def png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", checksum)
    )


def rgba_png(
    width: int = 1,
    height: int = 1,
    *,
    raw_scanlines: bytes | None = None,
    compressed_idat: bytes | None = None,
    chunks_after_idat: tuple[bytes, ...] = (),
    include_iend: bool = True,
    trailing: bytes = b"",
) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    if raw_scanlines is None:
        raw_scanlines = b"".join(b"\x00" + (b"\x00\x00\x00\xff" * width) for _ in range(height))
    if compressed_idat is None:
        compressed_idat = zlib.compress(raw_scanlines)
    chunks = [png_chunk(b"IHDR", ihdr), png_chunk(b"IDAT", compressed_idat)]
    chunks.extend(chunks_after_idat)
    if include_iend:
        chunks.append(png_chunk(b"IEND", b""))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks) + trailing


def single_png_ico(png: bytes, *, count: int = 1, offsets: tuple[int, ...] | None = None) -> bytes:
    directory_end = 6 + count * 16
    if offsets is None:
        offsets = tuple(directory_end for _ in range(count))
    width = struct.unpack(">I", png[16:20])[0]
    height = struct.unpack(">I", png[20:24])[0]
    entries = []
    for offset in offsets:
        entries.append(
            bytes((width % 256, height % 256, 0, 0))
            + struct.pack("<HHII", 1, 32, len(png), offset)
        )
    return struct.pack("<HHH", 0, 1, count) + b"".join(entries) + png


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def png_info(path: Path) -> tuple[int, int, int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"not a PNG: {path}")
    length = struct.unpack(">I", data[8:12])[0]
    if data[12:16] != b"IHDR" or length != 13:
        raise AssertionError(f"PNG missing IHDR: {path}")
    width, height, bit_depth, color_type, *_ = struct.unpack(">IIBBBBB", data[16:29])
    return width, height, bit_depth, color_type


class LinuxIconChainTest(unittest.TestCase):
    def test_png_parser_rejects_corrupt_chunk_crc(self):
        data = bytearray(rgba_png())
        idat_offset = data.index(b"IDAT") - 4
        idat_length = struct.unpack(">I", data[idat_offset : idat_offset + 4])[0]
        crc_offset = idat_offset + 8 + idat_length
        data[crc_offset] ^= 0x01

        with self.assertRaisesRegex(
            ICON_VALIDATOR_MODULE.IconValidationError, "CRC"
        ):
            ICON_VALIDATOR_MODULE.parse_png(bytes(data), "PNG fixture")

    def test_png_parser_rejects_truncated_compressed_idat(self):
        compressed = zlib.compress(b"\x00\x00\x00\x00\xff")
        data = rgba_png(compressed_idat=compressed[:-2])

        with self.assertRaisesRegex(
            ICON_VALIDATOR_MODULE.IconValidationError, "IDAT"
        ):
            ICON_VALIDATOR_MODULE.parse_png(data, "PNG fixture")

    def test_png_parser_rejects_fake_iend_bytes(self):
        data = rgba_png(
            chunks_after_idat=(png_chunk(b"tEXt", b"fake IEND marker"),),
            include_iend=False,
        )

        with self.assertRaisesRegex(
            ICON_VALIDATOR_MODULE.IconValidationError, "IEND"
        ):
            ICON_VALIDATOR_MODULE.parse_png(data, "PNG fixture")

    def test_png_parser_rejects_trailing_data_after_iend(self):
        with self.assertRaisesRegex(
            ICON_VALIDATOR_MODULE.IconValidationError, "trailing"
        ):
            ICON_VALIDATOR_MODULE.parse_png(
                rgba_png(trailing=b"not-part-of-the-png"), "PNG fixture"
            )

    def test_png_parser_rejects_noncontiguous_idat_chunks(self):
        compressed = zlib.compress(b"\x00\x00\x00\x00\xff")
        split_at = len(compressed) // 2
        ihdr = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        data = (
            b"\x89PNG\r\n\x1a\n"
            + ihdr
            + png_chunk(b"IDAT", compressed[:split_at])
            + png_chunk(b"tEXt", b"separator")
            + png_chunk(b"IDAT", compressed[split_at:])
            + png_chunk(b"IEND", b"")
        )

        with self.assertRaisesRegex(
            ICON_VALIDATOR_MODULE.IconValidationError, "contiguous"
        ):
            ICON_VALIDATOR_MODULE.parse_png(data, "PNG fixture")

    def test_png_parser_rejects_invalid_scanline_filter_byte(self):
        with self.assertRaisesRegex(
            ICON_VALIDATOR_MODULE.IconValidationError, "filter byte"
        ):
            ICON_VALIDATOR_MODULE.parse_png(
                rgba_png(raw_scanlines=b"\x05\x00\x00\x00\xff"),
                "PNG fixture",
            )

    def test_ico_parser_rejects_directory_entry_pointing_to_corrupt_png(self):
        png = bytearray(rgba_png(16, 16))
        png[-1] ^= 0x01

        with self.assertRaisesRegex(
            ICON_VALIDATOR_MODULE.IconValidationError, "CRC"
        ):
            ICON_VALIDATOR_MODULE.parse_ico(single_png_ico(bytes(png)), "ICO fixture")

    def test_ico_parser_rejects_overlapping_payload_ranges(self):
        png = rgba_png(16, 16)
        ico = single_png_ico(png, count=2, offsets=(38, 38))

        with self.assertRaisesRegex(
            ICON_VALIDATOR_MODULE.IconValidationError, "overlap"
        ):
            ICON_VALIDATOR_MODULE.parse_ico(ico, "ICO fixture")

    def test_generated_canonical_icon_and_all_sizes_are_rgba(self):
        canonical = WEB_STATIC / "assets/taiji/logo/logo-mark-icon.png"
        self.assertTrue(canonical.exists())
        self.assertEqual(png_info(canonical), (512, 512, 8, 6))
        for size in (32, 48, 64, 128, 192, 256, 512):
            self.assertEqual(
                png_info(WEB_STATIC / f"favicon-{size}.png"),
                (size, size, 8, 6),
            )
        self.assertEqual(
            hashlib.sha256(canonical.read_bytes()).hexdigest(),
            hashlib.sha256((WEB_STATIC / "favicon-512.png").read_bytes()).hexdigest(),
        )

    def test_ico_is_a_png_backed_windows_icon(self):
        data = (WEB_STATIC / "favicon.ico").read_bytes()
        self.assertGreaterEqual(len(data), 22)
        self.assertEqual(struct.unpack("<HHH", data[:6]), (0, 1, 1))
        self.assertIn(b"\x89PNG\r\n\x1a\n", data)
        self.assertEqual(
            hashlib.sha256(data).hexdigest(),
            ICON_VALIDATOR_MODULE.EXPECTED_TAIJI_FAVICON_ICO_SHA256,
        )

    def test_web_pwa_and_service_worker_have_no_legacy_svg_favicon_reference(self):
        index = read_text("hermes-local-lab/sources/hermes-webui/static/index.html")
        manifest = json.loads(read_text("hermes-local-lab/sources/hermes-webui/static/manifest.json"))
        sw = read_text("hermes-local-lab/sources/hermes-webui/static/sw.js")
        for source in (index, sw):
            self.assertNotIn("favicon.svg", source)
        self.assertNotIn("favicon-512.svg", index)
        self.assertTrue(re.search(r'href="static/favicon-32\.png"', index))
        self.assertTrue(re.search(r'href="static/favicon\.ico"', index))
        self.assertTrue(all(icon["src"].endswith(".png") for icon in manifest["icons"]))
        self.assertIn("./static/favicon-512.png", sw)
        self.assertIn("./static/favicon.ico", sw)

    def test_linux_desktop_identity_and_electron_class_are_stable(self):
        desktop = read_text("packaging/linux/taiji-agent.desktop")
        launcher = read_text("packaging/linux/bin/taiji-agent")
        main = read_text("apps/taiji-desktop/src/main.js")
        self.assertIn("StartupWMClass=taiji-agent", desktop)
        self.assertIn("X-GNOME-WMClass=taiji-agent", desktop)
        self.assertIn("--class=taiji-agent", launcher)
        self.assertIn('app.setName("taiji-agent")', main)
        self.assertIn('app.setDesktopName("taiji-agent.desktop")', main)
        self.assertIn("icon: iconPath || undefined", main)
        self.assertIn("icon: authIconPath || undefined", main)

    def test_deb_appstream_payload_and_native_verify_cover_icon_chain(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        contract = json.loads(read_text("packaging/linux/payload-contract.json"))
        verify = read_text("hermes-local-lab/scripts/taiji-native-verify")
        self.assertIn("validate_icon_assets.py", build)
        self.assertIn("taiji-agent.metainfo.xml", build)
        for size in (32, 48, 64, 128, 192, 256, 512):
            self.assertIn(f"hicolor/{size}x{size}/apps/taiji-agent.png", build)
        paths = {component["path"] for component in contract["components"]}
        self.assertIn("usr/share/metainfo/taiji-agent.metainfo.xml", paths)
        for size in (32, 48, 64, 128, 192, 256, 512):
            self.assertIn(f"usr/share/icons/hicolor/{size}x{size}/apps/taiji-agent.png", paths)
        self.assertIn("opt/taiji-agent/resources/icons/taiji-agent.png", paths)
        self.assertIn("Product icon chain is consistent", verify)
        self.assertIn("cmp", verify)

    def test_icon_validator_passes_source_tree(self):
        with tempfile.TemporaryDirectory(prefix="taiji-icon-validator-") as temp_dir:
            hicolor = Path(temp_dir) / "hicolor"
            for size in (32, 48, 64, 128, 192, 256, 512):
                target = hicolor / f"{size}x{size}/apps/taiji-agent.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(WEB_STATIC / f"favicon-{size}.png", target)
            result = subprocess.run(
                [
                    "python3",
                    str(ICON_VALIDATOR),
                    "--web-static",
                    str(WEB_STATIC),
                    "--install-icons",
                    str(hicolor),
                    "--resource-icon",
                    str(WEB_STATIC / "favicon-512.png"),
                    "--print-digest",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = hashlib.sha256(
            b"taiji-product-icon-set-v2\0"
            + b"".join(
                (WEB_STATIC / f"favicon-{size}.png").read_bytes()
                for size in (32, 48, 64, 128, 192, 256, 512)
            )
            + (WEB_STATIC / "favicon.ico").read_bytes()
        ).hexdigest()
        self.assertEqual(result.stdout.strip(), expected)

    def test_icon_validator_rejects_valid_but_unrelated_ico_pixels(self):
        with tempfile.TemporaryDirectory(prefix="taiji-icon-ico-mismatch-") as temp_dir:
            root = Path(temp_dir)
            web_static = root / "static"
            canonical = web_static / "assets/taiji/logo/logo-mark-icon.png"
            canonical.parent.mkdir(parents=True)
            shutil.copy2(
                WEB_STATIC / "assets/taiji/logo/logo-mark-icon.png",
                canonical,
            )
            hicolor = root / "hicolor"
            for size in (32, 48, 64, 128, 192, 256, 512):
                web_icon = web_static / f"favicon-{size}.png"
                shutil.copy2(WEB_STATIC / f"favicon-{size}.png", web_icon)
                installed = hicolor / f"{size}x{size}/apps/taiji-agent.png"
                installed.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(web_icon, installed)
            (web_static / "favicon.ico").write_bytes(
                single_png_ico(rgba_png(256, 256))
            )
            resource = root / "resource.png"
            shutil.copy2(WEB_STATIC / "favicon-512.png", resource)

            result = subprocess.run(
                [
                    "python3",
                    str(ICON_VALIDATOR),
                    "--web-static",
                    str(web_static),
                    "--install-icons",
                    str(hicolor),
                    "--resource-icon",
                    str(resource),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ICO", result.stderr)
        self.assertIn("hash-pinned Taiji product icon", result.stderr)

    def test_deb_manifest_uses_validator_digest_not_path_dependent_sha256sum_text(self):
        build = read_text("packaging/linux/deb/build-deb.sh")
        preflight = read_text("taijiagent 打包交付/01_制包机_发布预检.sh")
        manifest_body = build[
            build.index("write_package_manifest() {") : build.index("write_launch_manifest() {")
        ]
        self.assertIn('icon_set_sha256="$ICON_SET_SHA256"', manifest_body)
        self.assertIn("--print-digest", build)
        self.assertNotIn(
            'sha256sum "$SOURCE_WEB_DIR/static/favicon-$size.png"',
            manifest_body,
        )
        self.assertIn('ICON_VALIDATOR="$REPO_ROOT/packaging/linux/validate_icon_assets.py"', preflight)
        self.assertIn("--print-digest", preflight)
        self.assertIn('marker_icon_sha256', preflight)

    def test_icon_validator_rejects_hicolor_bytes_that_differ_from_web_source(self):
        with tempfile.TemporaryDirectory(prefix="taiji-icon-mismatch-") as temp_dir:
            hicolor = Path(temp_dir) / "hicolor"
            for size in (32, 48, 64, 128, 192, 256, 512):
                target = hicolor / f"{size}x{size}/apps/taiji-agent.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(WEB_STATIC / f"favicon-{size}.png", target)
            mismatched = hicolor / "32x32/apps/taiji-agent.png"
            original = mismatched.read_bytes()
            mismatched.write_bytes(
                original[:-12]
                + png_chunk(b"tEXt", b"different-but-parseable")
                + original[-12:]
            )
            result = subprocess.run(
                [
                    "python3",
                    str(ICON_VALIDATOR),
                    "--web-static",
                    str(WEB_STATIC),
                    "--install-icons",
                    str(hicolor),
                    "--resource-icon",
                    str(WEB_STATIC / "favicon-512.png"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("byte-identical", result.stderr)
