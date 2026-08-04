import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "packaging/linux/deb/publish-single-deb.sh"


def write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(0o755)


class SingleDebPublisherGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-single-deb-publisher-")
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.delivery = self.repo / "taijiagent 打包交付"
        self.package_dir = self.delivery / "生成的安装包"
        self.offline_evidence = self.delivery / "offline-install-rehearsal"
        self.target_evidence = self.delivery / "target-verification"
        self.profile_dir = self.delivery / "目标基线"
        self.output = self.root / "customer"
        self.receipts = self.root / "receipts"
        self.fake_bin = self.root / "bin"
        self.gate_log = self.root / "gate.log"
        self.profile_id = "kylin-v10-amd64-123456789abc"
        self.profile_sha256 = ""

        publisher = self.repo / "packaging/linux/deb/publish-single-deb.sh"
        publisher.parent.mkdir(parents=True)
        shutil.copy2(PUBLISHER, publisher)
        publisher.chmod(0o755)
        self.publisher = publisher
        (self.repo / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (publisher.parent / "runtime-depends.txt").write_text("libc6\n", encoding="utf-8")
        approved_maintainer = self.repo / "packaging/linux/approved-maintainer.json"
        approved_maintainer.write_text(
            json.dumps(
                {
                    "schema": "taiji-approved-maintainer/v1",
                    "maintainer": "Taiji Release <release@taiji.example.cn>",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        shutil.copy2(
            ROOT / "packaging/linux/validate-approved-maintainer.py",
            self.repo / "packaging/linux/validate-approved-maintainer.py",
        )
        self.approved_maintainer_sha256 = hashlib.sha256(
            approved_maintainer.read_bytes()
        ).hexdigest()
        self.package_dir.mkdir(parents=True)
        self.offline_evidence.mkdir()
        self.target_evidence.mkdir()
        self.profile_dir.mkdir()
        self.fake_bin.mkdir()

        self.profile = self.profile_dir / "target-baseline.json"
        self.profile.write_text(
            json.dumps({"profile_id": self.profile_id}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.profile_sha256 = hashlib.sha256(self.profile.read_bytes()).hexdigest()
        self.deb = self.package_dir / "taiji-agent_1.0.0_amd64.deb"
        self.deb.write_bytes(b"taiji-sales-deb-v1\n")
        self.deb_sha256 = hashlib.sha256(self.deb.read_bytes()).hexdigest()
        (self.package_dir / "taiji-package-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "deb": self.deb.name,
                    "deb_sha256": self.deb_sha256,
                    "target_baseline_profile_id": self.profile_id,
                    "target_baseline_sha256": self.profile_sha256,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.package_dir / ".build-success").write_text(
            f"deb={self.deb.name}\n"
            f"deb_sha256={self.deb_sha256}\n"
            f"target_baseline_profile_id={self.profile_id}\n"
            f"target_baseline_sha256={self.profile_sha256}\n",
            encoding="utf-8",
        )

        common = {
            "schema_version": 2,
            "deb_basename": self.deb.name,
            "deb_sha256": self.deb_sha256,
            "target_baseline_profile_id": self.profile_id,
            "target_baseline_sha256": self.profile_sha256,
        }
        (self.offline_evidence / "offline-install-rehearsal.json").write_text(
            json.dumps({**common, "evidence_type": "offline-install-rehearsal"}) + "\n",
            encoding="utf-8",
        )
        (self.target_evidence / "target-verification.json").write_text(
            json.dumps(
                {
                    **common,
                    "evidence_type": "target-desktop-verification",
                    "installation_method": "desktop-double-click",
                    "installation_method_evidence": "human-attestation",
                    "installation_method_machine_observed": False,
                    "installation_network": "continuous-process-sampling-no-non-loopback-up",
                    "installation_file_count": 1,
                    "additional_install_files": False,
                    "dpkg_status_before": "not-installed",
                    "dpkg_status_after": "install ok installed",
                    "first_configuration_cycle_completed": True,
                    "visible_first_configuration_completion": True,
                    "target_verified": True,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        for evidence in (
            self.offline_evidence / "offline-install-rehearsal.json",
            self.target_evidence / "target-verification.json",
        ):
            Path(f"{evidence}.sig").write_bytes(b"signed-evidence-fixture\n")

        write_executable(
            self.repo / "packaging/linux/target_baseline.py",
            """
            #!/usr/bin/env python3
            import sys
            raise SystemExit(0 if len(sys.argv) > 1 and sys.argv[1] == "validate" else 2)
            """,
        )
        write_executable(
            self.delivery / "01_制包机_发布预检.sh",
            """
            #!/usr/bin/env bash
            set -eu
            printf '01\n' >> "$TEST_GATE_LOG"
            if [ "${TEST_MUTATE_SOURCE:-0}" = 1 ]; then
              printf 'mutated-during-gate\n' >> "$TEST_SOURCE_DEB"
            fi
            if [ "${TEST_MUTATE_TARGET_SIGNATURE:-0}" = 1 ]; then
              printf 'mutated-signature\n' >> "$TEST_TARGET_SIGNATURE"
            fi
            [ "${TEST_FAIL_01:-0}" != 1 ]
            """,
        )
        write_executable(
            self.repo / "scripts/taiji-release-check.sh",
            """
            #!/usr/bin/env bash
            set -eu
            printf 'release-check\n' >> "$TEST_GATE_LOG"
            [ "${TAIJI_DELIVERY_DIR:?}" = "$TEST_DELIVERY_DIR" ]
            [ "${TAIJI_OFFLINE_REHEARSAL_DIR:?}" = "$TEST_OFFLINE_DIR" ]
            [ "${TAIJI_TARGET_VERIFICATION_DIR:?}" = "$TEST_TARGET_DIR" ]
            if [ "${TEST_OCCUPY_OUTPUT:-0}" = 1 ]; then
              mkdir "$TEST_OUTPUT_DIR"
              printf 'concurrent-owner\n' > "$TEST_OUTPUT_DIR/keep.txt"
            fi
            [ "${TEST_FAIL_RELEASE_CHECK:-0}" != 1 ]
            """,
        )
        write_executable(
            self.fake_bin / "dpkg-deb",
            """
            #!/usr/bin/env bash
            set -eu
            case "$1" in
              -f)
                case "$3" in
                  Package) printf 'taiji-agent\n' ;;
                  Version) printf '1.0.0\n' ;;
                  Architecture) printf 'amd64\n' ;;
                  Maintainer) printf 'Taiji Release <release@taiji.example.cn>\n' ;;
                  *) exit 2 ;;
                esac
                ;;
              -x)
                mkdir -p "$3/opt/taiji-agent/resources"
                cp -- "$TEST_PROFILE" "$3/opt/taiji-agent/resources/target-baseline.json"
                printf '{"targetBaselineProfile":"%s","targetBaselineSha256":"%s"}\n' \
                  "$TEST_PROFILE_ID" "$TEST_PROFILE_SHA256" \
                  > "$3/opt/taiji-agent/resources/taiji-release-manifest.json"
                ;;
              *) exit 2 ;;
            esac
            """,
        )
        write_executable(
            self.fake_bin / "install",
            """
            #!/usr/bin/env bash
            set -eu
            destination="${!#}"
            case "$destination" in
              "$TEST_RECEIPT_ROOT"/*/target-baseline.json)
                if [ "${TEST_OCCUPY_RECEIPT:-0}" = 1 ]; then
                  mkdir "$TEST_CONCURRENT_RECEIPT_DIR"
                  printf 'concurrent-owner\n' > "$TEST_CONCURRENT_RECEIPT_DIR/keep.txt"
                fi
                if [ "${TEST_FAIL_RECEIPT_BASELINE_COPY:-0}" = 1 ]; then
                  receipt_dir="$(dirname "$destination")"
                  [ -f "$receipt_dir/publication-receipt.json" ]
                  printf 'receipt-json-observed\n' > "$TEST_RECEIPT_JSON_MARKER"
                  exit 91
                fi
                ;;
            esac
            exec "$TEST_REAL_INSTALL" "$@"
            """,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_publisher(
        self,
        *extra: str,
        env_updates=None,
        use_default_receipt_root: bool = False,
        delivery_dir: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        selected_delivery = delivery_dir or self.delivery
        env = {
            **os.environ,
            "PATH": f"{self.fake_bin}{os.pathsep}{os.environ['PATH']}",
            "TEST_GATE_LOG": str(self.gate_log),
            "TEST_SOURCE_DEB": str(self.deb),
            "TEST_TARGET_SIGNATURE": str(
                self.target_evidence / "target-verification.json.sig"
            ),
            "TEST_DELIVERY_DIR": str(selected_delivery.resolve()),
            "TEST_OFFLINE_DIR": str(
                (selected_delivery / "offline-install-rehearsal").resolve()
            ),
            "TEST_TARGET_DIR": str(
                (selected_delivery / "target-verification").resolve()
            ),
            "TEST_OUTPUT_DIR": str(self.output),
            "TEST_RECEIPT_ROOT": str(self.receipts.resolve()),
            "TEST_CONCURRENT_RECEIPT_DIR": str(
                (
                    self.receipts
                    / f"1.0.0-{self.profile_id}-{self.deb_sha256[:12]}"
                ).resolve()
            ),
            "TEST_REAL_INSTALL": shutil.which("install") or "/usr/bin/install",
            "TEST_PROFILE": str(self.profile),
            "TEST_PROFILE_ID": self.profile_id,
            "TEST_PROFILE_SHA256": self.profile_sha256,
        }
        if env_updates:
            env.update(env_updates)
        command = [
            "bash",
            str(self.publisher),
            "--delivery-dir",
            str(selected_delivery),
            "--output-dir",
            str(self.output),
        ]
        if not use_default_receipt_root:
            command.extend(("--receipt-root", str(self.receipts)))
        command.extend(extra)
        return subprocess.run(
            command,
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_success_runs_both_formal_gates_then_publishes_one_snapshotted_deb(self) -> None:
        original = self.deb.read_bytes()

        result = self.run_publisher()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.gate_log.read_text(encoding="utf-8").splitlines(), ["01", "release-check"])
        published = list(self.output.iterdir())
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].suffix, ".deb")
        self.assertEqual(published[0].read_bytes(), original)
        receipt_files = list(self.receipts.glob("*/publication-receipt.json"))
        self.assertEqual(len(receipt_files), 1)
        receipt_dir = receipt_files[0].parent
        self.assertEqual(
            sorted(path.name for path in receipt_dir.iterdir()),
            ["deb.sha256", "publication-receipt.json", "target-baseline.json"],
        )
        self.assertEqual(
            (receipt_dir / "target-baseline.json").read_bytes(),
            self.profile.read_bytes(),
        )
        self.assertEqual(
            (receipt_dir / "deb.sha256").read_text(encoding="utf-8"),
            f"{self.deb_sha256}  {published[0].name}\n",
        )
        self.assertFalse(any(path.name.startswith(".taiji-receipt.") for path in self.receipts.iterdir()))
        receipt = json.loads(receipt_files[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["deb_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(receipt["target_profile_id"], self.profile_id)
        self.assertEqual(
            receipt["approved_maintainer_sha256"],
            self.approved_maintainer_sha256,
        )
        self.assertEqual(
            receipt["offline_evidence_sha256"],
            hashlib.sha256(
                (self.offline_evidence / "offline-install-rehearsal.json").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            receipt["offline_signature_sha256"],
            hashlib.sha256(
                (self.offline_evidence / "offline-install-rehearsal.json.sig").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            receipt["target_evidence_sha256"],
            hashlib.sha256(
                (self.target_evidence / "target-verification.json").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            receipt["target_signature_sha256"],
            hashlib.sha256(
                (self.target_evidence / "target-verification.json.sig").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(receipt["formal_gates"], ["01", "taiji-release-check"])
        self.assertEqual(
            receipt["signed_evidence_types"],
            ["offline-install-rehearsal", "target-desktop-verification"],
        )

    def test_missing_either_signature_fails_without_publication_or_receipt(self) -> None:
        for evidence_name in (
            "offline-install-rehearsal/offline-install-rehearsal.json.sig",
            "target-verification/target-verification.json.sig",
        ):
            with self.subTest(evidence=evidence_name):
                signature = self.delivery / evidence_name
                payload = signature.read_bytes()
                signature.unlink()
                result = self.run_publisher()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.receipts.exists())
                signature.write_bytes(payload)

    def test_default_internal_receipt_root_is_created_only_after_gates(self) -> None:
        result = self.run_publisher(use_default_receipt_root=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        default_root = self.repo / "runtime/release-evidence/single-deb"
        self.assertEqual(
            len(list(default_root.glob("*/publication-receipt.json"))),
            1,
        )

    def test_source_deb_replacement_during_gate_fails_closed(self) -> None:
        result = self.run_publisher(env_updates={"TEST_MUTATE_SOURCE": "1"})

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("changed", (result.stdout + result.stderr).lower())
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipts.exists())

    def test_signed_evidence_replacement_during_gate_fails_closed(self) -> None:
        result = self.run_publisher(
            env_updates={"TEST_MUTATE_TARGET_SIGNATURE": "1"}
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("changed", (result.stdout + result.stderr).lower())
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipts.exists())

    def test_unapproved_deb_maintainer_fails_without_publication_or_receipt(self) -> None:
        approved = self.repo / "packaging/linux/approved-maintainer.json"
        approved.write_text(
            json.dumps(
                {
                    "schema": "taiji-approved-maintainer/v1",
                    "maintainer": "Other Release <release@other.example.cn>",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        result = self.run_publisher()

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("maintainer", (result.stdout + result.stderr).lower())
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipts.exists())

    def test_concurrent_output_owner_is_preserved_and_no_receipt_is_created(self) -> None:
        result = self.run_publisher(env_updates={"TEST_OCCUPY_OUTPUT": "1"})

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.output.is_dir(), result.stdout + result.stderr)
        self.assertEqual(
            (self.output / "keep.txt").read_text(encoding="utf-8"),
            "concurrent-owner\n",
        )
        self.assertFalse(self.receipts.exists())

    def test_arbitrary_deb_input_is_not_a_supported_publication_interface(self) -> None:
        result = subprocess.run(
            ["bash", str(self.publisher), "--deb", str(self.deb)],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--delivery-dir", result.stderr)

    def test_external_delivery_preflight_is_never_executed(self) -> None:
        external_delivery = self.root / "external-delivery"
        shutil.copytree(self.delivery, external_delivery)
        malicious_marker = self.root / "malicious-preflight-ran"
        write_executable(
            external_delivery / "01_制包机_发布预检.sh",
            """
            #!/usr/bin/env bash
            set -eu
            printf 'executed\n' > "$TEST_MALICIOUS_MARKER"
            """,
        )

        result = self.run_publisher(
            delivery_dir=external_delivery,
            env_updates={"TEST_MALICIOUS_MARKER": str(malicious_marker)},
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("formal", (result.stdout + result.stderr).lower())
        self.assertFalse(malicious_marker.exists())
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipts.exists())

    def test_post_publish_failure_path_owns_a_safe_customer_output_rollback(self) -> None:
        source = PUBLISHER.read_text(encoding="utf-8")

        self.assertIn("OUTPUT_PUBLISHED=0", source)
        self.assertIn("safe_remove_published_output", source)
        self.assertIn('if [ "$status" -ne 0 ] && [ "$OUTPUT_PUBLISHED" = "1" ]', source)
        self.assertIn('safe_remove_published_output || true', source)
        self.assertIn("O_NOFOLLOW", source)
        self.assertIn("os.rmdir(output_name, dir_fd=parent_descriptor)", source)

    def test_receipt_attachment_failure_leaves_no_customer_or_formal_receipt(self) -> None:
        marker = self.root / "receipt-json-was-written"

        result = self.run_publisher(
            env_updates={
                "TEST_FAIL_RECEIPT_BASELINE_COPY": "1",
                "TEST_RECEIPT_JSON_MARKER": str(marker),
            }
        )

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(marker.is_file(), result.stdout + result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(
            any(self.receipts.glob("*/publication-receipt.json")),
            result.stdout + result.stderr,
        )
        self.assertFalse(self.receipts.exists(), result.stdout + result.stderr)

    def test_concurrent_receipt_owner_is_preserved_on_atomic_publish_failure(self) -> None:
        concurrent_receipt = (
            self.receipts / f"1.0.0-{self.profile_id}-{self.deb_sha256[:12]}"
        )

        result = self.run_publisher(env_updates={"TEST_OCCUPY_RECEIPT": "1"})

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.output.exists())
        self.assertTrue(concurrent_receipt.is_dir(), result.stdout + result.stderr)
        self.assertEqual(
            (concurrent_receipt / "keep.txt").read_text(encoding="utf-8"),
            "concurrent-owner\n",
        )
        self.assertFalse((concurrent_receipt / "publication-receipt.json").exists())
        self.assertEqual(
            sorted(path.name for path in self.receipts.iterdir()),
            [concurrent_receipt.name],
        )


if __name__ == "__main__":
    unittest.main()
