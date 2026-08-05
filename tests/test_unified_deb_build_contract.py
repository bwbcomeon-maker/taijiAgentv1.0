from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "packaging/linux/deb/build-deb.sh"
POLICY = ROOT / "packaging/linux/compatibility-policy.json"
POLICY_HELPER = ROOT / "packaging/linux/compatibility_policy.py"
PAYLOAD = ROOT / "packaging/linux/payload-contract.json"


def load_policy_helper():
    spec = importlib.util.spec_from_file_location("taiji_unified_deb_policy", POLICY_HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load policy helper: {POLICY_HELPER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UnifiedDebBuildContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = BUILD.read_text(encoding="utf-8")
        cls.payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
        cls.policy = json.loads(POLICY.read_text(encoding="utf-8"))
        cls.policy_helper = load_policy_helper()

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
        policy = self.policy
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
        self.assertIn("--print-shell", self.build)
        self.assertIn('TAIJI_PACKAGE_NAME=', self.build)
        self.assertIn('TAIJI_DEBIAN_DEPENDS=', self.build)
        self.assertIn('Package: $TAIJI_PACKAGE_NAME', self.build)
        self.assertIn('Architecture: $TAIJI_PACKAGE_ARCHITECTURE', self.build)
        self.assertIn('Maintainer: $TAIJI_PACKAGE_MAINTAINER', self.build)
        self.assertIn('Depends: $TAIJI_DEBIAN_DEPENDS', self.build)
        self.assertNotIn('Depends: $DEB_DEPENDS', self.build)

    def test_deb_embeds_exact_policy_and_abi_report(self) -> None:
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
        self.assertIn('POLICY_SHA256=', self.build)
        self.assertIn('compatibility_policy_sha256', self.build)
        self.assertIn('TAIJI_POLICY_SHA256', self.build)
        self.assertIn('render-preinst.py', self.build)
        self.assertIn('--policy "$POLICY_FILE"', self.build)
        self.assertIn('compatibility_policy_sha256": "$POLICY_SHA256"', self.build)
        self.assertIn('compatibility_policy_sha256', self.build)
        self.assertIn('policy_id', self.build)

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
