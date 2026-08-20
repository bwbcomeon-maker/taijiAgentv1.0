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
import re
import subprocess
from pathlib import Path

from ..core.errors import PipelineError
from ..core.models import canonical_json_sha256


SSH = "/usr/bin/ssh"
DEFAULT_CONNECT_TIMEOUT = "5"
WINDOWS_CACHE_SCHEMA = "taiji-windows-cache-observation/v1"
WINDOWS_HOST_SCHEMA = "taiji-windows-host-facts/v1"
ONLINE_SCHEMA = "taiji-package-online-doctor/v2"
PRODUCT_SCHEMA = "taiji-windows-product-probe/v1"
ROOT = Path(__file__).resolve().parents[3]
CACHE_REQUIREMENTS_PATH = ROOT / "packaging/windows/cache-requirements.json"
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
    """Return one safe SSH argv using encoded command or stdin for long scripts."""

    script_text = str(script)
    if len(script_text) > 6000:
        remote_args = [
            str(powershell_path),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "-",
        ]
    else:
        encoded = base64.b64encode(script_text.encode("utf-16le")).decode("ascii")
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
    $Stream.Position = 0
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

function Test-SafeZipMemberName {
  param([string]$Name)
  if ([string]::IsNullOrEmpty($Name) -or $Name.IndexOf([char]0) -ge 0 -or $Name.Contains('\')) { return $false }
  $candidate = $Name
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
  $sorted = New-Object System.Collections.ArrayList
  foreach ($member in @($Members)) {
    $inserted = $false
    for ($index = 0; $index -lt $sorted.Count; $index++) {
      $left = [System.Text.Encoding]::UTF8.GetBytes([string]$sorted[$index].path)
      $right = [System.Text.Encoding]::UTF8.GetBytes([string]$member.path)
      if ((Compare-ByteArrays $left $right) -gt 0) {
        [void]$sorted.Insert($index, $member)
        $inserted = $true
        break
      }
    }
    if (-not $inserted) { [void]$sorted.Add($member) }
  }
  return ,@($sorted.ToArray())
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
      $name = [string]$zipEntry.FullName
      if (-not (Test-SafeZipMemberName $name)) { return New-MissingCacheResult $entry }
      $identityName = Get-PathIdentity $name.TrimEnd('/')
      if ($seen.ContainsKey($identityName)) { return New-MissingCacheResult $entry }
      $seen[$identityName] = $true
    }
    $zipMembers = @()
    foreach ($requiredMember in @($Requirement.required_members)) {
      if (-not (Test-SafePosixPath ([string]$requiredMember))) { return New-MissingCacheResult $entry }
      $matches = @($archive.Entries | Where-Object { [string]$_.FullName -eq [string]$requiredMember })
      if ($matches.Count -ne 1 -or $matches[0].FullName.EndsWith('/')) {
        return New-MissingCacheResult $entry
      }
      $stream = $matches[0].Open()
      try {
        $zipMembers += ,[ordered]@{
          path = [string]$requiredMember
          bytes = [int64]$matches[0].Length
          sha256 = Get-Sha256Stream $stream
        }
      } finally {
        $stream.Dispose()
      }
    }
    $entry.bytes = [int64]$item.Length
    $entry.sha256 = Get-Sha256Path $fullPath
    $entry.members = @(Sort-MembersByUtf8 $zipMembers)
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
        accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if accepts_kwargs or "cwd" in signature.parameters:
            kwargs = {
                "cwd": Path.cwd(),
                "environment": os.environ.copy(),
                "timeout": 30,
            }
            if input_bytes is not None:
                kwargs["input"] = input_bytes
            return runner(argv, **kwargs)
        if input_bytes is not None and "input" in signature.parameters:
            return runner(argv, input=input_bytes)
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
        script_text = str(script)
        argv = powershell_argv(
            self.target["host_alias"],
            self.target["powershell"],
            script_text,
            self.ssh_config,
        )
        input_bytes = (
            (script_text + "\nWrite-Output ''\n").encode("utf-8")
            if len(script_text) > 6000
            else None
        )
        result = _invoke_runner(self.command_runner, argv, input_bytes=input_bytes)
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
