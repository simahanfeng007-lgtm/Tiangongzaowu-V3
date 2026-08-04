param(
  [string]$PackageRoot = "",
  [string]$QaRoot = "",
  [int]$RemoteDebuggingPort = 9223
)

$ErrorActionPreference = "Stop"
$WorkspaceRoot = Split-Path -Parent $PSScriptRoot
if (-not $PackageRoot) {
  $PackageRoot = Join-Path $WorkspaceRoot "release-stage\win32-x64\electron-builder\win-unpacked"
}
$PackageRoot = (Resolve-Path -LiteralPath $PackageRoot).Path
$Executable = Get-ChildItem -LiteralPath $PackageRoot -Filter "*.exe" |
  Where-Object { $_.Name -notlike "Uninstall*" } |
  Select-Object -First 1 -ExpandProperty FullName
if (-not $Executable) { throw "packaged Electron executable is missing" }
if (-not $QaRoot) {
  $QaRoot = Join-Path $env:LOCALAPPDATA "Temp\TGQA-UI"
}
$QaRoot = [IO.Path]::GetFullPath($QaRoot)
$RuntimeRoot = Join-Path $QaRoot "runtime"
$UserDataRoot = Join-Path $QaRoot "user-data"
$InitialWorkspace = Join-Path $QaRoot "workspace-before"
@($QaRoot, $RuntimeRoot, $UserDataRoot, $InitialWorkspace) | ForEach-Object {
  New-Item -ItemType Directory -Force -Path $_ | Out-Null
}

$Occupied = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object LocalPort -in 7174, 7175, 7176, 7184, $RemoteDebuggingPort
if ($Occupied) { throw "required UI QA port is already occupied" }

$env:TIANGONG_DESKTOP_RUNTIME_ROOT = $RuntimeRoot
$env:TIANGONG_HOME_PATH = $QaRoot
$env:TIANGONG_DOCUMENTS_PATH = $QaRoot
$env:TIANGONG_DESKTOP_PATH = $QaRoot
$env:TIANGONG_DOWNLOADS_PATH = $QaRoot
$env:TIANGONG_DESKTOP_WORKSPACE_ROOT = $InitialWorkspace
$Arguments = @(
  "--remote-debugging-port=$RemoteDebuggingPort",
  ('--user-data-dir="' + $UserDataRoot + '"')
)
$DesktopProcess = Start-Process `
  -FilePath $Executable `
  -ArgumentList $Arguments `
  -WorkingDirectory $PackageRoot `
  -PassThru `
  -WindowStyle Hidden

$Ready = $null
$Cdp = $null
$Deadline = [DateTime]::UtcNow.AddSeconds(100)
do {
  Start-Sleep -Milliseconds 500
  if ($DesktopProcess.HasExited) {
    throw "packaged Electron exited before UI QA"
  }
  try {
    $Ready = Invoke-RestMethod -Uri "http://127.0.0.1:7184/ready" -TimeoutSec 4
  } catch {}
  try {
    $Cdp = Invoke-RestMethod `
      -Uri "http://127.0.0.1:$RemoteDebuggingPort/json/version" `
      -TimeoutSec 2
  } catch {}
  if ($Ready.status -eq "READY" -and $Cdp.webSocketDebuggerUrl) { break }
} while ([DateTime]::UtcNow -lt $Deadline)

$Result = [ordered]@{
  pid = $DesktopProcess.Id
  ready = $Ready.status
  readiness_http_status = $Ready.http_status
  cdp = [bool]$Cdp.webSocketDebuggerUrl
  cdp_endpoint = "http://127.0.0.1:$RemoteDebuggingPort"
  package_root = $PackageRoot
  runtime_root = $RuntimeRoot
  initial_workspace = $InitialWorkspace
}
$Result | ConvertTo-Json -Compress
if ($Result.ready -ne "READY" -or -not $Result.cdp) {
  throw "UI QA application did not become ready and attachable"
}
