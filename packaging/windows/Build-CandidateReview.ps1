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

function Get-Sha256 {
  param([Parameter(Mandatory = $true)][string]$Path)
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
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

function Get-Sha256Bytes {
  param([Parameter(Mandatory = $true)][byte[]]$Bytes)
  $hash = [Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($hash.ComputeHash($Bytes)) -replace '-', '').ToLowerInvariant()
  } finally {
    $hash.Dispose()
  }
}

function Compare-ByteArrays {
  param([byte[]]$Left, [byte[]]$Right)
  if ($Left.Length -ne $Right.Length) { return $false }
  for ($index = 0; $index -lt $Left.Length; $index++) {
    if ($Left[$index] -ne $Right[$index]) { return $false }
  }
  return $true
}

function Write-DurableBytes {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][byte[]]$Bytes
  )
  if (Test-Path -LiteralPath $Path) {
    throw "refusing to overwrite review file: $Path"
  }
  $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
  try {
    $stream.Write($Bytes, 0, $Bytes.Length)
    $stream.Flush($true)
  } finally {
    $stream.Dispose()
  }
}

function Write-Utf8Text {
  param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Text)
  Write-DurableBytes -Path $Path -Bytes ([Text.UTF8Encoding]::new($false).GetBytes($Text))
}

function Write-AtomicJson {
  param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)]$Value)
  if (Test-Path -LiteralPath $Path) {
    throw "refusing to overwrite review file: $Path"
  }
  $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
  try {
    Write-DurableBytes -Path $temporary -Bytes (
      [Text.UTF8Encoding]::new($false).GetBytes((ConvertTo-CanonicalJson $Value) + [char]10)
    )
    Move-Item -LiteralPath $temporary -Destination $Path
  } finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
  }
}

function Append-Utf8Line {
  param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Line)
  $stream = [IO.File]::Open($Path, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::Read)
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Line + [char]10)
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush($true)
  } finally {
    $stream.Dispose()
  }
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
    Append-Utf8Line -Path $script:RemoteLog -Line "$Id PASS exit=0"
  } catch {
    $exitCode = if ($null -eq $LASTEXITCODE) { 1 } else { $LASTEXITCODE }
    Append-Utf8Line -Path $script:RemoteLog -Line "$Id FAIL exit=$exitCode"
    throw
  }
}

function ReReadAndVerifyReview {
  param(
    [Parameter(Mandatory = $true)][string]$ReviewRoot,
    [Parameter(Mandatory = $true)][string[]]$ExpectedNames
  )
  $actual = @(Get-ChildItem -LiteralPath $ReviewRoot -Force | ForEach-Object { $_.Name } | Sort-Object)
  $expectedSorted = @($ExpectedNames | Sort-Object)
  if (($actual -join '|') -ne ($expectedSorted -join '|')) {
    throw 'review exact set changed during final verification'
  }
  $identities = [ordered]@{}
  foreach ($name in $ExpectedNames) {
    $path = Join-Path $ReviewRoot $name
    Assert-RegularFile $path "review file $name"
    $bytes = [IO.File]::ReadAllBytes($path)
    $identities[$name] = [ordered]@{
      bytes = [int64]$bytes.Length
      sha256 = Get-Sha256Bytes $bytes
    }
  }
  return $identities
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
Write-Utf8Text -Path $RemoteLog -Text ("remote build started" + [char]10)

$OutputBaseName = "TaijiAgent-Setup-$Version-win-x64"
$ArtifactBasename = "$OutputBaseName.exe"
# review exact set includes TaijiAgent-Setup-$Version-win-x64.exe and TaijiAgent-Setup-$Version-win-x64.exe.sha256.
$OutputArtifactPath = Join-Path $OutputRoot $ArtifactBasename
$ArtifactPath = Join-Path $ReviewRoot $ArtifactBasename
$SidecarPath = Join-Path $ReviewRoot "$ArtifactBasename.sha256"
$ManifestPath = Join-Path $ReviewRoot 'taiji-package-manifest.json'
$FormalLogPath = Join-Path $ReviewRoot 'formal-build-tests.log'
$ReportBasename = ([string][char]0x6784) + [char]0x5efa + [char]0x62a5 + [char]0x544a + '.txt'
$ReportPath = Join-Path $ReviewRoot $ReportBasename
$RemoteStatePath = Join-Path $ReviewRoot 'run-state.json'
$MarkerPath = Join-Path $ReviewRoot '.build-success'
$InnoScript = Join-Path $PSScriptRoot 'TaijiAgent.iss'

# review exact set: seven regular files in review and one independent remote log.
$ReviewExpectedNames = @(
  $ArtifactBasename,
  "$ArtifactBasename.sha256",
  'taiji-package-manifest.json',
  'formal-build-tests.log',
  $ReportBasename,
  'run-state.json'
)
$ReviewExpectedBeforeMarker = @($ReviewExpectedNames)
$ReviewExpectedAfterMarker = @($ReviewExpectedNames + '.build-success')
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
  $desktopNpmCheckRoot = Join-Path $StagingRoot 'desktop-npm-check'
  if (-not (Test-Path -LiteralPath (Join-Path $StagingRoot 'payload-manifest.json') -PathType Leaf)) {
    throw 'staged payload manifest is missing'
  }
  Assert-RegularFile (Join-Path $desktopNpmCheckRoot 'package.json') 'staging desktop package'
  Assert-RegularFile (Join-Path $desktopNpmCheckRoot 'package-lock.json') 'staging desktop package lock'
  $npmCache = Join-Path $session.paths.staging_cache_root 'npm'
  if (-not (Test-Path -LiteralPath (Join-Path $npmCache '_cacache'))) {
    throw 'staging npm cache is missing'
  }
  Push-Location $desktopNpmCheckRoot
  try {
    $previousNpmErrorActionPreference = $ErrorActionPreference
    $npmExitCode = $null
    try {
      $ErrorActionPreference = 'Continue'
      & $session.tools.npm.path ci --offline --ignore-scripts --no-audit --cache $npmCache
      $npmExitCode = $LASTEXITCODE
    } finally {
      $ErrorActionPreference = $previousNpmErrorActionPreference
    }
    if ($null -eq $npmExitCode -or $npmExitCode -ne 0) {
      throw "offline npm ci failed: $npmExitCode"
    }
  } finally {
    Pop-Location
  }
}

