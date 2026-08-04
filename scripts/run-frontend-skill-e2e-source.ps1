[CmdletBinding()]
param(
    [ValidateRange(1, 34)]
    [int]$Start = 1,
    [ValidateRange(1, 34)]
    [int]$End = 34,
    [string]$OutputDir = "output\playwright\skill-e2e-source"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ResolvedOutput = [System.IO.Path]::GetFullPath((Join-Path $Root $OutputDir))
New-Item -ItemType Directory -Path $ResolvedOutput -Force | Out-Null

$env:TIANGONG_CDP_ENDPOINT = "http://127.0.0.1:9222"
$env:TIANGONG_E2E_START = [string]$Start
$env:TIANGONG_E2E_END = [string]$End
$env:TIANGONG_E2E_TIMEOUT_MS = "300000"
$env:TIANGONG_E2E_MIN_TIMEOUT_MS = "900000"
$env:TIANGONG_E2E_OUTPUT_DIR = $ResolvedOutput

Push-Location $Root
try {
    & node ".\scripts\frontend-skill-e2e.mjs" ".\tests\fixtures\frontend-skill-e2e-cases.json" `
        1> (Join-Path $ResolvedOutput "runner.stdout.log") `
        2> (Join-Path $ResolvedOutput "runner.stderr.log")
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
