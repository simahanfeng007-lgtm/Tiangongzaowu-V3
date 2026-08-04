"""Authoritative managed-novel transaction engine for Tiangong v3.

The language model proposes prose and structured deltas.  This module owns the
canonical project graph, validates deterministic continuity constraints, issues
state-bound leases, and commits accepted chapters atomically.  It deliberately
uses only the Python standard library so the Windows desktop source runtime and
frozen backend share the same authority semantics.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, Iterable, Iterator, Mapping, MutableMapping, Sequence
import uuid


SYSTEM_VERSION = "3.0.3-reconstructed-authoritative"
BLUEPRINT_SECTIONS = (
    "story",
    "characters",
    "world",
    "calendar",
    "locations",
    "routes",
    "schedules",
    "progression_rules",
    "plot_events",
    "chapters",
    "relationships",
    "foreshadows",
    "emotional_accounts",
    "settings",
)
REQUIRED_SECTIONS = ("story", "characters", "world", "calendar", "locations", "plot_events", "chapters")
LIST_SECTIONS = frozenset(
    {
        "characters",
        "locations",
        "routes",
        "schedules",
        "progression_rules",
        "plot_events",
        "chapters",
        "relationships",
        "foreshadows",
        "emotional_accounts",
    }
)
OBJECT_SECTIONS = frozenset({"story", "world", "calendar", "settings"})
STATE_FIELDS = frozenset({"alive", "location", "realm", "injuries", "inventory", "knowledge"})
EVENT_STATUSES = frozenset({"progressed", "turned", "closed"})
REQUIRED_SCENE_SCORES = (
    "surprise",
    "retrospective_inevitability",
    "consequence",
    "character_relevance",
    "causality_support",
    "attachment",
    "agency",
    "irreversibility",
    "callbacks",
    "restraint",
)
_PLACEHOLDER_RE = re.compile(r"(?:TODO|TBD|待补|占位|这里写|未完待续\s*$)", re.IGNORECASE)
_SAFE_TITLE_RE = re.compile(r"[^\w\-\u4e00-\u9fff]+", re.UNICODE)
_GLOBAL_LOCKS: dict[str, threading.RLock] = {}
_GLOBAL_LOCKS_GUARD = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = _canonical_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def _state_hash(state: Mapping[str, Any]) -> str:
    material = dict(state)
    material.pop("state_hash", None)
    return _sha256({"domain": "tiangong.novel.state.v1", "state": material})


def _blueprint_hash(blueprint: Mapping[str, Any]) -> str:
    return _sha256({"domain": "tiangong.novel.blueprint.v1", "blueprint": blueprint})


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _count_cjk(text: str) -> int:
    return len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))


def _slug(text: str, fallback: str = "item") -> str:
    cleaned = _SAFE_TITLE_RE.sub("-", text.strip()).strip("-")
    return (cleaned[:48] or fallback).lower()


def _deep_merge(base: Any, changes: Any) -> Any:
    if isinstance(base, Mapping) and isinstance(changes, Mapping):
        merged = {str(key): deepcopy(value) for key, value in base.items()}
        for key, value in changes.items():
            current = merged.get(str(key))
            merged[str(key)] = _deep_merge(current, value) if isinstance(current, Mapping) and isinstance(value, Mapping) else deepcopy(value)
        return merged
    return deepcopy(changes)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NovelSystemError("CORRUPT_CANONICAL_FILE", f"Canonical JSON is invalid: {path.name}", details={"path": str(path)}) from exc


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except (AttributeError, OSError):
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")


def _atomic_text(path: Path, value: str) -> None:
    _atomic_bytes(path, value.encode("utf-8"))


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False)).casefold()
    with _GLOBAL_LOCKS_GUARD:
        return _GLOBAL_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _cross_process_lock(path: Path, timeout: float = 15.0) -> Iterator[None]:
    """Small cross-platform advisory lock; canonical writes still use os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        deadline = time.monotonic() + timeout
        locked = False
        while not locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    if stream.tell() == 0:
                        stream.write(b"0")
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise NovelSystemError("PROJECT_BUSY", "Novel project is locked by another transaction", retryable=True)
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


