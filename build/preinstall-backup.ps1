[CmdletBinding()]
param(
    [string]$BackupRoot = "",
    [string]$CandidateManifestPath = "",
    [string]$InstallDirectory = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Resolve-SafeAbsolutePath {
    param(
        [Parameter(Mandatory = $true)][string]$PathValue,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not [IO.Path]::IsPathRooted($PathValue)) {
        throw "$Label must be an absolute path"
    }
    return [IO.Path]::GetFullPath($PathValue).TrimEnd("\")
}

function Assert-ChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Child
    )
    $parentFull = (Resolve-SafeAbsolutePath -PathValue $Parent -Label "backup root") + "\"
    $childFull = Resolve-SafeAbsolutePath -PathValue $Child -Label "backup child"
    if (-not $childFull.StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "backup child escaped the recovery root"
    }
    return $childFull
}

function Get-RelativeChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Child
    )
    # The installer intentionally invokes the inbox Windows PowerShell 5.1.
    # [IO.Path]::GetRelativePath is a newer .NET API and is unavailable there,
    # so derive the already-validated child path without depending on pwsh.
    $parentFull = (Resolve-SafeAbsolutePath -PathValue $Parent -Label "relative path root") + "\"
    $childFull = Resolve-SafeAbsolutePath -PathValue $Child -Label "relative path child"
    if (-not $childFull.StartsWith($parentFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "relative path child escaped its root"
    }
    return $childFull.Substring($parentFull.Length).Replace("\", "/")
}

function Get-Sha256Hex {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath
    )
    $stream = $null
    $hasher = $null
    try {
        $stream = [IO.File]::OpenRead($LiteralPath)
        $hasher = [Security.Cryptography.SHA256]::Create()
        return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace("-", "").ToLowerInvariant()
    }
    finally {
        if ($hasher) {
            $hasher.Dispose()
        }
        if ($stream) {
            $stream.Dispose()
        }
    }
}

function Get-DefaultCandidates {
    $documents = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments)
    # Keep this installer script ASCII/CRLF so inbox Windows PowerShell 5.1
    # parses it correctly while the cross-platform source gate remains clean.
    $packagedProfileName = [Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String("5aSp5bel6YCg54mpIHYzLjAuMyDlrozmlbTniYg=")
    )
    $lifeDataName = [Text.Encoding]::UTF8.GetString(
        [Convert]::FromBase64String("5aSp5bel6YCg54mp55Sf5ZG95pWw5o2u")
    )
    return @(
        [pscustomobject]@{
            id = "packaged-versioned-profile"
            path = Join-Path $env:APPDATA $packagedProfileName
        },
        [pscustomobject]@{
            id = "packaged-stable-profile"
            path = Join-Path $env:APPDATA "tiangong-v3-qiyuan"
        },
        [pscustomobject]@{
            id = "life-data"
            path = Join-Path $documents $lifeDataName
        }
    )
}

function Read-Candidates {
    if (-not $CandidateManifestPath) {
        return @(Get-DefaultCandidates)
    }
    $manifestPath = Resolve-SafeAbsolutePath -PathValue $CandidateManifestPath -Label "candidate manifest"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "candidate manifest does not exist"
    }
    $parsed = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($parsed -isnot [Array]) {
        $parsed = @($parsed)
    }
    return @($parsed)
}

function Copy-VerifiedTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    # P2-22: checkpoint SQLite WAL files before copying when the embedded
    # runtime is available; a torn WAL is worse than no backup.
    $checkpointPython = [string]$env:TIANGONG_RELEASE_PYTHON
    if ($checkpointPython -and (Test-Path -LiteralPath $checkpointPython)) {
        foreach ($db in @(
            Get-ChildItem -LiteralPath $Source -Recurse -File -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.Extension -in @(".sqlite", ".sqlite3") }
        )) {
            & $checkpointPython -c "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.close()" $db.FullName 2>$null
        }
    }
    & robocopy.exe $Source $Destination /E /COPY:DAT /DCOPY:DAT /XJ /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    $robocopyCode = $LASTEXITCODE
    if ($robocopyCode -ge 8) {
        throw "robocopy failed with exit code $robocopyCode"
    }
    # P2-22: source/destination equivalence — every source file must exist in
    # the backup with identical bytes and hash, not just an enumerated target.
    $sourceRoot = (Resolve-SafeAbsolutePath -PathValue $Source -Label "backup source") + "\"
    $sourceFiles = @(
        Get-ChildItem -LiteralPath $Source -Recurse -File -Force |
            Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) }
    )
    $destMap = @{}
    foreach ($file in @(
        Get-ChildItem -LiteralPath $Destination -Recurse -File -Force |
            Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) }
    )) {
        $destMap[[IO.Path]::GetFullPath($file.FullName).ToLowerInvariant()] = $file.FullName
    }
    if ($sourceFiles.Count -ne $destMap.Count) {
        throw "backup file count mismatch ($($sourceFiles.Count) source vs $($destMap.Count) backup)"
    }
    foreach ($file in $sourceFiles) {
        $relative = $file.FullName.Substring($sourceRoot.Length).Replace("\", "/")
        $destKey = ([IO.Path]::GetFullPath((Join-Path $Destination $relative))).ToLowerInvariant()
        if (-not $destMap.ContainsKey($destKey)) {
            throw "backup missing file: $relative"
        }
        $sourceHash = Get-Sha256Hex -LiteralPath $file.FullName
        $destHash = Get-Sha256Hex -LiteralPath $destMap[$destKey]
        if ($sourceHash -ne $destHash) {
            throw "backup hash mismatch: $relative"
        }
    }
}

