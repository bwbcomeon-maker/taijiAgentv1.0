[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$RunRoot,
  [Parameter(Mandatory = $true)][string]$RunId,
  [Parameter(Mandatory = $true)][string]$SourceRoot,
  [Parameter(Mandatory = $true)][string]$SourceBranch,
  [Parameter(Mandatory = $true)][string]$SourceCommit,
  [Parameter(Mandatory = $true)][string]$SourceTree,
  [Parameter(Mandatory = $true)][string]$InputManifestPath,
  [Parameter(Mandatory = $true)][string]$TargetConfigPath,
  [Parameter(Mandatory = $true)][string]$AssetProvenancePath,
  [Parameter(Mandatory = $true)][string]$InputArchiveBasename,
  [Parameter(Mandatory = $true)][string]$InputArchiveBytes,
  [Parameter(Mandatory = $true)][string]$InputArchiveSha256,
  [Parameter(Mandatory = $true)][string]$InputManifestBasename,
  [Parameter(Mandatory = $true)][string]$InputManifestBytes,
  [Parameter(Mandatory = $true)][string]$InputManifestSha256,
  [Parameter(Mandatory = $true)][string]$InputSidecarBasename,
  [Parameter(Mandatory = $true)][string]$InputSidecarBytes,
  [Parameter(Mandatory = $true)][string]$InputSidecarSha256,
  [Parameter(Mandatory = $true)][string]$CacheRoot,
  [Parameter(Mandatory = $true)][string]$CacheRequirementsPath,
  [Parameter(Mandatory = $true)][string]$ExpectedCacheRequirementsSha256,
  [Parameter(Mandatory = $true)][string]$ExpectedCacheObservationSha256,
  [Parameter(Mandatory = $true)][string]$PowerShellPath,
  [Parameter(Mandatory = $true)][string]$TarPath,
  [Parameter(Mandatory = $true)][string]$NodePath,
  [Parameter(Mandatory = $true)][string]$NpmPath,
  [Parameter(Mandatory = $true)][string]$PythonPath,
  [Parameter(Mandatory = $true)][string]$IsccPath,
  [Parameter(Mandatory = $true)][string]$SafeTarPath,
  [Parameter(Mandatory = $true)][string]$ExpectedSafeTarSha256,
  [Parameter(Mandatory = $true)][string]$ExpectedTargetConfigSha256,
  [Parameter(Mandatory = $true)][string]$ExpectedAssetProvenanceSha256,
  [Parameter(Mandatory = $true)][string]$ExpectedHostFactsSha256,
  [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = 'Stop'

function Assert-RegularFile {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Label,
    [switch]$AllowHardLink
  )
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Label is missing: $Path"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "$Label is a reparse point: $Path"
  }
  if ($item.LinkType -and
      (-not $AllowHardLink -or [string]$item.LinkType -cne 'HardLink')) {
    throw "$Label is a link: $Path"
  }
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

function Get-CanonicalHash {
  param([Parameter(Mandatory = $true)]$Value)
  $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $Value))
  $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  return ([BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant()
}

function Get-Sha256 {
  param([Parameter(Mandatory = $true)][string]$Path)
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-ToolVersion {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Name
  )
  if ($Name -eq 'safe_tar') {
    return 'taiji-safe-tar/v1'
  }
  $versionInfo = (Get-Item -LiteralPath $Path -Force).VersionInfo
  foreach ($value in @($versionInfo.FileVersion, $versionInfo.ProductVersion)) {
    if (-not [string]::IsNullOrWhiteSpace([string]$value)) {
      return ([string]$value).Trim()
    }
  }
  $fallback = (Get-Item -LiteralPath $Path -Force).VersionInfo.FileDescription
  if (-not [string]::IsNullOrWhiteSpace([string]$fallback)) {
    return ([string]$fallback).Trim()
  }
  return [IO.Path]::GetFileName($Path)
}

function Get-ToolEvidence {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Name
  )
  Assert-RegularFile $Path "tool $Name" -AllowHardLink
  return [ordered]@{
    path = $Path
    bytes = (Get-Item -LiteralPath $Path -Force).Length
    sha256 = Get-Sha256 $Path
    version = Get-ToolVersion -Path $Path -Name $Name
  }
}

function Write-AtomicJson {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)]$Value
  )
  if (Test-Path -LiteralPath $Path) {
    throw "refusing to overwrite JSON: $Path"
  }
  $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
  $json = ConvertTo-CanonicalJson $Value
  [IO.File]::WriteAllText($temporary, $json + [char]10, [Text.UTF8Encoding]::new($false))
  Move-Item -LiteralPath $temporary -Destination $Path
}

