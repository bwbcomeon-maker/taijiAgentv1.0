[CmdletBinding()]
param(
  [string]$StatePath = 'D:\tw\logs\fast-track-state.json'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
  throw "Fast-track state missing: $StatePath"
}

$FastTrackState = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
if ($FastTrackState.schema -ne 'taiji-windows-fast-track-state/v1') {
  throw "Unexpected fast-track state schema: $($FastTrackState.schema)"
}

$TwRoot = [string]$FastTrackState.tw_root
$ProductRoot = [string]$FastTrackState.product_root
$PackagingRoot = [string]$FastTrackState.packaging_root
$PythonStage = [string]$FastTrackState.python_stage
$PayloadRoot = [string]$FastTrackState.payload_root
$OutputRoot = [string]$FastTrackState.output_root
$BuilderPython = [string]$FastTrackState.builder_python
$Iscc = [string]$FastTrackState.iscc
$Baseline = [string]$FastTrackState.upstream_baseline_commit
$BaselineTree = [string]$FastTrackState.upstream_tree
$BaselineArchiveHash = [string]$FastTrackState.upstream_archive_sha256
$FetchUrl = [string]$FastTrackState.source_remote
$WindowsCandidateCommit = [string]$FastTrackState.windows_local_candidate_commit
$PackagingCommit = [string]$FastTrackState.packaging_commit
$SetupExe = [string]$FastTrackState.setup_exe

$RequiredStateFields = [ordered]@{
  tw_root = $TwRoot
  product_root = $ProductRoot
  packaging_root = $PackagingRoot
  python_stage = $PythonStage
  payload_root = $PayloadRoot
  output_root = $OutputRoot
  builder_python = $BuilderPython
  iscc = $Iscc
  source_remote = $FetchUrl
}
foreach ($Entry in $RequiredStateFields.GetEnumerator()) {
  if ([string]::IsNullOrWhiteSpace([string]$Entry.Value)) {
    throw "Fast-track state field is empty: $($Entry.Key)"
  }
}

if ($Baseline -notmatch '^[0-9a-fA-F]{40}$') {
  throw "Invalid upstream baseline commit: $Baseline"
}
if ($BaselineTree -notmatch '^[0-9a-fA-F]{40}$') {
  throw "Invalid upstream tree: $BaselineTree"
}
if ($BaselineArchiveHash -notmatch '^[0-9a-fA-F]{64}$') {
  throw "Invalid upstream archive SHA256: $BaselineArchiveHash"
}
foreach ($OptionalCommit in @(
  [ordered]@{ name = 'windows_local_candidate_commit'; value = $WindowsCandidateCommit },
  [ordered]@{ name = 'packaging_commit'; value = $PackagingCommit }
)) {
  $OptionalCommitValue = [string]$OptionalCommit['value']
  if (-not [string]::IsNullOrWhiteSpace($OptionalCommitValue) -and
      $OptionalCommitValue -notmatch '^[0-9a-fA-F]{40}$') {
    throw "Invalid optional commit: $($OptionalCommit['name'])=$OptionalCommitValue"
  }
}

$FullyQualifiedWindowsPathPattern = '^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+(?:\\|$))'
$FullyQualifiedStatePaths = [ordered]@{
  tw_root = $TwRoot
  product_root = $ProductRoot
  packaging_root = $PackagingRoot
  python_stage = $PythonStage
  payload_root = $PayloadRoot
  output_root = $OutputRoot
  builder_python = $BuilderPython
  iscc = $Iscc
}
if (-not [string]::IsNullOrWhiteSpace($SetupExe)) {
  $FullyQualifiedStatePaths['setup_exe'] = $SetupExe
}
foreach ($Entry in $FullyQualifiedStatePaths.GetEnumerator()) {
  if ([string]$Entry.Value -notmatch $FullyQualifiedWindowsPathPattern) {
    throw "Fast-track state path is not fully qualified: $($Entry.Key)=$($Entry.Value)"
  }
}

$NormalizedTwRoot = [System.IO.Path]::GetFullPath($TwRoot).TrimEnd([char]'\')
$TwRootPrefix = $NormalizedTwRoot + '\'
$CoreStatePaths = [ordered]@{
  product_root = $ProductRoot
  packaging_root = $PackagingRoot
  python_stage = $PythonStage
  payload_root = $PayloadRoot
  output_root = $OutputRoot
  iscc = $Iscc
}
if (-not [string]::IsNullOrWhiteSpace($SetupExe)) {
  $CoreStatePaths['setup_exe'] = $SetupExe
}
foreach ($Entry in $CoreStatePaths.GetEnumerator()) {
  $NormalizedCorePath = [System.IO.Path]::GetFullPath([string]$Entry.Value).TrimEnd([char]'\')
  $AtTwRoot = $NormalizedCorePath.Equals($NormalizedTwRoot, [System.StringComparison]::OrdinalIgnoreCase)
  $BelowTwRoot = $NormalizedCorePath.StartsWith($TwRootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
  if (-not ($AtTwRoot -or $BelowTwRoot)) {
    throw "Fast-track core path is outside tw_root: $($Entry.Key)=$($Entry.Value)"
  }
}

$AgentRoot = "$ProductRoot\hermes-local-lab\sources\hermes-agent"
$WebuiRoot = "$ProductRoot\hermes-local-lab\sources\hermes-webui"
$SitePackages = "$PythonStage\Lib\site-packages"
$PayloadPython = "$PayloadRoot\hermes-local-lab\runtime\python\python.exe"
$PayloadAgent = "$PayloadRoot\hermes-local-lab\sources\hermes-agent"

$env:npm_config_cache = "$TwRoot\cache\npm"
$env:ELECTRON_CACHE = "$TwRoot\cache\electron"
$env:PIP_CACHE_DIR = "$TwRoot\cache\pip"
$env:TEMP = "$TwRoot\build\tmp"
$env:TMP = "$TwRoot\build\tmp"
$env:TAIJI_FAST_TRACK = '1'

if (-not $global:TaijiFastTrackTranscriptStarted) {
  Start-Transcript -Path "$TwRoot\logs\build.log" -Append | Out-Null
  $global:TaijiFastTrackTranscriptStarted = $true
}

Write-Host "Fast-track state loaded: phase=$($FastTrackState.phase), baseline=$Baseline"
