"""Dynamic gates for the unified single-DEB publisher."""

from __future__ import annotations

import hashlib
import importlib.util
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
POLICY = ROOT / "packaging/linux/compatibility-policy.json"
POLICY_HELPER = ROOT / "packaging/linux/compatibility_policy.py"
PUBLIC_KEY = ROOT / "tools/taiji-release-evidence/signing-public.pem"


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
        self.output = self.root / "customer"
        self.receipts = self.root / "receipts"
        self.fake_bin = self.root / "bin"
        self.gate_log = self.root / "gate.log"
        self.fake_bin.mkdir()
        self.package_dir.mkdir(parents=True)
        self.delivery.mkdir(exist_ok=True)

        publisher = self.repo / "packaging/linux/deb/publish-single-deb.sh"
        publisher.parent.mkdir(parents=True)
        shutil.copy2(PUBLISHER, publisher)
        publisher.chmod(0o755)
        policy_dir = self.repo / "packaging/linux"
        policy_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(POLICY, policy_dir / "compatibility-policy.json")
        shutil.copy2(POLICY_HELPER, policy_dir / "compatibility_policy.py")
        public_dir = self.repo / "tools/taiji-release-evidence"
        public_dir.mkdir(parents=True)
        shutil.copy2(PUBLIC_KEY, public_dir / "signing-public.pem")
        write_executable(
            self.repo / "scripts/taiji-release-check.sh",
            """
            #!/usr/bin/env bash
            set -eu
            printf 'release-check\\n' >> "$TEST_GATE_LOG"
            if [ "${TEST_MUTATE_INPUT:-0}" = 1 ]; then
              printf 'mutated-after-snapshot\\n' >> "$TEST_CANDIDATE_DEB"
            fi
            exit 0
            """,
        )
        write_executable(
            self.fake_bin / "dpkg-deb",
            """
            #!/usr/bin/env bash
            set -eu
            [ "$1" = -f ]
            case "$3" in
              Package) printf 'taiji-agent\\n' ;;
              Version) printf '1.0.0\\n' ;;
              Architecture) printf 'amd64\\n' ;;
              Maintainer) printf '%s\\n' "$TEST_MAINTAINER" ;;
              *) exit 2 ;;
            esac
            """,
        )
        write_executable(
            self.fake_bin / "openssl",
            """
            #!/usr/bin/env bash
            exit 0
            """,
        )

        self.deb = self.package_dir / "taiji-agent_1.0.0_amd64.deb"
        self.deb.write_bytes(b"immutable-unified-deb-v1\n")
        self.deb_sha = hashlib.sha256(self.deb.read_bytes()).hexdigest()
        helper = self._load_policy_helper()
        policy = helper.load_and_validate(POLICY)
        policy_sha = helper.canonical_sha256(policy)
        self.maintainer = policy["package"]["maintainer"]
        self.policy_sha = policy_sha
        self.certification = self.delivery / "certification-set.json"
        self.certification.write_text(
            json.dumps(
                {
                    "schema": "taiji-linux-certification-set/v1",
                    "generated_at_utc": "2026-08-05T00:00:00Z",
                    "challenge_nonce": "a" * 64,
                    "source_commit": "b" * 40,
                    "version": "1.0.0",
                    "architecture": "amd64",
                    "deb_basename": self.deb.name,
                    "deb_sha256": self.deb_sha,
                    "compatibility_policy_id": policy["policy_id"],
                    "compatibility_policy_sha256": policy_sha,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.certification_signature = Path(f"{self.certification}.sig")
        self.certification_signature.write_bytes(b"certification-signature")
        self.release = self.delivery / "release-evidence.json"
        self.release.write_text(
            json.dumps(
                {
                    "schema": "taiji-release-evidence/v3",
                    "evidence_type": "single-deb-publication",
                    "generated_at_utc": "2026-08-05T00:00:00Z",
                    "challenge_nonce": "c" * 64,
                    "source_commit": "b" * 40,
                    "version": "1.0.0",
                    "architecture": "amd64",
                    "deb_basename": self.deb.name,
                    "deb_sha256": self.deb_sha,
                    "compatibility_policy_id": policy["policy_id"],
                    "compatibility_policy_sha256": policy_sha,
                    "certification_set_basename": self.certification.name,
                    "certification_set_sha256": hashlib.sha256(self.certification.read_bytes()).hexdigest(),
                    "certification_set_signature_basename": self.certification_signature.name,
                    "certification_set_signature_sha256": hashlib.sha256(self.certification_signature.read_bytes()).hexdigest(),
                    "maintainer": self.maintainer,
                    "customer_filename": self.deb.name,
                    "customer_folder_contract": "exactly-one-deb",
                    "signing_public_key_fingerprint": "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da",
                    "formal_gates": {"certification_set": "PASS"},
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.release_signature = Path(f"{self.release}.sig")
        self.release_signature.write_bytes(b"release-signature")
        self.policy = self.repo / "packaging/linux/compatibility-policy.json"

    @staticmethod
    def _load_policy_helper():
        spec = importlib.util.spec_from_file_location("publisher_policy_test", POLICY_HELPER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_publisher(self, *, extra_env=None, output=None, receipts=None):
        env = {
            **os.environ,
            "PATH": f"{self.fake_bin}{os.pathsep}{os.environ['PATH']}",
            "TEST_GATE_LOG": str(self.gate_log),
            "TEST_CANDIDATE_DEB": str(self.deb),
            "TEST_MAINTAINER": self.maintainer,
        }
        if extra_env:
            env.update(extra_env)
        command = [
            "bash",
            str(self.repo / "packaging/linux/deb/publish-single-deb.sh"),
            "--delivery-dir",
            str(self.delivery),
            "--candidate-deb",
            str(self.deb),
            "--policy",
            str(self.policy),
            "--certification-set",
            str(self.certification),
            "--certification-signature",
            str(self.certification_signature),
            "--release-evidence",
            str(self.release),
            "--release-signature",
            str(self.release_signature),
            "--output-dir",
            str(output or self.output),
            "--receipt-root",
            str(receipts or self.receipts),
        ]
        return subprocess.run(command, cwd=self.repo, env=env, text=True, capture_output=True, check=False)

    def test_success_directory_contains_only_fixed_basename_deb(self):
        result = self.run_publisher()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([item.name for item in self.output.iterdir()], [self.deb.name])
        self.assertEqual((self.output / self.deb.name).read_bytes(), self.deb.read_bytes())
        receipt = next(self.receipts.iterdir())
        self.assertEqual(
            {item.name for item in receipt.iterdir()},
            {
                "release-evidence.json",
                "release-evidence.json.sig",
                "certification-set.json",
                "certification-set.json.sig",
                "compatibility-policy.json",
                "deb.sha256",
            },
        )
        self.assertEqual((receipt / "deb.sha256").read_text(), f"{self.deb_sha}  {self.deb.name}\n")
        self.assertEqual(self.gate_log.read_text().splitlines(), ["release-check"])

    def test_input_replacement_during_gate_fails_closed(self):
        result = self.run_publisher(extra_env={"TEST_MUTATE_INPUT": "1"})

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipts.exists() and any(self.receipts.iterdir()))

    def test_v2_or_mismatched_signed_inputs_cannot_publish(self):
        release = json.loads(self.release.read_text())
        release["schema"] = "taiji-release-evidence/v2"
        self.release.write_text(json.dumps(release) + "\n")

        result = self.run_publisher()

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_certification_is_never_written_back_into_deb(self):
        original = self.deb.read_bytes()
        result = self.run_publisher()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.deb.read_bytes(), original)
        self.assertNotIn("certification-set", self.deb.read_bytes().decode("utf-8", errors="ignore"))

    def test_concurrent_output_or_receipt_is_never_overwritten(self):
        self.output.mkdir()
        (self.output / "owner.txt").write_text("owner")
        result = self.run_publisher()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.output / "owner.txt").read_text(), "owner")


if __name__ == "__main__":
    unittest.main()
