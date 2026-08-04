[CmdletBinding()]
param(
  [string]$ArtifactRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Package = Get-Content -LiteralPath (Join-Path $Root "app\package.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$ProductVersion = [string]$Package.version
if ($ProductVersion -notmatch '^\d+\.\d+\.\d+$') { throw "Package version must be a numeric SemVer triplet" }
if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
  $ArtifactRoot = Join-Path $Root ("release-artifacts\{0}\win32-x64" -f $ProductVersion)
}
$ArtifactRoot = (Resolve-Path -LiteralPath $ArtifactRoot).Path
$DeveloperName = [string]$Package.author.name
$ProductName = [string]$Package.productName
if ([string]::IsNullOrWhiteSpace($DeveloperName)) { throw "Package author name is required" }
if ([string]::IsNullOrWhiteSpace($ProductName)) { throw "Package product name is required" }

$RequiredNames = @("LICENSE.txt", "release-manifest.json", "release-provenance.json", "SHA256SUMS.txt")
foreach ($Name in $RequiredNames) {
  if (-not (Test-Path -LiteralPath (Join-Path $ArtifactRoot $Name) -PathType Leaf)) {
    throw "Missing release artifact: $Name"
  }
}
if (Get-ChildItem -LiteralPath $ArtifactRoot -File | Where-Object Name -Like "builder-*") {
  throw "electron-builder diagnostic files leaked into the release directory"
}

$ChecksumRows = @(Get-Content -LiteralPath (Join-Path $ArtifactRoot "SHA256SUMS.txt") -Encoding UTF8)
$VerifiedHashes = 0
foreach ($Row in $ChecksumRows) {
  if ($Row -notmatch "^([A-F0-9]{64})  (.+)$") { throw "Malformed SHA256SUMS row" }
  $Expected = $Matches[1]
  $Relative = $Matches[2].Replace("/", [IO.Path]::DirectorySeparatorChar)
  $Candidate = [IO.Path]::GetFullPath((Join-Path $ArtifactRoot $Relative))
  if (-not $Candidate.StartsWith($ArtifactRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Checksum path escaped release root"
  }
  if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { throw "Checksummed file is missing: $Relative" }
  $Actual = (Get-FileHash -LiteralPath $Candidate -Algorithm SHA256).Hash
  if ($Actual -ne $Expected) { throw "Checksum mismatch: $Relative" }
  $VerifiedHashes += 1
}

$Installers = @(Get-ChildItem -LiteralPath $ArtifactRoot -Filter "*.exe" -File)
if ($Installers.Count -ne 1) { throw "Release directory must contain exactly one installer EXE" }
$Installer = $Installers[0]
if ($Installer.Length -lt 100MB) { throw "Installer is unexpectedly small" }
$Provenance = Get-Content -LiteralPath (Join-Path $ArtifactRoot "release-provenance.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$Signature = Get-AuthenticodeSignature -LiteralPath $Installer.FullName
if ($Provenance.signing -eq "signed-production") {
  if ($Signature.Status -ne "Valid") { throw "Signed production installer does not have a valid signature" }
} elseif ($Provenance.signing -eq "unsigned-candidate") {
  if ($Signature.Status -ne "NotSigned") { throw "Unsigned candidate signature state is inconsistent" }
} else {
  throw "Unknown release signing mode"
}
if ($Installer.VersionInfo.CompanyName -ne $DeveloperName) { throw "Installer developer information is incorrect" }
if ($Installer.VersionInfo.ProductName -ne $ProductName) { throw "Installer product name is incorrect" }
if ($Installer.VersionInfo.FileVersion -ne $ProductVersion) { throw "Installer file version is incorrect" }

$Manifest = Get-Content -LiteralPath (Join-Path $ArtifactRoot "release-manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $Manifest.production_claim -or $Manifest.release_channel -ne "stable") {
  throw "Release manifest is not a stable production claim"
}
if (@($Manifest.component_manifest.components).Count -ne 5) { throw "Release manifest must bind five logical components" }
$DesktopComponents = @($Manifest.component_manifest.components | Where-Object component_id -eq "tiangong-desktop")
if ($DesktopComponents.Count -ne 1 -or [string]$DesktopComponents[0].executable_relative_path -ne "app.asar") {
  throw "Release manifest must bind the complete desktop app.asar"
}
$DesktopComponent = $DesktopComponents[0]
$RuntimePaths = @($Manifest.component_manifest.components | Where-Object component_id -ne "tiangong-desktop" | ForEach-Object executable_relative_path | Select-Object -Unique)
if ($RuntimePaths.Count -ne 1 -or $RuntimePaths[0] -ne "total-gateway/tiangong-total-gateway.exe") {
  throw "All logical backend modules must bind to the single 7184 executable"
}

$SevenZip = Join-Path $Root "app\node_modules\7zip-bin\win\x64\7za.exe"
if (-not (Test-Path -LiteralPath $SevenZip -PathType Leaf)) {
  $SevenZipCommand = Get-Command 7z, 7za -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($SevenZipCommand) {
    $SevenZip = $SevenZipCommand.Source
  } else {
    $BuilderCache = Join-Path $env:LOCALAPPDATA "electron-builder\Cache"
    $CachedSevenZip = Get-ChildItem -LiteralPath $BuilderCache -Recurse -Filter "7za.exe" -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($CachedSevenZip) { $SevenZip = $CachedSevenZip.FullName }
  }
}
if (-not (Test-Path -LiteralPath $SevenZip -PathType Leaf)) { throw "7-Zip verifier is missing" }
$ArchiveTest = (& $SevenZip t $Installer.FullName 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $ArchiveTest -notmatch "Everything is Ok") {
  throw "Installer archive integrity check failed"
}

function Read-LastJsonObject {
  param([string[]]$Lines)
  for ($Index = $Lines.Count - 1; $Index -ge 0; $Index -= 1) {
    $Line = [string]$Lines[$Index]
    if ([string]::IsNullOrWhiteSpace($Line)) { continue }
    try { return ($Line | ConvertFrom-Json -ErrorAction Stop) } catch {}
  }
  throw "Release probe did not return a JSON object"
}

$ExtractionRoot = Join-Path ([IO.Path]::GetTempPath()) ("TiangongNsisVerify-" + [Guid]::NewGuid().ToString("N"))
$TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$ExtractionRoot = [IO.Path]::GetFullPath($ExtractionRoot)
if (-not $ExtractionRoot.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase)) {
  throw "NSIS verification root escaped the system temp directory"
}
$PayloadComponents = 0
$PayloadFiles = 0
$PayloadAppAsarHash = ""
$PayloadAvatarModuleClosure = 0
$PayloadForbiddenAvatarAssetsAbsent = 0
$PayloadMaxRelativePath = 0
$PayloadMaxDefaultInstallPath = 0
try {
  New-Item -ItemType Directory -Path $ExtractionRoot | Out-Null
  $ExtractOutput = (& $SevenZip x -y ("-o" + $ExtractionRoot) $Installer.FullName 2>&1 | Out-String)
  if ($LASTEXITCODE -ne 0 -or $ExtractOutput -notmatch "Everything is Ok") {
    throw "Installer payload extraction failed"
  }
  $Resources = Join-Path $ExtractionRoot "resources"
  if (-not (Test-Path -LiteralPath $Resources -PathType Container)) {
    throw "Extracted NSIS payload does not contain resources"
  }
  $PayloadManifest = Join-Path $Resources "release\release-manifest.json"
  if (-not (Test-Path -LiteralPath $PayloadManifest -PathType Leaf)) {
    throw "Extracted NSIS payload is missing its release manifest"
  }
  $ArtifactManifest = Join-Path $ArtifactRoot "release-manifest.json"
  if (
    (Get-FileHash -LiteralPath $PayloadManifest -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath $ArtifactManifest -Algorithm SHA256).Hash
  ) {
    throw "Artifact and NSIS payload release manifests differ"
  }
  $BindingModule = Join-Path $Root "app\lib\release-binding.js"
  $BindingScript = @'
const binding = require(process.argv[1]);
const verified = binding.readVerifiedReleaseBinding(process.argv[2]);
if (!verified) process.exit(2);
process.stdout.write(JSON.stringify({
  desktopPath: verified.desktopPath,
  desktopSha256: verified.desktopSha256,
  manifestSha256: verified.releaseManifestSha256
}));
'@
  $BindingJson = (& node -e $BindingScript $BindingModule $PayloadManifest 2>&1 | Out-String)
  if ($LASTEXITCODE -ne 0) {
    throw "Extracted payload failed the runtime release-binding verifier"
  }
  $BindingResult = $BindingJson | ConvertFrom-Json
  if (Get-ChildItem -LiteralPath $ExtractionRoot -Recurse -Attributes ReparsePoint -ErrorAction SilentlyContinue) {
    throw "Extracted NSIS payload contains a symbolic link or reparse point"
  }
  $PayloadFiles = @(Get-ChildItem -LiteralPath $ExtractionRoot -Recurse -File).Count
  if ($PayloadFiles -lt 1000) { throw "Extracted NSIS payload file inventory is unexpectedly small" }
  $SeenWindowsPaths = @{}
  $ReservedWindowsName = '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?\z'
  $DefaultInstallRoot = Join-Path $env:SystemDrive ("Program Files\" + $ProductName)
  foreach ($PayloadFile in Get-ChildItem -LiteralPath $ExtractionRoot -Recurse -File -Force) {
    $Relative = $PayloadFile.FullName.Substring($ExtractionRoot.Length + 1)
    $WindowsKey = $Relative.ToUpperInvariant()
    if ($SeenWindowsPaths.ContainsKey($WindowsKey)) {
      throw "NSIS payload has a case-insensitive path collision: $Relative"
    }
    $SeenWindowsPaths[$WindowsKey] = $true
    foreach ($Segment in $Relative.Split([IO.Path]::DirectorySeparatorChar)) {
      if ($Segment.EndsWith('.') -or $Segment.EndsWith(' ') -or $Segment -match $ReservedWindowsName) {
        throw "NSIS payload has an invalid Windows path segment: $Relative"
      }
      if ($Segment.ToLowerInvariant() -in @(
        ".omni_audit",
        ".omni_backups",
        ".tiangong",
        ".pytest_cache",
        "__pycache__",
        "browser_snapshots"
      )) {
        throw "NSIS payload contains runtime residue directory: $Relative"
      }
    }
    $FoldedName = $PayloadFile.Name.ToLowerInvariant()
    if (
      $FoldedName.EndsWith(".log") -or
      $FoldedName.EndsWith(".lock") -or
      $FoldedName.EndsWith(".tmp") -or
      $FoldedName -eq "desktop_renderer.jsonl" -or
      $FoldedName -match '\.bak(?:[-._0-9].*)?\z'
    ) {
      throw "NSIS payload contains runtime residue file: $Relative"
    }
    $PayloadMaxRelativePath = [Math]::Max($PayloadMaxRelativePath, $Relative.Length)
    $PayloadMaxDefaultInstallPath = [Math]::Max(
      $PayloadMaxDefaultInstallPath,
      $DefaultInstallRoot.Length + 1 + $Relative.Length
    )
  }
  if ($PayloadMaxDefaultInstallPath -ge 240) {
    throw "NSIS payload exceeds the conservative Windows install-path budget"
  }

  foreach ($Component in @($Manifest.component_manifest.components)) {
    if ($Component.component_id -eq "tiangong-desktop") {
      if ([string]$Component.executable_relative_path -ne "app.asar") {
        throw "Desktop component must bind the complete app.asar"
      }
    }
    $Relative = ([string]$Component.executable_relative_path).Replace("/", [IO.Path]::DirectorySeparatorChar)
    $Candidate = [IO.Path]::GetFullPath((Join-Path $Resources $Relative))
    if (-not $Candidate.StartsWith($Resources + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
      throw "Component path escaped the extracted resources root"
    }
    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
      throw "Extracted component is missing: $($Component.component_id)"
    }
    $Hash = (Get-FileHash -LiteralPath $Candidate -Algorithm SHA256).Hash
    $Bytes = (Get-Item -LiteralPath $Candidate).Length
    if ($Hash -ne [string]$Component.sha256 -or $Bytes -ne [long]$Component.size_bytes) {
      throw "Extracted component changed during NSIS compression: $($Component.component_id)"
    }
    $PayloadComponents += 1
  }

  $OverlayBase = Join-Path $Resources "total-gateway\backend\tiangong-backend"
  foreach ($Relative in @(
    "v3\permission_settings.py",
    "_internal\frozen_modules\v3\execution_kernel\confirmation_bridge.py"
  )) {
    if (-not (Test-Path -LiteralPath (Join-Path $OverlayBase $Relative) -PathType Leaf)) {
      throw "A5 confirmation overlay was lost during NSIS compression: $Relative"
    }
  }

  $PayloadAppAsar = Join-Path $Resources "app.asar"
  if (-not (Test-Path -LiteralPath $PayloadAppAsar -PathType Leaf)) {
    throw "Extracted NSIS payload is missing app.asar"
  }
  $PayloadAppAsarHash = (Get-FileHash -LiteralPath $PayloadAppAsar -Algorithm SHA256).Hash
  if (
    [string]$BindingResult.desktopSha256 -ne $PayloadAppAsarHash -or
    [IO.Path]::GetFullPath([string]$BindingResult.desktopPath) -ne
    [IO.Path]::GetFullPath($PayloadAppAsar)
  ) {
    throw "Runtime release binding does not authorize the extracted app.asar"
  }
  $AvatarAsarVerifier = Join-Path $Root "scripts\verify-app-asar-avatar-contract.mjs"
  if (-not (Test-Path -LiteralPath $AvatarAsarVerifier -PathType Leaf)) {
    throw "Avatar app.asar contract verifier is missing"
  }
  $AvatarAsarJson = (& node $AvatarAsarVerifier $PayloadAppAsar 2>&1 | Out-String)
  if ($LASTEXITCODE -ne 0) {
    throw "Extracted app.asar failed avatar module and redistribution contract: $AvatarAsarJson"
  }
  $AvatarAsarResult = $AvatarAsarJson | ConvertFrom-Json
  if ($AvatarAsarResult.ok -ne $true) {
    throw "Extracted app.asar avatar contract did not return success"
  }
  $PayloadAvatarModuleClosure = [int]$AvatarAsarResult.requiredModuleCount
  $PayloadForbiddenAvatarAssetsAbsent = [int]$AvatarAsarResult.forbiddenAssetCount
  $StageRoot = if ([string]::IsNullOrWhiteSpace($env:TIANGONG_RELEASE_STAGE)) {
    Join-Path $Root "release-stage\win32-x64"
  } else {
    [IO.Path]::GetFullPath($env:TIANGONG_RELEASE_STAGE)
  }
  $StagedAppAsar = Join-Path $StageRoot "electron-builder\win-unpacked\resources\app.asar"
  if (Test-Path -LiteralPath $StagedAppAsar -PathType Leaf) {
    $StagedHash = (Get-FileHash -LiteralPath $StagedAppAsar -Algorithm SHA256).Hash
    if ($StagedHash -ne $PayloadAppAsarHash) {
      throw "app.asar changed between the unpacked build and NSIS payload"
    }
  }

  $ProbePath = Join-Path $Resources "total-gateway\tiangong-total-gateway.exe"
  $ProbeLines = @(& $ProbePath --release-probe 2>&1)
  if ($LASTEXITCODE -ne 0) { throw "Extracted single-process release probe failed" }
  $ProbeResult = Read-LastJsonObject -Lines $ProbeLines
  if (
    $ProbeResult.ok -ne $true -or
    $ProbeResult.component_id -ne "tiangong-total-gateway" -or
    $ProbeResult.deployment_mode -ne "embedded" -or
    [int]$ProbeResult.listener_port -ne 7184 -or
    $ProbeResult.life_api_contract -ne "tiangong.life.api.v2" -or
    $ProbeResult.communication_api_contract -ne "tiangong.communication.api.v1"
  ) {
    throw "Extracted single-process release probe contract mismatch"
  }
  if (Get-ChildItem -LiteralPath $ExtractionRoot -Recurse -Directory -Filter "__pycache__" -Force -ErrorAction SilentlyContinue) {
    throw "NSIS verification mutated the extracted payload with Python caches"
  }
} finally {
  if (Test-Path -LiteralPath $ExtractionRoot) {
    Remove-Item -LiteralPath $ExtractionRoot -Recurse -Force
  }
}

[pscustomobject][ordered]@{
  Status = "passed"
  Installer = $Installer.FullName
  InstallerBytes = $Installer.Length
  InstallerSHA256 = (Get-FileHash -LiteralPath $Installer.FullName -Algorithm SHA256).Hash
  Signature = [string]$Signature.Status
  ProductName = $Installer.VersionInfo.ProductName
  Developer = $Installer.VersionInfo.CompanyName
  FileVersion = $Installer.VersionInfo.FileVersion
  VerifiedHashes = $VerifiedHashes
  ManifestComponents = @($Manifest.component_manifest.components).Count
  ManifestDesktopSHA256 = [string]$DesktopComponent.sha256
  NsisPayloadFiles = $PayloadFiles
  NsisMaxRelativePath = $PayloadMaxRelativePath
  NsisMaxDefaultInstallPath = $PayloadMaxDefaultInstallPath
  NsisPayloadComponents = $PayloadComponents
  NsisAppAsarSHA256 = $PayloadAppAsarHash
  NsisAvatarModuleClosure = $PayloadAvatarModuleClosure
  NsisForbiddenAvatarAssetsAbsent = $PayloadForbiddenAvatarAssetsAbsent
  QrReleaseProbe = $Provenance.qr_release_probe
}
