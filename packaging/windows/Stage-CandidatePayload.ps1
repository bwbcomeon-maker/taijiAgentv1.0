[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$SessionPath
)

$ErrorActionPreference = 'Stop'

function Assert-RegularFile {
  param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Label)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Label is missing: $Path"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.LinkType) {
    throw "$Label is not a regular file: $Path"
  }
}

function ConvertTo-ExtendedPath {
  param([Parameter(Mandatory = $true)][string]$Path)
  if ($Path.StartsWith('\\?\')) { return $Path }
  if ($Path -notmatch '^[A-Za-z]:\\') {
    throw "working tree path must be an absolute drive path: $Path"
  }
  return '\\?\' + $Path
}

function Join-PathText {
  param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Child
  )
  return $Root.TrimEnd('\') + '\' + $Child.TrimStart('\')
}

function ConvertTo-CanonicalValue {
  param([Parameter(Mandatory = $true)]$Value)
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
  param([Parameter(Mandatory = $true)]$Value)
  return (ConvertTo-Json -InputObject (ConvertTo-CanonicalValue $Value) -Depth 100 -Compress)
}

function Get-Sha256 {
  param([Parameter(Mandatory = $true)][string]$Path)
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-Sha256Bytes {
  param([Parameter(Mandatory = $true)][byte[]]$Bytes)
  $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($Bytes)
  return ([BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant()
}

function Get-CanonicalHash {
  param([Parameter(Mandatory = $true)]$Value)
  return Get-Sha256Bytes ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $Value)))
}

function Write-Utf8Text {
  param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Text)
  [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}

function Copy-DirectoryChildren {
  param([Parameter(Mandatory = $true)][string]$Source, [Parameter(Mandatory = $true)][string]$Destination)
  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  foreach ($child in @(Get-ChildItem -LiteralPath $Source -Force)) {
    Copy-Item -LiteralPath $child.FullName -Destination (Join-PathText $Destination $child.Name) -Recurse -Force
  }
}

function Copy-PythonRuntimeFile {
  param(
    [Parameter(Mandatory = $true)][System.IO.FileInfo]$File,
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [Parameter(Mandatory = $true)][string]$DestinationRoot
  )
  $relative = $File.FullName.Substring($SourceRoot.Length).TrimStart('\')
  $relativeLeaf = [IO.Path]::GetFileName($relative)
  if ($relative -like '__pycache__\*' -or $relative -like '*\__pycache__\*' -or
      $relativeLeaf -like '*.pyc' -or $relativeLeaf -like '*.pyo') {
    return
  }
  $destination = Join-PathText $DestinationRoot $relative
  New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
  Copy-Item -LiteralPath $File.FullName -Destination $destination -Force
}

function Normalize-ZipMemberName {
  param([string]$Name)
  return $Name.Replace('\', '/')
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

function Get-Sha256Stream {
  param([Parameter(Mandatory = $true)][System.IO.Stream]$Stream)
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($sha.ComputeHash($Stream)) -replace '-', '').ToLowerInvariant()
  } finally {
    $sha.Dispose()
  }
}

function Sort-MembersByUtf8 {
  param([object[]]$Members)
  $sorted = New-Object System.Collections.ArrayList
  foreach ($member in @($Members)) {
    $inserted = $false
    for ($index = 0; $index -lt $sorted.Count; $index++) {
      $left = [Text.Encoding]::UTF8.GetBytes([string]$sorted[$index].path)
      $right = [Text.Encoding]::UTF8.GetBytes([string]$member.path)
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

function Get-CacheEntry {
  param(
    [Parameter(Mandatory = $true)]$Requirement,
    [Parameter(Mandatory = $true)][string]$CacheRoot
  )
  $relativePath = [string]$Requirement.relative_path
  $entry = [ordered]@{
    id = [string]$Requirement.id
    type = [string]$Requirement.type
    relative_path = $relativePath
    bytes = [int64]0
    sha256 = ('0' * 64)
    members = @()
  }
  if (-not (Test-SafePosixPath $relativePath)) { return $null }
  $fullPath = Join-PathText $CacheRoot ($relativePath -replace '/', '\')
  if (-not (Test-Path -LiteralPath $fullPath)) { return $null }
  $item = Get-Item -LiteralPath $fullPath -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.LinkType) { return $null }

  if ([string]$Requirement.type -eq 'directory') {
    if ($item -isnot [System.IO.DirectoryInfo]) { return $null }
    foreach ($requiredMember in @($Requirement.required_members)) {
      if (-not (Test-SafePosixPath ([string]$requiredMember))) { return $null }
      $requiredPath = Join-PathText $fullPath (([string]$requiredMember) -replace '/', '\')
      if (-not (Test-Path -LiteralPath $requiredPath)) { return $null }
    }
    $seen = @{}
    $members = @()
    foreach ($child in @(Get-ChildItem -LiteralPath $fullPath -File -Recurse -Force)) {
      if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $child.LinkType) { return $null }
      $memberPath = $child.FullName.Substring($fullPath.TrimEnd('\').Length).TrimStart('\').Replace('\', '/')
      if (-not (Test-SafePosixPath $memberPath)) { return $null }
      $identity = Get-PathIdentity $memberPath
      if ($seen.ContainsKey($identity)) { return $null }
      $seen[$identity] = $true
      $members += ,[ordered]@{
        path = $memberPath
        bytes = [int64]$child.Length
        sha256 = Get-Sha256 $child.FullName
      }
    }
    $members = Sort-MembersByUtf8 $members
    $totalBytes = [int64]0
    foreach ($member in @($members)) { $totalBytes += [int64]$member.bytes }
    $entry.bytes = $totalBytes
    $entry.sha256 = Get-CanonicalHash (,$members)
    $entry.members = @($members)
    return $entry
  }

  if ([string]$Requirement.type -ne 'regular-file' -or $item -isnot [System.IO.FileInfo]) {
    return $null
  }
  [void](Add-Type -AssemblyName System.IO.Compression.FileSystem)
  $archive = [System.IO.Compression.ZipFile]::OpenRead($fullPath)
  try {
    $seen = @{}
    foreach ($zipEntry in @($archive.Entries)) {
      $normalizedName = Normalize-ZipMemberName $zipEntry.FullName
      if (-not (Test-SafeZipMemberName $normalizedName)) { return $null }
      $identityName = Get-PathIdentity $normalizedName.TrimEnd('/')
      if ($seen.ContainsKey($identityName)) { return $null }
      $seen[$identityName] = $true
    }
    $members = @()
    foreach ($requiredMember in @($Requirement.required_members)) {
      $requiredNormalized = Normalize-ZipMemberName ([string]$requiredMember)
      if (-not (Test-SafePosixPath $requiredNormalized)) { return $null }
      $matches = @(
        $archive.Entries | Where-Object { (Normalize-ZipMemberName ([string]$_.FullName)) -eq $requiredNormalized }
      )
      if ($matches.Count -ne 1) { return $null }
      $stream = $matches[0].Open()
      try {
        $members += ,[ordered]@{
          path = [string]$requiredNormalized
          bytes = [int64]$matches[0].Length
          sha256 = Get-Sha256Stream $stream
        }
      } finally {
        $stream.Dispose()
      }
    }
    $entry.bytes = [int64]$item.Length
    $entry.sha256 = Get-Sha256 $fullPath
    $entry.members = Sort-MembersByUtf8 $members
    return $entry
  } finally {
    $archive.Dispose()
  }
}

Assert-RegularFile $SessionPath 'candidate session'
$session = Get-Content -LiteralPath $SessionPath -Raw | ConvertFrom-Json
if ($session.schema -ne 'taiji-windows-candidate-session/v1' -or $session.target_id -ne 'windows-x64') {
  throw 'candidate session identity is invalid'
}

$sourceRoot = ConvertTo-ExtendedPath ([string]$session.paths.source_root)
$stagingRoot = ConvertTo-ExtendedPath ([string]$session.paths.staging_root)
$stagingCacheRoot = ConvertTo-ExtendedPath ([string]$session.paths.staging_cache_root)
$payloadRoot = ConvertTo-ExtendedPath ([string]$session.paths.payload_root)
if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
  throw "safe source root is missing: $sourceRoot"
}
$sourceResidue = Get-ChildItem -LiteralPath $sourceRoot -Force -Recurse |
  Where-Object { $_.Name -eq '.git' -or ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $_.LinkType } |
  Select-Object -First 1
if ($sourceResidue) {
  throw "safe source contains Git metadata or a reparse point: $($sourceResidue.FullName)"
}

New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
New-Item -ItemType Directory -Path $stagingCacheRoot -Force | Out-Null
New-Item -ItemType Directory -Path $payloadRoot -Force | Out-Null

$requirements = Get-Content -LiteralPath $session.cache.requirements_path -Raw | ConvertFrom-Json
if ((Get-CanonicalHash $requirements) -ne $session.cache.requirements_sha256) {
  throw 'cache requirements identity drifted before staging'
}
Assert-RegularFile $session.cache.observation_path 'cache observation'
$observation = Get-Content -LiteralPath $session.cache.observation_path -Raw | ConvertFrom-Json
$sharedCacheRoot = [string]$session.cache.root
$observationKeys = @($observation.PSObject.Properties.Name | Sort-Object)
$expectedObservationKeys = @('cache_root', 'entries', 'observed_at', 'requirements_sha256', 'schema', 'target_id')
if (($observationKeys -join '|') -cne ($expectedObservationKeys -join '|')) {
  throw 'cache observation fields drifted before staging'
}
if ([string]$observation.cache_root -cne $sharedCacheRoot) {
  throw 'cache observation root drifted before staging'
}
$observationIdentity = [ordered]@{}
foreach ($property in $observation.psobject.Properties) {
  if ($property.Name -ne 'observed_at') {
    $observationIdentity[$property.Name] = $property.Value
  }
}
if ($observation.schema -ne 'taiji-windows-cache-observation/v1' -or
    $observation.target_id -ne 'windows-x64' -or
    $observation.requirements_sha256 -ne $session.cache.requirements_sha256 -or
    (Get-CanonicalHash $observationIdentity) -ne $session.cache.observation_sha256) {
  throw 'cache observation identity drifted before staging'
}

foreach ($entry in @($requirements.entries)) {
  $source = Join-PathText $sharedCacheRoot $entry.relative_path.Replace('/', '\')
  $destination = Join-PathText $stagingCacheRoot $entry.relative_path.Replace('/', '\')
  if (-not (Test-Path -LiteralPath $source)) {
    throw "WINDOWS_CACHE_MISSING: $($entry.id)"
  }
  if ($entry.type -eq 'directory') {
    Copy-DirectoryChildren -Source $source -Destination $destination
  } else {
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Force
  }
}

$stagingEntries = @()
foreach ($entry in @($requirements.entries)) {
  $stagingEntry = Get-CacheEntry -Requirement $entry -CacheRoot $stagingCacheRoot
  if ($null -eq $stagingEntry) {
    throw "WINDOWS_CACHE_MISSING: $($entry.id)"
  }
  $stagingEntries += $stagingEntry
}
$stagingObservationIdentity = [ordered]@{
  schema = 'taiji-windows-cache-observation/v1'
  target_id = 'windows-x64'
  requirements_sha256 = $session.cache.requirements_sha256
  cache_root = [string]$observation.cache_root
  entries = @($stagingEntries)
}
if ((Get-CanonicalHash $stagingObservationIdentity) -ne $session.cache.observation_sha256) {
  throw 'staging observation identity drifted before payload assembly'
}

$desktopRoot = Join-PathText $sourceRoot 'apps\taiji-desktop'
$packageJson = Join-PathText $desktopRoot 'package.json'
$packageLock = Join-PathText $desktopRoot 'package-lock.json'
$desktopSrc = Join-PathText $desktopRoot 'src'
Assert-RegularFile $packageJson 'desktop package'
Assert-RegularFile $packageLock 'desktop package lock'
if (-not (Test-Path -LiteralPath $desktopSrc -PathType Container)) {
  throw "desktop source is missing: $desktopSrc"
}
$desktopNpmCheckRoot = Join-PathText $stagingRoot 'desktop-npm-check'
New-Item -ItemType Directory -Path $desktopNpmCheckRoot -Force | Out-Null
Copy-Item -LiteralPath $packageJson -Destination (Join-PathText $desktopNpmCheckRoot 'package.json') -Force
Copy-Item -LiteralPath $packageLock -Destination (Join-PathText $desktopNpmCheckRoot 'package-lock.json') -Force
$stagingNpmCache = Join-PathText $stagingCacheRoot 'npm'
if (-not (Test-Path -LiteralPath (Join-PathText $stagingNpmCache '_cacache'))) {
  throw 'WINDOWS_CACHE_MISSING: npm cache'
}
Push-Location $desktopNpmCheckRoot
try {
  $npmOutput = (& $session.tools.npm.path ci --offline --ignore-scripts --no-audit --cache $stagingNpmCache 2>&1 | Out-String)
  Write-Utf8Text -Path (Join-PathText $desktopNpmCheckRoot 'npm-check.log') -Text ($npmOutput.TrimEnd() + [char]10)
  if ($LASTEXITCODE -ne 0) {
    throw "offline npm ci failed: $LASTEXITCODE"
  }
} finally {
  Pop-Location
}

$appRoot = Join-PathText $payloadRoot 'resources\app'
New-Item -ItemType Directory -Path $appRoot -Force | Out-Null
# resources\app\package.json is staged explicitly from the safe desktop source.
Copy-Item -LiteralPath $packageJson -Destination (Join-PathText $appRoot 'package.json') -Force
# resources\app\src is staged explicitly from the safe desktop source.
Copy-Item -LiteralPath $desktopSrc -Destination (Join-PathText $appRoot 'src') -Recurse -Force

$agentSource = Join-PathText $sourceRoot 'hermes-local-lab\sources\hermes-agent'
$webuiSource = Join-PathText $sourceRoot 'hermes-local-lab\sources\hermes-webui'
if (-not (Test-Path -LiteralPath $agentSource -PathType Container)) {
  throw "Agent source is missing: $agentSource"
}
if (-not (Test-Path -LiteralPath $webuiSource -PathType Container)) {
  throw "WebUI source is missing: $webuiSource"
}
$payloadSourcesRoot = Join-PathText $payloadRoot 'hermes-local-lab\sources'
Copy-DirectoryChildren -Source $agentSource -Destination (Join-PathText $payloadSourcesRoot 'hermes-agent')
Copy-DirectoryChildren -Source $webuiSource -Destination (Join-PathText $payloadSourcesRoot 'hermes-webui')

$pythonSource = Join-PathText $stagingCacheRoot 'python-runtime'
$pythonDestination = Join-PathText $payloadRoot 'hermes-local-lab\runtime\python'
Assert-RegularFile (Join-PathText $pythonSource 'python.exe') 'python runtime'
Assert-RegularFile (Join-PathText $pythonSource 'python311._pth') 'python path file'
New-Item -ItemType Directory -Path $pythonDestination -Force | Out-Null
foreach ($pythonFile in @(Get-ChildItem -LiteralPath $pythonSource -File -Recurse -Force)) {
  Copy-PythonRuntimeFile -File $pythonFile -SourceRoot $pythonSource -DestinationRoot $pythonDestination
}

$electronArchive = Join-PathText $stagingCacheRoot 'electron\electron-v39.8.10-win32-x64.zip'
Assert-RegularFile $electronArchive 'Electron cache archive'
Expand-Archive -LiteralPath $electronArchive -DestinationPath $payloadRoot -Force
$electronBinary = Join-PathText $payloadRoot 'electron.exe'
if (-not (Test-Path -LiteralPath $electronBinary -PathType Leaf)) {
  throw 'Electron cache expansion did not produce root electron.exe'
}
$taijiAgentExe = Join-PathText $payloadRoot 'TaijiAgent.exe'
if (Test-Path -LiteralPath $taijiAgentExe) {
  throw 'TaijiAgent.exe already exists before rename'
}
Rename-Item -LiteralPath $electronBinary -NewName 'TaijiAgent.exe'
$previousElectronRunAsNode = $env:ELECTRON_RUN_AS_NODE
$hadElectronRunAsNode = Test-Path Env:\ELECTRON_RUN_AS_NODE
try {
  $env:ELECTRON_RUN_AS_NODE = '1'
  $electronOutput = (& $taijiAgentExe -e "console.log(process.platform + ' ' + process.arch)" 2>&1 | Out-String).Trim()
  if ($LASTEXITCODE -ne 0 -or $electronOutput -cne 'win32 x64') {
    throw 'electron-win32-x64 verification failed'
  }
} finally {
  if ($hadElectronRunAsNode) {
    $env:ELECTRON_RUN_AS_NODE = $previousElectronRunAsNode
  } else {
    Remove-Item Env:\ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
  }
}

$sharedConfigPath = Join-PathText $payloadRoot 'hermes-local-lab\config'
New-Item -ItemType Directory -Path $sharedConfigPath -Force | Out-Null
$defaultConfigSource = Join-PathText $sourceRoot 'packaging\windows\taiji-default-config.yaml'
$diagnoseSource = Join-PathText $sourceRoot 'packaging\windows\diagnose.ps1'
Assert-RegularFile $defaultConfigSource 'Windows default config'
Assert-RegularFile $diagnoseSource 'Windows diagnose script'
# hermes-local-lab\config\taiji-default-config.yaml is the shared payload config path.
Copy-Item -LiteralPath $defaultConfigSource -Destination (Join-PathText $sharedConfigPath 'taiji-default-config.yaml') -Force
New-Item -ItemType Directory -Path (Join-PathText $payloadRoot 'tools') -Force | Out-Null
Copy-Item -LiteralPath $diagnoseSource -Destination (Join-PathText $payloadRoot 'tools\diagnose.ps1') -Force

$pthPath = Join-PathText $pythonDestination 'python311._pth'
$pthText = @(
  'python311.zip',
  '.',
  '..\..\sources\hermes-agent',
  '..\..\sources\hermes-webui',
  'Lib\site-packages',
  'import site'
) -join [char]10
Write-Utf8Text -Path $pthPath -Text ($pthText + [char]10)

$payloadPython = Join-PathText $pythonDestination 'python.exe'
Assert-RegularFile $payloadPython 'payload python runtime'
Assert-RegularFile $session.tools.python.path 'controller python'
$runtimeHelp = (& $payloadPython -I -B -m taiji_runtime.main --help 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
  throw 'taiji_runtime.main help failed'
}
$importGate = @'
import pathlib
import re

payload = pathlib.Path(r"__PAYLOAD_ROOT__")
agent_root = (payload / "hermes-local-lab" / "sources" / "hermes-agent").resolve()
webui_root = (payload / "hermes-local-lab" / "sources" / "hermes-webui").resolve()
main_source = payload / "resources" / "app" / "src" / "main.js"
assert agent_root.is_dir()
assert webui_root.is_dir()
assert main_source.is_file()
assert (payload / "resources" / "app" / "package.json").is_file()
assert (payload / "resources" / "app" / "src").is_dir()
import taiji_runtime.main
import taiji_runtime_profile
import taiji_license
import aiohttp
import fastapi
import uvicorn
import yaml
import cryptography
import psutil
import api.config
from api.config import get_ui_visibility
for module, expected_root in (
    (taiji_runtime.main, agent_root),
    (taiji_runtime_profile, agent_root),
    (taiji_license, agent_root),
    (api.config, webui_root),
):
    module_path = pathlib.Path(module.__file__).resolve()
    assert str(module_path).startswith(str(expected_root))
visibility = get_ui_visibility({})
nav = {name for name, visible in visibility.get("nav", {}).items() if visible}
assert nav == {"chat", "tasks", "writing", "settings"}
source = main_source.read_text(encoding="utf-8")
assert "taiji_runtime.main" in source
assert re.search(r"chat", source)
assert re.search(r"tasks", source)
assert re.search(r"writing", source)
assert re.search(r"settings", source)
print("PAYLOAD_MENU_POLICY_OK")
'@
$gatePath = Join-PathText $stagingRoot 'payload-import-gate.py'
Write-Utf8Text -Path $gatePath -Text ($importGate.Replace('__PAYLOAD_ROOT__', $payloadRoot.Replace('\', '\\')) + [char]10)
try {
  $gateOutput = (& $payloadPython -I -B $gatePath 2>&1 | Out-String)
  if ($LASTEXITCODE -ne 0 -or $gateOutput -notmatch 'PAYLOAD_MENU_POLICY_OK') {
    throw 'payload private Python import/menu gate failed'
  }
} finally {
  Remove-Item -LiteralPath $gatePath -Force -ErrorAction SilentlyContinue
}

$forbiddenDirectories = @('.git', '.ssh', '.gnupg', '.aws', '__pycache__', 'node_modules')
# payload hygiene patterns include *.db, *.sqlite, *.sqlite3, *.pyc, and *.pyo.
$forbiddenExtensions = @('.db', '.sqlite', '.sqlite3', '.pyc', '.pyo')
foreach ($item in @(Get-ChildItem -LiteralPath $payloadRoot -Force -Recurse)) {
  if ($item.Name -in $forbiddenDirectories -or $item.Extension -in $forbiddenExtensions -or
      $item.Name -eq '.env' -or $item.Name -like '.env.*' -or
      ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.LinkType) {
    throw "payload-hygiene-closure failed: $($item.FullName)"
  }
}

$unsortedPayloadEntries = @(
  Get-ChildItem -LiteralPath $payloadRoot -File -Recurse -Force |
    ForEach-Object {
      $relative = $_.FullName.Substring($payloadRoot.Length + 1).Replace('\', '/')
      [ordered]@{
        path = $relative
        bytes = [int64]$_.Length
        sha256 = Get-Sha256 $_.FullName
      }
    }
)
$payloadEntries = Sort-MembersByUtf8 $unsortedPayloadEntries
$payloadManifest = [ordered]@{
  schema = 'taiji-windows-payload-manifest/v1'
  source_commit = $session.source.commit
  source_tree = $session.source.tree
  entries = @($payloadEntries)
  file_count = $payloadEntries.Count
  total_bytes = ($payloadEntries | Measure-Object -Property bytes -Sum).Sum
}
$payloadManifest.manifest_sha256 = Get-CanonicalHash $payloadManifest
$manifestPath = Join-PathText $stagingRoot 'payload-manifest.json'
Write-Utf8Text -Path $manifestPath -Text ((ConvertTo-CanonicalJson $payloadManifest) + [char]10)
Write-Host 'WINDOWS_CANDIDATE_PAYLOAD_STAGED'
