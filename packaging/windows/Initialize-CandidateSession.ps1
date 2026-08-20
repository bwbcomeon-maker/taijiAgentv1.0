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
  [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = 'Stop'

function Assert-RegularFile {
  param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Label)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Label is missing: $Path"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "$Label is a reparse point: $Path"
  }
  if ($item.LinkType) {
    throw "$Label is a link: $Path"
  }
}

function ConvertTo-CanonicalJson {
  param([Parameter(Mandatory = $true)]$Value)
  return ($Value | ConvertTo-Json -Compress -Depth 100)
}

function Get-Sha256 {
  param([Parameter(Mandatory = $true)][string]$Path)
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
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
  [IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
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
    $ExpectedSafeTarSha256 -notmatch '^[0-9a-f]{64}$') {
  throw 'cache and safe-tar identities must be lowercase SHA256'
}

if (Test-Path -LiteralPath $RunRoot) {
  $runItem = Get-Item -LiteralPath $RunRoot -Force
  if ($runItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "run root is a reparse point: $RunRoot"
  }
  throw "run root already exists: $RunRoot"
}
New-Item -ItemType Directory -Path $RunRoot | Out-Null
foreach ($child in @('input', 'source', 'staging', 'output', 'review', 'logs')) {
  New-Item -ItemType Directory -Path (Join-Path $RunRoot $child) | Out-Null
}
New-Item -ItemType Directory -Path (Join-Path $RunRoot 'staging\cache') | Out-Null
New-Item -ItemType Directory -Path (Join-Path $RunRoot 'staging\payload') | Out-Null

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
$requirements = Get-Content -LiteralPath $CacheRequirementsPath -Raw | ConvertFrom-Json
$requirementsCanonical = ConvertTo-CanonicalJson $requirements
$requirementsTemporary = Join-Path $RunRoot 'input\cache-requirements.canonical.json'
[IO.File]::WriteAllText($requirementsTemporary, $requirementsCanonical, [Text.UTF8Encoding]::new($false))
if ((Get-Sha256 $requirementsTemporary) -ne $ExpectedCacheRequirementsSha256) {
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

$cacheEntries = @()
foreach ($requirement in @($requirements.entries)) {
  $entryRoot = Join-Path $CacheRoot $requirement.relative_path.Replace('/', '\')
  if ($requirement.type -eq 'directory') {
    if (-not (Test-Path -LiteralPath $entryRoot -PathType Container)) {
      throw "WINDOWS_CACHE_MISSING: $($requirement.id)"
    }
    foreach ($member in @($requirement.required_members)) {
      if (-not (Test-Path -LiteralPath (Join-Path $entryRoot $member.Replace('/', '\')))) {
        throw "WINDOWS_CACHE_MISSING: $($requirement.id)/$member"
      }
    }
    $members = @(
      Get-ChildItem -LiteralPath $entryRoot -File -Recurse -Force |
        ForEach-Object {
          [ordered]@{
            path = $_.FullName.Substring($entryRoot.Length + 1).Replace('\', '/')
            bytes = $_.Length
            sha256 = (Get-Sha256 $_.FullName)
          }
        } | Sort-Object path
    )
    $entryBytes = ($members | Measure-Object -Property bytes -Sum).Sum
    $entryHash = [Security.Cryptography.SHA256]::Create().ComputeHash(
      [Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson ([ordered]@{members = $members})))
    )
    $entrySha = ([BitConverter]::ToString($entryHash) -replace '-', '').ToLowerInvariant()
  } else {
    Assert-RegularFile $entryRoot 'Electron cache archive'
    $members = @()
    $entryBytes = (Get-Item -LiteralPath $entryRoot).Length
    $entrySha = Get-Sha256 $entryRoot
  }
  $cacheEntries += [ordered]@{
    id = $requirement.id
    type = $requirement.type
    relative_path = $requirement.relative_path
    bytes = [int64]$entryBytes
    sha256 = $entrySha
    members = @($members)
  }
}

$cacheObservation = [ordered]@{
  schema = 'taiji-windows-cache-observation/v1'
  target_id = 'windows-x64'
  requirements_sha256 = $ExpectedCacheRequirementsSha256
  cache_root = $CacheRoot
  entries = @($cacheEntries)
  observed_at = [DateTime]::UtcNow.ToString('o')
}
$observationIdentity = [ordered]@{}
foreach ($key in $cacheObservation.Keys) {
  if ($key -ne 'observed_at') {
    $observationIdentity[$key] = $cacheObservation[$key]
  }
}
$observationHashBytes = [Security.Cryptography.SHA256]::Create().ComputeHash(
  [Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $observationIdentity))
)
$observationHash = ([BitConverter]::ToString($observationHashBytes) -replace '-', '').ToLowerInvariant()
if ($observationHash -ne $ExpectedCacheObservationSha256) {
  throw 'WINDOWS_CACHE_MISSING: cache observation changed before staging'
}
Write-AtomicJson (Join-Path $RunRoot 'input\cache-observation.json') $cacheObservation

$inputRoot = Join-Path $RunRoot 'input'
$session = [ordered]@{
  schema = 'taiji-windows-candidate-session/v1'
  run_id = $RunId
  target_id = 'windows-x64'
  version = $Version
  source = [ordered]@{
    branch = $SourceBranch
    commit = $SourceCommit
    tree = $SourceTree
  }
  input = [ordered]@{
    manifest = [ordered]@{
      basename = [IO.Path]::GetFileName($InputManifestPath)
      bytes = (Get-Item -LiteralPath $InputManifestPath).Length
      sha256 = Get-Sha256 $InputManifestPath
    }
  }
  identity = [ordered]@{
    target_config_sha256 = Get-Sha256 $TargetConfigPath
    asset_provenance_sha256 = Get-Sha256 $AssetProvenancePath
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
    powershell = $PowerShellPath
    tar = $TarPath
    node = $NodePath
    npm = $NpmPath
    python = $PythonPath
    iscc = $IsccPath
    safe_tar = $SafeTarPath
  }
  cache = [ordered]@{
    root = $CacheRoot
    requirements_path = $CacheRequirementsPath
    requirements_sha256 = $ExpectedCacheRequirementsSha256
    observation_path = Join-Path $RunRoot 'input\cache-observation.json'
    observation_sha256 = $ExpectedCacheObservationSha256
  }
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