if (-not $BackupRoot) {
    if (-not $env:LOCALAPPDATA) {
        throw "LOCALAPPDATA is unavailable"
    }
    $BackupRoot = Join-Path $env:LOCALAPPDATA "TiangongV3Recovery\InstallerBackups"
}
$BackupRoot = Resolve-SafeAbsolutePath -PathValue $BackupRoot -Label "backup root"
if ([IO.Path]::GetPathRoot($BackupRoot).TrimEnd("\") -eq $BackupRoot) {
    throw "backup root may not be a volume root"
}

$candidateRows = @()
foreach ($candidate in @(Read-Candidates)) {
    $id = [string]$candidate.id
    $rawPath = [string]$candidate.path
    if ($id -notmatch "^[a-z0-9][a-z0-9._-]{0,63}$") {
        throw "candidate id is invalid: $id"
    }
    $source = Resolve-SafeAbsolutePath -PathValue $rawPath -Label "candidate source"
    if (Test-Path -LiteralPath $source -PathType Container) {
        $candidateRows += [pscustomobject]@{ id = $id; path = $source }
    }
}

# A pristine first install has nothing to protect and should leave no recovery
# residue. Existing installations always receive a new immutable snapshot.
if (-not $candidateRows) {
    exit 0
}

$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmss.fffffffZ")
$nonce = [Guid]::NewGuid().ToString("N")
$staging = Assert-ChildPath -Parent $BackupRoot -Child (Join-Path $BackupRoot ".staging-$nonce")
$final = Assert-ChildPath -Parent $BackupRoot -Child (Join-Path $BackupRoot "$timestamp-$nonce")
New-Item -ItemType Directory -Path $staging -Force | Out-Null

try {
    $sources = @()
    foreach ($candidate in $candidateRows) {
        $destination = Assert-ChildPath -Parent $staging -Child (Join-Path $staging $candidate.id)
        Copy-VerifiedTree -Source $candidate.path -Destination $destination
        $files = @(
            Get-ChildItem -LiteralPath $destination -Recurse -File -Force |
                Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
                Sort-Object FullName
        )
        $fileRows = @(
            foreach ($file in $files) {
                [ordered]@{
                    path = Get-RelativeChildPath -Parent $destination -Child $file.FullName
                    bytes = [int64]$file.Length
                    sha256 = Get-Sha256Hex -LiteralPath $file.FullName
                }
            }
        )
        $totalBytes = [int64]0
        foreach ($fileRow in $fileRows) {
            $totalBytes += [int64]$fileRow["bytes"]
        }
        $sources += [ordered]@{
            id = $candidate.id
            source = $candidate.path
            file_count = $fileRows.Count
            bytes = $totalBytes
            files = $fileRows
        }
    }

    $manifest = [ordered]@{
        schema = "tiangong.windows.preinstall-backup.v1"
        created_at_utc = (Get-Date).ToUniversalTime().ToString("o")
        install_directory = [string]$InstallDirectory
        sources = $sources
    }
    $manifestPath = Join-Path $staging "preinstall-backup-manifest.json"
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    $manifestHash = Get-Sha256Hex -LiteralPath $manifestPath
    Set-Content -LiteralPath (Join-Path $staging "preinstall-backup-manifest.sha256") `
        -Value "$manifestHash  preinstall-backup-manifest.json" -Encoding ASCII

    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    Move-Item -LiteralPath $staging -Destination $final
    Write-Output $final
    exit 0
}
catch {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Error "$($_.Exception.Message)`n$($_.ScriptStackTrace)"
    exit 1
}