Invoke-FormalCheck -Id "electron-win32-x64" -Action {
  $electron = Join-Path $PayloadRoot 'TaijiAgent.exe'
  Assert-RegularFile $electron 'staged Windows Electron executable'
  $previousElectronRunAsNode = $env:ELECTRON_RUN_AS_NODE
  $hadElectronRunAsNode = Test-Path Env:\ELECTRON_RUN_AS_NODE
  try {
    $env:ELECTRON_RUN_AS_NODE = '1'
    $electronOutput = (& $electron -e "console.log(process.platform + ' ' + process.arch)" 2>&1 | Out-String).Trim()
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
}

Invoke-FormalCheck -Id "payload-import-menu-policy" -Action {
  if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot 'resources\app\package.json') -PathType Leaf)) {
    throw 'payload package.json is missing'
  }
  if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot 'resources\app\src') -PathType Container)) {
    throw 'payload src is missing'
  }
  if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot 'hermes-local-lab\config\taiji-default-config.yaml') -PathType Leaf)) {
    throw 'packaged default config is missing'
  }
  if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot 'hermes-local-lab\sources\hermes-agent') -PathType Container)) {
    throw 'payload hermes-agent source is missing'
  }
  if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot 'hermes-local-lab\sources\hermes-webui') -PathType Container)) {
    throw 'payload hermes-webui source is missing'
  }
  $payloadPython = Join-Path $PayloadRoot 'hermes-local-lab\runtime\python\python.exe'
  Assert-RegularFile $payloadPython 'payload python runtime'
  $runtimeHelp = (& $payloadPython -I -B -m taiji_runtime.main --help 2>&1 | Out-String)
  if ($LASTEXITCODE -ne 0) {
    throw 'taiji_runtime.main help failed'
  }
  $GatePath = Join-Path $StagingRoot 'build-menu-gate.py'
  $importGate = @'
import os
import pathlib

