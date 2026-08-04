[CmdletBinding()]
param(
    [switch]$Verify,
    [ValidateRange(0, 65535)]
    [int]$RemoteDebuggingPort = 0
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AppRoot = Join-Path $Root "app"
$HostLocalAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$HostUserProfile = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
$HostDesktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
$HostDocuments = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments)
$HostPictures = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyPictures)
$HostMusic = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyMusic)
$HostVideos = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyVideos)
try {
    $HostDownloads = (New-Object -ComObject Shell.Application).Namespace("shell:Downloads").Self.Path
} catch {
    $HostDownloads = Join-Path $HostUserProfile "Downloads"
}
if (-not $HostLocalAppData) {
    throw "Windows LocalApplicationData is unavailable; refusing to fall back to the packaged profile."
}

$SourceProfileRoot = Join-Path $HostLocalAppData "TiangongV3-SourceWork"
$SourceUserData = Join-Path $SourceProfileRoot "electron-user-data"
$SourceRuntimeRoot = Join-Path $SourceProfileRoot "runtime"
$SourceStateRoot = Join-Path $SourceRuntimeRoot "state"
$SourceHomeRoot = Join-Path $SourceProfileRoot "home"
$SourceWorkspaceRoot = Join-Path $SourceProfileRoot "workspace"
$SourceLifeDataRoot = Join-Path $SourceRuntimeRoot "life-data"
$SourceLifeRuntimeRoot = Join-Path $SourceRuntimeRoot "complete-life"
$SourceLifeKernelRoot = Join-Path $SourceStateRoot "life_kernel"
$SourceLifeTransactionRoot = Join-Path $SourceStateRoot "life_transaction"
$SourceTempRoot = Join-Path $SourceProfileRoot "temp"
$SourceRoamingRoot = Join-Path $SourceProfileRoot "appdata"
$SourceLocalRoot = Join-Path $SourceProfileRoot "local-appdata"
foreach ($Directory in @(
    $SourceUserData, $SourceRuntimeRoot, $SourceStateRoot, $SourceHomeRoot,
    $SourceWorkspaceRoot, $SourceLifeDataRoot, $SourceLifeRuntimeRoot,
    $SourceLifeKernelRoot, $SourceLifeTransactionRoot, $SourceTempRoot,
    $SourceRoamingRoot, $SourceLocalRoot
)) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
}
# USERPROFILE/HOME 重定向到隔离 home 后，Windows Shell 的已知文件夹
# （Desktop/Pictures 等）会解析到其下的同名子目录；文件选择对话框初始化
# 快速访问时会探测这些路径，缺失会弹“位置不可用”。预先建齐空目录。
foreach ($HomeFolder in @("Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos")) {
    New-Item -ItemType Directory -Path (Join-Path $SourceHomeRoot $HomeFolder) -Force | Out-Null
}

