import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts/validate-taiji-release-evidence.py"
POLICY_PATH = ROOT / "packaging/linux/compatibility-policy.json"
POLICY_HELPER_PATH = ROOT / "packaging/linux/compatibility_policy.py"


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
            "electron_executable_sha256": "c" * 64,
            "desktop_entry_sha256": "d" * 64,
            "maintainer": "Taiji Agent Product Team <noreply@localhost>",
            "built_at_utc": "2026-08-05T00:00:00Z",
        }
        manifest.update(updates)
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

    def write_delivery_identity_fixture(self) -> None:
        self.source_archive = (
            self.delivery / f"taiji-agentv1.0-kylin-build-src-{self.commit}.tar.gz"
        )
        self.source_archive.write_bytes(b"source fixture\n")
        self.checksum = self.package_dir / f"{self.deb.name}.sha256"
        self.checksum.write_text(
            f"{self.sha256(self.deb)}  {self.deb.name}\n",
            encoding="ascii",
        )
        (self.delivery / "SHA256SUMS.txt").write_text(
            f"{self.sha256(self.source_archive)}  {self.source_archive.name}\n",
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
                    f"deb={self.deb.name}",
                    f"deb_sha256={self.sha256(self.deb)}",
                    f"checksum={self.checksum.name}",
                    "built_at_utc=2026-08-05T00:00:00Z",
                    f"manifest={self.manifest.name}",
                    f"compatibility_policy_id={self.policy_id}",
                    f"compatibility_policy_sha256={self.policy_sha256}",
                    f"elf_abi_audit_sha256={'e' * 64}",
                    f"icon_set_sha256={'1' * 64}",
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
            "signing_public_key_fingerprint": "2" * 64,
            "formal_gates": {
                "candidate_deb_unchanged": "PASS",
                "canonical_policy": "PASS",
                "certification_set": "PASS",
                "certification_signature": "PASS",
                "github_ci_gate": "PASS",
                "manifest_binding": "PASS",
            },
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
        source_archive.write_bytes(b"source fixture\n")
        (delivery / "SHA256SUMS.txt").write_text(
            f"{self.sha256(source_archive)}  {source_archive.name}\n",
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
            "deb_basename": deb.name,
            "deb_sha256": self.sha256(deb),
            "compatibility_policy_id": self.policy_id,
            "compatibility_policy_sha256": self.policy_sha256,
            "elf_abi_audit_sha256": "e" * 64,
            "icon_set_sha256": "1" * 64,
            "maintainer": "Taiji Agent Product Team <noreply@localhost>",
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
                    f"deb={deb.name}",
                    f"deb_sha256={self.sha256(deb)}",
                    f"checksum={deb.name}.sha256",
                    "built_at_utc=2026-08-05T00:00:00Z",
                    "manifest=taiji-package-manifest.json",
                    f"compatibility_policy_id={self.policy_id}",
                    f"compatibility_policy_sha256={self.policy_sha256}",
                    f"elf_abi_audit_sha256={'e' * 64}",
                    f"icon_set_sha256={'1' * 64}",
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


if __name__ == "__main__":
    unittest.main()
