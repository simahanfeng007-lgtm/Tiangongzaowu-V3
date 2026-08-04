[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateNotNullOrEmpty()]
  [string]$InstalledExe,

  [Parameter(Mandatory = $false)]
  [string]$QaRoot = "",

  [Parameter(Mandatory = $false)]
  [ValidateRange(1024, 65535)]
  [int]$DebugPort = 9331,

  [Parameter(Mandatory = $false)]
  [ValidateRange(30, 600)]
  [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$CdpHelper = Join-Path $PSScriptRoot "cdp-evaluate.mjs"
$QaPorts = @(7174, 7175, 7176, 7184, $DebugPort) | Select-Object -Unique
if ($DebugPort -in @(7174, 7175, 7176, 7184)) {
  throw "DebugPort must not overlap a Tiangong service port"
}

function Test-PathWithinRoot {
  param(
    [Parameter(Mandatory = $true)][string]$Candidate,
    [Parameter(Mandatory = $true)][string]$Root
  )
  $FullCandidate = [IO.Path]::GetFullPath($Candidate).TrimEnd("\", "/")
  $FullRoot = [IO.Path]::GetFullPath($Root).TrimEnd("\", "/")
  if ($FullCandidate.Equals($FullRoot, [StringComparison]::OrdinalIgnoreCase)) {
    return $true
  }
  return $FullCandidate.StartsWith(
    $FullRoot + [IO.Path]::DirectorySeparatorChar,
    [StringComparison]::OrdinalIgnoreCase
  )
}

function Get-ListeningConnections {
  param([int[]]$Ports)
  return @(
    Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
      Where-Object { $_.LocalPort -in $Ports }
  )
}

function Get-InstallationProcesses {
  param([Parameter(Mandatory = $true)][string]$InstallRoot)
  return @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object {
        $_.ExecutablePath -and
        (Test-PathWithinRoot -Candidate $_.ExecutablePath -Root $InstallRoot)
      }
  )
}

function Get-QAOwnedProcessIds {
  param(
    [Parameter(Mandatory = $true)][int]$RootProcessId,
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][DateTime]$LaunchStartedUtc
  )

  $Processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  $Lineage = New-Object "System.Collections.Generic.HashSet[int]"
  $Owned = New-Object "System.Collections.Generic.HashSet[int]"
  [void]$Lineage.Add($RootProcessId)
  $EarliestAllowedUtc = $LaunchStartedUtc.AddSeconds(-5)

  foreach ($ProcessInfo in $Processes) {
    $CreatedByThisRun = $false
    try {
      $CreatedByThisRun = ([DateTime]$ProcessInfo.CreationDate).ToUniversalTime() -ge $EarliestAllowedUtc
    } catch {}
    if (
      $CreatedByThisRun -and
      $ProcessInfo.ExecutablePath -and
      (Test-PathWithinRoot -Candidate $ProcessInfo.ExecutablePath -Root $InstallRoot)
    ) {
      [void]$Owned.Add([int]$ProcessInfo.ProcessId)
      [void]$Lineage.Add([int]$ProcessInfo.ProcessId)
    }
  }

  do {
    $Added = $false
    foreach ($ProcessInfo in $Processes) {
      $ProcessId = [int]$ProcessInfo.ProcessId
      $ParentProcessId = [int]$ProcessInfo.ParentProcessId
      if ($Lineage.Contains($ProcessId) -or -not $Lineage.Contains($ParentProcessId)) {
        continue
      }
      $CreatedByThisRun = $false
      try {
        $CreatedByThisRun = ([DateTime]$ProcessInfo.CreationDate).ToUniversalTime() -ge $EarliestAllowedUtc
      } catch {}
      if ($CreatedByThisRun) {
        [void]$Owned.Add($ProcessId)
        [void]$Lineage.Add($ProcessId)
        $Added = $true
      }
    }
  } while ($Added)

  return @($Owned)
}

function Stop-QAInstalledProcesses {
  param(
    [System.Diagnostics.Process]$DesktopProcess,
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][DateTime]$LaunchStartedUtc,
    [Parameter(Mandatory = $true)][int[]]$Ports
  )

  if (-not $DesktopProcess) { return }

  $RootProcessId = [int]$DesktopProcess.Id
  try {
    if (-not $DesktopProcess.HasExited) {
      $CloseRequested = $false
      try { $CloseRequested = $DesktopProcess.CloseMainWindow() } catch {}
      if ($CloseRequested) {
        try {
          Wait-Process -Id $RootProcessId -Timeout 20 -ErrorAction Stop
        } catch {}
      }
    }
  } catch {}

  $OwnedProcessIds = @(
    Get-QAOwnedProcessIds `
      -RootProcessId $RootProcessId `
      -InstallRoot $InstallRoot `
      -LaunchStartedUtc $LaunchStartedUtc
  )
  foreach ($ProcessId in ($OwnedProcessIds | Sort-Object -Descending -Unique)) {
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
  }

  $Deadline = [DateTime]::UtcNow.AddSeconds(20)
  do {
    $RemainingOwned = @(
      Get-QAOwnedProcessIds `
        -RootProcessId $RootProcessId `
        -InstallRoot $InstallRoot `
        -LaunchStartedUtc $LaunchStartedUtc |
        Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }
    )
    $RemainingListeners = @(Get-ListeningConnections -Ports $Ports)
    if ($RemainingOwned.Count -eq 0 -and $RemainingListeners.Count -eq 0) {
      return
    }
    foreach ($ProcessId in $RemainingOwned) {
      Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 250
  } while ([DateTime]::UtcNow -lt $Deadline)

  $LeakedProcesses = @(
    Get-QAOwnedProcessIds `
      -RootProcessId $RootProcessId `
      -InstallRoot $InstallRoot `
      -LaunchStartedUtc $LaunchStartedUtc |
      Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }
  )
  $LeakedListeners = @(Get-ListeningConnections -Ports $Ports)
  $LeakSummary = [ordered]@{
    process_ids = @($LeakedProcesses)
    listeners = @(
      $LeakedListeners | ForEach-Object {
        [ordered]@{
          port = [int]$_.LocalPort
          owning_process = [int]$_.OwningProcess
        }
      }
    )
  }
  throw ("installed QA process cleanup failed: " + ($LeakSummary | ConvertTo-Json -Depth 5 -Compress))
}

function Capture-QAEnvironment {
  $FixedNames = @(
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "HOME",
    "TEMP",
    "TMP",
    "HOMEDRIVE",
    "HOMEPATH",
    "PORTABLE_EXECUTABLE_DIR",
    "ELECTRON_RUN_AS_NODE",
    "NODE_OPTIONS"
  )
  $TiangongNames = @(
    Get-ChildItem Env: |
      Where-Object { $_.Name -like "TIANGONG_*" } |
      Select-Object -ExpandProperty Name
  )
  $Names = @($FixedNames + $TiangongNames) | Sort-Object -Unique
  $Snapshot = @{}
  foreach ($Name in $Names) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "Process")
    $Snapshot[$Name] = [pscustomobject]@{
      present = ($null -ne $Value)
      value = $Value
    }
  }
  return $Snapshot
}

function Restore-QAEnvironment {
  param([Parameter(Mandatory = $true)][hashtable]$Snapshot)

  Get-ChildItem Env: |
    Where-Object { $_.Name -like "TIANGONG_*" } |
    ForEach-Object {
      [Environment]::SetEnvironmentVariable($_.Name, $null, "Process")
    }

  foreach ($Name in $Snapshot.Keys) {
    $Entry = $Snapshot[$Name]
    if ($Entry.present) {
      [Environment]::SetEnvironmentVariable($Name, [string]$Entry.value, "Process")
    } else {
      [Environment]::SetEnvironmentVariable($Name, $null, "Process")
    }
  }

  foreach ($Name in $Snapshot.Keys) {
    $Expected = if ($Snapshot[$Name].present) { [string]$Snapshot[$Name].value } else { $null }
    $Actual = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ($Actual -ne $Expected) {
      throw "failed to restore process environment variable: $Name"
    }
  }
}

function Set-IsolatedEnvironment {
  param(
    [Parameter(Mandatory = $true)][hashtable]$Bindings
  )

  Get-ChildItem Env: |
    Where-Object { $_.Name -like "TIANGONG_*" } |
    ForEach-Object {
      [Environment]::SetEnvironmentVariable($_.Name, $null, "Process")
    }
  foreach ($Name in @("PORTABLE_EXECUTABLE_DIR", "ELECTRON_RUN_AS_NODE", "NODE_OPTIONS")) {
    [Environment]::SetEnvironmentVariable($Name, $null, "Process")
  }
  foreach ($Entry in $Bindings.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($Entry.Key, [string]$Entry.Value, "Process")
  }
  foreach ($Entry in $Bindings.GetEnumerator()) {
    $Actual = [Environment]::GetEnvironmentVariable($Entry.Key, "Process")
    if ($Actual -ne [string]$Entry.Value) {
      throw "failed to bind isolated process environment variable: $($Entry.Key)"
    }
  }
}

function Invoke-CdpExpression {
  param(
    [Parameter(Mandatory = $true)][string]$Expression,
    [Parameter(Mandatory = $true)][int]$Port
  )
  $env:TIANGONG_CDP_EXPRESSION = $Expression
  $Json = & node $CdpHelper "http://127.0.0.1:$Port" 2>$null
  if ($LASTEXITCODE -ne 0 -or -not $Json) {
    throw "CDP expression failed on port $Port"
  }
  return $Json | ConvertFrom-Json
}

if (-not (Test-Path -LiteralPath $CdpHelper -PathType Leaf)) {
  throw "CDP helper is missing: $CdpHelper"
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  throw "Node.js is required by the existing CDP helper"
}
if (-not (Test-Path -LiteralPath $InstalledExe -PathType Leaf)) {
  throw "InstalledExe does not exist: $InstalledExe"
}

$InstalledExePath = (Resolve-Path -LiteralPath $InstalledExe).Path
$InstalledExeItem = Get-Item -LiteralPath $InstalledExePath
if ($InstalledExeItem.Extension -ine ".exe" -or $InstalledExeItem.Name -like "Uninstall*") {
  throw "InstalledExe must be the installed Tiangong application executable, not an uninstaller"
}
$InstallRoot = $InstalledExeItem.Directory.FullName
$InstalledPayloadPresent = (
  (Test-Path -LiteralPath (Join-Path $InstallRoot "resources\app.asar") -PathType Leaf) -or
  (Test-Path -LiteralPath (Join-Path $InstallRoot "resources\app") -PathType Container)
)
if (-not $InstalledPayloadPresent) {
  throw "InstalledExe does not have an Electron resources\app.asar or resources\app payload"
}

if (-not $QaRoot) {
  $QaRoot = Join-Path (
    [IO.Path]::GetTempPath()
  ) ("TiangongInstalledCleanFirstRun-v3.0.3-" + [Guid]::NewGuid().ToString("N"))
}
if (-not [IO.Path]::IsPathRooted($QaRoot)) {
  throw "QaRoot must be an absolute path"
}
$QaRoot = [IO.Path]::GetFullPath($QaRoot).TrimEnd("\", "/")
$VolumeRoot = [IO.Path]::GetPathRoot($QaRoot).TrimEnd("\", "/")
if ($QaRoot.Equals($VolumeRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw "QaRoot cannot be a volume root"
}
if (Test-PathWithinRoot -Candidate $QaRoot -Root $InstallRoot) {
  throw "QaRoot cannot be inside the installed application directory"
}
if (Test-Path -LiteralPath $QaRoot) {
  if (-not (Test-Path -LiteralPath $QaRoot -PathType Container)) {
    throw "QaRoot exists but is not a directory"
  }
  if (Get-ChildItem -LiteralPath $QaRoot -Force | Select-Object -First 1) {
    throw "QaRoot must be absent or empty for a clean first-run simulation"
  }
} else {
  New-Item -ItemType Directory -Path $QaRoot -Force | Out-Null
}

$ProfileRoot = Join-Path $QaRoot "Profile"
$RoamingRoot = Join-Path $QaRoot "AppData\Roaming"
$LocalRoot = Join-Path $QaRoot "AppData\Local"
$TempRoot = Join-Path $QaRoot "Temp"
$ElectronUserDataRoot = Join-Path $RoamingRoot "tiangong-v3-qiyuan"
$RuntimeRoot = Join-Path $QaRoot "Runtime"
$RuntimeStateRoot = Join-Path $RuntimeRoot "state"
$QaWorkspaceRoot = Join-Path $ProfileRoot "Documents\TiangongWorkspace"
$LifeDataRoot = Join-Path $ProfileRoot "Documents\TiangongLifeData"
$KnownFolders = [ordered]@{
  TIANGONG_HOME_PATH = $ProfileRoot
  TIANGONG_DOCUMENTS_PATH = (Join-Path $ProfileRoot "Documents")
  TIANGONG_DESKTOP_PATH = (Join-Path $ProfileRoot "Desktop")
  TIANGONG_DOWNLOADS_PATH = (Join-Path $ProfileRoot "Downloads")
  TIANGONG_PICTURES_PATH = (Join-Path $ProfileRoot "Pictures")
  TIANGONG_VIDEOS_PATH = (Join-Path $ProfileRoot "Videos")
  TIANGONG_MUSIC_PATH = (Join-Path $ProfileRoot "Music")
}
$Directories = @(
  $ProfileRoot,
  $RoamingRoot,
  $LocalRoot,
  $TempRoot,
  $ElectronUserDataRoot,
  $RuntimeRoot,
  $RuntimeStateRoot,
  $QaWorkspaceRoot,
  $LifeDataRoot
) + @($KnownFolders.Values)
$Directories | Sort-Object -Unique | ForEach-Object {
  New-Item -ItemType Directory -Path $_ -Force | Out-Null
}

$ExistingInstallProcesses = @(Get-InstallationProcesses -InstallRoot $InstallRoot)
if ($ExistingInstallProcesses.Count -gt 0) {
  $ProcessIds = @($ExistingInstallProcesses | Select-Object -ExpandProperty ProcessId)
  throw ("installed Tiangong is already running; refusing to disturb it: " + ($ProcessIds -join ","))
}
$ExistingListeners = @(Get-ListeningConnections -Ports $QaPorts)
if ($ExistingListeners.Count -gt 0) {
  $Summary = @(
    $ExistingListeners | ForEach-Object { "$($_.LocalPort)/pid=$($_.OwningProcess)" }
  )
  throw ("QA ports are already occupied; refusing to disturb another process: " + ($Summary -join ","))
}

$EnvironmentSnapshot = Capture-QAEnvironment
$DesktopProcess = $null
$LaunchStartedUtc = [DateTime]::UtcNow
$PrimaryFailure = $null
$CleanupFailure = $null
$RestoreFailure = $null
$Result = $null

try {
  $DesktopToken = (
    "qa-installed-" +
    [Guid]::NewGuid().ToString("N") +
    [Guid]::NewGuid().ToString("N")
  )
  $EnvironmentBindings = [ordered]@{
    APPDATA = $RoamingRoot
    LOCALAPPDATA = $LocalRoot
    USERPROFILE = $ProfileRoot
    HOME = $ProfileRoot
    TEMP = $TempRoot
    TMP = $TempRoot
    TIANGONG_HOME_PATH = $ProfileRoot
    TIANGONG_DOCUMENTS_PATH = $KnownFolders.TIANGONG_DOCUMENTS_PATH
    TIANGONG_DESKTOP_PATH = $KnownFolders.TIANGONG_DESKTOP_PATH
    TIANGONG_DOWNLOADS_PATH = $KnownFolders.TIANGONG_DOWNLOADS_PATH
    TIANGONG_PICTURES_PATH = $KnownFolders.TIANGONG_PICTURES_PATH
    TIANGONG_VIDEOS_PATH = $KnownFolders.TIANGONG_VIDEOS_PATH
    TIANGONG_MUSIC_PATH = $KnownFolders.TIANGONG_MUSIC_PATH
    TIANGONG_DESKTOP_RUNTIME_ROOT = $RuntimeRoot
    TIANGONG_DESKTOP_STATE_DIR = $RuntimeStateRoot
    TIANGONG_RUN_STATE_DIR = (Join-Path $RuntimeRoot "run")
    TIANGONG_V3_STATE_DIR = (Join-Path $RuntimeStateRoot "v3")
    TIANGONG_DESKTOP_WORKSPACE_ROOT = $QaWorkspaceRoot
    TIANGONG_WORKSPACE_ROOT = $QaWorkspaceRoot
    TIANGONG_FORCE_WORKSPACE_ROOT = $QaWorkspaceRoot
    TIANGONG_OMNI_BODY_WORKSPACE = $QaWorkspaceRoot
    TIANGONG_LIFE_DATA_ROOT = $LifeDataRoot
    TIANGONG_LIFE_RUNTIME_ROOT = (Join-Path $RuntimeRoot "complete-life")
    TIANGONG_LIFE_KERNEL_ROOT = (Join-Path $RuntimeStateRoot "life_kernel")
    TIANGONG_LIFE_ROOT = (Join-Path $RuntimeStateRoot "life_transaction")
    TIANGONG_EXECUTION_RUNTIME_ROOT = (Join-Path $RuntimeStateRoot "life_kernel")
    TIANGONG_EXECUTION_LIFE_ROOT = (Join-Path $RuntimeStateRoot "life_transaction")
    TIANGONG_DESKTOP_TOKEN = $DesktopToken
  }
  Set-IsolatedEnvironment -Bindings $EnvironmentBindings

  $LaunchStartedUtc = [DateTime]::UtcNow
  $DesktopProcess = Start-Process `
    -FilePath $InstalledExePath `
    -ArgumentList @(
      ('--user-data-dir="' + $ElectronUserDataRoot + '"'),
      "--remote-debugging-port=$DebugPort",
      "--force-device-scale-factor=1"
    ) `
    -WorkingDirectory $InstallRoot `
    -PassThru `
    -WindowStyle Hidden

  # Keep this script source ASCII-only so Windows PowerShell 5.1 and PowerShell
  # 7.x pass the same JavaScript expression to the CDP helper.
  $ProbeExpression = @'
(async () => {
  const visible = (node) => {
    if (!node) return false;
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none'
      && style.visibility !== 'hidden'
      && Number(style.opacity) !== 0
      && rect.width > 0
      && rect.height > 0;
  };
  const slotNames = ['nav', 'context', 'conversation', 'inspector'];
  const slots = Object.fromEntries(slotNames.map((name) => {
    const node = document.querySelector(`[data-slot="${name}"]`);
    const record = {
      present: Boolean(node),
      visible: visible(node),
      childElementCount: node?.childElementCount || 0,
      textLength: String(node?.innerText || '').trim().length,
    };
    record.rendered = record.present && record.visible && record.childElementCount > 0;
    return [name, record];
  }));
  const fatal = document.querySelector('[data-ti-fatal]');
  const titlebar = document.querySelector('.desktop-titlebar-brand');
  const shell = document.querySelector('.app-shell[data-plugin-host]');
  const avatarPanel = document.querySelector('[data-avatar-panel="chat"]');
  const avatarCanvas = avatarPanel?.querySelector('canvas');
  const avatarEmptyState = avatarPanel?.querySelector('[data-avatar-empty-state]');
  const avatarRoleName = avatarPanel?.querySelector('[data-avatar-role-name]');
  const avatarModelSelect = document.querySelector(
    '[data-avatar-panel="body"] [data-avatar-model-select]'
  );
  const avatarImportStatus = document.querySelector(
    '[data-avatar-panel="body"] [data-avatar-import-status]'
  );
  const avatarModelOptions = avatarModelSelect
    ? [...avatarModelSelect.options].filter((option) => option.value)
    : [];
  let avatarWebgl = false;
  try {
    avatarWebgl = Boolean(
      avatarCanvas?.getContext?.('webgl2')
      || avatarCanvas?.getContext?.('webgl')
    );
  } catch {}
  const avatar = {
    panelPresent: Boolean(avatarPanel),
    canvasPresent: Boolean(avatarCanvas),
    canvasVisible: visible(avatarCanvas),
    webgl: avatarWebgl,
    roleName: String(avatarRoleName?.textContent || '').trim(),
    catalogModelCount: avatarModelOptions.length,
    modelSelectDisabled: Boolean(avatarModelSelect?.disabled),
    emptyStateVisible: visible(avatarEmptyState),
    emptyStateText: String(avatarEmptyState?.innerText || '').trim(),
    importStatus: String(avatarImportStatus?.textContent || '').trim(),
    importState: String(avatarImportStatus?.dataset?.state || ''),
  };
  avatar.rawCancellationCodeAbsent = !avatar.importStatus.includes('user_cancelled');
  avatar.emptyCatalogReady = avatar.catalogModelCount !== 0 || (
    avatar.modelSelectDisabled
    && avatar.emptyStateVisible
    && avatar.emptyStateText.includes('\u5C1A\u672A\u6DFB\u52A0\u8EAB\u4F53\u6A21\u578B')
  );
  avatar.ok = avatar.panelPresent
    && avatar.canvasPresent
    && avatar.canvasVisible
    && avatar.webgl
    && avatar.roleName.length > 0
    && avatar.roleName !== '\u2014'
    && avatar.rawCancellationCodeAbsent
    && avatar.emptyCatalogReady;
  const avatarBootError = window.__avatarBootError == null
    ? null
    : String(window.__avatarBootError);
  const ui = {
    ready: document.documentElement.dataset.tiangongReady || null,
    coreLoaded: document.documentElement.dataset.tiangongCoreLoaded || null,
    titlebarVisible: visible(titlebar) && String(titlebar?.innerText || '').trim().length > 0,
    shellVisible: visible(shell),
    slots,
    fatalPresent: Boolean(fatal),
    fatalVisible: visible(fatal),
    fatalText: String(fatal?.innerText || '').slice(0, 500),
    avatarBootSettled: Number.isFinite(window.__bootSettledAt),
    avatarBootError,
    avatar,
    localStorageKeys: Object.keys(window.localStorage || {}).sort(),
  };
  ui.ok = ui.ready === 'true'
    && ui.coreLoaded === 'true'
    && ui.titlebarVisible
    && ui.shellVisible
    && Object.values(slots).every((slot) => slot.rendered)
    && !ui.fatalPresent
    && ui.avatarBootSettled
    && ui.avatarBootError === null
    && ui.avatar.ok;

  const fetchJson = async (base, headers, path) => {
    try {
      const response = await fetch(base + path, { headers });
      let body = null;
      try { body = await response.json(); } catch {}
      return { httpStatus: response.status, body };
    } catch (error) {
      return {
        httpStatus: 0,
        body: null,
        error: String(error?.message || error),
      };
    }
  };
  let gateway = {
    ok: false,
    url: null,
    port: null,
    health: { httpStatus: 0, body: null, error: 'desktop bridge unavailable' },
    ready: { httpStatus: 0, body: null, error: 'desktop bridge unavailable' },
  };
  try {
    const url = window.tiangongDesktop.getGatewayUrl();
    const headers = window.tiangongDesktop.getGatewayHeaders();
    gateway.url = url;
    gateway.port = new URL(url).port;
    gateway.health = await fetchJson(url, headers, '/health');
    gateway.ready = await fetchJson(url, headers, '/ready');
    gateway.ok = gateway.port === '7184'
      && gateway.health.httpStatus === 200
      && gateway.health.body?.status === 'ALIVE'
      && gateway.ready.httpStatus === 200
      && gateway.ready.body?.status === 'READY';
  } catch (error) {
    gateway.error = String(error?.message || error);
  }
  return { ok: ui.ok && gateway.ok, ui, gateway };
})()
'@

  $Probe = $null
  $LastProbeError = ""
  $ProbeDeadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    if ($DesktopProcess.HasExited) {
      throw "installed Electron exited early with code $($DesktopProcess.ExitCode)"
    }
    try {
      $Probe = Invoke-CdpExpression -Expression $ProbeExpression -Port $DebugPort
      if ($Probe.ok -eq $true) { break }
      $LastProbeError = $Probe | ConvertTo-Json -Depth 10 -Compress
    } catch {
      $LastProbeError = $_.Exception.Message
    }
    Start-Sleep -Milliseconds 1000
  } while ([DateTime]::UtcNow -lt $ProbeDeadline)

  if (-not $Probe -or $Probe.ok -ne $true) {
    throw "installed clean first-run readiness timed out: $LastProbeError"
  }

  Start-Sleep -Milliseconds 1000
  $DiagnosticPath = Join-Path $RuntimeRoot "logs\desktop_renderer.jsonl"
  if (-not (Test-Path -LiteralPath $DiagnosticPath -PathType Leaf)) {
    throw "isolated renderer diagnostic was not created: $DiagnosticPath"
  }
  $DiagnosticText = Get-Content -LiteralPath $DiagnosticPath -Raw -Encoding UTF8
  $FatalDiagnosticPattern = '"kind":"(?:did-fail-load|render-process-gone|load-frontend-rejected|renderer-ready-marker-missing|renderer-ready-probe-failed|window-unresponsive|avatar-boot-failed)"'
  $FatalDiagnosticCount = [regex]::Matches($DiagnosticText, $FatalDiagnosticPattern).Count
  $FrontendLoaded = $DiagnosticText -match '"kind":"frontend-load-complete-ms"'
  if (-not $FrontendLoaded -or $FatalDiagnosticCount -ne 0) {
    throw "installed renderer diagnostics contain a fatal result or no completed frontend load"
  }

  $LegacyListeners = @(
    Get-ListeningConnections -Ports @(7174, 7175, 7176)
  )
  $GatewayListeners = @(
    Get-ListeningConnections -Ports @(7184)
  )
  if ($LegacyListeners.Count -ne 0 -or $GatewayListeners.Count -lt 1) {
    throw "installed port topology is invalid after readiness"
  }

  $Result = [ordered]@{
    schema = "tiangong.qa.installed-clean-first-run.v1"
    ok = $true
    installed_exe = $InstalledExePath
    install_root = $InstallRoot
    qa_root = $QaRoot
    isolated_profile_root = $ProfileRoot
    isolated_appdata_roaming = $RoamingRoot
    isolated_appdata_local = $LocalRoot
    isolated_temp_root = $TempRoot
    isolated_user_data_root = $ElectronUserDataRoot
    isolated_runtime_root = $RuntimeRoot
    isolated_workspace_root = $QaWorkspaceRoot
    isolated_life_data_root = $LifeDataRoot
    ui_ready = ($Probe.ui.ready -eq "true")
    ui_core_loaded = ($Probe.ui.coreLoaded -eq "true")
    ui_titlebar_visible = [bool]$Probe.ui.titlebarVisible
    ui_shell_visible = [bool]$Probe.ui.shellVisible
    ui_slots = $Probe.ui.slots
    fatal_dom_present = [bool]$Probe.ui.fatalPresent
    avatar_boot_settled = [bool]$Probe.ui.avatarBootSettled
    avatar_boot_error = $Probe.ui.avatarBootError
    avatar_panel = $Probe.ui.avatar
    gateway_url = [string]$Probe.gateway.url
    gateway_health_http_status = [int]$Probe.gateway.health.httpStatus
    gateway_health_status = [string]$Probe.gateway.health.body.status
    gateway_ready_http_status = [int]$Probe.gateway.ready.httpStatus
    gateway_ready_status = [string]$Probe.gateway.ready.body.status
    gateway_listener_process_ids = @(
      $GatewayListeners | Select-Object -ExpandProperty OwningProcess -Unique
    )
    legacy_ports_closed = ($LegacyListeners.Count -eq 0)
    frontend_load_diagnostic_present = $FrontendLoaded
    fatal_renderer_diagnostics = $FatalDiagnosticCount
    diagnostic_path = $DiagnosticPath
    local_storage_keys = @($Probe.ui.localStorageKeys)
    process_cleanup_complete = $false
    process_environment_restored = $false
  }
} catch {
  $PrimaryFailure = $_
} finally {
  try {
    Stop-QAInstalledProcesses `
      -DesktopProcess $DesktopProcess `
      -InstallRoot $InstallRoot `
      -LaunchStartedUtc $LaunchStartedUtc `
      -Ports $QaPorts
  } catch {
    $CleanupFailure = $_
  }
  try {
    Restore-QAEnvironment -Snapshot $EnvironmentSnapshot
  } catch {
    $RestoreFailure = $_
  }
}

if ($PrimaryFailure) {
  if ($CleanupFailure) {
    Write-Warning ("cleanup also failed: " + $CleanupFailure.Exception.Message)
  }
  if ($RestoreFailure) {
    Write-Warning ("environment restore also failed: " + $RestoreFailure.Exception.Message)
  }
  throw $PrimaryFailure
}
if ($CleanupFailure) { throw $CleanupFailure }
if ($RestoreFailure) { throw $RestoreFailure }

$Result.process_cleanup_complete = $true
$Result.process_environment_restored = $true
[pscustomobject]$Result | ConvertTo-Json -Depth 10
