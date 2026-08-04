[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$WorkspaceRoot,

  [Parameter(Mandatory = $true)]
  [string[]]$Target,

  [Parameter(Mandatory = $true)]
  [string]$ManifestDirectory
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Add-Type -AssemblyName Microsoft.VisualBasic

$Root = (Resolve-Path -LiteralPath $WorkspaceRoot).Path.TrimEnd([IO.Path]::DirectorySeparatorChar)
$RootPrefix = $Root + [IO.Path]::DirectorySeparatorChar
$GitRoot = Join-Path $Root ".git"
$ManifestRoot = [IO.Path]::GetFullPath($ManifestDirectory)

if (-not (Test-Path -LiteralPath $ManifestRoot)) {
  New-Item -ItemType Directory -Path $ManifestRoot -Force | Out-Null
}

function Assert-SafeTarget {
  param([Parameter(Mandatory = $true)][string]$Path)

  $FullPath = [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar)
  if (-not $FullPath.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refused target outside workspace: $FullPath"
  }
  if ($FullPath.Equals($Root, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refused workspace-root target"
  }
  if (
    $FullPath.Equals($GitRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $FullPath.StartsWith($GitRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
  ) {
    throw "Refused .git target: $FullPath"
  }
  return $FullPath
}

function Get-WorkspaceRelativePath {
  param([Parameter(Mandatory = $true)][string]$Path)

  $SafePath = Assert-SafeTarget -Path $Path
  return $SafePath.Substring($RootPrefix.Length)
}

function Write-RecoveryManifest {
  param(
    [Parameter(Mandatory = $true)][object]$Manifest,
    [Parameter(Mandatory = $true)][string]$Path
  )

  $Json = $Manifest | ConvertTo-Json -Depth 8
  [IO.File]::WriteAllText($Path, $Json, [Text.UTF8Encoding]::new($false))
}

$ResolvedTargets = @()
foreach ($RequestedTarget in ($Target | Select-Object -Unique)) {
  $Candidate = if ([IO.Path]::IsPathRooted($RequestedTarget)) {
    $RequestedTarget
  } else {
    Join-Path $Root $RequestedTarget
  }
  $SafePath = Assert-SafeTarget -Path $Candidate
  if (-not (Test-Path -LiteralPath $SafePath)) {
    continue
  }
  $ResolvedTargets += $SafePath
}

for ($Index = 0; $Index -lt $ResolvedTargets.Count; $Index += 1) {
  for ($OtherIndex = $Index + 1; $OtherIndex -lt $ResolvedTargets.Count; $OtherIndex += 1) {
    $Left = $ResolvedTargets[$Index].TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $Right = $ResolvedTargets[$OtherIndex].TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (
      $ResolvedTargets[$Index].StartsWith($Right, [StringComparison]::OrdinalIgnoreCase) -or
      $ResolvedTargets[$OtherIndex].StartsWith($Left, [StringComparison]::OrdinalIgnoreCase)
    ) {
      throw "Refused overlapping targets: $($ResolvedTargets[$Index]) and $($ResolvedTargets[$OtherIndex])"
    }
  }
}

$FileRecords = @()
$ItemRecords = @()
foreach ($Path in ($ResolvedTargets | Sort-Object)) {
  $Item = Get-Item -LiteralPath $Path -Force
  $Files = @()
  if ($Item.PSIsContainer) {
    $Files = @(Get-ChildItem -LiteralPath $Path -Force -Recurse -File -ErrorAction Stop)
  } else {
    $Files = @($Item)
  }

  $ItemBytes = [int64]0
  foreach ($File in $Files) {
    $SafeFile = Assert-SafeTarget -Path $File.FullName
    $ItemBytes += [int64]$File.Length
    $FileRecords += [pscustomobject][ordered]@{
      relative_path = Get-WorkspaceRelativePath -Path $SafeFile
      bytes = [int64]$File.Length
      sha256 = (Get-FileHash -LiteralPath $SafeFile -Algorithm SHA256).Hash
    }
  }

  $ItemRecords += [pscustomobject][ordered]@{
    relative_path = Get-WorkspaceRelativePath -Path $Path
    kind = if ($Item.PSIsContainer) { "directory" } else { "file" }
    files = $Files.Count
    bytes = $ItemBytes
    status = "prepared"
  }
}

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ManifestPath = Join-Path $ManifestRoot "tiangong-v3-recycle-$Timestamp.json"
$TotalBytes = [int64]0
foreach ($Record in $ItemRecords) {
  $TotalBytes += [int64]$Record.bytes
}
$Manifest = [ordered]@{
  schema = "tiangong.v3.workspace_recycle_manifest.v1"
  created_at = (Get-Date).ToString("o")
  completed_at = $null
  status = "prepared"
  workspace_root = $Root
  recycle_method = "Microsoft.VisualBasic.FileIO.SendToRecycleBin"
  total_items = $ItemRecords.Count
  total_files = $FileRecords.Count
  total_bytes = $TotalBytes
  items = $ItemRecords
  files = $FileRecords
}
Write-RecoveryManifest -Manifest $Manifest -Path $ManifestPath

try {
  for ($Index = 0; $Index -lt $ResolvedTargets.Count; $Index += 1) {
    $Path = $ResolvedTargets[$Index]
    $Item = Get-Item -LiteralPath $Path -Force
    if ($Item.PSIsContainer) {
      [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory(
        $Path,
        [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
        [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin
      )
    } else {
      [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(
        $Path,
        [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
        [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin
      )
    }
    $Manifest.items[$Index].status = "recycled"
    Write-RecoveryManifest -Manifest $Manifest -Path $ManifestPath
  }
  $Manifest.status = "complete"
  $Manifest.completed_at = (Get-Date).ToString("o")
  Write-RecoveryManifest -Manifest $Manifest -Path $ManifestPath
} catch {
  $Manifest.status = "failed"
  $Manifest.completed_at = (Get-Date).ToString("o")
  $Manifest.error = $_.Exception.Message
  Write-RecoveryManifest -Manifest $Manifest -Path $ManifestPath
  throw
}

[pscustomobject]@{
  Status = $Manifest.status
  Items = $Manifest.total_items
  Files = $Manifest.total_files
  Bytes = $Manifest.total_bytes
  Manifest = $ManifestPath
}