# 7184 is a fixed production contract.  Source mode may reuse only its own
# listener; a packaged/unknown listener is never adopted or terminated.
$SourceRootPrefix = $Root.TrimEnd("\") + "\"
try {
    Get-Command Get-NetTCPConnection -ErrorAction Stop | Out-Null
} catch {
    throw "Unable to verify the owner of port 7184; source startup fails closed."
}
$GatewayListeners = @(Get-NetTCPConnection -State Listen -LocalPort 7184 -ErrorAction SilentlyContinue)
foreach ($Listener in $GatewayListeners) {
    $Owner = Get-CimInstance Win32_Process -Filter "ProcessId = $($Listener.OwningProcess)" -ErrorAction SilentlyContinue
    $ExecutablePath = [string]$Owner.ExecutablePath
    $CommandLine = [string]$Owner.CommandLine
    $OwnedBySource = (
        $ExecutablePath.StartsWith($SourceRootPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        $CommandLine.IndexOf($SourceRootPrefix, [StringComparison]::OrdinalIgnoreCase) -ge 0
    )
    if (-not $OwnedBySource) {
        throw "Port 7184 is owned by non-source process PID $($Listener.OwningProcess) ($ExecutablePath). Source mode will not adopt or stop it."
    }
}

$Python = Join-Path $AppRoot "runtime\python312\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Python = Join-Path $AppRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Embedded source runtime is not installed. Run scripts\setup-source.ps1 first."
}
if (-not (Test-Path -LiteralPath (Join-Path $AppRoot "node_modules\electron\dist\electron.exe") -PathType Leaf)) {
    throw "Electron dependencies are not installed. Run scripts\setup-source.ps1 first."
}
if ($Verify) {
    & $Python (Join-Path $Root "scripts\verify_source.py") --quick
}

$env:TIANGONG_BACKEND_PYTHON = $Python
$env:TIANGONG_SOURCE_MODE = "1"
$env:TIANGONG_SOURCE_ROOT = $Root
$env:TIANGONG_SOURCE_PROFILE_ROOT = $SourceProfileRoot
$env:TIANGONG_SOURCE_USER_DATA = $SourceUserData
$env:TIANGONG_DESKTOP_RUNTIME_ROOT = $SourceRuntimeRoot
$env:TIANGONG_DESKTOP_STATE_DIR = $SourceStateRoot
$env:TIANGONG_RUN_STATE_DIR = $SourceStateRoot
$env:TIANGONG_V3_STATE_DIR = $SourceStateRoot
$env:TIANGONG_DESKTOP_WORKSPACE_ROOT = $SourceWorkspaceRoot
$env:TIANGONG_WORKSPACE_ROOT = $SourceWorkspaceRoot
$env:TIANGONG_FORCE_WORKSPACE_ROOT = $SourceWorkspaceRoot
$env:TIANGONG_OMNI_BODY_WORKSPACE = $SourceWorkspaceRoot
$env:TIANGONG_HOME_PATH = $SourceHomeRoot
$env:TIANGONG_LIFE_DATA_ROOT = $SourceLifeDataRoot
$env:TIANGONG_LIFE_RUNTIME_ROOT = $SourceLifeRuntimeRoot
$env:TIANGONG_LIFE_KERNEL_ROOT = $SourceLifeKernelRoot
$env:TIANGONG_LIFE_ROOT = $SourceLifeTransactionRoot
$env:TIANGONG_EXECUTION_RUNTIME_ROOT = $SourceLifeKernelRoot
$env:TIANGONG_EXECUTION_LIFE_ROOT = $SourceLifeTransactionRoot
$env:TIANGONG_DESKTOP_PATH = $HostDesktop
$env:TIANGONG_DOWNLOADS_PATH = $HostDownloads
$env:TIANGONG_DOCUMENTS_PATH = $HostDocuments
$env:TIANGONG_PICTURES_PATH = $HostPictures
$env:TIANGONG_MUSIC_PATH = $HostMusic
$env:TIANGONG_VIDEOS_PATH = $HostVideos
$env:APPDATA = $SourceRoamingRoot
$env:LOCALAPPDATA = $SourceLocalRoot
$env:HOME = $SourceHomeRoot
$env:USERPROFILE = $SourceHomeRoot
$env:TEMP = $SourceTempRoot
$env:TMP = $SourceTempRoot
$env:TIANGONG_BACKEND_DIR = Join-Path $AppRoot "backend\tiangong-backend"
$env:TIANGONG_LIFE_SERVICE_DIR = Join-Path $AppRoot "life-service"
$env:TIANGONG_COMMUNICATION_SOURCE_ROOT = Join-Path $Root "src"
$env:TIANGONG_TOTAL_GATEWAY_SOURCE_ROOT = Join-Path $Root "src"
$env:TIANGONG_GATEWAY_DEPLOYMENT_MODE = "embedded"
$env:TIANGONG_GATEWAY_SKILL_ROOT = Join-Path $AppRoot "backend\tiangong-backend\_internal\omni_body_skill"
$env:PYTHONUTF8 = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
# This host can create the AppContainer token but arbitrary Win32 children exit
# during system DLL initialization before user code starts.  Source mode opts
# into the existing compatibility sandbox explicitly; packaged/default runtime
# remains fail-closed.  The compatibility path still uses a private workspace
# copy, secret-free environment, resource limits and kill-on-close process tree.
$env:TIANGONG_SANDBOX_COMPAT = "1"

# Rebuild the development release authority from the exact source tree that
# will be launched. This prevents a checked-in legacy 7174/7175/7176 manifest
# from making the embedded Life/Runtime modules appear downgraded.
& $Python (Join-Path $Root "scripts\refresh-source-release.py")
if ($LASTEXITCODE -ne 0) { throw "Failed to refresh source release authority" }

Push-Location $AppRoot
try {
    $ElectronArgs = @("--user-data-dir=$SourceUserData")
    if ($RemoteDebuggingPort -gt 0) {
        $ElectronArgs += "--remote-debugging-port=$RemoteDebuggingPort"
    }
    $ElectronArgs += "."
    & (Join-Path $AppRoot "node_modules\.bin\electron.cmd") @ElectronArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
