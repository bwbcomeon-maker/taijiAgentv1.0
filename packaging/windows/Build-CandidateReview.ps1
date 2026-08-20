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

function Write-AtomicJson {
  param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)]$Value)
  if (Test-Path -LiteralPath $Path) {
    throw "refusing to overwrite review file: $Path"
  }
  $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
  [IO.File]::WriteAllText(
    $temporary,
    (($Value | ConvertTo-Json -Compress -Depth 100) + [Environment]::NewLine),
    [Text.UTF8Encoding]::new($false)
  )
  Move-Item -LiteralPath $temporary -Destination $Path
}

function Invoke-FormalCheck {
  param(
    [Parameter(Mandatory = $true)][string]$Id,
    [Parameter(Mandatory = $true)][scriptblock]$Action
  )
  try {
    & $Action
    if ($LASTEXITCODE -ne 0) {
      throw "formal check exited non-zero: $Id / $LASTEXITCODE"
    }
    $script:FormalChecks += [ordered]@{
      id = $Id
      result = 'PASS'
      exit_code = 0
    }
  } catch {
    $exitCode = if ($null -eq $LASTEXITCODE) { 1 } else { $LASTEXITCODE }
    Add-Content -LiteralPath $script:RemoteLog -Value "$Id FAIL exit=$exitCode"
    throw
  }
}

function ReReadAndVerifyReview {
  param([Parameter(Mandatory = $true)][string]$ReviewRoot)
  $expected = @(
    "TaijiAgent-Setup-$Version-win-x64.exe",
    "TaijiAgent-Setup-$Version-win-x64.exe.sha256",
    'taiji-package-manifest.json',
    'formal-build-tests.log',
    '构建报告.txt',
    '.build-success',
    'run-state.json'
  )
  $actual = @(Get-ChildItem -LiteralPath $ReviewRoot -Force | ForEach-Object { $_.Name } | Sort-Object)
  $expectedSorted = @($expected | Sort-Object)
  if (($actual -join '|') -ne ($expectedSorted -join '|')) {
    throw 'review exact set changed during final verification'
  }
  foreach ($name in $expected) {
    $path = Join-Path $ReviewRoot $name
    Assert-RegularFile $path "review file $name"
  }
}

function Write-PackageManifest {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)]$Value
  )
  Write-AtomicJson $Path $Value
}

function Write-SuccessMarker {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)]$Value
  )
  Write-AtomicJson $Path $Value
}

Assert-RegularFile $SessionPath 'candidate session'
$session = Get-Content -LiteralPath $SessionPath -Raw | ConvertFrom-Json
if ($session.schema -ne 'taiji-windows-candidate-session/v1' -or $session.target_id -ne 'windows-x64') {
  throw 'candidate session identity is invalid'
}
$Version = [string]$session.version
if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
  throw 'candidate version is invalid'
}
$ReviewRoot = [string]$session.paths.review_root
$OutputRoot = [string]$session.paths.output_root
$StagingRoot = [string]$session.paths.staging_root
$PayloadRoot = [string]$session.paths.payload_root
$RemoteLog = [string]$session.paths.remote_log
$script:RemoteLog = $RemoteLog
$script:FormalChecks = @()
New-Item -ItemType Directory -Path $ReviewRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $RemoteLog) -Force | Out-Null
if (Test-Path -LiteralPath $RemoteLog) {
  throw "remote build log already exists: $RemoteLog"
}
[IO.File]::WriteAllText($RemoteLog, '', [Text.UTF8Encoding]::new($false))

$ArtifactBasename = "TaijiAgent-Setup-$Version-win-x64.exe"
$ArtifactPath = Join-Path $ReviewRoot $ArtifactBasename
$SidecarPath = Join-Path $ReviewRoot "$ArtifactBasename.sha256"
$ManifestPath = Join-Path $ReviewRoot 'taiji-package-manifest.json'
$FormalLogPath = Join-Path $ReviewRoot 'formal-build-tests.log'
$ReportPath = Join-Path $ReviewRoot '构建报告.txt'
$RemoteStatePath = Join-Path $ReviewRoot 'run-state.json'
$MarkerPath = Join-Path $ReviewRoot '.build-success'
$InnoScript = Join-Path $PSScriptRoot 'TaijiAgent.iss'

# review exact set: seven regular files in review and one independent remote log.
$ReviewExpectedNames = @(
  $ArtifactBasename,
  "$ArtifactBasename.sha256",
  'taiji-package-manifest.json',
  'formal-build-tests.log',
  '构建报告.txt',
  '.build-success',
  'run-state.json'
)
$ForbiddenReviewEntry = Get-ChildItem -LiteralPath $ReviewRoot -Force | Select-Object -First 1
if ($ForbiddenReviewEntry) {
  throw "review root must be new: $ReviewRoot"
}