if ($RunId -notmatch '^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{8}$') {
  throw "invalid run id: $RunId"
}
if ($SourceBranch -cne 'main') {
  throw "source branch must be main: $SourceBranch"
}
foreach ($identity in @($SourceCommit, $SourceTree)) {
  if ($identity -notmatch '^[0-9a-f]{40}$') {
    throw "source identity is invalid: $identity"
  }
}
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
  throw "candidate version is invalid: $Version"
}
if ($ExpectedCacheRequirementsSha256 -notmatch '^[0-9a-f]{64}$' -or
    $ExpectedCacheObservationSha256 -notmatch '^[0-9a-f]{64}$' -or
    $ExpectedSafeTarSha256 -notmatch '^[0-9a-f]{64}$' -or
    $ExpectedTargetConfigSha256 -notmatch '^[0-9a-f]{64}$' -or
    $ExpectedAssetProvenanceSha256 -notmatch '^[0-9a-f]{64}$' -or
    $ExpectedHostFactsSha256 -notmatch '^[0-9a-f]{64}$' -or
    $InputArchiveSha256 -notmatch '^[0-9a-f]{64}$' -or
    $InputManifestSha256 -notmatch '^[0-9a-f]{64}$' -or
    $InputSidecarSha256 -notmatch '^[0-9a-f]{64}$') {
  throw 'candidate identities must be lowercase SHA256'
}

if (Test-Path -LiteralPath $RunRoot) {
  $runItem = Get-Item -LiteralPath $RunRoot -Force
  if ($runItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "run root is a reparse point: $RunRoot"
  }
  if (-not $runItem.PSIsContainer) {
    throw "run root is not a directory: $RunRoot"
  }
} else {
  New-Item -ItemType Directory -Path $RunRoot | Out-Null
}
foreach ($child in @('input', 'source', 'staging', 'output', 'review', 'logs')) {
  New-Item -ItemType Directory -Path (Join-Path $RunRoot $child) -Force | Out-Null
}
New-Item -ItemType Directory -Path (Join-Path $RunRoot 'staging\cache') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $RunRoot 'staging\payload') -Force | Out-Null

Assert-RegularFile $InputManifestPath 'input manifest'
Assert-RegularFile $TargetConfigPath 'target config'
Assert-RegularFile $AssetProvenancePath 'asset provenance'
Assert-RegularFile $CacheRequirementsPath 'cache requirements'
Assert-RegularFile $SafeTarPath 'safe tar'

