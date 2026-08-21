#!/usr/bin/python3
"""Import and audit the fixed Windows product-source bundle.

The helper has deliberately explicit subcommands.  It never infers an import,
never changes a product repository, and never overwrites an archive ref.
"""

from __future__ import print_function

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packaging.pipeline.adapters.windows_ssh import parse_product_probe, powershell_argv
from packaging.pipeline.core.errors import PipelineError
from packaging.pipeline.core.registry import create_adapter


HOST_ALIAS = "windows-direct"
PRODUCT_REPO = r"D:\tw\source\taijiAgentv1.0"
PRODUCT_BRANCH = "codex/windows-local"
PRODUCT_GIT = r"C:\Program Files\Git\cmd\git.exe"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
BASE_COMMIT = "5364233e1297e5f2837382823d4e35a0d114aba7"
TIP_COMMIT = "89954e96d23cf43f266197813eb283475d5ff7e1"
IMPORT_SCHEMA = "taiji-windows-product-import/v1"
IMPORT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
ALLOWED_PATHS = [
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


def _fail(message, category="PRODUCT_IMPORT_INVALID"):
    raise PipelineError(message, category=category)


def _canonical_bytes(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    try:
        return _sha256_bytes(Path(path).read_bytes())
    except OSError as exc:
        _fail("cannot read {}: {}".format(path, exc))


def _private_dir(path, create=False):
    path = Path(path).expanduser()
    if create:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=False)
        except FileExistsError:
            _fail("import staging already exists: {}".format(path), "IMPORT_STAGING_EXISTS")
        except OSError as exc:
            _fail("cannot create private import staging: {}".format(exc))
    try:
        metadata = path.lstat()
    except OSError as exc:
        _fail("import staging is unavailable: {}".format(exc))
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        _fail("import staging is not private: {}".format(path))
    return path


def _private_file(path, label):
    path = Path(path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        _fail("{} is unavailable: {}".format(label, exc))
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        _fail("{} is not a private regular file".format(label))
    return path


def _git_env():
    env = os.environ.copy()
    for key in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        env.pop(key, None)
    env.update({
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
    })
    return env


def _run(argv, *, cwd=None, input_bytes=None, check=True):
    result = subprocess.run(
        list(argv), cwd=str(cwd) if cwd is not None else None,
        input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_git_env(), check=False,
    )
    if check and result.returncode != 0:
        _fail("command failed: {}: {}".format(" ".join(argv), result.stderr.decode("utf-8", "replace").strip()), "PRODUCT_IMPORT_INVALID")
    return result


def run_git(repo, *args, input_bytes=None, check=True):
    return _run(["/usr/bin/git", "-C", str(Path(repo).resolve())] + list(args), input_bytes=input_bytes, check=check)


def _git_text(repo, *args):
    return run_git(repo, *args).stdout.decode("utf-8").strip()


def _commit_sha(value, label):
    if not isinstance(value, str) or COMMIT_RE.fullmatch(value) is None:
        _fail("{} is not a full commit".format(label))
    return value


def _sidecar(bundle, sidecar):
    bundle = _private_file(bundle, "bundle")
    sidecar = _private_file(sidecar, "sidecar")
    digest = _sha256_file(bundle)
    expected = "{}  {}\n".format(digest, bundle.name).encode("utf-8")
    try:
        actual = sidecar.read_bytes()
    except OSError as exc:
        _fail("cannot read sidecar: {}".format(exc))
    if actual != expected:
        _fail("bundle sidecar does not match bundle", "INPUT_VERIFICATION_FAILED")
    return digest


def _bundle_basename(tip):
    return "windows-product-{}.bundle".format(tip)


def _raw_changes(repo, parent, commit):
    result = run_git(repo, "diff-tree", "--no-commit-id", "--raw", "-r", "--no-renames", "-z", parent, commit)
    tokens = result.stdout.split(b"\0")
    changes = []
    index = 0
    while index < len(tokens) and tokens[index]:
        metadata = tokens[index].decode("utf-8")
        path = tokens[index + 1].decode("utf-8")
        index += 2
        fields = metadata.split()
        if len(fields) != 5 or not fields[0].startswith(":"):
            _fail("commit diff record is malformed")
        old_mode = fields[0][1:]
        new_mode, old_blob, new_blob, status = fields[1:]
        status = status[0]
        if status not in ("A", "M", "D") or path not in ALLOWED_PATHS:
            _fail("commit changes a forbidden path or status: {} {}".format(status, path), "WINDOWS_PRODUCT_PATH_INVALID")
        for mode in (old_mode, new_mode):
            if mode not in ("000000", "100644", "100755"):
                _fail("commit changes an unsupported file mode: {}".format(mode), "WINDOWS_PRODUCT_MODE_INVALID")
        old_value = None if old_mode == "000000" else old_mode
        new_value = None if new_mode == "000000" else new_mode
        content_sha = None
        if status != "D":
            content = run_git(repo, "cat-file", "blob", new_blob).stdout
            content_sha = _sha256_bytes(content)
        changes.append({
            "path": path, "status": status, "old_mode": old_value, "new_mode": new_value,
            "old_blob": None if old_blob == "0" * 40 else old_blob,
            "new_blob": None if new_blob == "0" * 40 else new_blob,
            "sha256": content_sha,
        })
    if not changes:
        _fail("commit changes no allowed path", "WINDOWS_PRODUCT_PATH_INVALID")
    return changes


def _patch_id(repo, commit):
    shown = run_git(repo, "show", "--pretty=format:", "--binary", commit).stdout
    result = _run(["/usr/bin/git", "patch-id", "--stable"], input_bytes=shown)
    first = result.stdout.decode("ascii").split()
    if not first or re.fullmatch(r"[0-9a-f]{40}", first[0]) is None:
        _fail("patch-id is invalid")
    return first[0]


def _inventory(repo, base, tip):
    base = _commit_sha(base, "base")
    tip = _commit_sha(tip, "tip")
    if run_git(repo, "merge-base", "--is-ancestor", base, tip, check=False).returncode != 0:
        _fail("base is not an ancestor of tip", "WINDOWS_PRODUCT_BASE_INVALID")
    commits = _git_text(repo, "rev-list", "--reverse", "--topo-order", "{}..{}".format(base, tip)).splitlines()
    if not commits:
        _fail("product range contains no commits", "WINDOWS_PRODUCT_RANGE_EMPTY")
    result = []
    for commit in commits:
        parents = _git_text(repo, "show", "-s", "--format=%P", commit).split()
        if len(parents) != 1:
            _fail("merge commit is not importable: {}".format(commit), "WINDOWS_PRODUCT_MERGE_COMMIT")
        result.append({
            "old_sha": commit,
            "parents": parents,
            "subject": _git_text(repo, "show", "-s", "--format=%s", commit),
            "patch_id": _patch_id(repo, commit),
            "paths": _raw_changes(repo, parents[0], commit),
        })
    return result


def _bundle_repo(import_dir, bundle):
    verify_root = Path(tempfile.mkdtemp(prefix=".bundle-verify-", dir=str(import_dir)))
    verify_root.chmod(0o700)
    bare = verify_root / "repo.git"
    try:
        _run(["/usr/bin/git", "init", "--bare", str(bare)])
        _run(["/usr/bin/git", "-C", str(bare), "bundle", "verify", str(bundle)])
        _run(["/usr/bin/git", "-C", str(bare), "fetch", str(bundle), "refs/heads/{}".format(PRODUCT_BRANCH)])
        return verify_root, bare
    except Exception:
        shutil.rmtree(str(verify_root), ignore_errors=True)
        raise


def verify_import(import_dir, base, tip):
    import_dir = _private_dir(import_dir)
    base = _commit_sha(base, "base")
    tip = _commit_sha(tip, "tip")
    bundle = import_dir / _bundle_basename(tip)
    sidecar = import_dir / (bundle.name + ".sha256")
    digest = _sidecar(bundle, sidecar)
    verify_root, bare = _bundle_repo(import_dir, bundle)
    try:
        observed_tip = _git_text(bare, "rev-parse", "FETCH_HEAD")
        if observed_tip != tip:
            _fail("bundle tip does not match expected tip", "WINDOWS_PRODUCT_TIP_INVALID")
        commits = _inventory(bare, base, tip)
    finally:
        shutil.rmtree(str(verify_root), ignore_errors=True)
    manifest = {
        "schema": IMPORT_SCHEMA,
        "import_id": import_dir.name,
        "host_alias": HOST_ALIAS,
        "product_repo": PRODUCT_REPO,
        "base_commit": base,
        "tip_commit": tip,
        "bundle": {
            "basename": bundle.name, "bytes": bundle.stat().st_size,
            "sha256": digest, "path": str(bundle.resolve()),
        },
        "sidecar": {
            "basename": sidecar.name, "bytes": sidecar.stat().st_size,
            "sha256": _sha256_file(sidecar),
        },
        "allowed_paths": list(ALLOWED_PATHS),
        "commits": commits,
        "verified_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    manifest_path = import_dir / "product-import.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        _fail("product-import.json already exists", "IMPORT_MANIFEST_EXISTS")
    _write_private_json(manifest_path, manifest)
    return manifest


def _write_private_json(path, value):
    path = Path(path)
    temporary = path.with_name(".{}-{}.tmp".format(path.name, os.getpid()))
    if temporary.exists() or temporary.is_symlink() or path.exists() or path.is_symlink():
        _fail("refusing to overwrite manifest: {}".format(path), "IMPORT_MANIFEST_EXISTS")
    data = _canonical_bytes(value) + b"\n"
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_manifest(path):
    path = _private_file(path, "manifest")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        _fail("manifest is invalid: {}".format(exc))
    if not isinstance(value, dict) or value.get("schema") != IMPORT_SCHEMA:
        _fail("manifest schema is invalid")
    return value


def install_ref(manifest_path, repo, ref):
    manifest = _load_manifest(manifest_path)
    tip = _commit_sha(manifest.get("tip_commit"), "manifest tip")
    expected_ref = "refs/archive/windows-product/{}".format(tip)
    if ref != expected_ref:
        _fail("archive ref is not the exact tip ref", "WINDOWS_PRODUCT_REF_INVALID")
    bundle = _private_file(manifest["bundle"]["path"], "bundle")
    if _sha256_file(bundle) != manifest["bundle"]["sha256"]:
        _fail("bundle changed after verification", "INPUT_VERIFICATION_FAILED")
    run_git(repo, "fetch", str(bundle), "refs/heads/{}".format(PRODUCT_BRANCH))
    fetched_tip = _git_text(repo, "rev-parse", "FETCH_HEAD")
    if fetched_tip != tip:
        _fail("fetched bundle tip does not match manifest", "WINDOWS_PRODUCT_TIP_INVALID")
    current = run_git(repo, "rev-parse", "--verify", ref, check=False)
    if current.returncode == 0:
        current_tip = current.stdout.decode("ascii").strip()
        if current_tip != tip:
            _fail("archive ref points to a different tip", "WINDOWS_PRODUCT_REF_EXISTS")
        return current_tip
    update = run_git(repo, "update-ref", ref, tip, "", check=False)
    if update.returncode != 0:
        _fail("archive ref changed during install", "WINDOWS_PRODUCT_REF_RACE")
    return tip


def _load_target(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        adapter = create_adapter("windows-x64")
        return adapter.validate_target(value)
    except (OSError, UnicodeError, ValueError, PipelineError) as exc:
        if isinstance(exc, PipelineError):
            raise
        _fail("target config is invalid: {}".format(exc), "TARGET_INVALID")


def _encoded_product_bundle_script(product_repo, bundle_path, git_path):
    def quote(value):
        return "'{}'".format(str(value).replace("'", "''"))
    return """$ErrorActionPreference = 'Stop'
$git = {git}
$repo = {repo}
$bundle = {bundle}
$parent = Split-Path -Parent $bundle
if (Test-Path -LiteralPath $parent) {{ throw 'import run already exists' }}
New-Item -ItemType Directory -Path $parent | Out-Null
& $git -C $repo bundle create $bundle refs/heads/{branch}
$sha = (Get-FileHash -LiteralPath $bundle -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText($bundle + '.sha256', ($sha + '  ' + [IO.Path]::GetFileName($bundle) + [char]10), [Text.UTF8Encoding]::new($false))
""".format(git=quote(git_path), repo=quote(product_repo), bundle=quote(bundle_path), branch=PRODUCT_BRANCH)


def _product_probe_script(product_repo, branch, expected_tip, base_commit, git_path):
    def quote(value):
        return "'{}'".format(str(value).replace("'", "''"))
    return """$ErrorActionPreference = 'Stop'
$git = {git}
$repo = {repo}
$branch = {branch}
$expectedTip = {tip}
$base = {base}
$head = (& $git -C $repo rev-parse ('refs/heads/' + $branch)).Trim()
$clean = (@(& $git -C $repo status --porcelain --untracked-files=all).Count -eq 0)
$basePresent = ((& $git -C $repo cat-file -e ($base + '^{{commit}}')) -eq $null)
$tipPresent = ((& $git -C $repo cat-file -e ($expectedTip + '^{{commit}}')) -eq $null)
$blockers = @()
if (-not $clean) {{ $blockers += 'PRODUCT_REPO_DIRTY' }}
if (-not $basePresent) {{ $blockers += 'PRODUCT_BASE_MISSING' }}
if (-not $tipPresent) {{ $blockers += 'PRODUCT_TIP_MISSING' }}
[ordered]@{{
  schema = 'taiji-windows-product-probe/v1'
  host_alias = $env:COMPUTERNAME
  product_repo = $repo
  product_branch = $branch
  product_commit = $head
  product_clean = $clean
  base_present = $basePresent
  expected_tip_present = $tipPresent
  blockers = @($blockers)
}} | ConvertTo-Json -Depth 8 -Compress
""".format(
        git=quote(git_path), repo=quote(product_repo), branch=quote(branch),
        tip=quote(expected_tip), base=quote(base_commit),
    )


def _scp_argv(host, remote_path, local_path, ssh_config=None):
    argv = ["/usr/bin/scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    if ssh_config is not None:
        argv.extend(["-F", str(Path(ssh_config).expanduser().resolve())])
    # OpenSSH scp treats backslashes in a Windows source path as escapes.
    # Forward slashes are accepted by Windows OpenSSH and preserve the drive.
    scp_remote_path = str(remote_path).replace("\\", "/")
    argv.extend(["{}:{}".format(host, scp_remote_path), str(local_path)])
    return argv


def probe(args):
    target = _load_target(args.target_config)
    if args.host != target["host_alias"]:
        _fail("probe host differs from target config", "TARGET_INVALID")
    from packaging.pipeline.adapters.windows_ssh import WindowsSshTransport
    transport = WindowsSshTransport(target, ssh_config=args.ssh_config, command_runner=None)
    result = parse_product_probe(
        transport._run_powershell(
            _product_probe_script(
                args.product_repo,
                args.expected_branch,
                args.expected_tip,
                args.expected_base,
                target["git"],
            )
        )
    )
    if (
        result.get("product_branch") != args.expected_branch
        or result.get("product_commit") != args.expected_tip
        or not result.get("base_present")
        or not result.get("expected_tip_present")
        or not result.get("product_clean")
        or result.get("blockers")
    ):
        _fail("product source probe does not match expected identity", "SOURCE_DRIFT")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def fetch(args):
    target = _load_target(args.target_config)
    if args.host != target["host_alias"]:
        _fail("fetch host differs from target config", "TARGET_INVALID")
    if IMPORT_ID_RE.fullmatch(args.import_id) is None:
        _fail("import id is invalid", "IMPORT_ID_INVALID")
    import_path = Path(args.state_root).expanduser() / "imports" / args.import_id
    import_was_present = import_path.exists() or import_path.is_symlink()
    import_dir = _private_dir(import_path, create=not import_was_present)
    tip = _commit_sha(args.tip, "tip")
    _commit_sha(args.base, "base")
    remote_dir = target["remote_root"] + "\\" + tip + "\\" + args.import_id + "\\import"
    bundle_name = _bundle_basename(tip)
    remote_bundle = remote_dir + "\\" + bundle_name
    script = _encoded_product_bundle_script(args.product_repo, remote_bundle, target["git"])
    ssh = powershell_argv(target["host_alias"], target["powershell"], script, args.ssh_config)
    if not import_was_present:
        _run(ssh, check=True)
    local_bundle = import_dir / bundle_name
    local_sidecar = import_dir / (bundle_name + ".sha256")
    for remote_path, local_path, label in (
        (remote_bundle, local_bundle, "bundle"),
        (remote_bundle + ".sha256", local_sidecar, "sidecar"),
    ):
        if local_path.exists() or local_path.is_symlink():
            metadata = local_path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.getuid()
            ):
                _fail("{} staging file is not a private regular file".format(label))
            local_path.chmod(0o600)
            continue
        _run(_scp_argv(target["host_alias"], remote_path, local_path, args.ssh_config), check=True)
        local_path.chmod(0o600)
    print(json.dumps({"import_dir": str(import_dir), "bundle": bundle_name}, sort_keys=True))


def inventory(args):
    manifest = _load_manifest(args.manifest)
    print(json.dumps(manifest["commits"], ensure_ascii=False, sort_keys=True))


def build_parser():
    parser = argparse.ArgumentParser(description="Gated Windows product bundle import helper")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("probe", allow_abbrev=False)
    p.add_argument("--target-config", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--product-repo", required=True)
    p.add_argument("--expected-branch", required=True)
    p.add_argument("--expected-tip", required=True)
    p.add_argument("--expected-base", required=True)
    p.add_argument("--ssh-config")
    p.set_defaults(handler=probe)
    f = sub.add_parser("fetch", allow_abbrev=False)
    f.add_argument("--target-config", required=True)
    f.add_argument("--import-id", required=True)
    f.add_argument("--host", required=True)
    f.add_argument("--product-repo", required=True)
    f.add_argument("--base", required=True)
    f.add_argument("--tip", required=True)
    f.add_argument("--state-root", required=True)
    f.add_argument("--ssh-config")
    f.set_defaults(handler=fetch)
    v = sub.add_parser("verify", allow_abbrev=False)
    v.add_argument("--import-dir", required=True)
    v.add_argument("--base", required=True)
    v.add_argument("--tip", required=True)
    v.set_defaults(handler=lambda a: print(json.dumps(verify_import(a.import_dir, a.base, a.tip), ensure_ascii=False, sort_keys=True)))
    i = sub.add_parser("install-ref", allow_abbrev=False)
    i.add_argument("--manifest", required=True)
    i.add_argument("--repo", required=True)
    i.add_argument("--ref", required=True)
    i.set_defaults(handler=lambda a: print(install_ref(a.manifest, a.repo, a.ref)))
    n = sub.add_parser("inventory", allow_abbrev=False)
    n.add_argument("--manifest", required=True)
    n.set_defaults(handler=inventory)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
        return 0
    except PipelineError as exc:
        print("BLOCKED\t{}\t{}".format(exc.category, exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
