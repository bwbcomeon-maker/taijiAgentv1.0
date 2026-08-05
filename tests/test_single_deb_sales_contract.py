"""Static sales contract for the unified single-DEB publisher."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "packaging/linux/deb/publish-single-deb.sh"


class SingleDebSalesContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.publisher = PUBLISHER.read_text(encoding="utf-8")

    def test_cli_requires_only_unified_inputs(self):
        for token in (
            "--delivery-dir",
            "--candidate-deb",
            "--policy",
            "--certification-set",
            "--certification-signature",
            "--release-evidence",
            "--release-signature",
            "--output-dir",
            "--receipt-root",
        ):
            self.assertIn(token, self.publisher)
        for forbidden in (
            "target_baseline.py",
            "runtime-depends.txt",
            "approved-maintainer.json",
            "TAIJI_PACKAGE_MAINTAINER",
        ):
            self.assertNotIn(forbidden, self.publisher)

    def test_customer_and_receipt_allowlists_are_explicit(self):
        self.assertIn("exactly-one-deb", self.publisher)
        self.assertIn("renameat2", self.publisher)
        self.assertIn("renameatx_np", self.publisher)
        self.assertIn("receipt identity is already reserved", self.publisher)
        for name in (
            "release-evidence.json",
            "release-evidence.json.sig",
            "certification-set.json",
            "certification-set.json.sig",
            "compatibility-policy.json",
            "deb.sha256",
        ):
            self.assertIn(name, self.publisher)
        self.assertIn("RECEIPT_NAMES", self.publisher)

    def test_publisher_has_input_snapshot_and_identity_bound_rollback(self):
        for token in (
            "snapshot(",
            "verify_identity(",
            "publisher input changed during formal gate",
            "rollback_output",
            "rollback_receipt",
            "output_published",
            "receipt_published",
        ):
            self.assertIn(token, self.publisher)

    def test_customer_payload_is_not_mutated_by_evidence(self):
        self.assertIn("shutil.copyfile(snapshots[\"candidate.deb\"][\"path\"], output_staging / customer_name)", self.publisher)
        self.assertNotIn("dpkg-deb -x", self.publisher)
        self.assertNotIn("install -m 0600", self.publisher)


if __name__ == "__main__":
    unittest.main()
