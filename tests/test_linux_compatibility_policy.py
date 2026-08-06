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

    def test_policy_helper_defers_annotations_for_kylin_python38(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        header = "\n".join(source.splitlines()[:12])
        self.assertIn("from __future__ import annotations", header)

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

    def test_non_glibc_runtime_boundary_is_explicitly_required(self):
        elf = self.policy["elf"]
        required_system = set(elf["required_system_sonames"])
        forbidden = set(elf["forbidden_bundled_sonames"])
        non_glibc_runtime = {
            "libgcc_s.so.1",
            "libstdc++.so.6",
            "libz.so.1",
            "libcrypt.so.1",
        }
        self.assertTrue(non_glibc_runtime <= required_system)
        self.assertTrue(non_glibc_runtime <= forbidden)

    def test_electron_distribution_is_pinned_by_path_soname_hash_and_literal(self):
        self.assertIn("electron_distribution", self.policy["elf"])
        distribution = self.policy["elf"]["electron_distribution"]
        self.assertEqual(distribution["version"], "39.8.10")
        self.assertEqual(
            distribution["archive_sha256"],
            "92e8b031fa5327c78a972279fd75fc8503fcd1773401809f4557e4de583eabd1",
        )
        expected_sonames = {
            "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron": None,
            "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/chrome-sandbox": None,
            "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/chrome_crashpad_handler": None,
            "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/libEGL.so": "libEGL.so",
            "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/libGLESv2.so": "libGLESv2.so",
            "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/libffmpeg.so": "libffmpeg.so",
            "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/libvulkan.so.1": "libvulkan.so.1",
            "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/libvk_swiftshader.so": "libvk_swiftshader.so",
        }
        files = distribution["elf_files"]
        self.assertEqual(
            {path: descriptor["soname"] for path, descriptor in files.items()},
            expected_sonames,
        )
        self.assertEqual(
            files[
                "opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron"
            ]["allowed_host_path_literals"],
            [
                "/home/privacy/",
                "/tmp/__v8_gc__",
                "/tmp/foo.js",
                "/tmp/node-repl-sock",
                "/tmp/perfetto-consumer",
                "/tmp/perfetto-producer",
                "/workspace/workspace.js",
            ],
        )
        for descriptor in files.values():
            self.assertEqual(
                set(descriptor),
                {"soname", "sha256", "allowed_host_path_literals"},
            )
            self.assertRegex(descriptor["sha256"], r"^[0-9a-f]{64}$")

        with tempfile.TemporaryDirectory() as temp_dir:
            changed = copy.deepcopy(self.policy)
            changed["elf"]["electron_distribution"]["version"] = "39.8.11"
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
