[CmdletBinding()]
param(
  [string]$ProductRoot = 'D:\tw\source\taijiAgentv1.0',
  [string]$PythonStage = 'D:\tw\build\python-runtime',
  [string]$PayloadRoot = 'D:\tw\payload'
)

$ErrorActionPreference = 'Stop'

function Invoke-RobocopyChecked {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination,
    [string[]]$Options = @('/E')
  )

  New-Item -ItemType Directory -Force -Path $Destination | Out-Null
  & robocopy.exe $Source $Destination @Options
  $RobocopyExitCode = $LASTEXITCODE
  if ($RobocopyExitCode -ge 8) {
    throw "Robocopy failed with exit code $RobocopyExitCode`: $Source -> $Destination"
  }
}

if (-not (Test-Path -LiteralPath $ProductRoot -PathType Container)) {
  throw "Product root missing: $ProductRoot"
}

$ProductBranch = (& git -C $ProductRoot branch --show-current | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
  throw "Cannot read product branch: $ProductRoot"
}
if ($ProductBranch -ne 'codex/windows-local') {
  throw "Unexpected product branch: $ProductBranch"
}

$ProductDirty = (& git -C $ProductRoot status --porcelain=v1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
  throw "Cannot read product worktree status: $ProductRoot"
}
if ($ProductDirty) {
  throw 'Product worktree is dirty; commit or preserve changes before staging'
}

$PythonStageExe = Join-Path $PythonStage 'python.exe'
if (-not (Test-Path -LiteralPath $PythonStageExe -PathType Leaf)) {
  throw "Private Python missing: $PythonStageExe"
}

$PayloadParent = Split-Path -Parent $PayloadRoot
$BuildRoot = Join-Path $PayloadParent 'build'
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null

$FastTrackStatePath = Join-Path $PayloadParent 'logs\fast-track-state.json'
if (-not (Test-Path -LiteralPath $FastTrackStatePath -PathType Leaf)) {
  throw "Fast-track state missing: $FastTrackStatePath"
}
$FastTrackState = Get-Content -LiteralPath $FastTrackStatePath -Raw | ConvertFrom-Json
if ($FastTrackState.schema -ne 'taiji-windows-fast-track-state/v1') {
  throw "Unexpected fast-track state schema: $($FastTrackState.schema)"
}
$ExpectedCandidateCommit = [string]$FastTrackState.windows_local_candidate_commit
if ($ExpectedCandidateCommit -notmatch '^[0-9a-fA-F]{40}$') {
  throw "Invalid Windows candidate commit in fast-track state: $ExpectedCandidateCommit"
}
$ProductHead = (& git -C $ProductRoot rev-parse HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $ProductHead -notmatch '^[0-9a-fA-F]{40}$') {
  throw "Cannot resolve product HEAD: $ProductRoot"
}
if ($ProductHead -cne $ExpectedCandidateCommit) {
  throw "Product HEAD does not match fast-track state: expected=$ExpectedCandidateCommit actual=$ProductHead"
}

if (Test-Path -LiteralPath $PayloadRoot -PathType Container) {
  $PayloadHasContent = Get-ChildItem -LiteralPath $PayloadRoot -Force | Select-Object -First 1
  if ($PayloadHasContent) {
    $BackupSuffix = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
    $PayloadBackup = Join-Path $BuildRoot "payload-backup-$BackupSuffix"
    if (Test-Path -LiteralPath $PayloadBackup) {
      throw "Payload backup already exists: $PayloadBackup"
    }
    Move-Item -LiteralPath $PayloadRoot -Destination $PayloadBackup
    Write-Host "Previous payload preserved: $PayloadBackup"
  }
}
New-Item -ItemType Directory -Force -Path $PayloadRoot | Out-Null

$DesktopRoot = Join-Path $ProductRoot 'apps\taiji-desktop'
$DesktopPackage = Join-Path $DesktopRoot 'package.json'
if (-not (Test-Path -LiteralPath $DesktopPackage -PathType Leaf)) {
  throw "Desktop package missing: $DesktopPackage"
}

$ElectronDist = Join-Path $DesktopRoot 'node_modules\electron\dist'
$ElectronExe = Join-Path $ElectronDist 'electron.exe'
if (-not (Test-Path -LiteralPath $ElectronExe -PathType Leaf)) {
  throw "Prewarmed Electron executable missing before offline npm ci: $ElectronExe"
}
$ElectronDistStash = Join-Path $BuildRoot 'electron-dist-prewarmed'
if (Test-Path -LiteralPath $ElectronDistStash) {
  throw "Electron dist stash already exists; preserve or restore it before retrying: $ElectronDistStash"
}
Move-Item -LiteralPath $ElectronDist -Destination $ElectronDistStash
if (-not (Test-Path -LiteralPath (Join-Path $ElectronDistStash 'electron.exe') -PathType Leaf)) {
  throw "Electron dist stash failed: $ElectronDistStash"
}

