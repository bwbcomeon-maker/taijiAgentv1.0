from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "packaging/linux/deb/build-deb.sh"
POLICY = ROOT / "packaging/linux/compatibility-policy.json"
POLICY_HELPER = ROOT / "packaging/linux/compatibility_policy.py"
PAYLOAD = ROOT / "packaging/linux/payload-contract.json"
PREINST_RENDERER = ROOT / "packaging/linux/deb/render-preinst.py"
PREINST_TEMPLATE = ROOT / "packaging/linux/deb/preinst"
PAYLOAD_VERIFIER = ROOT / "packaging/linux/verify-payload.py"
LAUNCH_PROFILE = ROOT / "apps/taiji-desktop/src/launch-profile.js"


def load_module(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UnifiedDebBuildContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = BUILD.read_text(encoding="utf-8")
        cls.payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
        cls.policy = json.loads(POLICY.read_text(encoding="utf-8"))
        cls.policy_helper = load_module("taiji_unified_deb_policy", POLICY_HELPER)
        cls.payload_verifier = load_module("taiji_unified_deb_payload_verifier", PAYLOAD_VERIFIER)

    def _render_preinst(self, output: Path) -> str:
        output.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                sys.executable,
                str(PREINST_RENDERER),
                "--template",
                str(PREINST_TEMPLATE),
                "--policy",
                str(POLICY),
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return output.read_text(encoding="utf-8")

    @staticmethod
    def _shell_assignment(text: str, name: str) -> str:
        match = re.search(rf"^{re.escape(name)}=(.+)$", text, flags=re.MULTILINE)
        if match is None:
            raise AssertionError(f"missing shell assignment: {name}")
        tokens = shlex.split(match.group(1), posix=True)
        if len(tokens) != 1:
            raise AssertionError(f"invalid shell assignment: {name}")
        return tokens[0]

    def _policy_component(self, component_id: str) -> dict:
        return next(item for item in self.payload["components"] if item["id"] == component_id)

    def test_build_has_no_customer_specific_inputs(self) -> None:
        forbidden = (
            "TARGET_BASELINE",
            "target-baseline",
            "target_baseline",
            "TARGET_PROFILE",
            "TARGET_PROFILE_ID",
            "approved-maintainer",
            "APPROVED_MAINTAINER",
            "runtime-depends.txt",
            "render-depends",
            "max-age-days",
        )
        for marker in forbidden:
            self.assertNotIn(marker, self.build, marker)

        self.assertIn('POLICY_FILE="$REPO_ROOT/packaging/linux/compatibility-policy.json"', self.build)
        self.assertIn("compatibility_policy.py", self.build)
        self.assertNotIn("${TAIJI_TARGET", self.build)

    def test_control_identity_and_depends_come_only_from_policy(self) -> None:
        policy = self.policy_helper.load_and_validate(POLICY)
        self.assertEqual(
            policy["package"],
            {
                "name": "taiji-agent",
                "architecture": "amd64",
                "install_root": "/opt/taiji-agent",
                "maintainer": "Taiji Agent Product Team <noreply@localhost>",
            },
        )
        self.assertEqual(policy["debian"]["depends"], ["ca-certificates", "libc6 (>= 2.31)"])

        completed = subprocess.run(
            [
                sys.executable,
                str(POLICY_HELPER),
                "validate",
                "--policy",
                str(POLICY),
                "--print-shell",
            ],
            env={**os.environ, "TAIJI_PACKAGE_NAME": "customer-override"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        exports = {}
        for line in completed.stdout.splitlines():
            key, value = shlex.split(line, posix=True)[0].split("=", 1)
            exports[key] = value
        self.assertEqual(exports, self.policy_helper.shell_exports(policy))
        self.assertEqual(exports["TAIJI_PACKAGE_NAME"], policy["package"]["name"])
        self.assertEqual(exports["TAIJI_PACKAGE_ARCHITECTURE"], policy["package"]["architecture"])
        self.assertEqual(exports["TAIJI_PACKAGE_MAINTAINER"], policy["package"]["maintainer"])
        self.assertEqual(exports["TAIJI_DEBIAN_DEPENDS"], ", ".join(policy["debian"]["depends"]))
        self.assertIn("--print-shell", self.build)
        self.assertIn('TAIJI_PACKAGE_NAME=', self.build)
        self.assertIn('TAIJI_DEBIAN_DEPENDS=', self.build)
        self.assertIn('Package: $TAIJI_PACKAGE_NAME', self.build)
        self.assertIn('Architecture: $TAIJI_PACKAGE_ARCHITECTURE', self.build)
        self.assertIn('Maintainer: $TAIJI_PACKAGE_MAINTAINER', self.build)
        self.assertIn('Depends: $TAIJI_DEBIAN_DEPENDS', self.build)
        self.assertNotIn('Depends: $DEB_DEPENDS', self.build)

    def test_deb_embeds_exact_policy_and_abi_report(self) -> None:
        policy = self.policy_helper.load_and_validate(POLICY)
        policy_bytes = self.policy_helper.canonical_bytes(policy)
        policy_hash = self.policy_helper.canonical_sha256(policy)
        policy_component = self._policy_component("compatibility_policy")
        abi_component = self._policy_component("elf_abi_audit")
        with tempfile.TemporaryDirectory(prefix="taiji-unified-deb-resources-") as temp_dir:
            staged_root = Path(temp_dir)
            resources = staged_root / "opt/taiji-agent/resources"
            resources.mkdir(parents=True)
            staged_policy = resources / "linux-compatibility-policy.json"
            staged_policy.write_bytes(policy_bytes)
            abi_report = {
                "schema": "taiji-elf-abi-audit/v1",
                "policy_id": policy["policy_id"],
                "compatibility_policy_sha256": policy_hash,
                "max_required_versions": policy["elf"]["maximum_symbol_versions"],
                "external_sonames": [],
                "private_sonames": [],
                "files": [
                    {
                        "relative_path": "opt/taiji-agent/runtime/node/bin/node",
                        "sha256": "0" * 64,
                        "machine": "x86_64",
                        "needed": [],
                        "runpath": [],
                        "version_needs": {},
                    }
                ],
            }
            staged_abi = resources / "elf-abi-audit.json"
            staged_abi.write_text(
                json.dumps(abi_report, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(POLICY.read_bytes(), policy_bytes)
            self.assertEqual(staged_policy.read_bytes(), POLICY.read_bytes())
            self.assertEqual(
                self.payload_verifier.verify_compatibility_policy(
                    staged_policy, policy_component["version"], "compatibility_policy"
                ),
                policy_hash,
            )
            self.assertEqual(
                self.payload_verifier.verify_elf_abi_audit(
                    staged_root.resolve(),
                    staged_abi,
                    abi_component["version"],
                    "elf_abi_audit",
                ),
                policy_hash,
            )

            rendered = self._render_preinst(staged_root / "DEBIAN/preinst")
            self.assertEqual(self._shell_assignment(rendered, "TAIJI_POLICY_ID"), policy["policy_id"])
            self.assertEqual(self._shell_assignment(rendered, "TAIJI_POLICY_SHA256"), policy_hash)

            staged_policy.unlink()
            with self.assertRaises(self.payload_verifier.PayloadContractError):
                self.payload_verifier.verify_compatibility_policy(
                    staged_policy, policy_component["version"], "compatibility_policy"
                )
            staged_policy.write_bytes(policy_bytes)
            staged_abi.unlink()
            with self.assertRaises(self.payload_verifier.PayloadContractError):
                self.payload_verifier.verify_elf_abi_audit(
                    staged_root.resolve(),
                    staged_abi,
                    abi_component["version"],
                    "elf_abi_audit",
                )
            staged_abi.write_text(
                json.dumps({**abi_report, "schema": "taiji-elf-abi-audit/v0"}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.payload_verifier.PayloadContractError):
                self.payload_verifier.verify_elf_abi_audit(
                    staged_root.resolve(),
                    staged_abi,
                    abi_component["version"],
                    "elf_abi_audit",
                )

        for marker in (
            'POLICY_INSTALL_PATH="$INSTALL_ROOT/resources/linux-compatibility-policy.json"',
            'ABI_REPORT_PATH="$INSTALL_ROOT/resources/elf-abi-audit.json"',
            'stage-private-libraries.py',
            'audit-elf-closure.py',
            "cmp -s \"$POLICY_FILE\"",
            "cmp -s \"$ABI_BUILD_REPORT\"",
            "elf-abi-audit.json",
        ):
            self.assertIn(marker, self.build, marker)

        payload_paths = {
            item["path"] for item in self.payload["components"]
        }
        self.assertIn("opt/taiji-agent/resources/linux-compatibility-policy.json", payload_paths)
        self.assertIn("opt/taiji-agent/resources/elf-abi-audit.json", payload_paths)
        self.assertIn("opt/taiji-agent/runtime/lib", payload_paths)

    def test_build_host_glibc_cannot_exceed_policy_floor(self) -> None:
        self.assertIn("ldd --version", self.build)
        self.assertRegex(
            self.build,
            re.compile(r"dpkg --compare-versions \"\$build_glibc\" le \"\$TAIJI_GLIBC_MIN\""),
        )
        self.assertIn("TAIJI_GLIBC_MIN=", self.build)

    def test_preinst_and_manifest_bind_same_policy_hash(self) -> None:
        policy = self.policy_helper.load_and_validate(POLICY)
        policy_hash = self.policy_helper.canonical_sha256(policy)
        with tempfile.TemporaryDirectory(prefix="taiji-preinst-manifest-") as temp_dir:
            preinst = self._render_preinst(Path(temp_dir) / "DEBIAN/preinst")
            manifest = {
                "schema": "taiji-package-manifest/v3",
                "package": policy["package"]["name"],
                "version": "1.0.0",
                "architecture": policy["package"]["architecture"],
                "source_commit": "0123456789abcdef0123456789abcdef01234567",
                "deb_basename": "taiji-agent_1.0.0_amd64.deb",
                "deb_sha256": "0" * 64,
                "maintainer": policy["package"]["maintainer"],
                "compatibility_policy_id": policy["policy_id"],
                "compatibility_policy_sha256": policy_hash,
                "elf_abi_audit_basename": "elf-abi-audit.json",
                "elf_abi_audit_sha256": "1" * 64,
                "electron_executable_sha256": "2" * 64,
                "desktop_entry_sha256": "3" * 64,
                "built_at_utc": "2026-01-01T00:00:00Z",
            }
            self.assertEqual(
                self._shell_assignment(preinst, "TAIJI_POLICY_ID"),
                manifest["compatibility_policy_id"],
            )
            self.assertEqual(
                self._shell_assignment(preinst, "TAIJI_POLICY_SHA256"),
                manifest["compatibility_policy_sha256"],
            )
            self.assertEqual(manifest["compatibility_policy_sha256"], policy_hash)
            changed_manifest = {**manifest, "compatibility_policy_sha256": "f" * 64}
            self.assertNotEqual(
                changed_manifest["compatibility_policy_sha256"],
                self._shell_assignment(preinst, "TAIJI_POLICY_SHA256"),
            )
            self.assertNotEqual(changed_manifest["compatibility_policy_sha256"], policy_hash)

        self.assertIn('POLICY_SHA256=', self.build)
        self.assertIn('compatibility_policy_sha256', self.build)
        self.assertIn('TAIJI_POLICY_SHA256', self.build)
        self.assertIn('render-preinst.py', self.build)
        self.assertIn('--policy "$POLICY_FILE"', self.build)
        self.assertIn('compatibility_policy_sha256": "$POLICY_SHA256"', self.build)
        self.assertIn('compatibility_policy_sha256', self.build)
        self.assertIn('policy_id', self.build)

    def test_launch_manifest_fixture_matches_launch_profile_contract(self) -> None:
        manifest_component = self._policy_component("launch_manifest")
        self.assertEqual(
            manifest_component["path"],
            "opt/taiji-agent/resources/taiji-release-manifest.json",
        )
        self.assertIn('LAUNCH_MANIFEST_PATH="$INSTALL_ROOT/resources/taiji-release-manifest.json"', self.build)
        self.assertIn("write_launch_manifest", self.build)
        self.assertIn('"./opt/taiji-agent/resources/taiji-release-manifest.json"', self.build)

        node_script = r'''
const fs = require("node:fs");
const path = require("node:path");
const { resolveLaunchProfile } = require(process.argv[1]);
const installRoot = process.argv[2];
const appPath = path.join(installRoot, "apps", "taiji-desktop");
try {
  const profile = resolveLaunchProfile({
    env: {},
    appPath,
    platform: "linux",
    arch: "x64",
    installRoot,
    expectedManifestUid: typeof process.getuid === "function" ? process.getuid() : 0,
  });
  process.stdout.write(JSON.stringify(profile.release));
} catch (error) {
  process.stderr.write(String(error && error.message || error));
  process.exit(1);
}
'''
        with tempfile.TemporaryDirectory(prefix="taiji-launch-manifest-") as temp_dir:
            install_root = Path(temp_dir).resolve()
            app_path = install_root / "apps/taiji-desktop"
            python_path = install_root / "runtime/agent/venv/bin/python"
            resources = install_root / "resources"
            app_path.mkdir(parents=True)
            resources.mkdir(parents=True)
            python_path.parent.mkdir(parents=True)
            (install_root / "runtime/web").mkdir(parents=True)
            python_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            python_path.chmod(0o755)
            manifest_path = resources / "taiji-release-manifest.json"
            manifest = {
                "schema": "taiji-release-manifest/v1",
                "platform": "linux",
                "arch": "amd64",
                "version": "1.0.0",
                "commit": "0123456789abcdef0123456789abcdef01234567",
                "installRoot": str(install_root),
            }
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            manifest_path.chmod(0o644)

            def run_launch_profile() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["node", "-e", node_script, str(LAUNCH_PROFILE), str(install_root)],
                    capture_output=True,
                    text=True,
                    check=False,
                )

            completed = run_launch_profile()
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), {
                "version": manifest["version"],
                "commit": manifest["commit"],
            })
            manifest_path.unlink()
            missing = run_launch_profile()
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("manifest", missing.stderr.lower())
            manifest_path.write_text(
                json.dumps({**manifest, "schema": "taiji-release-manifest/v0"}) + "\n",
                encoding="utf-8",
            )
            manifest_path.chmod(0o644)
            wrong_schema = run_launch_profile()
            self.assertNotEqual(wrong_schema.returncode, 0)
            self.assertIn("schema", wrong_schema.stderr.lower())

    def test_deb_never_embeds_certification_or_publication_evidence(self) -> None:
        for marker in (
            "certification-set",
            "certification_set",
            "release-evidence",
            "publication-receipt",
            "publication_receipt",
            "target-baseline",
            "target_baseline",
        ):
            self.assertNotIn(marker, self.build, marker)


if __name__ == "__main__":
    unittest.main()
