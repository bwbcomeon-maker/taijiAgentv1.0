[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$PayloadRoot,
  [Parameter(Mandatory = $true)][string]$ScratchRoot
)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PayloadRoot 'hermes-local-lab\runtime\python\python.exe'
$script = [IO.Path]::Combine($PSScriptRoot, 'license_payload_smoke.py')
$output = (& $python -I -B $script $PayloadRoot $ScratchRoot 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $output -notmatch 'WINDOWS_PAYLOAD_LICENSE_OK') {
  throw ('Windows payload license smoke failed: ' + $output)
}
Write-Output $output.TrimEnd()
