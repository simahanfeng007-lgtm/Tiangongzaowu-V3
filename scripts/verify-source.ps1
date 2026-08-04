[CmdletBinding()]
param([switch]$Full)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root "app\runtime\python312\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Embedded Python is unavailable. Run scripts\setup-source.ps1 first."
}
$args = @()
if (-not $Full) { $args += "--quick" }
& $Python (Join-Path $Root "scripts\verify_source.py") @args
exit $LASTEXITCODE
