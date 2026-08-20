"""Verify the selected Windows fast-track assets from immutable Git objects."""

import argparse
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path


EXPECTED_SOURCE_COMMIT = "f33663f7e3ffee672d39af7b4ecbe9fd2869a00b"
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

LOCK_SCHEMA = "taiji-windows-legacy-asset-provenance/v1"
SOURCE_REPOSITORY = "taiji-agentv1.0-win"


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def git_blob_sha1(data):
    header = "blob {}\0".format(len(data)).encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def _run_git(source_git_dir, args, runner):
    command = ["/usr/bin/git", "--git-dir", str(source_git_dir)] + list(args)
    if runner is None:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        _assert(
            completed.returncode == 0,
            "git command failed: {}\n{}".format(
                " ".join(command), completed.stderr.decode("utf-8", "replace")
            ),
        )
        return completed.stdout
    result = runner(command)
    if isinstance(result, subprocess.CompletedProcess):
        _assert(result.returncode == 0, "injected git command failed")
        return result.stdout
    return result


def _expected_asset_keys(asset):
    return (
        "source_path",
        "snapshot_path",
        "mode",
        "blob",
        "bytes",
        "sha256",
        "decision",
    )


def _validate_asset_shape(asset):
    _assert(isinstance(asset, dict), "asset must be an object")
    _assert(set(asset) == set(_expected_asset_keys(asset)), "asset keys are invalid")
    _assert(
        isinstance(asset["source_path"], str)
        and asset["source_path"]
        and "\\" not in asset["source_path"]
        and not asset["source_path"].startswith("/")
        and ".." not in asset["source_path"].split("/"),
        "asset source path is unsafe",
    )
    _assert(
        isinstance(asset["snapshot_path"], str)
        and asset["snapshot_path"]
        and "\\" not in asset["snapshot_path"]
        and not asset["snapshot_path"].startswith("/")
        and ".." not in asset["snapshot_path"].split("/"),
        "asset snapshot path is unsafe",
    )
    _assert(asset["mode"] == "100644", "asset mode is not 100644")
    _assert(
        isinstance(asset["blob"], str)
        and len(asset["blob"]) == 40
        and asset["blob"] == asset["blob"].lower()
        and all(character in "0123456789abcdef" for character in asset["blob"]),
        "asset Git blob is invalid",
    )
    _assert(isinstance(asset["bytes"], int) and asset["bytes"] >= 0, "asset bytes are invalid")
    _assert(
        isinstance(asset["sha256"], str)
        and len(asset["sha256"]) == 64
        and asset["sha256"] == asset["sha256"].lower()
        and all(character in "0123456789abcdef" for character in asset["sha256"]),
        "asset SHA256 is invalid",
    )
    _assert(isinstance(asset["decision"], str) and asset["decision"], "asset decision is invalid")


def verify_git_objects(source_git_dir, source_commit, expected_assets, *, runner=None):
    _assert(
        isinstance(source_commit, str)
        and len(source_commit) == 40
        and source_commit == source_commit.lower()
        and all(character in "0123456789abcdef" for character in source_commit),
        "source commit is invalid",
    )
    resolved = _run_git(
        source_git_dir,
        ["rev-parse", "--verify", "{}^{{commit}}".format(source_commit)],
        runner,
    ).strip()
    _assert(resolved.decode("ascii") == source_commit, "source commit resolution drifted")
    for asset in expected_assets:
        _validate_asset_shape(asset)
        tree_output = _run_git(
            source_git_dir,
            ["ls-tree", "-z", source_commit, "--", asset["source_path"]],
            runner,
        )
        records = [record for record in tree_output.split(b"\0") if record]
        _assert(len(records) == 1, "selected source path is not exactly one tree entry")
        metadata, path = records[0].split(b"\t", 1)
        mode, object_type, blob = metadata.split(b" ", 2)
        _assert(object_type == b"blob", "selected Git object is not a blob")
        _assert(mode.decode("ascii") == asset["mode"], "selected Git mode drifted")
        _assert(blob.decode("ascii") == asset["blob"], "selected Git blob drifted")
        _assert(path.decode("utf-8") == asset["source_path"], "selected Git path drifted")
        data = _run_git(source_git_dir, ["cat-file", "blob", asset["blob"]], runner)
        _assert(len(data) == asset["bytes"], "selected Git blob byte count drifted")
        _assert(hashlib.sha256(data).hexdigest() == asset["sha256"], "selected Git blob SHA256 drifted")
        _assert(git_blob_sha1(data) == asset["blob"], "selected Git blob SHA1 is inconsistent")


def verify_snapshots(repo_root, expected_assets):
    repo_root = Path(repo_root)
    for asset in expected_assets:
        _validate_asset_shape(asset)
        path = repo_root / asset["snapshot_path"]
        try:
            metadata = os.lstat(str(path))
        except FileNotFoundError:
            raise AssertionError("legacy asset snapshot is missing: {}".format(path))
        _assert(metadata.st_uid == os.getuid(), "snapshot owner drifted: {}".format(path))
        _assert(stat.S_ISREG(metadata.st_mode), "snapshot is not a regular file: {}".format(path))
        _assert(not stat.S_ISLNK(metadata.st_mode), "snapshot is a symlink: {}".format(path))
        _assert(metadata.st_nlink == 1, "snapshot has unexpected hardlinks: {}".format(path))
        _assert(
            stat.S_IMODE(metadata.st_mode) == 0o644,
            "snapshot permission bits drifted: {}".format(path),
        )
        data = path.read_bytes()
        _assert(len(data) == asset["bytes"], "snapshot byte count drifted: {}".format(path))
        _assert(
            hashlib.sha256(data).hexdigest() == asset["sha256"],
            "snapshot SHA256 drifted: {}".format(path),
        )
        _assert(git_blob_sha1(data) == asset["blob"], "snapshot Git blob SHA1 drifted: {}".format(path))


def _read_canonical_json(path):
    data = Path(path).read_bytes()
    _assert(not data.startswith(b"\xef\xbb\xbf"), "JSON must not contain a BOM")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AssertionError("invalid provenance JSON: {}".format(exc))
    _assert(
        canonical_json_bytes(value) + b"\n" == data,
        "provenance JSON is not canonical",
    )
    return value


def verify_lock(lock_path, expected_assets):
    value = _read_canonical_json(lock_path)
    expected = {
        "schema": LOCK_SCHEMA,
        "source_repository": SOURCE_REPOSITORY,
        "source_commit": EXPECTED_SOURCE_COMMIT,
        "assets": [dict(asset) for asset in expected_assets],
    }
    _assert(set(value) == set(expected), "provenance lock keys are invalid")
    _assert(value == expected, "provenance lock does not match hard-coded truth")


def verify_selected_assets(source_git_dir, repo_root, lock_path, *, runner=None):
    verify_git_objects(
        source_git_dir,
        EXPECTED_SOURCE_COMMIT,
        [dict(asset) for asset in EXPECTED_ASSETS],
        runner=runner,
    )
    verify_snapshots(repo_root, [dict(asset) for asset in EXPECTED_ASSETS])
    verify_lock(lock_path, [dict(asset) for asset in EXPECTED_ASSETS])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-git-dir", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--lock", required=True)
    args = parser.parse_args(argv)
    try:
        verify_selected_assets(args.source_git_dir, args.repo_root, args.lock)
    except AssertionError as exc:
        parser.exit(1, "asset verification failed: {}\n".format(exc))
    print("SELECTED_WINDOWS_ASSETS_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
