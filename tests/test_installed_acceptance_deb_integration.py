from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_DEB = ROOT / "packaging/linux/deb/build-deb.sh"
PAYLOAD_CONTRACT = ROOT / "packaging/linux/payload-contract.json"
PAYLOAD_VERIFIER = ROOT / "packaging/linux/verify-payload.py"
BUILDER = ROOT / "taijiagent 打包交付/00_制包机_生成离线交付包.sh"
PREFLIGHT = ROOT / "taijiagent 打包交付/01_制包机_发布预检.sh"
RELEASE_VALIDATOR = ROOT / "scripts/validate-taiji-release-evidence.py"


ACCEPTANCE_HASH_FIELDS = {
    "acceptance_binding_sha256",
    "acceptance_tools_manifest_sha256",
    "acceptance_entrypoint_sha256",
    "installed_release_manifest_sha256",
}

ACCEPTANCE_PAYLOAD_PATHS = {
    "usr/bin/taiji-agent-acceptance",
    "opt/taiji-agent/resources/taiji-acceptance-binding.json",
    "opt/taiji-agent/libexec/target-acceptance/acceptance-runner.py",
    "opt/taiji-agent/libexec/target-acceptance/acceptance_tools_manifest.py",
    "opt/taiji-agent/libexec/target-acceptance/04_目标终端_桌面App验收并导出证据.sh",
    "opt/taiji-agent/libexec/target-acceptance/验收工具/acceptance-tools-manifest.json",
    "opt/taiji-agent/libexec/target-acceptance/验收工具/run-installed-electron-acceptance.js",
    "opt/taiji-agent/libexec/target-acceptance/验收工具/assemble-target-evidence.py",
    "opt/taiji-agent/libexec/target-acceptance/验收工具/observe-single-deb-install.py",
    "opt/taiji-agent/libexec/target-acceptance/验收工具/certification-matrix.json",
    "opt/taiji-agent/libexec/target-acceptance/验收工具/validate-taiji-release-evidence.py",
    "opt/taiji-agent/libexec/target-acceptance/验收工具/taiji-challenge-envelope.py",
    "opt/taiji-agent/libexec/target-acceptance/验收工具/signing-public.pem",
}


class InstalledAcceptanceDebIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = BUILD_DEB.read_text(encoding="utf-8")
        cls.builder = BUILDER.read_text(encoding="utf-8")
        cls.preflight = PREFLIGHT.read_text(encoding="utf-8")
        cls.validator = RELEASE_VALIDATOR.read_text(encoding="utf-8")
        cls.contract = json.loads(PAYLOAD_CONTRACT.read_text(encoding="utf-8"))
        cls.verifier = PAYLOAD_VERIFIER.read_text(encoding="utf-8")

    def test_build_deb_stages_the_installed_acceptance_trust_chain(self) -> None:
        required_tokens = {
            'ACCEPTANCE_ROOT="$INSTALL_ROOT/libexec/target-acceptance"',
            'ACCEPTANCE_TOOLS_ROOT="$ACCEPTANCE_ROOT/验收工具"',
            'ACCEPTANCE_BINDING_PATH="$INSTALL_ROOT/resources/taiji-acceptance-binding.json"',
            'install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent-acceptance" "$PKG_ROOT/usr/bin/taiji-agent-acceptance"',
            'install -m 0644 "$REPO_ROOT/packaging/linux/acceptance_runner.py" "$ACCEPTANCE_ROOT/acceptance-runner.py"',
            'install -m 0644 "$REPO_ROOT/packaging/linux/acceptance_tools_manifest.py" "$ACCEPTANCE_ROOT/acceptance_tools_manifest.py"',
            'install -m 0755 "$REPO_ROOT/taijiagent 打包交付/04_目标终端_桌面App验收并导出证据.sh" "$ACCEPTANCE_ROOT/04_目标终端_桌面App验收并导出证据.sh"',
            'stage_installed_acceptance_toolchain',
            'taiji-installed-acceptance-binding/v1',
        }
        for token in required_tokens:
            self.assertIn(token, self.build, token)
        self.assertLess(
            self.build.index("write_launch_manifest"),
            self.build.rindex("stage_installed_acceptance_toolchain"),
        )

    def test_release_manifest_is_written_as_canonical_json(self) -> None:
        function = self.build.split("write_launch_manifest() {", 1)[1].split(
            "stage_installed_acceptance_toolchain() {", 1
        )[0]
        self.assertIn("sort_keys=True", function)
        self.assertIn('separators=(",", ":")', function)
        self.assertIn('+ "\\n"', function)
        self.assertNotIn('cat > "$LAUNCH_MANIFEST_PATH"', function)

    def test_package_manifest_and_release_gates_bind_acceptance_hashes(self) -> None:
        for field in ACCEPTANCE_HASH_FIELDS:
            manifest_fragment = f'"{field}": "$'
            self.assertIn(manifest_fragment, self.build, field)
            self.assertGreaterEqual(self.builder.count(field), 2, field)
            self.assertIn(field, self.preflight, field)
            self.assertIn(field, self.validator, field)

    def test_payload_contract_and_deb_audit_cover_the_acceptance_chain(self) -> None:
        paths = {component["path"] for component in self.contract["components"]}
        self.assertTrue(
            ACCEPTANCE_PAYLOAD_PATHS.issubset(paths),
            sorted(ACCEPTANCE_PAYLOAD_PATHS - paths),
        )
        for path in ACCEPTANCE_PAYLOAD_PATHS:
            self.assertIn(f'"./{path}"', self.build, path)

        canonical = json.dumps(
            self.contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        expected = hashlib.sha256(canonical).hexdigest()
        self.assertIn(f'TRUSTED_CONTRACT_SHA256 = "{expected}"', self.verifier)

    def test_privacy_scan_keeps_only_audited_compatibility_markers(self) -> None:
        self.assertIn("scan_acceptance_privacy_compatibility", self.build)
        self.assertIn(
            'scan_acceptance_privacy_compatibility "$acceptance_root"',
            self.build,
        )
        for marker in (
            "HERMES_HOME",
            "HERMES_CONFIG_PATH",
            "HERMES_CONFIG",
            "HERMES_ENV",
            "HERMES_WEBUI_AGENT_DIR",
            "HERMES_WEBUI_PYTHON",
            "taiji-agentv1.0/hermes-local-lab/sources/hermes-agent/uv.lock",
        ):
            self.assertIn(marker, self.build)


if __name__ == "__main__":
    unittest.main()
