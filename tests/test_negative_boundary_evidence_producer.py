"""Controlled negative-boundary evidence producer contract tests."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCER = ROOT / "scripts/produce-taiji-negative-boundary-evidence.py"
RENDERER = ROOT / "packaging/linux/deb/render-preinst.py"
PREINST_TEMPLATE = ROOT / "packaging/linux/deb/preinst"
POLICY = ROOT / "packaging/linux/compatibility-policy.json"
POLICY_HELPER = ROOT / "packaging/linux/compatibility_policy.py"
MATRIX = ROOT / "packaging/linux/certification-matrix.json"
CONTRACT = ROOT / "tools/taiji-desktop-acceptance/assemble-target-evidence.py"
CHALLENGE = "d" * 64


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ar_member(name: str, payload: bytes, mode: int = 0o100644) -> bytes:
    encoded_name = (name + "/").encode("ascii")
    if len(encoded_name) > 16:
        raise ValueError(name)
    header = b"".join(
        (
            encoded_name.ljust(16, b" "),
            b"0".ljust(12, b" "),
            b"0".ljust(6, b" "),
            b"0".ljust(6, b" "),
            format(mode, "o").encode("ascii").ljust(8, b" "),
            str(len(payload)).encode("ascii").ljust(10, b" "),
            b"`\n",
        )
    )
    return header + payload + (b"\n" if len(payload) % 2 else b"")


def make_deb(path: Path, preinst: bytes) -> None:
    control_buffer = io.BytesIO()
    with tarfile.open(fileobj=control_buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("./preinst")
        info.mode = 0o755
        info.size = len(preinst)
        archive.addfile(info, io.BytesIO(preinst))
    path.write_bytes(
        b"!<arch>\n"
        + ar_member("debian-binary", b"2.0\n")
        + ar_member("control.tar.gz", control_buffer.getvalue())
        + ar_member("data.tar.gz", b"fixture-data")
    )


class NegativeBoundaryEvidenceProducerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="taiji-negative-producer-")
        self.root = Path(self.temporary.name)
        self.rendered = self.root / "preinst"
        rendered = subprocess.run(
            [
                sys.executable,
                str(RENDERER),
                "--template",
                str(PREINST_TEMPLATE),
                "--policy",
                str(POLICY),
                "--output",
                str(self.rendered),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.deb = self.root / "taiji-agent_1.2.3_amd64.deb"
        make_deb(self.deb, self.rendered.read_bytes())
        helper = load_module(POLICY_HELPER, "taiji_negative_policy_fixture")
        policy = helper.load_and_validate(POLICY)
        self.policy_id = policy["policy_id"]
        self.policy_sha = helper.canonical_sha256(policy)
        self.manifest = self.root / "taiji-package-manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "schema": "taiji-package-manifest/v3",
                    "package": "taiji-agent",
                    "version": "1.2.3",
                    "architecture": "amd64",
                    "source_commit": "a" * 40,
                    "deb_basename": self.deb.name,
                    "deb_sha256": sha256(self.deb),
                    "compatibility_policy_id": self.policy_id,
                    "compatibility_policy_sha256": self.policy_sha,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.output = self.root / "negative-records"

    def tearDown(self):
        self.temporary.cleanup()

    def run_producer(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(PRODUCER),
                "--deb",
                str(self.deb),
                "--manifest",
                str(self.manifest),
                "--policy",
                str(POLICY.resolve()),
                "--matrix",
                str(MATRIX.resolve()),
                "--output",
                str(self.output),
                "--challenge",
                CHALLENGE,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_executes_all_six_real_preinst_boundaries_and_emits_closed_records(self):
        result = self.run_producer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
        boundaries = {item["id"]: item for item in matrix["negative_boundaries"]}
        self.assertEqual({path.name for path in self.output.iterdir()}, set(boundaries))
        contract = load_module(CONTRACT, "taiji_negative_contract_fixture")
        for category_id, boundary in boundaries.items():
            with self.subTest(category_id=category_id):
                category_dir = self.output / category_id
                self.assertEqual(
                    {path.name for path in category_dir.iterdir()},
                    {
                        "environment-evidence.json",
                        "preflight-result.json",
                        "business-data-inventory.json",
                    },
                )
                record = json.loads(
                    (category_dir / "environment-evidence.json").read_text(encoding="utf-8")
                )
                preflight_payload = (category_dir / "preflight-result.json").read_bytes()
                preflight = json.loads(preflight_payload)
                self.assertEqual(record["source_commit"], "a" * 40)
                self.assertEqual(record["deb_sha256"], sha256(self.deb))
                self.assertEqual(record["challenge_nonce"], CHALLENGE)
                self.assertEqual(record["checks"], {"preflight": "BLOCKED"})
                self.assertFalse(record["security_facts"]["business_data_mutation"])
                self.assertEqual(
                    record["security_facts"]["business_data_scope_id"],
                    "taiji-user-and-install-state-v1",
                )
                self.assertEqual(
                    record["security_facts"]["business_data_before_sha256"],
                    record["security_facts"]["business_data_after_sha256"],
                )
                self.assertEqual(preflight["error_code"], boundary["stable_error_code"])
                self.assertEqual(preflight["failed_capabilities"], [boundary["stable_error_code"]])
                contract.validate_negative_preflight_attachment(record, matrix, preflight_payload)
                inventory_payload = (category_dir / "business-data-inventory.json").read_bytes()
                contract.validate_negative_business_data_attachment(
                    record,
                    matrix,
                    inventory_payload,
                )

    def test_mutation_in_any_protected_xdg_or_install_path_fails_without_output(self):
        producer = load_module(PRODUCER, "taiji_negative_scope_mutation_fixture")
        original = producer._run_preflight

        def mutate_protected_path(preinst, fixture_root, os_release, scenario, result_path):
            completed = original(preinst, fixture_root, os_release, scenario, result_path)
            injected = fixture_root / "home/customer/.config/taiji-agent/injected.json"
            injected.parent.mkdir(parents=True, exist_ok=True)
            injected.write_text('{"mutated":true}\n', encoding="utf-8")
            return completed

        producer._run_preflight = mutate_protected_path
        args = Namespace(
            deb=self.deb.resolve(),
            manifest=self.manifest.resolve(),
            policy=POLICY.resolve(),
            matrix=MATRIX.resolve(),
            output=self.output.resolve(),
            challenge=CHALLENGE,
        )
        with self.assertRaisesRegex(producer.NegativeEvidenceError, "business data|protected"):
            producer.produce(args)
        self.assertFalse(self.output.exists())

    def test_permission_mutation_on_protected_directory_root_fails_without_output(self):
        producer = load_module(PRODUCER, "taiji_negative_scope_mode_mutation_fixture")
        original = producer._run_preflight

        def mutate_protected_root_mode(preinst, fixture_root, os_release, scenario, result_path):
            completed = original(preinst, fixture_root, os_release, scenario, result_path)
            protected_root = fixture_root / "home/customer/.local/share/taiji-agent"
            protected_root.chmod(0o700)
            return completed

        producer._run_preflight = mutate_protected_root_mode
        args = Namespace(
            deb=self.deb.resolve(),
            manifest=self.manifest.resolve(),
            policy=POLICY.resolve(),
            matrix=MATRIX.resolve(),
            output=self.output.resolve(),
            challenge=CHALLENGE,
        )
        with self.assertRaisesRegex(producer.NegativeEvidenceError, "business data|protected"):
            producer.produce(args)
        self.assertFalse(self.output.exists())

    def test_manifest_deb_hash_mismatch_fails_without_partial_output(self):
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["deb_sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

        result = self.run_producer()

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())

    def test_wrong_preinst_error_code_fails_closed_without_partial_output(self):
        forged = self.rendered.read_text(encoding="utf-8").replace(
            "TAIJI-LINUX-E001-ARCH",
            "TAIJI-LINUX-E999-FORGED",
        )
        make_deb(self.deb, forged.encode("utf-8"))
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["deb_sha256"] = sha256(self.deb)
        self.manifest.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

        result = self.run_producer()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from the canonical rendered source", result.stderr)
        self.assertFalse(self.output.exists())

    def test_noncanonical_preinst_is_rejected_before_any_candidate_code_executes(self):
        marker = self.root / "candidate-code-executed"
        forged = self.rendered.read_text(encoding="utf-8").replace(
            "set -euo pipefail\n",
            "set -euo pipefail\nprintf 'executed\\n' > {}\n".format(shlex.quote(str(marker))),
            1,
        )
        make_deb(self.deb, forged.encode("utf-8"))
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["deb_sha256"] = sha256(self.deb)
        self.manifest.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

        result = self.run_producer()

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        self.assertFalse(self.output.exists())

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep\n", encoding="utf-8")

        result = self.run_producer()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")


if __name__ == "__main__":
    unittest.main()
