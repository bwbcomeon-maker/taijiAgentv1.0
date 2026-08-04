import argparse
import hashlib
import importlib.util
import json
import tempfile
import unittest
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
    spec.loader.exec_module(module)
    return module


class ReleaseEvidenceSchemaV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-schema2-evidence-")
        self.root = Path(self.temporary.name)
        self.commit = "a" * 40
        self.profile_id = "kylin-v10-amd64-123456789abc"
        self.profile_sha256 = "b" * 64
        self.deb = self.root / "taiji-agent_1.0.0_amd64.deb"
        self.source = self.root / f"taiji-agentv1.0-kylin-build-src-{self.commit}.tar.gz"
        self.packages = self.root / "Packages"
        self.packages_gz = self.root / "Packages.gz"
        self.deb.write_bytes(b"deb-v2")
        self.source.write_bytes(b"source-v2")
        self.packages.write_bytes(b"packages-v2")
        self.packages_gz.write_bytes(b"packages-gz-v2")
        self.checksum = self.root / f"{self.deb.name}.sha256"
        self.checksum.write_text(
            f"{self.sha256(self.deb)}  {self.deb.name}\n", encoding="ascii"
        )
        self.manifest = self.root / "taiji-package-manifest.json"
        self.marker = self.root / ".build-success"
        self.write_binding()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_binding(self, *, manifest_updates=None, marker_updates=None) -> None:
        manifest = {
            "schema_version": 2,
            "package": "taiji-agent",
            "version": "1.0.0",
            "build_arch": "x86_64",
            "dpkg_arch": "amd64",
            "source_commit": self.commit,
            "source_archive": self.source.name,
            "source_sha256": self.sha256(self.source),
            "deb": self.deb.name,
            "deb_sha256": self.sha256(self.deb),
            "checksum": self.checksum.name,
            "packages_sha256": self.sha256(self.packages),
            "packages_gz_sha256": self.sha256(self.packages_gz),
            "electron_executable_sha256": "c" * 64,
            "desktop_entry_sha256": "d" * 64,
            "target_baseline_profile_id": self.profile_id,
            "target_baseline_sha256": self.profile_sha256,
            "built_at": "2026-08-04T00:00:00Z",
        }
        if manifest_updates:
            manifest.update(manifest_updates)
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
        self.marker.write_text(
            "".join(f"{key}={value}\n" for key, value in marker.items()),
            encoding="utf-8",
        )

    def args(self):
        return argparse.Namespace(
            source_commit=self.commit,
            deb=self.deb,
            checksum=self.checksum,
            manifest=self.manifest,
            build_marker=self.marker,
            source_archive=self.source,
            packages=self.packages,
            packages_gz=self.packages_gz,
            delivery_dir=self.root,
        )

    def test_schema_v2_build_binding_requires_and_returns_target_baseline_identity(self):
        validator = load_validator()
        validator.delivery_inventory_sha256 = lambda _path: "e" * 64

        binding = validator.validate_build_binding(self.args())

        self.assertEqual(binding[5:], (self.profile_id, self.profile_sha256))

    def test_schema_v2_rejects_missing_or_mismatched_marker_baseline_identity(self):
        validator = load_validator()
        validator.delivery_inventory_sha256 = lambda _path: "e" * 64
        cases = (
            ({"target_baseline_profile_id": None}, None),
            ({"target_baseline_sha256": None}, None),
            ({"target_baseline_profile_id": "wrong-profile"}, None),
            ({"target_baseline_sha256": "0" * 64}, None),
            ({}, {"target_baseline_profile_id": "other-profile"}),
            ({}, {"target_baseline_sha256": "f" * 64}),
        )
        for marker_updates, manifest_updates in cases:
            with self.subTest(marker=marker_updates, manifest=manifest_updates):
                self.write_binding(
                    marker_updates=marker_updates,
                    manifest_updates=manifest_updates,
                )
                with self.assertRaises(validator.EvidenceError):
                    validator.validate_build_binding(self.args())

    def test_sales_validator_rejects_legacy_manifest_schema(self):
        validator = load_validator()
        validator.delivery_inventory_sha256 = lambda _path: "e" * 64
        self.write_binding(manifest_updates={"schema_version": 1})

        with self.assertRaisesRegex(validator.EvidenceError, "schema_version=2"):
            validator.validate_build_binding(self.args())

    def test_both_evidence_types_include_target_baseline_binding(self):
        validator = load_validator()

        for keys in (validator.OFFLINE_KEYS, validator.TARGET_KEYS):
            self.assertIn("target_baseline_profile_id", keys)
            self.assertIn("target_baseline_sha256", keys)

    def test_target_schema_binds_machine_observation_human_attestation_and_first_configuration(self):
        validator = load_validator()
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
            self.assertIn(key, validator.TARGET_KEYS)
        self.assertNotIn("first_launch", validator.TARGET_KEYS)


if __name__ == "__main__":
    unittest.main()
