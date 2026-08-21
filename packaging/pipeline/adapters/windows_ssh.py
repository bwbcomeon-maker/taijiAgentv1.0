"""Read-only Windows builder and product probes for the real adapter phase.

This module deliberately owns only the SSH/PowerShell command boundary.  It
does not know how to build a candidate, import product history, or mutate a
remote repository.  Those operations remain separate plan tasks and gates.
"""

import base64
import copy
import gzip
import hashlib
import inspect
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from ..core.errors import PipelineError
from ..core.models import canonical_json_sha256, validate_v2_state
from ..core.state import RunStateStore


SSH = "/usr/bin/ssh"
SCP = "/usr/bin/scp"
DEFAULT_CONNECT_TIMEOUT = "5"
WINDOWS_COMMAND_TIMEOUT_SECONDS = 3600
WINDOWS_CACHE_SCHEMA = "taiji-windows-cache-observation/v1"
WINDOWS_HOST_SCHEMA = "taiji-windows-host-facts/v1"
ONLINE_SCHEMA = "taiji-package-online-doctor/v2"
PRODUCT_SCHEMA = "taiji-windows-product-probe/v1"
REAL_BUILD_STAGES = [
    "online-doctor",
    "create-remote-run",
    "transfer-input",
    "remote-input-verify",
    "remote-candidate-build",
    "fetch-review",
    "fetch-log",
    "local-review-verify",
    "publish",
]
REAL_FETCH_STAGES = [
    "fetch-review",
    "fetch-log",
    "local-review-verify",
    "publish",
]
ROOT = Path(__file__).resolve().parents[3]
CACHE_REQUIREMENTS_PATH = ROOT / "packaging/windows/cache-requirements.json"
WINDOWS_SCRIPTS_ROOT = ROOT / "packaging" / "windows"
FULL_ONLINE_FIELDS = {
    "schema",
    "builder_status",
    "host_alias",
    "os",
    "os_version",
    "architecture",
    "powershell_version",
    "git_path",
    "tar_path",
    "node_path",
    "npm_path",
    "python_path",
    "iscc_path",
    "filesystem",
    "free_bytes",
    "cache_root",
    "cache_checks",
    "cache_requirements_sha256",
    "cache_observation",
    "cache_observation_sha256",
    "host_facts",
    "host_facts_sha256",
    "remote_root_parent_exists",
    "blockers",
    "failure_categories",
}