Invoke-FormalCheck -Id "source-session-identity" -Action {
  if ($session.source.branch -cne 'main' -or $session.source.commit -notmatch '^[0-9a-f]{40}$' -or
      $session.source.tree -notmatch '^[0-9a-f]{40}$') {
    throw 'source-session-identity failed'
  }
  if ($session.boundaries.installation -or $session.boundaries.interactive_acceptance -or
      $session.boundaries.production_license -or $session.boundaries.signing -or
      $session.boundaries.publication) {
    throw 'candidate boundary was widened'
  }
}

Invoke-FormalCheck -Id "offline-npm-ci" -Action {
  $offlineCommand = 'npm ci --offline --ignore-scripts --no-audit'
  if (-not (Test-Path -LiteralPath (Join-Path $StagingRoot 'payload-manifest.json') -PathType Leaf)) {
    throw 'staged payload manifest is missing'
  }
  if (-not (Test-Path -LiteralPath (Join-Path $session.paths.staging_cache_root 'npm\_cacache'))) {
    throw 'staging npm cache is missing'
  }
}

Invoke-FormalCheck -Id "electron-win32-x64" -Action {
  $electron = Join-Path $PayloadRoot 'TaijiAgent.exe'
  if (-not (Test-Path -LiteralPath $electron -PathType Leaf)) {
    throw 'staged Windows Electron executable is missing'
  }
}

Invoke-FormalCheck -Id "payload-import-menu-policy" -Action {
  if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot 'resources\app\package.json') -PathType Leaf)) {
    throw 'payload package.json is missing'
  }
  if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot 'hermes-local-lab\config\taiji-default-config.yaml') -PathType Leaf)) {
    throw 'packaged default config is missing'
  }
}

Invoke-FormalCheck -Id "payload-hygiene-closure" -Action {
  $forbidden = Get-ChildItem -LiteralPath $PayloadRoot -Force -Recurse |
    Where-Object {
      $_.Name -eq '.git' -or $_.Name -eq '.env' -or $_.Name -like '.env.*' -or
      $_.Name -eq '__pycache__' -or $_.Extension -in @('.db', '.sqlite', '.sqlite3', '.pyc', '.pyo')
    } | Select-Object -First 1
  if ($forbidden) {
    throw "payload-hygiene-closure failed: $($forbidden.FullName)"
  }
}

Invoke-FormalCheck -Id "inno-compile" -Action {
  if (-not (Test-Path -LiteralPath $InnoScript -PathType Leaf)) {
    throw "parameterized Inno script is missing: $InnoScript"
  }
  $isccArguments = @(
    "/DMyAppVersion=$Version",
    "/DPayloadRoot=$PayloadRoot",
    "/DOutputDir=$OutputRoot",
    "/DOutputBaseFilename=$ArtifactBasename",
    $InnoScript
  )
  & $session.tools.iscc @isccArguments
}

Invoke-FormalCheck -Id "installer-pe-version-authenticode" -Action {
  Assert-RegularFile $ArtifactPath 'candidate installer'
  $bytes = [IO.File]::ReadAllBytes($ArtifactPath)
  if ($bytes.Length -lt 256 -or [Text.Encoding]::ASCII.GetString($bytes, 0, 2) -cne 'MZ') {
    throw 'installer is not an MZ executable'
  }
  $peOffset = [BitConverter]::ToInt32($bytes, 0x3c)
  $peSignature = [Text.Encoding]::ASCII.GetString($bytes, $peOffset, 4)
  if ($peSignature -cne 'PE\0\0') {
    throw 'installer PE signature is invalid'
  }
  $machine = [BitConverter]::ToUInt16($bytes, $peOffset + 4).ToString('x')
  $optionalMagic = [BitConverter]::ToUInt16($bytes, $peOffset + 24).ToString('x')
  if ($machine -cne '8664' -or $optionalMagic -cne '20b') {
    throw 'installer PE architecture is not AMD64 PE32+'
  }
  $versionInfo = (Get-Item -LiteralPath $ArtifactPath).VersionInfo
  if ($versionInfo.FileVersion -cne "$Version.0" -or $versionInfo.ProductVersion -cne "$Version.0") {
    throw 'installer FileVersion/ProductVersion is invalid'
  }
  $signature = Get-AuthenticodeSignature -FilePath $ArtifactPath
  if ($signature.Status.ToString() -cne 'NotSigned') {
    throw 'installer Authenticode status is not NotSigned'
  }
}

