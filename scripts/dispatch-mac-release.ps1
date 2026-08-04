[CmdletBinding()]
param(
  [ValidateSet("x64", "arm64")]
  [string]$Architecture = "arm64",
  [string]$Ref = "main"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
  throw "GitHub CLI (gh) is required to dispatch the macOS release workflow."
}

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Push-Location $Root
try {
  $Remote = (& git remote get-url origin 2>$null)
  if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Remote)) {
    throw "An origin GitHub remote is required before dispatching the macOS build."
  }
  & gh workflow run release-desktop.yml --ref $Ref -f platform=macos -f architecture=$Architecture
  if ($LASTEXITCODE -ne 0) { throw "GitHub Actions workflow dispatch failed." }
} finally {
  Pop-Location
}

