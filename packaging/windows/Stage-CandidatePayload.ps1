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

function Assert-CacheFile {
  param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Label)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Label is missing: $Path"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
      ($item.LinkType -and [string]$item.LinkType -cne 'HardLink')) {
    throw "$Label is not a safe cache file: $Path"
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

function Join-PathText {
  param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Child
  )
  return $Root.TrimEnd('\') + '\' + $Child.TrimStart('\')
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

function Get-Sha256 {
  param([Parameter(Mandatory = $true)][string]$Path)
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-Sha256Bytes {
  param([Parameter(Mandatory = $true)][byte[]]$Bytes)
  $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($Bytes)
  return ([BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant()
}

function Get-CanonicalHash {
  param([Parameter(Mandatory = $true)]$Value)
  return Get-Sha256Bytes ([Text.Encoding]::UTF8.GetBytes((ConvertTo-CanonicalJson $Value)))
}

function Write-Utf8Text {
  param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Text)
  [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}

function Copy-DirectoryChildren {
  param([Parameter(Mandatory = $true)][string]$Source, [Parameter(Mandatory = $true)][string]$Destination)
  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  foreach ($child in @(Get-ChildItem -LiteralPath $Source -Force)) {
    Copy-Item -LiteralPath $child.FullName -Destination (Join-PathText $Destination $child.Name) -Recurse -Force
  }
}

function Copy-ProductSourceChildren {
  param([Parameter(Mandatory = $true)][string]$Source, [Parameter(Mandatory = $true)][string]$Destination)
  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  foreach ($child in @(Get-ChildItem -LiteralPath $Source -Force)) {
    if ($child.Name -ceq '.env' -or $child.Name -like '.env.*') {
      continue
    }
    Copy-Item -LiteralPath $child.FullName -Destination (Join-PathText $Destination $child.Name) -Recurse -Force
  }
}

function Copy-PythonRuntimeFile {
  param(
    [Parameter(Mandatory = $true)][System.IO.FileInfo]$File,
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [Parameter(Mandatory = $true)][string]$DestinationRoot
  )
  $relative = $File.FullName.Substring($SourceRoot.Length).TrimStart('\')
  $relativeLeaf = [IO.Path]::GetFileName($relative)
  if ($relative -like '__pycache__\*' -or $relative -like '*\__pycache__\*' -or
      $relativeLeaf -like '*.pyc' -or $relativeLeaf -like '*.pyo') {
    return
  }
  if (($File.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
      ($File.LinkType -and [string]$File.LinkType -cne 'HardLink')) {
    throw "Python cache contains an unsafe file: $($File.FullName)"
  }
  $destination = Join-PathText $DestinationRoot $relative
  New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
  Copy-Item -LiteralPath $File.FullName -Destination $destination -Force
}

function Normalize-ZipMemberName {
  param([string]$Name)
  return $Name.Replace('\', '/')
}

function Test-SafePosixPath {
  param([string]$Path)
  if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
  if ($Path.IndexOf([char]0) -ge 0 -or $Path.Contains('\') -or $Path.Contains(':')) { return $false }
  if ($Path.StartsWith('/') -or $Path.EndsWith('/')) { return $false }
  if ($Path.Normalize([System.Text.NormalizationForm]::FormC) -cne $Path) { return $false }
  foreach ($part in $Path.Split('/')) {
    if ([string]::IsNullOrEmpty($part) -or $part -eq '.' -or $part -eq '..') { return $false }
  }
  return $true
}

function Test-SafeZipMemberName {
  param([string]$Name)
  if ([string]::IsNullOrEmpty($Name) -or $Name.IndexOf([char]0) -ge 0) { return $false }
  $candidate = Normalize-ZipMemberName $Name
  if ($candidate.EndsWith('/')) {
    $candidate = $candidate.Substring(0, $candidate.Length - 1)
  }
  if (-not (Test-SafePosixPath $candidate)) { return $false }
  foreach ($part in $candidate.Split('/')) {
    if ($part.EndsWith('.') -or $part.EndsWith(' ')) { return $false }
  }
  return $true
}

function Get-PathIdentity {
  param([string]$Path)
  return $Path.Normalize([System.Text.NormalizationForm]::FormC).ToLowerInvariant()
}

function Expand-SafeZipArchive {
  param(
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [Parameter(Mandatory = $true)][string]$DestinationRoot
  )
  [void](Add-Type -AssemblyName System.IO.Compression.FileSystem)
  $archive = [System.IO.Compression.ZipFile]::OpenRead($ArchivePath)
  try {
    $seen = @{}
    foreach ($entry in @($archive.Entries)) {
      $normalized = Normalize-ZipMemberName ([string]$entry.FullName)
      if (-not (Test-SafeZipMemberName $normalized)) {
        throw "Electron cache contains an unsafe ZIP member: $normalized"
      }
      $relative = $normalized.TrimEnd('/')
      $identity = Get-PathIdentity $relative
      if ($seen.ContainsKey($identity)) {
        throw "Electron cache contains a duplicate ZIP member: $relative"
      }
      $seen[$identity] = $true
      $destination = Join-PathText $DestinationRoot ($relative.Replace('/', '\'))
      if ($normalized.EndsWith('/')) {
        if (Test-Path -LiteralPath $destination -PathType Leaf) {
          throw "Electron cache directory collides with a file: $relative"
        }
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        continue
      }
      $parent = [IO.Path]::GetDirectoryName($destination)
      New-Item -ItemType Directory -Path $parent -Force | Out-Null
      $sourceStream = $entry.Open()
      try {
        $destinationStream = [IO.File]::Open(
          $destination,
          [IO.FileMode]::CreateNew,
          [IO.FileAccess]::Write,
          [IO.FileShare]::None
        )
        try {
          $sourceStream.CopyTo($destinationStream)
        } finally {
          $destinationStream.Dispose()
        }
      } finally {
        $sourceStream.Dispose()
      }
    }
  } finally {
    $archive.Dispose()
  }
}

function Get-ObservationEntry {
  param(
    [Parameter(Mandatory = $true)]$Observation,
    [Parameter(Mandatory = $true)][string]$Id
  )
  $matches = @($Observation.entries | Where-Object { [string]$_.id -ceq $Id })
  if ($matches.Count -ne 1) {
    throw "cache observation entry is missing or duplicated: $Id"
  }
  return $matches[0]
}

function Test-ExcludedPythonMember {
  param([Parameter(Mandatory = $true)][string]$Path)
  $parts = @($Path.Replace('\', '/').Split('/'))
  $leaf = $parts[$parts.Count - 1]
  return ($parts -contains '__pycache__') -or
    $leaf.EndsWith('.pyc', [StringComparison]::OrdinalIgnoreCase) -or
    $leaf.EndsWith('.pyo', [StringComparison]::OrdinalIgnoreCase)
}

function Compare-ByteArrays {
  param([byte[]]$Left, [byte[]]$Right)
  $length = [Math]::Min($Left.Length, $Right.Length)
  for ($index = 0; $index -lt $length; $index++) {
    if ($Left[$index] -lt $Right[$index]) { return -1 }
    if ($Left[$index] -gt $Right[$index]) { return 1 }
  }
  if ($Left.Length -lt $Right.Length) { return -1 }
  if ($Left.Length -gt $Right.Length) { return 1 }
  return 0
}

function Get-Sha256Stream {
  param([Parameter(Mandatory = $true)][System.IO.Stream]$Stream)
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($sha.ComputeHash($Stream)) -replace '-', '').ToLowerInvariant()
  } finally {
    $sha.Dispose()
  }
}

function Sort-MembersByUtf8 {
  param([object[]]$Members)
  $decorated = New-Object 'System.Collections.Generic.List[object]'
  $ordinal = 0
  foreach ($member in @($Members)) {
    $decorated.Add([pscustomobject]@{
      member = $member
      utf8_path = [Text.Encoding]::UTF8.GetBytes([string]$member.path)
      ordinal = $ordinal
    })
    $ordinal += 1
  }
  $comparison = [System.Comparison[object]]{
    param($left, $right)
    $result = Compare-ByteArrays $left.utf8_path $right.utf8_path
    if ($result -ne 0) { return $result }
    if ($left.ordinal -lt $right.ordinal) { return -1 }
    if ($left.ordinal -gt $right.ordinal) { return 1 }
    return 0
  }
  $decorated.Sort($comparison)
  $sorted = [Array]::CreateInstance([object], $decorated.Count)
  for ($index = 0; $index -lt $decorated.Count; $index++) {
    $sorted[$index] = $decorated[$index].member
  }
  return ,$sorted
}

function Get-CacheEntry {
  param(
    [Parameter(Mandatory = $true)]$Requirement,
    [Parameter(Mandatory = $true)][string]$CacheRoot
  )
  $relativePath = [string]$Requirement.relative_path
  $entry = [ordered]@{
    id = [string]$Requirement.id
    type = [string]$Requirement.type
    relative_path = $relativePath
    bytes = [int64]0
    sha256 = ('0' * 64)
    members = @()
  }
  if (-not (Test-SafePosixPath $relativePath)) { return $null }
  $fullPath = Join-PathText $CacheRoot ($relativePath -replace '/', '\')
  if (-not (Test-Path -LiteralPath $fullPath)) { return $null }
  $item = Get-Item -LiteralPath $fullPath -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.LinkType) { return $null }

  if ([string]$Requirement.type -eq 'directory') {
    if ($item -isnot [System.IO.DirectoryInfo]) { return $null }
    foreach ($requiredMember in @($Requirement.required_members)) {
      if (-not (Test-SafePosixPath ([string]$requiredMember))) { return $null }
      $requiredPath = Join-PathText $fullPath (([string]$requiredMember) -replace '/', '\')
      if (-not (Test-Path -LiteralPath $requiredPath)) { return $null }
    }
    $seen = @{}
    $members = @()
    foreach ($child in @(Get-ChildItem -LiteralPath $fullPath -File -Recurse -Force)) {
      if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $child.LinkType) { return $null }
      $memberPath = $child.FullName.Substring($fullPath.TrimEnd('\').Length).TrimStart('\').Replace('\', '/')
      if (-not (Test-SafePosixPath $memberPath)) { return $null }
      $identity = Get-PathIdentity $memberPath
      if ($seen.ContainsKey($identity)) { return $null }
      $seen[$identity] = $true
      $members += ,[ordered]@{
        path = $memberPath
        bytes = [int64]$child.Length
        sha256 = Get-Sha256 $child.FullName
      }
    }
    $members = Sort-MembersByUtf8 $members
    $totalBytes = [int64]0
    foreach ($member in @($members)) { $totalBytes += [int64]$member.bytes }
    $entry.bytes = $totalBytes
    $entry.sha256 = Get-CanonicalHash (,$members)
    $entry.members = @($members)
    return $entry
  }

  if ([string]$Requirement.type -ne 'regular-file' -or $item -isnot [System.IO.FileInfo]) {
    return $null
  }
  [void](Add-Type -AssemblyName System.IO.Compression.FileSystem)
  $archive = [System.IO.Compression.ZipFile]::OpenRead($fullPath)
  try {
    $seen = @{}
    foreach ($zipEntry in @($archive.Entries)) {
      $normalizedName = Normalize-ZipMemberName $zipEntry.FullName
      if (-not (Test-SafeZipMemberName $normalizedName)) { return $null }
      $identityName = Get-PathIdentity $normalizedName.TrimEnd('/')
      if ($seen.ContainsKey($identityName)) { return $null }
      $seen[$identityName] = $true
    }
    $members = @()
    foreach ($requiredMember in @($Requirement.required_members)) {
      $requiredNormalized = Normalize-ZipMemberName ([string]$requiredMember)
      if (-not (Test-SafePosixPath $requiredNormalized)) { return $null }
      $matches = @(
        $archive.Entries | Where-Object { (Normalize-ZipMemberName ([string]$_.FullName)) -eq $requiredNormalized }
      )
      if ($matches.Count -ne 1) { return $null }
      $stream = $matches[0].Open()
      try {
        $members += ,[ordered]@{
          path = [string]$requiredNormalized
          bytes = [int64]$matches[0].Length
          sha256 = Get-Sha256Stream $stream
        }
      } finally {
        $stream.Dispose()
      }
    }
    $entry.bytes = [int64]$item.Length
    $entry.sha256 = Get-Sha256 $fullPath
    $entry.members = Sort-MembersByUtf8 $members
    return $entry
  } finally {
    $archive.Dispose()
  }
}

Assert-RegularFile $SessionPath 'candidate session'
$session = Get-Content -LiteralPath $SessionPath -Raw | ConvertFrom-Json
if ($session.schema -ne 'taiji-windows-candidate-session/v1' -or $session.target_id -ne 'windows-x64') {
  throw 'candidate session identity is invalid'
}

$sourceRoot = ConvertTo-ExtendedPath ([string]$session.paths.source_root)
$stagingRoot = ConvertTo-ExtendedPath ([string]$session.paths.staging_root)
$stagingCacheRoot = ConvertTo-ExtendedPath ([string]$session.paths.staging_cache_root)
$payloadRoot = ConvertTo-ExtendedPath ([string]$session.paths.payload_root)
if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
  throw "safe source root is missing: $sourceRoot"
}
$sourceResidue = Get-ChildItem -LiteralPath $sourceRoot -Force -Recurse |
  Where-Object { $_.Name -eq '.git' -or ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $_.LinkType } |
  Select-Object -First 1
if ($sourceResidue) {
  throw "safe source contains Git metadata or a reparse point: $($sourceResidue.FullName)"
}

New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
New-Item -ItemType Directory -Path $stagingCacheRoot -Force | Out-Null
New-Item -ItemType Directory -Path $payloadRoot -Force | Out-Null

$requirements = Get-Content -LiteralPath $session.cache.requirements_path -Raw | ConvertFrom-Json
if ((Get-CanonicalHash $requirements) -ne $session.cache.requirements_sha256) {
  throw 'cache requirements identity drifted before staging'
}
Assert-RegularFile $session.cache.observation_path 'cache observation'
$observationText = [IO.File]::ReadAllText(
  [string]$session.cache.observation_path,
  [Text.UTF8Encoding]::new($false, $true)
)
try {
  $observation = ConvertFrom-Json -InputObject $observationText
} finally {
  $observationText = $null
}
$sharedCacheRoot = [string]$session.cache.root
$observationKeys = @($observation.PSObject.Properties.Name | Sort-Object)
$expectedObservationKeys = @('cache_root', 'entries', 'observed_at', 'requirements_sha256', 'schema', 'target_id')
if (($observationKeys -join '|') -cne ($expectedObservationKeys -join '|')) {
  throw 'cache observation fields drifted before staging'
}
if ([string]$observation.cache_root -cne $sharedCacheRoot) {
  throw 'cache observation root drifted before staging'
}
$observationIdentity = [ordered]@{}
foreach ($property in $observation.psobject.Properties) {
  if ($property.Name -ne 'observed_at') {
    $observationIdentity[$property.Name] = $property.Value
  }
}
if ($observation.schema -ne 'taiji-windows-cache-observation/v1' -or
    $observation.target_id -ne 'windows-x64' -or
    $observation.requirements_sha256 -ne $session.cache.requirements_sha256 -or
    (Get-CanonicalHash $observationIdentity) -ne $session.cache.observation_sha256) {
  throw 'cache observation identity drifted before staging'
}
$sharedCacheAccessRoot = ConvertTo-ExtendedPath $sharedCacheRoot
$npmObservation = Get-ObservationEntry -Observation $observation -Id 'npm-cache'
$electronObservation = Get-ObservationEntry -Observation $observation -Id 'electron-39.8.10-win32-x64'
$pythonObservation = Get-ObservationEntry -Observation $observation -Id 'private-python-runtime'
if ([string]$npmObservation.type -cne 'directory' -or
    [string]$npmObservation.relative_path -cne 'npm' -or
    [string]$electronObservation.type -cne 'regular-file' -or
    [string]$electronObservation.relative_path -cne 'electron/electron-v39.8.10-win32-x64.zip' -or
    [string]$pythonObservation.type -cne 'directory' -or
    [string]$pythonObservation.relative_path -cne 'python-runtime') {
  throw 'cache observation entry contract drifted before staging'
}

# npm may update its cache metadata, so copy only this small cache into the mutable run staging area.
$npmCacheSource = Join-PathText $sharedCacheAccessRoot 'npm'
$stagingNpmCache = Join-PathText $stagingCacheRoot 'npm'
if (-not (Test-Path -LiteralPath (Join-PathText $npmCacheSource '_cacache') -PathType Container)) {
  throw 'WINDOWS_CACHE_MISSING: npm cache'
}
$npmCacheItem = Get-Item -LiteralPath $npmCacheSource -Force
if (($npmCacheItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $npmCacheItem.LinkType) {
  throw 'WINDOWS_CACHE_MISSING: npm cache'
}
Copy-DirectoryChildren -Source $npmCacheSource -Destination $stagingNpmCache

$desktopRoot = Join-PathText $sourceRoot 'apps\taiji-desktop'
$packageJson = Join-PathText $desktopRoot 'package.json'
$packageLock = Join-PathText $desktopRoot 'package-lock.json'
$desktopSrc = Join-PathText $desktopRoot 'src'
Assert-RegularFile $packageJson 'desktop package'
Assert-RegularFile $packageLock 'desktop package lock'
if (-not (Test-Path -LiteralPath $desktopSrc -PathType Container)) {
  throw "desktop source is missing: $desktopSrc"
}
$desktopNpmCheckRoot = Join-PathText $stagingRoot 'desktop-npm-check'
New-Item -ItemType Directory -Path $desktopNpmCheckRoot -Force | Out-Null
Copy-Item -LiteralPath $packageJson -Destination (Join-PathText $desktopNpmCheckRoot 'package.json') -Force
Copy-Item -LiteralPath $packageLock -Destination (Join-PathText $desktopNpmCheckRoot 'package-lock.json') -Force
if (-not (Test-Path -LiteralPath (Join-PathText $stagingNpmCache '_cacache'))) {
  throw 'WINDOWS_CACHE_MISSING: npm cache'
}

$appRoot = Join-PathText $payloadRoot 'resources\app'
New-Item -ItemType Directory -Path $appRoot -Force | Out-Null
# resources\app\package.json is staged explicitly from the safe desktop source.
Copy-Item -LiteralPath $packageJson -Destination (Join-PathText $appRoot 'package.json') -Force
# resources\app\src is staged explicitly from the safe desktop source.
Copy-Item -LiteralPath $desktopSrc -Destination (Join-PathText $appRoot 'src') -Recurse -Force

$agentSource = Join-PathText $sourceRoot 'hermes-local-lab\sources\hermes-agent'
$webuiSource = Join-PathText $sourceRoot 'hermes-local-lab\sources\hermes-webui'
if (-not (Test-Path -LiteralPath $agentSource -PathType Container)) {
  throw "Agent source is missing: $agentSource"
}
if (-not (Test-Path -LiteralPath $webuiSource -PathType Container)) {
  throw "WebUI source is missing: $webuiSource"
}
$payloadSourcesRoot = Join-PathText $payloadRoot 'hermes-local-lab\sources'
Copy-ProductSourceChildren -Source $agentSource -Destination (Join-PathText $payloadSourcesRoot 'hermes-agent')
Copy-ProductSourceChildren -Source $webuiSource -Destination (Join-PathText $payloadSourcesRoot 'hermes-webui')

$pythonSource = Join-PathText $sharedCacheAccessRoot 'python-runtime'
$pythonDestination = Join-PathText $payloadRoot 'hermes-local-lab\runtime\python'
Assert-CacheFile (Join-PathText $pythonSource 'python.exe') 'python runtime'
Assert-CacheFile (Join-PathText $pythonSource 'python311._pth') 'python path file'
New-Item -ItemType Directory -Path $pythonDestination -Force | Out-Null
foreach ($pythonFile in @(Get-ChildItem -LiteralPath $pythonSource -File -Recurse -Force)) {
  Copy-PythonRuntimeFile -File $pythonFile -SourceRoot $pythonSource -DestinationRoot $pythonDestination
}

$electronArchive = Join-PathText $sharedCacheAccessRoot 'electron\electron-v39.8.10-win32-x64.zip'
Assert-CacheFile $electronArchive 'Electron cache archive'
if ([int64]$electronObservation.bytes -ne [int64](Get-Item -LiteralPath $electronArchive).Length -or
    [string]$electronObservation.sha256 -cne (Get-Sha256 $electronArchive)) {
  throw 'Electron cache identity drifted before payload assembly'
}
Expand-SafeZipArchive -ArchivePath $electronArchive -DestinationRoot $payloadRoot
$electronBinary = Join-PathText $payloadRoot 'electron.exe'
if (-not (Test-Path -LiteralPath $electronBinary -PathType Leaf)) {
  throw 'Electron cache expansion did not produce root electron.exe'
}
$taijiAgentExe = Join-PathText $payloadRoot 'TaijiAgent.exe'
if (Test-Path -LiteralPath $taijiAgentExe) {
  throw 'TaijiAgent.exe already exists before rename'
}
Rename-Item -LiteralPath $electronBinary -NewName 'TaijiAgent.exe'

$sharedConfigPath = Join-PathText $payloadRoot 'hermes-local-lab\config'
New-Item -ItemType Directory -Path $sharedConfigPath -Force | Out-Null
$defaultConfigSource = Join-PathText $sourceRoot 'packaging\windows\taiji-default-config.yaml'
$diagnoseSource = Join-PathText $sourceRoot 'packaging\windows\diagnose.ps1'
Assert-RegularFile $defaultConfigSource 'Windows default config'
Assert-RegularFile $diagnoseSource 'Windows diagnose script'
# hermes-local-lab\config\taiji-default-config.yaml is the shared payload config path.
Copy-Item -LiteralPath $defaultConfigSource -Destination (Join-PathText $sharedConfigPath 'taiji-default-config.yaml') -Force
New-Item -ItemType Directory -Path (Join-PathText $payloadRoot 'tools') -Force | Out-Null
Copy-Item -LiteralPath $diagnoseSource -Destination (Join-PathText $payloadRoot 'tools\diagnose.ps1') -Force

$pthPath = Join-PathText $pythonDestination 'python311._pth'
$pthText = @(
  'python311.zip',
  '.',
  '..\..\sources\hermes-agent',
  '..\..\sources\hermes-webui',
  'Lib\site-packages',
  'import site'
) -join [char]10
Write-Utf8Text -Path $pthPath -Text ($pthText + [char]10)

$forbiddenDirectories = @('.git', '.ssh', '.gnupg', '.aws', '__pycache__', 'node_modules')
# payload hygiene patterns include *.db, *.sqlite, *.sqlite3, *.pyc, and *.pyo.
$forbiddenExtensions = @('.db', '.sqlite', '.sqlite3', '.pyc', '.pyo')
foreach ($item in @(Get-ChildItem -LiteralPath $payloadRoot -Force -Recurse)) {
  if ($item.Name -in $forbiddenDirectories -or $item.Extension -in $forbiddenExtensions -or
      $item.Name -eq '.env' -or $item.Name -like '.env.*' -or
      ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.LinkType) {
    throw "payload-hygiene-closure failed: $($item.FullName)"
  }
}

$unsortedPayloadEntries = @(
  Get-ChildItem -LiteralPath $payloadRoot -File -Recurse -Force |
    ForEach-Object {
      $relative = $_.FullName.Substring($payloadRoot.Length + 1).Replace('\', '/')
      [ordered]@{
        path = $relative
        bytes = [int64]$_.Length
        sha256 = Get-Sha256 $_.FullName
      }
    }
)
$payloadEntries = Sort-MembersByUtf8 $unsortedPayloadEntries

$electronMembers = @($electronObservation.members | Where-Object { [string]$_.path -ceq 'electron.exe' })
$payloadElectron = @($payloadEntries | Where-Object { [string]$_.path -ceq 'TaijiAgent.exe' })
if ($electronMembers.Count -ne 1 -or $payloadElectron.Count -ne 1 -or
    [int64]$electronMembers[0].bytes -ne [int64]$payloadElectron[0].bytes -or
    [string]$electronMembers[0].sha256 -cne [string]$payloadElectron[0].sha256) {
  throw 'Electron payload identity drifted from cache observation'
}

$expectedPythonMembers = @{}
foreach ($member in @($pythonObservation.members)) {
  $memberPath = [string]$member.path
  if (-not (Test-SafePosixPath $memberPath)) {
    throw 'Python cache observation contains an unsafe member path'
  }
  if ((Test-ExcludedPythonMember $memberPath) -or $memberPath -ceq 'python311._pth') {
    continue
  }
  $identity = Get-PathIdentity $memberPath
  if ($expectedPythonMembers.ContainsKey($identity)) {
    throw 'Python cache observation contains a duplicate member path'
  }
  $expectedPythonMembers[$identity] = $member
}

$pythonPayloadPrefix = 'hermes-local-lab/runtime/python/'
$seenPythonMembers = @{}
foreach ($payloadEntry in @($payloadEntries)) {
  $payloadPath = [string]$payloadEntry.path
  if (-not $payloadPath.StartsWith($pythonPayloadPrefix, [StringComparison]::Ordinal)) {
    continue
  }
  $memberPath = $payloadPath.Substring($pythonPayloadPrefix.Length)
  if ($memberPath -ceq 'python311._pth') { continue }
  $identity = Get-PathIdentity $memberPath
  if (-not $expectedPythonMembers.ContainsKey($identity) -or $seenPythonMembers.ContainsKey($identity)) {
    throw 'consumed Python payload identity drifted from cache observation'
  }
  $expected = $expectedPythonMembers[$identity]
  if ([int64]$expected.bytes -ne [int64]$payloadEntry.bytes -or
      [string]$expected.sha256 -cne [string]$payloadEntry.sha256) {
    throw 'consumed Python payload identity drifted from cache observation'
  }
  $seenPythonMembers[$identity] = $true
}
if ($seenPythonMembers.Count -ne $expectedPythonMembers.Count) {
  throw 'consumed Python payload identity drifted from cache observation'
}

$payloadTotalBytes = [int64]0
foreach ($payloadEntryForTotal in @($payloadEntries)) {
  $payloadTotalBytes += [int64]$payloadEntryForTotal.bytes
}

$payloadManifest = [ordered]@{
  schema = 'taiji-windows-payload-manifest/v1'
  source_commit = $session.source.commit
  source_tree = $session.source.tree
  entries = @($payloadEntries)
  file_count = $payloadEntries.Count
  total_bytes = $payloadTotalBytes
}
$payloadManifest.manifest_sha256 = Get-CanonicalHash $payloadManifest
$manifestPath = Join-PathText $stagingRoot 'payload-manifest.json'
Write-Utf8Text -Path $manifestPath -Text ((ConvertTo-CanonicalJson $payloadManifest) + [char]10)
Write-Host 'WINDOWS_CANDIDATE_PAYLOAD_STAGED'
