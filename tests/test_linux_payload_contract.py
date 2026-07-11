from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
CONTRACT_FILE = ROOT / "packaging/linux/payload-contract.json"
VERIFIER = ROOT / "packaging/linux/verify-payload.py"
EMBEDDED_CONTRACT = Path("opt/taiji-agent/resources/payload-contract.json")
PUBLIC_KEY = ROOT / "tools/taiji-license-issuer/private/signing-public.pem"
PUBLIC_KEY_FINGERPRINT = "2dcff4f2b5e6f7a5e7e3f730e2f4446ad3265964431f614de7550265f7628b35"


def load_verifier_module():
    spec = importlib.util.spec_from_file_location("taiji_verify_payload", VERIFIER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load verifier module: {VERIFIER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LinuxPayloadContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="taiji-payload-contract-"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

    def _write(self, root: Path, relative: str, content: str, mode: int = 0o644) -> Path:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            target.chmod(0o644)
        target.write_text(content, encoding="utf-8")
        target.chmod(mode)
        return target

    def _assembled_payload(self) -> Path:
        root = self.temp_dir / "assembled"
        root.mkdir()
        contract = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
        self._write(root, contract["product_version"]["source"], VERSION_FILE.read_text(encoding="utf-8"))
        self._write(root, EMBEDDED_CONTRACT.as_posix(), json.dumps(contract, ensure_ascii=False) + "\n")

        for component in contract["components"]:
            target = root / component["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if component["type"] == "directory":
                target.mkdir(parents=True, exist_ok=True)
            elif not target.exists():
                target.write_text(component["id"] + "\n", encoding="utf-8")
            target.chmod(int(component["mode"], 8))

        self._write(root, "opt/taiji-agent/runtime/agent/PYTHON_VERSION", "3.12.3\n")
        self._write(root, "opt/taiji-agent/runtime/web/PRODUCT_VERSION", "0.1.0\n")
        self._write(
            root,
            "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/package.json",
            json.dumps({"version": "39.8.10"}) + "\n",
        )
        self._write(root, "opt/taiji-agent/runtime/node/NODE_VERSION", "22.23.1\n")
        self._write(
            root,
            "opt/taiji-agent/runtime/docx-engine-v2/package.json",
            json.dumps({"version": "0.1.0"}) + "\n",
        )
        self._write(
            root,
            "opt/taiji-agent/runtime/agent/skills/product-skills.json",
            json.dumps({"schema_version": "taiji-product-skills/v1"}) + "\n",
        )
        self._write(
            root,
            "opt/taiji-agent/runtime/agent/taiji-runtime-profile.json",
            json.dumps(
                {
                    "schema_version": "taiji-runtime-profile/v1",
                    "profile": "installed-production",
                }
            )
            + "\n",
        )
        self._write(
            root,
            "opt/taiji-agent/resources/license/signing-public.pem",
            PUBLIC_KEY.read_text(encoding="utf-8"),
        )
        self._write(
            root,
            "opt/taiji-agent/runtime/docx-engine-v2/template-registry.json",
            json.dumps({"version": 1, "builtin": [], "installed": []}) + "\n",
            mode=0o444,
        )
        return root

    def _verify(self, root: Path, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(VERIFIER), "--root", str(root)],
            cwd=str(cwd or ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_root_version_is_single_product_version_source(self) -> None:
        self.assertEqual(VERSION_FILE.read_text(encoding="utf-8"), "0.1.0\n")
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(encoding="utf-8")
        offline = (ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh").read_text(encoding="utf-8")
        self.assertIn('VERSION_FILE="$REPO_ROOT/VERSION"', build)
        self.assertNotIn('TAIJI_AGENT_VERSION:-0.1.0', build)
        self.assertNotIn('TAIJI_AGENT_VERSION:-0.1.0', offline)

    def test_complete_assembled_payload_fixture_passes(self) -> None:
        completed = self._verify(self._assembled_payload())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_version"], "taiji-payload-contract/v1")
        self.assertEqual(payload["product_version"], "0.1.0")
        self.assertIn("docx_engine_v2", payload["checked_components"])
        evidence = payload["payload_tree"]
        self.assertRegex(evidence["sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(evidence["file_count"], 0)
        self.assertGreater(evidence["byte_count"], 0)
        self.assertGreater(evidence["entry_count"], evidence["file_count"])
        self.assertEqual(evidence["symlink_count"], 0)
        self.assertEqual(
            evidence["symlink_policy"],
            "relative-internal-existing-targets-without-symlink-components",
        )

    def test_payload_tree_checksum_evidence_is_stable_and_counts_streamed_files(self) -> None:
        root = self._assembled_payload()
        large = root / "opt/taiji-agent/runtime/agent/large-runtime.bin"
        large.write_bytes((b"taiji-runtime\0" * 100_000) + b"tail")
        expected_files = [path for path in root.rglob("*") if path.is_file()]
        expected_bytes = sum(path.stat().st_size for path in expected_files)

        first = self._verify(root)
        second = self._verify(root)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        first_evidence = json.loads(first.stdout)["payload_tree"]
        second_evidence = json.loads(second.stdout)["payload_tree"]
        self.assertEqual(first_evidence, second_evidence)
        self.assertEqual(first_evidence["file_count"], len(expected_files))
        self.assertEqual(first_evidence["byte_count"], expected_bytes)

    def test_safe_internal_relative_symlink_is_audited_without_following_it(self) -> None:
        root = self._assembled_payload()
        os.symlink("../../opt/taiji-agent/VERSION", root / "usr/bin/taiji-version")

        completed = self._verify(root)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        evidence = json.loads(completed.stdout)["payload_tree"]
        self.assertEqual(evidence["symlink_count"], 1)

    def test_absolute_symlink_is_rejected_anywhere_in_payload(self) -> None:
        root = self._assembled_payload()
        os.symlink("/etc/passwd", root / "absolute-link")

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("absolute symlink", completed.stderr)

    def test_payload_root_symlink_is_rejected_without_following_it(self) -> None:
        assembled = self._assembled_payload()
        root_link = self.temp_dir / "assembled-link"
        os.symlink(assembled, root_link)

        completed = self._verify(root_link)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("payload root must not be a symlink", completed.stderr)

    def test_escaping_symlink_is_rejected_anywhere_in_payload(self) -> None:
        root = self._assembled_payload()
        os.symlink("../outside", root / "escaping-link")

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("escaping symlink", completed.stderr)

    def test_dangling_symlink_is_rejected_anywhere_in_payload(self) -> None:
        root = self._assembled_payload()
        os.symlink("missing-target", root / "dangling-link")

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("dangling symlink", completed.stderr)

    def test_symlink_chain_is_rejected_instead_of_being_followed(self) -> None:
        root = self._assembled_payload()
        os.symlink("opt/taiji-agent/VERSION", root / "version-link")
        os.symlink("version-link", root / "chained-link")

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("another symlink component", completed.stderr)

    def test_fifo_is_rejected_anywhere_in_payload(self) -> None:
        root = self._assembled_payload()
        os.mkfifo(root / "runtime.fifo")

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("FIFO", completed.stderr)

    def test_socket_is_rejected_anywhere_in_payload(self) -> None:
        root = self._assembled_payload()
        runtime_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(runtime_socket.close)
        runtime_socket.bind(str(root / "s"))

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("socket", completed.stderr)

    def test_device_node_mode_is_rejected_by_the_recursive_auditor(self) -> None:
        verifier = load_verifier_module()
        self.assertTrue(
            hasattr(verifier, "verify_node_metadata"),
            "recursive verifier must expose one node metadata gate",
        )

        with self.assertRaisesRegex(verifier.PayloadContractError, "device"):
            verifier.verify_node_metadata(
                Path("device-node"),
                SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_nlink=1),
            )

    def test_world_writable_node_is_rejected_anywhere_in_payload(self) -> None:
        root = self._assembled_payload()
        target = self._write(root, "opt/taiji-agent/runtime/world-writable", "unsafe\n")
        target.chmod(0o666)

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("world-writable", completed.stderr)

    def test_setuid_node_is_rejected_anywhere_in_payload(self) -> None:
        root = self._assembled_payload()
        target = self._write(root, "opt/taiji-agent/runtime/setuid-tool", "unsafe\n", 0o755)
        target.chmod(target.stat().st_mode | stat.S_ISUID)

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("setuid", completed.stderr)

    def test_setgid_node_is_rejected_anywhere_in_payload(self) -> None:
        root = self._assembled_payload()
        target = self._write(root, "opt/taiji-agent/runtime/setgid-tool", "unsafe\n", 0o755)
        target.chmod(target.stat().st_mode | stat.S_ISGID)

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("setgid", completed.stderr)

    def test_regular_file_hardlink_is_rejected_anywhere_in_payload(self) -> None:
        root = self._assembled_payload()
        original = self._write(root, "opt/taiji-agent/runtime/original", "same inode\n")
        os.link(original, root / "opt/taiji-agent/runtime/hardlink")

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("hardlink", completed.stderr)

    def test_unreadable_regular_file_is_rejected_fail_closed(self) -> None:
        root = self._assembled_payload()
        target = self._write(root, "opt/taiji-agent/runtime/unreadable", "secret\n")
        self.addCleanup(lambda: target.chmod(0o644) if target.exists() else None)
        target.chmod(0o000)

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("cannot read payload regular file", completed.stderr)

    def test_lstat_failure_is_rejected_fail_closed(self) -> None:
        root = self._assembled_payload()
        verifier = load_verifier_module()

        with mock.patch.object(verifier.os, "lstat", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(verifier.PayloadContractError, "cannot lstat payload root"):
                verifier.audit_payload_tree(root)

    def test_missing_component_fails_closed(self) -> None:
        root = self._assembled_payload()
        (root / "opt/taiji-agent/runtime/node/bin/node").unlink()
        completed = self._verify(root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("missing component packaged_node", completed.stderr)

    def test_component_version_must_match_contract(self) -> None:
        root = self._assembled_payload()
        self._write(root, "opt/taiji-agent/runtime/web/PRODUCT_VERSION", "9.9.9\n")
        completed = self._verify(root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("webui_runtime version 9.9.9 does not match product version 0.1.0", completed.stderr)

    def test_contract_rejects_parent_path_traversal(self) -> None:
        root = self._assembled_payload()
        embedded = root / EMBEDDED_CONTRACT
        contract = json.loads(embedded.read_text(encoding="utf-8"))
        contract["components"][0]["path"] = "../outside"
        embedded.write_text(json.dumps(contract) + "\n", encoding="utf-8")
        completed = self._verify(root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unsafe relative path", completed.stderr)

    def test_embedded_contract_cannot_delete_a_required_component(self) -> None:
        root = self._assembled_payload()
        embedded = root / EMBEDDED_CONTRACT
        contract = json.loads(embedded.read_text(encoding="utf-8"))
        contract["components"] = [
            item for item in contract["components"] if item["id"] != "packaged_node"
        ]
        embedded.write_text(json.dumps(contract) + "\n", encoding="utf-8")

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("trusted payload contract", completed.stderr)

    def test_embedded_contract_cannot_weaken_a_required_component(self) -> None:
        root = self._assembled_payload()
        embedded = root / EMBEDDED_CONTRACT
        contract = json.loads(embedded.read_text(encoding="utf-8"))
        packaged_node = next(
            item for item in contract["components"] if item["id"] == "packaged_node"
        )
        packaged_node["mode"] = "0644"
        (root / packaged_node["path"]).chmod(0o644)
        embedded.write_text(json.dumps(contract) + "\n", encoding="utf-8")

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("trusted payload contract", completed.stderr)

    def test_required_component_cannot_be_symlink(self) -> None:
        root = self._assembled_payload()
        node = root / "opt/taiji-agent/runtime/node/bin/node"
        node.unlink()
        outside = self._write(self.temp_dir, "outside-node", "outside\n", 0o755)
        os.symlink(outside, node)
        completed = self._verify(root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("component packaged_node is a symlink", completed.stderr)

    def test_verifier_never_falls_back_to_source_checkout_contract(self) -> None:
        empty_root = self.temp_dir / "empty-assembled-root"
        empty_root.mkdir()
        completed = self._verify(empty_root, cwd=ROOT)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("embedded payload contract is missing", completed.stderr)

    def test_source_development_profile_marker_is_rejected_anywhere_in_payload(self) -> None:
        root = self._assembled_payload()
        self._write(root, "opt/taiji-agent/runtime/agent/leaked-profile.pyc", "source-development\n")

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("source-development", completed.stderr)

    def test_license_public_key_contract_pins_spki_fingerprint(self) -> None:
        contract = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
        component = next(
            item for item in contract["components"] if item["id"] == "license_public_key"
        )

        self.assertEqual(component["version"]["kind"], "spki_sha256")
        self.assertEqual(component["version"]["expected"], PUBLIC_KEY_FINGERPRINT)

    def test_tampered_license_public_key_fails_payload_verification(self) -> None:
        root = self._assembled_payload()
        self._write(
            root,
            "opt/taiji-agent/resources/license/signing-public.pem",
            "-----BEGIN PUBLIC KEY-----\nattacker\n-----END PUBLIC KEY-----\n",
        )

        completed = self._verify(root)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("license_public_key", completed.stderr)

    def test_build_and_release_checks_consume_embedded_contract(self) -> None:
        build = (ROOT / "packaging/linux/deb/build-deb.sh").read_text(encoding="utf-8")
        release = (ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh").read_text(encoding="utf-8")
        self.assertIn("payload-contract.json", build)
        self.assertIn("verify-payload.py", build)
        self.assertIn('--root "$PKG_ROOT"', build)
        self.assertIn("dpkg-deb -x", release)
        self.assertIn("verify-payload.py", release)
        self.assertIn('--root "$payload_root"', release)


if __name__ == "__main__":
    unittest.main()