payload = pathlib.Path(r"__PAYLOAD_ROOT__")
agent_root = (payload / "hermes-local-lab" / "sources" / "hermes-agent").resolve()
webui_root = (payload / "hermes-local-lab" / "sources" / "hermes-webui").resolve()
packaged_config = payload / "hermes-local-lab" / "config" / "taiji-default-config.yaml"
main_source = payload / "resources" / "app" / "src" / "main.js"
runtime_source = payload / "resources" / "app" / "src" / "windows-runtime.js"
assert agent_root.is_dir()
assert webui_root.is_dir()
assert packaged_config.is_file()
assert main_source.is_file()
assert runtime_source.is_file()
assert (payload / "resources" / "app" / "package.json").is_file()
assert (payload / "resources" / "app" / "src").is_dir()
os.environ["TAIJI_WEBUI_PACKAGED_CONFIG"] = str(packaged_config)
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
visibility = get_ui_visibility()
nav = {name for name, visible in visibility.get("nav", {}).items() if visible}
assert nav == {"chat", "tasks", "writing", "settings"}
source = main_source.read_text(encoding="utf-8")
runtime_source_text = runtime_source.read_text(encoding="utf-8")
assert "windowsRuntimeCommands" in source
assert "startWindowsProcess(commands.agent" in source
assert 'args: ["-m", "taiji_runtime.main", "gateway", "run", "--accept-hooks"]' in runtime_source_text
assert "TAIJI_WEBUI_PACKAGED_CONFIG" in runtime_source_text
print("PAYLOAD_MENU_POLICY_OK")
'@
  Write-Utf8Text -Path $GatePath -Text ($importGate.Replace('__PAYLOAD_ROOT__', $PayloadRoot.Replace('\', '\\')) + [char]10)
  $previousMenuErrorActionPreference = $ErrorActionPreference
  $menuExitCode = $null
  $menuOutput = ''
  try {
    $ErrorActionPreference = 'Continue'
    $menuOutput = (& $payloadPython -I -B $GatePath 2>&1 | Out-String)
    $menuExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousMenuErrorActionPreference
    Remove-Item -LiteralPath $GatePath -Force -ErrorAction SilentlyContinue
  }
  if ($null -eq $menuExitCode -or $menuExitCode -ne 0 -or $menuOutput -notmatch 'PAYLOAD_MENU_POLICY_OK') {
    if (-not [string]::IsNullOrWhiteSpace($menuOutput)) {
      Write-Output ($menuOutput.TrimEnd())
    }
    throw 'payload private python import/menu gate failed'
  }
}

Invoke-FormalCheck -Id "payload-hygiene-closure" -Action {
  $payloadHygieneRoot = ConvertTo-ExtendedPath $PayloadRoot
  $forbidden = Get-ChildItem -LiteralPath $payloadHygieneRoot -Force -Recurse |
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
  $innoLinksRoot = Join-Path ([IO.Path]::GetPathRoot($PayloadRoot)) 'tw\inno-links'
  New-Item -ItemType Directory -Path $innoLinksRoot -Force | Out-Null
  $innoPayloadRoot = Join-Path $innoLinksRoot ([string]$session.run_id)
  if (Test-Path -LiteralPath $innoPayloadRoot) {
    throw "Inno payload junction already exists: $innoPayloadRoot"
  }
  $innoPayloadJunction = New-Item -ItemType Junction -Path $innoPayloadRoot -Target $PayloadRoot
  if (-not ($innoPayloadJunction.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
      -not (Test-Path -LiteralPath (Join-Path $innoPayloadRoot 'TaijiAgent.exe') -PathType Leaf)) {
    throw "Inno payload junction verification failed: $innoPayloadRoot"
  }
  $isccArguments = @(
    "/DMyAppVersion=$Version",
    "/DPayloadRoot=$innoPayloadRoot",
    "/DOutputDir=$OutputRoot",
    "/DOutputBaseFilename=$OutputBaseName",
    $InnoScript
  )
  & $session.tools.iscc.path @isccArguments
  if ($LASTEXITCODE -ne 0) {
    throw "Inno compile failed: $LASTEXITCODE"
  }
  Assert-RegularFile $OutputArtifactPath 'Inno output artifact'
  if (Test-Path -LiteralPath $ArtifactPath) {
    throw "review artifact already exists: $ArtifactPath"
  }
  $artifactStream = [IO.File]::Open(
    $OutputArtifactPath,
    [IO.FileMode]::Open,
    [IO.FileAccess]::ReadWrite,
    [IO.FileShare]::None
  )
  try {
    $artifactStream.Flush($true)
  } finally {
    $artifactStream.Dispose()
  }
  Move-Item -LiteralPath $OutputArtifactPath -Destination $ArtifactPath
}

Invoke-FormalCheck -Id "installer-pe-version-authenticode" -Action {
  Assert-RegularFile $ArtifactPath 'candidate installer'
  $bytes = [IO.File]::ReadAllBytes($ArtifactPath)
  if ($bytes.Length -lt 256 -or [Text.Encoding]::ASCII.GetString($bytes, 0, 2) -cne 'MZ') {
    throw 'installer is not an MZ executable'
  }
  $peOffset = [BitConverter]::ToInt32($bytes, 0x3c)
  # PE\0\0 bytes are validated directly to avoid string-literal ambiguity.
  $expectedSignature = [byte[]](0x50, 0x45, 0x00, 0x00)
  $actualSignature = $bytes[$peOffset..($peOffset + 3)]
  if (-not (Compare-ByteArrays $actualSignature $expectedSignature)) {
    throw 'installer PE signature is invalid'
  }
  $machine = [BitConverter]::ToUInt16($bytes, $peOffset + 4).ToString('x4')
  $optionalMagic = [BitConverter]::ToUInt16($bytes, $peOffset + 24).ToString('x3')
  if ($machine -cne '014c' -or $optionalMagic -cne '10b') {
    throw 'installer PE format is not the expected Inno x86 PE32 bootstrap'
  }
  $versionInfo = (Get-Item -LiteralPath $ArtifactPath).VersionInfo
  $fileVersion = ([string]$versionInfo.FileVersion).Trim()
  $productVersion = ([string]$versionInfo.ProductVersion).Trim()
  if ($fileVersion -cne "$Version.0" -or $productVersion -cne "$Version.0") {
    throw 'installer FileVersion/ProductVersion is invalid'
  }
  $signature = Get-AuthenticodeSignature -FilePath $ArtifactPath
  if ($signature.Status.ToString() -cne 'NotSigned') {
    throw 'installer Authenticode status is not NotSigned'
  }
}

$formalLogLines = @()
$formalIndex = 1
foreach ($check in @($script:FormalChecks)) {
  $formalLogLines += ('{0:d2} {1} PASS exit=0' -f $formalIndex, [string]$check.id)
  $formalIndex += 1
}
$formalLogLines += 'SUMMARY PASS checks=7'
Write-Utf8Text -Path $FormalLogPath -Text (($formalLogLines -join [char]10) + [char]10)
Append-Utf8Line -Path $RemoteLog -Line 'SUMMARY PASS checks=7'

$artifactBytes = (Get-Item -LiteralPath $ArtifactPath).Length
$artifactSha256 = Get-Sha256 $ArtifactPath
Write-Utf8Text -Path $SidecarPath -Text ("$artifactSha256  $ArtifactBasename" + [char]10)
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
    pe_machine = '0x014c'
    pe_optional_magic = '0x10b'
    file_version = "$Version.0"
    product_version = "$Version.0"
    authenticode_status = 'NotSigned'
  }
  boundaries = $session.boundaries
  started_at = $session.started_at
  finished_at = [DateTime]::UtcNow.ToString('o')
}
Write-PackageManifest $ManifestPath $manifest

$remoteState = [ordered]@{
  schema = 'taiji-package-remote-run/v1'
  run_id = $session.run_id
  target_id = 'windows-x64'
  source_commit = $session.source.commit
  host_facts_sha256 = $session.identity.host_facts_sha256
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
$reportText = 'Windows candidate review PASS' + [char]10 +
  'run=' + [string]$session.run_id + [char]10
Write-Utf8Text -Path $ReportPath -Text $reportText

# fetch-review and fetch-log remain separate controller stages; logs\remote-build.log is independent.
$reviewIdentity = ReReadAndVerifyReview -ReviewRoot $ReviewRoot -ExpectedNames $ReviewExpectedBeforeMarker
$marker = [ordered]@{
  schema = 'taiji-package-build-success/v1'
  run_id = $session.run_id
  target_id = 'windows-x64'
  source_commit = $session.source.commit
  artifact_basename = $ArtifactBasename
  artifact_bytes = $reviewIdentity[$ArtifactBasename].bytes
  artifact_sha256 = $reviewIdentity[$ArtifactBasename].sha256
  package_manifest_basename = 'taiji-package-manifest.json'
  package_manifest_bytes = $reviewIdentity['taiji-package-manifest.json'].bytes
  package_manifest_sha256 = $reviewIdentity['taiji-package-manifest.json'].sha256
  formal_build_tests_log_basename = 'formal-build-tests.log'
  formal_build_tests_log_bytes = $reviewIdentity['formal-build-tests.log'].bytes
  formal_build_tests_log_sha256 = $reviewIdentity['formal-build-tests.log'].sha256
  report_basename = $ReportBasename
  report_bytes = $reviewIdentity[$ReportBasename].bytes
  report_sha256 = $reviewIdentity[$ReportBasename].sha256
  remote_state_basename = 'run-state.json'
  remote_state_bytes = $reviewIdentity['run-state.json'].bytes
  remote_state_sha256 = $reviewIdentity['run-state.json'].sha256
}
Write-SuccessMarker $MarkerPath $marker
$null = ReReadAndVerifyReview -ReviewRoot $ReviewRoot -ExpectedNames $ReviewExpectedAfterMarker
Write-Host 'WINDOWS_CANDIDATE_REVIEW_READY'
