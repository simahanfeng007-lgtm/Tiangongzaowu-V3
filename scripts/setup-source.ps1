[CmdletBinding()]
param(
    [switch]$SkipNpm,
    [switch]$SkipPython,
    [switch]$InstallPlaywrightBrowser
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AppRoot = Join-Path $Root "app"
$VenvRoot = Join-Path $AppRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$EmbeddedPython = Join-Path $AppRoot "runtime\python312\python.exe"

function Resolve-BasePython {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        try {
            & $py.Source -3.12 -c "import sys; assert sys.version_info >= (3,12)" | Out-Null
            return @{ Command = $py.Source; Prefix = @("-3.12") }
        } catch { }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
    if (-not $python) { throw "Python 3.12+ was not found. Install 64-bit Python first." }
    & $python.Source -c "import sys; assert sys.version_info >= (3,12), sys.version" | Out-Null
    return @{ Command = $python.Source; Prefix = @() }
}

if (-not $SkipPython) {
    if (-not (Test-Path -LiteralPath $EmbeddedPython -PathType Leaf)) {
        Write-Host "[1/4] Provisioning embedded CPython 3.12"
        & (Join-Path $PSScriptRoot "provision-embedded-python.ps1")
    }
    $VenvPython = $EmbeddedPython
    if ($InstallPlaywrightBrowser) {
        & $VenvPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw "Failed to install the Playwright Chromium browser" }
    }
}

if (Test-Path -LiteralPath $EmbeddedPython -PathType Leaf) {
    $VenvPython = $EmbeddedPython
}
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    throw "Python environment is unavailable: $VenvPython"
}

Write-Host "[2/4] Synchronizing generated source mirrors"
& $VenvPython (Join-Path $Root "scripts\sync-generated-sources.py") --write
if ($LASTEXITCODE -ne 0) { throw "Failed to generate source mirrors" }
& $VenvPython (Join-Path $Root "scripts\sync-generated-sources.py") --check
if ($LASTEXITCODE -ne 0) { throw "Generated source mirror verification failed" }
& $VenvPython (Join-Path $Root "scripts\rebuild_frozen_release_overlays.py")
if ($LASTEXITCODE -ne 0) { throw "Failed to rebuild generated CPython 3.12 release overlays" }

if (-not $SkipNpm) {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
    if (-not $npm) { throw "Node.js/npm was not found. Install Node.js 22 LTS or newer." }
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $node) { $node = Get-Command node -ErrorAction SilentlyContinue }
    if (-not $node) { throw "Node.js was not found. Install Node.js 22 LTS or newer." }
    Write-Host "[3/4] Installing locked Electron dependencies"
    & $npm.Source --prefix $AppRoot ci --ignore-scripts
    if ($LASTEXITCODE -ne 0) { throw "Failed to install locked Electron dependencies" }
    # Keep the general dependency install script-free, then run the one audited
    # native payload installer required by the desktop product.
    $ElectronInstall = Join-Path $AppRoot "node_modules\electron\install.js"
    if (-not (Test-Path -LiteralPath $ElectronInstall -PathType Leaf)) {
        throw "Electron distribution installer is missing: $ElectronInstall"
    }
    & $node.Source $ElectronInstall
    if ($LASTEXITCODE -ne 0) { throw "Failed to install the Electron distribution" }
    $ElectronExecutable = Join-Path $AppRoot "node_modules\electron\dist\electron.exe"
    if (-not (Test-Path -LiteralPath $ElectronExecutable -PathType Leaf)) {
        throw "Electron distribution is missing after installation: $ElectronExecutable"
    }
}

Write-Host "[4/4] Verifying source tree"
& $VenvPython (Join-Path $Root "scripts\verify_source.py") --quick
if ($LASTEXITCODE -ne 0) { throw "Source tree verification failed" }
& $VenvPython (Join-Path $Root "scripts\refresh-source-release.py")
if ($LASTEXITCODE -ne 0) { throw "Failed to refresh source release authority" }
Write-Host "Source environment is ready. Run start-tiangong.bat."
