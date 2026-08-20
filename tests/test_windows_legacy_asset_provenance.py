"""Tests for the selected legacy Windows asset provenance boundary."""

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "packaging/windows/verify_legacy_assets.py"
LOCK = ROOT / "packaging/windows/asset-provenance.json"
OLD_GIT_DIR = Path("/Users/bwb/Documents/工作/taiji-agentv1.0-win/.git")
SOURCE_COMMIT = "f33663f7e3ffee672d39af7b4ecbe9fd2869a00b"

EXPECTED_ASSETS = (
    {
        "source_path": "scripts/windows/Initialize-FastTrackSession.ps1",
        "snapshot_path": (
            "packaging/windows/legacy-assets/"
            "scripts/windows/Initialize-FastTrackSession.ps1"
        ),
        "mode": "100644",
        "blob": "f792452ab6b3d2b95a1d2fd9e9badc5c71923cf2",
        "bytes": 4954,
        "sha256": "49b5081d36ece563db5ecaafc9696dde31e86a4f73f60a3fe5e6898b2cbd4ee0",
        "decision": "derive-parameterized-session",
    },
    {
        "source_path": "scripts/windows/Stage-WindowsPayload.ps1",
        "snapshot_path": (
            "packaging/windows/legacy-assets/"
            "scripts/windows/Stage-WindowsPayload.ps1"
        ),
        "mode": "100644",
        "blob": "17ba9b8fde890a112aa9882d17bf097247d4c910",
        "bytes": 18021,
        "sha256": "fbe32f4494d97e00b37e67627b106b08b840e34f449b2b2ebffedfcddcc54198",
        "decision": "derive-parameterized-staging",
    },
    {
        "source_path": "installer/TaijiAgent.iss",
        "snapshot_path": "packaging/windows/legacy-assets/installer/TaijiAgent.iss",
        "mode": "100644",
        "blob": "ce11f481b6399deec0b436e0e13326d6a692253d",
        "bytes": 1820,
        "sha256": "f6e1934c4aa8cffd948896cd7c72524138aaf1fa7515193637d6af9863cb0505",
        "decision": "derive-candidate-installer",
    },
)


def expected_assets():
    return [dict(asset) for asset in EXPECTED_ASSETS]


def load_verifier():
    for path in (VERIFIER, LOCK):
        if path == LOCK:
            assert path.is_file(), "missing provenance lock: {}".format(path)
        else:
            assert path.is_file(), "missing provenance verifier: {}".format(path)
    for asset in EXPECTED_ASSETS:
        path = ROOT / asset["snapshot_path"]
        assert path.is_file(), "missing legacy asset snapshot: {}".format(path)
    spec = importlib.util.spec_from_file_location("windows_legacy_asset_verifier", VERIFIER)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load legacy asset verifier")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, AttributeError) as exc:
        raise AssertionError("legacy asset verifier is not importable: {}".format(exc))
    return module


def run_git(git_dir, *args):
    completed = subprocess.run(
        ["/usr/bin/git", "--git-dir", str(git_dir)] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout


def git_blob_sha1(data):
    header = "blob {}\0".format(len(data)).encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def write_lock(path, assets):
    value = {
        "schema": "taiji-windows-legacy-asset-provenance/v1",
        "source_repository": "taiji-agentv1.0-win",
        "source_commit": SOURCE_COMMIT,
        "assets": [dict(asset) for asset in assets],
    }
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def make_snapshot_root(root, assets):
    for asset in assets:
        destination = root / asset["snapshot_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = run_git(
            OLD_GIT_DIR,
            "show",
            "{}:{}".format(SOURCE_COMMIT, asset["source_path"]),
        )
        destination.write_bytes(data)
        destination.chmod(0o644)


def make_synthetic_git_repo(root, *, mode="100644", content=b"synthetic\n"):
    repo = root / "synthetic-git"
    repo.mkdir()
    subprocess.run(["/usr/bin/git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "config", "user.name", "Test Runner"],
        check=True,
    )
    subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    source = repo / "selected.txt"
    source.write_bytes(content)
    source.chmod(int(mode, 8))
    subprocess.run(["/usr/bin/git", "-C", str(repo), "add", "selected.txt"], check=True)
    subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "commit", "-q", "-m", "synthetic"],
        check=True,
    )
    commit = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        check=True,
        text=True,
    ).stdout.strip()
    blob = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "rev-parse", "HEAD:selected.txt"],
        stdout=subprocess.PIPE,
        check=True,
        text=True,
    ).stdout.strip()
    asset = {
        "source_path": "selected.txt",
        "snapshot_path": "snapshot/selected.txt",
        "mode": mode,
        "blob": blob,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "decision": "synthetic",
    }
    return repo / ".git", commit, asset


