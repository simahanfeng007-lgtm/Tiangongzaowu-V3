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
    # bug-fix: ._pth 追加 ..\..\backend —— 存在 ._pth 时 python -m 不把 cwd 加进 sys.path，
    # pythonw -m total_gateway 会秒退；条目相对 exe 目录（app\runtime\python312）上跳两级
    # 恰为 $INSTDIR\app\backend（total_gateway 包所在层），保留原有 zip/site-packages 条目（2026-08-26，凌霜修 UX）
    @("python312.zip", ".", "Lib\\site-packages", "..\\..\\backend", "", "import site") | Set-Content -LiteralPath $Pth.FullName -Encoding ascii
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

# bug-fix: 补 tkinter 运行时供 customtkinter 壳启动（2026-08-26，凌霜）
# 官方 embeddable zip 不含 tkinter，customtkinter 壳启动前必须补齐：
# 从完整版 CPython 3.12 拷贝 tcl/（TCL_LIBRARY 指向）、tk/、DLLs/_tkinter.pyd、
# tcl86t.dll + tk86t.dll 到运行时根目录（._pth 已含 "."，根目录可直接 import）。
function Copy-TkinterRuntime {
    $Source = "C:\Python312"
    if (-not (Test-Path -LiteralPath (Join-Path $Source "python.exe") -PathType Leaf)) {
        $Detected = $null
        try {
            $Detected = (& python -c "import sys; print(sys.base_prefix)" 2>$null).Trim()
        } catch {
            $Detected = $null
        }
        if (-not $Detected -or -not (Test-Path -LiteralPath (Join-Path $Detected "python.exe") -PathType Leaf)) {
            Write-Warning "未找到完整版 CPython 3.12（C:\Python312 与 base_prefix 探测均失败），跳过 tkinter 补齐"
            return
        }
        $Source = $Detected
    }
    & (Join-Path $Source "python.exe") -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
    if ($LASTEXITCODE -ne 0) { throw "tkinter source is not CPython 3.12: $Source" }

    foreach ($Directory in @("tcl", "tk")) {
        $SourceDirectory = Join-Path $Source $Directory
        if (-not (Test-Path -LiteralPath $SourceDirectory -PathType Container)) {
            if ($Directory -eq "tk") { continue }  # 3.12 安装版只有 tcl\（内含 tk8.6），无独立 tk\
            throw "tkinter source directory is missing: $SourceDirectory"
        }
        Copy-Item -LiteralPath $SourceDirectory -Destination (Join-Path $RuntimeRoot $Directory) -Recurse -Force
    }
    foreach ($File in @("DLLs\_tkinter.pyd", "DLLs\tcl86t.dll", "DLLs\tk86t.dll")) {
        $SourceFile = Join-Path $Source $File
        if (-not (Test-Path -LiteralPath $SourceFile -PathType Leaf)) {
            $SourceFile = Join-Path $Source (Split-Path $File -Leaf)  # 旧布局把 dll 放在安装根目录
        }
        if (-not (Test-Path -LiteralPath $SourceFile -PathType Leaf)) { throw "tkinter source file is missing: $File" }
        Copy-Item -LiteralPath $SourceFile -Destination (Join-Path $RuntimeRoot (Split-Path $File -Leaf)) -Force
    }

    # bug-fix: embeddable zip 的 python312.zip 不含 tkinter 纯 Python 包，只拷 tcl/tk 运行时
    # 会让下方自检抛 ModuleNotFoundError —— 补拷 Lib\tkinter 到 Lib\site-packages
    # （._pth 已含 Lib\site-packages 搜索路径，无需再改 ._pth）（2026-08-26，凌霜修 UX）
    $TkinterPackage = Join-Path $Source "Lib\tkinter"
    if (-not (Test-Path -LiteralPath $TkinterPackage -PathType Container)) {
        throw "tkinter source package is missing: $TkinterPackage"
    }
    Copy-Item -LiteralPath $TkinterPackage -Destination (Join-Path $RuntimeRoot "Lib\site-packages\tkinter") -Recurse -Force

    # 装机自检：tkinter / customtkinter 均为壳 UI 硬依赖，必须可导入
    & $Python -c "import tkinter; print('tkinter', tkinter.TkVersion)"
    if ($LASTEXITCODE -ne 0) { throw "Embedded Python tkinter self-check failed" }
    # bug-fix: customtkinter 已入 requirements-release.lock（本脚本上方统一安装），
    # 自检由"未装则跳过"改为硬失败 —— 装机产品缺它必挂（2026-08-26，凌霜修 UX）
    & $Python -c "import customtkinter"
    if ($LASTEXITCODE -ne 0) { throw "Embedded Python customtkinter self-check failed" }
    Write-Host "Embedded Python tkinter runtime ready (source: $Source)"
}

Copy-TkinterRuntime