if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
  throw "safe source root is missing: $SourceRoot"
}
$sourceRootItem = Get-Item -LiteralPath $SourceRoot -Force
if ($sourceRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
  throw "source root is a reparse point: $SourceRoot"
}
$gitResidue = Get-ChildItem -LiteralPath $SourceRoot -Force -Recurse -ErrorAction Stop |
  Where-Object { $_.Name -eq '.git' -or ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
  Select-Object -First 1
if ($gitResidue) {
  throw "safe source root contains Git metadata or a reparse point: $($gitResidue.FullName)"
}

$target = Get-Content -LiteralPath $TargetConfigPath -Raw | ConvertFrom-Json
if ($target.schema -ne 'taiji-package-target/v2' -or $target.target_id -ne 'windows-x64') {
  throw 'target config identity is invalid'
}
$targetCanonicalSha = Get-CanonicalHash $target
if ($targetCanonicalSha -ne $ExpectedTargetConfigSha256) {
  throw 'target config canonical identity drifted'
}
$assetProvenance = Get-Content -LiteralPath $AssetProvenancePath -Raw | ConvertFrom-Json
$assetProvenanceCanonicalSha = Get-CanonicalHash $assetProvenance
if ($assetProvenanceCanonicalSha -ne $ExpectedAssetProvenanceSha256) {
  throw 'asset provenance canonical identity drifted'
}
$requirements = Get-Content -LiteralPath $CacheRequirementsPath -Raw | ConvertFrom-Json
if ((Get-CanonicalHash $requirements) -ne $ExpectedCacheRequirementsSha256) {
  throw 'cache requirements SHA256 drifted'
}

$safeTarHash = Get-Sha256 $SafeTarPath
if ($safeTarHash -ne $ExpectedSafeTarSha256) {
  throw 'safe tar SHA256 drifted'
}
$inputManifest = Get-Content -LiteralPath $InputManifestPath -Raw | ConvertFrom-Json
if ($inputManifest.source_commit -ne $SourceCommit) {
  throw 'input manifest source commit does not match session'
}
$TransferredObservationPath = Join-Path $RunRoot 'input\cache-observation.json'
Assert-RegularFile $TransferredObservationPath 'cache observation'
$TransferredObservation = Get-Content -LiteralPath $TransferredObservationPath -Raw | ConvertFrom-Json
if ($TransferredObservation.schema -ne 'taiji-windows-cache-observation/v1' -or
    $TransferredObservation.target_id -ne 'windows-x64' -or
    $TransferredObservation.requirements_sha256 -ne $ExpectedCacheRequirementsSha256) {
  throw 'cache observation schema or identity drifted before session initialization'
}
$TransferredObservationIdentity = [ordered]@{}
foreach ($property in $TransferredObservation.PSObject.Properties) {
  if ($property.Name -ne 'observed_at') {
    $TransferredObservationIdentity[$property.Name] = $property.Value
  }
}
if ([string]::IsNullOrWhiteSpace([string]$TransferredObservation.observed_at)) {
  throw 'cache observation observed_at is missing'
}
$TransferredObservationHash = Get-CanonicalHash $TransferredObservationIdentity
if ($TransferredObservationHash -ne $ExpectedCacheObservationSha256) {
  throw 'cache observation transfer drifted before session initialization'
}

$inputRoot = Join-Path $RunRoot 'input'
$archivePath = Join-Path $inputRoot $InputArchiveBasename
$sidecarPath = Join-Path $inputRoot $InputSidecarBasename
Assert-RegularFile $archivePath 'builder input archive'
Assert-RegularFile $sidecarPath 'builder input sidecar'
if ((Get-Item -LiteralPath $archivePath).Length -ne [int64]$InputArchiveBytes) {
  throw 'builder input archive bytes drifted before session initialization'
}
if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $InputArchiveSha256) {
  throw 'builder input archive sha256 drifted before session initialization'
}
if ((Get-Item -LiteralPath $InputManifestPath).Length -ne [int64]$InputManifestBytes) {
  throw 'builder input manifest bytes drifted before session initialization'
}
if ((Get-FileHash -LiteralPath $InputManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $InputManifestSha256) {
  throw 'builder input manifest sha256 drifted before session initialization'
}
if ((Get-Item -LiteralPath $sidecarPath).Length -ne [int64]$InputSidecarBytes) {
  throw 'builder input sidecar bytes drifted before session initialization'
}
if ((Get-FileHash -LiteralPath $sidecarPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $InputSidecarSha256) {
  throw 'builder input sidecar sha256 drifted before session initialization'
}
$hostFactsPath = Join-Path $inputRoot 'host-facts-sha256.txt'
Assert-RegularFile $hostFactsPath 'host facts sha256'
$hostFactsSha256 = (Get-Content -LiteralPath $hostFactsPath -Raw).Trim().ToLowerInvariant()
if ($hostFactsSha256 -ne $ExpectedHostFactsSha256) {
  throw 'host facts sha256 drifted before session initialization'
}
$session = [ordered]@{
  schema = 'taiji-windows-candidate-session/v1'
  run_id = $RunId
  target_id = 'windows-x64'
  version = $Version
  started_at = [DateTime]::UtcNow.ToString('o')
  source = [ordered]@{
    branch = $SourceBranch
    commit = $SourceCommit
    tree = $SourceTree
  }
  input = [ordered]@{
    archive = [ordered]@{
      basename = $InputArchiveBasename
      bytes = [int64]$InputArchiveBytes
      sha256 = $InputArchiveSha256
    }
    manifest = [ordered]@{
      basename = $InputManifestBasename
      bytes = [int64]$InputManifestBytes
      sha256 = $InputManifestSha256
    }
    sidecar = [ordered]@{
      basename = $InputSidecarBasename
      bytes = [int64]$InputSidecarBytes
      sha256 = $InputSidecarSha256
    }
  }
  identity = [ordered]@{
    target_config_sha256 = $ExpectedTargetConfigSha256
    asset_provenance_sha256 = $ExpectedAssetProvenanceSha256
    host_facts_sha256 = $ExpectedHostFactsSha256
  }
  paths = [ordered]@{
    run_root = $RunRoot
    source_root = $SourceRoot
    staging_root = Join-Path $RunRoot 'staging'
    staging_cache_root = Join-Path $RunRoot 'staging\cache'
    payload_root = Join-Path $RunRoot 'staging\payload'
    output_root = Join-Path $RunRoot 'output'
    review_root = Join-Path $RunRoot 'review'
    logs_root = Join-Path $RunRoot 'logs'
    remote_log = Join-Path $RunRoot 'logs\remote-build.log'
  }
  tools = [ordered]@{
    powershell = Get-ToolEvidence -Path $PowerShellPath -Name 'powershell'
    tar = Get-ToolEvidence -Path $TarPath -Name 'tar'
    node = Get-ToolEvidence -Path $NodePath -Name 'node'
    npm = Get-ToolEvidence -Path $NpmPath -Name 'npm'
    python = Get-ToolEvidence -Path $PythonPath -Name 'python'
    iscc = Get-ToolEvidence -Path $IsccPath -Name 'iscc'
    safe_tar = Get-ToolEvidence -Path $SafeTarPath -Name 'safe_tar'
  }
  cache = [ordered]@{
    root = $CacheRoot
    requirements_path = $CacheRequirementsPath
    requirements_sha256 = $ExpectedCacheRequirementsSha256
    observation_path = $TransferredObservationPath
    observation_sha256 = $ExpectedCacheObservationSha256
  }
  # cache_observation_sha256 remains bound to the finalized plan.
  boundaries = [ordered]@{
    installation = $false
    interactive_acceptance = $false
    production_license = $false
    signing = $false
    publication = $false
  }
}
Write-AtomicJson (Join-Path $RunRoot 'session.json') $session
Write-Host 'WINDOWS_CANDIDATE_SESSION_READY'
