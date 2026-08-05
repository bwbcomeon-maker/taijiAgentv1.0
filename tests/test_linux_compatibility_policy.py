import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "packaging/linux/compatibility-policy.json"
MODULE_PATH = ROOT / "packaging/linux/compatibility_policy.py"


def load_module():
    spec = importlib.util.spec_from_file_location("taiji_compatibility_policy", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LinuxCompatibilityPolicyTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.policy = self.module.load_and_validate(POLICY_PATH)

    def write_policy(self, directory, policy):
        path = Path(directory) / "compatibility-policy.json"
        path.write_bytes(self.module.canonical_bytes(policy))
        return path

    def assert_rejected(self, path):
        with self.assertRaises(self.module.PolicyError):
            self.module.load_and_validate(path)

    def test_repository_policy_is_canonical_and_hash_stable(self):
        raw = POLICY_PATH.read_bytes()
        self.assertEqual(raw, self.module.canonical_bytes(self.policy))
        self.assertEqual(raw[-1:], b"\n")
        self.assertEqual(
            self.module.canonical_sha256(self.policy),
            self.module.canonical_sha256(self.module.load_and_validate(POLICY_PATH)),
        )

    def test_policy_rejects_duplicate_unknown_and_noncanonical_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            duplicate = temp_root / "duplicate.json"
            duplicate.write_text(
                '{"schema":"taiji-linux-compatibility-policy/v1",'
                '"schema":"taiji-linux-compatibility-policy/v1"}\n',
                encoding="utf-8",
            )
            self.assert_rejected(duplicate)

            unknown = copy.deepcopy(self.policy)
            unknown["unexpected"] = True
            self.assert_rejected(self.write_policy(temp_root, unknown))

            environment_override = copy.deepcopy(self.policy)
            environment_override["environment_overrides"] = {"TAIJI_PACKAGE_MAINTAINER": "other"}
            self.assert_rejected(self.write_policy(temp_root, environment_override))

            noncanonical = temp_root / "noncanonical.json"
            noncanonical.write_bytes(self.module.canonical_bytes(self.policy)[:-1] + b" \n")
            self.assert_rejected(noncanonical)

    def test_policy_fixes_product_identity_and_rejects_environment_override(self):
        package = self.policy["package"]
        self.assertEqual(package["name"], "taiji-agent")
        self.assertEqual(package["architecture"], "amd64")
        self.assertEqual(package["install_root"], "/opt/taiji-agent")
        self.assertEqual(
            package["maintainer"], "Taiji Agent Product Team <noreply@localhost>"
        )
        with mock.patch.dict(
            os.environ,
            {
                "TAIJI_PACKAGE_MAINTAINER": "attacker <attacker@example.test>",
                "TAIJI_TARGET_BASELINE_GLIBC": "9.99",
                "TAIJI_TARGET_BASELINE_KERNEL": "9.99.0",
            },
            clear=False,
        ):
            loaded = self.module.load_and_validate(POLICY_PATH)
            exports = self.module.shell_exports(loaded)
        self.assertEqual(loaded["package"], package)
        self.assertEqual(exports["TAIJI_PACKAGE_MAINTAINER"], package["maintainer"])
        with tempfile.TemporaryDirectory() as temp_dir:
            changed = copy.deepcopy(self.policy)
            changed["package"]["maintainer"] = "Other <other@example.test>"
            self.assert_rejected(self.write_policy(temp_dir, changed))

    def test_policy_supports_only_three_deb_amd64_families(self):
        self.assertEqual(self.policy["architecture"], {"uname_machine": ["x86_64"], "dpkg": ["amd64"]})
        self.assertEqual(
            self.policy["os_families"],
            [
                {"family": "kylin", "ids": ["kylin"]},
                {"family": "uos", "ids": ["uos"]},
                {"family": "openkylin", "ids": ["openkylin"]},
            ],
        )

    def test_private_library_set_is_disjoint_and_system_core_is_forbidden(self):
        elf = self.policy["elf"]
        private = set(elf["allowed_private_sonames"])
        required_system = set(elf["required_system_sonames"])
        forbidden = set(elf["forbidden_bundled_sonames"])
        self.assertFalse(private & required_system)
        self.assertFalse(private & forbidden)
        self.assertTrue(required_system <= forbidden)
        with tempfile.TemporaryDirectory() as temp_dir:
            changed = copy.deepcopy(self.policy)
            changed["elf"]["required_system_sonames"].append("libX11.so.6")
            self.assert_rejected(self.write_policy(temp_dir, changed))

    def test_debian_depends_contains_no_target_captured_versions(self):
        depends = self.policy["debian"]["depends"]
        self.assertEqual(depends, ["ca-certificates", "libc6 (>= 2.31)"])
        self.assertEqual(self.module.render_debian_depends(self.policy), ", ".join(depends))
        rendered = self.module.render_debian_depends(self.policy)
        self.assertNotIn("-0kylin", rendered)
        self.assertNotIn("TAIJI_TARGET_BASELINE", rendered)
        self.assertNotIn("TAIJI_PACKAGE_MAINTAINER", rendered)


if __name__ == "__main__":
    unittest.main()
