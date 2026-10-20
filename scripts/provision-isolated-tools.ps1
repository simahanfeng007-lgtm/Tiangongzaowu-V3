[CmdletBinding()]
param(
    [string]$SourceGitRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$WorkspaceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$DestinationRoot = Join-Path $WorkspaceRoot "app\runtime\python312\git"
$EvidenceRoot = Join-Path $WorkspaceRoot "release-artifacts\build-evidence\git-bundle"
$DirectoryNames = @("bin", "cmd", "etc", "mingw64", "usr")
$FileNames = @("LICENSE.txt")
# These exact upstream help/history files contain host-path examples. They are
# not executable dependencies; preserve all licenses and runtime configuration.
$ExcludedDocumentation = @(
    "ReleaseNotes.html",
    "mingw64/share/doc/git-doc/technical/api-trace2.html",
    "usr/share/vim/vim92/doc/builtin.txt",
    "usr/share/vim/vim92/doc/gui_w32.txt",
    "usr/share/vim/vim92/doc/pi_netrw.txt",
    "usr/share/vim/vim92/doc/version6.txt",
    "usr/share/vim/vim92/doc/version8.txt",
    "usr/share/vim/vim92/pack/dist/opt/netrw/doc/netrw.txt"
)

function Get-SoftwareFiles {
    param([Parameter(Mandatory = $true)][string]$Root)

    $RootItem = Get-Item -LiteralPath $Root -Force
    if (-not $RootItem.PSIsContainer -or
        ($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Software root must be a normal directory"
    }
    $Pending = [Collections.Generic.Stack[string]]::new()
    $Files = [Collections.Generic.List[object]]::new()
    $Pending.Push($RootItem.FullName)
    while ($Pending.Count -gt 0) {
        $Current = $Pending.Pop()
        foreach ($Item in Get-ChildItem -LiteralPath $Current -Force) {
            if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Software tree contains a link or junction: $($Item.Name)"
            }
            if ($Item.PSIsContainer) {
                if ($Item.Name -in @(".git", ".ssh", ".gnupg", ".aws", ".azure")) {
                    throw "Software tree contains a user configuration directory: $($Item.Name)"
                }
                $Pending.Push($Item.FullName)
            } else {
                $Files.Add($Item)
            }
        }
    }
    return $Files.ToArray()
}

function Get-RelativeSoftwarePath {
    param([string]$Root, [string]$File)

    $Prefix = $Root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $File.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Software file escaped its root"
    }
    return $File.Substring($Prefix.Length).Replace("\", "/")
}

function Get-SoftwareDigest {
    param([string]$File)

    $Hasher = [Security.Cryptography.SHA256]::Create()
    $Stream = [IO.File]::OpenRead($File)
    try {
        return ([BitConverter]::ToString($Hasher.ComputeHash($Stream))).Replace("-", "").ToLowerInvariant()
    } finally {
        $Stream.Dispose()
        $Hasher.Dispose()
    }
}

function Assert-PublicSoftwareConfiguration {
    param([string]$Root, [object[]]$Files)

    $TextExtensions = @(".conf", ".config", ".cfg", ".ini", ".json", ".yaml", ".yml", ".xml", ".pem", ".key")
    foreach ($File in $Files) {
        $Relative = Get-RelativeSoftwarePath $Root $File.FullName
        if ($File.Name -match '^(\.gitconfig|\.git-credentials|\.netrc|_netrc|\.env(?:\..*)?|id_rsa|id_ecdsa|id_ed25519|ssh_host_.*_key|credentials(?:\.json)?|secrets(?:\.json)?|.*\.pfx|.*\.p12|.*\.ppk)$' -or
            ($File.Name -eq "gitconfig" -and $Relative -ne "etc/gitconfig")) {
            throw "Refused user configuration or private-key file: $Relative"
        }
        if ($File.Extension -in $TextExtensions -or $File.Name -eq "gitconfig") {
            $Text = [IO.File]::ReadAllText($File.FullName)
            if ($Text -match '-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----' -or
                $Text -match '(?im)^\s*(?:password|token|secret|api_key|access_token|refresh_token)\s*[:=]\s*[^\s#;]+' -or
                $Text -match 'https?://[^/\s:@]+:[^/\s@]+@') {
                throw "Refused suspicious configuration content: $Relative"
            }
            if ($Relative -eq "etc/gitconfig" -and
                $Text -match '(?im)^\s*\[(?:user|include|includeIf|safe)(?:\s|\])') {
                throw "Refused nonstandard user or external configuration in etc/gitconfig"
            }
        }
    }
}

if (-not $SourceGitRoot) {
    $GitCommand = Get-Command git.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $Candidate = Split-Path -Parent $GitCommand.Source
    while ($Candidate) {
        if ((Test-Path -LiteralPath (Join-Path $Candidate "cmd\git.exe") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $Candidate "mingw64") -PathType Container) -and
            (Test-Path -LiteralPath (Join-Path $Candidate "usr") -PathType Container)) {
            $SourceGitRoot = $Candidate
            break
        }
        $Parent = Split-Path -Parent $Candidate
        if ($Parent -eq $Candidate) { break }
        $Candidate = $Parent
    }
    if (-not $SourceGitRoot) { throw "Unable to discover an existing Git for Windows software tree" }
}
$SourceGitRoot = (Resolve-Path -LiteralPath $SourceGitRoot).Path
if (Test-Path -LiteralPath $DestinationRoot) {
    throw "Refused to replace an existing private Git bundle"
}
foreach ($Name in $DirectoryNames) {
    if (-not (Test-Path -LiteralPath (Join-Path $SourceGitRoot $Name) -PathType Container)) {
        throw "Git software directory is missing: $Name"
    }
}
foreach ($Name in $FileNames) {
    if (-not (Test-Path -LiteralPath (Join-Path $SourceGitRoot $Name) -PathType Leaf)) {
        throw "Git software license is missing: $Name"
    }
}

