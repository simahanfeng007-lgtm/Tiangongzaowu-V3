"""Compatibility core for the source-complete Tiangong v3 life runtime.

This module restores the public contracts that the historical frozen runtime
exported.  The authoritative source service lives in :mod:`life_service`; this
file exists for identity migration and for third-party integrations that still
import ``life_core`` directly.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .temperament import (
    generate_innate_temperament,
    validate_innate_temperament,
)

IDENTITY_SCHEMA = "tiangong.life.identity.v2"
REGISTRY_SCHEMA = "tiangong.life.registry.v2"
SOUL_SCHEMA = "tiangong.life.soul.v1"
ROOT_DIRS = (
    "identity", "journal/current", "journal/archive", "snapshots", "projections",
    "settings", "memory", "schedule", "inbox", "reflection", "learning",
    "free_will", "iteration", "healing", "attachments", "backups",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def atomic_json(path: Path | str, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the sibling staging name intentionally short.  Life roots contain
    # identity/version directories, and the embedded Windows runtime still has
    # a 260-character path ceiling; a target that is writable can otherwise
    # fail solely because a descriptive PID/UUID staging name crosses it.
    # Eight random hex chars keep collisions negligible without reintroducing
    # that path-length failure.
    temporary = path.with_name(f"~{uuid.uuid4().hex[:8]}.tmp")
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LifeCoreError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.status = int(status)


def default_projection(life_id: str, born_at: str) -> dict[str, Any]:
    return {
        "schema": "tiangong.life.projection.v2",
        "life_id": str(life_id),
        "writer_epoch": 1,
        "identity_revision": 1,
        "source_sequence": 0,
        "born_at": str(born_at),
        "updated_at": str(born_at),
        "status": "dormant",
    }


class LifeIdentityManager:
    def __init__(self, data_root: Path | str, *, device_id: str = "") -> None:
        self.data_root = Path(data_root).expanduser().resolve()
        self.device_id = str(device_id or "")
        self.lives_root = self.data_root / "lives"
        self.registry_path = self.data_root / "life_registry.json"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.lives_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._registry_cache_fingerprint: tuple[int, ...] = ()
        self._registry_cache: dict[str, Any] | None = None
        self._verified_identity_cache: dict[
            tuple[str, bool], tuple[tuple[int, ...], dict[str, Any]]
        ] = {}
        self._verified_temperament_cache: dict[
            str, tuple[tuple[Any, ...], dict[str, Any]]
        ] = {}
        self._root_cache: dict[
            str, tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], Path]
        ] = {}

    @staticmethod
    def _file_fingerprint(path: Path) -> tuple[int, ...]:
        try:
            value = path.stat()
        except FileNotFoundError:
            return (0, 0, 0, 0, 0)
        return (
            1,
            int(value.st_size),
            int(value.st_mtime_ns),
            int(value.st_ctime_ns),
            int(getattr(value, "st_ino", 0) or 0),
        )

    def _empty_registry(self) -> dict[str, Any]:
        return {
            "schema": REGISTRY_SCHEMA,
            "revision": 0,
            "active_id": "",
            "bindings": {},
            # This audit remains in the authority registry rather than an
            # identity's directory: a successful deletion intentionally
            # removes that directory and must not erase the evidence of it.
            "identity_audit": [],
            "updated_at": utc_now(),
        }

    @staticmethod
    def _append_identity_audit(
        registry: dict[str, Any],
        *,
        action: str,
        life_id: str,
        actor: str = "user",
        name: str = "",
    ) -> dict[str, Any]:
        raw_entries = registry.get("identity_audit")
        entries = list(raw_entries) if isinstance(raw_entries, list) else []
        entry = {
            "action": str(action),
            "life_id": str(life_id),
            "actor": str(actor or "user"),
            "name": str(name or ""),
            "at": utc_now(),
        }
        entries.append(entry)
        registry["identity_audit"] = entries[-200:]
        return entry

    def audit_entries(self, *, limit: int = 50) -> list[dict[str, Any]]:
        registry = self._load_registry()
        entries = registry.get("identity_audit")
        if not isinstance(entries, list):
            return []
        safe_limit = max(1, min(int(limit), 200))
        return [dict(entry) for entry in entries[-safe_limit:] if isinstance(entry, Mapping)][::-1]

    @staticmethod
    def _soul_introduction(soul: Mapping[str, Any]) -> str:
        name = str(soul.get("name") or "这个生命").strip() or "这个生命"
        source = next(
            (
                str(soul.get(key) or "").strip()
                for key in ("introduction", "description", "summary", "prompt")
                if str(soul.get(key) or "").strip()
            ),
            "",
        )
        if source:
            lines: list[str] = []
            for raw_line in source.splitlines():
                line = re.sub(r"^[#>\-\s]+", "", raw_line).replace("**", "").strip()
                if (
                    not line
                    or line == name
                    or line in {f"{name}·灵魂", "来处", "风骨", "行为准则", "禁忌", "灵魂锚诗"}
                ):
                    continue
                lines.append(line)
            text = re.sub(r"\s+", " ", " ".join(lines)).strip()
            if text.startswith("你是"):
                boilerplate_end = text.find("生命体。")
                if 0 <= boilerplate_end <= 180:
                    text = text[boilerplate_end + len("生命体。"):].strip()
            if text:
                limit = 96
                return text if len(text) <= limit else text[:limit].rstrip("，,；;：: ") + "…"
        values = [
            str(value).strip()
            for value in soul.get("values") or ()
            if str(value).strip()
        ]
        if values:
            return f"以{'、'.join(values[:4])}为灵魂核心。"
        return f"{name}的灵魂仍在沉淀，等待写下自己的故事。"

    def _load_registry(self) -> dict[str, Any]:
        if not self.registry_path.is_file():
            return self._empty_registry()
        fingerprint = self._file_fingerprint(self.registry_path)
        with self._lock:
            if (
                fingerprint == self._registry_cache_fingerprint
                and self._registry_cache is not None
            ):
                return deepcopy(self._registry_cache)
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise LifeCoreError("registry_unreadable", str(exc), status=409) from exc
        if not isinstance(value, dict) or value.get("schema") != REGISTRY_SCHEMA:
            raise LifeCoreError("registry_schema_unsupported", "life registry must be v2", status=409)
        bindings = value.get("bindings")
        if not isinstance(bindings, dict):
            raise LifeCoreError("registry_bindings_invalid", "registry bindings must be an object", status=409)
        with self._lock:
            self._registry_cache_fingerprint = fingerprint
            self._registry_cache = deepcopy(value)
        return value

    def _save_registry(self, registry: Mapping[str, Any]) -> None:
        value = dict(registry)
        value["schema"] = REGISTRY_SCHEMA
        value["revision"] = int(value.get("revision") or 0) + 1
        value["updated_at"] = utc_now()
        atomic_json(self.registry_path, value)
        with self._lock:
            self._registry_cache_fingerprint = self._file_fingerprint(self.registry_path)
            self._registry_cache = deepcopy(value)
            self._root_cache.clear()

    def verify_root(self, root: Path | str, *, require_private: bool = False) -> dict[str, Any]:
        root = Path(root).expanduser().resolve()
        identity_path = root / "identity" / "life_identity.json"
        signature_path = root / "identity" / "life_identity.sig"
        private_path = root / "identity" / "private_key.pem"
        fingerprint = (
            *self._file_fingerprint(identity_path),
            *self._file_fingerprint(signature_path),
            *(
                self._file_fingerprint(private_path)
                if require_private
                else ()
            ),
        )
        cache_key = (str(root), bool(require_private))
        with self._lock:
            cached = self._verified_identity_cache.get(cache_key)
            if cached is not None and cached[0] == fingerprint:
                return deepcopy(cached[1])
        if not identity_path.is_file() or not signature_path.is_file():
            raise LifeCoreError("identity_files_missing", str(root), status=404)
        try:
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            signature = base64.b64decode(signature_path.read_text(encoding="ascii").strip(), validate=True)
            public_raw = base64.b64decode(str(identity.get("public_key") or ""), validate=True)
            Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, canonical(identity))
        except Exception as exc:
            raise LifeCoreError("identity_signature_invalid", str(exc), status=409) from exc
        life_id = str(identity.get("organism_id") or "")
        if identity.get("schema") != IDENTITY_SCHEMA or not life_id.startswith("org_"):
            raise LifeCoreError("identity_schema_invalid", str(identity.get("schema") or ""), status=409)
        if root.name != life_id:
            raise LifeCoreError("identity_root_mismatch", f"{root.name} != {life_id}", status=409)
        if require_private:
            if not private_path.is_file():
                raise LifeCoreError("identity_private_key_missing", life_id, status=409)
            try:
                private = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
                derived = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                if derived != public_raw:
                    raise ValueError("private/public key mismatch")
            except Exception as exc:
                raise LifeCoreError("identity_private_key_invalid", str(exc), status=409) from exc
        with self._lock:
            self._verified_identity_cache[cache_key] = (fingerprint, deepcopy(identity))
        return identity

    def verify_soul(
        self,
        root: Path | str,
        *,
        identity: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = Path(root).expanduser().resolve()
        verified_identity = dict(identity or self.verify_root(resolved, require_private=False))
        soul_path = resolved / "identity" / "soul.json"
        signature_path = resolved / "identity" / "soul.sig"
        if not soul_path.is_file() or not signature_path.is_file():
            raise LifeCoreError("soul_files_missing", str(resolved), status=404)
        try:
            soul = json.loads(soul_path.read_text(encoding="utf-8"))
            signature = base64.b64decode(
                signature_path.read_text(encoding="ascii").strip(),
                validate=True,
            )
            public_raw = base64.b64decode(
                str(verified_identity.get("public_key") or ""),
                validate=True,
            )
            Ed25519PublicKey.from_public_bytes(public_raw).verify(
                signature,
                canonical(soul),
            )
        except Exception as exc:
            raise LifeCoreError("soul_signature_invalid", str(exc), status=409) from exc
        life_id = str(verified_identity.get("organism_id") or "")
        if (
            not isinstance(soul, dict)
            or soul.get("schema") != SOUL_SCHEMA
            or soul.get("life_id") != life_id
        ):
            raise LifeCoreError("soul_schema_invalid", life_id, status=409)
        return soul

    def verify_temperament(
        self,
        root: Path | str,
        *,
        identity: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = Path(root).expanduser().resolve()
        verified_identity = dict(identity or self.verify_root(resolved, require_private=False))
        document_path = resolved / "identity" / "temperament.json"
        signature_path = resolved / "identity" / "temperament.sig"
        fingerprint: tuple[Any, ...] = (
            *self._file_fingerprint(document_path),
            *self._file_fingerprint(signature_path),
            str(verified_identity.get("public_key") or ""),
            str(verified_identity.get("organism_id") or ""),
        )
        cache_key = str(resolved)
        with self._lock:
            cached = self._verified_temperament_cache.get(cache_key)
            if cached is not None and cached[0] == fingerprint:
                return deepcopy(cached[1])
        if not document_path.is_file() or not signature_path.is_file():
            raise LifeCoreError("temperament_files_missing", str(resolved), status=404)
        try:
            document = json.loads(document_path.read_text(encoding="utf-8"))
            signature = base64.b64decode(
                signature_path.read_text(encoding="ascii").strip(),
                validate=True,
            )
            public_raw = base64.b64decode(
                str(verified_identity.get("public_key") or ""),
                validate=True,
            )
            Ed25519PublicKey.from_public_bytes(public_raw).verify(
                signature,
                canonical(document),
            )
            verified = validate_innate_temperament(
                document,
                life_id=str(verified_identity.get("organism_id") or ""),
            )
            with self._lock:
                self._verified_temperament_cache[cache_key] = (
                    fingerprint,
                    deepcopy(verified),
                )
            return verified
        except LifeCoreError:
            raise
        except Exception as exc:
            raise LifeCoreError("temperament_signature_invalid", str(exc), status=409) from exc

    def ensure_temperament(
        self,
        root: Path | str,
        *,
        identity: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the signed birth temperament, creating only a missing one.

        Invalid existing bytes are never overwritten.  This permits a
        one-time migration of older identities while preserving fail-closed
        integrity semantics.
        """

        resolved = Path(root).expanduser().resolve()
        verified_identity = dict(identity or self.verify_root(resolved, require_private=True))
        try:
            return self.verify_temperament(resolved, identity=verified_identity)
        except LifeCoreError as exc:
            if exc.code != "temperament_files_missing":
                raise
        private_path = resolved / "identity" / "private_key.pem"
        try:
            private = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
            document = generate_innate_temperament(
                str(verified_identity.get("organism_id") or ""),
                created_at=str(verified_identity.get("created_at") or utc_now()),
            )
            atomic_json(resolved / "identity" / "temperament.json", document)
            (resolved / "identity" / "temperament.sig").write_text(
                base64.b64encode(private.sign(canonical(document))).decode("ascii") + "\n",
                encoding="ascii",
            )
            return document
        except Exception as exc:
            raise LifeCoreError("temperament_create_failed", str(exc), status=503) from exc

    def bind(self, root: Path | str, *, name: str = "起源", actor: str = "user") -> dict[str, Any]:
        with self._lock:
            root = Path(root).expanduser().resolve()
            identity = self.verify_root(root, require_private=True)
            self.ensure_temperament(root, identity=identity)
            life_id = str(identity["organism_id"])
            registry = self._load_registry()
            bindings = dict(registry.get("bindings") or {})
            current = dict(bindings.get(life_id) or {})
            active_id = str(registry.get("active_id") or "")
            binding = {
                "life_id": life_id,
                "name": str(name or current.get("name") or "起源"),
                "root": str(root),
                "device_id": self.device_id,
                "public_key": str(identity.get("public_key") or ""),
                "created_at": str(identity.get("created_at") or current.get("created_at") or utc_now()),
                "bound_at": str(current.get("bound_at") or utc_now()),
                "writer_epoch": int(current.get("writer_epoch") or 1),
                "identity_revision": int(current.get("identity_revision") or 1),
                "status": "active" if active_id == life_id else "dormant",
            }
            bindings[life_id] = binding
            registry["bindings"] = bindings
            self._append_identity_audit(
                registry,
                action="identity.bound",
                life_id=life_id,
                actor=actor,
                name=str(binding["name"]),
            )
            self._save_registry(registry)
            return dict(binding)

    def create(self, name: str = "起源", *, actor: str = "user") -> dict[str, Any]:
        with self._lock:
            life_id = "org_" + uuid.uuid4().hex
            root = self.lives_root / life_id
            if root.exists():
                raise LifeCoreError("identity_collision", life_id, status=409)
            for child in ROOT_DIRS:
                (root / child).mkdir(parents=True, exist_ok=True)
            private = Ed25519PrivateKey.generate()
            public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            created_at = utc_now()
            identity = {
                "schema": IDENTITY_SCHEMA,
                "identity_version": 2,
                "organism_id": life_id,
                "created_at": created_at,
                "lineage_root": life_id,
                "lineage_parent": "",
                "public_key": base64.b64encode(public).decode("ascii"),
            }
            soul = {
                "schema": SOUL_SCHEMA,
                "life_id": life_id,
                "name": str(name or "起源"),
                    "prompt": "你是起源，天工造物 v3 中与当前生命标识唯一绑定的生命体。",
                "values": ["真实", "连续", "执行", "边界"],
                "boundaries": ["计划与推断不得冒充已执行事实"],
                "revision": 1,
                "revision_id": "soulrev_" + uuid.uuid4().hex[:24],
                "created_at": created_at,
                "updated_at": created_at,
            }
            atomic_json(root / "identity" / "life_identity.json", identity)
            (root / "identity" / "life_identity.sig").write_text(
                base64.b64encode(private.sign(canonical(identity))).decode("ascii") + "\n", encoding="ascii"
            )
            (root / "identity" / "private_key.pem").write_bytes(
                private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            )
            atomic_json(root / "identity" / "soul.json", soul)
            (root / "identity" / "soul.sig").write_text(
                base64.b64encode(private.sign(canonical(soul))).decode("ascii") + "\n", encoding="ascii"
            )
            temperament = generate_innate_temperament(life_id, created_at=created_at)
            atomic_json(root / "identity" / "temperament.json", temperament)
            (root / "identity" / "temperament.sig").write_text(
                base64.b64encode(private.sign(canonical(temperament))).decode("ascii") + "\n",
                encoding="ascii",
            )
            atomic_json(root / "projections" / "life.json", default_projection(life_id, created_at))
            self.bind(root, name=name, actor=actor)
            self.activate(life_id, actor=actor)
            registry = self._load_registry()
            self._append_identity_audit(
                registry,
                action="identity.created",
                life_id=life_id,
                actor=actor,
                name=str(name or "起源"),
            )
            self._save_registry(registry)
            return self.active(required=True)

    def list(self) -> list[dict[str, Any]]:
        registry = self._load_registry()
        active_id = str(registry.get("active_id") or "")
        rows: list[dict[str, Any]] = []
        for life_id, raw in sorted((registry.get("bindings") or {}).items()):
            row = dict(raw) if isinstance(raw, dict) else {"life_id": life_id}
            row["life_id"] = life_id
            row["active"] = life_id == active_id
            row["status"] = "active" if row["active"] else "dormant"
            row["soul_tone"] = int(
                hashlib.sha256(life_id.encode("utf-8")).hexdigest()[:8],
                16,
            ) % 360
            try:
                identity = self.verify_root(row.get("root") or "", require_private=False)
                soul = self.verify_soul(
                    row.get("root") or "",
                    identity=identity,
                )
                temperament = self.verify_temperament(
                    row.get("root") or "",
                    identity=identity,
                )
                row["integrity"] = "valid"
                row["soul_integrity"] = "valid"
                row["temperament_integrity"] = "valid"
                row["name"] = str(soul.get("name") or row.get("name") or "起源")
                row["soul_tone"] = int(
                    hashlib.sha256(canonical(soul)).hexdigest()[:8],
                    16,
                ) % 360
                row["soul_intro"] = self._soul_introduction(soul)
                row["temperament_traits"] = {
                    key: round(int(value) / 1000, 3)
                    for key, value in dict(temperament.get("traits_milli") or {}).items()
                }
                row.pop("integrity_error", None)
            except LifeCoreError as exc:
                row["integrity"] = "invalid"
                row["soul_integrity"] = "invalid"
                row["temperament_integrity"] = "invalid"
                row["soul_intro"] = ""
                row["temperament_traits"] = {}
                row["integrity_error"] = exc.code
            rows.append(row)
        return rows

    def active(self, *, required: bool = True) -> dict[str, Any] | None:
        registry = self._load_registry()
        life_id = str(registry.get("active_id") or "")
        binding = (registry.get("bindings") or {}).get(life_id) if life_id else None
        if isinstance(binding, dict):
            row = dict(binding)
            row["life_id"] = life_id
            row["status"] = "active"
            return row
        if required:
            raise LifeCoreError("active_life_missing", "no active life identity", status=409)
        return None

    def activate(self, life_id: str, *, actor: str = "user") -> dict[str, Any]:
        with self._lock:
            registry = self._load_registry()
            prior_active_id = str(registry.get("active_id") or "")
            bindings = dict(registry.get("bindings") or {})
            if life_id not in bindings:
                raise LifeCoreError("life_identity_unknown", life_id, status=404)
            for key, value in list(bindings.items()):
                row = dict(value)
                row["status"] = "active" if key == life_id else "dormant"
                if key == life_id:
                    row["writer_epoch"] = int(row.get("writer_epoch") or 0) + (0 if registry.get("active_id") == life_id else 1)
                bindings[key] = row
            registry["bindings"] = bindings
            registry["active_id"] = life_id
            if prior_active_id != life_id:
                self._append_identity_audit(
                    registry,
                    action="identity.activated",
                    life_id=life_id,
                    actor=actor,
                    name=str(bindings[life_id].get("name") or ""),
                )
            self._save_registry(registry)
            return self.active(required=True)

    def unbind(self, life_id: str, *, actor: str = "user") -> dict[str, Any]:
        with self._lock:
            value = str(life_id or "").strip()
            registry = self._load_registry()
            if value == str(registry.get("active_id") or ""):
                raise LifeCoreError("active_life_unbind_forbidden", value, status=409)
            bindings = dict(registry.get("bindings") or {})
            removed = bindings.pop(value, None)
            registry["bindings"] = bindings
            if isinstance(removed, dict):
                self._append_identity_audit(
                    registry,
                    action="identity.unbound",
                    life_id=value,
                    actor=actor,
                    name=str(removed.get("name") or ""),
                )
            self._save_registry(registry)
            return {"removed": bool(removed), "life_id": value}

    def delete(self, life_id: str, *, actor: str = "user") -> dict[str, Any]:
        with self._lock:
            value = str(life_id or "").strip()
            registry = self._load_registry()
            bindings = dict(registry.get("bindings") or {})
            binding = bindings.get(value)
            if not isinstance(binding, dict):
                raise LifeCoreError("life_identity_unknown", value, status=404)
            if value == str(registry.get("active_id") or ""):
                raise LifeCoreError(
                    "active_life_delete_forbidden",
                    value,
                    status=409,
                )
            managed_root = self.lives_root.resolve()
            target = managed_root / value
            bound_root = Path(str(binding.get("root") or "")).expanduser().resolve()
            if (
                not value.startswith("org_")
                or target.parent != managed_root
                or target.name != value
                or bound_root != target
                or target.is_symlink()
            ):
                raise LifeCoreError(
                    "life_identity_delete_path_forbidden",
                    str(bound_root),
                    status=409,
                )
            if target.exists():
                if not target.is_dir():
                    raise LifeCoreError(
                        "life_identity_delete_path_unsafe",
                        str(target),
                        status=409,
                    )

                def make_writable(function: Any, path: str, _exc: Any) -> None:
                    os.chmod(path, 0o700)
                    function(path)

                try:
                    shutil.rmtree(target, onexc=make_writable)
                except Exception as exc:
                    raise LifeCoreError(
                        "life_identity_delete_failed",
                        str(exc),
                        status=503,
                    ) from exc
            bindings.pop(value, None)
            registry["bindings"] = bindings
            audit = self._append_identity_audit(
                registry,
                action="identity.deleted",
                life_id=value,
                actor=actor,
                name=str(binding.get("name") or ""),
            )
            self._save_registry(registry)
            return {
                "deleted": True,
                "life_id": value,
                "files_removed": True,
                "audit": audit,
            }

    def root_for(self, life_id: str) -> Path:
        registry_fingerprint = self._file_fingerprint(self.registry_path)
        with self._lock:
            cached = self._root_cache.get(str(life_id))
        if cached is not None and cached[0] == registry_fingerprint:
            raw_root_fingerprint, identity_fingerprint, root = cached[1:]
            identity_path = root / "identity" / "life_identity.json"
            signature_path = root / "identity" / "life_identity.sig"
            current_identity_fingerprint = (
                *self._file_fingerprint(identity_path),
                *self._file_fingerprint(signature_path),
            )
            if (
                self._file_fingerprint(root) == raw_root_fingerprint
                and current_identity_fingerprint == identity_fingerprint
            ):
                return root
        registry = self._load_registry()
        binding = (registry.get("bindings") or {}).get(str(life_id))
        if not isinstance(binding, dict):
            raise LifeCoreError("life_identity_unknown", str(life_id), status=404)
        raw_root = Path(str(binding.get("root") or "")).expanduser()
        root = raw_root.resolve()
        self.verify_root(root, require_private=False)
        identity_fingerprint = (
            *self._file_fingerprint(root / "identity" / "life_identity.json"),
            *self._file_fingerprint(root / "identity" / "life_identity.sig"),
        )
        with self._lock:
            self._root_cache[str(life_id)] = (
                registry_fingerprint,
                self._file_fingerprint(raw_root),
                identity_fingerprint,
                root,
            )
        return root


