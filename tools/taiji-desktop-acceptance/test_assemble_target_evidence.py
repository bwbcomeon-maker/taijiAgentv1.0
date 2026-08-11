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
INSTALL_OBSERVATION_BASENAME = "single-deb-install-observation.json"
INSTALL_METHOD_ATTESTATION_BASENAME = "single-deb-install-method-attestation.json"
GRAPHICAL_INSTALLER_EVIDENCE_BASENAME = "single-deb-graphical-installer.png"
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
    "desktop_auth_cookie",
    "model",
    "attachment_probe_sha256",
    "agent_pid",
    "web_pid",
    "screenshot_basename",
    "diagnostic_basename",
    "restart_rounds",
    "persistent_user_data",
    "core_observation",
    "model_config_observation",
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
        self.previous_non_linux_test_identity = os.environ.get(
            "TAIJI_ASSEMBLER_NON_LINUX_TEST_IDENTITY"
        )
        os.environ["TAIJI_ASSEMBLER_NON_LINUX_TEST_IDENTITY"] = "assembler-contract-test-v1"
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-target-assembler-test-")
        self.root = Path(self.temporary.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir(mode=0o700)
        self.output = self.root / "target-verification"
        self.challenge = "2" * 64
        self.session_id = "1" * 32
        self.source_commit = "a" * 40
        self.version = "0.1.0-preview"
        self.release_hash = "9" * 64
        assembler = load_module(ASSEMBLER, "taiji_target_assembler_fixture_identity")
        self.machine_hash, self.boot_hash = assembler.current_target_fingerprints(self.challenge)
        (
            self.target_uid,
            self.canonical_home_fingerprint,
            self.user_state_paths_fingerprint,
        ) = assembler.current_user_context_fingerprints(self.challenge)
        self.profile_id = "kylin-v10-amd64-123456789abc"
        self.profile_sha256 = "b" * 64

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
                    "schema_version": 2,
                    "package": "taiji-agent",
                    "build_arch": "x86_64",
                    "dpkg_arch": "amd64",
                    "source_commit": self.source_commit,
                    "version": self.version,
                    "deb": self.deb.name,
                    "deb_sha256": sha256(self.deb),
                    "electron_executable_sha256": sha256(self.electron),
                    "desktop_entry_sha256": sha256(self.desktop_entry),
                    "target_baseline_profile_id": self.profile_id,
                    "target_baseline_sha256": self.profile_sha256,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.install_observation = self.inputs / INSTALL_OBSERVATION_BASENAME
        self.install_observation.write_text(
            json.dumps(
                {
                    "schema": "taiji.single-deb-install-observation.v1",
                    "generated_at_utc": utc_now(),
                    "started_at_utc": utc_now(),
                    "completed_at_utc": utc_now(),
                    "challenge_nonce": self.challenge,
                    "machine_fingerprint_sha256": self.machine_hash,
                    "boot_fingerprint_sha256": self.boot_hash,
                    "target_uid": self.target_uid,
                    "canonical_home_fingerprint_sha256": self.canonical_home_fingerprint,
                    "user_state_paths_fingerprint_sha256": self.user_state_paths_fingerprint,
                    "source_commit": self.source_commit,
                    "manifest_sha256": sha256(self.manifest),
                    "deb_observed_basename": self.deb.name,
                    "deb_sha256": sha256(self.deb),
                    "target_baseline_profile_id": self.profile_id,
                    "target_baseline_sha256": self.profile_sha256,
                    "candidate_file_count": 1,
                    "additional_install_files_observed": False,
                    "package_status_before": "not-installed",
                    "package_status_after": "install ok installed",
                    "package_status_transitions": [
                        "not-installed",
                        "install ok unpacked",
                        "install ok installed",
                    ],
                    "network_observation": "continuous-process-sampling-no-non-loopback-up",
                    "network_sample_interval_ms": 250,
                    "network_sample_count": 23,
                    "user_state_before": "absent",
                    "user_state_after_install_before_first_launch": "absent",
                    "first_launch_eligible": True,
                    "installation_method_machine_observed": False,
                    "observation_process_continuous": True,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.graphical_installer_evidence = self.inputs / GRAPHICAL_INSTALLER_EVIDENCE_BASENAME
        self.graphical_installer_evidence.write_bytes(png_fixture())
        self.install_method_attestation = self.inputs / INSTALL_METHOD_ATTESTATION_BASENAME
        self.install_method_attestation.write_text(
            json.dumps(
                {
                    "schema": "taiji.single-deb-install-method-attestation.v1",
                    "generated_at_utc": utc_now(),
                    "observation_basename": self.install_observation.name,
                    "observation_sha256": sha256(self.install_observation),
                    "challenge_nonce": self.challenge,
                    "machine_fingerprint_sha256": self.machine_hash,
                    "boot_fingerprint_sha256": self.boot_hash,
                    "deb_sha256": sha256(self.deb),
                    "installation_method_attested": "desktop-double-click",
                    "installation_method_machine_observed": False,
                    "attestation_scope": "human-observed-system-graphical-installer",
                    "operator_id": "target-operator-01",
                    "confirmation": True,
                    "graphical_installer_evidence_basename": self.graphical_installer_evidence.name,
                    "graphical_installer_evidence_sha256": sha256(self.graphical_installer_evidence),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.driver_result = self.inputs / "driver-result.json"
        self.write_driver_result()

    def tearDown(self) -> None:
        self.temporary.cleanup()
        if self.previous_non_linux_test_identity is None:
            os.environ.pop("TAIJI_ASSEMBLER_NON_LINUX_TEST_IDENTITY", None)
        else:
            os.environ["TAIJI_ASSEMBLER_NON_LINUX_TEST_IDENTITY"] = (
                self.previous_non_linux_test_identity
            )

    def driver_payload(self) -> dict[str, object]:
        restart_rounds = [
            {
                "round": round_number,
                "ready": True,
                "electron_pid": 4242 + ((round_number - 1) * 10),
                "agent_pid": 4243 + ((round_number - 1) * 10),
                "web_pid": 4244 + ((round_number - 1) * 10),
                "secondary_pid": 4245 + ((round_number - 1) * 10),
                "cdp_port": 19222 + round_number - 1,
                "webui_port": 18787 + round_number - 1,
                "second_instance_exit_code": 0,
                "electron_exit_code": 0,
                "restored_and_focused": True,
                "page_close_sent": True,
                "process_identities_gone": {
                    "electron": True,
                    "agent": True,
                    "webui": True,
                    "secondary": True,
                },
                "ports_closed": {"cdp": True, "webui": True},
                "pidfiles_absent": True,
                "model_config_observed": True,
                "profile_continuity_observed": True,
            }
            for round_number in range(1, 4)
        ]
        payload = {
            "schema": "taiji.desktop.acceptance-driver.v2",
            "acceptance_session_id": self.session_id,
            "challenge_nonce": self.challenge,
            "electron_pid": 4242,
            "electron_executable": str(ELECTRON_PATH),
            "electron_executable_sha256": sha256(self.electron),
            "desktop_entry_sha256": sha256(self.desktop_entry),
            "app_url": "http://127.0.0.1:18787/?taiji_desktop=1",
            "webui_origin": "http://127.0.0.1:18787",
            "desktop_auth_cookie": {
                "name": "taiji_desktop_token",
                "present": True,
                "http_only": True,
                "same_site": "Strict",
                "path": "/",
                "value_format": "lowercase-hex-64",
            },
            "model": "openai/gpt-test",
            "attachment_probe_sha256": "7" * 64,
            "agent_pid": 4243,
            "web_pid": 4244,
            "screenshot_basename": self.screenshot.name,
            "diagnostic_basename": self.diagnostic.name,
            "restart_rounds": restart_rounds,
            "persistent_user_data": {
                "mode": "electron-default-persistent",
                "restart_rounds": 3,
                "user_data_override": False,
                "profile_reset": False,
                "environment_reused": True,
                "continuity_observed_rounds": 3,
                "continuity_token": "8" * 64,
            },
            "core_observation": {
                "status": "verified",
                "mechanism": "journalctl-json-user-electron",
                "baseline_entry_count": 0,
                "baseline_cursor_set_token": "9" * 64,
                "rounds": [
                    {
                        "round": round_number,
                        "status": "verified",
                        "added_entry_count": 0,
                        "cursor_set_token": format(round_number, "x") * 64,
                    }
                    for round_number in range(1, 4)
                ],
            },
            "model_config_observation": {
                "observed_rounds": 3,
                "consistent": True,
                "public_projection_token": "a" * 64,
            },
            "checks": {
                "visible_first_configuration_completion": True,
                "desktop_launch": True,
                "real_model_conversation": True,
                "attachment_flow": True,
                "window_close_exit": True,
                "diagnostic_export": True,
                "three_restart_cycles": True,
                "second_instance_focus": True,
                "model_configuration_state_consistent": True,
                "no_new_electron_core": True,
            },
            "js_error_count": 0,
            "unexpected_http_failures": 0,
            "electron_exit_code": 0,
        }
        self.assertEqual(set(payload), DRIVER_KEYS)
        return payload

    def test_release_manifest_requires_schema2_full_commit_and_target_baseline(self) -> None:
        assembler = load_module(ASSEMBLER, "taiji_target_assembler_manifest_identity_test")
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        kwargs = {
            "deb": self.deb,
            "deb_sha256": sha256(self.deb),
            "electron_sha256": sha256(self.electron),
            "desktop_entry_sha256": sha256(self.desktop_entry),
            "installed_version": self.version,
        }

        self.assertEqual(
            assembler.validate_manifest(manifest, **kwargs),
            (self.source_commit, self.version, self.profile_id, self.profile_sha256),
        )
        schema1 = {**manifest, "schema_version": 1}
        with self.assertRaisesRegex(assembler.AssemblyError, "source_commit"):
            assembler.validate_manifest({**manifest, "source_commit": "a" * 12}, **kwargs)
        with self.assertRaisesRegex(assembler.AssemblyError, "schema_version=2"):
            assembler.validate_manifest(schema1, **kwargs)
        with self.assertRaisesRegex(assembler.AssemblyError, "target_baseline"):
            assembler.validate_manifest(
                {key: value for key, value in manifest.items() if key != "target_baseline_sha256"},
                **kwargs,
            )

    def test_canonical_manifest_binds_v3_policy_without_target_baseline(self) -> None:
        assembler = load_module(ASSEMBLER, "taiji_target_assembler_canonical_manifest_test")
        manifest = {
            "schema": "taiji-package-manifest/v3",
            "package": "taiji-agent",
            "architecture": "amd64",
            "source_commit": self.source_commit,
            "version": self.version,
            "deb_basename": self.deb.name,
            "deb_sha256": sha256(self.deb),
            "electron_executable_sha256": sha256(self.electron),
            "desktop_entry_sha256": sha256(self.desktop_entry),
            "compatibility_policy_id": "taiji-linux-amd64-deb-v1",
            "compatibility_policy_sha256": "c" * 64,
        }
        source_commit, version, policy_id, policy_sha = assembler.validate_canonical_manifest(
            manifest,
            deb=self.deb,
            deb_sha256=sha256(self.deb),
            electron_sha256=sha256(self.electron),
            desktop_entry_sha256=sha256(self.desktop_entry),
            installed_version=self.version,
        )
        self.assertEqual((source_commit, version, policy_id, policy_sha), (
            self.source_commit,
            self.version,
            "taiji-linux-amd64-deb-v1",
            "c" * 64,
        ))

    def test_assembler_derives_current_machine_and_boot_fingerprints(self) -> None:
        assembler = load_module(ASSEMBLER, "taiji_target_assembler_current_identity_test")
        machine, boot = assembler.current_target_fingerprints(self.challenge)
        self.assertRegex(machine, r"^[0-9a-f]{64}$")
        self.assertRegex(boot, r"^[0-9a-f]{64}$")
        self.assertNotEqual(machine, boot)

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
            "install_observation": self.install_observation,
            "install_method_attestation": self.install_method_attestation,
            "graphical_installer_evidence": self.graphical_installer_evidence,
            "release_artifacts_sha256": self.release_hash,
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
                INSTALL_OBSERVATION_BASENAME,
                INSTALL_METHOD_ATTESTATION_BASENAME,
                GRAPHICAL_INSTALLER_EVIDENCE_BASENAME,
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
        self.assertEqual(evidence["schema_version"], 2)
        self.assertEqual(evidence["target_baseline_profile_id"], self.profile_id)
        self.assertEqual(evidence["target_baseline_sha256"], self.profile_sha256)
        self.assertEqual(evidence["installation_method"], "desktop-double-click")
        self.assertEqual(
            evidence["installation_network"],
            "continuous-process-sampling-no-non-loopback-up",
        )
        self.assertEqual(evidence["installation_file_count"], 1)
        self.assertFalse(evidence["additional_install_files"])
        self.assertEqual(evidence["dpkg_status_before"], "not-installed")
        self.assertEqual(evidence["dpkg_status_after"], "install ok installed")
        self.assertTrue(evidence["first_configuration_cycle_completed"])
        self.assertEqual(evidence["install_observation_sha256"], sha256(self.install_observation))
        self.assertEqual(
            evidence["install_method_attestation_sha256"],
            sha256(self.install_method_attestation),
        )
        self.assertEqual(
            evidence["graphical_installer_evidence_sha256"],
            sha256(self.graphical_installer_evidence),
        )
        args = SimpleNamespace(
            source_commit=self.source_commit,
            challenge=self.challenge,
            deb=self.deb,
            manifest=self.manifest,
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
            self.profile_id,
            self.profile_sha256,
        )

        session = json.loads(
            (self.output / "desktop-acceptance-session.json").read_text(encoding="utf-8")
        )
        self.assertEqual(session["transport"], "electron-cdp")
        self.assertTrue(session["desktop_token_present"])
        self.assertFalse(session["web_fallback_used"])
        self.assertEqual(session["target_baseline_profile_id"], self.profile_id)
        self.assertEqual(session["target_baseline_sha256"], self.profile_sha256)
        self.assertEqual(session["installation_method"], "desktop-double-click")
        self.assertEqual(
            session["installation_network"],
            "continuous-process-sampling-no-non-loopback-up",
        )
        self.assertEqual(session["installation_file_count"], 1)
        self.assertFalse(session["additional_install_files"])
        self.assertEqual(session["dpkg_status_before"], "not-installed")
        self.assertEqual(session["dpkg_status_after"], "install ok installed")
        self.assertTrue(session["first_configuration_cycle_completed"])
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
            manifest=self.manifest,
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
                        self.profile_id,
                        self.profile_sha256,
                    )

        driver_path.write_text(json.dumps(original_driver, sort_keys=True), encoding="utf-8")

    def test_install_observation_and_human_attestation_are_hash_bound(self) -> None:
        result = self.run_assembler()
        self.assertEqual(result.returncode, 0, result.stderr)
        validator = load_module(VALIDATOR, "taiji_release_evidence_validator_install_observation_test")
        evidence_path = self.output / "target-verification.json"
        evidence = validator.load_json(evidence_path, "target evidence")
        args = SimpleNamespace(
            source_commit=self.source_commit,
            challenge=self.challenge,
            deb=self.deb,
            manifest=self.manifest,
        )

        observation_path = self.output / INSTALL_OBSERVATION_BASENAME
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        observation["challenge_nonce"] = "f" * 64
        observation_path.write_text(json.dumps(observation, sort_keys=True), encoding="utf-8")
        tampered = dict(evidence)
        tampered["install_observation_sha256"] = sha256(observation_path)
        with self.assertRaises(validator.EvidenceError):
            validator.validate_target(
                tampered, evidence_path, args, sha256(self.deb), self.version,
                self.release_hash, sha256(self.electron), sha256(self.desktop_entry),
                self.profile_id, self.profile_sha256,
            )

    def test_assembler_rejects_internally_rebound_foreign_machine_or_boot_observation(self) -> None:
        original_observation = json.loads(self.install_observation.read_text(encoding="utf-8"))
        original_attestation = json.loads(
            self.install_method_attestation.read_text(encoding="utf-8")
        )
        cases = {
            "machine_fingerprint_sha256": "current target",
            "boot_fingerprint_sha256": "boot identity",
        }
        for field, expected_error in cases.items():
            with self.subTest(field=field):
                observation = dict(original_observation)
                observation[field] = "0" * 64
                self.install_observation.write_text(
                    json.dumps(observation, sort_keys=True), encoding="utf-8"
                )
                attestation = dict(original_attestation)
                attestation[field] = "0" * 64
                attestation["observation_sha256"] = sha256(self.install_observation)
                self.install_method_attestation.write_text(
                    json.dumps(attestation, sort_keys=True), encoding="utf-8"
                )

                result = self.run_assembler()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)
                self.assert_no_partial_output()

        self.install_observation.write_text(
            json.dumps(original_observation, sort_keys=True), encoding="utf-8"
        )
        self.install_method_attestation.write_text(
            json.dumps(original_attestation, sort_keys=True), encoding="utf-8"
        )

    def test_unknown_or_failed_driver_fields_are_rejected_without_publication(self) -> None:
        cases = {
            "unknown field": lambda payload: payload.update({"unexpected": True}),
            "failed check": lambda payload: payload["checks"].update({"attachment_flow": False}),
            "token URL": lambda payload: payload.update(
                {
                    "app_url": (
                        "http://127.0.0.1:18787/?taiji_desktop=1&"
                        f"taiji_desktop_token={'a' * 64}"
                    )
                }
            ),
            "unsafe cookie": lambda payload: payload["desktop_auth_cookie"].update(
                {"http_only": False}
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
