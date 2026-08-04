param(
  [Parameter(Mandatory = $false)]
  [string]$PackageRoot = "",
  [Parameter(Mandatory = $false)]
  [string]$QaRoot = ""
)

$ErrorActionPreference = "Stop"
$WorkspaceRoot = Split-Path -Parent $PSScriptRoot
if (-not $PackageRoot) {
  $PackageRoot = Join-Path $WorkspaceRoot "release-stage\win32-x64\electron-builder\win-unpacked"
}
$PackageRoot = (Resolve-Path -LiteralPath $PackageRoot).Path
$InitialPackageCacheDirectories = @(
  Get-ChildItem -LiteralPath $PackageRoot -Recurse -Directory -Filter "__pycache__" -Force -ErrorAction SilentlyContinue
)
if ($InitialPackageCacheDirectories.Count) {
  throw "packaged payload already contains runtime Python cache directories"
}
$Executable = Get-ChildItem -LiteralPath $PackageRoot -Filter "*.exe" |
  Where-Object { $_.Name -notlike "Uninstall*" } |
  Select-Object -First 1 -ExpandProperty FullName
if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
  throw "packaged Electron executable is missing"
}
if (-not $QaRoot) {
  $QaRoot = Join-Path $env:LOCALAPPDATA ("Temp\TiangongQA-v3.0.3-" + [Guid]::NewGuid().ToString("N"))
}
$QaRoot = [IO.Path]::GetFullPath($QaRoot)

$EnvironmentNames = @(
  "APPDATA",
  "LOCALAPPDATA",
  "USERPROFILE",
  "PORTABLE_EXECUTABLE_DIR",
  "TIANGONG_DESKTOP_RUNTIME_ROOT",
  "TIANGONG_HOME_PATH",
  "TIANGONG_DOCUMENTS_PATH",
  "TIANGONG_DESKTOP_PATH",
  "TIANGONG_DOWNLOADS_PATH",
  "TIANGONG_PICTURES_PATH",
  "TIANGONG_VIDEOS_PATH",
  "TIANGONG_MUSIC_PATH"
  "TIANGONG_DESKTOP_TOKEN"
  "TIANGONG_CDP_EXPRESSION"
)

function Stop-PackageProcesses {
  param([System.Diagnostics.Process]$DesktopProcess)
  if ($DesktopProcess -and -not $DesktopProcess.HasExited) {
    $CloseRequested = $false
    try { $CloseRequested = $DesktopProcess.CloseMainWindow() } catch {}
    if (-not $CloseRequested) {
      Stop-Process -Id $DesktopProcess.Id -Force -ErrorAction SilentlyContinue
    } else {
      try { Wait-Process -Id $DesktopProcess.Id -Timeout 20 -ErrorAction Stop } catch {
        Stop-Process -Id $DesktopProcess.Id -Force -ErrorAction SilentlyContinue
      }
    }
  }
  Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith(
      $PackageRoot,
      [StringComparison]::OrdinalIgnoreCase
    )
  } | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
  $Deadline = [DateTime]::UtcNow.AddSeconds(20)
  do {
    $Listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
      Where-Object LocalPort -in 7174, 7175, 7176, 7184
    if (-not $Listeners) { return }
    Start-Sleep -Milliseconds 250
  } while ([DateTime]::UtcNow -lt $Deadline)
  throw "packaged service listener leaked after shutdown"
}

function Invoke-CdpExpression {
  param(
    [Parameter(Mandatory = $true)][string]$Expression,
    [Parameter(Mandatory = $true)][int]$DebugPort
  )
  $env:TIANGONG_CDP_EXPRESSION = $Expression
  $Json = & node `
    (Join-Path $WorkspaceRoot "scripts\cdp-evaluate.mjs") `
    "http://127.0.0.1:$DebugPort" 2>$null
  if ($LASTEXITCODE -ne 0 -or -not $Json) {
    throw "CDP expression failed on port $DebugPort"
  }
  return $Json | ConvertFrom-Json
}

