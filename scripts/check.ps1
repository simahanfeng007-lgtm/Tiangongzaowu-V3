[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$AppRoot = Join-Path $Root "app"
$BackendRoot = Join-Path $AppRoot "backend\tiangong-backend"
$SourceRoot = Join-Path $Root "src"
$TestsRoot = Join-Path $Root "tests"
$Python = Join-Path $AppRoot "runtime\python312\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
  throw "Embedded Python 3.12 is required for the release quality gate"
}
$env:PYTHONDONTWRITEBYTECODE = "1"

& $Python (Join-Path $PSScriptRoot "audit-portable-paths.py") --root $Root
if ($LASTEXITCODE -ne 0) { throw "Portable path audit failed" }

Push-Location $Root
try {
  # Source enumeration via a manual directory walk.  A recursive
  # Get-ChildItem would still enter ACL-restricted cache directories
  # (.pytest_cache can be access-denied on this machine), so the walker
  # prunes them by name before descent, mirroring the runner exclusions.
  $SourceFiles = @()
  $skipNames = @(".git", "node_modules", ".pytest_cache", ".ruff_cache", "__pycache__")
  $skipPrefixes = @(
    (Join-Path $Root "app\runtime"),
    (Join-Path $Root "release-stage"),
    (Join-Path $Root "release-artifacts"),
    (Join-Path $Root "release-repair")
  )
  $walk = [System.Collections.Generic.Stack[string]]::new()
  $walk.Push($Root)
  while ($walk.Count -gt 0) {
    $current = $walk.Pop()
    $items = Get-ChildItem -LiteralPath $current -Force -ErrorAction SilentlyContinue
    foreach ($item in $items) {
      if ($item.PSIsContainer) {
        $skip = $skipNames -contains $item.Name
        if (-not $skip) {
          foreach ($prefix in $skipPrefixes) {
            if ($item.FullName.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
              $skip = $true
              break
            }
          }
        }
        if ($skip) { continue }
        $walk.Push($item.FullName)
      } else {
        $SourceFiles += $item.FullName
      }
    }
  }
} finally {
  Pop-Location
}
$forbidden = @($SourceFiles | Where-Object {
  $Leaf = [IO.Path]::GetFileName($_)
  $Base = [IO.Path]::GetFileNameWithoutExtension($Leaf)
  $Leaf -match "\.(bak|old|tmp)$" -or $Base -match "(_fixed|_patch)$"
} | ForEach-Object { Join-Path $Root $_ })
if ($forbidden) {
  $paths = $forbidden -join [Environment]::NewLine
  throw "Forbidden backup or patch artifacts were found:$([Environment]::NewLine)$paths"
}

$javascript = Get-ChildItem -LiteralPath $AppRoot -Recurse -File |
  Where-Object { $_.Extension -in ".js", ".mjs" -and $_.FullName -notlike "*\node_modules\*" }
foreach ($file in $javascript) {
  & node --check $file.FullName
  if ($LASTEXITCODE -ne 0) { throw "JavaScript syntax check failed: $($file.FullName)" }
}

$env:TIANGONG_SOURCE_ROOT = $Root
@'
import ast
import os
from pathlib import Path

root = Path(os.environ["TIANGONG_SOURCE_ROOT"])
for path in sorted([*root.joinpath("src").rglob("*.py"), *root.joinpath("tests").rglob("*.py")]):
    ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
'@ | & $Python -
if ($LASTEXITCODE -ne 0) { throw "Python syntax check failed" }

$ReadableSourceRoot = Join-Path $Root "readable-python-source"
$env:PYTHONPATH = @($SourceRoot, $BackendRoot, $ReadableSourceRoot) -join [IO.Path]::PathSeparator
$PythonTestRoots = @($TestsRoot)
$BundledSkillTests = Join-Path $BackendRoot "v3\bundled_skills\omni_body_skill\tests"
if (Test-Path -LiteralPath $BundledSkillTests -PathType Container) {
  $PythonTestRoots += $BundledSkillTests
}
& $Python -m pytest -q --maxfail=1 @PythonTestRoots
if ($LASTEXITCODE -ne 0) { throw "Python regression suite failed" }

$NodeTests = @(Get-ChildItem -LiteralPath $TestsRoot -Filter "*.test.mjs" -File |
  Sort-Object FullName |
  ForEach-Object { $_.FullName })
if (-not $NodeTests) { throw "No Node regression tests were discovered" }
& node --test @NodeTests
if ($LASTEXITCODE -ne 0) { throw "Node regression suite failed" }

& $Python (Join-Path $PSScriptRoot "sync-generated-sources.py") --check
if ($LASTEXITCODE -ne 0) { throw "Generated source mirror verification failed" }

Write-Host "Source verification passed: $($javascript.Count) JavaScript files; $($NodeTests.Count) Node test files."