class NovelSystemError(RuntimeError):
    """Deterministic fail-closed error returned to the tool adapter."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.retryable = bool(retryable)

    def payload(self) -> dict[str, Any]:
        return {
            "success": False,
            "ok": False,
            "accepted": False,
            "status": self.code,
            "failure_class": "TOOL_DETERMINISTIC",
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class _Issue:
    code: str
    message: str
    path: str
    weight: int = 10
    repair: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {"code": self.code, "message": self.message, "path": self.path, "weight": self.weight}
        if self.repair:
            result["repair"] = dict(self.repair)
        return result


class NovelSystemEngine:
    """Own one managed novel project's canonical graph and chapter ledger."""

    def __init__(self, project_root: str | os.PathLike[str] | Path) -> None:
        self.root = Path(project_root).expanduser().resolve(strict=False)
        self.system = self.root / ".novel-system"
        self.manifest_path = self.system / "manifest.json"
        self.staged_path = self.system / "blueprints" / "staged.json"
        self.original_path = self.system / "blueprints" / "original.json"
        self.rolling_path = self.system / "blueprints" / "rolling.json"
        self.state_path = self.system / "state" / "current.json"
        self.ledger_path = self.system / "ledger" / "chapters.json"
        self.leases_dir = self.system / "leases"
        self.prepared_dir = self.system / "transactions" / "prepared"
        self.committed_dir = self.system / "transactions" / "committed"
        self.snapshots_dir = self.system / "snapshots"
        self._thread_lock = _thread_lock(self.system / "transaction.lock")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            with _cross_process_lock(self.system / "transaction.lock"):
                yield

    def _require_project(self) -> dict[str, Any]:
        if not self.manifest_path.is_file():
            raise NovelSystemError("NOVEL_PROJECT_NOT_FOUND", "Target is not a managed novel project", details={"project_root": str(self.root)})
        manifest = _read_json(self.manifest_path, {})
        if not isinstance(manifest, dict) or manifest.get("schema") != "tiangong.novel.manifest.v1":
            raise NovelSystemError("INVALID_PROJECT_MANIFEST", "Managed novel manifest is missing or incompatible")
        return manifest

    def _manifest(self) -> dict[str, Any]:
        return self._require_project()

    def _blueprint(self, *, rolling: bool = True) -> dict[str, Any]:
        path = self.rolling_path if rolling and self.rolling_path.is_file() else self.staged_path
        value = _read_json(path, {})
        if not isinstance(value, dict):
            raise NovelSystemError("INVALID_BLUEPRINT", "Canonical blueprint must be a JSON object")
        return value

    def _state(self) -> dict[str, Any]:
        value = _read_json(self.state_path, {})
        if not isinstance(value, dict):
            raise NovelSystemError("INVALID_NOVEL_STATE", "Canonical novel state must be a JSON object")
        if value and value.get("state_hash") != _state_hash(value):
            raise NovelSystemError("STATE_HASH_MISMATCH", "Canonical novel state failed integrity verification")
        return value

    def _ledger(self) -> list[dict[str, Any]]:
        value = _read_json(self.ledger_path, [])
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise NovelSystemError("INVALID_CHAPTER_LEDGER", "Chapter ledger must be an array of objects")
        return value

    def _check_revision(self, manifest: Mapping[str, Any], expected: Any) -> None:
        if expected is None:
            return
        actual = _safe_int(manifest.get("blueprint_revision"), 0)
        if _safe_int(expected, -1) != actual:
            raise NovelSystemError(
                "STALE_BLUEPRINT_REVISION",
                "Blueprint revision changed before this mutation",
                details={"expected_revision": expected, "actual_revision": actual},
                retryable=True,
            )

    def _write_manifest(self, manifest: MutableMapping[str, Any]) -> None:
        manifest["updated_at"] = _utc_now()
        _atomic_json(self.manifest_path, manifest)

    def _write_state(self, state: MutableMapping[str, Any]) -> None:
        state["state_hash"] = _state_hash(state)
        _atomic_json(self.state_path, state)

    def _snapshot_blueprint(self, revision: int, blueprint: Mapping[str, Any]) -> None:
        _atomic_json(self.snapshots_dir / f"blueprint-r{revision:06d}.json", blueprint)

    def _commit_blueprint(
        self,
        manifest: MutableMapping[str, Any],
        blueprint: MutableMapping[str, Any],
        *,
        target: str = "staged",
    ) -> int:
        revision = _safe_int(manifest.get("blueprint_revision"), 0) + 1
        manifest["blueprint_revision"] = revision
        manifest["blueprint_hash"] = _blueprint_hash(blueprint)
        path = self.rolling_path if target == "rolling" else self.staged_path
        _atomic_json(path, blueprint)
        self._snapshot_blueprint(revision, blueprint)
        self._write_manifest(manifest)
        return revision

    @staticmethod
    def _empty_blueprint(project: Mapping[str, Any]) -> dict[str, Any]:
        value: dict[str, Any] = {"schema": "tiangong.novel.blueprint.v1", "project": dict(project)}
        for section in BLUEPRINT_SECTIONS:
            value[section] = [] if section in LIST_SECTIONS else {}
        return value

    @staticmethod
    def _success(status: str, **values: Any) -> dict[str, Any]:
        return {"success": True, "ok": True, "status": status, **values}

    def create_project(self, args: Mapping[str, Any]) -> dict[str, Any]:
        title = str(args.get("title") or "").strip()
        genre = str(args.get("genre") or "").strip()
        planned = _safe_int(args.get("planned_chapters"), 0)
        target_words = _safe_int(args.get("target_words"), 0)
        if not title or not genre or planned < 1 or target_words < 1000:
            raise NovelSystemError("INVALID_PROJECT_ARGUMENTS", "title, genre, planned_chapters, and target_words are required")
        minimum = math.ceil(target_words / 5000)
        if planned < minimum:
            raise NovelSystemError(
                "INVALID_FULL_BOOK_PLAN",
                "planned_chapters must describe the full book",
                details={"planned_chapters": planned, "target_words": target_words, "minimum_chapters": minimum},
            )
        with self._thread_lock:
            if self.manifest_path.exists():
                raise NovelSystemError("NOVEL_PROJECT_ALREADY_EXISTS", "Managed novel project already exists", details={"project_root": str(self.root)})
            self.root.mkdir(parents=True, exist_ok=True)
            with _cross_process_lock(self.system / "transaction.lock"):
                project_id = f"novel_{_sha256(str(self.root) + title)[:16]}"
                project = {
                    "id": project_id,
                    "title": title,
                    "genre": genre,
                    "planned_chapters": planned,
                    "target_words": target_words,
                }
                manifest: dict[str, Any] = {
                    "schema": "tiangong.novel.manifest.v1",
                    "system_version": SYSTEM_VERSION,
                    "project_id": project_id,
                    "title": title,
                    "genre": genre,
                    "planned_chapters": planned,
                    "target_words": target_words,
                    "created_at": _utc_now(),
                    "updated_at": _utc_now(),
                    "blueprint_revision": 0,
                    "compiled": False,
                    "accepted_chapters": 0,
                }
                blueprint = self._empty_blueprint(project)
                manifest["blueprint_hash"] = _blueprint_hash(blueprint)
                for directory in (
                    self.staged_path.parent,
                    self.state_path.parent,
                    self.ledger_path.parent,
                    self.leases_dir,
                    self.prepared_dir,
                    self.committed_dir,
                    self.snapshots_dir,
                    self.root / "正文",
                ):
                    directory.mkdir(parents=True, exist_ok=True)
                _atomic_json(self.staged_path, blueprint)
                _atomic_json(self.ledger_path, [])
                self._write_manifest(manifest)
                self._snapshot_blueprint(0, blueprint)
        return self._success(
            "NOVEL_PROJECT_CREATED",
            project_root=str(self.root),
            project_id=project_id,
            manifest=manifest,
            next_action="novel.blueprint.update",
        )

    def status(self) -> dict[str, Any]:
        manifest = self._manifest()
        blueprint = self._blueprint()
        state = self._state() if manifest.get("compiled") else {}
        ledger = self._ledger()
        prepared = sorted(path.stem for path in self.prepared_dir.glob("*.json")) if self.prepared_dir.is_dir() else []
        leases = []
        now = time.time()
        if self.leases_dir.is_dir():
            for path in sorted(self.leases_dir.glob("*.json")):
                lease = _read_json(path, {})
                if isinstance(lease, dict) and float(lease.get("expires_at_epoch") or 0) > now:
                    leases.append({key: lease.get(key) for key in ("lease_id", "chapter_number", "expires_at")})
        open_events = []
        pending_triggers = []
        if state:
            open_events = [value for value in (state.get("events") or {}).values() if isinstance(value, dict) and value.get("status") != "closed"]
            pending_triggers = [value for value in (state.get("emotional_triggers") or {}).values() if isinstance(value, dict) and value.get("status") == "pending"]
        return self._success(
            "NOVEL_PROJECT_STATUS",
            project_root=str(self.root),
            manifest=manifest,
            blueprint_revision=_safe_int(manifest.get("blueprint_revision"), 0),
            blueprint_hash=_blueprint_hash(blueprint),
            compiled=bool(manifest.get("compiled")),
            next_chapter=_safe_int(state.get("next_chapter"), 1) if state else 1,
            state_hash=state.get("state_hash") if state else None,
            accepted_chapters=len(ledger),
            open_events=open_events,
            pending_emotional_triggers=pending_triggers,
            active_leases=leases,
            prepared_transactions=prepared,
            recovery_required=bool(prepared),
            complete=bool(state and _safe_int(state.get("next_chapter"), 1) > _safe_int(manifest.get("planned_chapters"), 0)),
        )

    def update_blueprint(self, args: Mapping[str, Any]) -> dict[str, Any]:
        section = str(args.get("section") or "")
        data = deepcopy(args.get("data"))
        if section not in BLUEPRINT_SECTIONS:
            raise NovelSystemError("UNSUPPORTED_BLUEPRINT_SECTION", f"Unsupported blueprint section: {section}")
        expected_type = list if section in LIST_SECTIONS else dict
        if not isinstance(data, expected_type):
            raise NovelSystemError("INVALID_BLUEPRINT_SECTION_TYPE", f"{section} must be a {expected_type.__name__}")
        with self._locked():
            manifest = self._manifest()
            if manifest.get("compiled"):
                raise NovelSystemError("BLUEPRINT_ALREADY_COMPILED", "Use novel.plan.rebase for future changes after compilation")
            self._check_revision(manifest, args.get("expected_revision"))
            blueprint = self._blueprint(rolling=False)
            current = blueprint.get(section)
            if current:
                raise NovelSystemError(
                    "BLUEPRINT_SECTION_ALREADY_STAGED",
                    f"{section} already contains canonical data; use patch or upsert_many",
                    details={"section": section, "revision": manifest.get("blueprint_revision")},
                )
            blueprint[section] = data
            revision = self._commit_blueprint(manifest, blueprint)
        report = self._blueprint_report(blueprint)
        return self._success(
            "BLUEPRINT_SECTION_UPDATED",
            section=section,
            revision=revision,
            blueprint_hash=_blueprint_hash(blueprint),
            energy_before=None,
            energy_after=report["energy"],
            convergence="building" if report["coverage_incomplete"] else "improving",
            next_action="novel.blueprint.upsert_many" if report["coverage_incomplete"] else "novel.blueprint.assist",
        )

    def patch_blueprint(self, args: Mapping[str, Any]) -> dict[str, Any]:
        section = str(args.get("section") or "")
        selector = args.get("selector") or {}
        changes = args.get("changes")
        if section not in BLUEPRINT_SECTIONS or not isinstance(selector, Mapping) or not isinstance(changes, Mapping) or not changes:
            raise NovelSystemError("INVALID_BLUEPRINT_PATCH", "section, selector, and non-empty changes are required")
        with self._locked():
            manifest = self._manifest()
            if manifest.get("compiled"):
                raise NovelSystemError("BLUEPRINT_ALREADY_COMPILED", "Use novel.plan.rebase after compilation")
            self._check_revision(manifest, args.get("expected_revision"))
            blueprint = self._blueprint(rolling=False)
            before = self._blueprint_report(blueprint)["energy"]
            if section in OBJECT_SECTIONS:
                if selector:
                    raise NovelSystemError("INVALID_OBJECT_SELECTOR", f"{section} requires an empty selector")
                blueprint[section] = _deep_merge(blueprint.get(section) or {}, changes)
            else:
                rows = blueprint.get(section)
                if not isinstance(rows, list):
                    rows = []
                key = "number" if section == "chapters" else "id"
                selected = selector.get(key)
                index = next((i for i, item in enumerate(rows) if isinstance(item, dict) and item.get(key) == selected), None)
                if index is None:
                    if not args.get("create_if_missing"):
                        raise NovelSystemError("BLUEPRINT_ITEM_NOT_FOUND", f"No {section} item matches selector", details={"selector": dict(selector)})
                    item = dict(changes)
                    item.setdefault(key, selected)
                    rows.append(item)
                else:
                    rows[index] = _deep_merge(rows[index], changes)
                blueprint[section] = rows
            after_report = self._blueprint_report(blueprint)
            revision = self._commit_blueprint(manifest, blueprint)
        after = after_report["energy"]
        return self._success(
            "BLUEPRINT_PATCHED",
            section=section,
            selector=dict(selector),
            revision=revision,
            energy_before=before,
            energy_after=after,
            convergence="improving" if after < before else "stable" if after == before else "regressing",
            issues=after_report["issues"],
        )

    def upsert_blueprint_many(self, args: Mapping[str, Any]) -> dict[str, Any]:
        section = str(args.get("section") or "")
        items = deepcopy(args.get("items"))
        if section not in LIST_SECTIONS or not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
            raise NovelSystemError("INVALID_BLUEPRINT_BATCH", "A supported list section and non-empty object array are required")
        limit = 15 if section == "chapters" else 30
        if len(items) > limit:
            raise NovelSystemError("BLUEPRINT_BATCH_TOO_LARGE", f"{section} accepts at most {limit} items per transaction")
        with self._locked():
            manifest = self._manifest()
            if manifest.get("compiled"):
                raise NovelSystemError("BLUEPRINT_ALREADY_COMPILED", "Use novel.plan.rebase after compilation")
            self._check_revision(manifest, args.get("expected_revision"))
            blueprint = self._blueprint(rolling=False)
            before_report = self._blueprint_report(blueprint)
            rows = list(blueprint.get(section) or [])
            key = "number" if section == "chapters" else "id"
            index_by_key = {item.get(key): i for i, item in enumerate(rows) if isinstance(item, dict) and item.get(key) not in (None, "")}
            assigned: list[Any] = []
            for offset, incoming in enumerate(items, start=1):
                value = incoming.get(key)
                if key == "id" and value in (None, "", "auto"):
                    basis = str(incoming.get("name") or incoming.get("title") or incoming.get("from") or f"{len(rows)+offset}")
                    value = f"{section.rstrip('s')}.{_slug(basis)}.{_sha256(incoming)[:8]}"
                    incoming[key] = value
                if value in index_by_key:
                    rows[index_by_key[value]] = _deep_merge(rows[index_by_key[value]], incoming)
                else:
                    index_by_key[value] = len(rows)
                    rows.append(incoming)
                assigned.append(value)
            if section == "chapters":
                rows.sort(key=lambda row: _safe_int(row.get("number"), 0) if isinstance(row, Mapping) else 0)
            blueprint[section] = rows
            after_report = self._blueprint_report(blueprint)
            full_before = not before_report["coverage_incomplete"]
            if full_before and section == "plot_events":
                chapter_ids = {
                    event_id
                    for chapter in blueprint.get("chapters") or []
                    if isinstance(chapter, Mapping)
                    for event_id in chapter.get("event_ids") or []
                }
                unreferenced = [value for value in assigned if value not in chapter_ids]
                if unreferenced:
                    raise NovelSystemError("UNREFERENCED_EVENT_FORBIDDEN", "New plot events must be referenced by an existing chapter after coverage is complete", details={"event_ids": unreferenced})
            if full_before and after_report["energy"] > before_report["energy"]:
                raise NovelSystemError(
                    "BLUEPRINT_ENERGY_REGRESSION",
                    "Repair batch increased deterministic blueprint error energy",
                    details={"energy_before": before_report["energy"], "energy_after": after_report["energy"], "issues": after_report["issues"]},
                )
            revision = self._commit_blueprint(manifest, blueprint)
        return self._success(
            "BLUEPRINT_BATCH_UPSERTED",
            section=section,
            keys=assigned,
            revision=revision,
            energy_before=before_report["energy"],
            energy_after=after_report["energy"],
            convergence="building" if after_report["coverage_incomplete"] else "improving" if after_report["energy"] < before_report["energy"] else "stable",
            coverage=after_report["coverage"],
            next_action="novel.blueprint.upsert_many" if after_report["coverage_incomplete"] else "novel.blueprint.assist",
        )

    def _blueprint_report(self, blueprint: Mapping[str, Any]) -> dict[str, Any]:
        issues: list[_Issue] = []
        project = blueprint.get("project") if isinstance(blueprint.get("project"), Mapping) else {}
        planned = _safe_int(project.get("planned_chapters"), 0)
        target_words = _safe_int(project.get("target_words"), 0)
        for section in REQUIRED_SECTIONS:
            value = blueprint.get(section)
            if not value:
                issues.append(_Issue("MISSING_SECTION", f"Required section is empty: {section}", section, 40, {"action": "novel.blueprint.update", "section": section}))
        if planned < 1 or target_words < 1000:
            issues.append(_Issue("INVALID_PROJECT_SCOPE", "Project scope is missing", "project", 100))
        elif planned < math.ceil(target_words / 5000):
            issues.append(_Issue("CHAPTER_SCOPE_TOO_SMALL", "Full-book chapter count is too small for target words", "project.planned_chapters", 100))

        def indexed(section: str, key: str = "id") -> tuple[dict[Any, Mapping[str, Any]], list[Any]]:
            mapping: dict[Any, Mapping[str, Any]] = {}
            duplicates: list[Any] = []
            rows = blueprint.get(section) or []
            if not isinstance(rows, list):
                issues.append(_Issue("INVALID_SECTION_TYPE", f"{section} must be an array", section, 60))
                return mapping, duplicates
            for position, item in enumerate(rows):
                if not isinstance(item, Mapping):
                    issues.append(_Issue("INVALID_ITEM", f"{section}[{position}] must be an object", f"{section}[{position}]", 30))
                    continue
                value = item.get(key)
                if value in (None, ""):
                    issues.append(_Issue("MISSING_CANONICAL_KEY", f"{section}[{position}] is missing {key}", f"{section}[{position}].{key}", 20))
                    continue
                if value in mapping:
                    duplicates.append(value)
                    issues.append(_Issue("DUPLICATE_CANONICAL_KEY", f"Duplicate {section} {key}: {value}", f"{section}.{value}", 50))
                mapping[value] = item
            return mapping, duplicates

        characters, _ = indexed("characters")
        locations, _ = indexed("locations")
        events, _ = indexed("plot_events")
        chapters, _ = indexed("chapters", "number")
        chapter_numbers = sorted(value for value in chapters if isinstance(value, int) and not isinstance(value, bool))
        expected_numbers = list(range(1, planned + 1)) if planned else []
        missing_chapters = sorted(set(expected_numbers) - set(chapter_numbers))
        extra_chapters = sorted(set(chapter_numbers) - set(expected_numbers))
        if missing_chapters:
            issues.append(_Issue("CHAPTER_COVERAGE_INCOMPLETE", "Full-book chapter plan is incomplete", "chapters", min(500, 10 * len(missing_chapters)), {"missing_numbers": missing_chapters[:30]}))
        if extra_chapters:
            issues.append(_Issue("CHAPTER_OUT_OF_RANGE", "Chapter plan contains out-of-range chapters", "chapters", 20 * len(extra_chapters), {"numbers": extra_chapters[:30]}))

        referenced_event_ids: set[str] = set()
        for number, chapter in chapters.items():
            event_ids = chapter.get("event_ids") or []
            if not isinstance(event_ids, list) or not event_ids:
                issues.append(_Issue("CHAPTER_WITHOUT_EVENTS", f"Chapter {number} has no event_ids", f"chapters.{number}.event_ids", 30))
                continue
            for event_id in event_ids:
                referenced_event_ids.add(str(event_id))
                event = events.get(event_id)
                if event is None:
                    issues.append(_Issue("UNKNOWN_CHAPTER_EVENT", f"Chapter {number} references unknown event {event_id}", f"chapters.{number}.event_ids", 35))
                elif _safe_int(event.get("chapter"), 0) != number:
                    issues.append(_Issue("EVENT_CHAPTER_MISMATCH", f"Event {event_id} chapter disagrees with chapter plan", f"plot_events.{event_id}.chapter", 25, {"expected": number}))
            for character_id in chapter.get("participants") or []:
                if character_id not in characters:
                    issues.append(_Issue("UNKNOWN_CHARACTER", f"Chapter {number} references unknown character {character_id}", f"chapters.{number}.participants", 25))
            for location_id in chapter.get("locations") or []:
                if location_id not in locations:
                    issues.append(_Issue("UNKNOWN_LOCATION", f"Chapter {number} references unknown location {location_id}", f"chapters.{number}.locations", 25))
        for event_id, event in events.items():
            if str(event_id) not in referenced_event_ids:
                issues.append(_Issue("UNREFERENCED_EVENT", f"Event {event_id} is not bound to a chapter", f"plot_events.{event_id}", 15))
            for character_id in event.get("participants") or []:
                if character_id not in characters:
                    issues.append(_Issue("UNKNOWN_CHARACTER", f"Event {event_id} references unknown character {character_id}", f"plot_events.{event_id}.participants", 25))
            location = event.get("location")
            if location not in locations:
                issues.append(_Issue("UNKNOWN_LOCATION", f"Event {event_id} references unknown location {location}", f"plot_events.{event_id}.location", 25))
            if _safe_int(event.get("duration_ticks"), 0) < 1:
                issues.append(_Issue("INVALID_EVENT_DURATION", f"Event {event_id} duration must be positive", f"plot_events.{event_id}.duration_ticks", 30))
            for dependency in event.get("requires_events") or []:
                if dependency not in events:
                    issues.append(_Issue("UNKNOWN_EVENT_DEPENDENCY", f"Event {event_id} requires unknown event {dependency}", f"plot_events.{event_id}.requires_events", 30))

        # Character interval overlap and travel feasibility.
        by_character: dict[str, list[Mapping[str, Any]]] = {str(key): [] for key in characters}
        for event in events.values():
            for character_id in event.get("participants") or []:
                if character_id in by_character:
                    by_character[str(character_id)].append(event)
        for character_id, rows in by_character.items():
            rows.sort(key=lambda item: (_safe_int(item.get("start_tick"), 0), str(item.get("id") or "")))
            initial = characters[character_id].get("initial") if isinstance(characters[character_id].get("initial"), Mapping) else {}
            if rows and initial.get("location") and rows[0].get("location") != initial.get("location"):
                issues.append(
                    _Issue(
                        "INITIAL_LOCATION_MISMATCH",
                        f"{character_id} starts at {initial.get('location')} but first scene is {rows[0].get('location')}",
                        f"characters.{character_id}.initial.location",
                        20,
                        {"action": "novel.mobility.align_initial_many", "character_id": character_id, "location": rows[0].get("location")},
                    )
                )
            for left, right in zip(rows, rows[1:]):
                left_start = _safe_int(left.get("start_tick"), 0)
                left_end = left_start + max(1, _safe_int(left.get("duration_ticks"), 1))
                right_start = _safe_int(right.get("start_tick"), 0)
                if right_start < left_end:
                    issues.append(
                        _Issue(
                            "PARTICIPANT_EVENT_OVERLAP",
                            f"{character_id} overlaps events {left.get('id')} and {right.get('id')}",
                            f"plot_events.{right.get('id')}.start_tick",
                            35 + (left_end - right_start),
                            {"action": "novel.timeline.normalize", "pivot_event_id": right.get("id"), "minimum_shift": left_end - right_start},
                        )
                    )
                    continue
                left_location = str(left.get("location") or "")
                right_location = str(right.get("location") or "")
                if left_location and right_location and left_location != right_location:
                    duration = self._shortest_duration(blueprint, left_location, right_location)
                    if duration is None:
                        issues.append(
                            _Issue(
                                "MISSING_ROUTE",
                                f"No route exists for {character_id}: {left_location} -> {right_location}",
                                "routes",
                                30,
                                {"action": "novel.blueprint.upsert_many", "section": "routes", "from": left_location, "to": right_location},
                            )
                        )
                    elif right_start < left_end + duration:
                        issues.append(
                            _Issue(
                                "INSUFFICIENT_TRAVEL_TIME",
                                f"{character_id} cannot reach {right_location} before event {right.get('id')}",
                                f"plot_events.{right.get('id')}.start_tick",
                                30 + (left_end + duration - right_start),
                                {"action": "novel.timeline.normalize", "pivot_event_id": right.get("id"), "minimum_shift": left_end + duration - right_start},
                            )
                        )

        calendar = blueprint.get("calendar") if isinstance(blueprint.get("calendar"), Mapping) else {}
        ticks_per_year = _safe_int(calendar.get("ticks_per_year"), 0)
        if calendar and ticks_per_year < 1:
            issues.append(_Issue("INVALID_CALENDAR", "calendar.ticks_per_year must be positive", "calendar.ticks_per_year", 40))
        for event_id, event in events.items():
            expected_ages = event.get("expected_ages")
            if isinstance(expected_ages, Mapping) and ticks_per_year > 0:
                for character_id, expected_age in expected_ages.items():
                    character = characters.get(character_id)
                    if character is None:
                        continue
                    actual_age = math.floor((_safe_int(event.get("start_tick"), 0) - _safe_int(character.get("birth_tick"), 0)) / ticks_per_year)
                    if _safe_int(expected_age, actual_age) != actual_age:
                        issues.append(_Issue("AGE_MISMATCH", f"{character_id} age at {event_id} must be {actual_age}", f"plot_events.{event_id}.expected_ages.{character_id}", 15, {"actual_age": actual_age}))

        coverage = {
            "planned_chapters": planned,
            "present_chapters": len(chapters),
            "missing_chapters": missing_chapters,
            "plot_events": len(events),
            "referenced_plot_events": len(referenced_event_ids & {str(item) for item in events}),
        }
        coverage_incomplete = bool(missing_chapters or extra_chapters or len(chapters) != planned)
        sorted_issues = sorted(issues, key=lambda item: (-item.weight, item.code, item.path))
        return {
            "energy": sum(item.weight for item in sorted_issues),
            "issues": [item.to_dict() for item in sorted_issues],
            "coverage": coverage,
            "coverage_incomplete": coverage_incomplete,
        }

    def assist_blueprint(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._manifest()
        blueprint = self._blueprint(rolling=False)
        report = self._blueprint_report(blueprint)
        if report["coverage_incomplete"]:
            raise NovelSystemError(
                "BLUEPRINT_BUILDING",
                "Full-book chapter coverage must be completed before deterministic assistance",
                details={**report, "convergence": "building", "next_action": "novel.blueprint.upsert_many"},
            )
        previous = args.get("previous_energy")
        if previous is None:
            convergence = "baseline"
        else:
            previous_int = _safe_int(previous, -1)
            convergence = "improving" if report["energy"] < previous_int else "stable" if report["energy"] == previous_int else "regressing"
        batch_size = max(1, min(20, _safe_int(args.get("batch_size"), 6)))
        repair_batch = []
        for issue in report["issues"]:
            repair = issue.get("repair")
            if repair:
                repair_batch.append({"issue": issue, "repair": repair})
            if len(repair_batch) >= batch_size:
                break
        return self._success(
            "BLUEPRINT_ASSISTED",
            energy=report["energy"],
            previous_energy=previous,
            convergence=convergence,
            ready_for_compile=report["energy"] == 0,
            issues=report["issues"],
            repair_batch=repair_batch,
            repair_sequence=[item["repair"] for item in repair_batch],
            coverage=report["coverage"],
            next_action="novel.blueprint.compile" if report["energy"] == 0 else (repair_batch[0]["repair"].get("action") if repair_batch else "novel.blueprint.patch"),
        )

    @staticmethod
    def _entity_rows(blueprint: Mapping[str, Any], entity_type: str) -> list[Mapping[str, Any]]:
        section = {"character": "characters", "location": "locations", "event": "plot_events", "chapter": "chapters"}[entity_type]
        return [item for item in blueprint.get(section) or [] if isinstance(item, Mapping)]

    def resolve_reference(self, args: Mapping[str, Any]) -> dict[str, Any]:
        entity_type = str(args.get("entity_type") or "")
        queries = args.get("queries")
        if entity_type not in {"character", "location", "event", "chapter"} or not isinstance(queries, list) or not queries:
            raise NovelSystemError("INVALID_REFERENCE_QUERY", "entity_type and non-empty queries are required")
        rows = self._entity_rows(self._blueprint(), entity_type)
        key = "number" if entity_type == "chapter" else "id"
        resolutions = []
        for query in queries:
            query_text = str(query).strip().casefold()
            exact = []
            ranked = []
            for row in rows:
                labels = [str(row.get(key) or ""), str(row.get("name") or ""), str(row.get("title") or "")]
                normalized = [label.casefold() for label in labels if label]
                if query_text in normalized:
                    exact.append(row)
                else:
                    score = max((self._similarity(query_text, label) for label in normalized), default=0.0)
                    if score >= 0.25:
                        ranked.append((score, row))
            ranked.sort(key=lambda item: (-item[0], str(item[1].get(key))))
            resolutions.append(
                {
                    "query": query,
                    "resolved": len(exact) == 1,
                    "canonical": dict(exact[0]) if len(exact) == 1 else None,
                    "ambiguous": len(exact) > 1,
                    "suggestions": [{"score": round(score, 4), "entity": dict(row)} for score, row in ranked[:5]],
                }
            )
        return self._success("REFERENCES_RESOLVED", entity_type=entity_type, resolutions=resolutions)

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        if left in right or right in left:
            return min(len(left), len(right)) / max(len(left), len(right))
        left_set, right_set = set(left), set(right)
        return len(left_set & right_set) / max(1, len(left_set | right_set))

    @staticmethod
    def _route_graph(blueprint: Mapping[str, Any]) -> dict[str, list[tuple[str, int, str]]]:
        graph: dict[str, list[tuple[str, int, str]]] = {}
        for route in blueprint.get("routes") or []:
            if not isinstance(route, Mapping):
                continue
            start, end = str(route.get("from") or ""), str(route.get("to") or "")
            duration = _safe_int(route.get("min_duration_ticks"), 0)
            if not start or not end or duration < 1:
                continue
            graph.setdefault(start, []).append((end, duration, str(route.get("mode") or "unspecified")))
            if route.get("bidirectional"):
                graph.setdefault(end, []).append((start, duration, str(route.get("mode") or "unspecified")))
        return graph

    @classmethod
    def _shortest_route(cls, blueprint: Mapping[str, Any], start: str, end: str) -> tuple[int, list[str], list[str]] | None:
        if start == end:
            return 0, [start], []
        graph = cls._route_graph(blueprint)
        queue: list[tuple[int, str, list[str], list[str]]] = [(0, start, [start], [])]
        best = {start: 0}
        while queue:
            distance, node, path, modes = heapq.heappop(queue)
            if node == end:
                return distance, path, modes
            if distance != best.get(node):
                continue
            for neighbor, duration, mode in graph.get(node, []):
                candidate = distance + duration
                if candidate < best.get(neighbor, 10**18):
                    best[neighbor] = candidate
                    heapq.heappush(queue, (candidate, neighbor, path + [neighbor], modes + [mode]))
        return None

    @classmethod
    def _shortest_duration(cls, blueprint: Mapping[str, Any], start: str, end: str) -> int | None:
        route = cls._shortest_route(blueprint, start, end)
        return route[0] if route else None

    def timeline_calculate(self, args: Mapping[str, Any]) -> dict[str, Any]:
        blueprint = self._blueprint()
        operation = str(args.get("operation") or "")
        if operation == "age":
            calendar = blueprint.get("calendar") if isinstance(blueprint.get("calendar"), Mapping) else {}
            ticks_per_year = _safe_int(args.get("ticks_per_year"), _safe_int(calendar.get("ticks_per_year"), 0))
            birth_tick = _safe_int(args.get("birth_tick"), 0)
            at_tick = _safe_int(args.get("at_tick"), _safe_int(calendar.get("start_tick"), 0))
            if ticks_per_year < 1:
                raise NovelSystemError("INVALID_CALENDAR", "ticks_per_year must be positive")
            return self._success("TIMELINE_CALCULATED", operation="age", age=math.floor((at_tick - birth_tick) / ticks_per_year), birth_tick=birth_tick, at_tick=at_tick)
        if operation == "arrival":
            start, end = str(args.get("from") or ""), str(args.get("to") or "")
            depart = _safe_int(args.get("depart_tick"), 0)
            route = self._shortest_route(blueprint, start, end)
            if route is None:
                raise NovelSystemError("ROUTE_NOT_FOUND", f"No canonical route from {start} to {end}")
            duration, path, modes = route
            return self._success("TIMELINE_CALCULATED", operation="arrival", depart_tick=depart, duration_ticks=duration, earliest_arrival_tick=depart + duration, path=path, modes=modes)
        if operation == "overlap":
            a_start, a_duration = _safe_int(args.get("a_start"), 0), _safe_int(args.get("a_duration"), 0)
            b_start, b_duration = _safe_int(args.get("b_start"), 0), _safe_int(args.get("b_duration"), 0)
            start, end = max(a_start, b_start), min(a_start + a_duration, b_start + b_duration)
            return self._success("TIMELINE_CALCULATED", operation="overlap", overlaps=end > start, overlap_ticks=max(0, end - start), interval=[start, max(start, end)])
        raise NovelSystemError("UNSUPPORTED_TIMELINE_OPERATION", "operation must be age, arrival, or overlap")

    def shift_timeline_suffix(self, args: Mapping[str, Any]) -> dict[str, Any]:
        event_id = str(args.get("event_id") or "")
        delta = _safe_int(args.get("delta_ticks"), 0)
        reason = str(args.get("reason") or "").strip()
        if not event_id or delta < 1 or not reason:
            raise NovelSystemError("INVALID_TIMELINE_SHIFT", "event_id, positive delta_ticks, and reason are required")
        with self._locked():
            manifest = self._manifest()
            if manifest.get("compiled"):
                raise NovelSystemError("COMPILED_TIMELINE_IMMUTABLE", "Use novel.plan.rebase after blueprint compilation")
            self._check_revision(manifest, args.get("expected_revision"))
            blueprint = self._blueprint(rolling=False)
            events = [item for item in blueprint.get("plot_events") or [] if isinstance(item, dict)]
            pivot = next((item for item in events if item.get("id") == event_id), None)
            if pivot is None:
                raise NovelSystemError("EVENT_NOT_FOUND", f"Unknown pivot event: {event_id}")
            before = self._blueprint_report(blueprint)["energy"]
            pivot_tick = _safe_int(pivot.get("start_tick"), 0)
            shifted_ids = []
            for event in events:
                if _safe_int(event.get("start_tick"), 0) >= pivot_tick:
                    event["start_tick"] = _safe_int(event.get("start_tick"), 0) + delta
                    shifted_ids.append(event.get("id"))
            for chapter in blueprint.get("chapters") or []:
                if isinstance(chapter, dict) and _safe_int(chapter.get("start_tick"), 0) >= pivot_tick:
                    chapter["start_tick"] = _safe_int(chapter.get("start_tick"), 0) + delta
            after_report = self._blueprint_report(blueprint)
            revision = self._commit_blueprint(manifest, blueprint)
        return self._success("TIMELINE_SUFFIX_SHIFTED", revision=revision, event_id=event_id, delta_ticks=delta, reason=reason, shifted_event_ids=shifted_ids, energy_before=before, energy_after=after_report["energy"], convergence="improving" if after_report["energy"] < before else "stable" if after_report["energy"] == before else "regressing")

    def normalize_timeline(self, args: Mapping[str, Any]) -> dict[str, Any]:
        reason = str(args.get("reason") or "").strip()
        max_shifts = max(1, min(256, _safe_int(args.get("max_shifts"), 128)))
        if not reason:
            raise NovelSystemError("INVALID_NORMALIZATION_REASON", "reason is required")
        with self._locked():
            manifest = self._manifest()
            if manifest.get("compiled"):
                raise NovelSystemError("COMPILED_TIMELINE_IMMUTABLE", "Use novel.plan.rebase after compilation")
            self._check_revision(manifest, args.get("expected_revision"))
            blueprint = self._blueprint(rolling=False)
            before_report = self._blueprint_report(blueprint)
            before = before_report["energy"]
            shifts: list[dict[str, Any]] = []
            for _ in range(max_shifts):
                report = self._blueprint_report(blueprint)
                deterministic = [
                    item for item in report["issues"]
                    if item.get("code") in {"PARTICIPANT_EVENT_OVERLAP", "INSUFFICIENT_TRAVEL_TIME"}
                    and isinstance(item.get("repair"), Mapping)
                ]
                if not deterministic:
                    break
                repair = deterministic[0]["repair"]
                pivot_id = repair.get("pivot_event_id")
                delta = max(1, _safe_int(repair.get("minimum_shift"), 1))
                events = [item for item in blueprint.get("plot_events") or [] if isinstance(item, dict)]
                pivot = next((item for item in events if item.get("id") == pivot_id), None)
                if pivot is None:
                    break
                pivot_tick = _safe_int(pivot.get("start_tick"), 0)
                for event in events:
                    if _safe_int(event.get("start_tick"), 0) >= pivot_tick:
                        event["start_tick"] = _safe_int(event.get("start_tick"), 0) + delta
                for chapter in blueprint.get("chapters") or []:
                    if isinstance(chapter, dict) and _safe_int(chapter.get("start_tick"), 0) >= pivot_tick:
                        chapter["start_tick"] = _safe_int(chapter.get("start_tick"), 0) + delta
                shifts.append({"pivot_event_id": pivot_id, "delta_ticks": delta})
            after_report = self._blueprint_report(blueprint)
            after = after_report["energy"]
            if shifts and after >= before:
                raise NovelSystemError("NORMALIZATION_NOT_IMPROVING", "Deterministic normalization did not reduce error energy", details={"energy_before": before, "energy_after": after})
            revision = self._commit_blueprint(manifest, blueprint) if shifts else _safe_int(manifest.get("blueprint_revision"), 0)
        return self._success("TIMELINE_NORMALIZED", revision=revision, reason=reason, shifts=shifts, energy_before=before, energy_after=after, convergence="improving" if after < before else "stable", remaining_issues=after_report["issues"])

    def align_initial_locations_many(self, args: Mapping[str, Any]) -> dict[str, Any]:
        items = args.get("items")
        if not isinstance(items, list) or not items or not all(isinstance(item, Mapping) for item in items):
            raise NovelSystemError("INVALID_INITIAL_LOCATION_BATCH", "items must be a non-empty object array")
        with self._locked():
            manifest = self._manifest()
            if manifest.get("compiled"):
                raise NovelSystemError("COMPILED_BLUEPRINT_IMMUTABLE", "Use novel.plan.rebase after compilation")
            self._check_revision(manifest, args.get("expected_revision"))
            blueprint = self._blueprint(rolling=False)
            before = self._blueprint_report(blueprint)["energy"]
            characters = {item.get("id"): item for item in blueprint.get("characters") or [] if isinstance(item, dict)}
            changed = []
            for item in items:
                character_id, location = str(item.get("character_id") or ""), str(item.get("location") or "")
                character = characters.get(character_id)
                if character is None:
                    raise NovelSystemError("CHARACTER_NOT_FOUND", f"Unknown character: {character_id}")
                if location not in {row.get("id") for row in blueprint.get("locations") or [] if isinstance(row, Mapping)}:
                    raise NovelSystemError("LOCATION_NOT_FOUND", f"Unknown location: {location}")
                initial = character.setdefault("initial", {})
                initial["location"] = location
                changed.append({"character_id": character_id, "location": location})
            after_report = self._blueprint_report(blueprint)
            if after_report["energy"] >= before:
                raise NovelSystemError("INITIAL_ALIGNMENT_NOT_IMPROVING", "Initial-location alignment must strictly reduce error energy", details={"energy_before": before, "energy_after": after_report["energy"]})
            revision = self._commit_blueprint(manifest, blueprint)
        return self._success("INITIAL_LOCATIONS_ALIGNED", revision=revision, items=changed, energy_before=before, energy_after=after_report["energy"], convergence="improving")

    def compile_blueprint(self, args: Mapping[str, Any]) -> dict[str, Any]:
        with self._locked():
            manifest = self._manifest()
            if manifest.get("compiled"):
                original = _read_json(self.original_path, {})
                return self._success("BLUEPRINT_ALREADY_COMPILED", revision=manifest.get("blueprint_revision"), blueprint_hash=_blueprint_hash(original), state_hash=self._state().get("state_hash"), next_action="novel.chapter.checkout")
            self._check_revision(manifest, args.get("expected_revision"))
            blueprint = self._blueprint(rolling=False)
            report = self._blueprint_report(blueprint)
            if report["coverage_incomplete"] or report["energy"]:
                raise NovelSystemError("BLUEPRINT_COMPILE_REJECTED", "Blueprint has unresolved deterministic issues", details=report)
            original = deepcopy(blueprint)
            rolling = deepcopy(blueprint)
            characters_state = {}
            for character in original.get("characters") or []:
                if not isinstance(character, Mapping):
                    continue
                initial = deepcopy(character.get("initial") if isinstance(character.get("initial"), Mapping) else {})
                initial.setdefault("alive", True)
                for field in ("injuries", "inventory", "knowledge"):
                    initial.setdefault(field, [])
                characters_state[str(character.get("id"))] = initial
            events_state = {
                str(event.get("id")): {
                    "id": event.get("id"),
                    "status": "planned",
                    "chapter": event.get("chapter"),
                    "deadline_chapter": event.get("deadline_chapter"),
                    "closure_required": bool(event.get("closure_required")),
                    "result": None,
                }
                for event in original.get("plot_events") or []
                if isinstance(event, Mapping) and event.get("id")
            }
            emotional_accounts = {
                str(account.get("id")): {
                    **deepcopy(account),
                    "balance": float(account.get("initial_balance") or 0),
                    "last_transaction_chapter": 0,
                }
                for account in original.get("emotional_accounts") or []
                if isinstance(account, Mapping) and account.get("id")
            }
            state: dict[str, Any] = {
                "schema": "tiangong.novel.state.v1",
                "revision": 0,
                "next_chapter": 1,
                "accepted_chapters": 0,
                "current_tick": _safe_int((original.get("calendar") or {}).get("start_tick"), 0),
                "characters": characters_state,
                "events": events_state,
                "relationships": {},
                "foreshadows": {},
                "emotional_accounts": emotional_accounts,
                "emotional_triggers": {},
                "selected_scenes": {},
                "recent_summaries": [],
                "protected_anchor_ids": list((original.get("story") or {}).get("protected_anchors") or []),
            }
            state["state_hash"] = _state_hash(state)
            _atomic_json(self.original_path, original)
            _atomic_json(self.rolling_path, rolling)
            _atomic_json(self.ledger_path, [])
            _atomic_json(self.state_path, state)
            manifest["compiled"] = True
            manifest["compiled_at"] = _utc_now()
            manifest["original_blueprint_hash"] = _blueprint_hash(original)
            manifest["rolling_blueprint_hash"] = _blueprint_hash(rolling)
            self._write_manifest(manifest)
        return self._success("BLUEPRINT_COMPILED", revision=manifest.get("blueprint_revision"), original_blueprint_hash=manifest["original_blueprint_hash"], rolling_blueprint_hash=manifest["rolling_blueprint_hash"], state_hash=state["state_hash"], next_chapter=1, next_action="novel.chapter.checkout")

    def rebase_plan(self, args: Mapping[str, Any]) -> dict[str, Any]:
        expected_state_hash = str(args.get("expected_state_hash") or "")
        reason = str(args.get("reason") or "").strip()
        event_updates = args.get("event_updates")
        chapter_updates = args.get("chapter_updates")
        maintained = set(str(item) for item in (args.get("maintained_anchor_ids") or []))
        if not reason or not isinstance(event_updates, list) or not isinstance(chapter_updates, list):
            raise NovelSystemError("INVALID_REBASE", "expected_state_hash, reason, and update arrays are required")
        with self._locked():
            manifest = self._manifest()
            if not manifest.get("compiled"):
                raise NovelSystemError("BLUEPRINT_NOT_COMPILED", "Compile the blueprint before rebasing")
            state = self._state()
            if expected_state_hash != state.get("state_hash"):
                raise NovelSystemError("STALE_STATE", "State changed before rebase", details={"expected_state_hash": expected_state_hash, "actual_state_hash": state.get("state_hash")}, retryable=True)
            protected = set(str(item) for item in state.get("protected_anchor_ids") or [])
            if not protected.issubset(maintained):
                raise NovelSystemError("PROTECTED_ANCHOR_LOSS", "Rebase must preserve every protected anchor", details={"missing_anchor_ids": sorted(protected - maintained)})
            rolling = self._blueprint()
            next_chapter = _safe_int(state.get("next_chapter"), 1)
            events = {item.get("id"): item for item in rolling.get("plot_events") or [] if isinstance(item, dict)}
            chapters = {item.get("number"): item for item in rolling.get("chapters") or [] if isinstance(item, dict)}
            for update in event_updates:
                if not isinstance(update, Mapping) or update.get("id") not in events:
                    raise NovelSystemError("INVALID_EVENT_REBASE", "Each event update must target an existing canonical id")
                if _safe_int(events[update["id"]].get("chapter"), 0) < next_chapter:
                    raise NovelSystemError("ACCEPTED_PAST_IMMUTABLE", "Rebase cannot change accepted or past events", details={"event_id": update.get("id")})
                events[update["id"]] = _deep_merge(events[update["id"]], {key: value for key, value in update.items() if key != "id"})
            for update in chapter_updates:
                if not isinstance(update, Mapping) or update.get("number") not in chapters:
                    raise NovelSystemError("INVALID_CHAPTER_REBASE", "Each chapter update must target an existing chapter number")
                if _safe_int(update.get("number"), 0) < next_chapter:
                    raise NovelSystemError("ACCEPTED_PAST_IMMUTABLE", "Rebase cannot change accepted chapters", details={"chapter_number": update.get("number")})
                chapters[update["number"]] = _deep_merge(chapters[update["number"]], {key: value for key, value in update.items() if key != "number"})
            rolling["plot_events"] = list(events.values())
            rolling["chapters"] = sorted(chapters.values(), key=lambda item: _safe_int(item.get("number"), 0))
            _atomic_json(self.rolling_path, rolling)
            manifest["rolling_blueprint_hash"] = _blueprint_hash(rolling)
            manifest["rebase_revision"] = _safe_int(manifest.get("rebase_revision"), 0) + 1
            manifest.setdefault("rebase_history", []).append({"revision": manifest["rebase_revision"], "reason": reason, "at": _utc_now(), "state_hash": state["state_hash"]})
            self._write_manifest(manifest)
        return self._success("ROLLING_PLAN_REBASED", rebase_revision=manifest["rebase_revision"], rolling_blueprint_hash=manifest["rolling_blueprint_hash"], maintained_anchor_ids=sorted(maintained), next_chapter=next_chapter)

    def _active_trigger_due(self, state: Mapping[str, Any], chapter_number: int) -> Mapping[str, Any] | None:
        for trigger in (state.get("emotional_triggers") or {}).values():
            if isinstance(trigger, Mapping) and trigger.get("status") == "pending" and _safe_int(trigger.get("target_chapter_max"), 10**9) <= chapter_number:
                return trigger
        return None

    def checkout_chapter(self, args: Mapping[str, Any]) -> dict[str, Any]:
        chapter_number = _safe_int(args.get("chapter_number"), 0)
        with self._locked():
            manifest = self._manifest()
            if not manifest.get("compiled"):
                raise NovelSystemError("BLUEPRINT_NOT_COMPILED", "Compile the blueprint before chapter checkout")
            state = self._state()
            expected = _safe_int(state.get("next_chapter"), 1)
            planned = _safe_int(manifest.get("planned_chapters"), 0)
            if expected > planned:
                raise NovelSystemError("NOVEL_ALREADY_COMPLETE", "All planned chapters are accepted")
            if chapter_number != expected:
                raise NovelSystemError("OUT_OF_ORDER_CHAPTER", "Only the canonical next chapter may be checked out", details={"requested": chapter_number, "next_chapter": expected})
            trigger = self._active_trigger_due(state, chapter_number)
            if trigger:
                raise NovelSystemError("EMOTIONAL_SCENE_DESIGN_REQUIRED", "A due emotional trigger must be designed before checkout", details={"trigger": dict(trigger), "next_action": "novel.scene.design"})
            blueprint = self._blueprint()
            chapter = next((item for item in blueprint.get("chapters") or [] if isinstance(item, Mapping) and item.get("number") == chapter_number), None)
            if chapter is None:
                raise NovelSystemError("CHAPTER_PLAN_NOT_FOUND", f"Chapter plan {chapter_number} does not exist")
            event_ids = set(chapter.get("event_ids") or [])
            participant_ids = set(chapter.get("participants") or [])
            location_ids = set(chapter.get("locations") or [])
            events = [item for item in blueprint.get("plot_events") or [] if isinstance(item, Mapping) and item.get("id") in event_ids]
            characters = [item for item in blueprint.get("characters") or [] if isinstance(item, Mapping) and item.get("id") in participant_ids]
            locations = [item for item in blueprint.get("locations") or [] if isinstance(item, Mapping) and item.get("id") in location_ids]
            due_events = [item for item in (state.get("events") or {}).values() if isinstance(item, Mapping) and item.get("status") != "closed" and _safe_int(item.get("deadline_chapter"), 10**9) <= chapter_number]
            lease_id = f"lease_{uuid.uuid4().hex}"
            expires_epoch = time.time() + 4 * 60 * 60
            lease = {
                "schema": "tiangong.novel.chapter-lease.v1",
                "lease_id": lease_id,
                "chapter_number": chapter_number,
                "pre_state_hash": state["state_hash"],
                "rolling_blueprint_hash": _blueprint_hash(blueprint),
                "issued_at": _utc_now(),
                "expires_at": datetime.fromtimestamp(expires_epoch, timezone.utc).isoformat().replace("+00:00", "Z"),
                "expires_at_epoch": expires_epoch,
            }
            _atomic_json(self.leases_dir / f"{lease_id}.json", lease)
        selected_scenes = [scene for scene in (state.get("selected_scenes") or {}).values() if isinstance(scene, Mapping) and _safe_int(scene.get("target_chapter"), 0) == chapter_number]
        return self._success(
            "CHAPTER_CHECKED_OUT",
            lease_id=lease_id,
            chapter_number=chapter_number,
            pre_state_hash=state["state_hash"],
            rolling_blueprint_hash=lease["rolling_blueprint_hash"],
            expires_at=lease["expires_at"],
            chapter_card=dict(chapter),
            relevant={"events": events, "characters": characters, "locations": locations, "character_state": {key: value for key, value in (state.get("characters") or {}).items() if key in participant_ids}},
            due_open_events=due_events,
            selected_scenes=selected_scenes,
            recent_summaries=list(state.get("recent_summaries") or [])[-3:],
            next_action="novel.chapter.submit",
        )

    @staticmethod
    def _deviation_score(chapter: Mapping[str, Any], actual: Mapping[str, Any]) -> int:
        planned_events = set(str(item) for item in chapter.get("event_ids") or [])
        actual_events = {str(item.get("id")) for item in actual.get("events") or [] if isinstance(item, Mapping)}
        planned_participants = set(str(item) for item in chapter.get("participants") or [])
        actual_participants = {str(participant) for event in actual.get("events") or [] if isinstance(event, Mapping) for participant in event.get("participants") or []}
        planned_locations = set(str(item) for item in chapter.get("locations") or [])
        actual_locations = {str(event.get("location")) for event in actual.get("events") or [] if isinstance(event, Mapping) and event.get("location")}
        planned_outcomes = set(str(item) for item in chapter.get("required_outcomes") or [])
        actual_outcomes = {str(tag) for event in actual.get("events") or [] if isinstance(event, Mapping) for tag in event.get("outcome_tags") or []}
        planned_themes = set(str(item) for item in chapter.get("theme_tags") or [])
        actual_themes = set(str(item) for item in actual.get("theme_tags") or [])

        def distance(left: set[str], right: set[str]) -> float:
            if not left and not right:
                return 0.0
            return len(left ^ right) / max(1, len(left | right))

        weighted = (
            0.35 * distance(planned_events, actual_events)
            + 0.15 * distance(planned_participants, actual_participants)
            + 0.10 * distance(planned_locations, actual_locations)
            + 0.25 * distance(planned_outcomes, actual_outcomes)
            + 0.15 * distance(planned_themes, actual_themes)
        )
        return min(100, round(weighted * 100))

    def _validate_submission(
        self,
        *,
        blueprint: Mapping[str, Any],
        state: Mapping[str, Any],
        chapter: Mapping[str, Any],
        content: str,
        actual: Mapping[str, Any],
    ) -> tuple[int, list[dict[str, Any]]]:
        problems: list[dict[str, Any]] = []
        settings = blueprint.get("settings") if isinstance(blueprint.get("settings"), Mapping) else {}
        minimum = max(200, _safe_int(settings.get("min_chapter_chars"), 2500))
        cjk_chars = _count_cjk(content)
        if cjk_chars < minimum:
            problems.append({"code": "CHAPTER_TOO_SHORT", "actual": cjk_chars, "minimum": minimum})
        if _PLACEHOLDER_RE.search(content):
            problems.append({"code": "PLACEHOLDER_PROSE"})
        planned_ids = set(str(item) for item in chapter.get("event_ids") or [])
        actual_events = [item for item in actual.get("events") or [] if isinstance(item, Mapping)]
        actual_by_id = {str(item.get("id")): item for item in actual_events}
        missing = sorted(planned_ids - set(actual_by_id))
        if missing:
            problems.append({"code": "PLANNED_EVENTS_MISSING", "event_ids": missing})
        known_events = {str(item.get("id")): item for item in blueprint.get("plot_events") or [] if isinstance(item, Mapping)}
        for event_id, event in actual_by_id.items():
            if event_id not in known_events and not event.get("unplanned"):
                problems.append({"code": "UNPLANNED_EVENT_NOT_DECLARED", "event_id": event_id})
            if event.get("status") not in EVENT_STATUSES:
                problems.append({"code": "INVALID_EVENT_STATUS", "event_id": event_id})
            for term in event.get("evidence_terms") or []:
                if str(term) not in content:
                    problems.append({"code": "MISSING_EVENT_EVIDENCE", "event_id": event_id, "term": term})
        current_chapter = _safe_int(chapter.get("number"), 0)
        for event_id, event_state in (state.get("events") or {}).items():
            if not isinstance(event_state, Mapping) or event_state.get("status") == "closed":
                continue
            due = _safe_int(event_state.get("deadline_chapter"), 10**9)
            if event_state.get("closure_required") and due <= current_chapter:
                actual_event = actual_by_id.get(str(event_id))
                if not actual_event or actual_event.get("status") != "closed":
                    problems.append({"code": "OVERDUE_EVENT_NOT_CLOSED", "event_id": event_id, "deadline_chapter": due})
        for change in actual.get("state_changes") or []:
            if not isinstance(change, Mapping):
                continue
            character_id, field = str(change.get("character_id") or ""), str(change.get("field") or "")
            if character_id not in (state.get("characters") or {}):
                problems.append({"code": "UNKNOWN_STATE_CHARACTER", "character_id": character_id})
            if field not in STATE_FIELDS:
                problems.append({"code": "UNSUPPORTED_STATE_FIELD", "field": field})
            current = (state.get("characters") or {}).get(character_id, {}).get(field)
            if "from" in change and change.get("from") != current:
                problems.append({"code": "STATE_PRECONDITION_MISMATCH", "character_id": character_id, "field": field, "expected": change.get("from"), "actual": current})
        deviation = self._deviation_score(chapter, actual)
        if deviation >= 65:
            proof = actual.get("convergence_proof")
            protected = set(str(item) for item in state.get("protected_anchor_ids") or [])
            maintained = set(str(item) for item in (proof or {}).get("maintained_anchor_ids") or []) if isinstance(proof, Mapping) else set()
            if not isinstance(proof, Mapping) or not protected.issubset(maintained) or not _non_empty(proof.get("return_path")):
                problems.append({"code": "HIGH_DEVIATION_REQUIRES_CONVERGENCE_PROOF", "deviation_score": deviation, "missing_anchor_ids": sorted(protected - maintained)})
        return deviation, problems

    @staticmethod
    def _apply_state_changes(state: MutableMapping[str, Any], changes: Sequence[Any]) -> None:
        characters = state.setdefault("characters", {})
        for change in changes:
            if not isinstance(change, Mapping):
                continue
            character = characters[str(change.get("character_id"))]
            field = str(change.get("field"))
            op = str(change.get("op") or "set")
            if op == "set":
                character[field] = deepcopy(change.get("to"))
            elif op in {"add", "remove"}:
                current = list(character.get(field) or [])
                items = list(change.get("items") or [])
                if op == "add":
                    for item in items:
                        if item not in current:
                            current.append(item)
                else:
                    current = [item for item in current if item not in items]
                character[field] = current

    def _apply_emotions(self, state: MutableMapping[str, Any], actual: Mapping[str, Any], chapter_number: int, content: str, settings: Mapping[str, Any]) -> list[dict[str, Any]]:
        created_triggers = []
        accounts = state.setdefault("emotional_accounts", {})
        triggers = state.setdefault("emotional_triggers", {})
        threshold = float(settings.get("emotional_trigger_threshold") or 70)
        for transaction in actual.get("emotional_transactions") or []:
            if not isinstance(transaction, Mapping):
                continue
            account_id = str(transaction.get("account_id") or "")
            account = accounts.get(account_id)
            if not isinstance(account, MutableMapping):
                continue
            evidence = [str(item) for item in transaction.get("evidence_terms") or []]
            if not evidence or any(term not in content for term in evidence):
                continue
            factors = transaction.get("factors") if isinstance(transaction.get("factors"), Mapping) else {}
            positive = sum(max(0.0, min(1.0, float(factors.get(name) or 0))) for name in ("attachment", "duration", "sacrifice", "expectation", "foreshadow", "importance"))
            negative = sum(max(0.0, min(1.0, float(factors.get(name) or 0))) for name in ("leakage", "repetition"))
            amount = min(25.0, max(0.0, positive * 5.0 - negative * 4.0))
            if transaction.get("kind") == "withdraw":
                amount = -amount
            account["balance"] = max(0.0, min(100.0, float(account.get("balance") or 0) + amount))
            account["last_transaction_chapter"] = chapter_number
            if account["balance"] >= threshold and not any(isinstance(item, Mapping) and item.get("account_id") == account_id and item.get("status") == "pending" for item in triggers.values()):
                trigger_id = f"trigger_{account_id}_{chapter_number}_{_sha256(account)[:8]}"
                trigger = {
                    "trigger_id": trigger_id,
                    "account_id": account_id,
                    "balance": round(account["balance"], 3),
                    "status": "pending",
                    "created_chapter": chapter_number,
                    "target_chapter_min": chapter_number + 1,
                    "target_chapter_max": chapter_number + max(1, _safe_int(settings.get("emotional_payoff_window"), 3)),
                }
                triggers[trigger_id] = trigger
                created_triggers.append(trigger)
        return created_triggers

    def submit_chapter(self, args: Mapping[str, Any]) -> dict[str, Any]:
        lease_id = str(args.get("lease_id") or "")
        chapter_number = _safe_int(args.get("chapter_number"), 0)
        title = str(args.get("title") or "").strip()
        content = str(args.get("content") or "")
        actual = args.get("actual")
        if not lease_id or chapter_number < 1 or not title or not content.strip() or not isinstance(actual, Mapping):
            raise NovelSystemError("INVALID_CHAPTER_SUBMISSION", "lease_id, chapter_number, title, content, and actual are required")
        with self._locked():
            manifest = self._manifest()
            if not manifest.get("compiled"):
                raise NovelSystemError("BLUEPRINT_NOT_COMPILED", "Compile the blueprint before submission")
            state = self._state()
            lease_path = self.leases_dir / f"{lease_id}.json"
            lease = _read_json(lease_path, {})
            if not isinstance(lease, dict) or lease.get("lease_id") != lease_id:
                raise NovelSystemError("LEASE_NOT_FOUND", "Chapter lease is missing or already consumed")
            if float(lease.get("expires_at_epoch") or 0) <= time.time():
                raise NovelSystemError("LEASE_EXPIRED", "Chapter lease expired; check out the canonical next chapter again", retryable=True)
            if lease.get("chapter_number") != chapter_number or chapter_number != _safe_int(state.get("next_chapter"), 1):
                raise NovelSystemError("STALE_CHAPTER_LEASE", "Lease no longer targets the canonical next chapter", details={"next_chapter": state.get("next_chapter")}, retryable=True)
            if lease.get("pre_state_hash") != state.get("state_hash"):
                raise NovelSystemError("STALE_STATE", "Canonical state changed after checkout", details={"lease_state_hash": lease.get("pre_state_hash"), "actual_state_hash": state.get("state_hash")}, retryable=True)
            blueprint = self._blueprint()
            if lease.get("rolling_blueprint_hash") != _blueprint_hash(blueprint):
                raise NovelSystemError("STALE_ROLLING_BLUEPRINT", "Rolling blueprint changed after checkout", retryable=True)
            chapter = next((item for item in blueprint.get("chapters") or [] if isinstance(item, Mapping) and item.get("number") == chapter_number), None)
            if chapter is None:
                raise NovelSystemError("CHAPTER_PLAN_NOT_FOUND", f"Chapter plan {chapter_number} does not exist")
            deviation, problems = self._validate_submission(blueprint=blueprint, state=state, chapter=chapter, content=content, actual=actual)
            if problems:
                raise NovelSystemError("CHAPTER_SUBMISSION_REJECTED", "Chapter failed deterministic acceptance gates", details={"chapter_number": chapter_number, "deviation_score": deviation, "problems": problems, "lease_reusable": True})

            next_state = deepcopy(state)
            self._apply_state_changes(next_state, actual.get("state_changes") or [])
            for event in actual.get("events") or []:
                if not isinstance(event, Mapping):
                    continue
                event_id = str(event.get("id"))
                event_state = next_state.setdefault("events", {}).setdefault(event_id, {"id": event_id})
                event_state.update({
                    "status": event.get("status"),
                    "result": event.get("result"),
                    "chapter": chapter_number,
                    "start_tick": event.get("start_tick"),
                    "duration_ticks": event.get("duration_ticks"),
                    "location": event.get("location"),
                    "outcome_tags": list(event.get("outcome_tags") or []),
                })
                next_state["current_tick"] = max(_safe_int(next_state.get("current_tick"), 0), _safe_int(event.get("start_tick"), 0) + max(1, _safe_int(event.get("duration_ticks"), 1)))
            for change in actual.get("relationship_changes") or []:
                if isinstance(change, Mapping):
                    key = str(change.get("id") or "") or "|".join(str(item) for item in change.get("character_ids") or [])
                    next_state.setdefault("relationships", {})[key] = deepcopy(change)
            for operation in actual.get("foreshadow_ops") or []:
                if isinstance(operation, Mapping) and operation.get("id"):
                    next_state.setdefault("foreshadows", {})[str(operation.get("id"))] = deepcopy(operation)
            settings = blueprint.get("settings") if isinstance(blueprint.get("settings"), Mapping) else {}
            triggers = self._apply_emotions(next_state, actual, chapter_number, content, settings)
            next_state["revision"] = _safe_int(next_state.get("revision"), 0) + 1
            next_state["accepted_chapters"] = chapter_number
            next_state["next_chapter"] = chapter_number + 1
            next_state.setdefault("recent_summaries", []).append({"chapter_number": chapter_number, "title": title, "summary": str(actual.get("summary") or ""), "accepted_at": _utc_now()})
            next_state["recent_summaries"] = next_state["recent_summaries"][-10:]
            next_state["state_hash"] = _state_hash(next_state)

            safe_title = _slug(title, f"chapter-{chapter_number}")
            prose_relative = f"正文/第{chapter_number:04d}章_{safe_title}.md"
            prose_path = self.root / prose_relative
            ledger = self._ledger()
            stored_content = content.rstrip() + "\n"
            chapter_sha = _sha256(stored_content)
            record = {
                "chapter_number": chapter_number,
                "title": title,
                "path": prose_relative,
                "sha256": chapter_sha,
                "cjk_chars": _count_cjk(content),
                "summary": str(actual.get("summary") or ""),
                "deviation_score": deviation,
                "pre_state_hash": state["state_hash"],
                "post_state_hash": next_state["state_hash"],
                "accepted_at": _utc_now(),
                "lease_id": lease_id,
            }
            next_ledger = ledger + [record]
            transaction_id = f"txn_{chapter_number:04d}_{uuid.uuid4().hex}"
            prepared = {
                "schema": "tiangong.novel.chapter-transaction.v1",
                "transaction_id": transaction_id,
                "status": "prepared",
                "chapter_number": chapter_number,
                "prose_relative": prose_relative,
                "content": stored_content,
                "next_state": next_state,
                "next_ledger": next_ledger,
                "manifest_updates": {"accepted_chapters": chapter_number, "last_state_hash": next_state["state_hash"]},
                "lease_id": lease_id,
                "prepared_at": _utc_now(),
            }
            prepared_path = self.prepared_dir / f"{transaction_id}.json"
            _atomic_json(prepared_path, prepared)
            _atomic_text(prose_path, stored_content)
            _atomic_json(self.ledger_path, next_ledger)
            _atomic_json(self.state_path, next_state)
            manifest.update(prepared["manifest_updates"])
            self._write_manifest(manifest)
            try:
                lease_path.unlink()
            except FileNotFoundError:
                pass
            prepared["status"] = "committed"
            prepared["committed_at"] = _utc_now()
            _atomic_json(self.committed_dir / f"{transaction_id}.json", prepared)
            try:
                prepared_path.unlink()
            except FileNotFoundError:
                pass
        complete = chapter_number >= _safe_int(manifest.get("planned_chapters"), 0)
        return self._success(
            "CHAPTER_ACCEPTED",
            accepted=True,
            chapter_number=chapter_number,
            chapter_path=str(prose_path),
            chapter_sha256=chapter_sha,
            cjk_chars=record["cjk_chars"],
            deviation_score=deviation,
            state_hash=next_state["state_hash"],
            next_chapter=next_state["next_chapter"],
            emotional_triggers_created=triggers,
            complete=complete,
            next_action="novel.project.audit" if complete else ("novel.scene.design" if triggers else "novel.chapter.checkout"),
        )

    def recover(self) -> dict[str, Any]:
        self._require_project()
        recovered = []
        with self._locked():
            for path in sorted(self.prepared_dir.glob("*.json")):
                transaction = _read_json(path, {})
                if not isinstance(transaction, dict) or transaction.get("schema") != "tiangong.novel.chapter-transaction.v1":
                    raise NovelSystemError("CORRUPT_PREPARED_TRANSACTION", "Prepared chapter transaction is invalid", details={"path": str(path)})
                content = str(transaction.get("content") or "")
                prose_relative = str(transaction.get("prose_relative") or "")
                if not prose_relative or Path(prose_relative).is_absolute() or ".." in Path(prose_relative).parts:
                    raise NovelSystemError("UNSAFE_PREPARED_TRANSACTION", "Prepared transaction contains an unsafe prose path")
                _atomic_text(self.root / prose_relative, content if content.endswith("\n") else content.rstrip() + "\n")
                _atomic_json(self.ledger_path, transaction.get("next_ledger") or [])
                next_state = transaction.get("next_state")
                if not isinstance(next_state, dict) or next_state.get("state_hash") != _state_hash(next_state):
                    raise NovelSystemError("CORRUPT_PREPARED_STATE", "Prepared transaction state failed integrity verification")
                _atomic_json(self.state_path, next_state)
                manifest = self._manifest()
                manifest.update(transaction.get("manifest_updates") or {})
                self._write_manifest(manifest)
                lease_id = str(transaction.get("lease_id") or "")
                if lease_id:
                    try:
                        (self.leases_dir / f"{lease_id}.json").unlink()
                    except FileNotFoundError:
                        pass
                transaction["status"] = "committed"
                transaction["committed_at"] = _utc_now()
                _atomic_json(self.committed_dir / path.name, transaction)
                path.unlink()
                recovered.append(transaction.get("transaction_id"))
        return self._success("NOVEL_PROJECT_RECOVERED", recovered_transactions=recovered, recovered_count=len(recovered), state_hash=self._state().get("state_hash") if self.state_path.is_file() else None)

    def design_scene(self, args: Mapping[str, Any]) -> dict[str, Any]:
        trigger_id = str(args.get("trigger_id") or "")
        candidates = args.get("candidates")
        if not trigger_id or not isinstance(candidates, list) or not 2 <= len(candidates) <= 3 or not all(isinstance(item, Mapping) for item in candidates):
            raise NovelSystemError("INVALID_SCENE_CANDIDATES", "trigger_id and 2-3 candidate objects are required")
        with self._locked():
            manifest = self._manifest()
            if not manifest.get("compiled"):
                raise NovelSystemError("BLUEPRINT_NOT_COMPILED", "Compile the blueprint before scene design")
            state = self._state()
            trigger = (state.get("emotional_triggers") or {}).get(trigger_id)
            if not isinstance(trigger, MutableMapping) or trigger.get("status") != "pending":
                raise NovelSystemError("EMOTIONAL_TRIGGER_NOT_FOUND", f"Pending trigger not found: {trigger_id}")
            scored = []
            for index, candidate in enumerate(candidates):
                missing = [field for field in REQUIRED_SCENE_SCORES if not isinstance(candidate.get(field), (int, float)) or isinstance(candidate.get(field), bool)]
                required_text = [field for field in ("title", "payoff_type", "core_choice", "irreversible_cost", "permanent_consequence") if not _non_empty(candidate.get(field))]
                target_chapter = _safe_int(candidate.get("target_chapter"), 0)
                if missing or required_text or not _safe_int(trigger.get("target_chapter_min"), 0) <= target_chapter <= _safe_int(trigger.get("target_chapter_max"), 0):
                    scored.append({"index": index, "score": 0.0, "eligible": False, "missing_scores": missing, "missing_fields": required_text, "target_chapter": target_chapter})
                    continue
                values = []
                for field in REQUIRED_SCENE_SCORES:
                    raw = float(candidate[field])
                    values.append(max(0.0, min(100.0, raw * 100 if raw <= 1 else raw)))
                score = round(sum(values) / len(values), 3)
                scored.append({"index": index, "score": score, "eligible": score >= 70, "target_chapter": target_chapter})
            eligible = [item for item in scored if item["eligible"]]
            if not eligible:
                raise NovelSystemError("SCENE_DESIGN_REJECTED", "No scene candidate reached the minimum causal-emotional score", details={"candidates": scored, "minimum_score": 70})
            selected_meta = max(eligible, key=lambda item: (item["score"], -item["index"]))
            selected = deepcopy(candidates[selected_meta["index"]])
            selected.update({"trigger_id": trigger_id, "score": selected_meta["score"], "selected_at": _utc_now(), "status": "selected"})
            state.setdefault("selected_scenes", {})[trigger_id] = selected
            trigger["status"] = "designed"
            trigger["selected_score"] = selected_meta["score"]
            state["revision"] = _safe_int(state.get("revision"), 0) + 1
            self._write_state(state)
        return self._success("EMOTIONAL_SCENE_SELECTED", trigger_id=trigger_id, selected=selected, candidates=scored, state_hash=state["state_hash"], next_action="novel.chapter.checkout")

    def context_query(self, args: Mapping[str, Any]) -> dict[str, Any]:
        entity_type = str(args.get("entity_type") or "")
        entity_ids = args.get("entity_ids")
        if entity_type not in {"character", "event", "foreshadow", "relationship", "chapter", "emotion"} or not isinstance(entity_ids, list):
            raise NovelSystemError("INVALID_CONTEXT_QUERY", "entity_type and entity_ids are required")
        manifest = self._manifest()
        blueprint = self._blueprint()
        state = self._state() if manifest.get("compiled") else {}
        ledger = self._ledger()
        results = []
        for raw_id in entity_ids:
            if entity_type == "character":
                plan = next((item for item in blueprint.get("characters") or [] if isinstance(item, Mapping) and item.get("id") == raw_id), None)
                results.append({"id": raw_id, "plan": plan, "state": (state.get("characters") or {}).get(str(raw_id))})
            elif entity_type == "event":
                plan = next((item for item in blueprint.get("plot_events") or [] if isinstance(item, Mapping) and item.get("id") == raw_id), None)
                results.append({"id": raw_id, "plan": plan, "state": (state.get("events") or {}).get(str(raw_id))})
            elif entity_type == "chapter":
                number = _safe_int(raw_id, 0)
                plan = next((item for item in blueprint.get("chapters") or [] if isinstance(item, Mapping) and item.get("number") == number), None)
                accepted = next((item for item in ledger if item.get("chapter_number") == number), None)
                results.append({"id": number, "plan": plan, "accepted": accepted})
            elif entity_type == "foreshadow":
                plan = next((item for item in blueprint.get("foreshadows") or [] if isinstance(item, Mapping) and item.get("id") == raw_id), None)
                results.append({"id": raw_id, "plan": plan, "state": (state.get("foreshadows") or {}).get(str(raw_id))})
            elif entity_type == "relationship":
                plan = next((item for item in blueprint.get("relationships") or [] if isinstance(item, Mapping) and item.get("id") == raw_id), None)
                results.append({"id": raw_id, "plan": plan, "state": (state.get("relationships") or {}).get(str(raw_id))})
            else:
                plan = next((item for item in blueprint.get("emotional_accounts") or [] if isinstance(item, Mapping) and item.get("id") == raw_id), None)
                results.append({"id": raw_id, "plan": plan, "state": (state.get("emotional_accounts") or {}).get(str(raw_id)), "trigger": next((item for item in (state.get("emotional_triggers") or {}).values() if isinstance(item, Mapping) and item.get("account_id") == raw_id), None)})
        return self._success("NOVEL_CONTEXT_RETURNED", entity_type=entity_type, results=results, state_hash=state.get("state_hash") if state else None)

    def audit(self, args: Mapping[str, Any]) -> dict[str, Any]:
        manifest = self._manifest()
        blueprint = self._blueprint()
        report = self._blueprint_report(blueprint)
        problems = list(report["issues"])
        state = self._state() if manifest.get("compiled") else {}
        ledger = self._ledger()
        planned = _safe_int(manifest.get("planned_chapters"), 0)
        accepted_numbers = [item.get("chapter_number") for item in ledger]
        if accepted_numbers != list(range(1, len(ledger) + 1)):
            problems.append({"code": "CHAPTER_LEDGER_GAP", "path": "ledger", "message": "Accepted chapter ledger is not contiguous", "weight": 100})
        for record in ledger:
            path = self.root / str(record.get("path") or "")
            if not path.is_file() or _sha256(path.read_text(encoding="utf-8")) != record.get("sha256"):
                problems.append({"code": "CHAPTER_FILE_HASH_MISMATCH", "path": str(path), "message": "Accepted chapter file is missing or changed", "weight": 100})
        if state:
            next_chapter = _safe_int(state.get("next_chapter"), 1)
            for event_id, event in (state.get("events") or {}).items():
                if not isinstance(event, Mapping) or event.get("status") == "closed":
                    continue
                deadline = _safe_int(event.get("deadline_chapter"), 10**9)
                if event.get("closure_required") and deadline < next_chapter:
                    problems.append({"code": "OVERDUE_OPEN_EVENT", "path": f"state.events.{event_id}", "message": f"Event {event_id} is overdue", "weight": 60})
            pending = [item for item in (state.get("emotional_triggers") or {}).values() if isinstance(item, Mapping) and item.get("status") == "pending"]
            for trigger in pending:
                if _safe_int(trigger.get("target_chapter_max"), 10**9) < next_chapter:
                    problems.append({"code": "OVERDUE_EMOTIONAL_TRIGGER", "path": f"state.emotional_triggers.{trigger.get('trigger_id')}", "message": "Emotional payoff trigger is overdue", "weight": 40})
        complete = bool(manifest.get("compiled") and len(ledger) == planned and not problems)
        return self._success(
            "NOVEL_PROJECT_AUDITED",
            complete=complete,
            compiled=bool(manifest.get("compiled")),
            planned_chapters=planned,
            accepted_chapters=len(ledger),
            next_chapter=_safe_int(state.get("next_chapter"), 1) if state else 1,
            state_hash=state.get("state_hash") if state else None,
            blueprint_hash=_blueprint_hash(blueprint),
            energy=sum(_safe_int(item.get("weight"), 0) for item in problems),
            problems=problems,
            next_action="complete" if complete else ("novel.project.recover" if any(self.prepared_dir.glob("*.json")) else "novel.chapter.checkout" if manifest.get("compiled") else "novel.blueprint.assist"),
        )
