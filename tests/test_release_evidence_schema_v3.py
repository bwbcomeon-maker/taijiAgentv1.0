import argparse
import copy
import hashlib
import io
import importlib.util
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import tarfile
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts/validate-taiji-release-evidence.py"
POLICY_PATH = ROOT / "packaging/linux/compatibility-policy.json"
POLICY_HELPER_PATH = ROOT / "packaging/linux/compatibility_policy.py"
SOURCE_INTEGRITY_HELPER = ROOT / "packaging/linux/source-archive-integrity.py"


def toolchain_identity() -> dict[str, str]:
    return {
        "python_dependency_lock_status": "strict-locked",
        "python_lock_basename": "uv.lock",
        "python_lock_sha256": "dbab12665d98aef021ba64953c61b0ed8a908cfb56a1c01e2fcb4b052b71a2a1",
        "python_version": "3.11.15",
        "python_archive_sha256": "2ed5c2b6d2a018e0345219d6391a85b1eb0d0d1752b19cde6fc210d9392a752a",
        "python_executable_sha256": "5035e46784be79111e00103f91b37bcd3b26f2b8b936f26e2bd4bb8252cd0aba",
        "uv_version": "0.12.2",
        "uv_archive_sha256": "d66e96b5f1ca3b99806eee283a8125d33a0bd669e6e6d9bc4ab7ffda63c41bf4",
        "uv_executable_sha256": "72c5f455cd0e9793910f6a1db255de37b610a36a8db858afa3c72e34668e23e2",
        "node_version": "22.23.1",
        "node_archive_sha256": "9749e988f437343b7fa832c69ded82a312e41a03116d766797ac14f6f9eee578",
        "node_executable_sha256": "93956de2e59480474a7b46571da1651180b1a050cdf32641ebec4ce6e478e068",
        "electron_version": "39.8.10",
        "electron_archive_sha256": "92e8b031fa5327c78a972279fd75fc8503fcd1773401809f4557e4de583eabd1",
        "electron_executable_sha256": "c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d",
    }


def load_validator():
    spec = importlib.util.spec_from_file_location(
        "taiji_release_evidence_validator_schema3_test", VALIDATOR_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    # Register the dynamic module before dataclass processing. Python 3.14's
    # dataclasses resolves postponed annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_policy_helper():
    spec = importlib.util.spec_from_file_location(
        "taiji_release_evidence_policy_schema3_test", POLICY_HELPER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {POLICY_HELPER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png_fixture(width: int = 800, height: int = 600) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload))
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + b"".join(
        bytes((index % 251, (index * 3) % 251, (index * 7) % 251))
        for index in range(width)
    )
    return (
        signature
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row * height, level=9))
        + chunk(b"IEND", b"")
    )


