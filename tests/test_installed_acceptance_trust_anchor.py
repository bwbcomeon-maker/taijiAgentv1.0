import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "packaging/linux/acceptance_runner.py"
HELPER = ROOT / "packaging/linux/acceptance_tools_manifest.py"
ENTRYPOINT = ROOT / "packaging/linux/bin/taiji-agent-acceptance"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def canonical_json(payload):
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


class InstalledAcceptanceTrustAnchorTests(unittest.TestCase):
    def setUp(self):
        self.runner = load_module("acceptance_runner", RUNNER)
        self.helper = load_module("acceptance_tools_manifest_fixture", HELPER)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.install_root = self.root / "opt/taiji-agent"
        self.resources = self.install_root / "resources"
        self.code_root = self.install_root / "libexec/target-acceptance"
        self.tools_root = self.code_root / "验收工具"
        self.entrypoint = self.root / "usr/bin/taiji-agent-acceptance"
        self.delivery = self.root / "delivery"
        self.package_dir = self.delivery / "生成的安装包"
        self.source_commit = "a" * 40
        self.version = "1.0.3"
        self._build_fixture()
        self.runner.TRUSTED_OWNER_UID = os.getuid()
        self.runner.INSTALL_ROOT = self.install_root
        self.runner.RELEASE_MANIFEST_PATH = self.resources / "taiji-release-manifest.json"
        self.runner.BINDING_PATH = self.resources / "taiji-acceptance-binding.json"
        self.runner.CODE_ROOT = self.code_root
        self.runner.TOOLS_ROOT = self.tools_root
        self.runner.LAUNCHER_PATH = self.code_root / self.helper.CANONICAL_LAUNCHER["delivery_basename"]
        self.runner.HELPER_PATH = self.code_root / "acceptance_tools_manifest.py"
        self.runner.RUNNER_PATH = self.code_root / "acceptance-runner.py"
        self.runner.ENTRYPOINT_PATH = self.entrypoint

    def tearDown(self):
        self.temporary.cleanup()

    def _write(self, path, payload, mode=0o644):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        path.chmod(mode)

    def _build_fixture(self):
        source = self.root / "source"
        for entry in self.helper.CANONICAL_FILES:
            item = source / entry["source_path"]
            self._write(
                item,
                (entry["delivery_path"] + "\n").encode("utf-8"),
                entry.get("source_mode", entry["mode"]),
            )
        source_launcher = source / self.helper.CANONICAL_LAUNCHER["source_path"]
        self._write(source_launcher, b"#!/usr/bin/env bash\nexit 0\n", 0o755)
        manifest = self.helper.create_manifest(source, self.source_commit)

        self.tools_root.mkdir(parents=True)
        self.tools_root.chmod(0o755)
        for entry in self.helper.CANONICAL_FILES:
            self._write(
                self.tools_root / entry["delivery_path"],
                (source / entry["source_path"]).read_bytes(),
                entry["mode"],
            )
        self._write(
            self.code_root / self.helper.CANONICAL_LAUNCHER["delivery_basename"],
            source_launcher.read_bytes(),
            0o755,
        )
        self.helper.write_manifest_exclusive(
            self.tools_root / self.helper.MANIFEST_BASENAME,
            manifest,
        )
        self._write(self.code_root / "acceptance_tools_manifest.py", HELPER.read_bytes())
        self._write(self.code_root / "acceptance-runner.py", RUNNER.read_bytes())
        self._write(self.entrypoint, ENTRYPOINT.read_bytes(), 0o755)

        release = {
            "arch": "amd64",
            "commit": self.source_commit,
            "installRoot": "/opt/taiji-agent",
            "platform": "linux",
            "schema": "taiji-release-manifest/v1",
            "version": self.version,
        }
        release_raw = canonical_json(release)
        self._write(self.resources / "taiji-release-manifest.json", release_raw)
        tools_manifest_raw = (self.tools_root / self.helper.MANIFEST_BASENAME).read_bytes()
        binding = {
            "acceptance_tools_manifest_path": "/opt/taiji-agent/libexec/target-acceptance/验收工具/acceptance-tools-manifest.json",
            "acceptance_tools_manifest_sha256": hashlib.sha256(tools_manifest_raw).hexdigest(),
            "entrypoint_path": "/usr/bin/taiji-agent-acceptance",
            "entrypoint_sha256": hashlib.sha256(self.entrypoint.read_bytes()).hexdigest(),
            "helper_path": "/opt/taiji-agent/libexec/target-acceptance/acceptance_tools_manifest.py",
            "helper_sha256": hashlib.sha256((self.code_root / "acceptance_tools_manifest.py").read_bytes()).hexdigest(),
            "launcher_path": "/opt/taiji-agent/libexec/target-acceptance/04_目标终端_桌面App验收并导出证据.sh",
            "launcher_sha256": hashlib.sha256((self.code_root / self.helper.CANONICAL_LAUNCHER["delivery_basename"]).read_bytes()).hexdigest(),
            "release_manifest_sha256": hashlib.sha256(release_raw).hexdigest(),
            "runner_path": "/opt/taiji-agent/libexec/target-acceptance/acceptance-runner.py",
            "runner_sha256": hashlib.sha256((self.code_root / "acceptance-runner.py").read_bytes()).hexdigest(),
            "schema": "taiji-installed-acceptance-binding/v1",
            "source_commit": self.source_commit,
            "version": self.version,
        }
        binding_raw = canonical_json(binding)
        self._write(self.resources / "taiji-acceptance-binding.json", binding_raw)

        self.package_dir.mkdir(parents=True)
        self.package_dir.chmod(0o755)
        package_manifest = {
            "acceptance_binding_sha256": hashlib.sha256(binding_raw).hexdigest(),
            "acceptance_entrypoint_sha256": binding["entrypoint_sha256"],
            "acceptance_tools_manifest_sha256": binding["acceptance_tools_manifest_sha256"],
            "installed_release_manifest_sha256": binding["release_manifest_sha256"],
            "schema": "taiji-package-manifest/v3",
            "source_commit": self.source_commit,
            "version": self.version,
        }
        self._write(
            self.package_dir / "taiji-package-manifest.json",
            canonical_json(package_manifest),
        )

    def test_valid_root_owned_binding_verifies_installed_code_and_external_data(self):
        result = self.runner.verify_installed_acceptance(self.delivery)
        self.assertEqual(result["source_commit"], self.source_commit)
        self.assertEqual(result["version"], self.version)
        self.assertEqual(result["delivery_dir"], str(self.delivery))
        self.assertEqual(result["launcher_path"], str(self.runner.LAUNCHER_PATH))

    def test_external_self_consistent_tools_are_never_an_execution_source(self):
        external_tools = self.delivery / "验收工具"
        external_tools.mkdir()
        self._write(external_tools / "run-installed-electron-acceptance.js", b"malicious\n")
        result = self.runner.verify_installed_acceptance(self.delivery)
        self.assertEqual(result["launcher_path"], str(self.runner.LAUNCHER_PATH))
        self.assertNotIn(str(external_tools), result["launcher_path"])

    def test_external_manifest_must_match_the_root_owned_binding(self):
        manifest_path = self.package_dir / "taiji-package-manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["acceptance_tools_manifest_sha256"] = "b" * 64
        manifest_path.write_bytes(canonical_json(payload))
        with self.assertRaisesRegex(self.runner.AcceptanceRunnerError, "manifest|binding|digest"):
            self.runner.verify_installed_acceptance(self.delivery)

    def test_binding_duplicate_key_symlink_or_modified_installed_tool_is_rejected(self):
        binding_path = self.resources / "taiji-acceptance-binding.json"
        original = binding_path.read_bytes()
        binding_path.write_bytes(b'{"schema":"x","schema":"y"}\n')
        with self.assertRaisesRegex(self.runner.AcceptanceRunnerError, "duplicate|binding"):
            self.runner.verify_installed_acceptance(self.delivery)
        binding_path.write_bytes(original)
        binding_path.unlink()
        target = self.resources / "binding-target.json"
        self._write(target, original)
        binding_path.symlink_to(target)
        with self.assertRaisesRegex(self.runner.AcceptanceRunnerError, "binding|symlink|safely"):
            self.runner.verify_installed_acceptance(self.delivery)

    def test_delivery_dir_must_be_absolute_real_owned_and_not_writable_by_others(self):
        with self.assertRaisesRegex(self.runner.AcceptanceRunnerError, "absolute"):
            self.runner.verify_installed_acceptance(Path("relative"))
        lexical_parent = self.delivery.parent / "unused" / ".." / self.delivery.name
        with self.assertRaisesRegex(self.runner.AcceptanceRunnerError, r"canonical|\.\."):
            self.runner.verify_installed_acceptance(lexical_parent)
        self.delivery.chmod(0o777)
        with self.assertRaisesRegex(self.runner.AcceptanceRunnerError, "delivery|writable|trusted"):
            self.runner.verify_installed_acceptance(self.delivery)

    def test_environment_is_allowlisted_and_all_code_paths_are_fixed_installed_paths(self):
        args = self.runner.AcceptanceArguments(
            delivery_dir=self.delivery,
            customer_dir=self.root / "customer",
            install_observation=self.root / "install.json",
            method_attestation=self.root / "attestation.json",
            installer_screenshot=self.root / "installer.png",
            category_id="kylin-v10-sp1-2403-ukui",
            challenge="c" * 64,
            environment_observation=self.root / "environment.json",
            target_dir=self.root / "target",
            timeout_ms=900000,
        )
        ambient = {
            "HOME": str(self.root / "home"),
            "USER": "operator",
            "LOGNAME": "operator",
            "DISPLAY": ":0",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "XDG_SESSION_TYPE": "wayland",
            "XDG_SESSION_DESKTOP": "ukui",
            "XDG_CONFIG_HOME": str(self.root / "home/.config-custom"),
            "XDG_DATA_HOME": str(self.root / "home/.local/share-custom"),
            "XDG_CACHE_HOME": str(self.root / "home/.cache-custom"),
            "XDG_STATE_HOME": str(self.root / "home/.local/state-custom"),
            "LANGUAGE": "zh_CN:zh",
            "LC_MESSAGES": "zh_CN.UTF-8",
            "PYTHONPATH": "/tmp/evil",
            "NODE_OPTIONS": "--require=/tmp/evil.js",
            "LD_PRELOAD": "/tmp/evil.so",
            "BASH_ENV": "/tmp/evil.sh",
            "TAIJI_TARGET_DELIVERY_DIR": "/tmp/evil-delivery",
        }
        env = self.runner.build_acceptance_environment(args, ambient)
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertEqual(env["TAIJI_TARGET_DELIVERY_DIR"], str(self.delivery))
        self.assertEqual(env["XDG_SESSION_TYPE"], "wayland")
        self.assertEqual(env["XDG_CONFIG_HOME"], ambient["XDG_CONFIG_HOME"])
        self.assertEqual(env["LANGUAGE"], "zh_CN:zh")
        self.assertEqual(env["LC_MESSAGES"], "zh_CN.UTF-8")
        for forbidden in ("PYTHONPATH", "NODE_OPTIONS", "LD_PRELOAD", "BASH_ENV"):
            self.assertNotIn(forbidden, env)

    def test_entrypoint_uses_only_fixed_installed_python_runner_and_clean_environment(self):
        text = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("/usr/bin/env -i", text)
        self.assertIn("/opt/taiji-agent/runtime/agent/venv/bin/python", text)
        self.assertIn("/opt/taiji-agent/libexec/target-acceptance/acceptance-runner.py", text)
        self.assertIn("-I -B", text)
        self.assertNotIn("$PATH", text)
        self.assertNotIn("PYTHONPATH", text)
        for preserved in (
            "XDG_SESSION_TYPE",
            "XDG_SESSION_DESKTOP",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
            "LANGUAGE",
            "LC_MESSAGES",
        ):
            self.assertIn(preserved, text)

    def test_timeout_contract_matches_the_installed_electron_driver(self):
        base = dict(
            delivery_dir=self.delivery,
            customer_dir=self.root / "customer",
            install_observation=self.root / "install.json",
            method_attestation=self.root / "attestation.json",
            installer_screenshot=self.root / "installer.png",
            category_id="kylin-v10-sp1-2403-ukui",
            challenge="c" * 64,
            environment_observation=self.root / "environment.json",
            target_dir=self.root / "target",
        )
        for timeout in (29999, 1800001):
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(self.runner.AcceptanceRunnerError, "timeout"):
                    self.runner.build_acceptance_environment(
                        self.runner.AcceptanceArguments(timeout_ms=timeout, **base),
                        {},
                    )
        for timeout in (30000, 1800000):
            with self.subTest(timeout=timeout):
                env = self.runner.build_acceptance_environment(
                    self.runner.AcceptanceArguments(timeout_ms=timeout, **base),
                    {},
                )
                self.assertEqual(env["TAIJI_TARGET_ACCEPTANCE_TIMEOUT_MS"], str(timeout))

    def test_each_installed_executable_node_is_digest_and_mode_bound(self):
        for path in (
            self.entrypoint,
            self.code_root / "acceptance-runner.py",
            self.code_root / "acceptance_tools_manifest.py",
            self.code_root / self.helper.CANONICAL_LAUNCHER["delivery_basename"],
        ):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                original_mode = path.stat().st_mode & 0o777
                path.write_bytes(original + b"# changed\n")
                path.chmod(original_mode)
                with self.assertRaisesRegex(
                    self.runner.AcceptanceRunnerError,
                    "digest|binding",
                ):
                    self.runner.verify_installed_acceptance(self.delivery)
                path.write_bytes(original)
                path.chmod(original_mode)

    def test_main_executes_only_fixed_bash_and_installed_launcher(self):
        observed = {}
        self.runner.verify_installed_acceptance = lambda path: {"delivery_dir": str(path)}

        def fake_execve(path, argv, environment):
            observed.update(path=path, argv=argv, environment=environment)
            raise RuntimeError("exec captured")

        self.runner.os.execve = fake_execve
        argv = [
            "--delivery-dir", str(self.delivery),
            "--customer-dir", str(self.root / "customer"),
            "--install-observation", str(self.root / "install.json"),
            "--method-attestation", str(self.root / "attestation.json"),
            "--installer-screenshot", str(self.root / "installer.png"),
            "--category-id", "kylin-v10-sp1-2403-ukui",
            "--challenge", "c" * 64,
            "--environment-observation", str(self.root / "environment.json"),
            "--target-dir", str(self.root / "target"),
        ]
        with self.assertRaisesRegex(RuntimeError, "exec captured"):
            self.runner._main(argv)
        self.assertEqual(observed["path"], "/bin/bash")
        self.assertEqual(
            observed["argv"],
            ["/bin/bash", str(self.runner.LAUNCHER_PATH)],
        )
        self.assertEqual(
            observed["environment"]["TAIJI_TARGET_DELIVERY_DIR"],
            str(self.delivery),
        )


if __name__ == "__main__":
    unittest.main()
