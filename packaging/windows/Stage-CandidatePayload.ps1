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

function Get-Sha256 {
  param([Parameter(Mandatory = $true)][string]$Path)
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-CanonicalHash {
  param([Parameter(Mandatory = $true)]$Value)
  $json = $Value | ConvertTo-Json -Compress -Depth 100
  $bytes = [Text.Encoding]::UTF8.GetBytes($json)
  $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
  return ([BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant()
}

Assert-RegularFile $SessionPath 'candidate session'
$session = Get-Content -LiteralPath $SessionPath -Raw | ConvertFrom-Json
if ($session.schema -ne 'taiji-windows-candidate-session/v1' -or $session.target_id -ne 'windows-x64') {
  throw 'candidate session identity is invalid'
}
foreach ($key in @('source_root', 'staging_root', 'staging_cache_root', 'payload_root')) {
  if ([string]::IsNullOrWhiteSpace([string]$session.paths.$key)) {
    throw "candidate session path is empty: $key"
  }
}
if ($session.source.branch -cne 'main' -or $session.source.commit -notmatch '^[0-9a-f]{40}$' -or
    $session.source.tree -notmatch '^[0-9a-f]{40}$') {
  throw 'candidate source identity is invalid'
}

$sourceRoot = [string]$session.paths.source_root
$stagingRoot = [string]$session.paths.staging_root
$stagingCacheRoot = [string]$session.paths.staging_cache_root
$payloadRoot = [string]$session.paths.payload_root
if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
  throw "safe source root is missing: $sourceRoot"
}
$sourceResidue = Get-ChildItem -LiteralPath $sourceRoot -Force -Recurse |
  Where-Object { $_.Name -eq '.git' -or ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
  Select-Object -First 1
if ($sourceResidue) {
  throw "safe source contains Git metadata or a reparse point: $($sourceResidue.FullName)"
}

if (Test-Path -LiteralPath $stagingRoot) {
  $existing = Get-ChildItem -LiteralPath $stagingRoot -Force | Select-Object -First 1
  if ($existing) {
    throw "staging root is occupied: $stagingRoot"
  }
} else {
  New-Item -ItemType Directory -Path $stagingRoot | Out-Null
}
New-Item -ItemType Directory -Path $stagingCacheRoot -Force | Out-Null
New-Item -ItemType Directory -Path $payloadRoot -Force | Out-Null

$requirements = Get-Content -LiteralPath $session.cache.requirements_path -Raw | ConvertFrom-Json
$requirementsHash = Get-CanonicalHash $requirements
if ($requirementsHash -ne $session.cache.requirements_sha256) {
  throw 'cache requirements identity drifted before staging'
}
Assert-RegularFile $session.cache.observation_path 'cache observation'
$observation = Get-Content -LiteralPath $session.cache.observation_path -Raw | ConvertFrom-Json
$observationIdentity = [ordered]@{}
foreach ($property in $observation.psobject.Properties) {
  if ($property.Name -ne 'observed_at') {
    $observationIdentity[$property.Name] = $property.Value
  }
}
if ((Get-CanonicalHash $observationIdentity) -ne $session.cache.observation_sha256) {
  throw 'cache observation identity drifted before staging'
}

$sharedCacheRoot = [string]$session.cache.root
foreach ($entry in @($requirements.entries)) {
  $source = Join-Path $sharedCacheRoot $entry.relative_path.Replace('/', '\')
  $destination = Join-Path $stagingCacheRoot $entry.relative_path.Replace('/', '\')
  if (-not (Test-Path -LiteralPath $source)) {
    throw "WINDOWS_CACHE_MISSING: $($entry.id)"
  }
  if ($entry.type -eq 'directory') {
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $source '*') -Destination $destination -Recurse -Force
  } else {
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination
  }
}

$desktopRoot = Join-Path $sourceRoot 'apps\taiji-desktop'
$packageJson = Join-Path $desktopRoot 'package.json'
Assert-RegularFile $packageJson 'desktop package'
$stagingNpmCache = Join-Path $stagingCacheRoot 'npm'
if (-not (Test-Path -LiteralPath (Join-Path $stagingNpmCache '_cacache'))) {
  throw 'WINDOWS_CACHE_MISSING: npm cache'
}
$offlineNpmCommand = 'npm ci --offline --ignore-scripts --no-audit'
Push-Location $desktopRoot
try {
  & $session.tools.npm ci --offline --ignore-scripts --no-audit --cache $stagingNpmCache
  if ($LASTEXITCODE -ne 0) {
    throw "offline npm ci failed: $LASTEXITCODE"
  }
} finally {
  Pop-Location
}

Copy-Item -LiteralPath $desktopRoot -Destination (Join-Path $payloadRoot 'resources\app') -Recurse -Force
$pythonSource = Join-Path $stagingCacheRoot 'python-runtime'
$pythonDestination = Join-Path $payloadRoot 'hermes-local-lab\runtime\python'
if (-not (Test-Path -LiteralPath (Join-Path $pythonSource 'python.exe'))) {
  throw 'WINDOWS_CACHE_MISSING: python.exe'
}
Copy-Item -LiteralPath $pythonSource -Destination $pythonDestination -Recurse -Force
$electronArchive = Join-Path $stagingCacheRoot 'electron\electron-v39.8.10-win32-x64.zip'
Assert-RegularFile $electronArchive 'Electron cache archive'
Expand-Archive -LiteralPath $electronArchive -DestinationPath $payloadRoot -Force

$forbiddenDirectories = @('.git', '.ssh', '.gnupg', '.aws', '__pycache__', 'node_modules')
$forbiddenExtensions = @('.db', '.sqlite', '.sqlite3', '.pyc', '.pyo')
# payload hygiene patterns include *.db, *.sqlite and *.sqlite3.
$forbiddenPatterns = @('*.db', '*.sqlite', '*.sqlite3', '*.pyc', '*.pyo')
foreach ($item in @(Get-ChildItem -LiteralPath $payloadRoot -Force -Recurse)) {
  if ($item.Name -in $forbiddenDirectories -or $item.Extension -in $forbiddenExtensions -or
      $item.Name -like $forbiddenPatterns[0] -or $item.Name -like $forbiddenPatterns[1] -or
      $item.Name -like $forbiddenPatterns[2] -or $item.Name -like $forbiddenPatterns[3] -or
      $item.Name -like $forbiddenPatterns[4] -or
      $item.Name -eq '.env' -or $item.Name -like '.env.*' -or
      $item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "payload-hygiene-closure failed: $($item.FullName)"
  }
}

$payloadEntries = @(
  Get-ChildItem -LiteralPath $payloadRoot -File -Recurse -Force |
    ForEach-Object {
      $relative = $_.FullName.Substring($payloadRoot.Length + 1).Replace('\', '/')
      [ordered]@{
        path = $relative
        bytes = $_.Length
        sha256 = Get-Sha256 $_.FullName
      }
    } | Sort-Object path
)
$payloadManifest = [ordered]@{
  schema = 'taiji-windows-payload-manifest/v1'
  source_commit = $session.source.commit
  source_tree = $session.source.tree
  entries = @($payloadEntries)
  file_count = $payloadEntries.Count
  total_bytes = ($payloadEntries | Measure-Object -Property bytes -Sum).Sum
}
$payloadManifestHash = Get-CanonicalHash $payloadManifest
$payloadManifest.manifest_sha256 = $payloadManifestHash
$manifestPath = Join-Path $stagingRoot 'payload-manifest.json'
$manifestJson = $payloadManifest | ConvertTo-Json -Compress -Depth 100
[IO.File]::WriteAllText($manifestPath, $manifestJson + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
Write-Host 'WINDOWS_CANDIDATE_PAYLOAD_STAGED'
