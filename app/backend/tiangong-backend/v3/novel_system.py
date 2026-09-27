"""Authoritative managed-novel transaction engine for Tiangong v3.

The language model proposes prose and structured deltas.  This module owns the
canonical project records, validates record shape and versions, issues
state-bound leases, and records chapters with recoverable transactions. Story
annotations are supplied by the caller; only the adversarial judge decides
whether prose meets the user's goal. This module deliberately
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


SYSTEM_VERSION = "3.1.1-observation-and-transaction"
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
                    # CRT byte-range locks may extend past EOF. No sentinel is
                    # needed, and writing in append mode grows the file on
                    # every retry even while another process holds the lock.
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
        return {"success": True, "ok": True, "status": status,
                "content_quality": "unassessed", "completion_authority": "adversarial_judge", **values}

    def create_project(self, args: Mapping[str, Any]) -> dict[str, Any]:
        title = str(args.get("title") or "").strip()
        genre = str(args.get("genre") or "").strip()
        planned = _safe_int(args.get("planned_chapters"), 0)
        target_words = _safe_int(args.get("target_words"), 0)
        if not title or not genre or planned < 1 or target_words < 1:
            raise NovelSystemError("INVALID_PROJECT_ARGUMENTS", "title, genre, planned_chapters, and target_words are required")
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
            all_planned_chapters_recorded=bool(state and _safe_int(state.get("next_chapter"), 1) > _safe_int(manifest.get("planned_chapters"), 0)),
            content_quality="unassessed", completion_authority="adversarial_judge",
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
            expected_type = list if section in LIST_SECTIONS else dict
            if not isinstance(value, expected_type):
                issues.append(_Issue("INVALID_SECTION_TYPE", f"{section} must be a {expected_type.__name__}", section, 40))
        if planned < 1 or target_words < 1:
            issues.append(_Issue("INVALID_PROJECT_SCOPE", "Project scope is missing", "project", 100))

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
                if (key == "number" and (type(value) is not int or value < 1)) or (key == "id" and (not isinstance(value, str) or not value)):
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
            if not isinstance(event_ids, list) or any(not isinstance(item, str) for item in event_ids):
                issues.append(_Issue("INVALID_EVENT_LIST", f"Chapter {number} event_ids must be a string array", f"chapters.{number}.event_ids", 30))
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
        advisory_codes = {"UNREFERENCED_EVENT", "INITIAL_LOCATION_MISMATCH", "PARTICIPANT_EVENT_OVERLAP",
                          "MISSING_ROUTE", "INSUFFICIENT_TRAVEL_TIME", "AGE_MISMATCH"}
        contract_issues = [item.to_dict() for item in sorted_issues if item.code not in advisory_codes]
        observations = [item.to_dict() for item in sorted_issues if item.code in advisory_codes]
        return {
            "energy": sum(item.weight for item in sorted_issues),
            "energy_scope": "legacy_diagnostic_only_not_a_quality_gate",
            "contract_issues": contract_issues, "observations": observations,
            "content_quality": "unassessed", "completion_authority": "adversarial_judge",
            "issues": [item.to_dict() for item in sorted_issues],
            "coverage": coverage,
            "coverage_incomplete": coverage_incomplete,
        }

    def assist_blueprint(self, args: Mapping[str, Any]) -> dict[str, Any]:
        self._manifest()
        blueprint = self._blueprint(rolling=False)
        report = self._blueprint_report(blueprint)
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
            ready_for_compile=not report["contract_issues"],
            observations=report["observations"], content_quality="unassessed",
            issues=report["issues"],
            repair_batch=repair_batch,
            repair_sequence=[item["repair"] for item in repair_batch],
            coverage=report["coverage"],
            next_action="novel.blueprint.compile" if not report["contract_issues"] else (repair_batch[0]["repair"].get("action") if repair_batch else "novel.blueprint.patch"),
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
            if report["contract_issues"]:
                raise NovelSystemError("BLUEPRINT_COMPILE_REJECTED", "Blueprint has invalid record shapes or references", details=report)
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
            revision_of = args.get("revision_of")
            ledger = self._ledger()
            revising = bool(revision_of)
            if revising and (not ledger or chapter_number != expected - 1 or ledger[-1].get("sha256") != revision_of):
                raise NovelSystemError("CHAPTER_REVISION_CONFLICT", "Only the latest recorded chapter can be revised against its current byte hash")
            if expected > planned and not revising:
                raise NovelSystemError("CHAPTER_PLAN_EXHAUSTED", "All planned slots are recorded; use revision_of for a last-chapter repair")
            if chapter_number != expected and not revising:
                raise NovelSystemError("OUT_OF_ORDER_CHAPTER", "Only the canonical next chapter may be checked out", details={"requested": chapter_number, "next_chapter": expected})
            trigger = self._active_trigger_due(state, chapter_number)
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
                "schema": "tiangong.novel.chapter-lease.v2",
                "lease_id": lease_id,
                "chapter_number": chapter_number,
                "pre_state_hash": state["state_hash"],
                "revision_of": revision_of if revising else None,
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
            revision_of=lease.get("revision_of"),
            revision_scope="latest chapter prose only; use empty actual; prior annotations retained" if revising else None,
            relevant={"events": events, "characters": characters, "locations": locations, "character_state": {key: value for key, value in (state.get("characters") or {}).items() if key in participant_ids}},
            due_open_events=due_events,
            historical_emotional_trigger=dict(trigger) if trigger else None,
            content_quality="unassessed", completion_authority="adversarial_judge",
            selected_scenes=selected_scenes,
            recent_summaries=list(state.get("recent_summaries") or [])[-3:],
            next_action="novel.chapter.submit",
        )

    def _validate_submission(
        self, *, blueprint: Mapping[str, Any], state: Mapping[str, Any],
        chapter: Mapping[str, Any], content: str, actual: Mapping[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Validate record shape/CAS; prose quality belongs to the final judge."""
        problems: list[dict[str, Any]] = []
        for field in ("events", "state_changes", "relationship_changes", "foreshadow_ops", "emotional_transactions"):
            values = actual.get(field, [])
            if not isinstance(values, list) or any(not isinstance(item, Mapping) for item in values):
                problems.append({"code": "INVALID_DELTA_LIST", "field": field})
        if problems:
            return {}, problems
        events = actual.get("events", [])
        event_ids = [event.get("id") for event in events]
        if any(not isinstance(value, str) or not value.strip() for value in event_ids):
            problems.append({"code": "INVALID_EVENT_ID"})
        elif len(event_ids) != len(set(event_ids)):
            problems.append({"code": "DUPLICATE_EVENT_ID"})
        for event in events:
            if not isinstance(event.get("status"), str) or event["status"] not in EVENT_STATUSES:
                problems.append({"code": "INVALID_EVENT_STATUS", "event_id": event.get("id")})
            for field in ("participants", "outcome_tags", "evidence_terms"):
                value = event.get(field, [])
                if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                    problems.append({"code": "INVALID_EVENT_LIST", "field": field})
            for field in ("start_tick", "duration_ticks"):
                if field in event and (type(event[field]) is not int or (field == "duration_ticks" and event[field] < 1)):
                    problems.append({"code": "INVALID_EVENT_TIME", "field": field})
        for change in actual.get("state_changes", []):
            character_id, field = change.get("character_id"), change.get("field")
            if not isinstance(character_id, str) or character_id not in state.get("characters", {}):
                problems.append({"code": "UNKNOWN_STATE_CHARACTER", "character_id": character_id})
                continue
            if not isinstance(field, str) or field not in STATE_FIELDS:
                problems.append({"code": "UNSUPPORTED_STATE_FIELD", "field": field})
                continue
            current = state["characters"][character_id].get(field)
            if "from" in change and change["from"] != current:
                problems.append({"code": "STATE_PRECONDITION_MISMATCH", "character_id": character_id, "field": field})
            operation = change.get("op", "set")
            list_field = field in {"injuries", "inventory", "knowledge"}
            if not isinstance(operation, str) or operation not in {"set", "add", "remove"}:
                problems.append({"code": "INVALID_STATE_OPERATION"})
            elif operation in {"add", "remove"}:
                if not list_field or not isinstance(change.get("items"), list) or not isinstance(current, list):
                    problems.append({"code": "INVALID_STATE_LIST_CHANGE", "field": field})
            elif ("to" not in change or (list_field and not isinstance(change["to"], list))
                  or (field == "alive" and type(change["to"]) is not bool)
                  or (field in {"location", "realm"} and not isinstance(change["to"], str))):
                problems.append({"code": "INVALID_STATE_VALUE", "field": field})
        if problems:
            return {}, problems
        settings = blueprint.get("settings") if isinstance(blueprint.get("settings"), Mapping) else {}
        planned_ids = {str(value) for value in chapter.get("event_ids", [])}
        supplied_ids = set(event_ids)
        observations = {
            "cjk_chars": _count_cjk(content), "content_sha256": _sha256(content),
            "configured_min_chapter_chars": settings.get("min_chapter_chars"),
            "planned_event_ids_not_reported": sorted(planned_ids - supplied_ids),
            "reported_event_ids_outside_plan": sorted(supplied_ids - planned_ids),
            "literal_evidence_matches": [{"event_id": event["id"], "term": term, "present": term in content}
                for event in events for term in event.get("evidence_terms", [])],
            "reported_delta_source": "caller_supplied_story_annotations_not_verified_prose_facts",
            "content_quality": "unassessed", "completion_authority": "adversarial_judge",
        }
        return observations, []

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

    @staticmethod
    def _record_emotion_annotations(state: MutableMapping[str, Any], actual: Mapping[str, Any], chapter_number: int) -> None:
        # Preserve the caller's creative notes, without scoring prose, updating
        # inferred emotional balances, or imposing a mandatory payoff scene.
        for annotation in actual.get("emotional_transactions", []):
            state.setdefault("emotion_annotations", []).append({
                "chapter_number": chapter_number, "source": "caller_supplied",
                "content_quality": "unassessed", "annotation": deepcopy(annotation),
            })

    def submit_chapter(self, args: Mapping[str, Any]) -> dict[str, Any]:
        lease_id = str(args.get("lease_id") or "")
        chapter_number = _safe_int(args.get("chapter_number"), 0)
        title = str(args.get("title") or "").strip()
        content = str(args.get("content") or "")
        actual = args.get("actual")
        if not lease_id or chapter_number < 1 or not title or not content.strip() or not isinstance(actual, Mapping):
            raise NovelSystemError("INVALID_CHAPTER_SUBMISSION", "lease_id, chapter_number, title, content, and actual are required")
        if not re.fullmatch(r"lease_[0-9a-f]{32}", lease_id):
            raise NovelSystemError("INVALID_CHAPTER_LEASE", "Use the lease identifier returned by checkout")
        with self._locked():
            manifest = self._manifest()
            if not manifest.get("compiled"):
                raise NovelSystemError("BLUEPRINT_NOT_COMPILED", "Compile the blueprint before submission")
            if any(self.prepared_dir.glob("*.json")):
                raise NovelSystemError("RECOVERY_REQUIRED", "Reconcile the prepared transaction before another submission")
            state = self._state()
            lease_path = self.leases_dir / f"{lease_id}.json"
            lease = _read_json(lease_path, {})
            if not isinstance(lease, dict) or lease.get("lease_id") != lease_id:
                raise NovelSystemError("LEASE_NOT_FOUND", "Chapter lease is missing or already consumed")
            if lease.get("schema") != "tiangong.novel.chapter-lease.v2":
                raise NovelSystemError("LEASE_VERSION_CHANGED", "Obtain a new v2 checkout; old acceptance semantics are not reinterpreted")
            if float(lease.get("expires_at_epoch") or 0) <= time.time():
                raise NovelSystemError("LEASE_EXPIRED", "Chapter lease expired; check out the canonical next chapter again", retryable=True)
            ledger = self._ledger()
            revision_of = lease.get("revision_of")
            revising = bool(revision_of)
            previous_record = ledger[-1] if revising and ledger else None
            if revising and (not previous_record or previous_record.get("sha256") != revision_of or actual):
                raise NovelSystemError("CHAPTER_REVISION_CONFLICT", "Prose revision needs unchanged prior hash and empty actual; historical state deltas are retained")
            expected = _safe_int(state.get("next_chapter"), 1) - (1 if revising else 0)
            if lease.get("chapter_number") != chapter_number or chapter_number != expected:
                raise NovelSystemError("STALE_CHAPTER_LEASE", "Lease no longer targets the canonical next chapter", details={"next_chapter": state.get("next_chapter")}, retryable=True)
            if lease.get("pre_state_hash") != state.get("state_hash"):
                raise NovelSystemError("STALE_STATE", "Canonical state changed after checkout", details={"lease_state_hash": lease.get("pre_state_hash"), "actual_state_hash": state.get("state_hash")}, retryable=True)
            blueprint = self._blueprint()
            if lease.get("rolling_blueprint_hash") != _blueprint_hash(blueprint):
                raise NovelSystemError("STALE_ROLLING_BLUEPRINT", "Rolling blueprint changed after checkout", retryable=True)
            chapter = next((item for item in blueprint.get("chapters") or [] if isinstance(item, Mapping) and item.get("number") == chapter_number), None)
            if chapter is None:
                raise NovelSystemError("CHAPTER_PLAN_NOT_FOUND", f"Chapter plan {chapter_number} does not exist")
            observations, problems = self._validate_submission(blueprint=blueprint, state=state, chapter=chapter, content=content, actual=actual)
            if problems:
                raise NovelSystemError("CHAPTER_SUBMISSION_REJECTED", "Chapter delta violates the record or state contract", details={"chapter_number": chapter_number, "problems": problems, "lease_reusable": True})

            next_state = deepcopy(state)
            if not revising:
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
                self._record_emotion_annotations(next_state, actual, chapter_number)
            next_state["revision"] = _safe_int(next_state.get("revision"), 0) + 1
            next_state["accepted_chapters"] = chapter_number
            next_state["next_chapter"] = chapter_number + 1
            if not revising:
                next_state.setdefault("recent_summaries", []).append({"chapter_number": chapter_number, "title": title, "summary": str(actual.get("summary") or ""), "accepted_at": _utc_now()})
            next_state["recent_summaries"] = next_state["recent_summaries"][-10:]
            next_state["state_hash"] = _state_hash(next_state)

            safe_title = _slug(title, f"chapter-{chapter_number}")
            prose_relative = previous_record["path"] if revising else f"正文/第{chapter_number:04d}章_{safe_title}.md"
            prose_path = self.root / prose_relative
            if prose_path.is_symlink() or not prose_path.resolve().is_relative_to(self.root):
                raise NovelSystemError("CHAPTER_PATH_CONFLICT", "Chapter output leaves the managed project")
            previous_content = None
            if revising:
                if not prose_path.is_file() or _sha256(prose_path.read_bytes()) != revision_of:
                    raise NovelSystemError("CHAPTER_REVISION_CONFLICT", "Chapter bytes changed after their recorded version")
                previous_content = prose_path.read_text(encoding="utf-8")
                observations["prior_annotations_retained"] = True
            elif prose_path.exists():
                raise NovelSystemError("CHAPTER_PATH_CONFLICT", "Chapter output exists; reconcile before submitting")
            stored_content = content.rstrip() + "\n"
            chapter_sha = _sha256(stored_content)
            record = {
                "chapter_number": chapter_number,
                "title": title,
                "path": prose_relative,
                "sha256": chapter_sha,
                "cjk_chars": _count_cjk(content),
                "summary": previous_record.get("summary", "") if revising else str(actual.get("summary") or ""),
                "revision_of": revision_of,
                "observations": observations,
                "content_quality": "unassessed",
                "pre_state_hash": state["state_hash"],
                "post_state_hash": next_state["state_hash"],
                "accepted_at": _utc_now(),
                "lease_id": lease_id,
            }
            next_ledger = (ledger[:-1] if revising else ledger) + [record]
            transaction_id = f"txn_{chapter_number:04d}_{uuid.uuid4().hex}"
            prepared = {
                "schema": "tiangong.novel.chapter-transaction.v2",
                "transaction_id": transaction_id,
                "status": "prepared",
                "chapter_number": chapter_number,
                "prose_relative": prose_relative,
                "content": stored_content,
                "previous_record": previous_record,
                "previous_content": previous_content,
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
            "CHAPTER_COMMITTED",
            committed=True, revision_of=revision_of,
            chapter_number=chapter_number,
            chapter_path=str(prose_path),
            chapter_sha256=chapter_sha,
            cjk_chars=record["cjk_chars"],
            observations=observations,
            content_quality="unassessed", completion_authority="adversarial_judge",
            state_hash=next_state["state_hash"],
            next_chapter=next_state["next_chapter"],
            all_planned_chapters_recorded=complete,
            next_action="novel.project.audit" if complete else "novel.chapter.checkout",
        )

    def recover(self) -> dict[str, Any]:
        """Replay only a validated prepared byte transaction, including legacy v1.

        This establishes storage consistency, never historical prose approval.
        All checks for each transaction precede the first recovery write.
        """
        self._require_project()
        recovered = []
        with self._locked():
            for path in sorted(self.prepared_dir.glob("*.json")):
                transaction = _read_json(path, {})
                if (not isinstance(transaction, dict) or transaction.get("schema") not in
                        ("tiangong.novel.chapter-transaction.v1", "tiangong.novel.chapter-transaction.v2")):
                    raise NovelSystemError("CORRUPT_PREPARED_TRANSACTION", "Unknown prepared transaction schema")
                txn_id, lease_id = transaction.get("transaction_id"), transaction.get("lease_id")
                content, relative = transaction.get("content"), transaction.get("prose_relative")
                if (not isinstance(txn_id, str) or not re.fullmatch(r"txn_[0-9]+_[0-9a-f]{32}", txn_id)
                        or path.stem != txn_id or not isinstance(lease_id, str)
                        or not re.fullmatch(r"lease_[0-9a-f]{32}", lease_id)
                        or not isinstance(content, str) or not isinstance(relative, str)):
                    raise NovelSystemError("CORRUPT_PREPARED_TRANSACTION", "Prepared transaction identity or content is invalid")
                prose_path = self.root / relative
                if (Path(relative).is_absolute() or ".." in Path(relative).parts
                        or len(Path(relative).parts) != 2 or Path(relative).parts[0] != "正文"
                        or prose_path.suffix != ".md" or prose_path.is_symlink()
                        or not prose_path.resolve().is_relative_to(self.root)):
                    raise NovelSystemError("UNSAFE_PREPARED_TRANSACTION", "Prepared prose path is outside its chapter directory")
                next_state, next_ledger = transaction.get("next_state"), transaction.get("next_ledger")
                if not isinstance(next_state, dict) or next_state.get("state_hash") != _state_hash(next_state):
                    raise NovelSystemError("CORRUPT_PREPARED_STATE", "Prepared state failed integrity verification")
                if not isinstance(next_ledger, list) or not next_ledger or not all(isinstance(row, dict) for row in next_ledger):
                    raise NovelSystemError("CORRUPT_PREPARED_TRANSACTION", "Prepared ledger is invalid")
                last = next_ledger[-1]
                number = transaction.get("chapter_number")
                updates = transaction.get("manifest_updates")
                if (type(number) is not int or number != len(next_ledger)
                        or [row.get("chapter_number") for row in next_ledger] != list(range(1, number + 1))
                        or last.get("path") != relative or last.get("sha256") != _sha256(content)
                        or last.get("lease_id") != lease_id or last.get("post_state_hash") != next_state["state_hash"]
                        or next_state.get("next_chapter") != number + 1 or next_state.get("accepted_chapters") != number
                        or updates != {"accepted_chapters": number, "last_state_hash": next_state["state_hash"]}):
                    raise NovelSystemError("CORRUPT_PREPARED_TRANSACTION", "Prepared content, ledger and state do not agree")
                previous_record, previous_content = transaction.get("previous_record"), transaction.get("previous_content")
                before_ledger = next_ledger[:-1]
                allowed_bytes = [content.encode("utf-8")]
                if previous_record is not None:
                    if (transaction["schema"] != "tiangong.novel.chapter-transaction.v2"
                            or not isinstance(previous_record, dict) or not isinstance(previous_content, str)
                            or previous_record.get("chapter_number") != number or previous_record.get("path") != relative
                            or _sha256(previous_content) != previous_record.get("sha256")
                            or last.get("revision_of") != previous_record.get("sha256")):
                        raise NovelSystemError("CORRUPT_PREPARED_TRANSACTION", "Revision has inconsistent prior version evidence")
                    before_ledger = before_ledger + [previous_record]
                    allowed_bytes.append(previous_content.encode("utf-8"))
                elif last.get("revision_of"):
                    raise NovelSystemError("CORRUPT_PREPARED_TRANSACTION", "Revision is missing its prior record")
                current_state, current_ledger = self._state(), self._ledger()
                if (current_state.get("state_hash") not in (last.get("pre_state_hash"), last.get("post_state_hash"))
                        or current_ledger not in (before_ledger, next_ledger)):
                    raise NovelSystemError("RECOVERY_VERSION_CONFLICT", "Current state or ledger has changed; reconcile before replay")
                if prose_path.exists() and (not prose_path.is_file() or prose_path.read_bytes() not in allowed_bytes):
                    raise NovelSystemError("RECOVERY_CONTENT_CONFLICT", "Current chapter bytes differ; recovery will not overwrite them")
                manifest = self._manifest()
                _atomic_text(prose_path, content)
                _atomic_json(self.ledger_path, next_ledger)
                _atomic_json(self.state_path, next_state)
                manifest.update(updates)
                self._write_manifest(manifest)
                (self.leases_dir / f"{lease_id}.json").unlink(missing_ok=True)
                transaction["status"] = "committed"
                transaction["committed_at"] = _utc_now()
                _atomic_json(self.committed_dir / path.name, transaction)
                path.unlink()
                recovered.append(txn_id)
        return self._success("NOVEL_PROJECT_RECOVERED", recovered_transactions=recovered, recovered_count=len(recovered),
            state_hash=self._state().get("state_hash") if self.state_path.is_file() else None,
            content_quality="unassessed", completion_authority="adversarial_judge")

    def design_scene(self, args: Mapping[str, Any]) -> dict[str, Any]:
        candidates, selected_index = args.get("candidates"), args.get("selected_index")
        if (not isinstance(candidates, list) or not candidates or not all(isinstance(item, Mapping) for item in candidates)
                or type(selected_index) is not int or not 0 <= selected_index < len(candidates)):
            raise NovelSystemError("INVALID_SCENE_SELECTION", "candidates and an explicit in-range selected_index are required")
        selected = deepcopy(candidates[selected_index])
        if not _non_empty(selected.get("title")) or type(selected.get("target_chapter")) is not int:
            raise NovelSystemError("INVALID_SCENE_SELECTION", "selected candidate requires title and integer target_chapter")
        trigger_id = str(args.get("trigger_id") or "")
        with self._locked():
            if not self._manifest().get("compiled"):
                raise NovelSystemError("BLUEPRINT_NOT_COMPILED", "Compile the blueprint before recording a scene")
            state = self._state()
            if args.get("expected_state_hash") != state["state_hash"]:
                raise NovelSystemError("STALE_STATE", "Scene selection requires the current state hash", retryable=True)
            chapters = {item.get("number") for item in self._blueprint().get("chapters", [])}
            if selected["target_chapter"] not in chapters or selected["target_chapter"] < state["next_chapter"]:
                raise NovelSystemError("INVALID_SCENE_CHAPTER", "Choose an uncommitted chapter in the declared plan")
            trigger = state.get("emotional_triggers", {}).get(trigger_id) if trigger_id else None
            if trigger_id and (not isinstance(trigger, MutableMapping) or trigger.get("status") != "pending"):
                raise NovelSystemError("EMOTIONAL_TRIGGER_NOT_FOUND", "The named historical trigger is not pending")
            scene_id = trigger_id or "scene_" + uuid.uuid4().hex
            selected.update({"scene_id": scene_id, "selection_source": "caller", "selected_at": _utc_now(),
                             "status": "recorded", "content_quality": "unassessed"})
            state.setdefault("selected_scenes", {})[scene_id] = selected
            if trigger is not None:
                trigger["status"] = "caller_selected"
            state["revision"] = _safe_int(state.get("revision"), 0) + 1
            self._write_state(state)
        return self._success("SCENE_SELECTION_RECORDED", scene_id=scene_id, selected=selected,
            selected_index=selected_index, state_hash=state["state_hash"], content_quality="unassessed",
            completion_authority="adversarial_judge", next_action="novel.chapter.checkout")

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
                results.append({"id": number, "plan": plan, "recorded": accepted, "content_quality": "unassessed"})
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
        recorded = bool(manifest.get("compiled") and len(ledger) == planned)
        return self._success(
            "NOVEL_PROJECT_AUDITED",
            all_planned_chapters_recorded=recorded,
            content_quality="unassessed", completion_authority="adversarial_judge",
            compiled=bool(manifest.get("compiled")),
            planned_chapters=planned,
            accepted_chapters=len(ledger),
            next_chapter=_safe_int(state.get("next_chapter"), 1) if state else 1,
            state_hash=state.get("state_hash") if state else None,
            blueprint_hash=_blueprint_hash(blueprint),
            energy=sum(_safe_int(item.get("weight"), 0) for item in problems),
            problems=problems,
            next_action="preview.generate" if recorded else ("novel.project.recover" if any(self.prepared_dir.glob("*.json")) else "novel.chapter.checkout" if manifest.get("compiled") else "novel.blueprint.assist"),
        )
