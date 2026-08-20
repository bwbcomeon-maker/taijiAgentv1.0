"""Read-only Windows builder and product probes for the real adapter phase.

This module deliberately owns only the SSH/PowerShell command boundary.  It
does not know how to build a candidate, import product history, or mutate a
remote repository.  Those operations remain separate plan tasks and gates.
"""

import base64
import copy
import inspect
import json
import os
import subprocess
from pathlib import Path

from ..core.errors import PipelineError


SSH = "/usr/bin/ssh"
DEFAULT_CONNECT_TIMEOUT = "5"
WINDOWS_CACHE_SCHEMA = "taiji-windows-cache-observation/v1"
WINDOWS_HOST_SCHEMA = "taiji-windows-host-facts/v1"
ONLINE_SCHEMA = "taiji-package-online-doctor/v2"
PRODUCT_SCHEMA = "taiji-windows-product-probe/v1"


def powershell_argv(host_alias, powershell_path, script, ssh_config=None):
    """Return one safe SSH argv using one encoded PowerShell command."""

    encoded = base64.b64encode(str(script).encode("utf-16le")).decode("ascii")
    argv = [SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=" + DEFAULT_CONNECT_TIMEOUT]
    if ssh_config is not None:
        argv.extend(["-F", str(Path(ssh_config).expanduser().resolve())])
    argv.append(str(host_alias))
    remote = subprocess.list2cmdline(
        [
            str(powershell_path),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ]
    )
    argv.append(remote)
    return argv


def _ps_literal(value):
    return "'{}'".format(str(value).replace("'", "''"))


def builder_probe_script(target):
    """Render a read-only builder probe without a product-repository path."""

    remote_root = _ps_literal(target["remote_root"])
    cache_root = _ps_literal(target["cache_root"])
    minimum_bytes = int(target["minimum_free_gib"]) * 1024 * 1024 * 1024
    paths = {
        "git": target.get("git", r"C:\Program Files\Git\cmd\git.exe"),
        "tar": target.get("tar", r"C:\Windows\System32\tar.exe"),
        "node": target.get("node", r"C:\Program Files\nodejs\node.exe"),
        "npm": target.get("npm", r"C:\Program Files\nodejs\npm.cmd"),
        "python": target.get("python", r"D:\tw\cache\python-runtime\python.exe"),
        "iscc": target.get("iscc", r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        "powershell": target.get("powershell", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
    }
    path_assignments = "\n".join(
        "$path_{0} = {1}".format(name, _ps_literal(value))
        for name, value in sorted(paths.items())
    )
    return """$ErrorActionPreference = 'Stop'
{path_assignments}
$remoteRoot = {remote_root}
$cacheRoot = {cache_root}
$minimumBytes = {minimum_bytes}
$drive = Get-PSDrive -Name $cacheRoot.Substring(0, 1)
$driveInfo = New-Object System.IO.DriveInfo -ArgumentList ($cacheRoot.Substring(0, 1) + ':\\')
$cacheChecks = @(
  [ordered]@{{ name = 'npm-cache'; present = Test-Path -LiteralPath (Join-Path $cacheRoot 'npm') }},
  [ordered]@{{ name = 'electron-39.8.10-win32-x64'; present = Test-Path -LiteralPath (Join-Path $cacheRoot 'electron/electron-v39.8.10-win32-x64.zip') }},
  [ordered]@{{ name = 'private-python-runtime'; present = Test-Path -LiteralPath (Join-Path $cacheRoot 'python-runtime') }}
)
$toolChecks = @()
foreach ($pair in @(
  @{{name='powershell';path=$path_powershell}}, @{{name='git';path=$path_git}},
  @{{name='tar';path=$path_tar}}, @{{name='node';path=$path_node}},
  @{{name='npm';path=$path_npm}}, @{{name='python';path=$path_python}},
  @{{name='iscc';path=$path_iscc}}
)) {{
  $toolChecks += [ordered]@{{ name = $pair.name; path = $pair.path; present = Test-Path -LiteralPath $pair.path }}
}}
$blockers = @()
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {{ $blockers += 'WINDOWS_ARCHITECTURE_INVALID' }}
if ($driveInfo.DriveFormat -ne 'NTFS') {{ $blockers += 'WINDOWS_FILESYSTEM_INVALID' }}
if ($drive.Free -lt $minimumBytes) {{ $blockers += 'WINDOWS_FREE_SPACE_LOW' }}
if (@($toolChecks | Where-Object {{ -not $_.present }}).Count -gt 0) {{ $blockers += 'WINDOWS_TOOL_MISSING' }}
if (@($cacheChecks | Where-Object {{ -not $_.present }}).Count -gt 0) {{ $blockers += 'WINDOWS_CACHE_MISSING' }}
[ordered]@{{
  schema = 'taiji-windows-builder-doctor/v1'
  builder_status = if ($blockers.Count -eq 0) {{ 'BUILDER_READY' }} else {{ 'BLOCKED' }}
  host_alias = $env:COMPUTERNAME
  os = 'Windows'
  os_version = [Environment]::OSVersion.Version.ToString()
  architecture = $env:PROCESSOR_ARCHITECTURE
  powershell_version = $PSVersionTable.PSVersion.ToString()
  git_path = $path_git
  tar_path = $path_tar
  node_path = $path_node
  npm_path = $path_npm
  python_path = $path_python
  iscc_path = $path_iscc
  filesystem = $driveInfo.DriveFormat
  free_bytes = [int64]$drive.Free
  cache_root = $cacheRoot
  cache_checks = @($cacheChecks)
  remote_root_parent_exists = Test-Path -LiteralPath $remoteRoot
  blockers = @($blockers)
}} | ConvertTo-Json -Depth 12 -Compress
""".format(
        path_assignments=path_assignments,
        remote_root=remote_root,
        cache_root=cache_root,
        minimum_bytes=minimum_bytes,
    )


def product_probe_script(remote_root, branch, expected_tip, base_commit):
    """Render a read-only product Git identity probe."""

    return """$ErrorActionPreference = 'Stop'
$repo = {repo}
$branch = {branch}
$expectedTip = {tip}
$base = {base}
$head = (& git -C $repo rev-parse ('refs/heads/' + $branch)).Trim()
$clean = (@(& git -C $repo status --porcelain --untracked-files=all).Count -eq 0)
$basePresent = ((& git -C $repo cat-file -e ($base + '^{{commit}}')) -eq $null)
$tipPresent = ((& git -C $repo cat-file -e ($expectedTip + '^{{commit}}')) -eq $null)
$blockers = @()
if (-not $clean) {{ $blockers += 'PRODUCT_REPO_DIRTY' }}
if (-not $basePresent) {{ $blockers += 'PRODUCT_BASE_MISSING' }}
if (-not $tipPresent) {{ $blockers += 'PRODUCT_TIP_MISSING' }}
[ordered]@{{
  schema = '{schema}'
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
        repo=_ps_literal(remote_root),
        branch=_ps_literal(branch),
        tip=_ps_literal(expected_tip),
        base=_ps_literal(base_commit),
        schema=PRODUCT_SCHEMA,
    )


def _json_payload(payload, label):
    try:
        value = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise PipelineError("{} is not valid JSON: {}".format(label, exc), category="ONLINE_DOCTOR_BLOCKED")
    if not isinstance(value, dict):
        raise PipelineError("{} must be an object".format(label), category="ONLINE_DOCTOR_BLOCKED")
    return value


def parse_builder_probe(payload, minimum_free_gib=20):
    """Normalize a builder probe and derive readiness from observed fields."""

    result = _json_payload(payload, "builder probe")
    if result.get("schema") not in ("taiji-windows-builder-doctor/v1", ONLINE_SCHEMA):
        raise PipelineError("builder probe schema is invalid", category="ONLINE_DOCTOR_BLOCKED")
    blockers = list(result.get("blockers") or [])
    checks = result.get("cache_checks") or []
    if any(not isinstance(item, dict) or item.get("present") is not True for item in checks):
        if "WINDOWS_CACHE_MISSING" not in blockers:
            blockers.append("WINDOWS_CACHE_MISSING")
    free_bytes = result.get("free_bytes")
    if isinstance(free_bytes, int) and free_bytes < int(minimum_free_gib) * 1024 * 1024 * 1024:
        if "WINDOWS_FREE_SPACE_LOW" not in blockers:
            blockers.append("WINDOWS_FREE_SPACE_LOW")
    if result.get("architecture") not in (None, "AMD64"):
        if "WINDOWS_ARCHITECTURE_INVALID" not in blockers:
            blockers.append("WINDOWS_ARCHITECTURE_INVALID")
    if result.get("filesystem") not in (None, "NTFS"):
        if "WINDOWS_FILESYSTEM_INVALID" not in blockers:
            blockers.append("WINDOWS_FILESYSTEM_INVALID")
    result["blockers"] = blockers
    result["failure_categories"] = list(blockers)
    result["builder_status"] = "BUILDER_READY" if not blockers else "BLOCKED"
    return result


def parse_product_probe(payload):
    result = _json_payload(payload, "product probe")
    if result.get("schema") != PRODUCT_SCHEMA:
        raise PipelineError("product probe schema is invalid", category="PRODUCT_SOURCE_INVALID")
    required = {
        "schema", "host_alias", "product_repo", "product_branch", "product_commit",
        "product_clean", "base_present", "expected_tip_present", "blockers",
    }
    if set(result) != required:
        raise PipelineError("product probe fields are not exact", category="PRODUCT_SOURCE_INVALID")
    return result


def _invoke_runner(runner, argv):
    if runner is None:
        return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, check=False)
    try:
        signature = inspect.signature(runner)
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if accepts_kwargs or "cwd" in signature.parameters:
            return runner(argv, cwd=Path.cwd(), environment=os.environ.copy(), timeout=30)
    except (TypeError, ValueError):
        pass
    return runner(argv)


class WindowsSshTransport:
    """Transport seam for the later real Windows phase."""

    def __init__(self, target, *, ssh_config, command_runner):
        self.target = copy.deepcopy(target)
        self.ssh_config = ssh_config
        self.command_runner = command_runner

    def _run_powershell(self, script):
        argv = powershell_argv(
            self.target["host_alias"],
            self.target["powershell"],
            script,
            self.ssh_config,
        )
        result = _invoke_runner(self.command_runner, argv)
        if getattr(result, "returncode", 0) != 0:
            raise PipelineError("Windows read-only probe failed", category="BUILDER_UNREACHABLE")
        return getattr(result, "stdout", result)

    def online_doctor(self):
        payload = self._run_powershell(builder_probe_script(self.target))
        return parse_builder_probe(payload, self.target["minimum_free_gib"])

    def probe_product_source(self, branch, expected_tip, base_commit):
        payload = self._run_powershell(
            product_probe_script(self.target["remote_root"], branch, expected_tip, base_commit)
        )
        return parse_product_probe(payload)

    def build(self, plan, input_files):
        del plan, input_files
        raise PipelineError("real Windows build is gated by Plan 4 R4", category="BUILD_NOT_AUTHORIZED")

    def fetch(self, plan, staging_dir):
        del plan, staging_dir
        raise PipelineError("real Windows fetch is not enabled before R4", category="FETCH_NOT_AUTHORIZED")
