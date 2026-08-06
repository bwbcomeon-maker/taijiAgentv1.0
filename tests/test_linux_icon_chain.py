from __future__ import annotations

import hashlib
import json
import re
import struct
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_STATIC = ROOT / "hermes-local-lab/sources/hermes-webui/static"
ICON_VALIDATOR = ROOT / "packaging/linux/validate_icon_assets.py"


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
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
