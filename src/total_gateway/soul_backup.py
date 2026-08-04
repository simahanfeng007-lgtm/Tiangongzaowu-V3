"""Encrypted, manifest-verified Soul Backup and atomic restore."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import struct
import tempfile
import time
from typing import Iterable, Mapping
import zipfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


MAGIC = b"TIANGONG-SOUL-BACKUP-V1\0"


class SoulBackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class SoulSource:
    source_id: str
    root: Path


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_passphrase(passphrase: str) -> str:
    value = str(passphrase or "")
    if len(value) < 12 or len(value) > 4096:
        raise SoulBackupError("soul_backup_passphrase_invalid")
    return value


def _derive(passphrase: str, salt: bytes) -> bytes:
    value = _validate_passphrase(passphrase)
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(value.encode("utf-8"))


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    # sqlite3.Connection's context manager commits/rolls back but does not
    # close the handle. Explicit closing is required on Windows before the
    # temporary backup tree can be removed.
    with closing(sqlite3.connect(source_uri, uri=True, timeout=30)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            with dst:
                src.backup(dst, pages=256, sleep=0.01)
                dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _safe_sources(sources: Iterable[SoulSource]) -> tuple[SoulSource, ...]:
    rows: list[SoulSource] = []
    for source in sources:
        source_id = str(source.source_id or "").strip()
        root = source.root.expanduser().resolve(strict=False)
        if not source_id or not source_id.replace("_", "").replace("-", "").isalnum():
            raise SoulBackupError("soul_backup_source_id_invalid")
        if not root.is_absolute() or root == Path(root.anchor) or root.is_symlink():
            raise SoulBackupError("soul_backup_source_root_invalid")
        if any(root == existing.root or root.is_relative_to(existing.root) or existing.root.is_relative_to(root) for existing in rows):
            # Overlap would make restore ordering ambiguous; keep the more
            # specific source only when exactly duplicated, otherwise fail.
            if any(root == existing.root for existing in rows):
                continue
            raise SoulBackupError("soul_backup_source_roots_overlap")
        rows.append(SoulSource(source_id, root))
    return tuple(rows)


class SoulBackupManager:
    def __init__(self, state_root: Path, sources: Iterable[SoulSource]) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.sources = _safe_sources(sources)
        self.backup_root = self.state_root / "soul-backups"
        self.backup_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def default_sources(state_root: Path, environ: Mapping[str, str] | None = None) -> tuple[SoulSource, ...]:
        env = dict(os.environ if environ is None else environ)
        candidates: list[SoulSource] = [SoulSource("gateway", state_root)]
        life = str(env.get("TIANGONG_LIFE_DATA_ROOT") or "").strip()
        if life:
            candidates.append(SoulSource("life", Path(life)))
        tiangong_home = Path.home() / ".tiangong"
        if tiangong_home.exists():
            candidates.append(SoulSource("tiangong_home", tiangong_home))
        # Keep sources disjoint.  A broad directory must never be restored over a
        # more specific state root (or vice versa), because that would make the
        # multi-root swap non-atomic.  Priority is intentional: gateway state is
        # authoritative, followed by the explicit life-data root, then the
        # optional legacy ~/.tiangong home.
        selected: list[SoulSource] = []
        selected_roots: list[Path] = []
        for item in candidates:
            root = item.root.expanduser().resolve(strict=False)
            overlaps = any(
                root == existing
                or root.is_relative_to(existing)
                or existing.is_relative_to(root)
                for existing in selected_roots
            )
            if overlaps:
                continue
            selected.append(SoulSource(item.source_id, root))
            selected_roots.append(root)
        return tuple(selected)

    def create(self, destination: Path | None = None, *, passphrase: str) -> dict[str, object]:
        passphrase = _validate_passphrase(passphrase)
        observed_ms = int(time.time() * 1000)
        target = (destination or (self.backup_root / f"soul-{observed_ms}.tgsoul")).expanduser().resolve(strict=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tiangong-soul-") as td:
            staging = Path(td)
            payload_root = staging / "payload"
            records: list[dict[str, object]] = []
            for source in self.sources:
                if not source.root.exists():
                    continue
                for path in source.root.rglob("*"):
                    rel = path.relative_to(source.root)
                    if path.is_symlink():
                        raise SoulBackupError(f"soul_backup_symlink_forbidden:{source.source_id}/{rel.as_posix()}")
                    if not path.is_file():
                        continue
                    # Do not recursively back up previous backups.
                    if source.root == self.state_root and rel.parts and rel.parts[0] == "soul-backups":
                        continue
                    archived = payload_root / source.source_id / rel
                    try:
                        if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
                            _sqlite_backup(path, archived)
                        else:
                            archived.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(path, archived)
                    except sqlite3.DatabaseError:
                        archived.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, archived)
                    records.append({
                        "source_id": source.source_id,
                        "path": rel.as_posix(),
                        "size": archived.stat().st_size,
                        "sha256": _sha_file(archived),
                    })
            records.sort(key=lambda item: (str(item["source_id"]), str(item["path"])))
            manifest = {
                "schema": "tiangong.soul-backup.manifest.v1",
                "created_at_ms": observed_ms,
                "sources": [{"source_id": item.source_id} for item in self.sources],
                "files": records,
            }
            manifest["manifest_sha256"] = hashlib.sha256(_canonical(manifest)).hexdigest()
            (staging / "manifest.json").write_bytes(_canonical(manifest))
            zip_path = staging / "payload.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.write(staging / "manifest.json", "manifest.json")
                for item in records:
                    rel = Path(str(item["source_id"])) / Path(str(item["path"]))
                    archive.write(payload_root / rel, (Path("payload") / rel).as_posix())
            plaintext = zip_path.read_bytes()
            salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
            key = _derive(passphrase, salt)
            header = {
                "schema": "tiangong.soul-backup.envelope.v1",
                "created_at_ms": observed_ms,
                "salt": salt.hex(),
                "nonce": nonce.hex(),
                "cipher": "AES-256-GCM",
                "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
            }
            header_bytes = _canonical(header)
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, header_bytes)
            temp = target.with_suffix(target.suffix + ".partial")
            with temp.open("wb") as handle:
                handle.write(MAGIC)
                handle.write(struct.pack(">I", len(header_bytes)))
                handle.write(header_bytes)
                handle.write(ciphertext)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        return {
            "ok": True,
            "schema": "tiangong.soul-backup.result.v1",
            "path": str(target),
            "size": target.stat().st_size,
            "sha256": _sha_file(target),
            "manifest_sha256": manifest["manifest_sha256"],
            "file_count": len(records),
        }

    def _decrypt(self, backup_path: Path, passphrase: str) -> tuple[dict[str, object], bytes]:
        raw = backup_path.expanduser().resolve(strict=True).read_bytes()
        if not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 4:
            raise SoulBackupError("soul_backup_envelope_invalid")
        offset = len(MAGIC)
        header_len = struct.unpack(">I", raw[offset:offset + 4])[0]
        offset += 4
        if header_len <= 0 or header_len > 64 * 1024 or len(raw) <= offset + header_len:
            raise SoulBackupError("soul_backup_header_invalid")
        header_bytes = raw[offset:offset + header_len]
        offset += header_len
        try:
            header = json.loads(header_bytes)
            salt = bytes.fromhex(str(header["salt"]))
            nonce = bytes.fromhex(str(header["nonce"]))
            plaintext = AESGCM(_derive(passphrase, salt)).decrypt(nonce, raw[offset:], header_bytes)
        except Exception as exc:
            raise SoulBackupError("soul_backup_authentication_failed") from exc
        if hashlib.sha256(plaintext).hexdigest() != str(header.get("plaintext_sha256") or ""):
            raise SoulBackupError("soul_backup_plaintext_hash_invalid")
        return header, plaintext

    def verify(self, backup_path: Path, *, passphrase: str) -> dict[str, object]:
        _header, plaintext = self._decrypt(backup_path, passphrase)
        with tempfile.TemporaryDirectory(prefix="tiangong-soul-verify-") as td:
            zip_path = Path(td) / "payload.zip"
            zip_path.write_bytes(plaintext)
            with zipfile.ZipFile(zip_path) as archive:
                for info in archive.infolist():
                    member = Path(info.filename)
                    if member.is_absolute() or ".." in member.parts:
                        raise SoulBackupError("soul_backup_archive_path_invalid")
                manifest = json.loads(archive.read("manifest.json"))
                claimed = str(manifest.pop("manifest_sha256", ""))
                if hashlib.sha256(_canonical(manifest)).hexdigest() != claimed:
                    raise SoulBackupError("soul_backup_manifest_hash_invalid")
                manifest["manifest_sha256"] = claimed
                for item in manifest.get("files", []):
                    member = (Path("payload") / str(item["source_id"]) / Path(str(item["path"]))).as_posix()
                    data = archive.read(member)
                    if len(data) != int(item["size"]) or hashlib.sha256(data).hexdigest() != str(item["sha256"]):
                        raise SoulBackupError("soul_backup_file_hash_invalid")
        return {"ok": True, "schema": "tiangong.soul-backup.verify.v1", "file_count": len(manifest.get("files", [])), "manifest_sha256": manifest["manifest_sha256"]}

    def restore(self, backup_path: Path, *, passphrase: str, targets: Mapping[str, Path] | None = None) -> dict[str, object]:
        self.verify(backup_path, passphrase=passphrase)
        _header, plaintext = self._decrypt(backup_path, passphrase)
        target_map = {item.source_id: item.root for item in self.sources}
        if targets is not None:
            target_map = {str(key): Path(value).expanduser().resolve(strict=False) for key, value in targets.items()}
        with tempfile.TemporaryDirectory(prefix="tiangong-soul-restore-") as td:
            stage = Path(td)
            zip_path = stage / "payload.zip"
            zip_path.write_bytes(plaintext)
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(stage / "unpacked")
                manifest = json.loads((stage / "unpacked" / "manifest.json").read_text(encoding="utf-8"))
            swaps: list[tuple[Path, Path | None]] = []
            stamp = str(int(time.time() * 1000))
            try:
                for source in manifest.get("sources", []):
                    source_id = str(source["source_id"])
                    if source_id not in target_map:
                        raise SoulBackupError(f"soul_backup_restore_target_missing:{source_id}")
                    prepared = stage / "unpacked" / "payload" / source_id
                    prepared.mkdir(parents=True, exist_ok=True)
                    destination = target_map[source_id]
                    if destination == Path(destination.anchor) or destination.is_symlink():
                        raise SoulBackupError("soul_backup_restore_target_invalid")
                    rollback = destination.with_name(destination.name + f".pre-soul-restore-{stamp}") if destination.exists() else None
                    if rollback:
                        os.replace(destination, rollback)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(prepared, destination)
                    swaps.append((destination, rollback))
            except Exception:
                for destination, rollback in reversed(swaps):
                    shutil.rmtree(destination, ignore_errors=True)
                    if rollback and rollback.exists():
                        os.replace(rollback, destination)
                raise
            else:
                for _destination, rollback in swaps:
                    if rollback and rollback.exists():
                        shutil.rmtree(rollback, ignore_errors=True)
        return {"ok": True, "schema": "tiangong.soul-backup.restore.v1", "restored_sources": sorted(target_map)}


__all__ = ["SoulBackupError", "SoulBackupManager", "SoulSource"]
