from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = ROOT / "build/preinstall-backup.ps1"
POWERSHELL = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer backup behavior")


def isolated_environment(tmp_path: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("TIANGONG_")}
    env["APPDATA"] = str(tmp_path / "AppData/Roaming")
    env["LOCALAPPDATA"] = str(tmp_path / "AppData/Local")
    return env


def run_backup(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(BACKUP_SCRIPT), "-IsolatedInstallation", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, errors="replace", timeout=120,
    )


def test_isolated_upgrade_backs_up_only_its_profile_without_touching_normal_data(tmp_path: Path) -> None:
    env = isolated_environment(tmp_path)
    roaming = Path(env["APPDATA"])
    isolated = roaming / "tiangong-v3-isolated"
    normal = roaming / "tiangong-v3-qiyuan"
    versioned = roaming / "天工造物 v3.0.3 完整版"
    legacy_life = tmp_path / "Documents/天工造物生命数据"
    for directory in (isolated, normal, versioned, legacy_life):
        directory.mkdir(parents=True)
    canonical_files = []
    for directory in (normal, versioned, legacy_life):
        sentinel = directory / "do-not-touch.sqlite3"
        sentinel.write_bytes(b"canonical sentinel, not a SQLite database")
        canonical_files.append(sentinel)
    canonical_before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in canonical_files}
    payload = isolated / "settings.json"
    payload.write_bytes(b'{"isolated":true}\n')
    nested = isolated / "runtime/state/identity.json"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b'{"owner":"isolated"}\n')
    before = {path.relative_to(isolated).as_posix(): path.read_bytes()
              for path in isolated.rglob("*") if path.is_file()}

    completed = run_backup(env, "-InstallDirectory", str(tmp_path / "installed"))
    assert completed.returncode == 0, completed.stderr + completed.stdout
    backup_root = Path(env["LOCALAPPDATA"]) / "TiangongV3IsolatedRecovery/InstallerBackups"
    snapshots = list(backup_root.iterdir())
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert not snapshot.name.startswith(".staging-")
    manifest_path = snapshot / "preinstall-backup-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    assert len(manifest["sources"]) == 1
    assert manifest["sources"][0]["id"] == "isolated-profile"
    assert Path(manifest["sources"][0]["source"]) == isolated
    assert manifest["sources"][0]["file_count"] == 2
    assert {row["path"] for row in manifest["sources"][0]["files"]} == set(before)
    copied = snapshot / "isolated-profile"
    assert {path.relative_to(copied).as_posix(): path.read_bytes()
            for path in copied.rglob("*") if path.is_file()} == before
    declared = (snapshot / "preinstall-backup-manifest.sha256").read_text(encoding="ascii").split()[0]
    assert declared == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert {path.relative_to(isolated).as_posix(): path.read_bytes()
            for path in isolated.rglob("*") if path.is_file()} == before
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in canonical_files} == canonical_before
    assert not (Path(env["LOCALAPPDATA"]) / "TiangongV3Recovery").exists()


def test_isolated_first_install_does_not_snapshot_normal_profile(tmp_path: Path) -> None:
    env = isolated_environment(tmp_path)
    normal = Path(env["APPDATA"]) / "tiangong-v3-qiyuan"
    normal.mkdir(parents=True)
    sentinel = normal / "sentinel.txt"
    sentinel.write_bytes(b"normal profile")
    completed = run_backup(env)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert sentinel.read_bytes() == b"normal profile"
    assert not Path(env["LOCALAPPDATA"]).exists()


def test_isolated_backup_rejects_a_candidate_manifest_for_normal_data(tmp_path: Path) -> None:
    env = isolated_environment(tmp_path)
    normal = Path(env["APPDATA"]) / "tiangong-v3-qiyuan"
    normal.mkdir(parents=True)
    sentinel = normal / "sentinel.txt"
    sentinel.write_bytes(b"normal profile")
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps([{"id": "normal", "path": str(normal)}]), encoding="utf-8")
    completed = run_backup(env, "-CandidateManifestPath", str(candidates))
    assert completed.returncode != 0
    assert "cannot override isolated installation sources" in completed.stderr
    assert sentinel.read_bytes() == b"normal profile"
    assert not Path(env["LOCALAPPDATA"]).exists()


def test_isolated_backup_rejects_a_junction_before_following_normal_data(tmp_path: Path) -> None:
    env = isolated_environment(tmp_path)
    normal = Path(env["APPDATA"]) / "tiangong-v3-qiyuan"
    normal.mkdir(parents=True)
    sentinel = normal / "sentinel.sqlite3"
    sentinel.write_bytes(b"normal database sentinel")
    isolated = Path(env["APPDATA"]) / "tiangong-v3-isolated"
    isolated.mkdir()
    link = isolated / "linked-normal-profile"
    link_env = {**env, "TIANGONG_TEST_LINK": str(link), "TIANGONG_TEST_TARGET": str(normal)}
    created = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command",
         "New-Item -ItemType Junction -Path $env:TIANGONG_TEST_LINK -Value $env:TIANGONG_TEST_TARGET | Out-Null"],
        env=link_env, capture_output=True, text=True, errors="replace", timeout=30,
    )
    assert created.returncode == 0, created.stderr
    try:
        completed = run_backup(env)
        assert completed.returncode != 0
        assert "contains a reparse point" in completed.stderr
        assert sentinel.read_bytes() == b"normal database sentinel"
        assert not Path(env["LOCALAPPDATA"]).exists()
    finally:
        # Remove only the junction itself; never recursively traverse its target.
        os.rmdir(link)