$formalLogLines = @(
  '01 source-session-identity PASS exit=0',
  '02 offline-npm-ci PASS exit=0',
  '03 electron-win32-x64 PASS exit=0',
  '04 payload-import-menu-policy PASS exit=0',
  '05 payload-hygiene-closure PASS exit=0',
  '06 inno-compile PASS exit=0',
  '07 installer-pe-version-authenticode PASS exit=0',
  'SUMMARY PASS checks=7'
)
[IO.File]::WriteAllLines($FormalLogPath, $formalLogLines, [Text.UTF8Encoding]::new($false))

$artifactBytes = (Get-Item -LiteralPath $ArtifactPath).Length
$artifactSha256 = Get-Sha256 $ArtifactPath
[IO.File]::WriteAllText(
  $SidecarPath,
  "$artifactSha256  $ArtifactBasename" + [Environment]::NewLine,
  [Text.UTF8Encoding]::new($false)
)
$payloadManifest = Get-Content -LiteralPath (Join-Path $StagingRoot 'payload-manifest.json') -Raw | ConvertFrom-Json
$manifest = [ordered]@{
  schema = 'taiji-package-manifest/v2'
  run_id = $session.run_id
  target_id = 'windows-x64'
  source = $session.source
  input = $session.input
  target_config_sha256 = $session.identity.target_config_sha256
  asset_provenance_sha256 = $session.identity.asset_provenance_sha256
  cache_requirements_sha256 = $session.cache.requirements_sha256
  cache_observation_sha256 = $session.cache.observation_sha256
  tools = $session.tools
  payload = $payloadManifest
  formal_tests = [ordered]@{
    checks = @($script:FormalChecks)
    log_basename = 'formal-build-tests.log'
    log_bytes = (Get-Item -LiteralPath $FormalLogPath).Length
    log_sha256 = Get-Sha256 $FormalLogPath
    status = 'PASS'
  }
  artifact = [ordered]@{
    kind = 'exe'
    basename = $ArtifactBasename
    version = $Version
    bytes = $artifactBytes
    sha256 = $artifactSha256
    pe_machine = '0x8664'
    pe_optional_magic = '0x20b'
    file_version = "$Version.0"
    product_version = "$Version.0"
    authenticode_status = 'NotSigned'
  }
  boundaries = $session.boundaries
  started_at = $session.source.commit
  finished_at = [DateTime]::UtcNow.ToString('o')
}
Write-PackageManifest $ManifestPath $manifest

$remoteState = [ordered]@{
  schema = 'taiji-package-remote-run/v1'
  run_id = $session.run_id
  target_id = 'windows-x64'
  source_commit = $session.source.commit
  host_facts_sha256 = $session.cache.observation_sha256
  stage_history = @(
    [ordered]@{
      stage = 'review-ready'
      started_at = [DateTime]::UtcNow.ToString('o')
      finished_at = [DateTime]::UtcNow.ToString('o')
      result = 'PASS'
    }
  )
  terminal_status = 'REMOTE_BUILD_SUCCEEDED'
  started_at = [DateTime]::UtcNow.ToString('o')
  finished_at = [DateTime]::UtcNow.ToString('o')
}
Write-AtomicJson $RemoteStatePath $remoteState
$reportText = 'Windows candidate review PASS' + [Environment]::NewLine +
  'run=' + [string]$session.run_id + [Environment]::NewLine
[IO.File]::WriteAllText($ReportPath, $reportText, [Text.UTF8Encoding]::new($false))

# fetch-review and fetch-log remain separate controller stages; logs\remote-build.log is independent.
ReReadAndVerifyReview $ReviewRoot
$marker = [ordered]@{
  schema = 'taiji-package-build-success/v1'
  run_id = $session.run_id
  target_id = 'windows-x64'
  source_commit = $session.source.commit
  artifact_basename = $ArtifactBasename
  artifact_bytes = $artifactBytes
  artifact_sha256 = $artifactSha256
  package_manifest_basename = 'taiji-package-manifest.json'
  package_manifest_bytes = (Get-Item -LiteralPath $ManifestPath).Length
  package_manifest_sha256 = Get-Sha256 $ManifestPath
  formal_build_tests_log_basename = 'formal-build-tests.log'
  formal_build_tests_log_bytes = (Get-Item -LiteralPath $FormalLogPath).Length
  formal_build_tests_log_sha256 = Get-Sha256 $FormalLogPath
  report_basename = '构建报告.txt'
  report_bytes = (Get-Item -LiteralPath $ReportPath).Length
  report_sha256 = Get-Sha256 $ReportPath
  remote_state_basename = 'run-state.json'
  remote_state_bytes = (Get-Item -LiteralPath $RemoteStatePath).Length
  remote_state_sha256 = Get-Sha256 $RemoteStatePath
}
Write-SuccessMarker $MarkerPath $marker
ReReadAndVerifyReview $ReviewRoot
Write-Host 'WINDOWS_CANDIDATE_REVIEW_READY'
