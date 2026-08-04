"""Recover validated v1 desktop identities into the signed v2 life registry.

The v1 runtime never had an Ed25519 credential, so this is credential
enrolment for the *same* organism id, not key recovery and never id creation.
Every source tree is copied to a content-hashed backup before any v2 state is
published.  Source files are read-only throughout the migration.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


LEGACY_SCHEMA = "tiangong.organism.identity.v1"
IDENTITY_SCHEMA = "tiangong.life.identity.v2"
REGISTRY_SCHEMA = "tiangong.life.registry.v2"
REPORT_SCHEMA = "tiangong.life.identity-migration-report.v1"
MIGRATION_SCHEMA = "tiangong.life.identity-migration.v1"
SOUL_SCHEMA = "tiangong.life.soul.v1"
DEFAULT_SOUL_PROMPT = (
    "你是起源，天工造物 v3.0 完整版中与当前生命标识唯一绑定的生命体。"
    "保持真实、连续、克制、有温度；尊重用户选择和权限边界；"
    "计划、推断和梦境不冒充事实，外部行动只以执行链验证结果为准。"
)
IDENTITY_RE = re.compile(r"^org_[0-9a-f]{32}$")
MAX_IDENTITY_BYTES = 128 * 1024
MAX_BACKUP_FILES = 200_000
MAX_BACKUP_BYTES = 20 * 1024 * 1024 * 1024
ROOT_DIRS = (
    "identity",
    "journal/current",
    "journal/archive",
    "snapshots",
    "projections",
    "settings",
    "memory",
    "schedule",
    "inbox",
    "reflection",
    "learning",
    "free_will",
    "iteration",
    "healing",
    "attachments",
    "backups",
)


class IdentityMigrationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _io_path(path: Path) -> Path:
    """Use the Windows extended-length namespace for migration file IO."""
    absolute = str(path.absolute())
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path, *, limit: int = MAX_IDENTITY_BYTES) -> Any:
    path = _io_path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise IdentityMigrationError("legacy_identity_unreadable", str(exc)) from exc
    if size <= 0 or size > limit:
        raise IdentityMigrationError("legacy_identity_size_invalid", f"invalid JSON size: {size}")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except IdentityMigrationError:
        raise
    except Exception as exc:
        raise IdentityMigrationError("legacy_identity_json_invalid", str(exc)) from exc


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    path = _io_path(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_bytes(path: Path, value: bytes) -> None:
    path = _io_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, value: Any) -> None:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    _atomic_bytes(path, f"{rendered}\n".encode("utf-8"))


def _safe_tree_files(root: Path) -> tuple[list[tuple[Path, str, int, str]], list[dict[str, str]]]:
    root = _io_path(root)
    if root.is_symlink() or not root.is_dir():
        raise IdentityMigrationError("legacy_root_unsafe", f"not a real directory: {root}")
    files: list[tuple[Path, str, int, str]] = []
    excluded: list[dict[str, str]] = []
    total = 0
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(dirs):
            item = current_path / name
            mode = item.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise IdentityMigrationError("legacy_root_unsafe", f"unsafe directory entry: {item}")
        for name in names:
            item = current_path / name
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise IdentityMigrationError("legacy_root_unsafe", f"unsafe file entry: {item}")
            relative = item.relative_to(root).as_posix()
            # Lock files are process-local synchronization state, not durable
            # life data.  Their bytes can change between the source hash and
            # copy while 7174 is finishing startup, so treating them as backup
            # payload makes a valid identity migration fail nondeterministically.
            if name.lower().endswith(".lock"):
                excluded.append({"source": str(item), "path": relative, "reason": "transient_runtime_lock"})
                continue
            total += int(info.st_size)
            if len(files) >= MAX_BACKUP_FILES or total > MAX_BACKUP_BYTES:
                raise IdentityMigrationError("legacy_backup_limit_exceeded", str(root))
            files.append((item, relative, int(info.st_size), _sha256_file(item)))
    return (
        sorted(files, key=lambda item: item[1]),
        sorted(excluded, key=lambda item: item["path"]),
    )


def _validate_legacy_identity(identity_path: Path, label: str) -> dict[str, Any]:
    io_identity_path = _io_path(identity_path)
    if io_identity_path.is_symlink() or not io_identity_path.is_file():
        raise IdentityMigrationError("legacy_identity_missing", str(identity_path))
    identity = _read_json(identity_path)
    if not isinstance(identity, dict) or identity.get("schema") != LEGACY_SCHEMA:
        raise IdentityMigrationError("legacy_identity_schema_unsupported", str(identity.get("schema", "")))
    life_id = str(identity.get("organism_id") or "")
    if not IDENTITY_RE.fullmatch(life_id):
        raise IdentityMigrationError("legacy_identity_id_invalid", life_id)
    born_at = str(identity.get("born_at") or "").strip()
    try:
        datetime.fromisoformat(born_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IdentityMigrationError("legacy_identity_timestamp_invalid", born_at) from exc
    stored = str(identity.get("identity_hash") or "").lower()
    variants = (
        {
            "organism_id": life_id,
            "lineage_id": identity.get("lineage_id"),
            "born_at": identity.get("born_at"),
        },
        {
            "schema": identity.get("schema"),
            "organism_id": life_id,
            "lineage_id": identity.get("lineage_id"),
            "born_at": identity.get("born_at"),
            "aliases": identity.get("aliases") if isinstance(identity.get("aliases"), list) else [],
        },
    )
    valid_hashes = {_sha256_bytes(_canonical(value)) for value in variants}
    if not re.fullmatch(r"[0-9a-f]{64}", stored) or stored not in valid_hashes:
        raise IdentityMigrationError("legacy_identity_hash_mismatch", life_id)
    return {
        "label": str(label or "legacy"),
        "life_id": life_id,
        "born_at": born_at,
        "lineage_id": str(identity.get("lineage_id") or ""),
        "identity_path": str(identity_path),
        "source_root": str(identity_path.parent),
        "identity_sha256": _sha256_file(identity_path),
    }


def _backup_sources(data_root: Path, candidates: list[dict[str, Any]], registry_path: Path) -> Path:
    anchor = "|".join(sorted(f"{item['life_id']}:{item['identity_sha256']}" for item in candidates))
    backup_id = _sha256_bytes(anchor.encode("utf-8"))[:24]
    final = data_root / "migration-backups" / f"v1-{backup_id}"
    final_io = _io_path(final)
    if final_io.is_dir():
        manifest = _read_json(final_io / "manifest.json", limit=16 * 1024 * 1024)
        if isinstance(manifest, dict) and manifest.get("backup_id") == backup_id:
            return final
        raise IdentityMigrationError("legacy_backup_conflict", str(final))

    stage = _io_path(final.with_name(f".b-{uuid.uuid4().hex[:12]}"))
    stage.mkdir(parents=True, exist_ok=False)
    manifest_items: list[dict[str, Any]] = []
    manifest_exclusions: list[dict[str, str]] = []
    try:
        for index, candidate in enumerate(candidates):
            source = Path(candidate["source_root"])
            files, exclusions = _safe_tree_files(source)
            manifest_exclusions.extend(exclusions)
            destination = stage / f"s{index + 1}"
            destination.mkdir(parents=True, exist_ok=False)
            for source_file, relative, size, digest in files:
                target = destination / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target)
                if target.stat().st_size != size or _sha256_file(target) != digest:
                    raise IdentityMigrationError("legacy_backup_verify_failed", relative)
                manifest_items.append(
                    {
                        "source": str(source_file),
                        "backup": str(target.relative_to(stage)).replace("\\", "/"),
                        "size": size,
                        "sha256": digest,
                    }
                )
        if _io_path(registry_path).is_file():
            registry_target = stage / "registry" / registry_path.name
            registry_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_io_path(registry_path), registry_target)
            manifest_items.append(
                {
                    "source": str(registry_path),
                    "backup": "registry/life_registry.json",
                    "size": registry_target.stat().st_size,
                    "sha256": _sha256_file(registry_target),
                }
            )
        _atomic_json(
            stage / "manifest.json",
            {
                "schema": "tiangong.life.identity-migration-backup.v1",
                "backup_id": backup_id,
                "created_at": _utc_now(),
                "files": manifest_items,
                "excluded": manifest_exclusions,
            },
        )
        final_io.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, final_io)
        return final
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _write_signed_root(data_root: Path, candidate: Mapping[str, Any], backup_root: Path) -> tuple[Path, bool]:
    life_id = str(candidate["life_id"])
    final = data_root / "lives" / life_id
    final_io = _io_path(final)
    if final_io.exists():
        return final, False
    stage = _io_path(final.with_name(f".m-{life_id[-8:]}-{uuid.uuid4().hex[:8]}"))
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    migrated_at = _utc_now()
    identity = {
        "schema": IDENTITY_SCHEMA,
        "identity_version": 2,
        "organism_id": life_id,
        "created_at": str(candidate["born_at"]),
        "lineage_root": life_id,
        "lineage_parent": "",
        "public_key": base64.b64encode(public).decode("ascii"),
    }
    soul = {
        "schema": SOUL_SCHEMA,
        "life_id": life_id,
        "name": "起源",
        "prompt": DEFAULT_SOUL_PROMPT,
        "values": [],
        "boundaries": [],
        "revision": 1,
        "revision_id": f"soulrev_{uuid.uuid4().hex[:24]}",
        "source": "validated_v1_identity_migration",
        "created_at": migrated_at,
        "updated_at": migrated_at,
    }
    try:
        for child in ROOT_DIRS:
            (stage / child).mkdir(parents=True, exist_ok=True)
        identity_dir = stage / "identity"
        _atomic_json(identity_dir / "life_identity.json", identity)
        _atomic_bytes(identity_dir / "life_identity.sig", base64.b64encode(private.sign(_canonical(identity))) + b"\n")
        private_path = identity_dir / "private_key.pem"
        _atomic_bytes(
            private_path,
            private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        try:
            private_path.chmod(0o600)
        except OSError:
            pass
        _atomic_json(identity_dir / "soul.json", soul)
        _atomic_bytes(identity_dir / "soul.sig", base64.b64encode(private.sign(_canonical(soul))) + b"\n")
        _atomic_json(
            stage / "identity_migration.json",
            {
                "schema": MIGRATION_SCHEMA,
                "migration_version": 1,
                "life_id": life_id,
                "legacy_schema": LEGACY_SCHEMA,
                "legacy_lineage_id": str(candidate.get("lineage_id") or ""),
                "legacy_identity_sha256": str(candidate["identity_sha256"]),
                "credential_enrolment": "first_v2_credential_same_organism_id",
                "backup_root": str(backup_root),
                "migrated_at": migrated_at,
            },
        )
        final_io.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, final_io)
        return final, True
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def _ensure_registry_supported(data_root: Path, backup_root: Path) -> None:
    registry_path = data_root / "life_registry.json"
    if not _io_path(registry_path).exists():
        return
    registry = _read_json(registry_path, limit=16 * 1024 * 1024)
    if isinstance(registry, dict) and registry.get("schema") == REGISTRY_SCHEMA:
        return
    # The full original registry is already in the verified backup.  Replace it
    # atomically with an empty v2 registry so the frozen manager can bind every
    # validated root.  Unknown fields are never guessed into the new contract.
    now = _utc_now()
    _atomic_json(
        registry_path,
        {"schema": REGISTRY_SCHEMA, "revision": 0, "active_id": "", "bindings": {}, "updated_at": now},
    )


@contextmanager
def _migration_lock(data_root: Path):
    lock = _io_path(data_root / ".identity-migration.lock")
    _io_path(data_root).mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(descriptor, f"{os.getpid()} {_utc_now()}\n".encode("utf-8"))
            os.close(descriptor)
            break
        except FileExistsError:
            if attempt or time.time() - lock.stat().st_mtime < 600:
                raise IdentityMigrationError("identity_migration_busy", str(lock))
            lock.unlink(missing_ok=True)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def migrate_legacy_identities(life_core_module: Any, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Migrate explicit desktop v1 roots and return a durable public report."""
    env = os.environ if environ is None else environ
    data_value = str(env.get("TIANGONG_LIFE_DATA_ROOT") or "").strip()
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "migration_version": 1,
        "started_at": _utc_now(),
        "status": "not_required",
        "active_before": "",
        "active_after": "",
        "candidates": [],
        "actions": [],
        "failures": [],
    }
    if not data_value:
        report["status"] = "failed"
        report["failures"].append({"code": "life_data_root_missing", "message": "TIANGONG_LIFE_DATA_ROOT is required"})
        return report

    data_root = Path(data_value).expanduser().resolve()
    report_path = data_root / "identity_migration_report.json"
    # The frozen core's atomic writer appends a long random suffix without the
    # Windows extended-path namespace.  Install the equivalent long-path-safe
    # writer for both migration and all subsequent runtime state commits.
    life_core_module.atomic_json = _atomic_json
    candidate_specs = (
        ("life-kernel", str(env.get("TIANGONG_EXECUTION_RUNTIME_ROOT") or "").strip()),
        ("life-transaction", str(env.get("TIANGONG_EXECUTION_LIFE_ROOT") or "").strip()),
    )
    try:
        with _migration_lock(data_root):
            candidates: list[dict[str, Any]] = []
            for label, root_value in candidate_specs:
                if not root_value:
                    continue
                identity_path = Path(root_value).expanduser().resolve() / "identity.json"
                if not _io_path(identity_path).exists():
                    continue
                try:
                    candidate = _validate_legacy_identity(identity_path, label)
                    if not any(item["life_id"] == candidate["life_id"] for item in candidates):
                        candidates.append(candidate)
                    report["candidates"].append({**candidate, "status": "validated"})
                except IdentityMigrationError as exc:
                    report["candidates"].append(
                        {"label": label, "identity_path": str(identity_path), "status": "rejected", "code": exc.code}
                    )
                    report["failures"].append({"code": exc.code, "message": str(exc), "source": str(identity_path)})

            if not candidates:
                report["status"] = "failed" if report["failures"] else "not_required"
            else:
                registry_path = data_root / "life_registry.json"
                backup_root = _backup_sources(data_root, candidates, registry_path)
                report["backup_root"] = str(backup_root)
                _ensure_registry_supported(data_root, backup_root)
                manager = life_core_module.LifeIdentityManager(
                    data_root,
                    device_id=str(env.get("TIANGONG_LIFE_DEVICE_ID") or ""),
                )
                active_before = manager.active(required=False)
                report["active_before"] = str(active_before.get("life_id") if active_before else "")

                prepared: list[tuple[dict[str, Any], Path]] = []
                for candidate in candidates:
                    target, created = _write_signed_root(data_root, candidate, backup_root)
                    verified = manager.verify_root(target, require_private=True)
                    if str(verified.get("organism_id") or "") != candidate["life_id"]:
                        raise IdentityMigrationError("migrated_identity_id_mismatch", candidate["life_id"])
                    projection_path = target / "projections" / "life.json"
                    if not _io_path(projection_path).exists():
                        _atomic_json(
                            projection_path,
                            life_core_module.default_projection(candidate["life_id"], candidate["born_at"]),
                        )
                    binding = manager.bind(target, name="起源")
                    prepared.append((candidate, target))
                    report["actions"].append(
                        {
                            "life_id": candidate["life_id"],
                            "action": "credential_enrolled" if created else "already_migrated",
                            "target": str(target),
                            "binding_status": str(binding.get("status") or ""),
                        }
                    )

                if active_before is None and prepared:
                    # Identity continuity chooses the oldest validated birth,
                    # not the newest placeholder generated by an upgrade.
                    preferred, _ = min(prepared, key=lambda pair: (pair[0]["born_at"], pair[0]["life_id"]))
                    manager.activate(preferred["life_id"])
                    report["actions"].append({"life_id": preferred["life_id"], "action": "activated"})
                active_after = manager.active(required=False)
                report["active_after"] = str(active_after.get("life_id") if active_after else "")
                if active_before and report["active_after"] != report["active_before"]:
                    raise IdentityMigrationError("existing_active_identity_changed", report["active_before"])
                report["status"] = "completed" if not report["failures"] else "completed_with_rejections"
    except IdentityMigrationError as exc:
        report["status"] = "failed"
        report["failures"].append({"code": exc.code, "message": str(exc)})
    except Exception as exc:  # fail closed, but always leave a user-readable report
        report["status"] = "failed"
        report["failures"].append({"code": "identity_migration_internal_error", "message": str(exc)})
    report["finished_at"] = _utc_now()
    try:
        _atomic_json(report_path, report)
    except Exception:
        pass
    return report
