import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts/validate-taiji-release-evidence.py"


def load_validator():
    spec = importlib.util.spec_from_file_location(
        "taiji_release_evidence_validator_schema2_test", VALIDATOR_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    # Register the dynamic module before dataclass processing. Python 3.14's
    # dataclasses resolves postponed annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReleaseEvidenceSchemaV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = load_validator()
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-schema2-evidence-")
        self.root = Path(self.temporary.name)
        self.commit = "a" * 40
        self.profile_id = "kylin-v10-amd64-123456789abc"
        self.profile_sha256 = "b" * 64
        self.deb = self.root / "taiji-agent_1.0.0_amd64.deb"
        self.deb.write_bytes(b"deb-v2")
        self.manifest = self.root / "taiji-package-manifest.json"
        self.write_manifest()
        self.evidence = self.root / "legacy-v2.json"
        self.write_evidence()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_manifest(self, **updates) -> None:
        manifest = {
            "schema_version": 2,
            "package": "taiji-agent",
            "version": "1.0.0",
            "build_arch": "x86_64",
            "dpkg_arch": "amd64",
            "source_commit": self.commit,
            "deb": self.deb.name,
            "deb_sha256": self.sha256(self.deb),
            "target_baseline_profile_id": self.profile_id,
            "target_baseline_sha256": self.profile_sha256,
            "electron_executable_sha256": "c" * 64,
            "desktop_entry_sha256": "d" * 64,
            "built_at": "2026-08-04T00:00:00Z",
        }
        manifest.update(updates)
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

    def write_complete_legacy_binding(self, *, manifest_updates=None, marker_updates=None) -> None:
        self.source = self.root / f"taiji-agentv1.0-kylin-build-src-{self.commit}.tar.gz"
        self.packages = self.root / "Packages"
        self.packages_gz = self.root / "Packages.gz"
        self.source.write_bytes(b"source-v2")
        self.packages.write_bytes(b"packages-v2")
        self.packages_gz.write_bytes(b"packages-gz-v2")
        self.checksum = self.root / f"{self.deb.name}.sha256"
        self.checksum.write_text(
            f"{self.sha256(self.deb)}  {self.deb.name}\n", encoding="ascii"
        )
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest.update(
            {
                "build_arch": "x86_64",
                "dpkg_arch": "amd64",
                "source_archive": self.source.name,
                "source_sha256": self.sha256(self.source),
                "checksum": self.checksum.name,
                "packages_sha256": self.sha256(self.packages),
                "packages_gz_sha256": self.sha256(self.packages_gz),
            }
        )
        if manifest_updates:
            for key, value in manifest_updates.items():
                if value is None:
                    manifest.pop(key, None)
                else:
                    manifest[key] = value
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        marker = {
            "version": "1.0.0",
            "source_archive": self.source.name,
            "source_sha256": self.sha256(self.source),
            "deb": self.deb.name,
            "deb_sha256": self.sha256(self.deb),
            "checksum": self.checksum.name,
            "built_at": "2026-08-04T00:00:00Z",
            "manifest": self.manifest.name,
            "packages_sha256": self.sha256(self.packages),
            "packages_gz_sha256": self.sha256(self.packages_gz),
            "target_baseline_profile_id": self.profile_id,
            "target_baseline_sha256": self.profile_sha256,
        }
        if marker_updates:
            for key, value in marker_updates.items():
                if value is None:
                    marker.pop(key, None)
                else:
                    marker[key] = value
        self.marker = self.root / ".build-success"
        self.marker.write_text(
            "".join(f"{key}={value}\n" for key, value in marker.items()),
            encoding="utf-8",
        )

    def write_evidence(self, **updates) -> None:
        evidence = {
            "schema_version": 2,
            "evidence_type": "offline-install-rehearsal",
            "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_commit": self.commit,
            "deb_basename": self.deb.name,
            "deb_sha256": self.sha256(self.deb),
            "target_baseline_profile_id": self.profile_id,
            "target_baseline_sha256": self.profile_sha256,
        }
        evidence.update(updates)
        self.evidence.write_text(json.dumps(evidence), encoding="utf-8")

    def args(self, **updates):
        values = {
            "source_commit": self.commit,
            "deb": self.deb,
            "checksum": None,
            "manifest": self.manifest,
            "challenge": "",
        }
        values.update(updates)
        return argparse.Namespace(**values)

    def complete_args(self):
        return self.args(
            checksum=self.checksum,
            build_marker=self.marker,
            source_archive=self.source,
            packages=self.packages,
            packages_gz=self.packages_gz,
            delivery_dir=self.root,
        )

    def cli(self, *extra):
        return subprocess.run(
            [
                sys.executable,
                str(VALIDATOR_PATH),
                "offline",
                "--evidence",
                str(self.evidence),
                "--source-commit",
                self.commit,
                "--deb",
                str(self.deb),
                "--manifest",
                str(self.manifest),
                *extra,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_v2_requires_explicit_legacy_read_only(self):
        with self.assertRaisesRegex(self.validator.EvidenceError, "legacy-v2-read-only"):
            self.validator.validate_build_binding(self.args())

        result = self.cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("v2", result.stderr)
        self.assertNotIn("LEGACY_READ_ONLY", result.stdout)

    def test_v2_cannot_be_pre_signed_or_used_as_current_release(self):
        pre_sign = self.cli("--legacy-v2-read-only", "--pre-sign")
        self.assertNotEqual(pre_sign.returncode, 0)
        self.assertIn("互斥", pre_sign.stderr)

        read_only = self.cli("--legacy-v2-read-only")
        self.assertEqual(read_only.returncode, 0, read_only.stderr)
        self.assertIn("LEGACY_READ_ONLY", read_only.stdout)
        self.assertNotIn("release-evidence-pre-sign-valid", read_only.stdout)

    def test_v2_read_only_binds_evidence_to_manifest_baseline(self):
        self.write_evidence(
            target_baseline_profile_id="other-profile",
            target_baseline_sha256="c" * 64,
        )
        result = self.cli("--legacy-v2-read-only")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("target_baseline", result.stderr)

    def test_v2_read_only_requires_historical_runtime_hashes(self):
        self.write_manifest(electron_executable_sha256=None)
        result = self.cli("--legacy-v2-read-only")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("electron_executable_sha256", result.stderr)

    def test_v2_read_only_preserves_complete_legacy_build_binding(self):
        self.write_complete_legacy_binding()
        self.validator.delivery_inventory_sha256 = lambda _path: "e" * 64
        binding = self.validator.validate_build_binding(
            self.complete_args(), legacy_v2_read_only=True
        )
        self.assertEqual(binding[5:], (self.profile_id, self.profile_sha256))

    def test_v2_read_only_rejects_complete_legacy_marker_mismatch(self):
        cases = (
            ({"target_baseline_profile_id": None}, None),
            ({"target_baseline_sha256": "0" * 64}, None),
            ({}, {"target_baseline_profile_id": "other-profile"}),
            ({}, {"target_baseline_sha256": "f" * 64}),
        )
        for marker_updates, manifest_updates in cases:
            with self.subTest(marker=marker_updates, manifest=manifest_updates):
                self.write_complete_legacy_binding(
                    marker_updates=marker_updates,
                    manifest_updates=manifest_updates,
                )
                self.validator.delivery_inventory_sha256 = lambda _path: "e" * 64
                with self.assertRaises(self.validator.EvidenceError):
                    self.validator.validate_build_binding(
                        self.complete_args(), legacy_v2_read_only=True
                    )

    def test_sales_validator_rejects_legacy_manifest_schema_without_mode(self):
        with self.assertRaisesRegex(self.validator.EvidenceError, "schema_version=2"):
            self.validator.validate_build_binding(self.args(), legacy_v2_read_only=False)

    def test_both_evidence_types_include_target_baseline_binding(self):
        for keys in (self.validator.OFFLINE_KEYS, self.validator.TARGET_KEYS):
            self.assertIn("target_baseline_profile_id", keys)
            self.assertIn("target_baseline_sha256", keys)

    def test_target_schema_binds_machine_observation_human_attestation_and_first_configuration(self):
        for key in (
            "install_observation_basename",
            "install_observation_sha256",
            "install_method_attestation_basename",
            "install_method_attestation_sha256",
            "graphical_installer_evidence_basename",
            "graphical_installer_evidence_sha256",
            "installation_method_evidence",
            "installation_method_machine_observed",
            "first_configuration_cycle_completed",
            "visible_first_configuration_completion",
        ):
            self.assertIn(key, self.validator.TARGET_KEYS)
        self.assertNotIn("first_launch", self.validator.TARGET_KEYS)


if __name__ == "__main__":
    unittest.main()
