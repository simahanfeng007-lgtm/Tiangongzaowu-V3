[CmdletBinding()]
param(
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Source = Join-Path $Root "src\life_service"
$Runtime = Join-Path $Root "app\life-service\runtime314\life_service"
$ContractSource = Join-Path $Root "src\contracts"
$ContractRuntime = Join-Path $Root "app\life-service\runtime314\contracts"

function RelativeFiles([string]$Base) {
  if (-not (Test-Path -LiteralPath $Base -PathType Container)) {
    return @{}
  }
  $resolvedBase = (Resolve-Path -LiteralPath $Base).Path.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
  )
  $prefix = $resolvedBase + [IO.Path]::DirectorySeparatorChar
  $result = @{}
  Get-ChildItem -LiteralPath $resolvedBase -Recurse -File |
    Where-Object {
      $_.Extension -ne ".pyc" -and $_.Name -ne ".tiangong-generated-source.json" -and $_.FullName -notlike "*\__pycache__\*"
    } |
    ForEach-Object {
      if (-not $_.FullName.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Life source mirror file escaped its root: $($_.FullName)"
      }
      $relative = $_.FullName.Substring($prefix.Length)
      $result[$relative] = $_.FullName
    }
  return $result
}

if ($Apply) {
  foreach ($pair in @(@($Source, $Runtime), @($ContractSource, $ContractRuntime))) {
    New-Item -ItemType Directory -Force -Path $pair[1] | Out-Null
    $sourceFiles = RelativeFiles $pair[0]
    foreach ($relative in $sourceFiles.Keys) {
      $target = Join-Path $pair[1] $relative
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
      Copy-Item -LiteralPath $sourceFiles[$relative] -Destination $target -Force
    }
  }
}

function AssertMirror([string]$ExpectedRoot, [string]$ActualRoot, [string]$Label) {
  $expected = RelativeFiles $ExpectedRoot
  $actual = RelativeFiles $ActualRoot
  $missing = @($expected.Keys | Where-Object { -not $actual.ContainsKey($_) } | Sort-Object)
  $extra = @($actual.Keys | Where-Object { -not $expected.ContainsKey($_) } | Sort-Object)
  $changed = @(
    $expected.Keys |
      Where-Object {
        $actual.ContainsKey($_) -and
        (Get-FileHash -Algorithm SHA256 -LiteralPath $expected[$_]).Hash -ne
          (Get-FileHash -Algorithm SHA256 -LiteralPath $actual[$_]).Hash
      } |
      Sort-Object
  )
  if ($missing.Count -or $extra.Count -or $changed.Count) {
    throw "$Label mirror mismatch. Missing=$($missing -join ',') Extra=$($extra -join ',') Changed=$($changed -join ',')"
  }
  return $expected.Count
}

$lifeCount = AssertMirror $Source $Runtime "Life source"
$contractCount = AssertMirror $ContractSource $ContractRuntime "Life contract"
Write-Host "Life runtime mirrors verified: life=$lifeCount contracts=$contractCount files."