class WindowsLegacyAssetProvenanceTests(unittest.TestCase):
    def test_committed_assets_match_hard_coded_truth(self):
        verifier = load_verifier()
        self.assertEqual(verifier.EXPECTED_SOURCE_COMMIT, SOURCE_COMMIT)
        self.assertEqual(list(verifier.EXPECTED_ASSETS), list(EXPECTED_ASSETS))
        verifier.verify_git_objects(OLD_GIT_DIR, SOURCE_COMMIT, expected_assets())
        verifier.verify_snapshots(ROOT, expected_assets())
        verifier.verify_lock(LOCK, expected_assets())
        verifier.verify_selected_assets(
            OLD_GIT_DIR, ROOT, LOCK
        )

    def test_selected_git_objects_reject_wrong_commit_path_mode_blob_bytes_and_hash(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as temporary:
            git_dir, commit, asset = make_synthetic_git_repo(Path(temporary))
            verifier.verify_git_objects(git_dir, commit, [asset])

            mutations = (
                ("commit", "0" * 40),
                ("source_path", "wrong.txt"),
                ("mode", "100755"),
                ("blob", "0" * 40),
                ("bytes", asset["bytes"] + 1),
                ("sha256", "0" * 64),
            )
            for field, value in mutations:
                with self.subTest(field=field):
                    mutated = dict(asset)
                    if field == "commit":
                        mutated_commit = value
                    else:
                        mutated_commit = commit
                        mutated[field] = value
                    with self.assertRaises(AssertionError):
                        verifier.verify_git_objects(git_dir, mutated_commit, [mutated])

    def test_snapshot_verifier_rejects_symlink_mode_hardlink_and_hash_drift(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_snapshot_root(root, expected_assets())
            verifier.verify_snapshots(root, expected_assets())

            for kind in ("symlink", "mode", "hardlink", "hash"):
                with self.subTest(kind=kind):
                    make_snapshot_root(root, expected_assets())
                    path = root / EXPECTED_ASSETS[0]["snapshot_path"]
                    if kind == "symlink":
                        replacement = root / "replacement.txt"
                        replacement.write_bytes(path.read_bytes())
                        path.unlink()
                        path.symlink_to(replacement)
                    elif kind == "mode":
                        path.chmod(0o755)
                    elif kind == "hardlink":
                        os.link(path, root / "hardlink.txt")
                    else:
                        path.write_bytes(b"drift")
                        path.chmod(0o644)
                    with self.assertRaises(AssertionError):
                        verifier.verify_snapshots(root, expected_assets())

    def test_lock_mutation_cannot_change_verifier_truth(self):
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            make_snapshot_root(root, expected_assets())
            lock = root / "asset-provenance.json"
            write_lock(lock, expected_assets())
            verifier.verify_selected_assets(OLD_GIT_DIR, root, lock)
            for field, value in (
                ("blob", "0" * 40),
                ("sha256", "0" * 64),
                ("decision", "accept-all"),
            ):
                with self.subTest(field=field):
                    mutated = expected_assets()
                    mutated[0][field] = value
                    write_lock(lock, mutated)
                    with self.assertRaises(AssertionError):
                        verifier.verify_selected_assets(OLD_GIT_DIR, root, lock)
                    write_lock(lock, expected_assets())

    def test_verifier_help_runs_isolated_from_external_cwd(self):
        load_verifier()
        with tempfile.TemporaryDirectory() as temporary:
            external_cwd = Path(temporary)
            before = sorted(path.name for path in external_cwd.iterdir())
            completed = subprocess.run(
                ["/usr/bin/python3", "-I", "-B", str(VERIFIER), "--help"],
                cwd=str(external_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertNotIn("ModuleNotFoundError", completed.stderr)
            self.assertEqual(before, sorted(path.name for path in external_cwd.iterdir()))


if __name__ == "__main__":
    unittest.main()