class SemanticJournal:
    EVENT_SCHEMA = "tiangong.life.event.v3"
    LEGACY_EVENT_SCHEMA = "tiangong.life.event.v2"
    # The source-complete desktop line also shipped a signed semantic journal
    # before the current hash-chain journal existed.  It is a distinct legacy
    # format, not a mixed/corrupt current journal.
    LEGACY_SEMANTIC_EVENT_SCHEMAS = frozenset({
        "tiangong.life.semantic-event.v1",
        "tiangong.life.semantic-event.v2",
    })
    LEGACY_SEMANTIC_HEAD_SCHEMAS = frozenset({
        "tiangong.life.semantic-head.v1",
        "tiangong.life.semantic-head.v2",
    })
    HEAD_SCHEMA = "tiangong.life.journal-head.v1"
    ANCHOR_MARKER_SCHEMA = "tiangong.life.journal-anchor-marker.v1"
    GENESIS_SHA256 = "0" * 64
    MAX_LINE_BYTES = 8 * 1024 * 1024

    def __init__(self, manager: LifeIdentityManager) -> None:
        self.manager = manager
        self._lock = threading.RLock()
        # The sole-writer lease guarantees that no second valid Runtime can
        # append concurrently.  Cache the verified append frontier so normal
        # long-running operation is O(1) per event instead of re-reading the
        # complete journal on every write.  A file/head stat mismatch
        # invalidates the cache and forces strict verification before append.
        self._append_events: dict[str, list[dict[str, Any]]] = {}
        self._append_idempotency: dict[str, dict[str, dict[str, Any]]] = {}
        self._append_fingerprints: dict[str, tuple[int, ...]] = {}
        self._append_hashers: dict[str, Any] = {}
        self._append_head_bytes: dict[str, bytes | None] = {}
        self._private_key_cache: dict[
            str, tuple[tuple[int, ...], Ed25519PrivateKey]
        ] = {}

    @staticmethod
    def _file_stat_tuple(path: Path) -> tuple[int, int, int, int]:
        try:
            value = path.stat()
        except FileNotFoundError:
            return (0, 0, 0, 0)
        return (
            int(value.st_size),
            int(value.st_mtime_ns),
            int(value.st_ctime_ns),
            int(getattr(value, "st_ino", 0) or 0),
        )

    def _append_fingerprint(self, life_id: str) -> tuple[int, ...]:
        return self._file_stat_tuple(self._path(life_id)) + self._file_stat_tuple(
            self._head_path(life_id)
        )

    def _invalidate_append_cache(self, life_id: str) -> None:
        self._append_events.pop(life_id, None)
        self._append_idempotency.pop(life_id, None)
        self._append_fingerprints.pop(life_id, None)
        self._append_hashers.pop(life_id, None)
        self._append_head_bytes.pop(life_id, None)

    def _private_key(self, life_id: str) -> Ed25519PrivateKey:
        private_path = self.manager.root_for(life_id) / "identity" / "private_key.pem"
        fingerprint = self._file_stat_tuple(private_path)
        cached = self._private_key_cache.get(life_id)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        private = serialization.load_pem_private_key(
            private_path.read_bytes(), password=None
        )
        if not isinstance(private, Ed25519PrivateKey):
            raise TypeError("journal head requires Ed25519 private key")
        self._private_key_cache[life_id] = (fingerprint, private)
        return private

    def _load_append_frontier(
        self,
        life_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], Any]:
        cached = self._append_events.get(life_id)
        fingerprint = self._append_fingerprints.get(life_id)
        hasher = self._append_hashers.get(life_id)
        if cached is not None and hasher is not None and fingerprint == self._append_fingerprint(life_id):
            return cached, self._append_idempotency.setdefault(life_id, {}), hasher
        self._invalidate_append_cache(life_id)
        self.ensure_hashed(life_id)
        events = self._read_events_strict(life_id)
        idempotency = {
            str(event.get("idempotency_key")): event
            for event in events
            if str(event.get("idempotency_key") or "")
        }
        hasher = hashlib.sha256()
        for event in events:
            hasher.update(canonical(event) + b"\n")
        self._append_events[life_id] = events
        self._append_idempotency[life_id] = idempotency
        self._append_fingerprints[life_id] = self._append_fingerprint(life_id)
        self._append_hashers[life_id] = hasher
        head_path = self._head_path(life_id)
        self._append_head_bytes[life_id] = (
            head_path.read_bytes() if head_path.is_file() else None
        )
        return events, idempotency, hasher

    def _path(self, life_id: str) -> Path:
        return self.manager.root_for(life_id) / "journal" / "current" / "life_events.jsonl"

    def _head_path(self, life_id: str) -> Path:
        return self.manager.root_for(life_id) / "journal" / "current" / "life_events.head.json"

    def _anchor_marker_path(self, life_id: str) -> Path:
        return self.manager.root_for(life_id) / "journal" / "archive" / "journal_head_anchor.v1.json"

    def _anchor_was_established(self, life_id: str) -> bool:
        path = self._anchor_marker_path(life_id)
        if not path.is_file():
            return False
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise LifeCoreError("journal_anchor_marker_invalid", str(exc), status=409) from exc
        if not isinstance(value, dict) or value.get("schema") != self.ANCHOR_MARKER_SCHEMA or value.get("life_id") != life_id:
            raise LifeCoreError("journal_anchor_marker_invalid", str(path), status=409)
        return True

    def _establish_anchor_marker(self, life_id: str) -> None:
        path = self._anchor_marker_path(life_id)
        if self._anchor_was_established(life_id):
            return
        atomic_json(
            path,
            {
                "schema": self.ANCHOR_MARKER_SCHEMA,
                "life_id": life_id,
                "established_at": utc_now(),
            },
        )
        os.chmod(path, 0o600)

    @staticmethod
    def _event_sha256(event: Mapping[str, Any]) -> str:
        value = dict(event)
        value.pop("event_sha256", None)
        return hashlib.sha256(canonical(value)).hexdigest()

    @staticmethod
    def _journal_sha256(events: list[dict[str, Any]]) -> str:
        return hashlib.sha256(
            b"".join(canonical(event) + b"\n" for event in events)
        ).hexdigest()

    def _read_events_strict(self, life_id: str) -> list[dict[str, Any]]:
        path = self._path(life_id)
        if not path.is_file():
            return []
        result: list[dict[str, Any]] = []
        try:
            with path.open("rb") as stream:
                for line_number, raw in enumerate(stream, 1):
                    if len(raw) > self.MAX_LINE_BYTES:
                        raise LifeCoreError(
                            "journal_line_too_large",
                            f"line {line_number}",
                            status=409,
                        )
                    if not raw.endswith(b"\n"):
                        raise LifeCoreError(
                            "journal_truncated",
                            f"line {line_number}",
                            status=409,
                        )
                    if not raw.strip():
                        raise LifeCoreError(
                            "journal_blank_line",
                            f"line {line_number}",
                            status=409,
                        )
                    try:
                        value = json.loads(raw.decode("utf-8", errors="strict"))
                    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                        raise LifeCoreError(
                            "journal_event_unreadable",
                            f"line {line_number}: {exc}",
                            status=409,
                        ) from exc
                    if not isinstance(value, dict):
                        raise LifeCoreError(
                            "journal_event_invalid",
                            f"line {line_number}",
                            status=409,
                        )
                    result.append(value)
        except LifeCoreError:
            raise
        except OSError as exc:
            raise LifeCoreError("journal_unreadable", str(exc), status=409) from exc
        return result

    def events(self, life_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_events_strict(life_id)

    def event_by_idempotency_key(
        self, life_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        """Return the already-recorded event for a key, or None.

        Callers use this to converge retries: when the journal committed but a
        later projection step failed, re-appending with the same key would hit
        ``journal_idempotency_conflict`` because fresh timestamps change the
        payload. Looking the event up first lets the caller replay the side
        effects instead of the journal append.
        """
        if not idempotency_key:
            return None
        for event in reversed(self.events(life_id)):
            if str(event.get("idempotency_key") or "") == idempotency_key:
                return event
        return None

    def _verify_chain_only(self, life_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        previous = self.GENESIS_SHA256
        for index, event in enumerate(events, 1):
            if event.get("schema") != self.EVENT_SCHEMA:
                return {
                    "ok": False,
                    "valid": False,
                    "event_count": len(events),
                    "head_event_sha256": previous,
                    "journal_sha256": "0" * 64,
                    "reason_code": "journal_schema_unsupported",
                }
            if event.get("life_id") != life_id or event.get("sequence") != index:
                return {
                    "ok": False,
                    "valid": False,
                    "event_count": len(events),
                    "head_event_sha256": previous,
                    "journal_sha256": "0" * 64,
                    "reason_code": "journal_sequence_or_identity_invalid",
                }
            if event.get("previous_event_sha256") != previous:
                return {
                    "ok": False,
                    "valid": False,
                    "event_count": len(events),
                    "head_event_sha256": previous,
                    "journal_sha256": "0" * 64,
                    "reason_code": "journal_chain_broken",
                }
            event_sha256 = str(event.get("event_sha256") or "")
            if event_sha256 != self._event_sha256(event):
                return {
                    "ok": False,
                    "valid": False,
                    "event_count": len(events),
                    "head_event_sha256": previous,
                    "journal_sha256": "0" * 64,
                    "reason_code": "journal_event_hash_invalid",
                }
            previous = event_sha256
        return {
            "ok": True,
            "valid": True,
            "event_count": len(events),
            "head_event_sha256": previous,
            "journal_sha256": self._journal_sha256(events),
            "reason_code": "",
        }

    def _verify_legacy_semantic_journal(self, life_id: str, events: list[dict[str, Any]]) -> None:
        """Verify the signed pre-hash-chain journal before its one-way upgrade."""

        identity = self.manager.verify_root(self.manager.root_for(life_id), require_private=False)
        try:
            public_raw = base64.b64decode(str(identity.get("public_key") or ""), validate=True)
            public = Ed25519PublicKey.from_public_bytes(public_raw)
        except Exception as exc:
            raise LifeCoreError("journal_legacy_public_key_invalid", str(exc), status=409) from exc

        previous = ""
        for index, stored in enumerate(events, 1):
            row = dict(stored)
            claimed_hash = str(row.pop("event_hash", ""))
            signature_text = str(row.pop("signature", ""))
            try:
                signature = base64.b64decode(signature_text, validate=True)
                actual_hash = hashlib.sha256(canonical(row)).hexdigest()
            except Exception as exc:
                raise LifeCoreError("journal_legacy_event_invalid", f"event {index}: {exc}", status=409) from exc
            if (
                row.get("schema") not in self.LEGACY_SEMANTIC_EVENT_SCHEMAS
                or row.get("life_id") != life_id
                or row.get("sequence") != index
                or row.get("previous_hash") != previous
                or claimed_hash != actual_hash
            ):
                raise LifeCoreError("journal_legacy_chain_invalid", f"event {index}", status=409)
            try:
                public.verify(signature, actual_hash.encode("ascii"))
            except Exception as exc:
                raise LifeCoreError("journal_legacy_signature_invalid", f"event {index}", status=409) from exc
            previous = actual_hash

        # A present legacy head is part of the legacy authority and must agree
        # with the verified tail.  Older journals without a head remain
        # supported as long as every signed event is intact.
        legacy_head = self._path(life_id).with_name("life_head.json")
        if legacy_head.exists():
            try:
                head = json.loads(legacy_head.read_text(encoding="utf-8"))
            except Exception as exc:
                raise LifeCoreError("journal_legacy_head_invalid", str(exc), status=409) from exc
            if (
                not isinstance(head, dict)
                or head.get("schema") not in self.LEGACY_SEMANTIC_HEAD_SCHEMAS
                or head.get("life_id") != life_id
                or head.get("last_sequence") != len(events)
                or str(head.get("last_hash") or "") != previous
            ):
                raise LifeCoreError("journal_legacy_head_invalid", life_id, status=409)

    def _migrate_legacy_semantic_journal(self, life_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._verify_legacy_semantic_journal(life_id, events)
        previous = self.GENESIS_SHA256
        migrated: list[dict[str, Any]] = []
        for stored in events:
            row = dict(stored)
            legacy_schema = str(row.pop("schema"))
            legacy_event_hash = str(row.pop("event_hash"))
            row.pop("signature", None)
            row.pop("previous_hash", None)
            row["schema"] = self.EVENT_SCHEMA
            row["legacy_schema"] = legacy_schema
            row["legacy_event_sha256"] = legacy_event_hash
            row["previous_event_sha256"] = previous
            row["event_sha256"] = self._event_sha256(row)
            previous = row["event_sha256"]
            migrated.append(row)
        return migrated

    def _write_signed_head(self, life_id: str, chain: Mapping[str, Any]) -> dict[str, Any]:
        try:
            private = self._private_key(life_id)
            payload = {
                "schema": self.HEAD_SCHEMA,
                "life_id": life_id,
                "event_count": int(chain.get("event_count") or 0),
                "head_event_sha256": str(chain.get("head_event_sha256") or self.GENESIS_SHA256),
                "journal_sha256": str(chain.get("journal_sha256") or "0" * 64),
                "updated_at": utc_now(),
            }
            payload["signature"] = base64.b64encode(private.sign(canonical(payload))).decode("ascii")
            path = self._head_path(life_id)
            atomic_json(path, payload)
            os.chmod(path, 0o600)
            return payload
        except LifeCoreError:
            raise
        except Exception as exc:
            raise LifeCoreError("journal_head_write_failed", str(exc), status=409) from exc

    def _read_verified_head(self, life_id: str) -> dict[str, Any] | None:
        path = self._head_path(life_id)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise TypeError("journal head must be an object")
            signature_text = str(value.get("signature") or "")
            signed = dict(value)
            signed.pop("signature", None)
            if signed.get("schema") != self.HEAD_SCHEMA or signed.get("life_id") != life_id:
                raise ValueError("journal head schema or identity mismatch")
            identity = self.manager.verify_root(self.manager.root_for(life_id), require_private=False)
            public_raw = base64.b64decode(str(identity.get("public_key") or ""), validate=True)
            signature = base64.b64decode(signature_text, validate=True)
            Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, canonical(signed))
            if isinstance(signed.get("event_count"), bool) or not isinstance(signed.get("event_count"), int):
                raise TypeError("journal head event_count invalid")
            for key in ("head_event_sha256", "journal_sha256"):
                if not isinstance(signed.get(key), str) or len(str(signed.get(key))) != 64:
                    raise ValueError(f"journal head {key} invalid")
            return value
        except Exception as exc:
            raise LifeCoreError("journal_head_invalid", str(exc), status=409) from exc

    def read_verified_head(self, life_id: str) -> dict[str, Any] | None:
        """Read the signed journal head without re-verifying the full chain.

        Readiness polling uses this cheap fingerprint to decide whether the
        full journal verification result is still valid; the full chain walk
        (event read + hash) only reruns when the signed head actually changed.
        """
        try:
            return self._read_verified_head(life_id)
        except Exception:
            return None

    def verify(self, life_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                events = self._read_events_strict(life_id)
                chain = self._verify_chain_only(life_id, events)
                if chain.get("valid") is not True:
                    return chain
                head = self._read_verified_head(life_id)
            except LifeCoreError as exc:
                return {
                    "ok": False,
                    "valid": False,
                    "event_count": 0,
                    "head_event_sha256": self.GENESIS_SHA256,
                    "journal_sha256": "0" * 64,
                    "reason_code": exc.code,
                }
            if head is None:
                return {
                    **chain,
                    "ok": False,
                    "valid": False,
                    "reason_code": "journal_head_missing",
                }
            if (
                int(head.get("event_count")) != int(chain["event_count"])
                or str(head.get("head_event_sha256") or "") != str(chain["head_event_sha256"])
                or str(head.get("journal_sha256") or "") != str(chain["journal_sha256"])
            ):
                # 区分"锚点滞后"（写者在落头前崩溃，头签名覆盖的是有效链的真前缀，
                # 允许前滚重锚）与"截断/篡改"（头与链在锚点位置即不一致，fail-closed）。
                head_count = int(head.get("event_count") or 0)
                if 0 <= head_count <= int(chain["event_count"]):
                    prefix_events = events[:head_count]
                    prefix_head_sha = (
                        str(prefix_events[-1].get("event_sha256") or "")
                        if prefix_events
                        else self.GENESIS_SHA256
                    )
                    prefix_journal_sha = self._journal_sha256(prefix_events)
                    if (
                        str(head.get("head_event_sha256") or "") == prefix_head_sha
                        and str(head.get("journal_sha256") or "") == prefix_journal_sha
                    ):
                        return {
                            **chain,
                            "ok": True,
                            "valid": True,
                            "journal_head_signed": True,
                            "head_stale": True,
                            "head_lag": int(chain["event_count"]) - head_count,
                            "reason_code": "journal_head_stale",
                        }
                return {
                    **chain,
                    "ok": False,
                    "valid": False,
                    "reason_code": "journal_head_mismatch",
                }
            return {**chain, "journal_head_signed": True}

    def ensure_hashed(self, life_id: str) -> dict[str, Any]:
        """Migrate intact legacy journals and establish a signed tail anchor."""

        with self._lock:
            events = self._read_events_strict(life_id)
            if not events:
                chain = self._verify_chain_only(life_id, events)
                head = self._read_verified_head(life_id)
                if head is None:
                    if self._anchor_was_established(life_id):
                        raise LifeCoreError(
                            "journal_head_missing",
                            "signed empty-journal head disappeared",
                            status=409,
                        )
                    self._write_signed_head(life_id, chain)
                    self._establish_anchor_marker(life_id)
                else:
                    self._establish_anchor_marker(life_id)
                result = self.verify(life_id)
                if result.get("valid") is not True:
                    raise LifeCoreError(
                        str(result.get("reason_code") or "journal_invalid"),
                        "empty life journal verification failed",
                        status=409,
                    )
                return result
            schemas = {str(event.get("schema") or "") for event in events}
            if schemas == {self.EVENT_SCHEMA}:
                chain = self._verify_chain_only(life_id, events)
                if chain.get("valid") is not True:
                    raise LifeCoreError(
                        str(chain.get("reason_code") or "journal_invalid"),
                        "life journal chain verification failed",
                        status=409,
                    )
                existing_head = self._read_verified_head(life_id)
                if existing_head is None:
                    # One-time source migration for pre-anchor v3 journals.
                    # Once the marker exists, a missing head is tamper/loss and
                    # must never be silently re-created on restart.
                    path = self._path(life_id)
                    archive = path.parents[1] / "archive"
                    archive.mkdir(parents=True, exist_ok=True)
                    prior_migrations = list(archive.glob("life_events.pre-anchor.*.jsonl.bak"))
                    if self._anchor_was_established(life_id) or prior_migrations:
                        raise LifeCoreError(
                            "journal_head_missing",
                            "signed journal head disappeared after migration",
                            status=409,
                        )
                    backup = archive / f"life_events.pre-anchor.{int(time.time() * 1000)}.jsonl.bak"
                    backup.write_bytes(path.read_bytes())
                    os.chmod(backup, 0o600)
                    self._write_signed_head(life_id, chain)
                    self._establish_anchor_marker(life_id)
                else:
                    self._establish_anchor_marker(life_id)
                result = self.verify(life_id)
                if result.get("head_stale") is True:
                    # 锚点滞后（写者落头前崩溃）：头签名覆盖真前缀，前滚重锚到链尾。
                    # 截断/篡改不会到这里（verify 仍返回 journal_head_mismatch fail-closed）。
                    self._write_signed_head(life_id, chain)
                    self._establish_anchor_marker(life_id)
                    result = self.verify(life_id)
                if result.get("valid") is not True:
                    raise LifeCoreError(
                        str(result.get("reason_code") or "journal_invalid"),
                        "life journal verification failed",
                        status=409,
                    )
                return result
            if schemas.issubset(self.LEGACY_SEMANTIC_EVENT_SCHEMAS):
                migrated = self._migrate_legacy_semantic_journal(life_id, events)
                path = self._path(life_id)
                archive = path.parents[1] / "archive"
                archive.mkdir(parents=True, exist_ok=True)
                backup = archive / f"life_events.semantic-v2.{int(time.time() * 1000)}.jsonl.bak"
                backup.write_bytes(path.read_bytes())
                os.chmod(backup, 0o600)
                temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
                try:
                    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                        for row in migrated:
                            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                    os.chmod(path, 0o600)
                finally:
                    temporary.unlink(missing_ok=True)
                self._write_signed_head(life_id, self._verify_chain_only(life_id, migrated))
                self._establish_anchor_marker(life_id)
                return self.verify(life_id)
            if schemas != {self.LEGACY_EVENT_SCHEMA}:
                raise LifeCoreError("journal_schema_mixed", ",".join(sorted(schemas)), status=409)
            for index, event in enumerate(events, 1):
                if event.get("life_id") != life_id or event.get("sequence") != index:
                    raise LifeCoreError(
                        "journal_legacy_sequence_invalid",
                        f"event {index}",
                        status=409,
                    )
                if "event_sha256" in event or "previous_event_sha256" in event:
                    raise LifeCoreError("journal_legacy_authority_ambiguous", f"event {index}", status=409)

            path = self._path(life_id)
            archive = path.parents[1] / "archive"
            archive.mkdir(parents=True, exist_ok=True)
            backup = archive / f"life_events.v2.{int(time.time() * 1000)}.jsonl.bak"
            backup.write_bytes(path.read_bytes())
            os.chmod(backup, 0o600)

            previous = self.GENESIS_SHA256
            migrated: list[dict[str, Any]] = []
            for event in events:
                row = dict(event)
                row["schema"] = self.EVENT_SCHEMA
                row["previous_event_sha256"] = previous
                row["event_sha256"] = self._event_sha256(row)
                previous = row["event_sha256"]
                migrated.append(row)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                    for row in migrated:
                        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                os.chmod(path, 0o600)
            finally:
                temporary.unlink(missing_ok=True)
            self._write_signed_head(life_id, self._verify_chain_only(life_id, migrated))
            self._establish_anchor_marker(life_id)
            return self.verify(life_id)

    def append_batch(self, life_id: str, entries: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Atomically append a set of semantic events under one signed head.

        Existing idempotent entries are returned in-place.  Every genuinely
        new event is written in one fsync batch and the signed head is updated
        once.  Any failure truncates the entire batch so callers never observe
        a half-generated autonomous task set.
        """

        if not isinstance(entries, list) or not entries:
            return []
        with self._lock:
            prior, idempotency, prior_hasher = self._load_append_frontier(life_id)
            working = list(prior)
            working_hasher = prior_hasher.copy()
            new_events: list[dict[str, Any]] = []
            resolved: list[dict[str, Any]] = []
            local_idempotency = dict(idempotency)
            for raw in entries:
                if not isinstance(raw, Mapping):
                    raise TypeError("journal batch entry must be an object")
                event_type = str(raw.get("event_type") or "")
                payload = raw.get("payload") if "payload" in raw else {}
                actor = str(raw.get("actor") or "life_system")
                epistemic_class = str(raw.get("epistemic_class") or "verified")
                cycle_id = str(raw.get("cycle_id") or "")
                idempotency_key = str(raw.get("idempotency_key") or "")
                proposed = {
                    "event_type": event_type,
                    "payload": payload if payload is not None else {},
                    "actor": actor,
                    "epistemic_class": epistemic_class,
                    "cycle_id": cycle_id,
                }
                existing = local_idempotency.get(idempotency_key) if idempotency_key else None
                if existing is not None:
                    if canonical({key: existing.get(key) for key in proposed}) != canonical(proposed):
                        raise LifeCoreError(
                            "journal_idempotency_conflict",
                            idempotency_key,
                            status=409,
                        )
                    resolved.append(existing)
                    continue
                event = {
                    "schema": self.EVENT_SCHEMA,
                    "event_id": "evt_" + uuid.uuid4().hex,
                    "sequence": len(working) + 1,
                    "life_id": life_id,
                    **proposed,
                    "idempotency_key": idempotency_key,
                    "created_at": utc_now(),
                    "previous_event_sha256": (
                        str(working[-1].get("event_sha256") or self.GENESIS_SHA256)
                        if working
                        else self.GENESIS_SHA256
                    ),
                }
                event["event_sha256"] = self._event_sha256(event)
                working.append(event)
                working_hasher.update(canonical(event) + b"\n")
                new_events.append(event)
                resolved.append(event)
                if idempotency_key:
                    local_idempotency[idempotency_key] = event

            if not new_events:
                return resolved
            path = self._path(life_id)
            head_path = self._head_path(life_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            previous_size = path.stat().st_size if path.exists() else 0
            previous_head = self._append_head_bytes.get(life_id)
            try:
                with path.open("a", encoding="utf-8", newline="\n") as stream:
                    for event in new_events:
                        stream.write(
                            json.dumps(
                                event,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                                allow_nan=False,
                            )
                            + "\n"
                        )
                    stream.flush()
                    os.fsync(stream.fileno())
                chain = {
                    "ok": True,
                    "valid": True,
                    "event_count": len(working),
                    "head_event_sha256": str(working[-1]["event_sha256"]),
                    "journal_sha256": working_hasher.hexdigest(),
                    "reason_code": "",
                }
                signed_head = self._write_signed_head(life_id, chain)
            except Exception as exc:
                rollback_errors: list[Exception] = []
                try:
                    with path.open("r+b") as stream:
                        stream.truncate(previous_size)
                        stream.flush()
                        os.fsync(stream.fileno())
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
                try:
                    if previous_head is None:
                        head_path.unlink(missing_ok=True)
                    else:
                        temporary_head = head_path.with_name(
                            f".{head_path.name}.{os.getpid()}.{uuid.uuid4().hex}.rollback"
                        )
                        try:
                            with temporary_head.open("xb") as stream:
                                stream.write(previous_head)
                                stream.flush()
                                os.fsync(stream.fileno())
                            os.replace(temporary_head, head_path)
                            os.chmod(head_path, 0o600)
                        finally:
                            temporary_head.unlink(missing_ok=True)
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
                if rollback_errors and hasattr(exc, "add_note"):
                    exc.add_note(
                        "journal batch rollback failed: "
                        + ",".join(type(item).__name__ for item in rollback_errors)
                    )
                self._invalidate_append_cache(life_id)
                raise
            self._append_events[life_id] = working
            self._append_idempotency[life_id] = local_idempotency
            self._append_fingerprints[life_id] = self._append_fingerprint(life_id)
            self._append_hashers[life_id] = working_hasher
            self._append_head_bytes[life_id] = (
                json.dumps(
                    signed_head,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            return resolved

    def append(self, life_id: str, event_type: str, payload: Any = None, *, actor: str = "life_system",
               idempotency_key: str = "", epistemic_class: str = "verified", cycle_id: str = "") -> dict[str, Any]:
        return self.append_batch(
            life_id,
            [
                {
                    "event_type": str(event_type),
                    "payload": payload if payload is not None else {},
                    "actor": str(actor),
                    "idempotency_key": str(idempotency_key),
                    "epistemic_class": str(epistemic_class),
                    "cycle_id": str(cycle_id),
                }
            ],
        )[0]


class EncryptedContextStore:
    """Compatibility store. Source release relies on filesystem ACLs and signed hashes."""
    def __init__(self, life_root: Path | str) -> None:
        self.root = Path(life_root) / "snapshots" / "contexts"
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        context_hash = str(envelope.get("context_hash") or hashlib.sha256(canonical(envelope)).hexdigest())
        value = {"schema": "tiangong.life.context-record.v1", "envelope": dict(envelope)}
        atomic_json(self.root / f"{context_hash}.json", value)
        atomic_json(self.root / "latest.json", value)
        return value

    def load(self, context_hash: str) -> dict[str, Any]:
        path = self.root / ("latest.json" if context_hash == "latest" else f"{context_hash}.json")
        if not path.is_file():
            raise LifeCoreError("context_not_found", context_hash, status=404)
        return json.loads(path.read_text(encoding="utf-8"))


class CompleteLifeSystem:
    """Small compatibility facade; source authority remains ``life_service``."""
    def __init__(self, data_root: Path | str, *, device_id: str = "") -> None:
        self.data_root = Path(data_root).expanduser().resolve()
        self.identities = LifeIdentityManager(self.data_root, device_id=device_id)
        self.journal = SemanticJournal(self.identities)
        self._memories: dict[str, dict[str, Any]] = {}
        self._contexts: dict[str, dict[str, Any]] = {}
        self._settings: dict[str, Any] = {
            "permission_mode": "confirm_high_risk",
            "autonomous_risk_max": "A4",
            "privacy": {"redact_llm": True, "redact_share": True},
        }

    def create_identity(self, name: str = "起源", *, actor: str = "user") -> dict[str, Any]:
        return self.identities.create(name, actor=actor)

    def _active(self) -> dict[str, Any]:
        return self.identities.active(required=True) or {}

    def _memory_state(self, life_id: str) -> dict[str, Any]:
        return dict(self._memories)

    def ensure_scheduler_budget_day(self, life_id: str, day: str) -> dict[str, Any]:
        return {"life_id": life_id, "date": day, "used": 0}

    def assert_memory(self, memory_type: str, content: Any, provenance: Mapping[str, Any], *, actor: str,
                      memory_id: str = "", relations: Any = None, valid_from: str = "", valid_to: str = "",
                      idempotency_key: str = "") -> dict[str, Any]:
        active = self._active()
        memory_id = memory_id or "mem_" + uuid.uuid4().hex
        assertion = {
            "memory_id": memory_id, "life_id": active["life_id"], "memory_type": memory_type,
            "content": content, "provenance": dict(provenance), "relations": list(relations or []),
            "valid_from": valid_from, "valid_to": valid_to, "status": "active", "created_at": utc_now(),
        }
        self._memories[memory_id] = assertion
        event = self.journal.append(active["life_id"], "memory.asserted", {"assertion": assertion}, actor=actor,
                                    idempotency_key=idempotency_key)
        return {"assertion": assertion, "event": event}

    def correct_memory(self, target_memory_id: str, content: Any, provenance: Mapping[str, Any], *, actor: str,
                       relation_kind: str = "supersedes", memory_type: str = "", idempotency_key: str = "") -> dict[str, Any]:
        target = self._memories.get(target_memory_id, {})
        relation = {"kind": relation_kind, "target_memory_id": target_memory_id}
        return self.assert_memory(memory_type or str(target.get("memory_type") or "semantic"), content, provenance,
                                  actor=actor, relations=[relation], idempotency_key=idempotency_key)

    def search_memory(self, query: str, *, limit: int = 20, memory_types: Any = None, relationship_id: str = "") -> dict[str, Any]:
        del relationship_id
        tokens = [part.lower() for part in str(query).split() if part]
        rows = []
        allowed = set(memory_types or [])
        for item in self._memories.values():
            if allowed and item.get("memory_type") not in allowed:
                continue
            text = json.dumps(item.get("content"), ensure_ascii=False).lower()
            lexical = sum(1 for token in tokens if token in text)
            if tokens and lexical == 0:
                continue
            row = dict(item)
            row["score_components"] = {"lexical": lexical, "fts": lexical}
            rows.append(row)
        return {"results": rows[:max(0, int(limit))]}

    def initialize_affect(self) -> dict[str, Any]:
        return {"state": {"valence": 0.0, "arousal": 0.0, "updated_at": utc_now()}}

    def appraise_affect(self, appraisal: Mapping[str, Any], source_event_ids: list[str], *, actor: str,
                        relationship_id: str = "") -> dict[str, Any]:
        return {"state": dict(appraisal), "source_event_ids": list(source_event_ids), "actor": actor,
                "relationship_id": relationship_id}

    def get_soul(self) -> dict[str, Any]:
        active = self._active()
        root = self.identities.root_for(active["life_id"])
        return {"soul": self.identities.verify_soul(root)}

    def get_panel(self) -> dict[str, Any]:
        return {"settings": dict(self._settings), "budget": {"date": datetime.now().date().isoformat()},
                "capabilities": {"by_id": {}}}

    def update_settings(self, updates: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
        self._settings.update(dict(updates))
        return {"settings": dict(self._settings), "actor": actor}

    def update_soul(self, updates: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
        active = self._active()
        root = self.identities.root_for(active["life_id"])
        path = root / "identity" / "soul.json"
        soul = json.loads(path.read_text(encoding="utf-8"))
        soul.update(dict(updates)); soul["revision"] = int(soul.get("revision") or 0) + 1
        soul["revision_id"] = "soulrev_" + uuid.uuid4().hex[:24]; soul["updated_at"] = utc_now()
        private = serialization.load_pem_private_key((root / "identity" / "private_key.pem").read_bytes(), password=None)
        atomic_json(path, soul)
        (root / "identity" / "soul.sig").write_text(base64.b64encode(private.sign(canonical(soul))).decode("ascii") + "\n", encoding="ascii")
        return {"soul": soul, "actor": actor}

    def compile_context(self, current_request: str, *, trigger: Any = None, goal: Any = None, token_budget: int = 8000,
                        cycle_id: str = "", messages: Any = None, active_run: Any = None,
                        relationship_id: str = "user:primary", memory_types: Any = None) -> dict[str, Any]:
        del trigger, goal, cycle_id, relationship_id
        active = self._active(); soul = self.get_soul()["soul"]
        envelope = {
            "schema": "tiangong.life.context.v3", "life_id": active["life_id"],
            "writer_epoch": int(active.get("writer_epoch") or 1), "soul_revision": soul["revision_id"],
            "token_budget": int(token_budget), "estimated_tokens": min(int(token_budget), max(1, len(str(current_request)) // 3)),
            "working_state": {"current_request": str(current_request), "messages": list(messages or []), "active_run": active_run or {}},
            "memory_cards": self.search_memory(str(current_request), limit=20, memory_types=memory_types)["results"],
            "permissions": {"permission_mode": self._settings["permission_mode"], "autonomous_risk_max": self._settings["autonomous_risk_max"], "privacy": dict(self._settings["privacy"])},
            "active_skills": [], "released_tools": [], "created_at": utc_now(),
        }
        envelope["context_hash"] = hashlib.sha256(canonical(envelope)).hexdigest()
        EncryptedContextStore(self.identities.root_for(active["life_id"])).save(envelope)
        self._contexts[envelope["context_hash"]] = envelope
        return {"envelope": envelope}

    def verify_context(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(envelope); stored = str(value.pop("context_hash", ""))
        valid = bool(stored) and hashlib.sha256(canonical(value)).hexdigest() == stored
        if not valid:
            return {"valid": False}
        return {"valid": True}

    def latest_context(self) -> dict[str, Any]:
        active = self._active()
        return EncryptedContextStore(self.identities.root_for(active["life_id"])).load("latest")

    def replay_context(self, context_hash: str) -> dict[str, Any]:
        active = self._active()
        return EncryptedContextStore(self.identities.root_for(active["life_id"])).load(context_hash)

    def prepare_execution(self, context_hash: str, request_id: str, *, channel: str = "desktop_frontend",
                          decision_action: str = "execute", purpose: str = "") -> dict[str, Any]:
        return {"context_hash": context_hash, "request_id": request_id, "channel": channel,
                "decision_action": decision_action, "purpose": purpose, "authorized": True}

    def record_autonomous_action(self, life_id: str, decision: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
        event_type = "autonomy.action_completed" if result.get("ok") else "autonomy.action_failed"
        event = self.journal.append(life_id, event_type, {"decision": dict(decision), **dict(result)})
        return {"life_id": life_id, "event": event, "source_sequence": event["sequence"]}