function Invoke-PackagedScenario {
  param(
    [string]$Name,
    [ValidateSet("explicit", "stale-file", "portable-file")]
    [string]$RuntimeMode,
    [double]$ScaleFactor = 1.0,
    [int]$DebugPort = 9321,
    [switch]$LegacyUpgrade,
    [switch]$LayoutMatrix
  )
  $UserProfileLabel = ([char]0x7528).ToString() + [char]0x6237 + " " + [char]0x914D + [char]0x7F6E
  $RuntimeLabel = ([char]0x8FD0).ToString() + [char]0x884C + " " + [char]0x6570 + [char]0x636E
  $ScenarioRoot = Join-Path $QaRoot $Name
  $ProfileRoot = Join-Path $ScenarioRoot $UserProfileLabel
  $RoamingRoot = Join-Path $ScenarioRoot "AppData\Roaming"
  $LocalRoot = Join-Path $ScenarioRoot "AppData\Local"
  $ElectronUserDataRoot = Join-Path $RoamingRoot "tiangong-v3-qiyuan"
  $RequestedRuntime = Join-Path $ScenarioRoot $RuntimeLabel
  $Folders = @{
    TIANGONG_HOME_PATH = $ProfileRoot
    TIANGONG_DOCUMENTS_PATH = (Join-Path $ProfileRoot "Documents")
    TIANGONG_DESKTOP_PATH = (Join-Path $ProfileRoot "Desktop")
    TIANGONG_DOWNLOADS_PATH = (Join-Path $ProfileRoot "Downloads")
    TIANGONG_PICTURES_PATH = (Join-Path $ProfileRoot "Pictures")
    TIANGONG_VIDEOS_PATH = (Join-Path $ProfileRoot "Videos")
    TIANGONG_MUSIC_PATH = (Join-Path $ProfileRoot "Music")
  }
  @($ScenarioRoot, $ProfileRoot, $RoamingRoot, $LocalRoot, $ElectronUserDataRoot, $RequestedRuntime) +
    @($Folders.Values) | ForEach-Object {
      New-Item -ItemType Directory -Force -Path $_ | Out-Null
    }

  $Previous = @{}
  foreach ($VariableName in $EnvironmentNames) {
    $Previous[$VariableName] = [Environment]::GetEnvironmentVariable($VariableName, "Process")
  }
  $DesktopProcess = $null
  $DesktopApiToken = "qa-desktop-token-" + ("a" * 64)
  $ExpectedLegacyLifeId = ""
  try {
    $env:APPDATA = $RoamingRoot
    $env:LOCALAPPDATA = $LocalRoot
    $env:USERPROFILE = $ProfileRoot
    foreach ($Entry in $Folders.GetEnumerator()) {
      [Environment]::SetEnvironmentVariable($Entry.Key, $Entry.Value, "Process")
    }
    $env:TIANGONG_DESKTOP_TOKEN = $DesktopApiToken
    Remove-Item Env:PORTABLE_EXECUTABLE_DIR -ErrorAction SilentlyContinue
    if ($RuntimeMode -eq "explicit") {
      $env:TIANGONG_DESKTOP_RUNTIME_ROOT = $RequestedRuntime
    } elseif ($RuntimeMode -eq "stale-file") {
      $StaleRoot = Join-Path $ScenarioRoot "stale-runtime-root"
      [IO.File]::WriteAllText($StaleRoot, "this is a file, not a directory")
      $env:TIANGONG_DESKTOP_RUNTIME_ROOT = $StaleRoot
    } else {
      $PortableFile = Join-Path $ScenarioRoot "read-only-portable-target.exe"
      [IO.File]::WriteAllText($PortableFile, "not a directory")
      $env:PORTABLE_EXECUTABLE_DIR = $PortableFile
      $env:TIANGONG_DESKTOP_RUNTIME_ROOT = $RequestedRuntime
    }

    if ($LegacyUpgrade) {
      $ExpectedLegacyLifeId = "org_" + ("1" * 32)
      $BornAt = "2020-01-02T03:04:05.000Z"
      $LineageId = "lineage_" + ("2" * 32)
      $Canonical = '{"born_at":"' + $BornAt + '","lineage_id":"' + $LineageId + '","organism_id":"' + $ExpectedLegacyLifeId + '"}'
      $Sha = [Security.Cryptography.SHA256]::Create()
      try {
        $IdentityHash = ([BitConverter]::ToString($Sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Canonical)))).Replace("-", "").ToLowerInvariant()
      } finally {
        $Sha.Dispose()
      }
      $LegacyRoot = Join-Path $RequestedRuntime "state\life_kernel"
      $LegacyJournal = Join-Path $LegacyRoot "journal"
      New-Item -ItemType Directory -Force -Path $LegacyJournal | Out-Null
      $LegacyIdentityJson = [ordered]@{
        schema = "tiangong.organism.identity.v1"
        organism_id = $ExpectedLegacyLifeId
        lineage_id = $LineageId
        born_at = $BornAt
        identity_hash = $IdentityHash
      } | ConvertTo-Json
      $Utf8NoBom = New-Object Text.UTF8Encoding($false)
      [IO.File]::WriteAllText((Join-Path $LegacyRoot "identity.json"), $LegacyIdentityJson, $Utf8NoBom)
      [IO.File]::WriteAllText((Join-Path $LegacyJournal "life_events.jsonl"), "{`"event`":`"legacy-upgrade-must-preserve`"}`n", $Utf8NoBom)
    }

    $DebugOccupied = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
      Where-Object LocalPort -eq $DebugPort
    if ($DebugOccupied) { throw "CDP port $DebugPort is already occupied" }

    $DesktopProcess = Start-Process `
      -FilePath $Executable `
      -ArgumentList @(
        ('--user-data-dir="' + $ElectronUserDataRoot + '"'),
        "--remote-debugging-port=$DebugPort",
        "--force-device-scale-factor=$ScaleFactor"
      ) `
      -WorkingDirectory $PackageRoot `
      -PassThru `
      -WindowStyle Hidden
    $Ready = $null
    $GatewayHealth = $null
    $ReadinessProbe = $null
    $GatewayProbeExpression = @'
(async () => {
  const base = window.tiangongDesktop.getGatewayUrl();
  const headers = window.tiangongDesktop.getGatewayHeaders();
  const fetchJson = async (path) => {
    try {
      const response = await fetch(base + path, { headers });
      let body = null;
      try { body = await response.json(); } catch {}
      return { httpStatus: response.status, body };
    } catch (error) {
      return { httpStatus: 0, error: String(error?.message || error), body: null };
    }
  };
  return { gateway: await fetchJson('/health'), ready: await fetchJson('/ready') };
})()
'@
    Start-Sleep -Seconds 10
    if ($DesktopProcess.HasExited) {
      throw "packaged Electron exited early with code $($DesktopProcess.ExitCode)"
    }
    try { $ReadinessProbe = Invoke-CdpExpression -Expression $GatewayProbeExpression -DebugPort $DebugPort } catch {}
    $GatewayHealth = $ReadinessProbe.gateway.body
    $Ready = $ReadinessProbe.ready.body
    if (
      $ReadinessProbe.gateway.httpStatus -ne 200 -or
      $GatewayHealth.status -ne "ALIVE" -or
      $ReadinessProbe.ready.httpStatus -ne 200 -or
      $Ready.status -ne "READY"
    ) {
      # First-launch Defender/Mark-of-the-Web scanning can consume most of the
      # product's 60-second service budget.  Retry once without probe traffic.
      Start-Sleep -Seconds 50
      if ($DesktopProcess.HasExited) {
        throw "packaged Electron exited during extended first-launch wait with code $($DesktopProcess.ExitCode)"
      }
      $ReadinessProbe = Invoke-CdpExpression -Expression $GatewayProbeExpression -DebugPort $DebugPort
      $GatewayHealth = $ReadinessProbe.gateway.body
      $Ready = $ReadinessProbe.ready.body
    }
    if (
      $ReadinessProbe.gateway.httpStatus -ne 200 -or
      $GatewayHealth.status -ne "ALIVE" -or
      $ReadinessProbe.ready.httpStatus -ne 200 -or
      $Ready.status -ne "READY"
    ) {
      throw ("packaged gateway readiness probe failed: " + `
        ($ReadinessProbe | ConvertTo-Json -Depth 8 -Compress))
    }

    $LifePanelExpression = @'
(async () => {
  const response = await fetch(
    window.tiangongDesktop.getGatewayUrl() + '/api/v1/v3/life/panel',
    { headers: window.tiangongDesktop.getGatewayHeaders() }
  );
  return { httpStatus: response.status, body: await response.json() };
})()
'@
    $LifePanelResponse = Invoke-CdpExpression -Expression $LifePanelExpression -DebugPort $DebugPort
    if ($LifePanelResponse.httpStatus -ne 200) {
      throw ("packaged life panel failed: " + ($LifePanelResponse | ConvertTo-Json -Depth 8 -Compress))
    }
    $LifePanel = $LifePanelResponse.body
    if ($LegacyUpgrade) {
      if ($LifePanel.setup_required -eq $true -or $LifePanel.identity.life_id -ne $ExpectedLegacyLifeId) {
        throw "legacy upgrade did not restore and activate the original life id"
      }
    } elseif ($LifePanel.setup_required -eq $true) {
      $CreateExpression = @'
(async () => {
  const response = await fetch(
    window.tiangongDesktop.getGatewayUrl() + '/api/v1/v3/life/identity/create',
    {
      method: 'POST',
      headers: { ...window.tiangongDesktop.getGatewayHeaders(), 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({ name: 'QA Fresh User' })
    }
  );
  return { httpStatus: response.status, body: await response.json() };
})()
'@
      $CreateResponse = Invoke-CdpExpression -Expression $CreateExpression -DebugPort $DebugPort
      if ($CreateResponse.httpStatus -ne 200) {
        throw ("packaged life identity creation failed: " + ($CreateResponse | ConvertTo-Json -Depth 8 -Compress))
      }
      $LifePanel = (Invoke-CdpExpression -Expression $LifePanelExpression -DebugPort $DebugPort).body
    }
    if ($LifePanel.setup_required -eq $true -or -not $LifePanel.identity.life_id) {
      throw "packaged first run did not reach an active life identity"
    }

    # Keep this script source ASCII-only. Windows PowerShell 5 decodes a UTF-8
    # script without a BOM using the active ANSI code page, which previously
    # turned this directory name into mojibake after an otherwise harmless edit.
    $LifeDataDirectoryName = -join @(
      [char]0x5929, [char]0x5DE5, [char]0x9020, [char]0x7269,
      [char]0x751F, [char]0x547D, [char]0x6570, [char]0x636E
    )
    $MigrationReport = Join-Path `
      (Join-Path $Folders.TIANGONG_DOCUMENTS_PATH $LifeDataDirectoryName) `
      "identity_migration_report.json"
    $MigrationStatus = "not_required"
    $MigrationDeadline = [DateTime]::UtcNow.AddSeconds(15)
    do {
      if (Test-Path -LiteralPath $MigrationReport -PathType Leaf) {
        try {
          $MigrationStatus = [string](
            Get-Content -LiteralPath $MigrationReport -Raw -Encoding UTF8 |
              ConvertFrom-Json
          ).status
        } catch {
          $MigrationStatus = "report_read_incomplete"
        }
      }
      if (-not $LegacyUpgrade -or $MigrationStatus -in @("completed", "failed")) { break }
      Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $MigrationDeadline)
    if ($LegacyUpgrade -and $MigrationStatus -ne "completed") {
      throw "legacy identity migration report did not complete: status=$MigrationStatus report=$MigrationReport"
    }

    $LayoutResult = $null
    if ($LayoutMatrix) {
      $LayoutJson = & node (Join-Path $WorkspaceRoot "scripts\cdp-layout-matrix.mjs") "http://127.0.0.1:$DebugPort"
      if ($LASTEXITCODE -ne 0) { throw "packaged high-DPI layout matrix failed: $LayoutJson" }
      $LayoutResult = $LayoutJson | ConvertFrom-Json
      if (-not $LayoutResult.ok -or $LayoutResult.checks -lt 700) {
        throw "packaged high-DPI layout matrix did not cover the required cases"
      }
    }

    $Diagnostic = Get-ChildItem -LiteralPath $ScenarioRoot -Recurse `
      -Filter "desktop_renderer.jsonl" -ErrorAction SilentlyContinue |
      Select-Object -First 1
    $Rows = if ($Diagnostic) {
      @(Get-Content -LiteralPath $Diagnostic.FullName -Encoding UTF8)
    } else { @() }
    $FatalDiagnostics = @($Rows | Where-Object {
      $_ -match '"kind":"(did-fail-load|render-process-gone|load-frontend-rejected)"'
    })
    $VerifiedComponents = @($Ready.decision.verified_component_ids)
    $LegacyListeners = @(
      Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object LocalPort -in 7174, 7175, 7176
    )
    $GatewayListeners = @(
      Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object LocalPort -eq 7184
    )
    $Result = [ordered]@{
      scenario = $Name
      runtime_mode = $RuntimeMode
      device_scale_factor = $ScaleFactor
      process_alive = (-not $DesktopProcess.HasExited)
      gateway_alive = ($GatewayHealth.status -eq "ALIVE")
      life_alive = ($VerifiedComponents -contains "tiangong-life-service")
      communication_alive = ($VerifiedComponents -contains "tiangong-communication-service")
      gateway_ready = ($Ready.status -eq "READY")
      single_process_runtime = ($Ready.topology.physical_python_processes -eq 1)
      gateway_listener_present = ($GatewayListeners.Count -ge 1)
      legacy_ports_closed = ($LegacyListeners.Count -eq 0)
      readiness_http_status = [int]$ReadinessProbe.ready.httpStatus
      readiness_reasons = @($Ready.reason_codes)
      verified_components = $VerifiedComponents
      frontend_loaded = [bool]($Rows -match "frontend-load-complete-ms")
      active_life_id = [string]$LifePanel.identity.life_id
      legacy_life_restored = (-not $LegacyUpgrade -or $LifePanel.identity.life_id -eq $ExpectedLegacyLifeId)
      migration_status = $MigrationStatus
      layout_checks = if ($LayoutResult) { [int]$LayoutResult.checks } else { 0 }
      fatal_renderer_diagnostics = $FatalDiagnostics.Count
      diagnostic_path = if ($Diagnostic) { $Diagnostic.FullName } else { "" }
    }
    if (
      -not $Result.process_alive -or
      -not $Result.gateway_alive -or
      -not $Result.life_alive -or
      -not $Result.communication_alive -or
      -not $Result.gateway_ready -or
      -not $Result.single_process_runtime -or
      -not $Result.gateway_listener_present -or
      -not $Result.legacy_ports_closed -or
      $Result.readiness_http_status -ne 200 -or
      $VerifiedComponents.Count -ne 4 -or
      -not $Result.frontend_loaded -or
      -not $Result.active_life_id -or
      -not $Result.legacy_life_restored -or
      $Result.fatal_renderer_diagnostics -ne 0
    ) {
      throw ("packaged first-run scenario failed: " + ($Result | ConvertTo-Json -Depth 6 -Compress))
    }
    return [pscustomobject]$Result
  } finally {
    Stop-PackageProcesses -DesktopProcess $DesktopProcess
    foreach ($VariableName in $EnvironmentNames) {
      [Environment]::SetEnvironmentVariable(
        $VariableName,
        $Previous[$VariableName],
        "Process"
      )
    }
  }
}

$ExistingListeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object LocalPort -in 7174, 7175, 7176, 7184
if ($ExistingListeners) {
  throw "Tiangong QA ports 7174, 7175, 7176 or 7184 are already occupied; refusing to disturb another instance"
}

$Results = @(
  Invoke-PackagedScenario -Name "01-clean-chinese-space-profile" -RuntimeMode "explicit" -ScaleFactor 1.0 -DebugPort 9321 -LayoutMatrix
  Invoke-PackagedScenario -Name "02-stale-old-binding-fallback" -RuntimeMode "stale-file" -ScaleFactor 1.25 -DebugPort 9322
  Invoke-PackagedScenario -Name "03-unwritable-portable-fallback" -RuntimeMode "portable-file" -ScaleFactor 1.75 -DebugPort 9323
  Invoke-PackagedScenario -Name "04-legacy-v1-upgrade" -RuntimeMode "explicit" -ScaleFactor 2.0 -DebugPort 9324 -LegacyUpgrade
)
$FinalPackageCacheDirectories = @(
  Get-ChildItem -LiteralPath $PackageRoot -Recurse -Directory -Filter "__pycache__" -Force -ErrorAction SilentlyContinue
)
if ($FinalPackageCacheDirectories.Count) {
  throw "packaged first run mutated the installation payload with Python caches"
}
$Results | ConvertTo-Json -Depth 6
