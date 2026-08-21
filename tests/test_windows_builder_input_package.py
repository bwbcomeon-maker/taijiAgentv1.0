"""RED/GREEN contract tests for Windows builder-input freezing."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = REPO_ROOT / "packaging/windows/builder_input_package.py"
SAFE_TAR_PATH = REPO_ROOT / "packaging/windows/safe_tar.py"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"
WINDOWS_TARGET_PATH = REPO_ROOT / "packaging/pipeline/targets/windows-x64.json"
ASSET_PROVENANCE_PATH = REPO_ROOT / "packaging/windows/asset-provenance.json"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def run_checkout_bound_help(path: Path) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="taiji-help-cwd-") as temporary:
        return subprocess.run(
            ["/usr/bin/python3", "-I", "-B", str(path.resolve()), "--help"],
            cwd=temporary,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )


def canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def load_helper():
    spec = importlib.util.spec_from_file_location("taiji_windows_builder_input_test", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load Windows builder-input helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/usr/bin/git", "-C", str(repo)] + list(args),
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def make_repo(root: Path, *, version: str = "1.2.3") -> tuple[Path, str, Path, Path]:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Codex Tester")
    git(repo, "config", "user.email", "codex@example.com")
    write_text(
        repo / ".gitignore",
        "/taijiagent-windows-builder-input-*.tar.gz\n"
        "/taijiagent-windows-builder-input-*.manifest.json\n"
        "/taijiagent-windows-builder-input-*.tar.gz.sha256\n",
    )
    write_text(repo / "VERSION", version + "\n")
    write_text(
        repo / "apps/taiji-desktop/package.json",
        json.dumps({"name": "taiji-desktop", "version": version}, ensure_ascii=False, separators=(",", ":")) + "\n",
    )
    write_text(
        repo / "packaging/windows/safe_tar.py",
        "def placeholder():\n    return 'fixture-safe-tar'\n",
    )
    target_payload = json.loads(WINDOWS_TARGET_PATH.read_text(encoding="utf-8"))
    target_config_path = repo / "packaging/pipeline/targets/windows-x64.json"
    write_text(
        target_config_path,
        json.dumps(target_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
    )
    asset_provenance_path = repo / "packaging/windows/asset-provenance.json"
    asset_provenance_payload = {
        "assets": [
            {
                "blob": "a" * 40,
                "bytes": 123,
                "decision": "derive-fixture",
                "mode": "100644",
                "sha256": "b" * 64,
                "snapshot_path": "packaging/windows/legacy-assets/example.txt",
                "source_path": "example.txt",
            }
        ],
        "schema": "taiji-windows-legacy-asset-provenance/v1",
        "source_commit": "c" * 40,
        "source_repository": "fixture-repository",
    }
    write_text(
        asset_provenance_path,
        json.dumps(asset_provenance_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
    )
    write_text(
        repo / "packaging/windows/cache-requirements.json",
        (REPO_ROOT / "packaging/windows/cache-requirements.json").read_text(encoding="utf-8"),
    )
    write_text(repo / "README.md", "# fixture\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, commit, target_config_path, asset_provenance_path


def make_adapter_online(adapter_module, requirements_path: Path, python_path: str) -> dict[str, object]:
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    requirements_sha = adapter_module.canonical_json_sha256(requirements)
    observation_identity = {
        "schema": "taiji-windows-cache-observation/v1",
        "target_id": "windows-x64",
        "requirements_sha256": requirements_sha,
        "cache_root": r"D:\tw\cache",
        "entries": [],
    }
    host_facts = {
        "schema": "taiji-windows-host-facts/v1",
        "host_alias": "windows-direct",
        "os": "Windows",
        "os_version": "10.0",
        "architecture": "AMD64",
        "filesystem": "NTFS",
        "powershell_version": "5.1",
    }
    return {
        "schema": "taiji-package-online-doctor/v2",
        "builder_status": "BUILDER_READY",
        "host_alias": "windows-direct",
        "os": "Windows",
        "os_version": "10.0",
        "architecture": "AMD64",
        "powershell_version": "5.1",
        "git_path": r"C:\Program Files\Git\cmd\git.exe",
        "tar_path": r"C:\Windows\System32\tar.exe",
        "node_path": r"C:\Program Files\nodejs\node.exe",
        "npm_path": r"C:\Program Files\nodejs\npm.cmd",
        "python_path": python_path,
        "iscc_path": r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "filesystem": "NTFS",
        "free_bytes": 50 * 1024 * 1024 * 1024,
        "cache_root": r"D:\tw\cache",
        "cache_checks": [],
        "cache_requirements_sha256": requirements_sha,
        "cache_observation": dict(observation_identity, observed_at="2026-08-21T12:00:00Z"),
        "cache_observation_sha256": adapter_module.canonical_json_sha256(observation_identity),
        "host_facts": host_facts,
        "host_facts_sha256": adapter_module.canonical_json_sha256(host_facts),
        "remote_root_parent_exists": True,
        "blockers": [],
        "failure_categories": [],
    }


class WindowsBuilderInputPackageTests(unittest.TestCase):
    def assert_helper_help_and_api(self):
        self.assertTrue(HELPER_PATH.is_file(), "missing helper: {}".format(HELPER_PATH))
        result = run_checkout_bound_help(HELPER_PATH)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout.lower())
        module = load_helper()
        for name in ("inspect_input", "create_input", "verify_input", "main"):
            self.assertTrue(callable(getattr(module, name, None)), "{} is missing".format(name))
        return module

    def test_checkout_bound_help_and_public_callables_exist(self):
        self.assert_helper_help_and_api()

    def test_missing_triplet_stays_missing_and_plan_does_not_create(self):
        self.assert_helper_help_and_api()
        adapter_module = __import__(
            "packaging.pipeline.adapters.windows_x64",
            fromlist=["WindowsX64Adapter"],
        )
        with tempfile.TemporaryDirectory(prefix="taiji-windows-missing-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            target = json.loads(target_config_path.read_text(encoding="utf-8"))
            helper = load_helper()
            missing = helper.inspect_input(repo, commit)
            self.assertEqual(missing, {"status": "MISSING", "files": {}})
            adapter = adapter_module.WindowsX64Adapter()
            with mock.patch.object(adapter_module, "ASSET_PROVENANCE_PATH", asset_provenance_path), mock.patch.object(
                adapter_module,
                "TARGET_CONFIG_PATH",
                target_config_path,
                create=True,
            ), mock.patch.object(
                adapter_module,
                "SAFE_TAR_SOURCE_PATH",
                repo / "packaging/windows/safe_tar.py",
                create=True,
            ):
                plan = adapter.build_plan(repo, target, root / "state", run_id="run-1", ssh_config=None)
            self.assertEqual(plan["input"], {"status": "MISSING", "files": {}})
            for suffix in (".tar.gz", ".manifest.json", ".tar.gz.sha256"):
                self.assertFalse((repo / ("taijiagent-windows-builder-input-" + commit + suffix)).exists())

    def test_create_and_verify_bind_exact_triplet_gitignore_and_bootstrap(self):
        helper = self.assert_helper_help_and_api()
        adapter_module = __import__(
            "packaging.pipeline.adapters.windows_x64",
            fromlist=["WindowsX64Adapter"],
        )
        with tempfile.TemporaryDirectory(prefix="taiji-windows-create-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            created = helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            verified = helper.verify_input(repo, commit)
            inspected = helper.inspect_input(repo, commit)
            self.assertEqual(inspected, verified)
            self.assertEqual(verified["status"], "REUSABLE")
            self.assertEqual(verified["source_commit"], commit)
            names = {
                "archive": "taijiagent-windows-builder-input-{}.tar.gz".format(commit),
                "manifest": "taijiagent-windows-builder-input-{}.manifest.json".format(commit),
                "checksum": "taijiagent-windows-builder-input-{}.tar.gz.sha256".format(commit),
            }
            self.assertEqual(created["files"]["archive"]["basename"], names["archive"])
            self.assertEqual(created["files"]["manifest"]["basename"], names["manifest"])
            self.assertEqual(created["files"]["checksum"]["basename"], names["checksum"])
            manifest = json.loads((repo / names["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(
                set(manifest),
                {
                    "schema",
                    "source_commit",
                    "source_tree",
                    "version",
                    "source_branch",
                    "archive_basename",
                    "archive_bytes",
                    "archive_sha256",
                    "target_config_sha256",
                    "asset_provenance_sha256",
                    "created_at",
                },
            )
            self.assertEqual(manifest["schema"], "taiji-windows-builder-input/v1")
            self.assertEqual(manifest["source_commit"], commit)
            self.assertEqual(manifest["source_branch"], "main")
            self.assertEqual(manifest["version"], "1.2.3")
            self.assertEqual(manifest["archive_basename"], names["archive"])
            self.assertEqual(manifest["archive_bytes"], (repo / names["archive"]).stat().st_size)
            self.assertEqual(manifest["archive_sha256"], sha256_path(repo / names["archive"]))
            self.assertEqual(
                manifest["target_config_sha256"],
                sha256_bytes(canonical_json_bytes(json.loads(target_config_path.read_text(encoding="utf-8")))),
            )
            self.assertEqual(
                manifest["asset_provenance_sha256"],
                sha256_bytes(canonical_json_bytes(json.loads(asset_provenance_path.read_text(encoding="utf-8")))),
            )
            self.assertRegex(manifest["created_at"], r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
            self.assertEqual(
                (repo / names["checksum"]).read_text(encoding="utf-8"),
                "{}  {}\n{}  {}\n".format(
                    sha256_path(repo / names["archive"]),
                    names["archive"],
                    sha256_path(repo / names["manifest"]),
                    names["manifest"],
                ),
            )
            with (repo / names["archive"]).open("rb") as handle:
                self.assertEqual(int.from_bytes(handle.read(8)[4:8], "little"), 0)
            with tarfile.open(repo / names["archive"], "r:gz") as archive:
                members = {member.name for member in archive.getmembers() if member.isfile()}
            self.assertIn("VERSION", members)
            self.assertIn("apps/taiji-desktop/package.json", members)
            patterns = [
                "/taijiagent-windows-builder-input-*.tar.gz",
                "/taijiagent-windows-builder-input-*.manifest.json",
                "/taijiagent-windows-builder-input-*.tar.gz.sha256",
            ]
            gitignore_lines = GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
            for pattern in patterns:
                self.assertEqual(gitignore_lines.count(pattern), 1)
            adapter = adapter_module.WindowsX64Adapter()
            with mock.patch.object(adapter_module, "ASSET_PROVENANCE_PATH", asset_provenance_path), mock.patch.object(
                adapter_module,
                "CACHE_REQUIREMENTS_PATH",
                repo / "packaging/windows/cache-requirements.json",
            ), mock.patch.object(
                adapter_module,
                "TARGET_CONFIG_PATH",
                target_config_path,
                create=True,
            ), mock.patch.object(
                adapter_module,
                "SAFE_TAR_SOURCE_PATH",
                repo / "packaging/windows/safe_tar.py",
                create=True,
            ):
                plan = adapter.build_plan(
                    repo,
                    json.loads(target_config_path.read_text(encoding="utf-8")),
                    root / "state",
                    run_id="run-1",
                    ssh_config=None,
                )
                online = make_adapter_online(
                    adapter_module,
                    repo / "packaging/windows/cache-requirements.json",
                    json.loads(target_config_path.read_text(encoding="utf-8"))["python"],
                )
                finalized = adapter.bind_online_plan(plan, online)
            self.assertEqual(
                finalized["controller_bootstrap"],
                {
                    "safe_tar": {
                        "source_path": str((repo / "packaging/windows/safe_tar.py").resolve()),
                        "remote_path": r"input\controller-safe-tar.py",
                        "bytes": (repo / "packaging/windows/safe_tar.py").stat().st_size,
                        "sha256": sha256_path(repo / "packaging/windows/safe_tar.py"),
                        "python_path": json.loads(target_config_path.read_text(encoding="utf-8"))["python"],
                    }
                },
            )

    def test_verify_rejects_archive_bytes_even_when_manifest_and_sidecar_match_tampered_archive(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-windows-archive-bind-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            archive_path = repo / "taijiagent-windows-builder-input-{}.tar.gz".format(commit)
            manifest_path = repo / "taijiagent-windows-builder-input-{}.manifest.json".format(commit)
            checksum_path = repo / "taijiagent-windows-builder-input-{}.tar.gz.sha256".format(commit)
            with gzip.GzipFile(fileobj=io.BytesIO(archive_path.read_bytes())) as handle:
                tar_payload = handle.read()
            mutated = io.BytesIO()
            with gzip.GzipFile(filename="", mode="wb", fileobj=mutated, mtime=1) as handle:
                handle.write(tar_payload)
            archive_path.write_bytes(mutated.getvalue())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["archive_bytes"] = archive_path.stat().st_size
            manifest["archive_sha256"] = sha256_path(archive_path)
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            checksum_path.write_text(
                "{}  {}\n{}  {}\n".format(
                    sha256_path(archive_path),
                    archive_path.name,
                    sha256_path(manifest_path),
                    manifest_path.name,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(helper.PipelineError) as raised:
                helper.verify_input(repo, commit)
            self.assertEqual(raised.exception.category, "INPUT_VERIFICATION_FAILED")

    def test_git_repo_location_environment_cannot_hijack_helper_or_adapter_git_calls(self):
        helper = self.assert_helper_help_and_api()
        adapter_module = __import__(
            "packaging.pipeline.adapters.windows_x64",
            fromlist=["WindowsX64Adapter"],
        )
        scrubbed_keys = {
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_COMMON_DIR",
        }
        with tempfile.TemporaryDirectory(prefix="taiji-windows-env-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            poison = root / "poison"
            poison.mkdir()
            calls = []
            real_run = helper.subprocess.run

            def recording_run(argv, **kwargs):
                calls.append(kwargs.get("env"))
                return real_run(argv, **kwargs)

            with mock.patch.dict(os.environ, {key: str(poison) for key in scrubbed_keys}, clear=False), mock.patch.object(
                helper.subprocess, "run", side_effect=recording_run
            ):
                helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            self.assertTrue(calls)
            for env in calls:
                self.assertIsNotNone(env)
                for key in scrubbed_keys:
                    self.assertNotIn(key, env)

            adapter_calls = []
            real_adapter_run = adapter_module.subprocess.run

            def adapter_recording_run(argv, **kwargs):
                adapter_calls.append(kwargs.get("env"))
                return real_adapter_run(argv, **kwargs)

            target = json.loads(target_config_path.read_text(encoding="utf-8"))
            with mock.patch.dict(os.environ, {key: str(poison) for key in scrubbed_keys}, clear=False), \
                mock.patch.object(adapter_module, "ASSET_PROVENANCE_PATH", asset_provenance_path), \
                mock.patch.object(adapter_module, "CACHE_REQUIREMENTS_PATH", repo / "packaging/windows/cache-requirements.json"), \
                mock.patch.object(adapter_module, "SAFE_TAR_SOURCE_PATH", repo / "packaging/windows/safe_tar.py", create=True), \
                mock.patch.object(adapter_module, "TARGET_CONFIG_PATH", target_config_path, create=True), \
                mock.patch.object(adapter_module.subprocess, "run", side_effect=adapter_recording_run):
                adapter_module.WindowsX64Adapter().build_plan(repo, target, root / "state", run_id="run-1", ssh_config=None)
            self.assertTrue(adapter_calls)
            for env in adapter_calls:
                self.assertIsNotNone(env)
                for key in scrubbed_keys:
                    self.assertNotIn(key, env)

    def test_archive_ignores_untracked_attributes_and_all_external_git_config(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-windows-archive-isolation-") as temporary:
            root = Path(temporary)
            repo, _commit, target_config_path, asset_provenance_path = make_repo(root)
            write_text(repo / ".gitattributes", "README.md export-ignore\n")
            git(repo, "add", ".gitattributes")
            git(repo, "commit", "-m", "track archive attributes")
            commit = git(repo, "rev-parse", "HEAD").stdout.strip()

            baseline = helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            archive_path = Path(baseline["files"]["archive"]["path"])
            baseline_archive = archive_path.read_bytes()
            with tarfile.open(archive_path, "r:gz") as archive:
                self.assertNotIn("README.md", {member.name for member in archive.getmembers()})
            for item in baseline["files"].values():
                Path(item["path"]).unlink()

            poison_attributes = root / "poison.attributes"
            write_text(poison_attributes, "README.md -export-ignore\n")
            poison_config = root / "poison.gitconfig"
            write_text(
                poison_config,
                "[tar]\n\tumask = 0077\n[core]\n\tattributesFile = {}\n".format(poison_attributes),
            )
            write_text(repo / ".git/info/attributes", "README.md -export-ignore\n")
            git(repo, "config", "tar.umask", "0077")
            git(repo, "config", "core.attributesFile", str(poison_attributes))
            poisoned_environment = {
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "tar.umask",
                "GIT_CONFIG_VALUE_0": "0077",
                "GIT_CONFIG_KEY_1": "core.attributesFile",
                "GIT_CONFIG_VALUE_1": str(poison_attributes),
                "GIT_CONFIG_GLOBAL": str(poison_config),
                "GIT_CONFIG_SYSTEM": str(poison_config),
            }
            with mock.patch.dict(os.environ, poisoned_environment, clear=False):
                isolated = helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            isolated_archive = Path(isolated["files"]["archive"]["path"])
            self.assertEqual(isolated_archive.read_bytes(), baseline_archive)
            with tarfile.open(isolated_archive, "r:gz") as archive:
                self.assertNotIn("README.md", {member.name for member in archive.getmembers()})

    def test_create_uses_non_overwrite_publication_and_rolls_back_only_new_outputs_on_race(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-windows-race-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            archive_path = repo / "taijiagent-windows-builder-input-{}.tar.gz".format(commit)
            manifest_path = repo / "taijiagent-windows-builder-input-{}.manifest.json".format(commit)
            checksum_path = repo / "taijiagent-windows-builder-input-{}.tar.gz.sha256".format(commit)
            sentinel = b"pre-existing-manifest\n"
            real_link = getattr(helper.os, "link", None)
            self.assertTrue(callable(real_link), "os.link is required for the race contract test")
            state = {"count": 0}

            def racing_link(source, target):
                state["count"] += 1
                if Path(target).resolve() == manifest_path.resolve():
                    manifest_path.write_bytes(sentinel)
                return real_link(source, target)

            with mock.patch.object(helper.os, "link", side_effect=racing_link):
                with self.assertRaises(helper.PipelineError) as raised:
                    helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            self.assertEqual(raised.exception.category, "INPUT_ALREADY_EXISTS")
            self.assertFalse(archive_path.exists())
            self.assertFalse(checksum_path.exists())
            self.assertEqual(manifest_path.read_bytes(), sentinel)

    def test_publish_chmod_failure_rolls_back_every_final_and_is_safely_retryable(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-windows-publish-rollback-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            final_paths = [
                repo / "taijiagent-windows-builder-input-{}.tar.gz".format(commit),
                repo / "taijiagent-windows-builder-input-{}.manifest.json".format(commit),
                repo / "taijiagent-windows-builder-input-{}.tar.gz.sha256".format(commit),
            ]
            real_chmod = helper.os.chmod

            def fail_first_final_chmod(path, mode):
                if Path(path).resolve() == final_paths[0].resolve():
                    raise PermissionError("injected final chmod failure")
                return real_chmod(path, mode)

            with mock.patch.object(helper.os, "chmod", side_effect=fail_first_final_chmod):
                with self.assertRaises(helper.PipelineError) as raised:
                    helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            self.assertEqual(raised.exception.category, "INPUT_CREATION_FAILED")
            self.assertFalse(any(path.exists() or path.is_symlink() for path in final_paths))
            self.assertEqual(list(repo.glob(".taijiagent-windows-builder-input-*.tmp")), [])

            created = helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            self.assertEqual(created["status"], "REUSABLE")
            self.assertTrue(all(path.is_file() for path in final_paths))

    def test_build_plan_rejects_safe_tar_drift_and_uses_commit_bound_bootstrap_bytes(self):
        helper = self.assert_helper_help_and_api()
        adapter_module = __import__(
            "packaging.pipeline.adapters.windows_x64",
            fromlist=["WindowsX64Adapter"],
        )
        with tempfile.TemporaryDirectory(prefix="taiji-windows-bootstrap-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            safe_tar_path = repo / "packaging/windows/safe_tar.py"
            outside_safe_tar = root / "outside-safe-tar.py"
            outside_safe_tar.write_text("drifted\n", encoding="utf-8")
            target = json.loads(target_config_path.read_text(encoding="utf-8"))
            with mock.patch.object(adapter_module, "ASSET_PROVENANCE_PATH", asset_provenance_path), \
                mock.patch.object(adapter_module, "CACHE_REQUIREMENTS_PATH", repo / "packaging/windows/cache-requirements.json"), \
                mock.patch.object(adapter_module, "TARGET_CONFIG_PATH", target_config_path, create=True), \
                mock.patch.object(adapter_module, "SAFE_TAR_SOURCE_PATH", safe_tar_path, create=True):
                plan = adapter_module.WindowsX64Adapter().build_plan(
                    repo,
                    target,
                    root / "state",
                    run_id="run-1",
                    ssh_config=None,
                )
                commit_bytes = git(repo, "show", "{}:packaging/windows/safe_tar.py".format(commit)).stdout.encode("utf-8")
                self.assertEqual(plan["controller_bootstrap"]["safe_tar"]["bytes"], len(commit_bytes))
                self.assertEqual(plan["controller_bootstrap"]["safe_tar"]["sha256"], sha256_bytes(commit_bytes))
            with mock.patch.object(adapter_module, "ASSET_PROVENANCE_PATH", asset_provenance_path), \
                mock.patch.object(adapter_module, "CACHE_REQUIREMENTS_PATH", repo / "packaging/windows/cache-requirements.json"), \
                mock.patch.object(adapter_module, "TARGET_CONFIG_PATH", target_config_path, create=True), \
                mock.patch.object(adapter_module, "SAFE_TAR_SOURCE_PATH", outside_safe_tar, create=True):
                with self.assertRaises(adapter_module.PipelineError) as raised:
                    adapter_module.WindowsX64Adapter().build_plan(
                        repo,
                        target,
                        root / "state-2",
                        run_id="run-2",
                        ssh_config=None,
                    )
            self.assertEqual(raised.exception.category, "PLAN_INVALID")

    def test_build_plan_rejects_caller_target_drift_and_uses_committed_target_payload(self):
        adapter_module = __import__(
            "packaging.pipeline.adapters.windows_x64",
            fromlist=["WindowsX64Adapter"],
        )
        with tempfile.TemporaryDirectory(prefix="taiji-windows-target-bound-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            committed_target = json.loads(target_config_path.read_text(encoding="utf-8"))
            with mock.patch.object(adapter_module, "ASSET_PROVENANCE_PATH", asset_provenance_path), \
                mock.patch.object(adapter_module, "CACHE_REQUIREMENTS_PATH", repo / "packaging/windows/cache-requirements.json"), \
                mock.patch.object(adapter_module, "TARGET_CONFIG_PATH", target_config_path, create=True), \
                mock.patch.object(adapter_module, "SAFE_TAR_SOURCE_PATH", repo / "packaging/windows/safe_tar.py", create=True):
                plan = adapter_module.WindowsX64Adapter().build_plan(
                    repo,
                    committed_target,
                    root / "state",
                    run_id="run-1",
                    ssh_config=None,
                )
            self.assertEqual(plan["target_config"], committed_target)
            self.assertEqual(plan["target_adapter"], committed_target)
            self.assertEqual(plan["host_alias"], committed_target["host_alias"])
            self.assertEqual(plan["controller_bootstrap"]["safe_tar"]["python_path"], committed_target["python"])
            self.assertEqual(
                plan["remote_run_dir"],
                committed_target["remote_root"] + "\\" + commit + "\\run-1",
            )

            for field, alternate in (
                ("python", r"D:\tw\other-python\python.exe"),
                ("host_alias", "windows-alt"),
                ("remote_root", r"D:\tw\elsewhere"),
            ):
                with self.subTest(field=field):
                    drifted_target = dict(committed_target)
                    drifted_target[field] = alternate
                    with mock.patch.object(adapter_module, "ASSET_PROVENANCE_PATH", asset_provenance_path), \
                        mock.patch.object(adapter_module, "CACHE_REQUIREMENTS_PATH", repo / "packaging/windows/cache-requirements.json"), \
                        mock.patch.object(adapter_module, "TARGET_CONFIG_PATH", target_config_path, create=True), \
                        mock.patch.object(adapter_module, "SAFE_TAR_SOURCE_PATH", repo / "packaging/windows/safe_tar.py", create=True):
                        with self.assertRaises(adapter_module.PipelineError) as raised:
                            adapter_module.WindowsX64Adapter().build_plan(
                                repo,
                                drifted_target,
                                root / ("state-" + field),
                                run_id="run-" + field,
                                ssh_config=None,
                            )
                    self.assertEqual(raised.exception.category, "PLAN_INVALID")

    def test_controller_runner_cannot_bypass_commit_bound_target_asset_or_safe_tar(self):
        adapter_module = __import__(
            "packaging.pipeline.adapters.windows_x64",
            fromlist=["WindowsX64Adapter"],
        )
        with tempfile.TemporaryDirectory(prefix="taiji-windows-runner-bound-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            target = json.loads(target_config_path.read_text(encoding="utf-8"))

            target_payload = json.loads(target_config_path.read_text(encoding="utf-8"))
            target_payload["python"] = r"D:\tw\drifted-python\python.exe"
            target_show = json.dumps(target_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"

            asset_payload = json.loads(asset_provenance_path.read_text(encoding="utf-8"))
            asset_payload["source_repository"] = "drifted-repository"
            asset_show = json.dumps(asset_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"

            intercepts = {
                ("show", "{}:packaging/pipeline/targets/windows-x64.json".format(commit)): target_show,
                ("show", "{}:packaging/windows/asset-provenance.json".format(commit)): asset_show,
                ("show", "{}:packaging/windows/safe_tar.py".format(commit)): "drifted-safe-tar\n",
            }

            def controller_runner(argv, **kwargs):
                command = tuple(str(item) for item in argv[3:])
                if command in intercepts:
                    return type("Result", (), {"returncode": 0, "stdout": intercepts[command], "stderr": ""})()
                return subprocess.run(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=kwargs.get("text", True),
                    check=False,
                    env=kwargs.get("env"),
                )

            for label, expected in (
                ("target", "target config"),
                ("asset", "asset provenance"),
                ("safe-tar", "safe_tar"),
            ):
                with self.subTest(case=label):
                    active = {
                        key: value
                        for key, value in intercepts.items()
                        if label == "safe-tar" and "safe_tar.py" in key[1]
                        or label == "target" and "targets/windows-x64.json" in key[1]
                        or label == "asset" and "asset-provenance.json" in key[1]
                    }

                    def single_intercept_runner(argv, **kwargs):
                        command = tuple(str(item) for item in argv[3:])
                        if command in active:
                            return type("Result", (), {"returncode": 0, "stdout": active[command], "stderr": ""})()
                        return subprocess.run(
                            argv,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=kwargs.get("text", True),
                            check=False,
                            env=kwargs.get("env"),
                        )

                    with mock.patch.object(adapter_module, "ASSET_PROVENANCE_PATH", asset_provenance_path), \
                        mock.patch.object(adapter_module, "CACHE_REQUIREMENTS_PATH", repo / "packaging/windows/cache-requirements.json"), \
                        mock.patch.object(adapter_module, "TARGET_CONFIG_PATH", target_config_path, create=True), \
                        mock.patch.object(adapter_module, "SAFE_TAR_SOURCE_PATH", repo / "packaging/windows/safe_tar.py", create=True):
                        with self.assertRaises(adapter_module.PipelineError) as raised:
                            adapter_module.WindowsX64Adapter(controller_runner=single_intercept_runner).build_plan(
                                repo,
                                target,
                                root / ("state-" + label),
                                run_id="run-" + label,
                                ssh_config=None,
                            )
                    self.assertEqual(raised.exception.category, "PLAN_INVALID")

    def test_bind_online_plan_rejects_missing_or_drifted_python_path(self):
        adapter_module = __import__(
            "packaging.pipeline.adapters.windows_x64",
            fromlist=["WindowsX64Adapter"],
        )
        with tempfile.TemporaryDirectory(prefix="taiji-windows-online-python-") as temporary:
            root = Path(temporary)
            repo, _commit, target_config_path, asset_provenance_path = make_repo(root)
            target = json.loads(target_config_path.read_text(encoding="utf-8"))
            with mock.patch.object(adapter_module, "ASSET_PROVENANCE_PATH", asset_provenance_path), \
                mock.patch.object(adapter_module, "CACHE_REQUIREMENTS_PATH", repo / "packaging/windows/cache-requirements.json"), \
                mock.patch.object(adapter_module, "TARGET_CONFIG_PATH", target_config_path, create=True), \
                mock.patch.object(adapter_module, "SAFE_TAR_SOURCE_PATH", repo / "packaging/windows/safe_tar.py", create=True):
                plan = adapter_module.WindowsX64Adapter().build_plan(
                    repo,
                    target,
                    root / "state",
                    run_id="run-1",
                    ssh_config=None,
                )
                online = make_adapter_online(
                    adapter_module,
                    repo / "packaging/windows/cache-requirements.json",
                    target["python"],
                )
                missing_python = dict(online)
                del missing_python["python_path"]
                with self.assertRaises(adapter_module.PipelineError) as raised:
                    adapter_module.WindowsX64Adapter().bind_online_plan(plan, missing_python)
                self.assertEqual(raised.exception.category, "ONLINE_DOCTOR_BLOCKED")

                minimal_without_python = {
                    key: online[key]
                    for key in (
                        "schema",
                        "builder_status",
                        "blockers",
                        *adapter_module.WindowsX64Adapter.online_plan_keys,
                    )
                }
                with self.assertRaises(adapter_module.PipelineError) as raised:
                    adapter_module.WindowsX64Adapter().bind_online_plan(
                        plan,
                        minimal_without_python,
                    )
                self.assertEqual(raised.exception.category, "ONLINE_DOCTOR_BLOCKED")

                wrong_python = dict(online)
                wrong_python["python_path"] = r"D:\tw\other-python\python.exe"
                with self.assertRaises(adapter_module.PipelineError) as raised:
                    adapter_module.WindowsX64Adapter().bind_online_plan(plan, wrong_python)
                self.assertEqual(raised.exception.category, "ONLINE_DOCTOR_BLOCKED")

    def test_create_and_plan_reject_non_exact_version_bytes(self):
        helper = self.assert_helper_help_and_api()
        adapter_module = __import__(
            "packaging.pipeline.adapters.windows_x64",
            fromlist=["WindowsX64Adapter"],
        )
        cases = {
            "missing-lf": b"1.2.3",
            "crlf": b"1.2.3\r\n",
            "bom": b"\xef\xbb\xbf1.2.3\n",
            "extra-line": b"1.2.3\n1.2.4\n",
        }
        for label, payload in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory(prefix="taiji-windows-version-") as temporary:
                root = Path(temporary)
                repo, _commit, target_config_path, asset_provenance_path = make_repo(root)
                (repo / "VERSION").write_bytes(payload)
                git(repo, "add", "VERSION")
                git(repo, "commit", "-m", "bad version")
                commit = git(repo, "rev-parse", "HEAD").stdout.strip()
                with self.assertRaises(helper.PipelineError) as raised:
                    helper.create_input(repo, commit, target_config_path, asset_provenance_path)
                self.assertEqual(raised.exception.category, "REPO_IDENTITY_MISMATCH")
                with mock.patch.object(adapter_module, "ASSET_PROVENANCE_PATH", asset_provenance_path), \
                    mock.patch.object(adapter_module, "CACHE_REQUIREMENTS_PATH", repo / "packaging/windows/cache-requirements.json"), \
                    mock.patch.object(adapter_module, "TARGET_CONFIG_PATH", target_config_path, create=True), \
                    mock.patch.object(adapter_module, "SAFE_TAR_SOURCE_PATH", repo / "packaging/windows/safe_tar.py", create=True):
                    with self.assertRaises(adapter_module.PipelineError) as raised:
                        adapter_module.WindowsX64Adapter().build_plan(
                            repo,
                            json.loads(target_config_path.read_text(encoding="utf-8")),
                            root / "state",
                            run_id="run-1",
                            ssh_config=None,
                        )
                self.assertEqual(raised.exception.category, "REPO_IDENTITY_MISMATCH")

    def test_target_and_asset_inputs_must_be_repo_fixed_commit_bound_and_canonical_hashed(self):
        helper = self.assert_helper_help_and_api()
        adapter_module = __import__(
            "packaging.pipeline.adapters.windows_x64",
            fromlist=["WindowsX64Adapter"],
        )
        with tempfile.TemporaryDirectory(prefix="taiji-windows-fixed-inputs-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            created = helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            manifest_path = Path(created["files"]["manifest"]["path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            target_payload = json.loads(target_config_path.read_text(encoding="utf-8"))
            asset_payload = json.loads(asset_provenance_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["target_config_sha256"], sha256_bytes(canonical_json_bytes(target_payload)))
            self.assertEqual(manifest["asset_provenance_sha256"], sha256_bytes(canonical_json_bytes(asset_payload)))
            target = json.loads(target_config_path.read_text(encoding="utf-8"))
            with mock.patch.object(adapter_module, "ASSET_PROVENANCE_PATH", asset_provenance_path), \
                mock.patch.object(adapter_module, "CACHE_REQUIREMENTS_PATH", repo / "packaging/windows/cache-requirements.json"), \
                mock.patch.object(adapter_module, "TARGET_CONFIG_PATH", target_config_path, create=True), \
                mock.patch.object(adapter_module, "SAFE_TAR_SOURCE_PATH", repo / "packaging/windows/safe_tar.py", create=True):
                plan = adapter_module.WindowsX64Adapter().build_plan(repo, target, root / "state", run_id="run-1", ssh_config=None)
            self.assertEqual(plan["target_config_sha256"], manifest["target_config_sha256"])
            self.assertEqual(plan["asset_provenance_sha256"], manifest["asset_provenance_sha256"])

            external_target = root / "external-target.json"
            external_target.write_text(
                json.dumps(json.loads(WINDOWS_TARGET_PATH.read_text(encoding="utf-8")), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(helper.PipelineError) as raised:
                helper.create_input(repo, commit, external_target, asset_provenance_path)
            self.assertEqual(raised.exception.category, "PLAN_INVALID")

            external_asset = root / "external-asset.json"
            external_asset.write_text(ASSET_PROVENANCE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaises(helper.PipelineError) as raised:
                helper.create_input(repo, commit, target_config_path, external_asset)
            self.assertEqual(raised.exception.category, "PLAN_INVALID")

            wrong_target_repo = root / "wrong-target"
            shutil.copytree(repo, wrong_target_repo)
            wrong_target = json.loads((wrong_target_repo / "packaging/pipeline/targets/windows-x64.json").read_text(encoding="utf-8"))
            wrong_target["target_id"] = "other"
            (wrong_target_repo / "packaging/pipeline/targets/windows-x64.json").write_text(
                json.dumps(wrong_target, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            git(wrong_target_repo, "add", "packaging/pipeline/targets/windows-x64.json")
            git(wrong_target_repo, "commit", "-m", "wrong target")
            with self.assertRaises(helper.PipelineError) as raised:
                helper.create_input(
                    wrong_target_repo,
                    git(wrong_target_repo, "rev-parse", "HEAD").stdout.strip(),
                    wrong_target_repo / "packaging/pipeline/targets/windows-x64.json",
                    wrong_target_repo / "packaging/windows/asset-provenance.json",
                )
            self.assertEqual(raised.exception.category, "PLAN_INVALID")

    def test_triplet_modes_are_exact_0600(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-windows-modes-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            for name in (
                "taijiagent-windows-builder-input-{}.tar.gz".format(commit),
                "taijiagent-windows-builder-input-{}.manifest.json".format(commit),
                "taijiagent-windows-builder-input-{}.tar.gz.sha256".format(commit),
            ):
                self.assertEqual(stat.S_IMODE((repo / name).stat().st_mode), 0o600)

    def test_partial_and_invalid_triplets_fail_without_repair(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-windows-invalid-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            paths = {
                "archive": repo / "taijiagent-windows-builder-input-{}.tar.gz".format(commit),
                "manifest": repo / "taijiagent-windows-builder-input-{}.manifest.json".format(commit),
                "checksum": repo / "taijiagent-windows-builder-input-{}.tar.gz.sha256".format(commit),
            }
            original = {name: path.read_bytes() for name, path in paths.items()}

            for missing_name in paths:
                sandbox = root / ("partial-" + missing_name)
                shutil.copytree(repo, sandbox)
                missing_path = sandbox / paths[missing_name].name
                missing_path.unlink()
                with self.subTest(missing=missing_name):
                    with self.assertRaises(helper.PipelineError) as raised:
                        helper.inspect_input(sandbox, commit)
                    self.assertEqual(raised.exception.category, "INPUT_TRIPLET_PARTIAL")
                    self.assertFalse(missing_path.exists())

            bad_manifest_repo = root / "bad-manifest"
            shutil.copytree(repo, bad_manifest_repo)
            manifest_path = bad_manifest_repo / paths["manifest"].name
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["source_tree"] = "f" * 40
            manifest_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(helper.PipelineError) as raised:
                helper.verify_input(bad_manifest_repo, commit)
            self.assertEqual(raised.exception.category, "INPUT_VERIFICATION_FAILED")
            self.assertEqual((bad_manifest_repo / paths["archive"].name).read_bytes(), original["archive"])

            bad_checksum_repo = root / "bad-checksum"
            shutil.copytree(repo, bad_checksum_repo)
            checksum_path = bad_checksum_repo / paths["checksum"].name
            checksum_path.write_text(
                "{}  {}\n{}  {}\n".format(
                    "0" * 64,
                    paths["archive"].name,
                    sha256_path(bad_checksum_repo / paths["manifest"].name),
                    paths["manifest"].name,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(helper.PipelineError) as raised:
                helper.verify_input(bad_checksum_repo, commit)
            self.assertEqual(raised.exception.category, "INPUT_VERIFICATION_FAILED")
            self.assertEqual((bad_checksum_repo / paths["manifest"].name).read_bytes(), original["manifest"])

    def test_symlink_hardlink_owner_mode_and_create_guards_are_enforced(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-windows-guards-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            created = helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            archive_path = Path(created["files"]["archive"]["path"])
            manifest_path = Path(created["files"]["manifest"]["path"])
            checksum_path = Path(created["files"]["checksum"]["path"])

            with self.assertRaises(helper.PipelineError) as raised:
                helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            self.assertEqual(raised.exception.category, "INPUT_ALREADY_EXISTS")

            chmod_repo = root / "mode"
            shutil.copytree(repo, chmod_repo)
            os.chmod(chmod_repo / archive_path.name, 0o644)
            with self.assertRaises(helper.PipelineError) as raised:
                helper.verify_input(chmod_repo, commit)
            self.assertEqual(raised.exception.category, "INPUT_VERIFICATION_FAILED")

            link_repo = root / "symlink"
            shutil.copytree(repo, link_repo)
            (link_repo / archive_path.name).unlink()
            os.symlink(link_repo / manifest_path.name, link_repo / archive_path.name)
            with self.assertRaises(helper.PipelineError) as raised:
                helper.verify_input(link_repo, commit)
            self.assertEqual(raised.exception.category, "INPUT_VERIFICATION_FAILED")

            hardlink_repo = root / "hardlink"
            shutil.copytree(repo, hardlink_repo)
            extra_link = hardlink_repo / "archive-second-link.tar.gz"
            os.link(hardlink_repo / archive_path.name, extra_link)
            with self.assertRaises(helper.PipelineError) as raised:
                helper.verify_input(hardlink_repo, commit)
            self.assertEqual(raised.exception.category, "INPUT_VERIFICATION_FAILED")

            owner_repo = root / "owner"
            shutil.copytree(repo, owner_repo)
            actual_uid = os.getuid()
            with mock.patch.object(helper.os, "getuid", return_value=actual_uid + 1):
                with self.assertRaises(helper.PipelineError) as raised:
                    helper.verify_input(owner_repo, commit)
            self.assertEqual(raised.exception.category, "INPUT_VERIFICATION_FAILED")

            dirty_repo = root / "dirty"
            shutil.copytree(repo, dirty_repo)
            write_text(dirty_repo / "README.md", "# dirty\n")
            with self.assertRaises(helper.PipelineError) as raised:
                helper.create_input(dirty_repo, git(dirty_repo, "rev-parse", "HEAD").stdout.strip(), target_config_path, asset_provenance_path)
            self.assertEqual(raised.exception.category, "WORKTREE_NOT_CLEAN")

            branch_repo = root / "branch"
            shutil.copytree(repo, branch_repo)
            git(branch_repo, "checkout", "-b", "codex/not-main")
            with self.assertRaises(helper.PipelineError) as raised:
                helper.create_input(branch_repo, git(branch_repo, "rev-parse", "HEAD").stdout.strip(), target_config_path, asset_provenance_path)
            self.assertEqual(raised.exception.category, "BRANCH_NOT_MAIN")

            drift_repo = root / "drift"
            shutil.copytree(repo, drift_repo)
            git(drift_repo, "commit", "--allow-empty", "-m", "later")
            with self.assertRaises(helper.PipelineError) as raised:
                helper.create_input(drift_repo, commit, target_config_path, asset_provenance_path)
            self.assertEqual(raised.exception.category, "SOURCE_COMMIT_DRIFTED")

    def test_create_uses_full_commit_git_archive_and_version_binding(self):
        helper = self.assert_helper_help_and_api()
        with tempfile.TemporaryDirectory(prefix="taiji-windows-archive-") as temporary:
            root = Path(temporary)
            repo, commit, target_config_path, asset_provenance_path = make_repo(root)
            real_run = helper.subprocess.run
            calls = []

            def recording_run(argv, **kwargs):
                calls.append([str(item) for item in argv])
                return real_run(argv, **kwargs)

            with mock.patch.object(helper.subprocess, "run", side_effect=recording_run):
                helper.create_input(repo, commit, target_config_path, asset_provenance_path)
            archive_calls = [
                call for call in calls
                if any(call[index:index + 2] == ["archive", "--format=tar"] for index in range(len(call) - 1))
            ]
            self.assertEqual(len(archive_calls), 1)
            self.assertEqual(archive_calls[0][-1], commit)

            bad_version_repo = root / "bad-version"
            shutil.copytree(repo, bad_version_repo)
            write_text(bad_version_repo / "VERSION", "1.2.3\n1.2.4\n")
            git(bad_version_repo, "add", "VERSION")
            git(bad_version_repo, "commit", "-m", "bad version")
            bad_commit = git(bad_version_repo, "rev-parse", "HEAD").stdout.strip()
            with self.assertRaises(helper.PipelineError) as raised:
                helper.create_input(bad_version_repo, bad_commit, target_config_path, asset_provenance_path)
            self.assertEqual(raised.exception.category, "REPO_IDENTITY_MISMATCH")

            mismatch_repo = root / "bad-package-version"
            shutil.copytree(repo, mismatch_repo)
            write_text(
                mismatch_repo / "apps/taiji-desktop/package.json",
                json.dumps({"name": "taiji-desktop", "version": "9.9.9"}, ensure_ascii=False, separators=(",", ":")) + "\n",
            )
            git(mismatch_repo, "add", "apps/taiji-desktop/package.json")
            git(mismatch_repo, "commit", "-m", "bad package version")
            mismatch_commit = git(mismatch_repo, "rev-parse", "HEAD").stdout.strip()
            with self.assertRaises(helper.PipelineError) as raised:
                helper.create_input(mismatch_repo, mismatch_commit, target_config_path, asset_provenance_path)
            self.assertEqual(raised.exception.category, "REPO_IDENTITY_MISMATCH")


if __name__ == "__main__":
    unittest.main()