class ReleaseEvidenceSchemaV3Test(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = load_validator()
        self.policy_helper = load_policy_helper()
        self.policy = self.policy_helper.load_and_validate(POLICY_PATH)
        self.policy_id = self.policy["policy_id"]
        self.policy_sha256 = self.policy_helper.canonical_sha256(self.policy)
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-schema3-evidence-")
        self.root = Path(self.temporary.name)
        self.commit = "a" * 40
        self.delivery = self.root / "delivery"
        self.package_dir = self.delivery / "生成的安装包"
        self.package_dir.mkdir(parents=True)
        self.deb = self.package_dir / "taiji-agent_1.0.0_amd64.deb"
        self.deb.write_bytes(b"deb-v3")
        self.manifest = self.package_dir / "taiji-package-manifest.json"
        self.write_manifest()
        self.write_delivery_identity_fixture()
        self.ci_evidence = self.root / "github-ci-evidence.json"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        self.ci_evidence.write_text(
            json.dumps(
                {
                    "schema": "taiji-github-ci-evidence/v1",
                    "provider": "github-actions",
                    "repository": "example/taiji-agent",
                    "workflow_name": "Pull Request CI",
                    "required_check_name": "CI Gate",
                    "run_id": 123456789,
                    "run_attempt": 1,
                    "event": "push",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": self.commit,
                    "html_url": "https://github.com/example/taiji-agent/actions/runs/123456789",
                    "completed_at_utc": now,
                    "collected_at_utc": now,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.evidence = self.root / "release-evidence.json"
        self.write_evidence()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_manifest(self, **updates) -> None:
        manifest = {
            "schema": "taiji-package-manifest/v3",
            "package": "taiji-agent",
            "version": "1.0.0",
            "architecture": "amd64",
            "source_commit": self.commit,
            "deb_basename": self.deb.name,
            "deb_sha256": self.sha256(self.deb),
            "compatibility_policy_id": self.policy_id,
            "compatibility_policy_sha256": self.policy_sha256,
            "elf_abi_audit_basename": "elf-abi-audit.json",
            "elf_abi_audit_sha256": "e" * 64,
            "icon_set_sha256": "1" * 64,
            "electron_executable_sha256": "c63780578ca420c8651b81544e1551cef8b71a31c64712378467ed30dae06f6d",
            "desktop_entry_sha256": "d" * 64,
            "maintainer": "Taiji Agent Product Team <noreply@localhost>",
            "built_at_utc": "2026-08-05T00:00:00Z",
            **toolchain_identity(),
        }
        if hasattr(self, "source_inventory"):
            manifest.update(
                {
                    "source_archive_basename": self.source_archive.name,
                    "source_archive_sha256": self.sha256(self.source_archive),
                    "source_inventory_basename": self.source_inventory.name,
                    "source_inventory_sha256": self.sha256(self.source_inventory),
                }
            )
        manifest.update(updates)
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

    def write_delivery_identity_fixture(self) -> None:
        self.source_archive = (
            self.delivery / f"taiji-agentv1.0-kylin-build-src-{self.commit}.tar.gz"
        )
        self.write_source_archive(self.source_archive)
        self.source_inventory = self.delivery / (
            f"taiji-agentv1.0-kylin-build-src-{self.commit}.inventory.json"
        )
        self.write_source_inventory(self.source_archive, self.source_inventory)
        shutil.copy2(SOURCE_INTEGRITY_HELPER, self.delivery / SOURCE_INTEGRITY_HELPER.name)
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest.update(
            {
                "source_archive_basename": self.source_archive.name,
                "source_archive_sha256": self.sha256(self.source_archive),
                "source_inventory_basename": self.source_inventory.name,
                "source_inventory_sha256": self.sha256(self.source_inventory),
            }
        )
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        self.checksum = self.package_dir / f"{self.deb.name}.sha256"
        self.checksum.write_text(
            f"{self.sha256(self.deb)}  {self.deb.name}\n",
            encoding="ascii",
        )
        (self.delivery / "SHA256SUMS.txt").write_text(
            f"{self.sha256(self.source_archive)}  {self.source_archive.name}\n"
            f"{self.sha256(self.source_inventory)}  {self.source_inventory.name}\n",
            encoding="ascii",
        )
        self.build_marker = self.package_dir / ".build-success"
        self.build_marker.write_text(
            "\n".join(
                (
                    "version=1.0.0",
                    f"source_archive={self.source_archive.name}",
                    f"source_sha256={self.sha256(self.source_archive)}",
                    f"source_commit={self.commit}",
                    f"source_inventory={self.source_inventory.name}",
                    f"source_inventory_sha256={self.sha256(self.source_inventory)}",
                    f"deb={self.deb.name}",
                    f"deb_sha256={self.sha256(self.deb)}",
                    f"checksum={self.checksum.name}",
                    "built_at_utc=2026-08-05T00:00:00Z",
                    f"manifest={self.manifest.name}",
                    f"compatibility_policy_id={self.policy_id}",
                    f"compatibility_policy_sha256={self.policy_sha256}",
                    f"elf_abi_audit_sha256={'e' * 64}",
                    f"icon_set_sha256={'1' * 64}",
                    *(f"{key}={value}" for key, value in sorted(toolchain_identity().items())),
                    "maintainer=Taiji Agent Product Team <noreply@localhost>",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        (self.package_dir / "构建报告.txt").write_text("report\n", encoding="utf-8")
        for filename in (
            "00_制包机_生成离线交付包.sh",
            "01_制包机_发布预检.sh",
            "02_目标终端_安装并验证.sh",
            "03_目标终端_导出诊断报告.sh",
            "04_目标终端_桌面App验收并导出证据.sh",
            "99_本机_准备制包输入包.sh",
            "操作说明.md",
            "版本信息.txt",
        ):
            (self.delivery / filename).write_text(f"fixture {filename}\n", encoding="utf-8")
        tools_dir = self.delivery / "验收工具"
        tools_dir.mkdir()
        for filename in (
            "run-installed-electron-acceptance.js",
            "assemble-target-evidence.py",
            "observe-single-deb-install.py",
            "certification-matrix.json",
            "assemble-taiji-certification-set.py",
            "validate-taiji-release-evidence.py",
            "signing-public.pem",
        ):
            (tools_dir / filename).write_text(f"fixture {filename}\n", encoding="utf-8")

    @staticmethod
    def write_source_archive(path: Path, payload: bytes = b"version = 1\n") -> None:
        member = tarfile.TarInfo(
            "taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/uv.lock"
        )
        member.size = len(payload)
        member.mode = 0o644
        with tarfile.open(path, "w:gz") as archive:
            archive.addfile(member, io.BytesIO(payload))

    def write_source_inventory(self, archive: Path, inventory: Path) -> None:
        inventory.unlink(missing_ok=True)
        result = subprocess.run(
            [
                sys.executable,
                str(SOURCE_INTEGRITY_HELPER),
                "create",
                "--archive",
                str(archive),
                "--inventory",
                str(inventory),
                "--source-commit",
                self.commit,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def write_evidence(self, **updates) -> None:
        evidence = {
            "schema": "taiji-release-evidence/v3",
            "evidence_type": "single-deb-publication",
            "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "challenge_nonce": "ab" * 32,
            "source_commit": self.commit,
            "version": "1.0.0",
            "architecture": "amd64",
            "deb_basename": self.deb.name,
            "deb_sha256": self.sha256(self.deb),
            "compatibility_policy_id": self.policy_id,
            "compatibility_policy_sha256": self.policy_sha256,
            "certification_set_basename": "certification-set.json",
            "certification_set_sha256": "f" * 64,
            "certification_set_signature_basename": "certification-set.json.sig",
            "certification_set_signature_sha256": "1" * 64,
            "ci_evidence_basename": self.ci_evidence.name,
            "ci_evidence_sha256": self.sha256(self.ci_evidence),
            "maintainer": "Taiji Agent Product Team <noreply@localhost>",
            "customer_filename": self.deb.name,
            "customer_folder_contract": "exactly-one-deb",
            "signing_public_key_fingerprint": "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da",
            "formal_gates": {
                "candidate_deb_unchanged": "PASS",
                "canonical_policy": "PASS",
                "certification_set": "PASS",
                "certification_signature": "PASS",
                "github_ci_gate": "PASS",
                "manifest_binding": "PASS",
            },
            **toolchain_identity(),
        }
        evidence.update(updates)
        self.evidence.write_text(json.dumps(evidence), encoding="utf-8")

    def args(self, **updates):
        values = {
            "source_commit": self.commit,
            "deb": self.deb,
            "manifest": self.manifest,
            "checksum": self.checksum,
            "build_marker": self.build_marker,
            "source_archive": self.source_archive,
            "delivery_dir": self.delivery,
            "challenge": "ab" * 32,
        }
        values.update(updates)
        return argparse.Namespace(**values)

    def run_cli(self, *extra):
        return subprocess.run(
            [
                sys.executable,
                str(VALIDATOR_PATH),
                "release",
                "--evidence",
                str(self.evidence),
                "--source-commit",
                self.commit,
                "--deb",
                str(self.deb),
                "--manifest",
                str(self.manifest),
                "--checksum",
                str(self.checksum),
                "--build-marker",
                str(self.build_marker),
                "--source-archive",
                str(self.source_archive),
                "--delivery-dir",
                str(self.delivery),
                "--challenge",
                "ab" * 32,
                "--pre-sign",
                *extra,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_v3_build_binding_uses_compatibility_policy_identity(self):
        binding = self.validator.validate_build_binding(self.args())
        self.assertIsInstance(binding, self.validator.BuildBinding)
        self.assertEqual(binding.source_commit, self.commit)
        self.assertEqual(binding.version, "1.0.0")
        self.assertEqual(binding.architecture, "amd64")
        self.assertEqual(binding.deb_basename, self.deb.name)
        self.assertEqual(binding.deb_sha256, self.sha256(self.deb))
        self.assertEqual(binding.compatibility_policy_id, self.policy_id)
        self.assertEqual(binding.compatibility_policy_sha256, self.policy_sha256)
        self.assertEqual(binding.source_archive_basename, self.source_archive.name)
        self.assertEqual(binding.source_archive_sha256, self.sha256(self.source_archive))
        self.assertEqual(
            binding.delivery_inventory_sha256,
            self.validator.delivery_inventory_sha256(self.delivery),
        )

        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("release-evidence-pre-sign-valid", result.stdout)

    def _target_driver_v2_fixture(self):
        checks = {
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
        }
        restart_rounds = []
        for round_number in range(1, 4):
            restart_rounds.append(
                {
                    "round": round_number,
                    "ready": True,
                    "electron_pid": 4100 + round_number,
                    "agent_pid": 4200 + round_number,
                    "web_pid": 4300 + round_number,
                    "secondary_pid": 4400 + round_number,
                    "cdp_port": 15000 + round_number,
                    "webui_port": 18000 + round_number,
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
            )
        driver = {
            "schema": "taiji.desktop.acceptance-driver.v2",
            "acceptance_session_id": "b" * 32,
            "challenge_nonce": "ab" * 32,
            "electron_pid": restart_rounds[0]["electron_pid"],
            "electron_executable": self.validator.ELECTRON_PATH,
            "electron_executable_sha256": "c" * 64,
            "desktop_entry_sha256": "d" * 64,
            "app_url": "http://127.0.0.1:18001/?taiji_desktop=1",
            "webui_origin": "http://127.0.0.1:18001",
            "desktop_auth_cookie": {
                "name": "taiji_desktop_token",
                "present": True,
                "http_only": True,
                "same_site": "Strict",
                "path": "/",
                "value_format": "lowercase-hex-64",
            },
            "model": "deepseek-chat",
            "attachment_probe_sha256": "e" * 64,
            "agent_pid": restart_rounds[0]["agent_pid"],
            "web_pid": restart_rounds[0]["web_pid"],
            "screenshot_basename": self.validator.SCREENSHOT_BASENAME,
            "diagnostic_basename": self.validator.DIAGNOSTIC_BASENAME,
            "restart_rounds": restart_rounds,
            "persistent_user_data": {
                "mode": "electron-default-persistent",
                "restart_rounds": 3,
                "user_data_override": False,
                "profile_reset": False,
                "environment_reused": True,
                "continuity_observed_rounds": 3,
                "continuity_token": "f" * 64,
            },
            "core_observation": {
                "status": "verified",
                "mechanism": "journalctl-json-user-electron",
                "baseline_entry_count": 0,
                "baseline_cursor_set_token": "1" * 64,
                "rounds": [
                    {
                        "round": round_number,
                        "status": "verified",
                        "added_entry_count": 0,
                        "cursor_set_token": str(round_number + 1) * 64,
                    }
                    for round_number in range(1, 4)
                ],
            },
            "model_config_observation": {
                "observed_rounds": 3,
                "consistent": True,
                "public_projection_token": "5" * 64,
            },
            "checks": checks,
            "js_error_count": 0,
            "unexpected_http_failures": 0,
            "electron_exit_code": 0,
        }
        data = {
            "acceptance_session_id": driver["acceptance_session_id"],
            "challenge_nonce": driver["challenge_nonce"],
            "electron_executable_sha256": driver["electron_executable_sha256"],
            "desktop_entry_sha256": driver["desktop_entry_sha256"],
            "screenshot_basename": driver["screenshot_basename"],
            "diagnostic_basename": driver["diagnostic_basename"],
            **checks,
        }
        session = {
            "electron_pid": driver["electron_pid"],
            "js_error_count": 0,
            "unexpected_http_failures": 0,
            "checks": dict(checks),
        }
        return data, session, driver

    def test_release_validator_accepts_only_complete_target_driver_v2(self):
        data, session, driver = self._target_driver_v2_fixture()
        self.validator.validate_target_driver(data, session, driver)

        legacy = copy.deepcopy(driver)
        legacy["schema"] = "taiji.desktop.acceptance-driver.v1"
        with self.assertRaisesRegex(self.validator.EvidenceError, "schema"):
            self.validator.validate_target_driver(data, session, legacy)

        expected_checks = {
            "three_restart_cycles",
            "second_instance_focus",
            "model_configuration_state_consistent",
            "no_new_electron_core",
        }
        self.assertTrue(expected_checks <= self.validator.TARGET_CHECK_KEYS)
        self.assertTrue(expected_checks <= self.validator.TARGET_KEYS)

    def test_release_validator_rejects_target_driver_v2_downgrades(self):
        data, session, driver = self._target_driver_v2_fixture()
        mutations = []

        changed = copy.deepcopy(driver)
        changed["restart_rounds"][1]["ports_closed"]["webui"] = False
        mutations.append(changed)
        changed = copy.deepcopy(driver)
        changed["persistent_user_data"]["continuity_observed_rounds"] = 2
        mutations.append(changed)
        changed = copy.deepcopy(driver)
        changed["core_observation"]["status"] = "unverified"
        mutations.append(changed)
        changed = copy.deepcopy(driver)
        changed["model_config_observation"]["consistent"] = False
        mutations.append(changed)
        changed = copy.deepcopy(driver)
        changed["checks"]["second_instance_focus"] = False
        mutations.append(changed)

        for candidate in mutations:
            with self.subTest(candidate=candidate):
                with self.assertRaises(self.validator.EvidenceError):
                    self.validator.validate_target_driver(data, session, candidate)

        changed_session = copy.deepcopy(session)
        changed_session["checks"]["three_restart_cycles"] = False
        with self.assertRaises(self.validator.EvidenceError):
            self.validator.validate_target_driver(data, changed_session, driver)

        changed_data = copy.deepcopy(data)
        changed_data["no_new_electron_core"] = False
        with self.assertRaises(self.validator.EvidenceError):
            self.validator.validate_target_driver(changed_data, session, driver)

    def _positive_certification_bundle_fixture(self):
        _data, _session, driver = self._target_driver_v2_fixture()
        challenge = "ab" * 32
        commitment = "6" * 64
        fingerprint = hashlib.sha256(
            (challenge + "\0" + commitment).encode("utf-8")
        ).hexdigest()
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        checks = {key: "PASS" for key in self.validator.TARGET_CHECK_KEYS}
        checks.update({"preflight": "PASS", "install": "PASS"})
        security_facts = {
            "administrator_available": True,
            "business_data_mutation": False,
            "graphical_desktop": True,
            "network_observation": "continuous-process-sampling-no-non-loopback-up",
            "package_manager": "dpkg",
            "security_profile": "supported-default",
        }
        record = {
            "schema": "taiji-linux-environment-evidence/v2",
            "category_id": "kylin-min-ukui",
            "category_kind": "positive",
            "compatibility": "COMPATIBLE",
            "source_commit": self.commit,
            "version": "1.0.0",
            "architecture": "amd64",
            "deb_basename": self.deb.name,
            "deb_sha256": self.sha256(self.deb),
            "compatibility_policy_id": self.policy_id,
            "compatibility_policy_sha256": self.policy_sha256,
            "machine_identity_commitment_sha256": commitment,
            "os_id": "kylin",
            "os_version": "v10/2403",
            "desktop_environment": "UKUI",
            "security_facts": security_facts,
            "checks": checks,
            "attachments": [],
            "challenge_nonce": challenge,
            "acceptance_session_id": driver["acceptance_session_id"],
            "machine_fingerprint_sha256": fingerprint,
        }
        environment_observation = {
            key: record[key]
            for key in (
                "category_id",
                "category_kind",
                "compatibility",
                "source_commit",
                "version",
                "architecture",
                "deb_basename",
                "deb_sha256",
                "compatibility_policy_id",
                "compatibility_policy_sha256",
                "machine_identity_commitment_sha256",
                "os_id",
                "os_version",
                "desktop_environment",
                "security_facts",
            )
        }
        environment_observation.update(
            {
                "schema": "taiji-linux-environment-observation/v1",
                "checks": {"preflight": "PASS", "install": "PASS"},
                "attachments": [],
            }
        )
        install_observation = {
            "schema": "taiji.single-deb-install-observation/v2",
            "generated_at_utc": timestamp,
            "started_at_utc": timestamp,
            "completed_at_utc": timestamp,
            "challenge_nonce": challenge,
            "machine_identity_commitment_sha256": commitment,
            "machine_fingerprint_sha256": fingerprint,
            "boot_fingerprint_sha256": "8" * 64,
            "target_uid": 1000,
            "canonical_home_fingerprint_sha256": "a" * 64,
            "user_state_paths_fingerprint_sha256": "b" * 64,
            "source_commit": self.commit,
            "manifest_sha256": "7" * 64,
            "deb_observed_basename": self.deb.name,
            "deb_sha256": self.sha256(self.deb),
            "candidate_file_count": 1,
            "additional_install_files_observed": False,
            "package_status_before": "not-installed",
            "package_status_after": "install ok installed",
            "package_status_transitions": ["not-installed", "install ok installed"],
            "network_observation": "continuous-process-sampling-no-non-loopback-up",
            "network_sample_interval_ms": 100,
            "network_sample_count": 3,
            "user_state_before": "absent",
            "user_state_after_install_before_first_launch": "absent",
            "first_launch_eligible": True,
            "installation_method_machine_observed": False,
            "observation_process_continuous": True,
        }
        payloads = {
            "environment-observation.json": (
                json.dumps(environment_observation, sort_keys=True) + "\n"
            ).encode("utf-8"),
            "single-deb-install-observation.json": (
                json.dumps(install_observation, sort_keys=True) + "\n"
            ).encode("utf-8"),
            "single-deb-graphical-installer.png": png_fixture(),
            "desktop-app.png": png_fixture(),
            "desktop-driver-result.json": (
                json.dumps(driver, sort_keys=True) + "\n"
            ).encode("utf-8"),
            "taiji-support-bundle.json": (
                json.dumps(
                    {
                        "schema": "taiji.product.support-bundle.v1",
                        "manifest": {
                            "redacted": True,
                            "logs_included": False,
                            "paths_included": False,
                            "secrets_included": False,
                        },
                        "diagnostics": {
                            "schema": "taiji.product.diagnostics.v1",
                            "generated_at": timestamp,
                            "incident_id": "inc-0123456789ab",
                            "overall": "ready",
                            "components": [
                                {"id": "webui", "label": "桌面界面", "status": "ready"},
                                {"id": "agent", "label": "智能体服务", "status": "ready"},
                                {"id": "gateway", "label": "本地任务服务", "status": "ready"},
                                {"id": "license", "label": "授权状态", "status": "ready"},
                                {"id": "docx", "label": "文档引擎", "status": "ready"},
                                {"id": "skills", "label": "专家能力", "status": "ready"},
                                {"id": "node", "label": "运行环境", "status": "ready"},
                            ],
                        },
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
        }
        observation_hash = hashlib.sha256(
            payloads["single-deb-install-observation.json"]
        ).hexdigest()
        graphical_hash = hashlib.sha256(
            payloads["single-deb-graphical-installer.png"]
        ).hexdigest()
        attestation = {
            "schema": "taiji.single-deb-install-method-attestation.v1",
            "generated_at_utc": timestamp,
            "observation_basename": "single-deb-install-observation.json",
            "observation_sha256": observation_hash,
            "challenge_nonce": challenge,
            "machine_fingerprint_sha256": fingerprint,
            "boot_fingerprint_sha256": install_observation["boot_fingerprint_sha256"],
            "deb_sha256": self.sha256(self.deb),
            "installation_method_attested": "desktop-double-click",
            "installation_method_machine_observed": False,
            "attestation_scope": "human-observed-system-graphical-installer",
            "operator_id": "operator-001",
            "confirmation": True,
            "graphical_installer_evidence_basename": "single-deb-graphical-installer.png",
            "graphical_installer_evidence_sha256": graphical_hash,
        }
        payloads["single-deb-install-method-attestation.json"] = (
            json.dumps(attestation, sort_keys=True) + "\n"
        ).encode("utf-8")
        target = {
            "schema": "taiji-linux-target-verification/v2",
            "evidence_type": "target-desktop-environment",
            "generated_at_utc": timestamp,
            "acceptance_session_id": record["acceptance_session_id"],
            "challenge_nonce": challenge,
            "machine_identity_commitment_sha256": commitment,
            "machine_fingerprint_sha256": fingerprint,
            "release_artifacts_sha256": "9" * 64,
            "category_id": record["category_id"],
            "category_kind": "positive",
            "compatibility": "COMPATIBLE",
            "source_commit": self.commit,
            "version": "1.0.0",
            "architecture": "amd64",
            "deb_basename": self.deb.name,
            "deb_sha256": self.sha256(self.deb),
            "compatibility_policy_id": self.policy_id,
            "compatibility_policy_sha256": self.policy_sha256,
            "os_id": record["os_id"],
            "os_version": record["os_version"],
            "desktop_environment": record["desktop_environment"],
            "installation_method": "desktop-double-click",
            "installation_method_evidence": "human-attestation",
            "installation_method_machine_observed": False,
            "checks": dict(driver["checks"]),
        }
        pointers = {
            "environment_observation": "environment-observation.json",
            "install_observation": "single-deb-install-observation.json",
            "install_method_attestation": "single-deb-install-method-attestation.json",
            "graphical_installer_evidence": "single-deb-graphical-installer.png",
            "driver_result": "desktop-driver-result.json",
            "screenshot": "desktop-app.png",
            "diagnostic": "taiji-support-bundle.json",
        }
        for field, basename in pointers.items():
            target[field + "_basename"] = basename
            target[field + "_sha256"] = hashlib.sha256(payloads[basename]).hexdigest()
        payloads["target-verification.json"] = (
            json.dumps(target, sort_keys=True) + "\n"
        ).encode("utf-8")
        record["attachments"] = [
            {"basename": basename, "sha256": hashlib.sha256(payload).hexdigest()}
            for basename, payload in sorted(payloads.items())
        ]
        return record, payloads

    def test_positive_certification_bundle_is_recursively_validated_not_hash_only(self):
        record, payloads = self._positive_certification_bundle_fixture()
        self.validator.validate_positive_certification_bundle(
            record,
            payloads,
            expected_release_artifacts_sha256="9" * 64,
            expected_manifest_sha256="7" * 64,
            expected_electron_executable_sha256="c" * 64,
            expected_desktop_entry_sha256="d" * 64,
        )

        legacy_record = copy.deepcopy(record)
        legacy_record["schema"] = "taiji-linux-environment-evidence/v1"
        with self.assertRaisesRegex(self.validator.EvidenceError, "schema"):
            self.validator.validate_positive_certification_bundle(
                legacy_record,
                payloads,
                expected_release_artifacts_sha256="9" * 64,
                expected_manifest_sha256="7" * 64,
                expected_electron_executable_sha256="c" * 64,
                expected_desktop_entry_sha256="d" * 64,
            )

    def test_positive_certification_rejects_png_magic_only_and_shallow_support_bundle(self):
        def rebind(record, payloads):
            attestation = json.loads(
                payloads["single-deb-install-method-attestation.json"].decode("utf-8")
            )
            attestation["graphical_installer_evidence_sha256"] = hashlib.sha256(
                payloads["single-deb-graphical-installer.png"]
            ).hexdigest()
            payloads["single-deb-install-method-attestation.json"] = (
                json.dumps(attestation, sort_keys=True) + "\n"
            ).encode("utf-8")
            target = json.loads(payloads["target-verification.json"].decode("utf-8"))
            pointers = {
                "environment_observation": "environment-observation.json",
                "install_observation": "single-deb-install-observation.json",
                "install_method_attestation": "single-deb-install-method-attestation.json",
                "graphical_installer_evidence": "single-deb-graphical-installer.png",
                "driver_result": "desktop-driver-result.json",
                "screenshot": "desktop-app.png",
                "diagnostic": "taiji-support-bundle.json",
            }
            for field, basename in pointers.items():
                target[field + "_sha256"] = hashlib.sha256(payloads[basename]).hexdigest()
            payloads["target-verification.json"] = (
                json.dumps(target, sort_keys=True) + "\n"
            ).encode("utf-8")
            record["attachments"] = [
                {"basename": basename, "sha256": hashlib.sha256(payload).hexdigest()}
                for basename, payload in sorted(payloads.items())
            ]

        record, payloads = self._positive_certification_bundle_fixture()
        forged_pngs = dict(payloads)
        forged_pngs["single-deb-graphical-installer.png"] = b"\x89PNG\r\n\x1a\nnot-an-image"
        forged_pngs["desktop-app.png"] = b"\x89PNG\r\n\x1a\nnot-an-image"
        rebind(record, forged_pngs)
        with self.assertRaisesRegex(self.validator.EvidenceError, "PNG"):
            self.validator.validate_positive_certification_bundle(
                record,
                forged_pngs,
                expected_release_artifacts_sha256="9" * 64,
                expected_manifest_sha256="7" * 64,
                expected_electron_executable_sha256="c" * 64,
                expected_desktop_entry_sha256="d" * 64,
            )

        forged_support = dict(payloads)
        forged_support["taiji-support-bundle.json"] = (
            json.dumps(
                {
                    "schema": "taiji.product.support-bundle.v1",
                    "manifest": {"redacted": True},
                    "diagnostics": {"overall": "ready"},
                }
            )
            + "\n"
        ).encode("utf-8")
        rebind(record, forged_support)
        with self.assertRaisesRegex(self.validator.EvidenceError, "manifest|diagnostics"):
            self.validator.validate_positive_certification_bundle(
                record,
                forged_support,
                expected_release_artifacts_sha256="9" * 64,
                expected_manifest_sha256="7" * 64,
                expected_electron_executable_sha256="c" * 64,
                expected_desktop_entry_sha256="d" * 64,
            )

        forged_record = copy.deepcopy(record)
        forged_record["machine_identity_commitment_sha256"] = "4" * 64
        forged_record["machine_fingerprint_sha256"] = hashlib.sha256(
            (
                forged_record["challenge_nonce"]
                + "\0"
                + forged_record["machine_identity_commitment_sha256"]
            ).encode("utf-8")
        ).hexdigest()
        forged_payloads = {
            basename: ("evidence:" + basename).encode("utf-8")
            for basename in payloads
        }
        forged_record["attachments"] = [
            {"basename": basename, "sha256": hashlib.sha256(payload).hexdigest()}
            for basename, payload in sorted(forged_payloads.items())
        ]
        with self.assertRaises(self.validator.EvidenceError):
            self.validator.validate_positive_certification_bundle(
                forged_record,
                forged_payloads,
                expected_release_artifacts_sha256="9" * 64,
                expected_manifest_sha256="7" * 64,
                expected_electron_executable_sha256="c" * 64,
                expected_desktop_entry_sha256="d" * 64,
            )

    def test_v3_rejects_target_baseline_fields(self):
        self.write_manifest(target_baseline_profile_id="legacy-profile")
        with self.assertRaisesRegex(self.validator.EvidenceError, "target baseline"):
            self.validator.validate_build_binding(self.args())
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("target baseline", result.stderr)

    def test_v3_rejects_policy_hash_mismatch(self):
        self.write_manifest(compatibility_policy_sha256="0" * 64)
        with self.assertRaisesRegex(self.validator.EvidenceError, "canonical policy"):
            self.validator.validate_build_binding(self.args())
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical policy", result.stderr)

    def test_v3_rejects_missing_or_nonpass_ci_formal_gate(self):
        for gates in (
            {},
            {
                "candidate_deb_unchanged": "PASS",
                "canonical_policy": "PASS",
                "certification_set": "PASS",
                "certification_signature": "PASS",
                "github_ci_gate": "FAIL",
                "manifest_binding": "PASS",
            },
        ):
            with self.subTest(gates=gates):
                self.write_evidence(formal_gates=gates)
                result = self.run_cli()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("formal_gates", result.stderr)

    def test_v3_rejects_ci_file_tamper_or_wrong_head_sha(self):
        ci = json.loads(self.ci_evidence.read_text(encoding="utf-8"))
        ci["head_sha"] = "b" * 40
        self.ci_evidence.write_text(json.dumps(ci) + "\n", encoding="utf-8")
        self.write_evidence(ci_evidence_sha256=self.sha256(self.ci_evidence))

        result = self.run_cli()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CI", result.stderr)

    def test_v3_rejects_arbitrary_or_partial_formal_gate_claims(self):
        binding = self.validator.validate_build_binding(self.args())
        data = json.loads(self.evidence.read_text(encoding="utf-8"))
        data["formal_gates"] = {"x": "PASS"}

        with self.assertRaisesRegex(self.validator.EvidenceError, "formal_gates"):
            self.validator.validate_release_evidence_v3(
                data,
                self.evidence,
                self.args(),
                binding,
            )

    def test_old_v3_without_strict_toolchain_is_rejected_not_upgraded(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest.pop("uv_executable_sha256")
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        downgraded = self.manifest.read_bytes()

        with self.assertRaisesRegex(self.validator.EvidenceError, "工具链|toolchain"):
            self.validator.validate_build_binding(self.args())
        self.assertNotIn("uv_executable_sha256", json.loads(self.manifest.read_text()))
        self.assertEqual(self.manifest.read_bytes(), downgraded)

    def test_v3_build_binding_rejects_old_or_wrong_named_source_archive(self):
        wrong_archive = self.delivery / "taiji-agentv1.0-kylin-build-src-bbbbbbb.tar.gz"
        wrong_archive.write_bytes(self.source_archive.read_bytes())

        with self.assertRaisesRegex(self.validator.EvidenceError, "source|\u6e90\u7801"):
            self.validator.validate_build_binding(self.args())

    def test_v3_build_binding_rejects_root_source_checksum_drift(self):
        (self.delivery / "SHA256SUMS.txt").write_text(
            f"{'0' * 64}  {self.source_archive.name}\n",
            encoding="ascii",
        )

        with self.assertRaisesRegex(self.validator.EvidenceError, "SHA256SUMS|\u6e90\u7801"):
            self.validator.validate_build_binding(self.args())

    def test_v3_build_binding_rejects_build_marker_source_commit_drift(self):
        marker = self.build_marker.read_text(encoding="utf-8")
        self.build_marker.write_text(
            marker.replace(f"source_commit={self.commit}", f"source_commit={'b' * 40}"),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(self.validator.EvidenceError, "source_commit|\u6784\u5efa\u6210\u529f\u6807\u8bb0"):
            self.validator.validate_build_binding(self.args())

    def test_v3_build_binding_rejects_source_archive_lock_drift(self):
        old_source_sha = self.sha256(self.source_archive)
        self.write_source_archive(self.source_archive, b"version = 2\n")
        self.write_source_inventory(self.source_archive, self.source_inventory)
        new_source_sha = self.sha256(self.source_archive)
        new_inventory_sha = self.sha256(self.source_inventory)
        (self.delivery / "SHA256SUMS.txt").write_text(
            f"{new_source_sha}  {self.source_archive.name}\n"
            f"{new_inventory_sha}  {self.source_inventory.name}\n",
            encoding="ascii",
        )
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["source_archive_sha256"] = new_source_sha
        manifest["source_inventory_sha256"] = new_inventory_sha
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        marker = self.build_marker.read_text(encoding="utf-8")
        self.build_marker.write_text(
            marker.replace(
                f"source_sha256={old_source_sha}",
                f"source_sha256={new_source_sha}",
            ).replace(
                next(line for line in marker.splitlines() if line.startswith("source_inventory_sha256=")),
                f"source_inventory_sha256={new_inventory_sha}",
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(self.validator.EvidenceError, "uv.lock|lock"):
            self.validator.validate_build_binding(self.args())

    def test_delivery_copy_fallback_uses_current_canonical_policy_identity(self):
        isolated_script = self.root / "isolated" / "scripts" / VALIDATOR_PATH.name
        isolated_script.parent.mkdir(parents=True)
        shutil.copy2(VALIDATOR_PATH, isolated_script)
        spec = importlib.util.spec_from_file_location(
            "taiji_release_evidence_validator_isolated_copy_test",
            isolated_script,
        )
        if spec is None or spec.loader is None:
            self.fail(f"cannot load isolated validator copy: {isolated_script}")
        isolated = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = isolated
        spec.loader.exec_module(isolated)

        self.assertEqual(
            isolated.canonical_policy_identity(),
            (self.policy_id, self.policy_sha256),
        )

    def test_v3_delivery_inventory_accepts_single_deb_without_legacy_apt_repository(self):
        delivery = self.root / "inventory-delivery"
        package_dir = delivery / "生成的安装包"
        tools_dir = delivery / "验收工具"
        package_dir.mkdir(parents=True)
        tools_dir.mkdir()

        root_files = (
            "00_制包机_生成离线交付包.sh",
            "01_制包机_发布预检.sh",
            "02_目标终端_安装并验证.sh",
            "03_目标终端_导出诊断报告.sh",
            "04_目标终端_桌面App验收并导出证据.sh",
            "99_本机_准备制包输入包.sh",
            "SHA256SUMS.txt",
            "操作说明.md",
            "版本信息.txt",
        )
        for filename in root_files:
            (delivery / filename).write_text(f"fixture {filename}\n", encoding="utf-8")
        source_archive = delivery / f"taiji-agentv1.0-kylin-build-src-{self.commit}.tar.gz"
        self.write_source_archive(source_archive)
        source_inventory = delivery / f"taiji-agentv1.0-kylin-build-src-{self.commit}.inventory.json"
        self.write_source_inventory(source_archive, source_inventory)
        shutil.copy2(SOURCE_INTEGRITY_HELPER, delivery / SOURCE_INTEGRITY_HELPER.name)
        (delivery / "SHA256SUMS.txt").write_text(
            f"{self.sha256(source_archive)}  {source_archive.name}\n"
            f"{self.sha256(source_inventory)}  {source_inventory.name}\n",
            encoding="ascii",
        )

        deb = package_dir / "taiji-agent_1.0.0_amd64.deb"
        deb.write_bytes(b"single deb fixture\n")
        (package_dir / f"{deb.name}.sha256").write_text(
            f"{self.sha256(deb)}  {deb.name}\n",
            encoding="ascii",
        )
        manifest = {
            "schema": "taiji-package-manifest/v3",
            "package": "taiji-agent",
            "version": "1.0.0",
            "architecture": "amd64",
            "source_commit": self.commit,
            "source_archive_basename": source_archive.name,
            "source_archive_sha256": self.sha256(source_archive),
            "source_inventory_basename": source_inventory.name,
            "source_inventory_sha256": self.sha256(source_inventory),
            "deb_basename": deb.name,
            "deb_sha256": self.sha256(deb),
            "compatibility_policy_id": self.policy_id,
            "compatibility_policy_sha256": self.policy_sha256,
            "elf_abi_audit_sha256": "e" * 64,
            "icon_set_sha256": "1" * 64,
            "maintainer": "Taiji Agent Product Team <noreply@localhost>",
            **toolchain_identity(),
        }
        (package_dir / "taiji-package-manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        (package_dir / ".build-success").write_text(
            "\n".join(
                (
                    "version=1.0.0",
                    f"source_archive={source_archive.name}",
                    f"source_sha256={self.sha256(source_archive)}",
                    f"source_commit={self.commit}",
                    f"source_inventory={source_inventory.name}",
                    f"source_inventory_sha256={self.sha256(source_inventory)}",
                    f"deb={deb.name}",
                    f"deb_sha256={self.sha256(deb)}",
                    f"checksum={deb.name}.sha256",
                    "built_at_utc=2026-08-05T00:00:00Z",
                    "manifest=taiji-package-manifest.json",
                    f"compatibility_policy_id={self.policy_id}",
                    f"compatibility_policy_sha256={self.policy_sha256}",
                    f"elf_abi_audit_sha256={'e' * 64}",
                    f"icon_set_sha256={'1' * 64}",
                    *(f"{key}={value}" for key, value in sorted(toolchain_identity().items())),
                    "maintainer=Taiji Agent Product Team <noreply@localhost>",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        (package_dir / "构建报告.txt").write_text("report\n", encoding="utf-8")

        for filename in (
            "run-installed-electron-acceptance.js",
            "assemble-target-evidence.py",
            "observe-single-deb-install.py",
            "certification-matrix.json",
            "assemble-taiji-certification-set.py",
            "validate-taiji-release-evidence.py",
            "signing-public.pem",
        ):
            (tools_dir / filename).write_text(f"fixture {filename}\n", encoding="utf-8")

        digest = self.validator.delivery_inventory_sha256(delivery)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertFalse((delivery / "离线依赖").exists())

        certification = delivery / "certification"
        certification.mkdir()
        (certification / "certification-set.json").write_text("{}\n", encoding="utf-8")
        (delivery / "release-evidence.json").write_text("{}\n", encoding="utf-8")
        (delivery / "release-evidence.json.sig").write_bytes(b"signature")
        self.assertEqual(
            self.validator.delivery_inventory_sha256(delivery),
            digest,
            "post-build certification and signed publication evidence must not drift the build inventory",
        )
        (delivery / "release-evidence.json.bak").write_text("{}\n", encoding="utf-8")
        self.assertNotEqual(
            self.validator.delivery_inventory_sha256(delivery),
            digest,
            "lookalike publication files must remain part of the build inventory",
        )


if __name__ == "__main__":
    unittest.main()
