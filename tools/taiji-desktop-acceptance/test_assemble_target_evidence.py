#!/usr/bin/env python3
"""Tests for the target desktop evidence assembler."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


TOOLS_DIR = Path(__file__).resolve().parent
ASSEMBLER = TOOLS_DIR / "assemble-target-evidence.py"
REPO_ROOT = TOOLS_DIR.parents[1]
VALIDATOR = REPO_ROOT / "scripts" / "validate-taiji-release-evidence.py"
ELECTRON_PATH = Path(
    "/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron"
)
PRESERVED_DRIVER_BASENAME = "desktop-driver-result.json"
DRIVER_KEYS = {
    "schema",
    "acceptance_session_id",
    "challenge_nonce",
    "electron_pid",
    "electron_executable",
    "electron_executable_sha256",
    "desktop_entry_sha256",
    "app_url",
    "webui_origin",
    "model",
    "attachment_probe_sha256",
    "agent_pid",
    "web_pid",
    "screenshot_basename",
    "diagnostic_basename",
    "checks",
    "js_error_count",
    "unexpected_http_failures",
    "electron_exit_code",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def png_fixture() -> bytes:
    width, height = 800, 600
    rows = []
    for row in range(height):
        pixels = bytearray()
        for column in range(width):
            pixels.extend(((column + row) % 256, (column * 3) % 256, (row * 5) % 256))
        rows.append(b"\x00" + bytes(pixels))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(
        b"IDAT", zlib.compress(b"".join(rows))
    ) + chunk(b"IEND", b"")


def support_bundle() -> dict[str, object]:
    labels = {
        "webui": "桌面界面",
        "agent": "智能体服务",
        "gateway": "本地任务服务",
        "license": "授权状态",
        "docx": "文档引擎",
        "skills": "专家能力",
        "node": "运行环境",
    }
    return {
        "schema": "taiji.product.support-bundle.v1",
        "manifest": {
            "redacted": True,
            "logs_included": False,
            "paths_included": False,
            "secrets_included": False,
        },
        "diagnostics": {
            "schema": "taiji.product.diagnostics.v1",
            "generated_at": utc_now(),
            "incident_id": "inc-123456789abc",
            "overall": "ready",
            "components": [
                {"id": component_id, "label": label, "status": "ready", "version": "1.0.0"}
                for component_id, label in labels.items()
            ],
        },
    }


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TargetEvidenceAssemblerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-target-assembler-test-")
        self.root = Path(self.temporary.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir(mode=0o700)
        self.output = self.root / "target-verification"
        self.challenge = "2" * 64
        self.session_id = "1" * 32
        self.source_commit = "a" * 12
        self.version = "0.1.0-preview"
        self.release_hash = "9" * 64
        self.machine_hash = "8" * 64

        self.deb = self.inputs / f"taiji-agent_{self.version}_amd64.deb"
        self.deb.write_bytes((b"taiji-deb-payload" * 131072) + b"end")
        self.electron = self.inputs / "electron"
        self.electron.write_bytes((b"ELF-taiji-electron" * 131072) + b"end")
        self.desktop_entry = self.inputs / "taiji-agent.desktop"
        self.desktop_entry.write_text(
            "[Desktop Entry]\nName=太极 Agent\nExec=/opt/taiji-agent/bin/taiji-desktop\n",
            encoding="utf-8",
        )
        self.screenshot = self.inputs / "desktop-app.png"
        self.screenshot.write_bytes(png_fixture())
        self.diagnostic = self.inputs / "taiji-support-bundle.json"
        self.diagnostic.write_text(
            json.dumps(support_bundle(), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        self.manifest = self.inputs / "taiji-package-manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "package": "taiji-agent",
                    "build_arch": "x86_64",
                    "dpkg_arch": "amd64",
                    "source_commit": self.source_commit,
                    "version": self.version,
                    "deb": self.deb.name,
                    "deb_sha256": sha256(self.deb),
                    "electron_executable_sha256": sha256(self.electron),
                    "desktop_entry_sha256": sha256(self.desktop_entry),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.driver_result = self.inputs / "driver-result.json"
        self.write_driver_result()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def driver_payload(self) -> dict[str, object]:
        payload = {
            "schema": "taiji.desktop.acceptance-driver.v1",
            "acceptance_session_id": self.session_id,
            "challenge_nonce": self.challenge,
            "electron_pid": 4242,
            "electron_executable": str(ELECTRON_PATH),
            "electron_executable_sha256": sha256(self.electron),
            "desktop_entry_sha256": sha256(self.desktop_entry),
            "app_url": (
                "http://127.0.0.1:18787/?taiji_desktop=1&"
                "taiji_desktop_token=%3Credacted%3E"
            ),
            "webui_origin": "http://127.0.0.1:18787",
            "model": "openai/gpt-test",
            "attachment_probe_sha256": "7" * 64,
            "agent_pid": 4243,
            "web_pid": 4244,
            "screenshot_basename": self.screenshot.name,
            "diagnostic_basename": self.diagnostic.name,
            "checks": {
                "desktop_launch": True,
                "real_model_conversation": True,
                "attachment_flow": True,
                "window_close_exit": True,
                "diagnostic_export": True,
            },
            "js_error_count": 0,
            "unexpected_http_failures": 0,
            "electron_exit_code": 0,
        }
        self.assertEqual(set(payload), DRIVER_KEYS)
        return payload

    def write_driver_result(self, transform=None) -> None:
        payload = self.driver_payload()
        if transform is not None:
            transform(payload)
        self.driver_result.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    def command(self, **overrides: object) -> list[str]:
        values: dict[str, object] = {
            "driver_result": self.driver_result,
            "screenshot": self.screenshot,
            "diagnostic": self.diagnostic,
            "manifest": self.manifest,
            "deb": self.deb,
            "electron_executable": self.electron,
            "desktop_entry": self.desktop_entry,
            "release_artifacts_sha256": self.release_hash,
            "machine_fingerprint_sha256": self.machine_hash,
            "installed_package_version": self.version,
            "challenge": self.challenge,
            "os_id": "kylin",
            "os_version": "V10 SP1",
            "desktop_environment": "UKUI",
            "output_dir": self.output,
        }
        values.update(overrides)
        command = [sys.executable, str(ASSEMBLER)]
        for key, value in values.items():
            command.extend((f"--{key.replace('_', '-')}", str(value)))
        return command

    def run_assembler(self, **overrides: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.command(**overrides),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def assert_no_partial_output(self) -> None:
        self.assertFalse(os.path.lexists(self.output))
        self.assertEqual(list(self.root.glob(".target-verification.tmp-*")), [])

    def test_publishes_validator_accepted_target_evidence(self) -> None:
        result = self.run_assembler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("target-evidence-assembled", result.stdout)
        self.assertTrue(self.output.is_dir())
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertEqual(
            {path.name for path in self.output.iterdir()},
            {
                "desktop-acceptance-session.json",
                "desktop-app.png",
                PRESERVED_DRIVER_BASENAME,
                "taiji-support-bundle.json",
                "target-verification.json",
            },
        )
        for path in self.output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_nlink, 1)

        validator = load_module(VALIDATOR, "taiji_release_evidence_validator_for_assembler_test")
        evidence_path = self.output / "target-verification.json"
        evidence = validator.load_json(evidence_path, "target evidence")
        preserved_driver = self.output / PRESERVED_DRIVER_BASENAME
        self.assertEqual(preserved_driver.read_bytes(), self.driver_result.read_bytes())
        self.assertEqual(evidence["driver_result_basename"], PRESERVED_DRIVER_BASENAME)
        self.assertEqual(evidence["driver_result_sha256"], sha256(preserved_driver))
        args = SimpleNamespace(
            source_commit=self.source_commit,
            challenge=self.challenge,
            deb=self.deb,
        )
        validator.validate_target(
            evidence,
            evidence_path,
            args,
            sha256(self.deb),
            self.version,
            self.release_hash,
            sha256(self.electron),
            sha256(self.desktop_entry),
        )

        session = json.loads(
            (self.output / "desktop-acceptance-session.json").read_text(encoding="utf-8")
        )
        self.assertEqual(session["transport"], "electron-cdp")
        self.assertTrue(session["desktop_token_present"])
        self.assertFalse(session["web_fallback_used"])
        rendered = json.dumps(session, ensure_ascii=False)
        self.assertNotIn("openai/gpt-test", rendered)
        self.assertNotIn("taiji_desktop_token", rendered)

    def test_validator_rejects_preserved_driver_schema_or_binding_tampering(self) -> None:
        result = self.run_assembler()
        self.assertEqual(result.returncode, 0, result.stderr)

        validator = load_module(VALIDATOR, "taiji_release_evidence_validator_for_driver_tamper_test")
        evidence_path = self.output / "target-verification.json"
        original_evidence = validator.load_json(evidence_path, "target evidence")
        driver_path = self.output / PRESERVED_DRIVER_BASENAME
        original_driver = json.loads(driver_path.read_text(encoding="utf-8"))
        args = SimpleNamespace(
            source_commit=self.source_commit,
            challenge=self.challenge,
            deb=self.deb,
        )

        cases = {
            "unknown driver field": lambda payload: payload.update({"unexpected": True}),
            "driver session mismatch": lambda payload: payload.update(
                {"acceptance_session_id": "f" * 32}
            ),
            "driver challenge mismatch": lambda payload: payload.update(
                {"challenge_nonce": "e" * 64}
            ),
            "driver electron pid mismatch": lambda payload: payload.update({"electron_pid": 5252}),
            "driver electron hash mismatch": lambda payload: payload.update(
                {"electron_executable_sha256": "d" * 64}
            ),
            "driver desktop entry mismatch": lambda payload: payload.update(
                {"desktop_entry_sha256": "c" * 64}
            ),
            "driver check mismatch": lambda payload: payload["checks"].update(
                {"attachment_flow": False}
            ),
            "driver screenshot mismatch": lambda payload: payload.update(
                {"screenshot_basename": "different.png"}
            ),
        }
        for label, transform in cases.items():
            with self.subTest(label=label):
                payload = json.loads(json.dumps(original_driver))
                transform(payload)
                driver_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
                evidence = dict(original_evidence)
                evidence["driver_result_sha256"] = sha256(driver_path)
                with self.assertRaises(validator.EvidenceError):
                    validator.validate_target(
                        evidence,
                        evidence_path,
                        args,
                        sha256(self.deb),
                        self.version,
                        self.release_hash,
                        sha256(self.electron),
                        sha256(self.desktop_entry),
                    )

        driver_path.write_text(json.dumps(original_driver, sort_keys=True), encoding="utf-8")

    def test_unknown_or_failed_driver_fields_are_rejected_without_publication(self) -> None:
        cases = {
            "unknown field": lambda payload: payload.update({"unexpected": True}),
            "failed check": lambda payload: payload["checks"].update({"attachment_flow": False}),
            "secret URL": lambda payload: payload.update(
                {
                    "app_url": (
                        "http://127.0.0.1:18787/?taiji_desktop=1&"
                        "taiji_desktop_token=secret"
                    )
                }
            ),
            "wrong executable": lambda payload: payload.update(
                {"electron_executable": "/tmp/electron"}
            ),
        }
        for label, transform in cases.items():
            with self.subTest(label=label):
                self.write_driver_result(transform)
                result = self.run_assembler()
                self.assertNotEqual(result.returncode, 0)
                self.assert_no_partial_output()
        self.write_driver_result()

    def test_extra_app_query_data_is_rejected_without_publication(self) -> None:
        self.write_driver_result(
            lambda payload: payload.update(
                {"app_url": f"{payload['app_url']}&debug_secret=must-not-be-accepted"}
            )
        )
        result = self.run_assembler()
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_partial_output()

    def test_artifact_and_identity_mismatches_fail_closed(self) -> None:
        cases = {
            "challenge": {"challenge": "3" * 64},
            "installed version": {"installed_package_version": "0.1.1"},
            "release hash": {"release_artifacts_sha256": "not-a-hash"},
            "unsupported os": {"os_id": "debian"},
        }
        for label, overrides in cases.items():
            with self.subTest(label=label):
                result = self.run_assembler(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assert_no_partial_output()

        self.electron.write_bytes(b"changed electron")
        result = self.run_assembler()
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_partial_output()

    def test_symlink_and_hardlink_inputs_are_rejected(self) -> None:
        symlink = self.root / "driver-link.json"
        symlink.symlink_to(self.driver_result)
        result = self.run_assembler(driver_result=symlink)
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_partial_output()

        hardlink = self.root / "diagnostic-hardlink.json"
        os.link(self.diagnostic, hardlink)
        result = self.run_assembler(diagnostic=hardlink)
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_partial_output()

    def test_existing_output_is_not_overwritten(self) -> None:
        self.output.mkdir(mode=0o700)
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        result = self.run_assembler()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertEqual(list(self.root.glob(".target-verification.tmp-*")), [])

    def test_hash_helper_streams_regular_files_and_rejects_hardlinks(self) -> None:
        assembler = load_module(ASSEMBLER, "taiji_target_evidence_assembler_for_hash_test")
        large = self.inputs / "large.bin"
        large.write_bytes((b"0123456789abcdef" * 200000) + b"tail")
        expected = hashlib.sha256(large.read_bytes()).hexdigest()
        self.assertEqual(assembler.sha256_regular_file(large, "large test file"), expected)

        hardlink = self.root / "large-hardlink.bin"
        os.link(large, hardlink)
        with self.assertRaises(assembler.AssemblyError):
            assembler.sha256_regular_file(hardlink, "hardlinked test file")


if __name__ == "__main__":
    unittest.main()
