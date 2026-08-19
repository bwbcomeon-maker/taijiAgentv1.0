[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
$InstallRoot = Split-Path -Parent $PSScriptRoot
$UserRoot = Join-Path $env:LOCALAPPDATA 'Taiji Agent'
$ReportDir = Join-Path $UserRoot 'diagnostics'
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
$ReportPath = Join-Path $ReportDir ("diagnostic-{0}.txt" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))

$Lines = New-Object System.Collections.Generic.List[string]
$Lines.Add("captured_at=$((Get-Date).ToUniversalTime().ToString('o'))")
$Lines.Add("computer=$env:COMPUTERNAME")
$Lines.Add("os=$((Get-CimInstance Win32_OperatingSystem).Caption)")
$Lines.Add("os_build=$((Get-CimInstance Win32_OperatingSystem).BuildNumber)")
$Lines.Add("install_root=$InstallRoot")

foreach ($relative in @(
  'TaijiAgent.exe',
  'hermes-local-lab\runtime\python\python.exe',
  'hermes-local-lab\sources\hermes-agent\taiji_runtime\main.py',
  'hermes-local-lab\sources\hermes-webui\server.py'
)) {
  $candidate = Join-Path $InstallRoot $relative
  $Lines.Add("file:$relative=$([bool](Test-Path $candidate))")
}

foreach ($url in @('http://127.0.0.1:18642/health','http://127.0.0.1:18787/health')) {
  try {
    $response = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3
    $Lines.Add("health:$url=$($response.StatusCode)")
  } catch {
    $Lines.Add("health:$url=unavailable")
  }
}

Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match '^(TaijiAgent|python)\.exe$' -and $_.ExecutablePath
} | ForEach-Object {
  $Lines.Add("process=$($_.Name)|pid=$($_.ProcessId)|path=$($_.ExecutablePath)")
}

$LogDir = Join-Path $UserRoot 'state\logs'
if (Test-Path $LogDir) {
  Get-ChildItem $LogDir -File | ForEach-Object {
    $Lines.Add("log=$($_.Name)|bytes=$($_.Length)|updated=$($_.LastWriteTimeUtc.ToString('o'))")
  }
}

$Lines | Set-Content $ReportPath -Encoding UTF8
Write-Host "诊断报告已生成：$ReportPath"
Start-Process explorer.exe -ArgumentList "/select,`"$ReportPath`""