$ElectronDistStashed = $true
try {
  Push-Location $DesktopRoot
  try {
    & npm ci --offline --ignore-scripts --no-audit --cache (Join-Path $PayloadParent 'cache\npm')
    if ($LASTEXITCODE -ne 0) {
      throw "Offline npm ci failed: $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
} finally {
  if ($ElectronDistStashed) {
    if (Test-Path -LiteralPath $ElectronDist) {
      throw "Offline npm ci unexpectedly created Electron dist; prewarmed dist remains at: $ElectronDistStash"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ElectronDist) | Out-Null
    Move-Item -LiteralPath $ElectronDistStash -Destination $ElectronDist
    $ElectronDistStashed = $false
  }
}

if (-not (Test-Path -LiteralPath $ElectronExe -PathType Leaf)) {
  throw "Restored Electron executable missing: $ElectronExe"
}

$PreviousElectronRunAsNode = $env:ELECTRON_RUN_AS_NODE
try {
  $env:ELECTRON_RUN_AS_NODE = '1'
  $ElectronIdentity = (& $ElectronExe -e "console.log(process.platform + ' ' + process.arch)" | Out-String).Trim()
  if ($LASTEXITCODE -ne 0) {
    throw "Electron identity check failed: $LASTEXITCODE"
  }
} finally {
  Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
  if ($null -ne $PreviousElectronRunAsNode) {
    $env:ELECTRON_RUN_AS_NODE = $PreviousElectronRunAsNode
  }
}
if ($ElectronIdentity -ne 'win32 x64') {
  throw "Unexpected Electron identity: $ElectronIdentity"
}

$CopyOptions = @('/E', '/R:2', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS')
Invoke-RobocopyChecked -Source $ElectronDist -Destination $PayloadRoot -Options $CopyOptions

$PayloadElectronExe = Join-Path $PayloadRoot 'electron.exe'
$PayloadAppExe = Join-Path $PayloadRoot 'TaijiAgent.exe'
if (-not (Test-Path -LiteralPath $PayloadElectronExe -PathType Leaf)) {
  throw "Staged Electron executable missing: $PayloadElectronExe"
}
Move-Item -LiteralPath $PayloadElectronExe -Destination $PayloadAppExe

$PayloadDesktop = Join-Path $PayloadRoot 'resources\app'
New-Item -ItemType Directory -Force -Path $PayloadDesktop | Out-Null
Copy-Item -LiteralPath $DesktopPackage -Destination (Join-Path $PayloadDesktop 'package.json') -Force

$DesktopSource = Join-Path $DesktopRoot 'src'
if (-not (Test-Path -LiteralPath $DesktopSource -PathType Container)) {
  throw "Desktop source missing: $DesktopSource"
}
Invoke-RobocopyChecked -Source $DesktopSource -Destination (Join-Path $PayloadDesktop 'src') -Options $CopyOptions

$PayloadPythonRoot = Join-Path $PayloadRoot 'hermes-local-lab\runtime\python'
Invoke-RobocopyChecked -Source $PythonStage -Destination $PayloadPythonRoot -Options $CopyOptions

$PythonPth = Join-Path $PayloadPythonRoot 'python311._pth'
if (-not (Test-Path -LiteralPath $PythonPth -PathType Leaf)) {
  throw "Private Python path file missing: $PythonPth"
}
[string[]]$PythonPthLines = @(
  'python311.zip',
  '.',
  '..\..\sources\hermes-agent',
  '..\..\sources\hermes-webui',
  'Lib\site-packages',
  'import site'
)
[System.IO.File]::WriteAllLines($PythonPth, $PythonPthLines, [System.Text.Encoding]::ASCII)
$WrittenPthLines = @(Get-Content -LiteralPath $PythonPth)
if (($WrittenPthLines.Count -ne $PythonPthLines.Count) -or
    ([string]::Join("`n", $WrittenPthLines) -cne [string]::Join("`n", $PythonPthLines))) {
  throw "Private Python path file was not rewritten exactly: $PythonPth"
}

$SourceCopyPrefix = @('/E', '/R:2', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS')
$SourceExcludedDirectories = @(
  '.git', '.ssh', '.gnupg', '.aws', '.hermes', '.taiji', '.taiji-agent',
  'venv*', '.venv*', 'node_modules', '__pycache__', '.pytest_cache', 'tests', 'test',
  'test-results', 'test_output', 'test-output', 'playwright-report', 'coverage', 'htmlcov',
  'privvy*', 'tmp'
)
$SourceExcludedFiles = @(
  '.git', '*.pyc', '*.pyo', '*.log', '*.pid', '.env', '.env.*', '*.db', '*.sqlite', '*.sqlite3',
  '*.ppk', 'privvy*', 'cli-config.yaml', '*.sqlite-wal', '*.sqlite-shm', '*.sqlite3-wal',
  '*.sqlite3-shm', '*.db-wal', '*.db-shm', '*-journal'
)
$PayloadAgent = Join-Path $PayloadRoot 'hermes-local-lab\sources\hermes-agent'
$PayloadWebui = Join-Path $PayloadRoot 'hermes-local-lab\sources\hermes-webui'
$PayloadDefaultConfig = Join-Path $PayloadRoot 'hermes-local-lab\config\taiji-default-config.yaml'
$TrackedSourceToken = [System.Guid]::NewGuid().ToString('N')
$TrackedSourceArchive = Join-Path $BuildRoot "tracked-sources-$TrackedSourceToken.tar"
$TrackedSourceRoot = Join-Path $BuildRoot "tracked-sources-$TrackedSourceToken"
try {
  & git -C $ProductRoot archive --format=tar "--output=$TrackedSourceArchive" $ExpectedCandidateCommit -- `
    'hermes-local-lab/sources/hermes-agent' `
    'hermes-local-lab/sources/hermes-webui' `
    'hermes-local-lab/config/taiji-default-config.yaml'
  if ($LASTEXITCODE -ne 0) {
    throw "Tracked source git archive failed: $LASTEXITCODE"
  }
  if (-not (Test-Path -LiteralPath $TrackedSourceArchive -PathType Leaf) -or
      (Get-Item -LiteralPath $TrackedSourceArchive).Length -eq 0) {
    throw "Tracked source archive missing or empty: $TrackedSourceArchive"
  }

  New-Item -ItemType Directory -Force -Path $TrackedSourceRoot | Out-Null
  & tar.exe -xf $TrackedSourceArchive -C $TrackedSourceRoot
  if ($LASTEXITCODE -ne 0) {
    throw "Tracked source archive extraction failed: $LASTEXITCODE"
  }

  $TrackedAgentSource = Join-Path $TrackedSourceRoot 'hermes-local-lab\sources\hermes-agent'
  $TrackedWebuiSource = Join-Path $TrackedSourceRoot 'hermes-local-lab\sources\hermes-webui'
  $TrackedDefaultConfig = Join-Path $TrackedSourceRoot 'hermes-local-lab\config\taiji-default-config.yaml'
  if (-not (Test-Path -LiteralPath $TrackedAgentSource -PathType Container)) {
    throw "Tracked Agent source missing after extraction: $TrackedAgentSource"
  }
  if (-not (Test-Path -LiteralPath $TrackedWebuiSource -PathType Container)) {
    throw "Tracked WebUI source missing after extraction: $TrackedWebuiSource"
  }
  if (-not (Test-Path -LiteralPath $TrackedDefaultConfig -PathType Leaf)) {
    throw "Tracked default config missing after extraction: $TrackedDefaultConfig"
  }

  $AgentCopyOptions = $SourceCopyPrefix + @('/XD') + $SourceExcludedDirectories + @(
    (Join-Path $TrackedAgentSource 'logs'),
    (Join-Path $TrackedAgentSource 'state')
  ) + @('/XF') + $SourceExcludedFiles
  $WebuiCopyOptions = $SourceCopyPrefix + @('/XD') + $SourceExcludedDirectories + @(
    (Join-Path $TrackedWebuiSource 'logs'),
    (Join-Path $TrackedWebuiSource 'state')
  ) + @('/XF') + $SourceExcludedFiles
  Invoke-RobocopyChecked -Source $TrackedAgentSource -Destination $PayloadAgent -Options $AgentCopyOptions
  Invoke-RobocopyChecked -Source $TrackedWebuiSource -Destination $PayloadWebui -Options $WebuiCopyOptions
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $PayloadDefaultConfig) | Out-Null
  Copy-Item -LiteralPath $TrackedDefaultConfig -Destination $PayloadDefaultConfig -Force

  if (-not (Test-Path -LiteralPath $PayloadDefaultConfig -PathType Leaf)) {
    throw "Required packaged default config missing: $PayloadDefaultConfig"
  }

  foreach ($RequiredWebuiPath in @('static', 'api', 'server.py')) {
    $RequiredPath = Join-Path $PayloadWebui $RequiredWebuiPath
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
      throw "Required WebUI payload path missing: $RequiredPath"
    }
  }

  $OfflineSnapshot = Join-Path $PayloadAgent 'agent\data\models_dev_snapshot.v1.json.gz'
  if (-not (Test-Path -LiteralPath $OfflineSnapshot -PathType Leaf)) {
    throw "Tracked Agent offline fallback snapshot missing: $OfflineSnapshot"
  }
} finally {
  if (Test-Path -LiteralPath $TrackedSourceArchive) {
    Remove-Item -LiteralPath $TrackedSourceArchive -Force
  }
  if (Test-Path -LiteralPath $TrackedSourceRoot) {
    Remove-Item -LiteralPath $TrackedSourceRoot -Recurse -Force
  }
}

$DiagnoseSource = Join-Path $ProductRoot 'packaging\windows\diagnose.ps1'
if (-not (Test-Path -LiteralPath $DiagnoseSource -PathType Leaf)) {
  throw "Windows diagnostic script missing: $DiagnoseSource"
}
$PayloadTools = Join-Path $PayloadRoot 'tools'
New-Item -ItemType Directory -Force -Path $PayloadTools | Out-Null
Copy-Item -LiteralPath $DiagnoseSource -Destination (Join-Path $PayloadTools 'diagnose.ps1') -Force

$PayloadPython = Join-Path $PayloadPythonRoot 'python.exe'
$ImportGate = @'
import os, pathlib, sys
import api, aiohttp, taiji_runtime, taiji_runtime_profile, taiji_license
import fastapi, uvicorn, yaml, cryptography, psutil
agent = pathlib.Path(os.environ["TAIJI_PAYLOAD_AGENT_ROOT"]).resolve()
webui = pathlib.Path(os.environ["TAIJI_PAYLOAD_WEBUI_ROOT"]).resolve()
packaged = pathlib.Path(os.environ["TAIJI_PAYLOAD_PACKAGED_CONFIG"]).resolve()
agent_files = [pathlib.Path(taiji_runtime_profile.__file__).resolve(), pathlib.Path(taiji_license.__file__).resolve()]
api_file = pathlib.Path(api.__file__).resolve()
print(sys.executable)
print(*(str(item) for item in agent_files), str(api_file), sep="\n")
assert all(item.parent == agent for item in agent_files)
assert webui in api_file.parents
assert packaged.is_file()
os.environ["TAIJI_WEBUI_PACKAGED_CONFIG"] = str(packaged)
from api.config import get_ui_visibility
nav = get_ui_visibility()["nav"]
visible = [key for key, value in nav.items() if value is True]
assert visible == ["chat", "tasks", "writing", "settings"], visible
print("PAYLOAD_MENU_POLICY_OK visible=" + ",".join(visible))
print("PAYLOAD_IMPORT_OK")
'@
$ImportGatePath = Join-Path $BuildRoot ("payload-import-gate-{0}.py" -f ([System.Guid]::NewGuid().ToString('N')))
try {
  [System.IO.File]::WriteAllText($ImportGatePath, $ImportGate)
  $env:TAIJI_PAYLOAD_AGENT_ROOT = $PayloadAgent
  $env:TAIJI_PAYLOAD_WEBUI_ROOT = $PayloadWebui
  $env:TAIJI_PAYLOAD_PACKAGED_CONFIG = $PayloadDefaultConfig
  Push-Location $PayloadAgent
  try {
    & $PayloadPython $ImportGatePath
    if ($LASTEXITCODE -ne 0) {
      throw "Payload private Python import gate failed: $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
} finally {
  Remove-Item -LiteralPath $ImportGatePath -Force -ErrorAction SilentlyContinue
  Remove-Item Env:TAIJI_PAYLOAD_AGENT_ROOT -ErrorAction SilentlyContinue
  Remove-Item Env:TAIJI_PAYLOAD_WEBUI_ROOT -ErrorAction SilentlyContinue
  Remove-Item Env:TAIJI_PAYLOAD_PACKAGED_CONFIG -ErrorAction SilentlyContinue
}

$PayloadDirectories = @(Get-ChildItem -LiteralPath $PayloadRoot -Recurse -Force -Directory)
$ForbiddenDirectory = $PayloadDirectories | Where-Object {
  $_.Name -eq '.git' -or $_.Name -eq '.ssh' -or $_.Name -eq '.gnupg' -or
  $_.Name -eq '.aws' -or $_.Name -like '*.app' -or $_.Name -like 'privvy*'
} | Select-Object -First 1
if ($ForbiddenDirectory) {
  throw "Forbidden payload directory: $($ForbiddenDirectory.FullName)"
}

$PayloadFiles = @(Get-ChildItem -LiteralPath $PayloadRoot -Recurse -Force -File)
$ForbiddenMetadataFile = $PayloadFiles | Where-Object {
  $_.Name -eq '.git' -or $_.Name -eq '.env' -or $_.Name -like '.env.*' -or
  $_.Name -like 'privvy*' -or $_.Name -eq 'cli-config.yaml' -or $_.Extension -eq '.ppk'
} | Select-Object -First 1
if ($ForbiddenMetadataFile) {
  throw "Git metadata or environment file in payload: $($ForbiddenMetadataFile.FullName)"
}

$ForbiddenDatabaseSidecar = $PayloadFiles | Where-Object {
  $_.Name -like '*.sqlite-wal' -or $_.Name -like '*.sqlite-shm' -or
  $_.Name -like '*.sqlite3-wal' -or $_.Name -like '*.sqlite3-shm' -or
  $_.Name -like '*.db-wal' -or $_.Name -like '*.db-shm' -or
  $_.Name -like '*-journal'
} | Select-Object -First 1
if ($ForbiddenDatabaseSidecar) {
  throw "Database sidecar in payload: $($ForbiddenDatabaseSidecar.FullName)"
}

$ForbiddenVenvPython = $PayloadFiles | Where-Object {
  $_.Name -in @('python', 'python.exe') -and
  $_.FullName.Replace('/', '\') -match '(?i)\\\.?venv[^\\]*\\bin\\python(?:\.exe)?$'
} | Select-Object -First 1
if ($ForbiddenVenvPython) {
  throw "Forbidden venv Python in payload: $($ForbiddenVenvPython.FullName)"
}

$PayloadSourceRoots = @($PayloadAgent, $PayloadWebui)
$PayloadSourceDirectories = @($PayloadSourceRoots | ForEach-Object {
  Get-ChildItem -LiteralPath $_ -Recurse -Force -Directory
})
$ForbiddenSourceDirectory = $PayloadSourceDirectories | Where-Object {
  $_.Name -eq 'tmp'
} | Select-Object -First 1
if ($ForbiddenSourceDirectory) {
  throw "Local runtime data directory in payload source: $($ForbiddenSourceDirectory.FullName)"
}

$SourceFiles = @($PayloadSourceRoots | ForEach-Object {
  Get-ChildItem -LiteralPath $_ -Recurse -Force -File
})
$ForbiddenSourceFile = $SourceFiles | Where-Object {
  $_.Name -eq '.env' -or $_.Name -like '.env.*' -or
  $_.Extension -in @('.db', '.sqlite', '.sqlite3') -or
  $_.Name -like '*.sqlite-wal' -or $_.Name -like '*.sqlite-shm' -or
  $_.Name -like '*.sqlite3-wal' -or $_.Name -like '*.sqlite3-shm' -or
  $_.Name -like '*.db-wal' -or $_.Name -like '*.db-shm' -or
  $_.Name -like '*-journal'
} | Select-Object -First 1
if ($ForbiddenSourceFile) {
  throw "Environment file or historical database in payload: $($ForbiddenSourceFile.FullName)"
}

$SecretFileNames = @('id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519')
$SecretFile = $PayloadFiles | Where-Object {
  $_.Name -in $SecretFileNames -or $_.Extension -in @('.p12', '.pfx')
} | Select-Object -First 1
if ($SecretFile) {
  throw "Possible build-user credential in payload: $($SecretFile.FullName)"
}

$PrivateKeyFile = $PayloadFiles | Where-Object {
    $_.Length -le 5MB -and $_.Extension -in @('.pem', '.key') -and
    (Select-String -LiteralPath $_.FullName -Pattern '-----BEGIN (?:RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----' -Quiet)
  } |
  Select-Object -First 1
if ($PrivateKeyFile) {
  throw "Private key material in payload: $($PrivateKeyFile.FullName)"
}

Write-Host "Windows payload staged: $PayloadRoot"
