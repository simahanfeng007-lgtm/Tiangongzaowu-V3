param(
  [Parameter(Mandatory=$true)][string]$Installer,
  [Parameter(Mandatory=$true)][string]$AppExe,
  [Parameter(Mandatory=$true)][string]$HealthMarker,
  [Parameter(Mandatory=$true)][string]$Token,
  [Parameter(Mandatory=$true)][int]$ParentPid,
  [Parameter(Mandatory=$true)][string]$RollbackInstaller,
  [Parameter(Mandatory=$false)][string]$RollbackInstallerSha256 = ""
)
$ErrorActionPreference = "Stop"
function Assert-RollbackVerified {
  if (-not (Test-Path -LiteralPath $RollbackInstaller)) { throw "rollback_baseline_missing" }
  # P2-21: the rollback installer must be re-verified before it is executed.
  if ($RollbackInstallerSha256) {
    $actual = (Get-FileHash -LiteralPath $RollbackInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $RollbackInstallerSha256.ToLowerInvariant()) { throw "rollback_hash_mismatch" }
  } else {
    throw "rollback_hash_not_provided"
  }
}
function Invoke-Rollback([string]$Reason) {
  Get-Process -ErrorAction SilentlyContinue | Where-Object {
    try { $_.Path -eq $AppExe } catch { $false }
  } | Stop-Process -Force -ErrorAction SilentlyContinue
  Assert-RollbackVerified
  $rollback = Start-Process -FilePath $RollbackInstaller -ArgumentList "/S" -PassThru -Wait
  if ($rollback.ExitCode -eq 0) {
    Start-Process -FilePath $AppExe
    exit 20
  }
  Write-Error "rollback_failed:$Reason:$($rollback.ExitCode)"
  exit 22
}
try {
  Wait-Process -Id $ParentPid -Timeout 120 -ErrorAction SilentlyContinue
  if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) { Stop-Process -Id $ParentPid -Force }
  Assert-RollbackVerified
  $install = Start-Process -FilePath $Installer -ArgumentList "/S" -PassThru -Wait
  if ($install.ExitCode -ne 0) { Invoke-Rollback "installer_exit_$($install.ExitCode)" }
  Start-Process -FilePath $AppExe -ArgumentList "--tiangong-post-update-token=$Token"
  $deadline = (Get-Date).AddMinutes(3)
  while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $HealthMarker) { exit 0 }
    Start-Sleep -Seconds 2
  }
  Invoke-Rollback "health_timeout"
} catch {
  if (Test-Path -LiteralPath $RollbackInstaller) { Invoke-Rollback $_.Exception.Message }
  throw
}
