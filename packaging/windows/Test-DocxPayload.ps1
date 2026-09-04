[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$PayloadRoot,
  [Parameter(Mandatory = $true)][string]$ScratchRoot
)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PayloadRoot 'hermes-local-lab\runtime\python\python.exe'
$script = Join-Path $PSScriptRoot 'docx_payload_smoke.py'
$output = (& $python -I -B $script $PayloadRoot $ScratchRoot 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $output -notmatch 'WINDOWS_PAYLOAD_DOCX_OK') {
  throw ('Windows payload DOCX smoke failed: ' + $output)
}
Write-Output $output.TrimEnd()
