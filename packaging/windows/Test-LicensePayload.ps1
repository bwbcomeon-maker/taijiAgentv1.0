[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$PayloadRoot,
  [Parameter(Mandatory = $true)][string]$ScratchRoot
)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PayloadRoot 'hermes-local-lab\runtime\python\python.exe'
$node = Join-Path $PayloadRoot 'hermes-local-lab\runtime\node\node.exe'
$script = [IO.Path]::Combine($PSScriptRoot, 'license_payload_smoke.py')
$previousPath = $env:PATH
try {
  # Match the installed Electron runtime rather than inheriting the builder's
  # broader PATH, which can hide missing absolute system-tool resolution.
  $env:PATH = (Split-Path -Parent $node) + ';' +
    (Split-Path -Parent $python) + ';' +
    (Join-Path $env:SystemRoot 'System32')
  $output = (& $python -I -B $script $PayloadRoot $ScratchRoot 2>&1 | Out-String)
} finally {
  $env:PATH = $previousPath
}
if ($LASTEXITCODE -ne 0 -or $output -notmatch 'WINDOWS_PAYLOAD_LICENSE_OK') {
  throw ('Windows payload license smoke failed: ' + $output)
}
Write-Output $output.TrimEnd()