# Validate before copying. Excluded launchers, device/temp trees and uninstallers
# remain outside the product; no user profile or repository is copied.
$AllSourceFiles = @(Get-SoftwareFiles $SourceGitRoot)
Assert-PublicSoftwareConfiguration $SourceGitRoot $AllSourceFiles
$SourceFiles = @($AllSourceFiles | Where-Object {
    $Relative = Get-RelativeSoftwarePath $SourceGitRoot $_.FullName
    $Top = $Relative.Split('/')[0]
    ($Top -in $DirectoryNames -or $Relative -in $FileNames) -and
        $Relative -notin $ExcludedDocumentation
} | Sort-Object FullName)
$SourceVersion = (Get-Item -LiteralPath (Join-Path $SourceGitRoot "cmd\git.exe")).VersionInfo.ProductVersion
if ($SourceVersion -notmatch '^\d+\.\d+\.\d+\.windows\.\d+$') {
    throw "Existing Git software has an unsupported or missing version identity"
}
[IO.Directory]::CreateDirectory($EvidenceRoot) | Out-Null
[IO.Directory]::CreateDirectory($DestinationRoot) | Out-Null
$CopyResults = [Collections.Generic.List[object]]::new()
foreach ($Name in $DirectoryNames) {
    $CopyLog = Join-Path $EvidenceRoot "robocopy-$Name.log"
    $CopyArguments = @(
        (Join-Path $SourceGitRoot $Name), (Join-Path $DestinationRoot $Name),
        "/E", "/XJ", "/COPY:DAT", "/DCOPY:DAT", "/R:1", "/W:1",
        "/NP", "/NFL", "/NDL", "/LOG:$CopyLog", "/XF"
    ) + @($ExcludedDocumentation | ForEach-Object { Join-Path $SourceGitRoot $_ })
    & robocopy.exe @CopyArguments | Out-Null
    $CopyExit = $LASTEXITCODE
    $CopyResults.Add([ordered]@{ directory = $Name; exitCode = $CopyExit })
    if ($CopyExit -ge 8) { throw "Private Git copy failed for $Name with robocopy exit $CopyExit" }
}
foreach ($Name in $FileNames) {
    Copy-Item -LiteralPath (Join-Path $SourceGitRoot $Name) -Destination (Join-Path $DestinationRoot $Name)
}
$DestinationFiles = @(Get-SoftwareFiles $DestinationRoot)
if ($DestinationFiles.Count -ne $SourceFiles.Count) {
    throw "Private Git copy file count differs from the selected software tree"
}
$Records = [Collections.Generic.List[object]]::new()
$TotalBytes = [long]0
foreach ($File in $SourceFiles) {
    $Relative = Get-RelativeSoftwarePath $SourceGitRoot $File.FullName
    $CopiedPath = Join-Path $DestinationRoot $Relative
    $Copied = Get-Item -LiteralPath $CopiedPath -Force
    $SourceDigest = Get-SoftwareDigest $File.FullName
    if ($Copied.Length -ne $File.Length -or (Get-SoftwareDigest $CopiedPath) -ne $SourceDigest) {
        throw "Private Git copy size or digest differs: $Relative"
    }
    $TotalBytes += $File.Length
    $Records.Add([ordered]@{ path = $Relative; sizeBytes = $File.Length; sha256 = $SourceDigest })
}