def powershell_argv(host_alias, powershell_path, script, ssh_config=None):
    """Return one safe SSH argv using the target PowerShell absolute path + EncodedCommand."""

    script_text = str(script)
    payload = base64.b64encode(gzip.compress(script_text.encode("utf-8"))).decode("ascii")
    loader = """
$payload = '{payload}'
$memory = [System.IO.MemoryStream]::new([Convert]::FromBase64String($payload))
try {{
  $gzip = [System.IO.Compression.GzipStream]::new(
    $memory,
    [System.IO.Compression.CompressionMode]::Decompress
  )
  try {{
    $reader = [System.IO.StreamReader]::new($gzip, [System.Text.Encoding]::UTF8)
    try {{
      $script = $reader.ReadToEnd()
    }} finally {{
      $reader.Dispose()
    }}
  }} finally {{
    $gzip.Dispose()
  }}
}} finally {{
  $memory.Dispose()
}}
& ([scriptblock]::Create($script))
""".strip().format(payload=payload)
    encoded = base64.b64encode(loader.encode("utf-16le")).decode("ascii")
    remote_args = [
        str(powershell_path),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encoded,
    ]
    argv = [SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=" + DEFAULT_CONNECT_TIMEOUT]
    if ssh_config is not None:
        argv.extend(["-F", str(Path(ssh_config).expanduser().resolve())])
    argv.append(str(host_alias))
    argv.append(subprocess.list2cmdline(remote_args))
    return argv


def _powershell_stdin_argv(host_alias, powershell_path, ssh_config=None):
    remote_args = [
        str(powershell_path),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "-",
    ]
    argv = [SSH, "-o", "BatchMode=yes", "-o", "ConnectTimeout=" + DEFAULT_CONNECT_TIMEOUT]
    if ssh_config is not None:
        argv.extend(["-F", str(Path(ssh_config).expanduser().resolve())])
    argv.append(str(host_alias))
    argv.append(subprocess.list2cmdline(remote_args))
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
    requirements = json.loads(CACHE_REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    requirements_bytes = json.dumps(
        requirements, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    replacements = {
        "__PATH_ASSIGNMENTS__": path_assignments,
        "__REMOTE_ROOT__": remote_root,
        "__CACHE_ROOT__": cache_root,
        "__MINIMUM_BYTES__": str(minimum_bytes),
        "__REQUIREMENTS_B64__": base64.b64encode(requirements_bytes).decode("ascii"),
    }
    script = r"""$ErrorActionPreference = 'Stop'
__PATH_ASSIGNMENTS__
$remoteRoot = __REMOTE_ROOT__
$cacheRoot = __CACHE_ROOT__
$minimumBytes = __MINIMUM_BYTES__

function ConvertTo-CanonicalValue {
  param([object]$Value)
  if ($null -eq $Value) { return $null }
  if ($Value -is [System.Collections.IDictionary]) {
    $ordered = [ordered]@{}
    foreach ($key in @($Value.Keys | ForEach-Object { [string]$_ } | Sort-Object)) {
      $ordered[$key] = ConvertTo-CanonicalValue $Value[$key]
    }
    return $ordered
  }
  if ($Value -is [System.Management.Automation.PSCustomObject]) {
    $ordered = [ordered]@{}
    foreach ($key in @($Value.PSObject.Properties.Name | Sort-Object)) {
      $ordered[$key] = ConvertTo-CanonicalValue $Value.$key
    }
    return $ordered
  }
  if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
    $items = @()
    foreach ($item in @($Value)) {
      $items += ,(ConvertTo-CanonicalValue $item)
    }
    return ,$items
  }
  return $Value
}

function ConvertTo-CanonicalJson {
  param([object]$Value)
  $canonical = ConvertTo-CanonicalValue $Value
  return (ConvertTo-Json -InputObject $canonical -Depth 64 -Compress)
}

function Get-Sha256Bytes {
  param([byte[]]$Bytes)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
  } finally {
    $sha.Dispose()
  }
}

function Get-Sha256Path {
  param([string]$Path)
  $stream = [System.IO.File]::OpenRead($Path)
  try {
    return Get-Sha256Stream $stream
  } finally {
    $stream.Dispose()
  }
}

function Get-Sha256Stream {
  param([System.IO.Stream]$Stream)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    if ($Stream.CanSeek) { $Stream.Position = 0 }
    return ([BitConverter]::ToString($sha.ComputeHash($Stream))).Replace('-', '').ToLowerInvariant()
  } finally {
    $sha.Dispose()
  }
}

function Test-SafePosixPath {
  param([string]$Path)
  if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
  if ($Path.IndexOf([char]0) -ge 0 -or $Path.Contains('\') -or $Path.Contains(':')) { return $false }
  if ($Path.StartsWith('/') -or $Path.EndsWith('/')) { return $false }
  if ($Path.Normalize([System.Text.NormalizationForm]::FormC) -cne $Path) { return $false }
  foreach ($part in $Path.Split('/')) {
    if ([string]::IsNullOrEmpty($part) -or $part -eq '.' -or $part -eq '..') { return $false }
  }
  return $true
}

function Normalize-ZipMemberName {
  param([string]$Name)
  return $Name.Replace('\', '/')
}

function Test-SafeZipMemberName {
  param([string]$Name)
  if ([string]::IsNullOrEmpty($Name) -or $Name.IndexOf([char]0) -ge 0) { return $false }
  $candidate = Normalize-ZipMemberName $Name
  if ($candidate.EndsWith('/')) {
    $candidate = $candidate.Substring(0, $candidate.Length - 1)
  }
  if (-not (Test-SafePosixPath $candidate)) { return $false }
  foreach ($part in $candidate.Split('/')) {
    if ($part.EndsWith('.') -or $part.EndsWith(' ')) { return $false }
  }
  return $true
}

function Get-PathIdentity {
  param([string]$Path)
  return $Path.Normalize([System.Text.NormalizationForm]::FormC).ToLowerInvariant()
}

function Compare-ByteArrays {
  param([byte[]]$Left, [byte[]]$Right)
  $length = [Math]::Min($Left.Length, $Right.Length)
  for ($index = 0; $index -lt $length; $index++) {
    if ($Left[$index] -lt $Right[$index]) { return -1 }
    if ($Left[$index] -gt $Right[$index]) { return 1 }
  }
  if ($Left.Length -lt $Right.Length) { return -1 }
  if ($Left.Length -gt $Right.Length) { return 1 }
  return 0
}

function Sort-MembersByUtf8 {
  param([object[]]$Members)
  $decorated = New-Object 'System.Collections.Generic.List[object]'
  $ordinal = 0
  foreach ($member in @($Members)) {
    $decorated.Add([pscustomobject]@{
      member = $member
      utf8_path = [System.Text.Encoding]::UTF8.GetBytes([string]$member.path)
      ordinal = $ordinal
    })
    $ordinal += 1
  }
  $comparison = [System.Comparison[object]]{
    param($left, $right)
    $result = Compare-ByteArrays $left.utf8_path $right.utf8_path
    if ($result -ne 0) { return $result }
    if ($left.ordinal -lt $right.ordinal) { return -1 }
    if ($left.ordinal -gt $right.ordinal) { return 1 }
    return 0
  }
  $decorated.Sort($comparison)
  $sorted = [Array]::CreateInstance([object], $decorated.Count)
  for ($index = 0; $index -lt $decorated.Count; $index++) {
    $sorted[$index] = $decorated[$index].member
  }
  return ,$sorted
}

function New-MissingCacheResult {
  param([object]$Entry)
  return [ordered]@{ present = $false; entry = $Entry }
}

function Get-CacheEntry {
  param([object]$Requirement)
  $relativePath = [string]$Requirement.relative_path
  $entry = [ordered]@{
    id = [string]$Requirement.id
    type = [string]$Requirement.type
    relative_path = $relativePath
    bytes = [int64]0
    sha256 = ('0' * 64)
    members = @()
  }
  if (-not (Test-SafePosixPath $relativePath)) { return New-MissingCacheResult $entry }
  $fullPath = Join-Path $cacheRoot ($relativePath -replace '/', '\')
  if (-not (Test-Path -LiteralPath $fullPath)) { return New-MissingCacheResult $entry }
  $item = Get-Item -LiteralPath $fullPath -Force
  if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    return New-MissingCacheResult $entry
  }

  if ([string]$Requirement.type -eq 'directory') {
    if ($item -isnot [System.IO.DirectoryInfo]) { return New-MissingCacheResult $entry }
    foreach ($requiredMember in @($Requirement.required_members)) {
      if (-not (Test-SafePosixPath ([string]$requiredMember))) { return New-MissingCacheResult $entry }
      $requiredPath = Join-Path $fullPath (([string]$requiredMember) -replace '/', '\')
      if (-not (Test-Path -LiteralPath $requiredPath)) { return New-MissingCacheResult $entry }
      $requiredItem = Get-Item -LiteralPath $requiredPath -Force
      if (($requiredItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        return New-MissingCacheResult $entry
      }
      if ($requiredItem -isnot [System.IO.FileInfo] -and $requiredItem -isnot [System.IO.DirectoryInfo]) {
        return New-MissingCacheResult $entry
      }
    }
    $seen = @{}
    $fileMembers = @()
    foreach ($child in @(Get-ChildItem -LiteralPath $fullPath -Recurse -Force)) {
      if (($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        return New-MissingCacheResult $entry
      }
      if ($child -is [System.IO.DirectoryInfo]) { continue }
      if ($child -isnot [System.IO.FileInfo]) { return New-MissingCacheResult $entry }
      $prefix = $fullPath.TrimEnd('\')
      $memberPath = $child.FullName.Substring($prefix.Length).TrimStart('\').Replace('\', '/')
      if (-not (Test-SafePosixPath $memberPath)) { return New-MissingCacheResult $entry }
      $identity = Get-PathIdentity $memberPath
      if ($seen.ContainsKey($identity)) { return New-MissingCacheResult $entry }
      $seen[$identity] = $true
      $fileMembers += ,[ordered]@{
        path = $memberPath
        bytes = [int64]$child.Length
        sha256 = Get-Sha256Path $child.FullName
      }
    }
    $members = Sort-MembersByUtf8 $fileMembers
    $totalBytes = [int64]0
    foreach ($member in @($members)) { $totalBytes += [int64]$member.bytes }
    $entry.bytes = $totalBytes
    $entry.sha256 = Get-Sha256Bytes ([System.Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson -Value (,$members))))
    $entry.members = @($members)
    return [ordered]@{ present = $true; entry = $entry }
  }

  if ([string]$Requirement.type -ne 'regular-file' -or $item -isnot [System.IO.FileInfo]) {
    return New-MissingCacheResult $entry
  }
  $archive = $null
  try {
    [void](Add-Type -AssemblyName System.IO.Compression.FileSystem)
    $archive = [System.IO.Compression.ZipFile]::OpenRead($fullPath)
    $seen = @{}
    foreach ($zipEntry in @($archive.Entries)) {
      $normalizedName = Normalize-ZipMemberName $zipEntry.FullName
      if (-not (Test-SafeZipMemberName $normalizedName)) { return New-MissingCacheResult $entry }
      $identityName = Get-PathIdentity $normalizedName.TrimEnd('/')
      if ($seen.ContainsKey($identityName)) { return New-MissingCacheResult $entry }
      $seen[$identityName] = $true
    }
    $zipMembers = @()
    foreach ($requiredMember in @($Requirement.required_members)) {
      $requiredNormalized = Normalize-ZipMemberName ([string]$requiredMember)
      if (-not (Test-SafePosixPath $requiredNormalized)) { return New-MissingCacheResult $entry }
      $matches = @(
        $archive.Entries | Where-Object {
          (Normalize-ZipMemberName ([string]$_.FullName)) -eq $requiredNormalized
        }
      )
      if ($matches.Count -ne 1 -or (Normalize-ZipMemberName $matches[0].FullName).EndsWith('/')) {
        return New-MissingCacheResult $entry
      }
      $stream = $matches[0].Open()
      try {
        $zipMembers += ,[ordered]@{
          path = [string]$requiredNormalized
          bytes = [int64]$matches[0].Length
          sha256 = Get-Sha256Stream $stream
        }
      } finally {
        $stream.Dispose()
      }
    }
    $entry.bytes = [int64]$item.Length
    $entry.sha256 = Get-Sha256Path $fullPath
    $entry.members = Sort-MembersByUtf8 $zipMembers
    return [ordered]@{ present = $true; entry = $entry }
  } catch {
    return New-MissingCacheResult $entry
  } finally {
    if ($null -ne $archive) { $archive.Dispose() }
  }
}

$requirements = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__REQUIREMENTS_B64__')) | ConvertFrom-Json
$requirementsSha256 = Get-Sha256Bytes ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson -Value $requirements)))
$cacheChecks = @()
$observedEntries = @()
$cacheMissing = $false
foreach ($requirement in @($requirements.entries)) {
  $observed = Get-CacheEntry $requirement
  $cacheChecks += ,[ordered]@{
    name = [string]$requirement.id
    present = [bool]$observed.present
  }
  if (-not $observed.present) { $cacheMissing = $true }
  $observedEntries += ,$observed.entry
}

$driveLetter = $cacheRoot.Substring(0, 1)
$drive = Get-PSDrive -Name $driveLetter
$driveInfo = New-Object System.IO.DriveInfo -ArgumentList ($driveLetter + ':\')
$toolChecks = @()
foreach ($pair in @(
  @{name='powershell';path=$path_powershell}, @{name='git';path=$path_git},
  @{name='tar';path=$path_tar}, @{name='node';path=$path_node},
  @{name='npm';path=$path_npm}, @{name='python';path=$path_python},
  @{name='iscc';path=$path_iscc}
)) {
  $toolChecks += ,[ordered]@{
    name = $pair.name
    path = $pair.path
    present = Test-Path -LiteralPath $pair.path
  }
}
$remoteRootParent = Split-Path -Parent $remoteRoot
$remoteRootParentExists = Test-Path -LiteralPath $remoteRootParent
$blockers = @()
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { $blockers += 'WINDOWS_ARCHITECTURE_INVALID' }
if ($driveInfo.DriveFormat -ne 'NTFS') { $blockers += 'WINDOWS_FILESYSTEM_INVALID' }
if ($drive.Free -lt $minimumBytes) { $blockers += 'WINDOWS_FREE_SPACE_LOW' }
if (@($toolChecks | Where-Object { -not $_.present }).Count -gt 0) { $blockers += 'WINDOWS_TOOL_MISSING' }
if ($cacheMissing) { $blockers += 'WINDOWS_CACHE_MISSING' }
if (-not $remoteRootParentExists) { $blockers += 'WINDOWS_REMOTE_ROOT_PARENT_MISSING' }

$observation = [ordered]@{
  schema = 'taiji-windows-cache-observation/v1'
  target_id = 'windows-x64'
  requirements_sha256 = $requirementsSha256
  cache_root = $cacheRoot
  entries = @($observedEntries)
  observed_at = [DateTime]::UtcNow.ToString('o')
}
$observationIdentity = [ordered]@{}
foreach ($key in @('schema', 'target_id', 'requirements_sha256', 'cache_root', 'entries')) {
  $observationIdentity[$key] = $observation[$key]
}
$cacheObservationSha256 = Get-Sha256Bytes ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson -Value $observationIdentity)))
$hostFacts = [ordered]@{
  schema = 'taiji-windows-host-facts/v1'
  host_alias = $env:COMPUTERNAME
  os = 'Windows'
  os_version = [Environment]::OSVersion.Version.ToString()
  architecture = $env:PROCESSOR_ARCHITECTURE
  filesystem = $driveInfo.DriveFormat
  powershell_version = $PSVersionTable.PSVersion.ToString()
}
$hostFactsSha256 = Get-Sha256Bytes ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson -Value $hostFacts)))

[ordered]@{
  schema = 'taiji-package-online-doctor/v2'
  builder_status = if ($blockers.Count -eq 0) { 'BUILDER_READY' } else { 'BLOCKED' }
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
  cache_requirements_sha256 = $requirementsSha256
  cache_observation = $observation
  cache_observation_sha256 = $cacheObservationSha256
  host_facts = $hostFacts
  host_facts_sha256 = $hostFactsSha256
  remote_root_parent_exists = $remoteRootParentExists
  blockers = @($blockers)
  failure_categories = @($blockers)
} | ConvertTo-Json -Depth 64 -Compress
"""
    for placeholder, value in replacements.items():
        script = script.replace(placeholder, value)
    return script


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
    if result.get("schema") == ONLINE_SCHEMA:
        if set(result) != FULL_ONLINE_FIELDS:
            raise PipelineError(
                "online doctor fields are not exact",
                category="ONLINE_DOCTOR_BLOCKED",
            )
        sha256_values = (
            result["cache_requirements_sha256"],
            result["cache_observation_sha256"],
            result["host_facts_sha256"],
        )
        if any(
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in sha256_values
        ):
            raise PipelineError("online doctor SHA is invalid", category="ONLINE_DOCTOR_BLOCKED")
        try:
            requirements = json.loads(CACHE_REQUIREMENTS_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise PipelineError(
                "Windows cache requirements are unreadable: {}".format(exc),
                category="ONLINE_DOCTOR_BLOCKED",
            )
        if canonical_json_sha256(requirements) != result["cache_requirements_sha256"]:
            raise PipelineError(
                "Windows cache requirements identity drifted",
                category="ONLINE_DOCTOR_BLOCKED",
            )

        observation = result["cache_observation"]
        observation_keys = {
            "schema", "target_id", "requirements_sha256", "cache_root", "entries", "observed_at",
        }
        if not isinstance(observation, dict) or set(observation) != observation_keys:
            raise PipelineError("cache observation fields are not exact", category="ONLINE_DOCTOR_BLOCKED")
        if (
            observation["schema"] != WINDOWS_CACHE_SCHEMA
            or observation["target_id"] != "windows-x64"
            or observation["requirements_sha256"] != result["cache_requirements_sha256"]
            or not isinstance(observation["cache_root"], str)
            or not isinstance(observation["observed_at"], str)
            or not observation["observed_at"]
            or not isinstance(observation["entries"], list)
        ):
            raise PipelineError("cache observation identity is invalid", category="ONLINE_DOCTOR_BLOCKED")
        entry_keys = {"id", "type", "relative_path", "bytes", "sha256", "members"}
        member_keys = {"path", "bytes", "sha256"}
        for entry in observation["entries"]:
            if not isinstance(entry, dict) or set(entry) != entry_keys:
                raise PipelineError("cache observation entry is invalid", category="ONLINE_DOCTOR_BLOCKED")
            if (
                not isinstance(entry["id"], str)
                or not isinstance(entry["type"], str)
                or not isinstance(entry["relative_path"], str)
                or type(entry["bytes"]) is not int
                or entry["bytes"] < 0
                or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"] or "") is None
                or not isinstance(entry["members"], list)
            ):
                raise PipelineError("cache observation entry identity is invalid", category="ONLINE_DOCTOR_BLOCKED")
            for member in entry["members"]:
                if (
                    not isinstance(member, dict)
                    or set(member) != member_keys
                    or not isinstance(member["path"], str)
                    or type(member["bytes"]) is not int
                    or member["bytes"] < 0
                    or re.fullmatch(r"[0-9a-f]{64}", member["sha256"] or "") is None
                ):
                    raise PipelineError("cache observation member is invalid", category="ONLINE_DOCTOR_BLOCKED")
        observation_identity = copy.deepcopy(observation)
        observation_identity.pop("observed_at")
        if canonical_json_sha256(observation_identity) != result["cache_observation_sha256"]:
            raise PipelineError(
                "cache observation identity drifted",
                category="ONLINE_DOCTOR_BLOCKED",
            )

        host_facts = result["host_facts"]
        host_keys = {
            "schema", "host_alias", "os", "os_version",
            "architecture", "filesystem", "powershell_version",
        }
        if not isinstance(host_facts, dict) or set(host_facts) != host_keys:
            raise PipelineError("host facts fields are not exact", category="ONLINE_DOCTOR_BLOCKED")
        if (
            host_facts["schema"] != WINDOWS_HOST_SCHEMA
            or host_facts["architecture"] != "AMD64"
            or host_facts["filesystem"] != "NTFS"
            or host_facts["host_alias"] != result["host_alias"]
        ):
            raise PipelineError("host facts stable identity is invalid", category="ONLINE_DOCTOR_BLOCKED")
        if canonical_json_sha256(host_facts) != result["host_facts_sha256"]:
            raise PipelineError("host facts identity drifted", category="ONLINE_DOCTOR_BLOCKED")

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


def _invoke_runner(runner, argv, input_bytes=None):
    if runner is None:
        return subprocess.run(
            argv,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
        )
    try:
        signature = inspect.signature(runner)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if accepts_kwargs or "cwd" in signature.parameters:
            kwargs = {
                "cwd": Path.cwd(),
                "environment": os.environ.copy(),
                "timeout": WINDOWS_COMMAND_TIMEOUT_SECONDS,
            }
            if accepts_kwargs or "text" in signature.parameters:
                kwargs["text"] = False
            if input_bytes is not None:
                kwargs["input"] = input_bytes
            return runner(argv, **kwargs)
        if input_bytes is not None and "input" in signature.parameters:
            return runner(argv, input=input_bytes)
    return runner(argv)


def _sha256_path(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _dump_canonical_json(path, value):
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    Path(path).write_bytes(payload + b"\n")
    return payload


def _remote_join(root, *parts):
    value = str(root).rstrip("\\/")
    for part in parts:
        value = value + "\\" + str(part).strip("\\/")
    return value


def _resolve_remote_relative(root, relative_path):
    value = str(relative_path).replace("/", "\\")
    if not value or value.startswith("\\") or re.match(r"^[A-Za-z]:\\", value):
        raise PipelineError("remote path must stay within the run root", category="PLAN_INVALID")
    parts = [part for part in value.split("\\") if part]
    if not parts or any(part in (".", "..") for part in parts):
        raise PipelineError("remote path must stay within the run root", category="PLAN_INVALID")
    return _remote_join(root, *parts)


def _quote_ps(value):
    return "'" + str(value).replace("'", "''") + "'"


def _render_candidate_stage_invocation(
    *, stage_name, script_path, session_path, logs_root, failure_category, postcheck=""
):
    stdout_path = _remote_join(logs_root, "{}.stdout.log".format(stage_name))
    stderr_path = _remote_join(logs_root, "{}.stderr.log".format(stage_name))
    result_path = _remote_join(logs_root, "{}-result.json".format(stage_name))
    script = r"""
$ErrorActionPreference = 'Stop'
$utf8NoBom = [Text.UTF8Encoding]::new($false)
$stdoutPath = __STDOUT_PATH__
$stderrPath = __STDERR_PATH__
$resultPath = __RESULT_PATH__

function Write-ExecutionResult {
  param([Parameter(Mandatory = $true)]$Value)
  $temporary = "$resultPath.$([Guid]::NewGuid().ToString('N')).tmp"
  [IO.File]::WriteAllText(
    $temporary,
    ((ConvertTo-Json -InputObject $Value -Depth 8 -Compress) + [char]10),
    $utf8NoBom
  )
  if (Test-Path -LiteralPath $resultPath) {
    [IO.File]::Replace($temporary, $resultPath, $null)
  } else {
    [IO.File]::Move($temporary, $resultPath)
  }
}

[IO.File]::WriteAllText($stdoutPath, '', $utf8NoBom)
[IO.File]::WriteAllText($stderrPath, '', $utf8NoBom)
$startedAt = [DateTime]::UtcNow.ToString('o')
Write-ExecutionResult ([ordered]@{
  schema = 'taiji-windows-stage-result/v1'
  stage = '__STAGE_NAME__'
  status = 'RUNNING'
  started_at = $startedAt
  finished_at = $null
  exit_code = $null
  failure_stage = $null
  stdout_path = $stdoutPath
  stderr_path = $stderrPath
})

try {
  $global:LASTEXITCODE = 0
  & __SCRIPT_PATH__ -SessionPath __SESSION_PATH__ *>&1 | ForEach-Object {
    $line = ($_ | Out-String).TrimEnd()
    $destination = if ($_ -is [System.Management.Automation.ErrorRecord]) {
      $stderrPath
    } else {
      $stdoutPath
    }
    if ($line.Length -gt 0) {
      [IO.File]::AppendAllText($destination, ($line + [char]10), $utf8NoBom)
    }
    Write-Output $_
  }
__POSTCHECK__
  $finishedAt = [DateTime]::UtcNow.ToString('o')
  Write-ExecutionResult ([ordered]@{
    schema = 'taiji-windows-stage-result/v1'
    stage = '__STAGE_NAME__'
    status = 'PASS'
    started_at = $startedAt
    finished_at = $finishedAt
    exit_code = 0
    failure_stage = $null
    stdout_path = $stdoutPath
    stderr_path = $stderrPath
  })
} catch {
  $finishedAt = [DateTime]::UtcNow.ToString('o')
  $exitCode = if ($LASTEXITCODE -is [int] -and $LASTEXITCODE -ne 0) {
    [int]$LASTEXITCODE
  } else {
    1
  }
  $failure = ($_ | Out-String).TrimEnd()
  if ($failure.Length -gt 0) {
    [IO.File]::AppendAllText($stderrPath, ($failure + [char]10), $utf8NoBom)
  }
  Write-ExecutionResult ([ordered]@{
    schema = 'taiji-windows-stage-result/v1'
    stage = '__STAGE_NAME__'
    status = 'FAIL'
    started_at = $startedAt
    finished_at = $finishedAt
    exit_code = $exitCode
    failure_stage = '__FAILURE_CATEGORY__'
    stdout_path = $stdoutPath
    stderr_path = $stderrPath
  })
  throw
}
"""
    replacements = {
        "__STAGE_NAME__": str(stage_name),
        "__SCRIPT_PATH__": _quote_ps(script_path),
        "__SESSION_PATH__": _quote_ps(session_path),
        "__STDOUT_PATH__": _quote_ps(stdout_path),
        "__STDERR_PATH__": _quote_ps(stderr_path),
        "__RESULT_PATH__": _quote_ps(result_path),
        "__FAILURE_CATEGORY__": str(failure_category),
        "__POSTCHECK__": str(postcheck).rstrip(),
    }
    for marker, value in replacements.items():
        script = script.replace(marker, value)
    return script


class WindowsSshTransport:
    """Real Windows SSH transport for the candidate build/fetch stages."""

    def __init__(self, target, *, ssh_config, command_runner):
        self.target = copy.deepcopy(target)
        self.ssh_config = ssh_config
        self.command_runner = command_runner
        self.remote_build_succeeded = False
        self.remote_run_created = False
        self._contexts = {}

    def _run_powershell(self, script):
        script_text = (
            "$utf8 = [Text.UTF8Encoding]::new($false)\n"
            "[Console]::OutputEncoding = $utf8\n"
            "$OutputEncoding = $utf8\n"
            + str(script)
            + "\n"
        )
        argv = _powershell_stdin_argv(
            self.target["host_alias"],
            self.target["powershell"],
            self.ssh_config,
        )
        result = _invoke_runner(
            self.command_runner,
            argv,
            input_bytes=script_text.encode("utf-8"),
        )
        if getattr(result, "returncode", 0) != 0:
            raise PipelineError("Windows read-only probe failed", category="BUILDER_UNREACHABLE")
        return getattr(result, "stdout", result)

    def _run_remote_stage(self, script, category):
        script_text = (
            "$utf8 = [Text.UTF8Encoding]::new($false)\n"
            "[Console]::OutputEncoding = $utf8\n"
            "$OutputEncoding = $utf8\n"
            + str(script)
            + "\n"
        )
        result = _invoke_runner(self.command_runner, _powershell_stdin_argv(
            self.target["host_alias"],
            self.target["powershell"],
            self.ssh_config,
        ), input_bytes=script_text.encode("utf-8"))
        if getattr(result, "returncode", 0) != 0:
            raise PipelineError("Windows remote stage failed", category=category)
        return getattr(result, "stdout", result)

    def _scp_argv(self, source, destination, *, recursive=False):
        argv = [SCP, "-o", "BatchMode=yes", "-o", "ConnectTimeout=" + DEFAULT_CONNECT_TIMEOUT]
        if self.ssh_config is not None:
            argv.extend(["-F", str(Path(self.ssh_config).expanduser().resolve())])
        if recursive:
            argv.append("-r")
        argv.extend([str(source), str(destination)])
        return argv

    def _run_scp(self, source, destination, *, recursive=False):
        last_error = None
        for _attempt in range(2):
            result = _invoke_runner(
                self.command_runner,
                self._scp_argv(source, destination, recursive=recursive),
            )
            if getattr(result, "returncode", 0) == 0:
                return result
            last_error = result
        raise PipelineError("Windows SCP transfer was interrupted", category="SCP_INTERRUPTED")

    def _require_plan(self, plan):
        required = {
            "run_id",
            "remote_run_dir",
            "input",
            "source_branch",
            "source_commit",
            "source_tree",
            "version",
            "target_config",
            "asset_provenance_sha256",
            "target_config_sha256",
            "cache_requirements_sha256",
            "cache_observation",
            "cache_observation_sha256",
            "host_facts_sha256",
            "local_run_dir",
            "target_id",
        }
        if not isinstance(plan, dict) or not required.issubset(set(plan)):
            raise PipelineError("Windows plan is incomplete", category="PLAN_INVALID")
        return plan

    def _context_for(self, plan):
        plan = self._require_plan(plan)
        run_id = str(plan["run_id"])
        if run_id not in self._contexts:
            local_root = Path(tempfile.mkdtemp(prefix="windows-ssh-transport-"))
            input_root = local_root / "input"
            scripts_root = local_root / "scripts"
            input_root.mkdir(mode=0o700)
            scripts_root.mkdir(mode=0o700)
            observation_path = input_root / "cache-observation.json"
            _dump_canonical_json(observation_path, plan["cache_observation"])
            host_facts_path = input_root / "host-facts-sha256.txt"
            host_facts_path.write_text(str(plan["host_facts_sha256"]) + "\n", encoding="utf-8")
            target_path = input_root / "target-config.json"
            target_path.write_text(
                json.dumps(plan["target_config"], ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            bootstrap = plan.get("controller_bootstrap")
            safe_tar = bootstrap.get("safe_tar") if isinstance(bootstrap, dict) else None
            required_safe_tar = {"source_path", "remote_path", "bytes", "sha256", "python_path"}
            if not isinstance(safe_tar, dict) or set(safe_tar) != required_safe_tar:
                raise PipelineError("Windows safe-tar bootstrap is incomplete", category="PLAN_INVALID")
            safe_tar_path = Path(str(safe_tar["source_path"]))
            if not safe_tar_path.is_file():
                raise PipelineError("controller safe-tar source is missing", category="PLAN_INVALID")
            if safe_tar_path.is_symlink():
                raise PipelineError("controller safe-tar source is unsafe", category="PLAN_INVALID")
            safe_tar_bytes = safe_tar_path.stat().st_size
            safe_tar_sha256 = _sha256_path(safe_tar_path)
            if safe_tar_bytes != safe_tar["bytes"] or safe_tar_sha256 != safe_tar["sha256"]:
                raise PipelineError("controller safe-tar identity drifted", category="PLAN_INVALID")
            safe_tar_remote_path = _resolve_remote_relative(
                plan["remote_run_dir"], safe_tar["remote_path"]
            )
            if not re.match(r"^[A-Za-z]:\\", str(safe_tar["python_path"])):
                raise PipelineError("controller safe-tar python must be absolute", category="PLAN_INVALID")
            scripts = {}
            for name in (
                "Initialize-CandidateSession.ps1",
                "Stage-CandidatePayload.ps1",
                "Build-CandidateReview.ps1",
                "TaijiAgent.iss",
            ):
                source = WINDOWS_SCRIPTS_ROOT / name
                destination = scripts_root / name
                destination.write_bytes(source.read_bytes())
                scripts[name] = destination
            self._contexts[run_id] = {
                "local_root": local_root,
                "input_root": input_root,
                "scripts_root": scripts_root,
                "observation_path": observation_path,
                "host_facts_path": host_facts_path,
                "target_path": target_path,
                "safe_tar_path": safe_tar_path,
                "safe_tar_remote_path": safe_tar_remote_path,
                "safe_tar_sha256": safe_tar_sha256,
                "safe_tar_bytes": safe_tar_bytes,
                "safe_tar_python_path": str(safe_tar["python_path"]),
                "scripts": scripts,
                "state_path": Path(plan["local_run_dir"]) / "run-state.json",
                "remote": {
                    "root": str(plan["remote_run_dir"]),
                    "input": _remote_join(plan["remote_run_dir"], "input"),
                    "source": _remote_join(plan["remote_run_dir"], "source"),
                    "review": _remote_join(plan["remote_run_dir"], "review"),
                    "logs": _remote_join(plan["remote_run_dir"], "logs"),
                    "scripts": _remote_join(plan["remote_run_dir"], "scripts"),
                },
            }
        return self._contexts[run_id]

    def _load_fetch_state(self, plan):
        plan = self._require_plan(plan)
        run_id = str(plan["run_id"])
        run_dir = Path(plan["local_run_dir"]).resolve()
        if run_dir.name != run_id or run_dir.parent.name != "runs":
            raise PipelineError("frozen run state path drifted", category="FETCH_NOT_ALLOWED")
        state_root = run_dir.parent.parent
        store = RunStateStore(state_root)
        if store.run_dir(run_id).resolve() != run_dir:
            raise PipelineError("frozen run state root drifted", category="FETCH_NOT_ALLOWED")
        try:
            state = store.load(run_id)
            validate_v2_state(state)
        except PipelineError as exc:
            raise PipelineError(
                "frozen run state is invalid: {}".format(exc),
                category="FETCH_NOT_ALLOWED",
            ) from exc
        path = store.state_path(run_id)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise PipelineError("frozen run state is unavailable: {}".format(exc), category="FETCH_NOT_ALLOWED") from exc
        if (
            metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or not path.is_file()
            or path.is_symlink()
            or (metadata.st_mode & 0o777) != 0o600
        ):
            raise PipelineError("frozen run state is unsafe", category="FETCH_NOT_ALLOWED")
        payload = path.read_bytes()
        canonical = json.dumps(
            state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        if payload != canonical:
            raise PipelineError("frozen run state is not canonical", category="FETCH_NOT_ALLOWED")
        if (
            state.get("schema") != "taiji-package-run-state/v2"
            or state.get("run_id") != run_id
            or state.get("target_id") != plan["target_id"]
            or state.get("paths", {}).get("local_run_dir") != plan["local_run_dir"]
            or state.get("plan") != plan
            or state.get("target_config") != self.target
            or state.get("host", {}).get("alias") != self.target["host_alias"]
            or state.get("host", {}).get("remote_run_dir") != plan["remote_run_dir"]
        ):
            raise PipelineError("frozen run state identity drifted", category="FETCH_NOT_ALLOWED")
        if state.get("stage") not in (
            "REMOTE_BUILD_SUCCEEDED",
            "FETCH_PENDING",
            "REVIEW_FETCHED",
        ):
            raise PipelineError("frozen run state stage does not allow fetch", category="FETCH_NOT_ALLOWED")
        if not state.get("remote_build_succeeded") or not state.get("fetch_allowed"):
            raise PipelineError("frozen run state does not allow fetch", category="FETCH_NOT_ALLOWED")
        return state

    def _fetch_context_from_state(self, state):
        validate_v2_state(state)
        frozen_plan = state["plan"]
        remote_root = frozen_plan["remote_run_dir"]
        return {
            "remote": {
                "root": remote_root,
                "review": _remote_join(remote_root, "review"),
                "logs": _remote_join(remote_root, "logs"),
            }
        }

    def _remote_destination(self, remote_path):
        return "{}:{}".format(
            self.target["host_alias"],
            str(remote_path).replace("\\", "/"),
        )

    def online_doctor(self):
        payload = self._run_powershell(builder_probe_script(self.target))
        return parse_builder_probe(payload, self.target["minimum_free_gib"])

    def probe_product_source(self, branch, expected_tip, base_commit):
        payload = self._run_powershell(
            product_probe_script(self.target["remote_root"], branch, expected_tip, base_commit)
        )
        return parse_product_probe(payload)

    def create_remote_run(self, plan):
        context = self._context_for(plan)
        remote = context["remote"]
        script = """
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath {root}) {{
  throw 'REMOTE_RUN_CONFLICT: remote run root already exists'
}}
New-Item -ItemType Directory -Path {root} | Out-Null
New-Item -ItemType Directory -Path {input} | Out-Null
New-Item -ItemType Directory -Path {source} | Out-Null
New-Item -ItemType Directory -Path {review} | Out-Null
New-Item -ItemType Directory -Path {logs} | Out-Null
New-Item -ItemType Directory -Path {scripts} | Out-Null
Write-Host 'REMOTE_RUN_READY'
""".format(
            root=_quote_ps(remote["root"]),
            input=_quote_ps(remote["input"]),
            source=_quote_ps(remote["source"]),
            review=_quote_ps(remote["review"]),
            logs=_quote_ps(remote["logs"]),
            scripts=_quote_ps(remote["scripts"]),
        )
        self._run_remote_stage(script, "WINDOWS_RUN_FAILED")
        self.remote_run_created = True
        self.remote_build_succeeded = False

    def transfer_input(self, plan):
        context = self._context_for(plan)
        if not self.remote_run_created:
            raise PipelineError("remote run has not been created", category="WINDOWS_RUN_FAILED")
        remote = context["remote"]
        files = plan["input"]["files"]
        transfers = [
            (files["archive"]["path"], _remote_join(remote["input"], files["archive"]["basename"])),
            (files["manifest"]["path"], _remote_join(remote["input"], files["manifest"]["basename"])),
            (files["checksum"]["path"], _remote_join(remote["input"], files["checksum"]["basename"])),
            (context["observation_path"], _remote_join(remote["input"], "cache-observation.json")),
            (context["host_facts_path"], _remote_join(remote["input"], "host-facts-sha256.txt")),
            (context["target_path"], _remote_join(remote["input"], "target-config.json")),
            (WINDOWS_SCRIPTS_ROOT / "asset-provenance.json", _remote_join(remote["input"], "asset-provenance.json")),
            (CACHE_REQUIREMENTS_PATH, _remote_join(remote["input"], "cache-requirements.json")),
            (context["safe_tar_path"], context["safe_tar_remote_path"]),
            (context["scripts"]["Initialize-CandidateSession.ps1"], _remote_join(remote["scripts"], "Initialize-CandidateSession.ps1")),
            (context["scripts"]["Stage-CandidatePayload.ps1"], _remote_join(remote["scripts"], "Stage-CandidatePayload.ps1")),
            (context["scripts"]["Build-CandidateReview.ps1"], _remote_join(remote["scripts"], "Build-CandidateReview.ps1")),
            (context["scripts"]["TaijiAgent.iss"], _remote_join(remote["scripts"], "TaijiAgent.iss")),
        ]
        for source, remote_path in transfers:
            self._run_scp(source, self._remote_destination(remote_path))

    def verify_remote_input(self, plan):
        context = self._context_for(plan)
        remote = context["remote"]
        files = plan["input"]["files"]
        session_path = _remote_join(remote["root"], "session.json")
        checkout_root = _remote_join(remote["source"], "checkout")
        extended_checkout_root = "\\\\?\\" + checkout_root
        script = """
$ErrorActionPreference = 'Stop'
function Assert-RegularRemoteFile {{
  param([string]$Path, [string]$Label)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {{
    throw "$Label is missing: $Path"
  }}
}}
function ConvertTo-CanonicalValue {{
  param([Parameter(Mandatory = $true)]$Value)
  if ($null -eq $Value) {{ return $null }}
  if ($Value -is [System.Collections.IDictionary]) {{
    $ordered = [ordered]@{{}}
    foreach ($key in @($Value.Keys | ForEach-Object {{ [string]$_ }} | Sort-Object)) {{
      $ordered[$key] = ConvertTo-CanonicalValue $Value[$key]
    }}
    return $ordered
  }}
  if ($Value -is [System.Management.Automation.PSCustomObject]) {{
    $ordered = [ordered]@{{}}
    foreach ($key in @($Value.PSObject.Properties.Name | Sort-Object)) {{
      $ordered[$key] = ConvertTo-CanonicalValue $Value.$key
    }}
    return $ordered
  }}
  if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {{
    $items = @()
    foreach ($item in @($Value)) {{ $items += ,(ConvertTo-CanonicalValue $item) }}
    return ,$items
  }}
  return $Value
}}
function ConvertTo-CanonicalJson {{
  param([Parameter(Mandatory = $true)]$Value)
  return (ConvertTo-Json -InputObject (ConvertTo-CanonicalValue $Value) -Depth 100 -Compress)
}}
function Get-Sha256Bytes {{
  param([Parameter(Mandatory = $true)][byte[]]$Bytes)
  $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($Bytes)
  return ([BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant()
}}
Assert-RegularRemoteFile {archive_path} 'builder input archive'
Assert-RegularRemoteFile {manifest_path} 'builder input manifest'
Assert-RegularRemoteFile {sidecar_path} 'builder input sidecar'
Assert-RegularRemoteFile {observation_path} 'cache observation'
Assert-RegularRemoteFile {host_facts_path} 'host facts sha'
Assert-RegularRemoteFile {bootstrap_path} 'controller safe tar'
if ((Get-Item -LiteralPath {archive_path}).Length -ne {archive_bytes}) {{
  throw 'builder input archive bytes drifted'
}}
if ((Get-FileHash -LiteralPath {archive_path} -Algorithm SHA256).Hash.ToLowerInvariant() -ne {archive_sha256}) {{
  throw 'builder input archive sha256 drifted'
}}
if ((Get-Item -LiteralPath {manifest_path}).Length -ne {manifest_bytes}) {{
  throw 'builder input manifest bytes drifted'
}}
if ((Get-FileHash -LiteralPath {manifest_path} -Algorithm SHA256).Hash.ToLowerInvariant() -ne {manifest_sha256}) {{
  throw 'builder input manifest sha256 drifted'
}}
if ((Get-Item -LiteralPath {sidecar_path}).Length -ne {sidecar_bytes}) {{
  throw 'builder input sidecar bytes drifted'
}}
if ((Get-FileHash -LiteralPath {sidecar_path} -Algorithm SHA256).Hash.ToLowerInvariant() -ne {sidecar_sha256}) {{
  throw 'builder input sidecar sha256 drifted'
}}
if ((Get-Content -LiteralPath {sidecar_path} -Raw) -cne {sidecar_text}) {{
  throw 'builder input sidecar text drifted'
}}
if ((Get-Content -LiteralPath {host_facts_path} -Raw).Trim().ToLowerInvariant() -ne {host_facts_sha256}) {{
  throw 'host facts sha256 drifted'
}}
$observation = Get-Content -LiteralPath {observation_path} -Raw | ConvertFrom-Json
$observationKeys = @($observation.PSObject.Properties.Name | Sort-Object)
$expectedObservationKeys = @('cache_root', 'entries', 'observed_at', 'requirements_sha256', 'schema', 'target_id')
if (($observationKeys -join '|') -cne ($expectedObservationKeys -join '|')) {{
  throw 'cache observation fields drifted before extract'
}}
$observationIdentity = [ordered]@{{}}
foreach ($property in $observation.PSObject.Properties) {{
  if ($property.Name -ne 'observed_at') {{
    $observationIdentity[$property.Name] = $property.Value
  }}
}}
$observationSha256 = Get-Sha256Bytes ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $observationIdentity)))
if ($observation.schema -ne 'taiji-windows-cache-observation/v1' -or
    $observation.target_id -ne 'windows-x64' -or
    $observation.requirements_sha256 -ne {cache_requirements_sha256} -or
    [string]$observation.cache_root -cne {cache_root} -or
    $observationSha256 -ne {cache_observation_sha256}) {{
  throw 'cache observation identity drifted before extract'
}}
if ((Get-Item -LiteralPath {bootstrap_path}).Length -ne {bootstrap_bytes}) {{
  throw 'controller safe tar bytes drifted'
}}
if ((Get-FileHash -LiteralPath {bootstrap_path} -Algorithm SHA256).Hash.ToLowerInvariant() -ne {bootstrap_sha256}) {{
  throw 'controller safe tar SHA256 drifted'
}}
if (Test-Path -LiteralPath {checkout_root}) {{
  throw 'controller safe tar destination must not exist before extract'
}}
& {bootstrap_python} -I -B {bootstrap_path} extract --archive {archive_path} --destination {checkout_root} --manifest {manifest_path}
if ($LASTEXITCODE -ne 0) {{
  throw "controller safe tar extract failed: $LASTEXITCODE"
}}
& {initialize} `
  -RunRoot {run_root} `
  -RunId {run_id} `
  -SourceRoot {source_root} `
  -SourceBranch {source_branch} `
  -SourceCommit {source_commit} `
  -SourceTree {source_tree} `
  -InputManifestPath {input_manifest} `
  -TargetConfigPath {target_config} `
  -AssetProvenancePath {asset_provenance} `
  -InputArchiveBasename {input_archive_basename} `
  -InputArchiveBytes {input_archive_bytes} `
  -InputArchiveSha256 {input_archive_sha256} `
  -InputManifestBasename {input_manifest_basename} `
  -InputManifestBytes {input_manifest_bytes} `
  -InputManifestSha256 {input_manifest_sha256} `
  -InputSidecarBasename {input_sidecar_basename} `
  -InputSidecarBytes {input_sidecar_bytes} `
  -InputSidecarSha256 {input_sidecar_sha256} `
  -CacheRoot {cache_root} `
  -CacheRequirementsPath {cache_requirements} `
  -ExpectedCacheRequirementsSha256 {cache_requirements_sha256} `
  -ExpectedCacheObservationSha256 {cache_observation_sha256} `
  -PowerShellPath {powershell_path} `
  -TarPath {tar_path} `
  -NodePath {node_path} `
  -NpmPath {npm_path} `
  -PythonPath {python_path} `
  -IsccPath {iscc_path} `
  -SafeTarPath {safe_tar_path} `
  -ExpectedSafeTarSha256 {safe_tar_sha256} `
  -ExpectedTargetConfigSha256 {target_config_sha256} `
  -ExpectedAssetProvenanceSha256 {asset_provenance_sha256} `
  -ExpectedHostFactsSha256 {host_facts_sha256} `
  -Version {version}
if (-not (Test-Path -LiteralPath {session_path} -PathType Leaf)) {{
  throw 'candidate session was not created'
}}
""".format(
            initialize=_quote_ps(_remote_join(remote["scripts"], "Initialize-CandidateSession.ps1")),
            run_root=_quote_ps(remote["root"]),
            run_id=_quote_ps(plan["run_id"]),
            source_root=_quote_ps(extended_checkout_root),
            source_branch=_quote_ps(plan["source_branch"]),
            source_commit=_quote_ps(plan["source_commit"]),
            source_tree=_quote_ps(plan["source_tree"]),
            input_manifest=_quote_ps(_remote_join(remote["input"], files["manifest"]["basename"])),
            target_config=_quote_ps(_remote_join(remote["input"], "target-config.json")),
            asset_provenance=_quote_ps(_remote_join(remote["input"], "asset-provenance.json")),
            input_archive_basename=_quote_ps(files["archive"]["basename"]),
            input_archive_bytes=files["archive"]["bytes"],
            input_archive_sha256=_quote_ps(files["archive"]["sha256"]),
            input_manifest_basename=_quote_ps(files["manifest"]["basename"]),
            input_manifest_bytes=files["manifest"]["bytes"],
            input_manifest_sha256=_quote_ps(files["manifest"]["sha256"]),
            input_sidecar_basename=_quote_ps(files["checksum"]["basename"]),
            input_sidecar_bytes=files["checksum"]["bytes"],
            input_sidecar_sha256=_quote_ps(files["checksum"]["sha256"]),
            cache_root=_quote_ps(plan["target_config"]["cache_root"]),
            cache_requirements=_quote_ps(_remote_join(remote["input"], "cache-requirements.json")),
            cache_requirements_sha256=_quote_ps(plan["cache_requirements_sha256"]),
            cache_observation_sha256=_quote_ps(plan["cache_observation_sha256"]),
            powershell_path=_quote_ps(plan["target_config"]["powershell"]),
            tar_path=_quote_ps(plan["target_config"]["tar"]),
            node_path=_quote_ps(plan["target_config"]["node"]),
            npm_path=_quote_ps(plan["target_config"]["npm"]),
            python_path=_quote_ps(plan["target_config"]["python"]),
            iscc_path=_quote_ps(plan["target_config"]["iscc"]),
            safe_tar_path=_quote_ps(context["safe_tar_remote_path"]),
            safe_tar_sha256=_quote_ps(context["safe_tar_sha256"]),
            target_config_sha256=_quote_ps(plan["target_config_sha256"]),
            asset_provenance_sha256=_quote_ps(plan["asset_provenance_sha256"]),
            version=_quote_ps(plan["version"]),
            session_path=_quote_ps(session_path),
            archive_path=_quote_ps(_remote_join(remote["input"], files["archive"]["basename"])),
            manifest_path=_quote_ps(_remote_join(remote["input"], files["manifest"]["basename"])),
            sidecar_path=_quote_ps(_remote_join(remote["input"], files["checksum"]["basename"])),
            observation_path=_quote_ps(_remote_join(remote["input"], "cache-observation.json")),
            host_facts_path=_quote_ps(_remote_join(remote["input"], "host-facts-sha256.txt")),
            bootstrap_path=_quote_ps(context["safe_tar_remote_path"]),
            bootstrap_bytes=context["safe_tar_bytes"],
            bootstrap_sha256=_quote_ps(context["safe_tar_sha256"]),
            bootstrap_python=_quote_ps(context["safe_tar_python_path"]),
            checkout_root=_quote_ps(checkout_root),
            archive_bytes=files["archive"]["bytes"],
            archive_sha256=_quote_ps(files["archive"]["sha256"]),
            manifest_bytes=files["manifest"]["bytes"],
            manifest_sha256=_quote_ps(files["manifest"]["sha256"]),
            sidecar_bytes=files["checksum"]["bytes"],
            sidecar_sha256=_quote_ps(files["checksum"]["sha256"]),
            sidecar_text=_quote_ps(
                "{}  {}\n{}  {}\n".format(
                    files["archive"]["sha256"],
                    files["archive"]["basename"],
                    files["manifest"]["sha256"],
                    files["manifest"]["basename"],
                )
            ),
            host_facts_sha256=_quote_ps(plan["host_facts_sha256"]),
        )
        self._run_remote_stage(script, "INPUT_VERIFICATION_FAILED")

    def build_remote_candidate(self, plan):
        context = self._context_for(plan)
        remote = context["remote"]
        session_path = _remote_join(remote["root"], "session.json")
        stage_script = _render_candidate_stage_invocation(
            stage_name="payload",
            script_path=_remote_join(remote["scripts"], "Stage-CandidatePayload.ps1"),
            session_path=session_path,
            logs_root=remote["logs"],
            failure_category="WINDOWS_PAYLOAD_FAILED",
        )
        self._run_remote_stage(stage_script, "WINDOWS_PAYLOAD_FAILED")

        postcheck = """
if (-not (Test-Path -LiteralPath {marker_path} -PathType Leaf)) {{
  throw 'remote marker is missing'
}}
if (-not (Test-Path -LiteralPath {review_manifest} -PathType Leaf)) {{
  throw 'remote review manifest is missing'
}}
""".format(
            marker_path=_quote_ps(_remote_join(remote["review"], ".build-success")),
            review_manifest=_quote_ps(_remote_join(remote["review"], "taiji-package-manifest.json")),
        )
        build_script = _render_candidate_stage_invocation(
            stage_name="inno",
            script_path=_remote_join(remote["scripts"], "Build-CandidateReview.ps1"),
            session_path=session_path,
            logs_root=remote["logs"],
            failure_category="WINDOWS_INNO_FAILED",
            postcheck=postcheck,
        )
        self._run_remote_stage(build_script, "WINDOWS_INNO_FAILED")
        self.remote_build_succeeded = True

    def fetch(self, plan, staging_dir):
        state = self._load_fetch_state(plan)
        context = self._fetch_context_from_state(state)
        staging_dir = Path(staging_dir).resolve()
        if staging_dir.exists():
            raise PipelineError("local fetch staging is occupied", category="LOCAL_OUTPUT_OCCUPIED")
        staging_dir.mkdir(parents=True, mode=0o700)
        review_path = staging_dir / "review"
        remote_log_path = staging_dir / "remote-build.log"
        self._run_scp(
            self._remote_destination(context["remote"]["review"]),
            review_path,
            recursive=True,
        )
        self._run_scp(
            self._remote_destination(_remote_join(context["remote"]["logs"], "remote-build.log")),
            remote_log_path,
        )
        staging_dir.chmod(0o700)
        if review_path.exists():
            review_path.chmod(0o700)
            for entry in review_path.iterdir():
                if entry.is_dir():
                    entry.chmod(0o700)
                else:
                    entry.chmod(0o600)
        if remote_log_path.exists():
            remote_log_path.chmod(0o600)
        return {
            "review_path": str(review_path),
            "remote_log_path": str(remote_log_path),
        }
