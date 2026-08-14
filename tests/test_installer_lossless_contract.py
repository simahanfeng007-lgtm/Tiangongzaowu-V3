from __future__ import annotations

import hashlib
import pytest
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = ROOT / "build" / "preinstall-backup.ps1"
INSTALLER_INCLUDE = ROOT / "build" / "installer.nsh"


def _powershell() -> str:
    # Keep this identical to build/installer.nsh.  Preferring pwsh here hid a
    # real Windows PowerShell 5.1 incompatibility in the shipped installer.
    windows_dir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = (
        str(Path(windows_dir) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
        "powershell.exe",
    )
    for candidate in candidates:
        try:
            completed = subprocess.run(
                [candidate, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
                capture_output=True,
                check=False,
                text=True,
                timeout=15,
            )
        except OSError:
            continue
        if completed.returncode == 0:
            return candidate
    raise AssertionError("PowerShell is required for the installer backup contract")


def test_installer_aborts_when_preinstall_backup_fails() -> None:
    include = INSTALLER_INCLUDE.read_text(encoding="utf-8")
    assert "preinstall-backup.ps1" in include
    assert "ExecWait" in include
    assert "${If} $0 != 0" in include
    assert "Abort" in include
    assert "user data and recovery snapshots are preserved" in include


@pytest.mark.skipif(os.name != "nt", reason="installer PowerShell contract is Windows-only")
def test_installer_contract_uses_inbox_windows_powershell() -> None:
    include = INSTALLER_INCLUDE.read_text(encoding="utf-8")
    assert r"$WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" in include
    assert Path(_powershell()).name.casefold() == "powershell.exe"


@pytest.mark.skipif(os.name != "nt", reason="installer PowerShell contract is Windows-only")
def test_preinstall_backup_is_verified_atomic_and_non_destructive(tmp_path: Path) -> None:
    source_a = tmp_path / "profile"
    source_b = tmp_path / "life"
    source_a.mkdir()
    source_b.mkdir()
    (source_a / "设置.json").write_text('{"theme":"dark"}', encoding="utf-8")
    nested = source_b / "lives" / "origin"
    nested.mkdir(parents=True)
    (nested / "soul.txt").write_text("生命数据", encoding="utf-8")

    candidates = tmp_path / "candidates.json"
    candidates.write_text(
        json.dumps(
            [
                {"id": "profile", "path": str(source_a)},
                {"id": "life-data", "path": str(source_b)},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    backup_root = tmp_path / "recovery"
    before = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (source_a / "设置.json", nested / "soul.txt")
    }

    completed = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(BACKUP_SCRIPT),
            "-BackupRoot",
            str(backup_root),
            "-CandidateManifestPath",
            str(candidates),
            "-InstallDirectory",
            str(tmp_path / "installed"),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout

    snapshots = [
        path
        for path in backup_root.iterdir()
        if path.is_dir() and not path.name.startswith(".staging-")
    ]
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    manifest_path = snapshot / "preinstall-backup-manifest.json"
    digest_path = snapshot / "preinstall-backup-manifest.sha256"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    assert manifest["schema"] == "tiangong.windows.preinstall-backup.v1"
    assert {item["id"] for item in manifest["sources"]} == {"profile", "life-data"}
    assert sum(item["file_count"] for item in manifest["sources"]) == 2
    declared = digest_path.read_text(encoding="ascii").split()[0]
    assert declared == hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    after = {
        path.relative_to(tmp_path).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (source_a / "设置.json", nested / "soul.txt")
    }
    assert after == before
    assert not list(backup_root.glob(".staging-*"))
