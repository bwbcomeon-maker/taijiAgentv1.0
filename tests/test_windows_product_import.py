"""Synthetic local contracts for Windows product-source bundle import."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "packaging/windows/import_product_source.py"
EXPECTED_PRODUCT_PATHS = [
    "apps/taiji-desktop/src/main.js",
    "apps/taiji-desktop/src/windows-runtime.js",
    "apps/taiji-desktop/tests/windows-runtime.test.js",
    "apps/taiji-desktop/tests/windows-startup-scope.test.js",
    "hermes-local-lab/config/taiji-default-config.yaml",
    "hermes-local-lab/sources/hermes-agent/taiji_runtime_profile.py",
    "hermes-local-lab/sources/hermes-agent/tests/test_taiji_runtime_profile.py",
    "hermes-local-lab/sources/hermes-webui/api/config.py",
    "hermes-local-lab/sources/hermes-webui/tests/test_ui_visibility_config.py",
    "packaging/windows/diagnose.ps1",
]


def run_git(cwd, *args, check=True):
    env = os.environ.copy()
    for name in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        env.pop(name, None)
    env.update({"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(cwd)] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False,
    )
    if check and result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return result


def load_helper(testcase):
    testcase.assertTrue(HELPER_PATH.is_file())
    spec = importlib.util.spec_from_file_location("taiji_product_import_contract", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_sidecar(bundle, sidecar):
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    sidecar.write_text("{}  {}\n".format(digest, bundle.name), encoding="utf-8")


def make_bundle_fixture(root):
    repo = Path(root) / "source.git"
    import_dir = Path(root) / "import-1"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.email", "test@example.invalid")
    run_git(repo, "config", "user.name", "Contract Test")
    allowed = repo / "apps/taiji-desktop/src/main.js"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("base\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "base")
    base = run_git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    run_git(repo, "checkout", "-b", "codex/windows-local")
    allowed.write_text("one\n", encoding="utf-8")
    run_git(repo, "commit", "-am", "first product change")
    second = repo / "packaging/windows/diagnose.ps1"
    second.parent.mkdir(parents=True, exist_ok=True)
    second.write_text("Write-Output ok\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "second product change")
    tip = run_git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    import_dir.mkdir(mode=0o700)
    bundle = import_dir / "windows-product-{}.bundle".format(tip)
    run_git(repo, "bundle", "create", str(bundle), "refs/heads/codex/windows-local")
    bundle.chmod(0o600)
    sidecar = import_dir / (bundle.name + ".sha256")
    write_sidecar(bundle, sidecar)
    sidecar.chmod(0o600)
    return repo, import_dir, base, tip


class WindowsProductImportTests(unittest.TestCase):
    def test_direct_help_from_non_repo_cwd_bootstraps_only_its_root(self):
        result = subprocess.run(
            ["/usr/bin/python3", "-I", "-B", str(HELPER_PATH), "--help"],
            cwd="/private/tmp", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"{probe,fetch,verify,install-ref,inventory}", result.stdout)

    def test_fixed_product_tip_uses_the_reviewed_ten_path_allowlist(self):
        helper = load_helper(self)
        self.assertEqual(helper.ALLOWED_PATHS, EXPECTED_PRODUCT_PATHS)

    def test_valid_bundle_writes_exact_manifest_and_inventory_order(self):
        helper = load_helper(self)
        with tempfile.TemporaryDirectory(prefix="taiji-product-import-") as temporary:
            repo, import_dir, base, tip = make_bundle_fixture(temporary)
            manifest = helper.verify_import(import_dir, base, tip)
            self.assertEqual(manifest["schema"], "taiji-windows-product-import/v1")
            self.assertEqual(manifest["tip_commit"], tip)
            self.assertEqual(manifest["allowed_paths"], helper.ALLOWED_PATHS)
            self.assertEqual(len(manifest["commits"]), 2)
            expected = run_git(repo, "rev-list", "--reverse", "--topo-order", "{}..{}".format(base, tip)).stdout.decode().split()
            self.assertEqual([item["old_sha"] for item in manifest["commits"]], expected)
            manifest_path = import_dir / "product-import.json"
            self.assertEqual(manifest_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), manifest)

    def test_sidecar_sha_and_basename_drift_are_rejected(self):
        helper = load_helper(self)
        with tempfile.TemporaryDirectory(prefix="taiji-product-import-") as temporary:
            _repo, import_dir, base, tip = make_bundle_fixture(temporary)
            sidecar = import_dir / ("windows-product-{}.bundle.sha256".format(tip))
            sidecar.write_text("0" * 64 + "  wrong.bundle\n", encoding="utf-8")
            with self.assertRaises(helper.PipelineError) as context:
                helper.verify_import(import_dir, base, tip)
            self.assertEqual(context.exception.category, "INPUT_VERIFICATION_FAILED")

    def test_base_and_tip_identity_are_exact(self):
        helper = load_helper(self)
        with tempfile.TemporaryDirectory(prefix="taiji-product-import-") as temporary:
            _repo, import_dir, base, tip = make_bundle_fixture(temporary)
            with self.assertRaises(helper.PipelineError):
                helper.verify_import(import_dir, tip, tip)
            with self.assertRaises(helper.PipelineError):
                helper.verify_import(import_dir, base, "f" * 40)

    def test_forbidden_path_is_rejected(self):
        helper = load_helper(self)
        with tempfile.TemporaryDirectory(prefix="taiji-product-import-") as temporary:
            repo, import_dir, base, _tip = make_bundle_fixture(temporary)
            forbidden = repo / "not-allowed.txt"
            forbidden.write_text("no\n", encoding="utf-8")
            run_git(repo, "add", forbidden.name)
            run_git(repo, "commit", "-m", "forbidden")
            tip = run_git(repo, "rev-parse", "HEAD").stdout.decode().strip()
            bundle = import_dir / "windows-product-{}.bundle".format(tip)
            run_git(repo, "bundle", "create", str(bundle), "refs/heads/codex/windows-local")
            bundle.chmod(0o600)
            write_sidecar(bundle, import_dir / (bundle.name + ".sha256"))
            (import_dir / (bundle.name + ".sha256")).chmod(0o600)
            with self.assertRaises(helper.PipelineError) as context:
                helper.verify_import(import_dir, base, tip)
            self.assertEqual(context.exception.category, "WINDOWS_PRODUCT_PATH_INVALID")

    def test_existing_staging_and_existing_manifest_never_overwrite(self):
        helper = load_helper(self)
        with tempfile.TemporaryDirectory(prefix="taiji-product-import-") as temporary:
            state = Path(temporary) / "state"
            first = helper._private_dir(state / "import-1", create=True)
            self.assertTrue(first.is_dir())
            with self.assertRaises(helper.PipelineError) as context:
                helper._private_dir(state / "import-1", create=True)
            self.assertEqual(context.exception.category, "IMPORT_STAGING_EXISTS")
            (first / "product-import.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(helper.PipelineError) as context:
                helper._write_private_json(first / "product-import.json", {"x": 1})
            self.assertEqual(context.exception.category, "IMPORT_MANIFEST_EXISTS")

    def test_archive_ref_install_is_idempotent_and_rejects_different_tip(self):
        helper = load_helper(self)
        with tempfile.TemporaryDirectory(prefix="taiji-product-import-") as temporary:
            repo, import_dir, base, tip = make_bundle_fixture(temporary)
            manifest = helper.verify_import(import_dir, base, tip)
            ref = "refs/archive/windows-product/{}".format(tip)
            self.assertEqual(helper.install_ref(import_dir / "product-import.json", repo, ref), tip)
            self.assertEqual(helper.install_ref(import_dir / "product-import.json", repo, ref), tip)
            run_git(repo, "update-ref", ref, base)
            with self.assertRaises(helper.PipelineError) as context:
                helper.install_ref(import_dir / "product-import.json", repo, ref)
            self.assertEqual(context.exception.category, "WINDOWS_PRODUCT_REF_EXISTS")
            self.assertEqual(manifest["tip_commit"], tip)

    def test_remote_argv_and_script_are_fixed_and_non_shell(self):
        helper = load_helper(self)
        target = {
            "host_alias": "windows-direct",
            "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "git": r"C:\Program Files\Git\cmd\git.exe",
            "remote_root": r"D:\tw\taiji-builds",
        }
        script = helper._encoded_product_bundle_script(
            r"D:\tw\source\taijiAgentv1.0",
            r"D:\tw\taiji-builds\tip\run\import\windows-product-tip.bundle",
            target["git"],
        )
        argv = helper.powershell_argv(target["host_alias"], target["powershell"], script)
        self.assertEqual(argv[0], "/usr/bin/ssh")
        self.assertNotIn("base..tip", script)
        self.assertIn("refs/heads/codex/windows-local", script)
        scp = helper._scp_argv("windows-direct", r"D:\tw\x.bundle", "/tmp/x.bundle")
        self.assertEqual(scp[0], "/usr/bin/scp")
        self.assertEqual(scp[-2], "windows-direct:D:/tw/x.bundle")
        self.assertNotIn("shell=True", script)

    def test_remote_sidecar_script_uses_lf_only(self):
        helper = load_helper(self)
        script = helper._encoded_product_bundle_script(
            r"D:\tw\source\taijiAgentv1.0",
            r"D:\tw\taiji-builds\tip\run\import\windows-product-tip.bundle",
            r"C:\Program Files\Git\cmd\git.exe",
        )
        self.assertIn("[char]10", script)
        self.assertNotIn("[Environment]::NewLine", script)

    def test_fetch_resumes_existing_staging_without_remote_rebuild(self):
        helper = load_helper(self)
        with tempfile.TemporaryDirectory(prefix="taiji-product-import-resume-") as temporary:
            state = Path(temporary)
            import_dir = state / "imports" / "resume-1"
            import_dir.mkdir(parents=True, mode=0o700)
            tip = "a" * 40
            bundle = import_dir / "windows-product-{}.bundle".format(tip)
            bundle.write_bytes(b"preserved bundle")
            bundle.chmod(0o644)
            calls = []

            def fake_run(argv, check=True):
                del check
                calls.append(list(argv))
                destination = Path(argv[-1])
                digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
                destination.write_text(
                    "{}  {}\n".format(digest, bundle.name), encoding="utf-8"
                )
                return subprocess.CompletedProcess(argv, 0, b"", b"")

            args = SimpleNamespace(
                target_config=str(ROOT / "packaging/pipeline/targets/windows-x64.json"),
                import_id="resume-1",
                host="windows-direct",
                product_repo=r"D:\tw\source\taijiAgentv1.0",
                base="b" * 40,
                tip=tip,
                state_root=str(state),
                ssh_config=None,
            )
            with mock.patch.object(helper, "_run", side_effect=fake_run):
                helper.fetch(args)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "/usr/bin/scp")
            self.assertEqual(calls[0][-2], "windows-direct:D:/tw/taiji-builds/{}/resume-1/import/{}.sha256".format(tip, bundle.name))
            self.assertEqual(bundle.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
