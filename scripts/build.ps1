[CmdletBinding()]
param(
  [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $SkipChecks) {
  & (Join-Path $PSScriptRoot "check.ps1")
}

$OutRoot = Join-Path $Root "out"
$WheelRoot = Join-Path $OutRoot "wheels"
$ContractRoot = Join-Path $OutRoot "contracts"
$ReleaseRoot = Join-Path $OutRoot "release"
foreach ($BuildRoot in @($WheelRoot, $ContractRoot, $ReleaseRoot)) {
  if (-not (Test-Path -LiteralPath $BuildRoot)) { continue }
  $ResolvedBuildRoot = (Resolve-Path -LiteralPath $BuildRoot).Path
  if (
    -not $ResolvedBuildRoot.StartsWith($OutRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    (Split-Path -Leaf $ResolvedBuildRoot) -notin @("wheels", "contracts", "release")
  ) {
    throw "Refused unsafe build-output cleanup"
  }
  Remove-Item -LiteralPath $ResolvedBuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $WheelRoot -Force | Out-Null

$Manifest = Get-Content -LiteralPath (Join-Path $Root "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$env:SOURCE_DATE_EPOCH = [string]([DateTimeOffset]::Parse([string]$Manifest.generated_at).ToUnixTimeSeconds())

$SystemTemp = (Resolve-Path -LiteralPath $env:TEMP).Path
$StageRoot = Join-Path $SystemTemp "tiangong-v3-gateway-build-$PID"
$ResolvedStage = [System.IO.Path]::GetFullPath($StageRoot)
if (-not $ResolvedStage.StartsWith($SystemTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Unsafe build stage path"
}

try {
  New-Item -ItemType Directory -Path $ResolvedStage -Force | Out-Null
  Copy-Item -LiteralPath (Join-Path $Root "pyproject.toml") -Destination $ResolvedStage
  Copy-Item -LiteralPath (Join-Path $Root "src") -Destination $ResolvedStage -Recurse

  $env:PYTHONPATH = Join-Path $ResolvedStage "src"
  python -m contracts.artifacts --output $ContractRoot
  if ($LASTEXITCODE -ne 0) { throw "Contract artifact generation failed" }

  python -m total_gateway.release_manifest --workspace $Root --output $ReleaseRoot
  if ($LASTEXITCODE -ne 0) { throw "Release manifest generation failed" }

  python -m pip wheel $ResolvedStage --no-deps --no-build-isolation --wheel-dir $WheelRoot
  if ($LASTEXITCODE -ne 0) { throw "Wheel build failed" }
} finally {
  if (Test-Path -LiteralPath $ResolvedStage) {
    $CleanupPath = (Resolve-Path -LiteralPath $ResolvedStage).Path
    if (
      $CleanupPath.StartsWith($SystemTemp, [System.StringComparison]::OrdinalIgnoreCase) -and
      (Split-Path -Leaf $CleanupPath) -eq "tiangong-v3-gateway-build-$PID"
    ) {
      Remove-Item -LiteralPath $CleanupPath -Recurse -Force
    } else {
      throw "Refused unsafe build-stage cleanup"
    }
  }
}

Get-ChildItem -LiteralPath $WheelRoot -Filter "*.whl" -File |
  Select-Object FullName, Length, @{Name = "SHA256"; Expression = { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash } }
Get-ChildItem -LiteralPath $ContractRoot -Filter "*.json" -File |
  Sort-Object Name |
  Select-Object FullName, Length, @{Name = "SHA256"; Expression = { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash } }
Get-ChildItem -LiteralPath $ReleaseRoot -Filter "*.json" -File |
  Sort-Object Name |
  Select-Object FullName, Length, @{Name = "SHA256"; Expression = { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash } }
