[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimeRoot = Join-Path $Root "app\runtime\python312"
$Python = Join-Path $RuntimeRoot "python.exe"
$Version = "3.12.10"
$Archive = Join-Path ([IO.Path]::GetTempPath()) "python-$Version-embed-amd64.zip"
$Bootstrap = Join-Path ([IO.Path]::GetTempPath()) "get-pip.py"
$Uri = "https://www.python.org/ftp/python/$Version/python-$Version-embed-amd64.zip"
$TunaUri = "https://mirrors.tuna.tsinghua.edu.cn/python/$Version/python-$Version-embed-amd64.zip"
$ExpectedMd5 = "fe8ef205f2e9c3ba44d0cf9954e1abd3"

function Invoke-DownloadWithFallback {
    param(
        [Parameter(Mandatory = $true)][string]$PrimaryUri,
        [Parameter(Mandatory = $true)][string]$FallbackUri,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $DownloadPath = "$OutputPath.download"
    Remove-Item -LiteralPath $DownloadPath -Force -ErrorAction SilentlyContinue
    & curl.exe --fail --location --output $DownloadPath $PrimaryUri
    if ($LASTEXITCODE -ne 0) {
        if ($env:TIANGONG_DISABLE_DEPENDENCY_FALLBACK -eq "1") {
            throw "$Label download failed and dependency fallback is disabled"
        }
        Write-Host "[dependency-fallback] $Label`: retrying with $FallbackUri"
        Remove-Item -LiteralPath $DownloadPath -Force -ErrorAction SilentlyContinue
        & curl.exe --fail --location --output $DownloadPath $FallbackUri
        if ($LASTEXITCODE -ne 0) { throw "$Label download failed with primary and fallback sources" }
    }
    Move-Item -LiteralPath $DownloadPath -Destination $OutputPath -Force
}

function Get-FileDigest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath,

        [Parameter(Mandatory = $true)]
        [ValidateSet("MD5", "SHA256")]
        [string]$Algorithm
    )

    $ResolvedPath = (Resolve-Path -LiteralPath $LiteralPath).Path
    $Stream = [IO.File]::OpenRead($ResolvedPath)
    $Hasher = switch ($Algorithm) {
        "MD5" { [Security.Cryptography.MD5]::Create() }
        "SHA256" { [Security.Cryptography.SHA256]::Create() }
    }
    try {
        return ([BitConverter]::ToString($Hasher.ComputeHash($Stream))).Replace("-", "").ToLowerInvariant()
    } finally {
        $Hasher.Dispose()
        $Stream.Dispose()
    }
}

if ($Force -and (Test-Path -LiteralPath $RuntimeRoot)) {
    Remove-Item -LiteralPath $RuntimeRoot -Recurse -Force
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
        Invoke-DownloadWithFallback -PrimaryUri $Uri -FallbackUri $TunaUri -OutputPath $Archive -Label "embedded CPython"
    }
    if ((Get-FileDigest -LiteralPath $Archive -Algorithm MD5) -ne $ExpectedMd5) {
        throw "Embedded CPython archive checksum mismatch"
    }
    if (Test-Path -LiteralPath $RuntimeRoot) {
        if ((Get-ChildItem -LiteralPath $RuntimeRoot -Force | Measure-Object).Count -ne 0) {
            throw "Embedded runtime target is non-empty: $RuntimeRoot"
        }
        Remove-Item -LiteralPath $RuntimeRoot -Force
    }
    Expand-Archive -LiteralPath $Archive -DestinationPath $RuntimeRoot -Force
    $Pth = Get-ChildItem -LiteralPath $RuntimeRoot -Filter "python*._pth" | Select-Object -First 1
    if (-not $Pth) { throw "Embedded Python path configuration is missing" }
    @("python312.zip", ".", "Lib\\site-packages", "", "import site") | Set-Content -LiteralPath $Pth.FullName -Encoding ascii
    if (-not (Test-Path -LiteralPath $Bootstrap -PathType Leaf)) {
        Invoke-DownloadWithFallback `
            -PrimaryUri "https://bootstrap.pypa.io/get-pip.py" `
            -FallbackUri "https://raw.githubusercontent.com/pypa/get-pip/main/public/get-pip.py" `
            -OutputPath $Bootstrap `
            -Label "pip bootstrap"
    }
    & $Python $Bootstrap
    if ($LASTEXITCODE -ne 0) { throw "Unable to bootstrap pip in embedded CPython" }
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Embedded Python is missing: $Python" }
& $Python -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
& $Python (Join-Path $Root "scripts\install-python-dependencies.py") `
    --upgrade-pip `
    --requirements (Join-Path $Root "requirements-release.lock") `
    --project $Root
if ($LASTEXITCODE -ne 0) { throw "Embedded Python dependency installation failed" }
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw "Embedded Python dependency verification failed" }

# 2026-08-22：随包分发 Playwright 的 Chromium（"浏览器控不了"主诉）。
# requirements-source.lock 已含 playwright 模块，但浏览器二进制此前从不
# 安装——用户机器既无 ms-playwright 缓存又无可发现的本机 Chrome/Edge 时，
# browser.* 全部降级失败。装进运行时目录（PLAYWRIGHT_BROWSERS_PATH），
# main.js 启动网关时注入同一路径；运行时仍按序回退本机 Chrome/Edge。
# 设 TIANGONG_SKIP_PLAYWRIGHT_BROWSERS=1 可跳过（约 120MB 下载）。
if ($env:TIANGONG_SKIP_PLAYWRIGHT_BROWSERS -ne "1") {
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $RuntimeRoot "ms-playwright"
    & $Python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { Write-Warning "playwright chromium install failed; browser actions will fall back to local Chrome/Edge" }
} else {
    Write-Warning "TIANGONG_SKIP_PLAYWRIGHT_BROWSERS=1：跳过随包 Chromium，browser.* 依赖本机 Chrome/Edge"
}

# A local-path pip install records the publisher machine in direct_url.json.
# The embedded product packages are synchronized from source during setup, and
# the release portability gate intentionally rejects this host provenance.
$SitePackagesRoot = Join-Path $RuntimeRoot "Lib\site-packages"
$ResolvedSitePackagesRoot = [IO.Path]::GetFullPath($SitePackagesRoot)
$SitePackagesPrefix = $ResolvedSitePackagesRoot.TrimEnd(
    [IO.Path]::DirectorySeparatorChar,
    [IO.Path]::AltDirectorySeparatorChar
) + [IO.Path]::DirectorySeparatorChar
$DirectUrlFiles = @(
    Get-ChildItem -LiteralPath $ResolvedSitePackagesRoot -Recurse -Filter "direct_url.json" -File
)
foreach ($DirectUrlFile in $DirectUrlFiles) {
    $ResolvedDirectUrl = [IO.Path]::GetFullPath($DirectUrlFile.FullName)
    if (-not $ResolvedDirectUrl.StartsWith(
        $SitePackagesPrefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refused unsafe direct_url provenance cleanup: $ResolvedDirectUrl"
    }
    Remove-Item -LiteralPath $ResolvedDirectUrl -Force
}
if (Get-ChildItem -LiteralPath $ResolvedSitePackagesRoot -Recurse -Filter "direct_url.json" -File) {
    throw "Embedded Python still contains direct_url provenance"
}

$manifest = [ordered]@{
    schema = "tiangong.embedded-python.v1"
    python_version = (& $Python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
    architecture = (& $Python -c "import platform; print(platform.architecture()[0])").Trim()
    requirements_sha256 = Get-FileDigest -LiteralPath (Join-Path $Root "requirements-release.lock") -Algorithm SHA256
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RuntimeRoot "runtime-manifest.json") -Encoding utf8
Write-Host "Embedded CPython runtime ready: $Python"