$BundledGit = Join-Path $DestinationRoot "cmd\git.exe"
$SmokeRoot = Join-Path $EvidenceRoot ("offline-smoke-" + [Guid]::NewGuid().ToString("N"))
$SmokeRepository = Join-Path $SmokeRoot "repository"
[IO.Directory]::CreateDirectory($SmokeRepository) | Out-Null
$EmptyConfig = Join-Path $SmokeRoot "empty.gitconfig"
[IO.File]::WriteAllText($EmptyConfig, "", [Text.UTF8Encoding]::new($false))
$SavedGitEnvironment = @{}
foreach ($Variable in Get-ChildItem Env:) {
    if ($Variable.Name.StartsWith("GIT_", [StringComparison]::OrdinalIgnoreCase)) {
        $SavedGitEnvironment[$Variable.Name] = $Variable.Value
    }
}
$SavedPath = $env:PATH
$SmokeLines = [Collections.Generic.List[string]]::new()
function Invoke-BundledGit {
    param([string[]]$Arguments)

    $Output = @(& $BundledGit @Arguments 2>&1)
    $GitExit = $LASTEXITCODE
    $Result = ($Output | ForEach-Object { $_.ToString() }) -join "`n"
    $SmokeLines.Add("git $($Arguments -join ' ') => exit $GitExit")
    if ($Result) { $SmokeLines.Add($Result) }
    if ($GitExit -ne 0) { throw "Private Git offline smoke failed with exit $GitExit" }
    return $Result
}
try {
    foreach ($Name in $SavedGitEnvironment.Keys) { Remove-Item -LiteralPath "Env:$Name" }
    $env:PATH = (Join-Path $env:SystemRoot "System32") + ";" + $env:SystemRoot
    $env:GIT_CONFIG_GLOBAL = $EmptyConfig
    $env:GIT_CONFIG_SYSTEM = $EmptyConfig
    $env:GIT_CONFIG_NOSYSTEM = "1"
    $GitVersion = Invoke-BundledGit -Arguments @("--version")
    if ($GitVersion -ne "git version $SourceVersion") { throw "Private Git version differs from the source software" }
    Invoke-BundledGit -Arguments @("-C", $SmokeRepository, "init", "--quiet", "--initial-branch=main") | Out-Null
    $Sample = "Tiangong bundled Git offline smoke"
    [IO.File]::WriteAllText((Join-Path $SmokeRepository "smoke.txt"), $Sample + "`n", [Text.UTF8Encoding]::new($false))
    Invoke-BundledGit -Arguments @("-C", $SmokeRepository, "add", "--", "smoke.txt") | Out-Null
    Invoke-BundledGit -Arguments @("-C", $SmokeRepository, "-c", "user.name=Tiangong Offline QA", "-c", "user.email=qa@example.invalid", "-c", "commit.gpgSign=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "commit", "--quiet", "-m", "Verify bundled Git offline") | Out-Null
    if ((Invoke-BundledGit -Arguments @("-C", $SmokeRepository, "log", "-1", "--format=%s")) -ne "Verify bundled Git offline") {
        throw "Private Git log did not observe the offline commit"
    }
    if ((Invoke-BundledGit -Arguments @("-C", $SmokeRepository, "show", "HEAD:smoke.txt")) -ne $Sample) {
        throw "Private Git object content differs from the committed sample"
    }
} finally {
    foreach ($Variable in @(Get-ChildItem Env:)) {
        if ($Variable.Name.StartsWith("GIT_", [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath "Env:$($Variable.Name)"
        }
    }
    foreach ($Name in $SavedGitEnvironment.Keys) { Set-Item -LiteralPath "Env:$Name" -Value $SavedGitEnvironment[$Name] }
    $env:PATH = $SavedPath
    [IO.File]::WriteAllLines((Join-Path $EvidenceRoot "offline-smoke.log"), $SmokeLines, [Text.UTF8Encoding]::new($false))
}
$Manifest = [ordered]@{
    schema = "tiangong.isolated-tools.git.v1"
    toolId = "git"
    gitVersion = $GitVersion
    sourceSoftwareVersion = $SourceVersion
    payloadRoots = @($DirectoryNames) + @($FileNames)
    nonRuntimeDocumentationExcluded = @($ExcludedDocumentation)
    fileCount = $Records.Count
    sizeBytes = $TotalBytes
    files = $Records.ToArray()
}
# Only relative paths and public software identities enter the product payload.
[IO.File]::WriteAllText((Join-Path $DestinationRoot "bundle-manifest.json"), ($Manifest | ConvertTo-Json -Depth 6) + "`n", [Text.UTF8Encoding]::new($false))
$Report = [ordered]@{
    gitVersion = $GitVersion
    sourceSoftwareVersion = $SourceVersion
    nonRuntimeDocumentationExcluded = @($ExcludedDocumentation)
    fileCount = $Records.Count
    sizeBytes = $TotalBytes
    sizeAndSha256Verified = $true
    offlineSmokePassed = $true
    hostPathExcludedDuringSmoke = $true
    robocopy = $CopyResults.ToArray()
}
[IO.File]::WriteAllText((Join-Path $EvidenceRoot "verification.json"), ($Report | ConvertTo-Json -Depth 4) + "`n", [Text.UTF8Encoding]::new($false))
$Report | ConvertTo-Json -Depth 4
