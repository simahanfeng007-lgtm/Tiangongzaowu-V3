"""Embedded and standalone hosts for the Tiangong Life Kernel.

The kernel is logically independent and owns its persistence/writer lease.  In
normal desktop mode it is hosted inside the 7184 process; the same class can be
wrapped by the standalone 7175 development server without changing contracts.
"""
from __future__ import annotations

import errno
import json
import os
import random
import re
import stat
import threading
import time
import traceback
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from contracts import (
    CausalContextItem,
    CausalEpisodeVNext,
    LifeAuthorityHead,
    LifeRevisionVector,
    RootExperienceHead,
    RunLifeBinding,
    canonical_json_bytes,
    canonical_sha256,
)

from .autonomous_tasks import (
    ACTIVE_TASK_STATES,
    DEFAULT_ACTIVITY_TYPES,
    ALLOWED_TASK_STATES,
    autonomy_activity_catalog,
    default_autonomy_state,
    derive_task_candidates,
    materialize_tasks,
    normalize_activity_types,
    normalize_autonomy_state,
    update_task_status,
)
from . import complete_core as complete_life_core
from .complete_core import CompleteLifeSystem, LifeCoreError, atomic_json, utc_now
from .complete_scheduler import EmbeddedLifeScheduler
from .context_api import LifeContextApiError, LifeContextCompileAuthorizeApi, LifeProjectionInputs
from .identity_migration import migrate_legacy_identities
from .activity_scope import build_activity_scope, normalize_repository_evidence
from .legacy_fusion import default_body, default_schedule, normalize_body, normalize_schedule, relationship_projection
from .panel_projection import (
    action_value_projection,
    boundary_projection,
    catalog_tasks_for_day,
    fallback_context_projection,
    long_term_goals,
    model_budget_projection,
    motivation_drift_projection,
    preference_projection,
    record_day,
    records_for_day,
    reflection_projection,
)
from .artifact_executor import (
    ARTIFACT_SCHEMA,
    ArtifactExecutorError,
    compile_artifact,
    delete_artifact_bundle,
    persist_artifact_bundle,
    persist_current_pointer,
    publish_artifact,
    rollback_pointer,
)
from .learning_workflow import build_draft, confirm_draft, discard_draft, publish_draft
from .learning_executor import execute_learning_preview
from .capability_health import (
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_PATCH_ROUNDS,
    attach_health,
    degrade_pointer,
    ingest_outcome,
    propose_patch,
    reactivate_pointer,
    runtime_usable,
    settle_patch,
)
from .memory_classification import classify_memory, normalize_relations
from .memory_lifecycle import advance_lifecycle, initial_lifecycle, normalize_lifecycle, recall_lifecycle
from .memory_coordinator import MemoryCoordinator
from .proactive_initiative import evaluate_proactive_candidate
from .store import LifeShadowStore, LifeShadowStoreError
from .cognition import CognitionTrigger, UnifiedCognitionShadow
from .temperament import (
    normalize_temperament_state,
    public_temperament_projection,
)
from .transient_affect import (
    appraise_user_turn,
    decay_transient_affect,
    normalize_transient_affect,
)


LIFE_API_CONTRACT = "tiangong.life.api.v2"
LIFE_COMPONENT_ID = "tiangong-life-service"
EMBEDDED_LIFE_BUILD_ID = "tiangong-v3.0.3-embedded-life-source-20260722"
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_CONTRACT_MEMORY_ID = re.compile(r"mem_[0-9a-f]{64}")
_MEMORY_CONTRACT_PLAINTEXT_KEYS = (
    "memory_id",
    "memory_type",
    "requested_memory_type",
    "content",
    "provenance",
    "relations",
    "classification",
    "epistemic_status",
    "confidence_milli",
    "priority",
    "life_id",
)
_CONTRACT_ASSERTION_KINDS = {
    "observation",
    "user_preference",
    "hard_constraint",
    "goal",
    "relationship",
    "skill",
    "causal_summary",
    "legacy",
}
_CONTRACT_RETENTION_CLASSES = {
    "EPHEMERAL_TOOL",
    "ACTIVE_WORKING",
    "CHECKPOINT",
    "TERMINAL_RESULT",
    "LONG_TERM_MEMORY",
    "LEGAL_HOLD",
}
_CONTRACT_EPISTEMIC_STATUSES = {"observed", "user_asserted", "hypothesis", "verified"}
_MAX_MEMORY_CANDIDATE_BATCH = 64
_MAX_MEMORY_CANDIDATE_BYTES = 64 * 1024
_MAX_MEMORY_CANDIDATES_QUEUED = 500
_MAX_MEMORY_PAYLOAD_BYTES = 1024 * 1024
_MAX_SEARCH_QUERY_BYTES = 64 * 1024
_MAX_SEARCH_FILTER_ITEMS = 256
_MAX_TASK_RESULT_BYTES = 1024 * 1024
_MEMORY_SECRET = re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}\b|(?i:(?:api[_ -]?key|token|password)\s*[:=]\s*[^\s,;]+)")
_INPUT_TEMPLATE = re.compile(r"\{\{input\.([A-Za-z0-9_.-]{1,120})\}\}")


def _template_syntax_ok(value: Any) -> bool:
    """静态校验参数模板语法：括号闭合且占位符符合 {{input.x}} 规范。

    不做输入存在性检查：运行时输入是否齐全属于执行期职责，验证门只
    拦截结构损坏的补丁（未闭合括号、错误占位符命名）。
    """
    if isinstance(value, str):
        if value.count("{{") != value.count("}}"):
            return False
        for match in re.finditer(r"\{\{([^{}]*)\}\}", value):
            token = match.group(1).strip()
            if not re.fullmatch(r"input\.[A-Za-z0-9_.-]+", token):
                return False
        return True
    if isinstance(value, Mapping):
        return all(_template_syntax_ok(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_template_syntax_ok(item) for item in value)
    return True


def _capability_panel_projection(scope: Mapping[str, Any], *, today: str) -> dict[str, Any]:
    """能力面板投影：给每个能力行挂指针状态（激活/降级/禁用）。

    activation_status 优先于 artifact.status：已发布但被自动降级的能力
    不再计入 active_skills / released_tools，避免前端把降级能力显示为可用。
    """
    raw = scope.get("capabilities")
    if not isinstance(raw, Mapping):
        return {"by_id": {}, "active_skills": [], "released_tools": [], "history": [], "usage": {}}
    pointers = scope.get("capability_pointers")
    if not isinstance(pointers, Mapping):
        pointers = {}

    def enrich(row: Mapping[str, Any]) -> dict[str, Any]:
        value = deepcopy(dict(row))
        lineage_id = str(row.get("lineage_id") or "")
        pointer = pointers.get(lineage_id)
        if (
            isinstance(pointer, Mapping)
            and pointer.get("current_artifact_id") == row.get("artifact_id")
        ):
            value["activation_status"] = str(pointer.get("status") or "pending")
            value["runtime_usable"] = pointer.get("status") == "active"
            if pointer.get("status") == "degraded":
                value["degraded_reason"] = str(pointer.get("degraded_reason") or "")
        return value

    by_id = {
        key: enrich(row)
        for key, row in raw.items()
        if isinstance(row, Mapping)
    }
    rows = list(by_id.values())
    return {
        "by_id": by_id,
        "active_skills": [
            row for row in rows
            if row.get("kind") == "skill" and row.get("activation_status") == "active"
        ],
        "released_tools": [
            row for row in rows
            if row.get("kind") == "tool" and row.get("activation_status") == "active"
        ],
        "history": [
            deepcopy(row)
            for row in rows
            if record_day(row) == today
        ],
        "usage": {},
    }


def _is_life_generated_capability(artifact: Mapping[str, Any]) -> bool:
    """Recognize both new ownership-tagged and pre-tag learning artifacts."""
    return (
        artifact.get("origin") == "life_learning"
        or (
            artifact.get("schema") == ARTIFACT_SCHEMA
            and artifact.get("kind") in {"skill", "tool"}
            and bool(artifact.get("learning_id"))
        )
    )


class EmbeddedLifeError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class LifeWriterLease:
    """One writer per life data root, regardless of embedded/standalone host."""

    def __init__(self, path: Path, stream: object, instance_id: str, mode: str) -> None:
        self.path = path
        self._stream = stream
        self.instance_id = instance_id
        self.mode = mode
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    @staticmethod
    def _lock(stream: object) -> None:
        descriptor = stream.fileno()  # type: ignore[attr-defined]
        if os.name == "nt":
            import msvcrt

            stream.seek(0)  # type: ignore[attr-defined]
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(stream: object) -> None:
        descriptor = stream.fileno()  # type: ignore[attr-defined]
        if os.name == "nt":
            import msvcrt

            stream.seek(0)  # type: ignore[attr-defined]
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)

    @classmethod
    def acquire(cls, data_root: Path, *, mode: str) -> "LifeWriterLease":
        data_root.mkdir(parents=True, exist_ok=True)
        if data_root.is_symlink() or not data_root.is_dir():
            raise EmbeddedLifeError("life.writer.root_unsafe", status=409)
        path = data_root / "life.writer.lock"
        # The writer lock is itself an authority boundary.  Never follow a
        # pre-created symlink/reparse-point to an attacker-selected file.
        try:
            if path.exists() and path.is_symlink():
                raise EmbeddedLifeError("life.writer.lock_unsafe", status=409)
        except OSError as exc:
            raise EmbeddedLifeError("life.writer.lock_unsafe", status=409) from exc

        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOINHERIT", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        stream = None
        instance_id = "life-" + uuid.uuid4().hex
        try:
            descriptor = os.open(path, flags, 0o600)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise EmbeddedLifeError("life.writer.lock_unsafe", status=409)
            # O_NOFOLLOW is unavailable on Windows.  Re-check the directory
            # entry after opening and reject any reparse/symlink substitution.
            if path.is_symlink():
                raise EmbeddedLifeError("life.writer.lock_unsafe", status=409)
            stream = os.fdopen(descriptor, "r+b", buffering=0)
            descriptor = None
            if opened.st_size == 0:
                stream.write(b"0")
                stream.flush()
                os.fsync(stream.fileno())
            cls._lock(stream)
            os.chmod(path, 0o600)
            atomic_json(
                data_root / "life.writer.json",
                {
                    "schema": "tiangong.life.writer-lease.v1",
                    "instance_id": instance_id,
                    "mode": mode,
                    "pid": os.getpid(),
                    "started_at": utc_now(),
                },
            )
            return cls(path, stream, instance_id, mode)
        except EmbeddedLifeError:
            if stream is not None:
                stream.close()
            elif descriptor is not None:
                os.close(descriptor)
            raise
        except OSError as exc:
            if stream is not None:
                stream.close()
            elif descriptor is not None:
                os.close(descriptor)
            code = (
                "life.writer.lock_unsafe"
                if getattr(exc, "errno", None) == errno.ELOOP
                else "life.writer.already_owned"
            )
            raise EmbeddedLifeError(code, status=409) from exc

    def release(self) -> None:
        if not self._active:
            return
        try:
            self._unlock(self._stream)
        finally:
            self._stream.close()
            self._active = False


@dataclass(frozen=True, slots=True)
class _LifePaths:
    data_root: Path
    runtime_root: Path
    state_file: Path
    authority_store: Path
    artifact_root: Path


class EmbeddedLifeRuntime:
    """Complete Life API v2 contract hosted without a second HTTP listener."""

    def __init__(
        self,
        *,
        data_root: Path,
        runtime_root: Path,
        mode: str = "embedded",
        device_id: str = "",
    ) -> None:
        raw_data_root = data_root.expanduser()
        raw_runtime_root = runtime_root.expanduser()
        try:
            if raw_data_root.is_symlink():
                raise EmbeddedLifeError("life.writer.root_unsafe", status=409)
            if raw_runtime_root.is_symlink():
                raise EmbeddedLifeError("life.runtime.root_unsafe", status=409)
        except OSError as exc:
            raise EmbeddedLifeError("life.root.unsafe", status=409) from exc
        data_root = raw_data_root.resolve(strict=False)
        runtime_root = raw_runtime_root.resolve(strict=False)
        if data_root == Path(data_root.anchor) or runtime_root == Path(runtime_root.anchor):
            raise EmbeddedLifeError("life.root.unsafe", status=409)
        runtime_root.mkdir(parents=True, exist_ok=True)
        self.paths = _LifePaths(
            data_root=data_root,
            runtime_root=runtime_root,
            state_file=runtime_root / "embedded-life-state.json",
            authority_store=runtime_root / "life-authority.shadow.sqlite3",
            artifact_root=data_root / "artifacts",
        )
        self.mode = mode
        self._lock = threading.RLock()
        self._started_ns = time.monotonic_ns()
        self._closed = True
        self._closing = False
        self._projection_dirty_reason = ""
        self._lease: LifeWriterLease | None = None
        self.authority_store: LifeShadowStore | None = None
        self._memory_contract_synced: set[str] = set()
        # _persist 去抖状态（见 _persist 注释：投影高频写 O(n^2) 治理）
        self._last_persist_monotonic = 0.0
        self._persist_pending = False
        self._memory_contract_divergences: dict[str, int] = {}
        self._memory_contract_rebuilt: dict[str, int] = {}
        # 就绪探针增量缓存：journal 全链校验与 autonomy 全量哈希只在
        # 头/快照指纹变化时重算，避免每次 /ready 轮询付出 7-12s 全量成本。
        self._journal_verify_cache: dict[str, Any] = {}
        self._journal_verify_sig: tuple[Any, ...] = ()
        self._autonomy_health_cache: dict[str, Any] = {}
        self._autonomy_health_sig: tuple[Any, ...] = ()
        self.scheduler: EmbeddedLifeScheduler | None = None
        self._autonomy_decider: Any = None
        self._learning_decider: Any = None
        self._cognition_decider: Any = None
        self._cognition_shadow: UnifiedCognitionShadow | None = None
        self._artifact_action_catalog_provider: Any = None
        self._artifact_publisher: Any = None
        self._capability_workspace_mapper: Any = None
        self._capability_workspace_remover: Any = None
        self._capability_workspace_marker: Any = None
        self._capability_patch_decider: Any = None
        self._artifact_invoker: Any = None
        self._learning_researcher: Any = None
        self._learning_synthesizer: Any = None
        self._learning_share_writer: Any = None
        self._proactive_decider: Any = None
        self._proactive_expression_writer: Any = None
        self._proactive_world_provider: Any = None
        self._world_identity_provider: Any = None
        try:
            self._lease = LifeWriterLease.acquire(data_root, mode=mode)
            self.system = CompleteLifeSystem(data_root, device_id=device_id)
            active_life_id = self._ensure_usable_active_life()
            self.authority_store = LifeShadowStore.open(
                self.paths.authority_store,
                create=True,
                now_ms=time.time_ns() // 1_000_000,
            )
            self._cognition_shadow = UnifiedCognitionShadow(
                self.authority_store,
                cognition_decider=None,
                binding_factory=None,
            )
            self._state = self._load_state()
            heartbeat_recovered = self._reconcile_scheduler_heartbeat(active_life_id)
            recovered_inflight = False
            identity_states = self._state.get("identity_states")
            if isinstance(identity_states, Mapping):
                for identity_scope in identity_states.values():
                    if not isinstance(identity_scope, dict):
                        continue
                    scheduler_state = identity_scope.get("scheduler")
                    if not isinstance(scheduler_state, dict):
                        continue
                    for key in (
                        "autonomy_decision_inflight",
                        "learning_decision_inflight",
                        "self_iteration_decision_inflight",
                        "greeting_inflight",
                        "proactive_decision_inflight",
                    ):
                        if scheduler_state.get(key) is True:
                            scheduler_state[key] = False
                            recovered_inflight = True
            projection_changed = self._reconcile_authoritative_journal(active_life_id)
            classification_changed = self._ensure_memory_classification(active_life_id)
            memory_contract_changed = self._reconcile_memory_contract(active_life_id)
            if projection_changed or classification_changed or memory_contract_changed or heartbeat_recovered or recovered_inflight:
                self._persist(active_life_id)
            heartbeat_seconds = float(os.environ.get("TIANGONG_LIFE_HEARTBEAT_SECONDS") or 30.0)
            self.scheduler = EmbeddedLifeScheduler(
                self._scheduler_tick,
                interval_seconds=heartbeat_seconds,
            )
            self.scheduler.start()
            self._closed = False
            self._closing = False
        except Exception as init_error:
            cleanup_errors: list[Exception] = []
            scheduler = self.scheduler
            if scheduler is not None:
                try:
                    scheduler.stop()
                except Exception as exc:
                    cleanup_errors.append(exc)
            store = self.authority_store
            if store is not None:
                try:
                    store.close()
                except Exception as exc:
                    cleanup_errors.append(exc)
            lease = self._lease
            if lease is not None:
                try:
                    lease.release()
                except Exception as exc:
                    cleanup_errors.append(exc)
            if cleanup_errors:
                init_error.add_note(
                    "life kernel partial-initialization cleanup failed: "
                    + ",".join(type(exc).__name__ for exc in cleanup_errors)
                )
            raise

    @classmethod
    def from_environment(
        cls,
        *,
        gateway_state_root: Path,
        mode: str = "embedded",
        gateway_environment: str = "development",
        environ: Mapping[str, str] | None = None,
    ) -> "EmbeddedLifeRuntime":
        env = dict(os.environ if environ is None else environ)
        explicit_data_root = str(env.get("TIANGONG_LIFE_DATA_ROOT") or "").strip()
        if explicit_data_root:
            data_root = Path(explicit_data_root)
        elif str(gateway_environment).strip().lower() == "test":
            # Tests and verification subprocesses must never contend for the
            # real user's writer lease or mutate the persistent life identity.
            data_root = gateway_state_root.parent / "life-data"
        elif str(env.get("TIANGONG_DOCUMENTS_PATH") or "").strip():
            data_root = Path(str(env["TIANGONG_DOCUMENTS_PATH"])) / "天工造物生命数据"
        else:
            # Non-desktop embedders have no OS-known-folder authority. Keep
            # their data beside the caller-provided state instead of guessing
            # a localized or redirected Documents path.
            data_root = gateway_state_root.parent / "life-data"
        runtime_root = Path(
            env.get("TIANGONG_LIFE_RUNTIME_ROOT")
            or (gateway_state_root.parent / "complete-life")
        )
        # The old standalone 7175 bootstrap migrated v1 identities before
        # constructing its writable life core.  The single-process 7184 host
        # must preserve the same ordering: migration precedes both writer-lease
        # acquisition and the fresh-identity fallback, otherwise a valid
        # organism id is silently replaced on the first embedded launch.
        migration_env = dict(env)
        migration_env["TIANGONG_LIFE_DATA_ROOT"] = str(data_root)
        legacy_runtime_root = str(
            env.get("TIANGONG_EXECUTION_RUNTIME_ROOT")
            or env.get("TIANGONG_LIFE_KERNEL_ROOT")
            or ""
        ).strip()
        legacy_transaction_root = str(
            env.get("TIANGONG_EXECUTION_LIFE_ROOT")
            or env.get("TIANGONG_LIFE_ROOT")
            or ""
        ).strip()
        if legacy_runtime_root:
            migration_env["TIANGONG_EXECUTION_RUNTIME_ROOT"] = legacy_runtime_root
        if legacy_transaction_root:
            migration_env["TIANGONG_EXECUTION_LIFE_ROOT"] = legacy_transaction_root
        migration_report = migrate_legacy_identities(
            complete_life_core,
            migration_env,
        )
        if migration_report.get("status") == "failed":
            raise EmbeddedLifeError("life.identity_migration_failed", status=503)

        runtime = cls(data_root=data_root, runtime_root=runtime_root, mode=mode)
        runtime.identity_migration_report = deepcopy(migration_report)
        return runtime

    def _record_life_recovery(self, *, prior_life_id: str, reason_code: str, replacement_life_id: str) -> None:
        """Leave a local audit record without rewriting unreadable life data."""

        atomic_json(
            self.paths.runtime_root / "life-recovery.json",
            {
                "schema": "tiangong.life.unusable-recovery.v1",
                "prior_life_id": prior_life_id,
                "reason_code": reason_code,
                "replacement_life_id": replacement_life_id,
                "recovered_at": utc_now(),
            },
        )

    def _reconcile_scheduler_heartbeat(self, life_id: str) -> bool:
        """Advance the local heartbeat projection from the signed journal.

        A crash after appending a heartbeat but before persisting
        ``embedded-life-state.json`` leaves the next startup reusing an
        existing idempotency key.  The journal is authoritative, so this is a
        projection-only repair: it never rewrites history and only moves the
        local counter forward when a fully matching signed event proves it.
        """

        prefix = f"heartbeat:{life_id}:"
        recovered_count = 0
        for event in self.system.journal.events(life_id):
            if str(event.get("event_type") or "") != "life.heartbeat":
                continue
            key = str(event.get("idempotency_key") or "")
            if not key.startswith(prefix):
                continue
            try:
                count = int(key[len(prefix):])
            except (TypeError, ValueError):
                continue
            payload = event.get("payload")
            if (
                count < 0
                or not isinstance(payload, Mapping)
                or payload.get("heartbeat_count") != count
            ):
                continue
            recovered_count = max(recovered_count, count)
        scope = self._scope_state(life_id)
        scheduler = scope.setdefault("scheduler", {})
        current = int(scheduler.get("heartbeat_count") or 0)
        if recovered_count <= current:
            return False
        scheduler["heartbeat_count"] = recovered_count
        scheduler["last_reason"] = "life.scheduler.reconciled_from_journal"
        return True

    def _replace_unreadable_registry(self, reason_code: str) -> None:
        """Preserve an unreadable registry and establish a fresh registry authority."""

        registry = self.paths.data_root / "life_registry.json"
        if registry.is_file():
            backup_dir = self.paths.data_root / "recovery"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"life_registry.unusable.{int(time.time() * 1000)}.{uuid.uuid4().hex}.json.bak"
            os.replace(registry, backup)
            os.chmod(backup, 0o600)
        self.system = CompleteLifeSystem(self.paths.data_root)
        self._record_life_recovery(
            prior_life_id="",
            reason_code=reason_code,
            replacement_life_id="",
        )

    def _ensure_usable_active_life(self) -> str:
        """Use a verified active life, or safely create one when none is usable.

        A bad legacy file must never be overwritten just to make the desktop
        boot.  Its original bytes stay in place (or, for an unreadable
        registry, move to a recovery backup) and a newly generated identity is
        made active instead.
        """

        try:
            active = self.system.identities.active(required=False)
        except LifeCoreError as exc:
            # 草案不变量 3：身份权威损坏（registry 不可读/schema 不符/bindings 非法）
            # 属矛盾的安全事实，fail-closed；绝不在损坏证据上静默替换身份。
            if exc.code in {
                "registry_unreadable",
                "registry_schema_unsupported",
                "registry_bindings_invalid",
            }:
                raise EmbeddedLifeError(
                    f"life.registry.{exc.code}", status=409
                ) from exc
            self._replace_unreadable_registry(exc.code)
            active = None
        if active is None:
            created_life_id = str(self.system.create_identity("起源")["life_id"])
            self.system.journal.ensure_hashed(created_life_id)
            return created_life_id
        prior_life_id = str(active.get("life_id") or "")
        try:
            self.system.identities.verify_root(
                self.system.identities.root_for(prior_life_id),
                require_private=True,
            )
            self.system.journal.ensure_hashed(prior_life_id)
            return prior_life_id
        except LifeCoreError as exc:
            # A signed journal head that later disappears is evidence loss or
            # tampering, not a recoverable identity defect.  Creating a fresh
            # identity here would hide the loss and leave the original writer
            # state ambiguous, so preserve the fail-closed contract.
            if exc.code == "journal_head_missing":
                raise
            # 草案不变量 3：私钥缺失/损坏、身份文件缺失、journal 截断均按
            # 证据丢失 fail-closed，不得静默重建身份掩盖损坏。
            if exc.code in {
                "identity_private_key_missing",
                "identity_private_key_invalid",
                "identity_files_missing",
                "identity_signature_invalid",
                "identity_schema_invalid",
                "identity_root_mismatch",
            } or exc.code.startswith("journal_"):
                raise EmbeddedLifeError(
                    f"life.identity.{exc.code}", status=409
                ) from exc
            replacement = self.system.create_identity("起源")
            replacement_life_id = str(replacement["life_id"])
            self.system.journal.ensure_hashed(replacement_life_id)
            self._record_life_recovery(
                prior_life_id=prior_life_id,
                reason_code=exc.code,
                replacement_life_id=replacement_life_id,
            )
            return replacement_life_id

    def _default_identity_state(self) -> dict[str, Any]:
        return {
            "revision": 1,
            "memories": {},
            "memory_relations": [],
            "memory_candidates": {},
            "affect": {
                "valence": 0.0,
                "arousal": 0.0,
                "dominance": 0.0,
                "updated_at": utc_now(),
            },
            "settings": {
                "permission_mode": "confirm_high_risk",
                "autonomous_risk_max": "A4",
                "autonomy_enabled": True,
                "autonomy_task_generation_enabled": True,
                "autonomy_activity_types": list(DEFAULT_ACTIVITY_TYPES),
                "privacy": {"redact_llm": True, "redact_share": True},
                "heartbeat_enabled": True,
                "llm_daily_budget": 20,
                "llm_daily_attempt_budget": 30,
                "share_enabled": True,
                "share_quiet_if_user_active": True,
                "share_min_interval_seconds": 2700,
                "share_hourly_limit": 1,
                "share_daily_limit": 5,
                "share_dnd_start": "23:00",
                "share_dnd_end": "08:00",
                # P16 native proactive cognition. Legacy share/greeting settings
                # remain compatibility-only and cannot authorize this producer.
                "proactive_enabled": True,
                "proactive_mode": "shadow",
                "proactive_decision_interval_seconds": 900,
                "proactive_min_interval_seconds": 3600,
                "proactive_max_messages_per_hour": 2,
                "proactive_max_messages_per_day": 6,
                "proactive_dnd_enabled": False,
                "proactive_dnd_start_hour": 22,
                "proactive_dnd_end_hour": 7,
                # Explicit timezone keeps DND independent of host locale.
                "proactive_timezone_offset_minutes": 0,
                "proactive_max_future_skew_seconds": 300,
                "proactive_respect_user_activity": True,
                "proactive_user_active_window_seconds": 180,
                "proactive_min_evidence_confidence_milli": 350,
                "proactive_evidence_stale_after_seconds": 86400,
                "proactive_min_utility_lcb_milli": 120,
                "proactive_min_margin_milli": 80,
                "learned_boundary_rules": [],
            },
            "inbox": [],
            "inbox_tombstones": [],
            "inbox_contract_version": 2,
            "proactive_chats": [],
            "capabilities": {},
            "capability_pointers": {},
            "knowledge": {},
            "learning": {},
            "upgrades": {},
            "executions": {},
            # Formerly detached subsystems now live under the same identity
            # scope and writer lease as memory, affect and autonomy.
            "schedule": default_schedule(),
            "relationships": {},
            "body": default_body(),
            "autonomy": default_autonomy_state(),
            "scheduler": {
                "heartbeat_count": 0,
                "last_heartbeat_at": "",
                "last_reason": "",
                "last_learning_decision_at_ms": 0,
                "learning_decision_inflight": False,
                "last_learning_decision_error": "",
                "last_autonomy_decision_at_ms": 0,
                "autonomy_decision_inflight": False,
                "last_autonomy_decision_error": "",
                "model_budget_date": "",
                "model_attempts": 0,
                "model_successes": 0,
                "model_failures": 0,
                "model_timeouts": 0,
                "model_skipped": 0,
                "last_user_activity_at_ms": 0,
                "last_share_at_ms": 0,
                "last_share_decision_reason": "",
                "last_proactive_decision_at_ms": 0,
                "proactive_decision_inflight": False,
                "last_proactive_delivery_at_ms": 0,
                "last_proactive_reason": "",
                "last_user_run_id": "",
            },
            "updated_at": utc_now(),
        }

    def _default_state(self) -> dict[str, Any]:
        return {
            "schema": "tiangong.life.embedded-state.v2",
            "revision": 1,
            "identity_states": {},
            "updated_at": utc_now(),
        }

    def _scope_state(self, life_id: str = "") -> dict[str, Any]:
        clean_life_id = str(life_id or self._active().get("life_id") or "").strip()
        if not _OPAQUE.fullmatch(clean_life_id):
            raise EmbeddedLifeError("life.identity.id_invalid", status=409)
        states = self._state.setdefault("identity_states", {})
        if not isinstance(states, dict):
            raise EmbeddedLifeError("life.state.identity_states_invalid", status=409)
        scope = states.get(clean_life_id)
        defaults = self._default_identity_state()
        created = not isinstance(scope, dict)
        legacy_inbox_contract = (
            isinstance(scope, dict) and "inbox_contract_version" not in scope
        )
        if not isinstance(scope, dict):
            scope = defaults
            states[clean_life_id] = scope
        else:
            for key, fallback in defaults.items():
                scope.setdefault(key, deepcopy(fallback))
            if legacy_inbox_contract:
                scope["inbox_contract_version"] = 1
        settings = scope.get("settings")
        if not isinstance(settings, dict):
            raise EmbeddedLifeError("life.state.settings_invalid", status=409)
        default_settings = defaults["settings"]
        for key, fallback in default_settings.items():
            settings.setdefault(key, deepcopy(fallback))
        privacy = settings.get("privacy")
        if not isinstance(privacy, dict):
            raise EmbeddedLifeError("life.state.settings_privacy_invalid", status=409)
        for key, fallback in default_settings["privacy"].items():
            privacy.setdefault(key, fallback)
        try:
            settings["autonomy_activity_types"] = normalize_activity_types(
                settings.get("autonomy_activity_types")
            )
        except ValueError as exc:
            raise EmbeddedLifeError("life.settings.autonomy_activity_types_invalid", status=409) from exc
        scope["schedule"] = normalize_schedule(scope.get("schedule"), today=utc_now()[:10], autonomy_tasks=[])
        scope["relationships"] = scope.get("relationships") if isinstance(scope.get("relationships"), dict) else {}
        scope["body"] = normalize_body(scope.get("body"), updated_at=utc_now())
        try:
            scope["autonomy"] = normalize_autonomy_state(scope.get("autonomy"))
        except ValueError as exc:
            raise EmbeddedLifeError("life.state.autonomy_invalid", status=409) from exc
        # The v2 inbox contract is new-life-only. Old identities are neither
        # scanned nor migrated; this avoids silently rewriting their mailbox.
        # Publishing is handled by _sync_daily_summary so reads stay pure and
        # every share setting participates in one policy decision.
        inbox = scope.get("inbox")
        if not isinstance(inbox, list):
            inbox = []
            scope["inbox"] = inbox
        tombstones = scope.get("inbox_tombstones")
        if not isinstance(tombstones, list):
            tombstones = []
            scope["inbox_tombstones"] = tombstones
        del inbox[:-100]
        del tombstones[:-400]
        innate = self._innate_temperament(clean_life_id)
        scope["temperament"] = normalize_temperament_state(
            innate,
            scope.get("temperament") if isinstance(scope.get("temperament"), Mapping) else None,
        )
        projection = public_temperament_projection(innate, scope["temperament"])
        disposition = projection["current_affective_disposition"]
        affect_baseline = {
            "valence": float(disposition["valence_set_point"]),
            "arousal": float(disposition["arousal_set_point"]),
            "dominance": float(disposition["dominance_set_point"]),
        }
        if created:
            scope["affect"] = {
                **affect_baseline,
                "dimension_override": affect_baseline,
                "updated_at": utc_now(),
                "source": "innate_temperament",
            }
        elif (
            isinstance(scope.get("affect"), Mapping)
            and not scope["affect"].get("schema")
            and not scope["affect"].get("source")
        ):
            # A newly allocated identity scope is materialized by _load_state
            # before _scope_state first sees it, so `created` can be false.
            # Treat only the untouched legacy default as the innate baseline.
            legacy_affect = scope["affect"]
            if all(float(legacy_affect.get(key) or 0.0) == 0.0 for key in ("valence", "arousal", "dominance")):
                scope["affect"] = {
                    **legacy_affect,
                    **affect_baseline,
                    "dimension_override": affect_baseline,
                    "source": "innate_temperament",
                }
        scope["affect"] = normalize_transient_affect(
            scope.get("affect") if isinstance(scope.get("affect"), Mapping) else {},
            life_id=clean_life_id,
            baseline=affect_baseline,
            now_ms=time.time_ns() // 1_000_000,
        )
        return scope

    @staticmethod
    def _redact_sensitive_text(value: str) -> str:
        return _MEMORY_SECRET.sub("[已脱敏]", str(value or ""))

    @staticmethod
    def _minute_is_in_window(minute: int, start: int, end: int) -> bool:
        if start == end:
            return False
        if start < end:
            return start <= minute < end
        return minute >= start or minute < end

    def _sync_daily_summary(
        self,
        life_id: str,
        *,
        now_ms: int | None = None,
    ) -> bool:
        """Publish one policy-governed aggregate after today's plan completes."""
        scope = self._scope_state(life_id)
        settings = scope["settings"]
        scheduler = scope.setdefault("scheduler", {})
        current_ms = int(now_ms if now_ms is not None else time.time_ns() // 1_000_000)
        today = utc_now()[:10]
        summary_id = f"daily-summary:{today}"
        inbox = scope["inbox"]
        existing = next(
            (
                row
                for row in inbox
                if isinstance(row, dict)
                and str(row.get("message_id") or "") == summary_id
            ),
            None,
        )
        if isinstance(existing, dict):
            scheduler["last_share_decision_reason"] = "life.share.already_published"
            return False

        reason = ""
        if int(scope.get("inbox_contract_version") or 1) < 2:
            reason = "life.share.new_life_contract_required"
        elif not bool(settings.get("share_enabled")):
            reason = "life.share.disabled"
        elif int(settings.get("share_hourly_limit") or 0) <= 0:
            reason = "life.share.hourly_limit_disabled"
        elif int(settings.get("share_daily_limit") or 0) <= 0:
            reason = "life.share.daily_limit_disabled"
        elif summary_id in {str(value) for value in scope["inbox_tombstones"] if str(value)}:
            reason = "life.share.deleted_by_user"

        selected = {
            str(value)
            for value in settings.get("autonomy_activity_types") or []
            if str(value)
        }
        tasks = [
            row
            for row in catalog_tasks_for_day(
                [
                    task
                    for task in scope["autonomy"].get("tasks", {}).values()
                    if isinstance(task, Mapping)
                ],
                day=today,
            )
            if (
                str(row.get("activity_id") or "") in selected
                or str(row.get("status") or "") == "completed"
            )
        ]
        if not reason and not tasks:
            reason = "life.share.no_plan"
        elif not reason and any(str(row.get("status") or "") != "completed" for row in tasks):
            reason = "life.share.plan_incomplete"

        local = time.localtime(current_ms / 1000)
        minute = local.tm_hour * 60 + local.tm_min
        try:
            start_hour, start_minute = (
                int(part) for part in str(settings["share_dnd_start"]).split(":", 1)
            )
            end_hour, end_minute = (
                int(part) for part in str(settings["share_dnd_end"]).split(":", 1)
            )
        except (KeyError, TypeError, ValueError):
            start_hour, start_minute, end_hour, end_minute = 23, 0, 8, 0
        if (
            not reason
            and self._minute_is_in_window(
                minute,
                start_hour * 60 + start_minute,
                end_hour * 60 + end_minute,
            )
        ):
            reason = "life.share.do_not_disturb"

        last_share_ms = int(scheduler.get("last_share_at_ms") or 0)
        minimum_interval_ms = int(settings.get("share_min_interval_seconds") or 0) * 1000
        if not reason and last_share_ms and current_ms - last_share_ms < minimum_interval_ms:
            reason = "life.share.minimum_interval"
        last_user_ms = int(scheduler.get("last_user_activity_at_ms") or 0)
        if (
            not reason
            and bool(settings.get("share_quiet_if_user_active"))
            and last_user_ms
            and current_ms - last_user_ms < 180_000
        ):
            reason = "life.share.user_active"

        today_start = current_ms - (
            (local.tm_hour * 3600 + local.tm_min * 60 + local.tm_sec) * 1000
        )
        hour_start = current_ms - ((local.tm_min * 60 + local.tm_sec) * 1000)
        published_times = [
            int(row.get("created_at_ms") or 0)
            for row in inbox
            if isinstance(row, Mapping)
            and str(row.get("kind") or "") in {"daily_life_summary", "life_share"}
        ]
        if (
            not reason
            and sum(1 for value in published_times if value >= today_start)
            >= int(settings.get("share_daily_limit") or 0)
        ):
            reason = "life.share.daily_limit"
        if (
            not reason
            and sum(1 for value in published_times if value >= hour_start)
            >= int(settings.get("share_hourly_limit") or 0)
        ):
            reason = "life.share.hourly_limit"
        if reason:
            scheduler["last_share_decision_reason"] = reason
            return False

        lines: list[str] = []
        for task in tasks:
            reflection = reflection_projection(task)
            title = str(
                task.get("title")
                or task.get("objective")
                or task.get("activity_id")
                or "自主行动"
            ).strip()
            result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
            summary = str(
                reflection.get("human_summary")
                or result.get("summary")
                or "已完成"
            ).strip()
            lines.append(f"• {title}：{summary}")
        message = (
            f"今天共完成 {len(tasks)} 项生命计划。\n\n" + "\n".join(lines)
        )[:4000]
        privacy = settings.get("privacy") if isinstance(settings.get("privacy"), Mapping) else {}
        if bool(privacy.get("redact_share", True)):
            message = self._redact_sensitive_text(message)
        inbox.append(
            {
                "message_id": summary_id,
                "title": "今日生命总结",
                "message": message,
                "kind": "daily_life_summary",
                "task_count": len(tasks),
                "summary_task_ids": [str(row.get("task_id") or "") for row in tasks],
                "created_at": utc_now(),
                "created_at_ms": current_ms,
                "read": False,
            }
        )
        del inbox[:-100]
        scheduler["last_share_at_ms"] = current_ms
        scheduler["last_share_decision_reason"] = "life.share.published"
        self.system.journal.append(
            life_id,
            "life.share.published",
            {
                "message_id": summary_id,
                "kind": "daily_life_summary",
                "task_count": len(tasks),
            },
            actor="life_scheduler",
            idempotency_key=f"life.share:{life_id}:{summary_id}",
        )
        return True

    def _load_state(self) -> dict[str, Any]:
        active_life_id = str(self._active()["life_id"])
        if not self.paths.state_file.is_file():
            value = self._default_state()
            value["identity_states"][active_life_id] = self._default_identity_state()
            atomic_json(self.paths.state_file, value)
            return value
        try:
            value = json.loads(self.paths.state_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EmbeddedLifeError("life.state.corrupt", status=409) from exc
        if not isinstance(value, dict):
            raise EmbeddedLifeError("life.state.schema_invalid", status=409)
        schema = value.get("schema")
        if schema == "tiangong.life.embedded-state.v1":
            # One-time, lossless migration: all legacy unscoped state belonged
            # to the identity that was active under the old single-identity
            # runtime.  Future identities receive independent empty scopes.
            migrated = self._default_state()
            legacy_scope = self._default_identity_state()
            for key in legacy_scope:
                if key in value:
                    legacy_scope[key] = deepcopy(value[key])
            legacy_scope["revision"] = max(1, int(value.get("revision") or 1))
            legacy_scope["updated_at"] = str(value.get("updated_at") or utc_now())
            migrated["revision"] = max(1, int(value.get("revision") or 1))
            migrated["updated_at"] = str(value.get("updated_at") or utc_now())
            migrated["identity_states"][active_life_id] = legacy_scope
            atomic_json(self.paths.state_file, migrated)
            return migrated
        if schema != "tiangong.life.embedded-state.v2":
            raise EmbeddedLifeError("life.state.schema_invalid", status=409)
        default = self._default_state()
        for key, fallback in default.items():
            value.setdefault(key, deepcopy(fallback))
        states = value.get("identity_states")
        if not isinstance(states, dict):
            raise EmbeddedLifeError("life.state.identity_states_invalid", status=409)
        if active_life_id not in states:
            states[active_life_id] = self._default_identity_state()
            atomic_json(self.paths.state_file, value)
        return value

    def _persist(self, life_id: str = "", *, force: bool = False) -> None:
        # journal 是权威（每事件 fsync）；state JSON 是可重建投影
        # （启动时 _reconcile_authoritative_journal 全量幂等重放）。
        # 高频记忆写下每次全量序列化是 O(n^2)：去抖到每秒最多一次，
        # close/force 强制落盘；崩溃窗口内投影滞后由启动重放补齐。
        now_monotonic = time.monotonic()
        if not force and (now_monotonic - self._last_persist_monotonic) < 1.0:
            scope_pending = self._scope_state(life_id)
            scope_pending["revision"] = int(scope_pending.get("revision") or 0) + 1
            scope_pending["updated_at"] = utc_now()
            self._state["revision"] = int(self._state.get("revision") or 0) + 1
            self._state["updated_at"] = utc_now()
            self._persist_pending = True
            return
        self._last_persist_monotonic = now_monotonic
        self._persist_pending = False
        scope = self._scope_state(life_id)
        prior = {
            "scope_revision": scope.get("revision"),
            "scope_updated_at": scope.get("updated_at"),
            "state_revision": self._state.get("revision"),
            "state_updated_at": self._state.get("updated_at"),
        }
        now = utc_now()
        scope["revision"] = int(scope.get("revision") or 0) + 1
        scope["updated_at"] = now
        self._state["revision"] = int(self._state.get("revision") or 0) + 1
        self._state["updated_at"] = now
        try:
            atomic_json(self.paths.state_file, self._state)
        except Exception:
            scope["revision"] = prior["scope_revision"]
            scope["updated_at"] = prior["scope_updated_at"]
            self._state["revision"] = prior["state_revision"]
            self._state["updated_at"] = prior["state_updated_at"]
            self._projection_dirty_reason = "life.projection.persist_failed"
            raise

    def _ensure_memory_classification(self, life_id: str) -> bool:
        """Migrate legacy memory rows into deterministic causal classification.

        This is projection-only migration.  It never invents a new semantic
        journal event for an old assertion; replay of the original assertion
        followed by the same classifier yields the same classification.
        """

        changed = False
        scope = self._scope_state(life_id)
        memories = scope.get("memories")
        if not isinstance(memories, dict):
            raise EmbeddedLifeError("life.state.memories_invalid", status=409)
        for memory_id, row in memories.items():
            if not isinstance(row, dict):
                raise EmbeddedLifeError("life.state.memory_invalid", status=409)
            if row.get("status") == "deleted":
                continue
            classification = row.get("classification")
            if isinstance(classification, Mapping) and classification.get("classification_sha256"):
                continue
            try:
                classified = classify_memory(
                    content=row.get("content"),
                    provenance=row.get("provenance") if isinstance(row.get("provenance"), Mapping) else {},
                    relations=row.get("relations") if isinstance(row.get("relations"), list) else [],
                    requested_memory_type=row.get("requested_memory_type") or row.get("memory_type") or "",
                    requested_causal_role=(
                        classification.get("causal_role")
                        if isinstance(classification, Mapping)
                        else row.get("causal_role")
                    ),
                    epistemic_status=str(row.get("epistemic_status") or "user_asserted"),
                    confidence_milli=int(row.get("confidence_milli") or 800),
                    priority=int(row.get("priority") or 900),
                )
            except (TypeError, ValueError) as exc:
                raise EmbeddedLifeError("life.state.memory_classification_invalid", status=409) from exc
            row["requested_memory_type"] = str(row.get("requested_memory_type") or row.get("memory_type") or "")
            row["memory_type"] = classified["classification"]["memory_type"]
            row["relations"] = classified["relations"]
            row["classification"] = classified["classification"]
            row.setdefault("life_id", life_id)
            row.setdefault("memory_id", str(memory_id))
            changed = True
            lifecycle = normalize_lifecycle(row)
            if row.get("lifecycle") != lifecycle:
                row["lifecycle"] = lifecycle
                changed = True
        return changed

    def _advance_memory_lifecycles(self, life_id: str) -> dict[str, int]:
        """Advance decay/freeze state without changing immutable assertions."""

        scope = self._scope_state(life_id)
        changed = 0
        frozen = 0
        for row in scope.get("memories", {}).values():
            if not isinstance(row, dict) or row.get("status") == "deleted":
                continue
            lifecycle, lifecycle_changed = advance_lifecycle(row)
            if lifecycle_changed:
                row["lifecycle"] = lifecycle
                changed += 1
                if lifecycle.get("state") == "frozen":
                    frozen += 1
        if changed:
            day = int(time.time() // 86_400)
            self.system.journal.append(
                life_id,
                "memory.lifecycle_advanced",
                # The per-record lifecycle is persisted projection state.
                # Journal this once per day as a maintenance checkpoint; its
                # payload must stay stable for idempotent scheduler retries.
                {"day": day},
                actor="life_scheduler",
                idempotency_key=f"memory.lifecycle:{life_id}:{day}",
            )
            self._persist(life_id)
        return {"changed": changed, "frozen": frozen}

    @staticmethod
    def _merge_asserted_memory_projection(existing: dict[str, Any], asserted: Mapping[str, Any]) -> bool:
        """Validate immutable assertion semantics while allowing later events.

        ``status`` and ``updated_at`` are projection fields changed by later
        journal events.  A deleted row also intentionally replaces content
        with a tombstone.  Missing classifier fields from a legacy projection
        are restored from the journal assertion; conflicting present semantic
        fields still fail closed.
        """

        changed = False
        # Classification is deterministic from the assertion, whereas
        # lifecycle deliberately evolves through decay and cue-driven recall.
        # It must never make a historic assertion look conflicting on replay.
        mutable = {"status", "updated_at", "lifecycle"}
        deleted = existing.get("status") == "deleted"
        for key, value in asserted.items():
            if key in mutable or (deleted and key == "content"):
                continue
            if key not in existing:
                existing[key] = deepcopy(value)
                changed = True
                continue
            if canonical_sha256(existing.get(key)) != canonical_sha256(value):
                raise EmbeddedLifeError("life.projection.memory_conflict", status=409)
        return changed

    @staticmethod
    def _merge_generated_task_projection(existing: dict[str, Any], generated: Mapping[str, Any]) -> bool:
        """Validate immutable task proposal fields across later transitions."""

        changed = False
        mutable = {"status", "updated_at_ms", "attempt_count", "result", "task_sha256"}
        for key, value in generated.items():
            if key in mutable:
                continue
            if key not in existing:
                existing[key] = deepcopy(value)
                changed = True
                continue
            if canonical_sha256(existing.get(key)) != canonical_sha256(value):
                raise EmbeddedLifeError("life.projection.autonomy_task_conflict", status=409)
        return changed

    def _reconcile_authoritative_journal(self, life_id: str) -> bool:
        """Rebuild immutable projections after a crash between journal and state writes.

        The semantic journal is the authoritative write-ahead record for
        memories, autonomy tasks and terminal executions.  State JSON is a
        replaceable projection.  Missing projection rows are restored;
        conflicting immutable rows fail closed instead of being overwritten.
        """

        changed = False
        scope = self._scope_state(life_id)
        for event in self.system.journal.events(life_id):
            event_type = str(event.get("event_type") or "")
            payload = event.get("payload")
            if event_type in {"memory.asserted", "memory.corrected"} and isinstance(payload, Mapping):
                assertion = payload.get("assertion")
                if not isinstance(assertion, Mapping):
                    raise EmbeddedLifeError("life.projection.memory_event_invalid", status=409)
                record = deepcopy(dict(assertion))
                memory_id = str(record.get("memory_id") or "")
                if not _OPAQUE.fullmatch(memory_id):
                    raise EmbeddedLifeError("life.projection.memory_id_invalid", status=409)
                record.setdefault("life_id", life_id)
                existing_memory = scope["memories"].get(memory_id)
                if existing_memory is None:
                    scope["memories"][memory_id] = record
                    changed = True
                elif not isinstance(existing_memory, dict):
                    raise EmbeddedLifeError("life.projection.memory_conflict", status=409)
                elif self._merge_asserted_memory_projection(existing_memory, record):
                    changed = True
                if event_type == "memory.corrected":
                    target_memory_id = str(payload.get("target_memory_id") or "")
                    target = scope["memories"].get(target_memory_id)
                    if not isinstance(target, dict):
                        raise EmbeddedLifeError("life.projection.memory_target_missing", status=409)
                    if target.get("status") != "corrected":
                        target["status"] = "corrected"
                        target["updated_at"] = str(payload.get("updated_at") or record.get("created_at") or utc_now())
                        changed = True
            elif event_type == "memory.status_changed" and isinstance(payload, Mapping):
                memory_id = str(payload.get("memory_id") or "")
                row = scope["memories"].get(memory_id)
                if not isinstance(row, dict):
                    raise EmbeddedLifeError("life.projection.memory_target_missing", status=409)
                status = str(payload.get("status") or "")
                if row.get("status") != status or row.get("updated_at") != payload.get("updated_at"):
                    row["status"] = status
                    row["updated_at"] = str(payload.get("updated_at") or utc_now())
                    changed = True
            elif event_type == "memory.deleted" and isinstance(payload, Mapping):
                memory_id = str(payload.get("memory_id") or "")
                row = scope["memories"].get(memory_id)
                if not isinstance(row, dict):
                    raise EmbeddedLifeError("life.projection.memory_target_missing", status=409)
                if row.get("status") != "deleted" or row.get("content") != {"tombstone": True}:
                    row["status"] = "deleted"
                    row["content"] = {"tombstone": True}
                    row["updated_at"] = str(payload.get("updated_at") or utc_now())
                    changed = True
            elif event_type == "memory.relation_added" and isinstance(payload, Mapping):
                relation = payload.get("relation")
                if not isinstance(relation, Mapping):
                    raise EmbeddedLifeError("life.projection.memory_relation_invalid", status=409)
                record = deepcopy(dict(relation))
                relation_id = str(record.get("relation_id") or "")
                if not _OPAQUE.fullmatch(relation_id):
                    raise EmbeddedLifeError("life.projection.memory_relation_invalid", status=409)
                existing_relations = scope["memory_relations"]
                found = next(
                    (
                        item
                        for item in existing_relations
                        if isinstance(item, Mapping) and item.get("relation_id") == relation_id
                    ),
                    None,
                )
                if found is None:
                    existing_relations.append(record)
                    changed = True
                elif canonical_sha256(found) != canonical_sha256(record):
                    raise EmbeddedLifeError("life.projection.memory_relation_conflict", status=409)
            elif event_type == "memory.candidates_proposed" and isinstance(payload, Mapping):
                candidates = payload.get("candidates")
                if not isinstance(candidates, list):
                    raise EmbeddedLifeError("life.projection.memory_candidate_invalid", status=409)
                queue = scope.setdefault("memory_candidates", {})
                if not isinstance(queue, dict):
                    raise EmbeddedLifeError("life.projection.memory_candidate_invalid", status=409)
                for raw_candidate in candidates:
                    if not isinstance(raw_candidate, Mapping):
                        raise EmbeddedLifeError("life.projection.memory_candidate_invalid", status=409)
                    record = deepcopy(dict(raw_candidate))
                    candidate_id = str(record.get("candidate_id") or "")
                    if not _OPAQUE.fullmatch(candidate_id):
                        raise EmbeddedLifeError("life.projection.memory_candidate_invalid", status=409)
                    found_candidate = queue.get(candidate_id)
                    if found_candidate is None:
                        queue[candidate_id] = record
                        changed = True
                    elif canonical_sha256(found_candidate) != canonical_sha256(record):
                        raise EmbeddedLifeError("life.projection.memory_candidate_conflict", status=409)
            elif event_type == "autonomy.task_generated" and isinstance(payload, Mapping):
                task = payload.get("task")
                if not isinstance(task, Mapping):
                    raise EmbeddedLifeError("life.projection.autonomy_task_invalid", status=409)
                record = deepcopy(dict(task))
                task_id = str(record.get("task_id") or "")
                if not _OPAQUE.fullmatch(task_id):
                    raise EmbeddedLifeError("life.projection.autonomy_task_invalid", status=409)
                autonomy = normalize_autonomy_state(scope.get("autonomy"))
                existing_task = autonomy["tasks"].get(task_id)
                if existing_task is None:
                    autonomy["tasks"][task_id] = record
                    autonomy["task_sequence"] = max(
                        int(autonomy.get("task_sequence") or 0),
                        int(record.get("sequence") or 0),
                    )
                    autonomy["generated_total"] = max(
                        int(autonomy.get("generated_total") or 0),
                        len(autonomy["tasks"]),
                    )
                    scope["autonomy"] = autonomy
                    changed = True
                elif not isinstance(existing_task, dict):
                    raise EmbeddedLifeError("life.projection.autonomy_task_conflict", status=409)
                elif self._merge_generated_task_projection(existing_task, record):
                    changed = True
            elif event_type == "autonomy.task_status_changed" and isinstance(payload, Mapping):
                task_id = str(payload.get("task_id") or "")
                task = normalize_autonomy_state(scope.get("autonomy"))["tasks"].get(task_id)
                if not isinstance(task, dict):
                    raise EmbeddedLifeError("life.projection.autonomy_task_missing", status=409)
                projected = payload.get("task")
                if not isinstance(projected, Mapping):
                    raise EmbeddedLifeError("life.projection.autonomy_task_invalid", status=409)
                if canonical_sha256(task) != canonical_sha256(projected):
                    scope["autonomy"]["tasks"][task_id] = deepcopy(dict(projected))
                    changed = True
            elif event_type == "execution.committed" and isinstance(payload, Mapping):
                record = deepcopy(dict(payload))
                request_id = str(record.get("request_id") or "")
                commit_sha256 = str(record.get("commit_sha256") or "")
                if not _OPAQUE.fullmatch(request_id) or not re.fullmatch(r"[0-9a-f]{64}", commit_sha256):
                    raise EmbeddedLifeError("life.projection.execution_event_invalid", status=409)
                existing = scope["executions"].get(request_id)
                if existing is None:
                    scope["executions"][request_id] = record
                    changed = True
                elif not isinstance(existing, Mapping) or existing.get("commit_sha256") != commit_sha256:
                    raise EmbeddedLifeError("life.projection.execution_conflict", status=409)
        autonomy = normalize_autonomy_state(scope.get("autonomy"))
        tasks = autonomy.get("tasks") if isinstance(autonomy.get("tasks"), Mapping) else {}
        derived = {
            "generated_total": len(tasks),
            "completed_total": sum(
                1 for task in tasks.values()
                if isinstance(task, Mapping) and task.get("status") == "completed"
            ),
            "failed_total": sum(
                1 for task in tasks.values()
                if isinstance(task, Mapping) and task.get("status") == "failed"
            ),
            "task_sequence": max(
                [int(task.get("sequence") or 0) for task in tasks.values() if isinstance(task, Mapping)]
                or [0]
            ),
        }
        for key, value in derived.items():
            if int(autonomy.get(key) or 0) != value:
                autonomy[key] = value
                changed = True
        scope["autonomy"] = autonomy
        return changed

    def _active(self) -> dict[str, Any]:
        return self.system.identities.active(required=True) or {}

    def _soul(self) -> dict[str, Any]:
        return dict(self.system.get_soul()["soul"])

    def _innate_temperament(self, life_id: str = "") -> dict[str, Any]:
        clean_life_id = str(life_id or self._active().get("life_id") or "").strip()
        root = self.system.identities.root_for(clean_life_id)
        return self.system.identities.ensure_temperament(root)

    def _temperament_projection(self, life_id: str = "") -> dict[str, Any]:
        clean_life_id = str(life_id or self._active().get("life_id") or "").strip()
        innate = self._innate_temperament(clean_life_id)
        scope = self._scope_state(clean_life_id)
        return public_temperament_projection(innate, scope.get("temperament"))

    def _affect_baseline(self, life_id: str, scope: Mapping[str, Any]) -> dict[str, float]:
        innate = self._innate_temperament(life_id)
        projection = public_temperament_projection(innate, scope.get("temperament"))
        disposition = projection["current_affective_disposition"]
        return {
            "valence": float(disposition["valence_set_point"]),
            "arousal": float(disposition["arousal_set_point"]),
            "dominance": float(disposition["dominance_set_point"]),
        }

    def _decay_transient_affect(
        self,
        life_id: str,
        *,
        now_ms: int | None = None,
    ) -> tuple[dict[str, Any], int, int]:
        scope = self._scope_state(life_id)
        state, elapsed_ms, max_delta = decay_transient_affect(
            scope["affect"],
            life_id=life_id,
            baseline=self._affect_baseline(life_id, scope),
            now_ms=int(now_ms if now_ms is not None else time.time_ns() // 1_000_000),
        )
        scope["affect"] = state
        return state, elapsed_ms, max_delta

    def _appraise_current_user_affect(self, payload: Mapping[str, Any]) -> bool:
        current_request = payload.get("current_request")
        request_id = payload.get("request_id")
        run_id = payload.get("run_id")
        generation = payload.get("generation")
        principal_scope_hash = payload.get("principal_scope_hash")
        issued_at_ms = payload.get("issued_at_ms")
        allowed_fields = LifeContextCompileAuthorizeApi._FIELDS
        if (
            set(payload) not in {
                allowed_fields,
                allowed_fields - {"current_context_tokens"},
            }
            or not isinstance(current_request, str)
            or not current_request.strip()
            or len(current_request) > 50_000
            or "\x00" in current_request
            or not isinstance(request_id, str)
            or re.fullmatch(r"req_[0-9a-f]{64}", request_id) is None
            or not isinstance(run_id, str)
            or re.fullmatch(r"run_[0-9a-f]{64}", run_id) is None
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
            or not isinstance(principal_scope_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", principal_scope_hash) is None
            or isinstance(issued_at_ms, bool)
            or not isinstance(issued_at_ms, int)
            or issued_at_ms < 0
        ):
            return False
        life_id = str(self._active()["life_id"])
        scope = self._scope_state(life_id)
        state, changed = appraise_user_turn(
            scope["affect"],
            life_id=life_id,
            baseline=self._affect_baseline(life_id, scope),
            text=current_request,
            request_id=request_id,
            now_ms=issued_at_ms,
        )
        scope["affect"] = state
        return changed

    def _revisions(self) -> LifeRevisionVector:
        active = self._active()
        soul = self._soul()
        base = self.authority_store.build_revision_vector(
            str(active["life_id"]),
            writer_epoch=int(active.get("writer_epoch") or 1),
            identity_revision=int(active.get("identity_revision") or 1),
            soul_revision=max(1, int(soul.get("revision") or 1)),
        )
        scope_revision = int(self._scope_state().get("revision") or 1)
        memory_revision = max(base.memory_revision, scope_revision)
        affect_revision = max(base.affect_revision, scope_revision)
        return base.model_copy(
            update={
                "memory_revision": memory_revision,
                "affect_revision": affect_revision,
                "policy_revision": max(base.policy_revision, scope_revision),
                "capability_revision": max(base.capability_revision, scope_revision),
                "vector_sha256": "0" * 64,
            }
        ).with_computed_vector_sha256()

    def _projection_authority(self) -> dict[str, Any]:
        revisions = self._revisions()
        life_id = revisions.life_id
        return {
            "schema": "tiangong.gateway.life-view-authority.v1",
            "revisions": revisions.model_dump(mode="json"),
            "source_refs": {
                "identity": [f"life:{life_id}:identity:{revisions.identity_revision}"],
                "soul": [f"life:{life_id}:soul:{revisions.soul_revision}"],
                "temperament": [
                    f"life:{life_id}:temperament:{self._temperament_projection(life_id)['revision']}"
                ],
                "memory": [f"life:{life_id}:memory:{revisions.memory_revision}"],
                "affect": [f"life:{life_id}:affect:{revisions.affect_revision}"],
                "causal": [f"life:{life_id}:causal:{revisions.causal_revision}"],
                "viability": [f"life:{life_id}:viability:{revisions.viability_revision}"],
                "policy": [f"life:{life_id}:policy:{revisions.policy_revision}"],
                "reflection": [f"life:{life_id}:reflection:{revisions.reflection_revision}"],
                "capability": [f"life:{life_id}:capability:{revisions.capability_revision}"],
            },
            "vector_sha256": revisions.vector_sha256,
        }

    def _external_memory_items(self, *, limit: int = 64) -> tuple[CausalContextItem, ...]:
        items: list[CausalContextItem] = []
        scope = self._scope_state()
        try:
            active = self._active()
            life_id = str(active.get("life_id") or "life_projection")
            life_name = str(active.get("display_name") or active.get("name") or "")
        except AttributeError:
            active = {}
            life_id = "life_projection"
            life_name = ""
        memories = scope.get("memories")
        memory_rows = memories if isinstance(memories, dict) else {}
        settings = scope.get("settings")
        settings_row = settings if isinstance(settings, Mapping) else {}
        privacy_value = settings_row.get("privacy")
        privacy = (
            privacy_value
            if isinstance(privacy_value, Mapping)
            else {}
        )
        redact_llm = bool(privacy.get("redact_llm", True))
        today = utc_now()[:10]
        now_ms = time.time_ns() // 1_000_000
        recent_cutoff_ms = now_ms - (30 * 60 * 1000)
        autonomy_tasks = [
            deepcopy(row)
            for row in scope.get("autonomy", {}).get("tasks", {}).values()
            if (
                isinstance(row, Mapping)
                and record_day(row) == today
                and max(
                    int(row.get("updated_at_ms") or 0),
                    int(row.get("created_at_ms") or 0),
                )
                >= recent_cutoff_ms
            )
        ]
        autonomy_tasks.sort(
            key=lambda row: (
                int(row.get("sequence") or 0),
                int(row.get("created_at_ms") or 0),
            )
        )
        task_projection: list[dict[str, Any]] = []
        for task in autonomy_tasks:
            result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
            reflection = reflection_projection(task)
            task_projection.append(
                {
                    "task_id": str(task.get("task_id") or ""),
                    "activity": str(task.get("activity_id") or task.get("task_kind") or ""),
                    "title": str(task.get("title") or task.get("objective") or ""),
                    "status": str(task.get("status") or ""),
                    "window": str(task.get("window") or ""),
                    "summary": str(
                        reflection.get("human_summary")
                        or result.get("summary")
                        or ""
                    )[:1200],
                    "next_step": str(result.get("next_step") or "")[:600],
                    "updated_at_ms": int(task.get("updated_at_ms") or 0),
                }
            )
        life_context = {
            "schema": "tiangong.life.model-runtime-context.v1",
            "instruction": (
                "This is the authoritative recent Life activity window. Use it only for "
                "what the life is doing now or just did. For today, yesterday, or older "
                "activity, call life.activity.query. Never invent absent activity."
            ),
            "date": today,
            "window_minutes": 30,
            "identity": {"life_id": life_id, "name": life_name},
            "recent_actions": task_projection,
            "authority": "embedded_life_runtime",
            "history_included": False,
        }
        life_summary = json.dumps(
            life_context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if redact_llm:
            life_summary = self._redact_sensitive_text(life_summary)
        items.append(
            CausalContextItem(
                item_ref=f"life_activity_{life_id}_{today.replace('-', '')}",
                item_kind="constraint",
                source_revision=max(1, int(scope.get("revision") or 1)),
                summary=life_summary[:12_000],
                epistemic_status="observed",
                confidence_milli=1000,
                priority=1400,
                privacy_scope="user:primary",
                token_count=max(1, len(life_summary[:12_000].encode("utf-8"))),
                supporting_event_ids=(),
            )
        )
        for index, record in enumerate(reversed(list(memory_rows.values()))):
            if index >= limit:
                break
            # Context compilation follows the same default visibility rule as
            # memory recall.  Suppressed, corrected, superseded, and deleted
            # records must not silently re-enter the model context.
            if not isinstance(record, dict) or str(record.get("status") or "active") != "active":
                continue
            text = json.dumps(record.get("content"), ensure_ascii=False, sort_keys=True)
            if redact_llm:
                text = self._redact_sensitive_text(text)
            if not text.strip():
                continue
            memory_id = str(record.get("memory_id") or "")
            if not _OPAQUE.fullmatch(memory_id):
                continue
            items.append(
                CausalContextItem(
                    item_ref=memory_id,
                    item_kind="memory",
                    source_revision=max(1, int(record.get("revision") or 1)),
                    summary=text[:20_000],
                    epistemic_status=str(record.get("epistemic_status") or "user_asserted"),
                    confidence_milli=max(0, min(1000, int(record.get("confidence_milli") or 800))),
                    priority=max(-3000, min(5000, int(record.get("priority") or 900))),
                    privacy_scope="user:primary",
                    # CausalContextBuilder validates this against its
                    # conservative byte-level counter.  A character heuristic
                    # made non-ASCII memories impossible to authorize.
                    token_count=max(1, min(1_000_000, len(text.encode("utf-8")))),
                    supporting_event_ids=(),
                )
            )
        affect = scope.get("affect")
        affect_state = affect if isinstance(affect, Mapping) else {}
        dimensions: dict[str, float] = {}
        for key in ("valence", "arousal", "dominance"):
            try:
                dimensions[key] = round(max(-1.0, min(1.0, float(affect_state.get(key) or 0.0))), 6)
            except (TypeError, ValueError):
                dimensions[key] = 0.0
        emotions = affect_state.get("emotions") if isinstance(affect_state.get("emotions"), Mapping) else {}
        affect_summary = json.dumps(
            {
                "schema": "tiangong.life.affect-context.v2",
                "instruction": (
                    "Affect only modulates attention and expression: wording, tone, and pacing. "
                    "It is not factual evidence and cannot change permissions, safety, "
                    "tool choice, execution results, or completion claims."
                ),
                "state": {
                    **dimensions,
                    "emotions": {
                        str(key): max(0, min(1000, int(value)))
                        for key, value in emotions.items()
                        if str(key)
                    },
                    "primary_emotion": str(affect_state.get("primary_emotion") or "calm"),
                    "primary_emotion_zh": str(affect_state.get("primary_emotion_zh") or "平静"),
                    "intensity_milli": max(0, min(1000, int(affect_state.get("intensity_milli") or 0))),
                    "intensity_band": str(affect_state.get("intensity_band") or "none"),
                    "updated_at_ms": max(0, int(affect_state.get("updated_at_ms") or 0)),
                    "revision": max(1, int(affect_state.get("revision") or 1)),
                },
                "expression_directive": str(affect_state.get("expression_directive") or ""),
                "authority": "attention_and_expression_only",
                "may_change_facts": False,
                "may_change_permissions": False,
                "may_change_tools": False,
                "may_claim_execution": False,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            affect_revision = max(1, int(self._revisions().affect_revision))
        except AttributeError:
            affect_revision = max(1, int(scope.get("revision") or 1))
        items.append(
            CausalContextItem(
                item_ref=f"affect_{life_id}",
                item_kind="constraint",
                source_revision=affect_revision,
                summary=affect_summary,
                epistemic_status="observed",
                confidence_milli=1000,
                priority=1200,
                privacy_scope="user:primary",
                token_count=max(1, len(affect_summary.encode("utf-8"))),
                supporting_event_ids=(),
            )
        )
        try:
            temperament = self._temperament_projection(life_id)
        except AttributeError:
            temperament = {}
        if not temperament:
            return tuple(sorted(items, key=lambda item: item.item_ref))
        temperament_summary = json.dumps(
            {
                "schema": "tiangong.life.temperament-context.v1",
                "instruction": (
                    "Temperament may modulate attention, pacing and expression only. "
                    "It cannot alter facts, permissions, safety boundaries or execution results. "
                    "It is independent from Soul."
                ),
                "traits": temperament["current_traits"],
                "affective_disposition": temperament["current_affective_disposition"],
                "revision": temperament["revision"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        items.append(
            CausalContextItem(
                item_ref=f"temperament_{life_id}",
                item_kind="constraint",
                source_revision=max(1, int(temperament["revision"])),
                summary=temperament_summary,
                epistemic_status="observed",
                confidence_milli=1000,
                priority=1180,
                privacy_scope="user:primary",
                token_count=max(1, len(temperament_summary.encode("utf-8"))),
                supporting_event_ids=(),
            )
        )
        # The context authority treats item_ref order as part of the signed,
        # deterministic context contract.  The projection above intentionally
        # reads newest memories first for selection, but that insertion order
        # must not cross the authority boundary: it is not a canonical order.
        return tuple(sorted(items, key=lambda item: item.item_ref))

    def _projection_inputs(self) -> LifeProjectionInputs:
        active = self._active()
        soul = self._soul()
        capabilities = {
            "by_id": deepcopy(self._scope_state().get("capabilities") or {}),
            "revision": int(self._scope_state().get("revision") or 1),
        }
        return LifeProjectionInputs(
            life_id=str(active["life_id"]),
            writer_epoch=int(active.get("writer_epoch") or 1),
            identity_revision=int(active.get("identity_revision") or 1),
            soul=soul,
            capabilities=capabilities,
            revision_floor=self._revisions(),
            external_items=self._external_memory_items(),
        )

    def _autonomy_state(self, life_id: str = "") -> dict[str, Any]:
        scope = self._scope_state(life_id)
        autonomy = normalize_autonomy_state(scope.get("autonomy"))
        settings = scope.get("settings") if isinstance(scope.get("settings"), Mapping) else {}
        autonomy["enabled"] = bool(settings.get("autonomy_enabled", True))
        autonomy["task_generation_enabled"] = bool(
            settings.get("autonomy_task_generation_enabled", True)
        )
        scope["autonomy"] = autonomy
        return autonomy

    def _generate_autonomy_tasks(self, *, life_id: str, reason: str) -> list[dict[str, Any]]:
        scope = self._scope_state(life_id)
        autonomy = self._autonomy_state(life_id)
        if not autonomy.get("enabled") or not autonomy.get("task_generation_enabled"):
            return []
        before = deepcopy(autonomy)
        candidates = derive_task_candidates(
            scope,
            life_id=life_id,
            day_key=utc_now()[:10],
        )
        candidate_fingerprints = {str(item.get("fingerprint") or "") for item in candidates}
        stale_updates: list[dict[str, Any]] = []
        for task_id, task in list(autonomy["tasks"].items()):
            if not isinstance(task, Mapping):
                continue
            if str(task.get("status") or "") not in ACTIVE_TASK_STATES:
                continue
            if str(task.get("fingerprint") or "") in candidate_fingerprints:
                continue
            stale_updates.append(
                update_task_status(
                    autonomy,
                    task_id=str(task_id),
                    status="cancelled",
                    result={
                        "reason_code": "life.autonomy.causal_state_resolved",
                        "generation_reason": str(reason or "scheduled")[:80],
                    },
                )
            )
        created = materialize_tasks(
            autonomy,
            candidates,
            life_id=life_id,
            reason=reason,
        )
        try:
            journal_entries = [
                {
                    "event_type": "autonomy.task_status_changed",
                    "payload": {"task_id": task["task_id"], "task": task},
                    "actor": "life_autonomy",
                    "idempotency_key": f"autonomy.task.auto-cancel:{task['task_id']}",
                }
                for task in stale_updates
            ] + [
                {
                    "event_type": "autonomy.task_generated",
                    "payload": {"task": task},
                    "actor": "life_autonomy",
                    "idempotency_key": f"autonomy.task:{task['task_id']}",
                }
                for task in created
            ]
            if journal_entries:
                self.system.journal.append_batch(life_id, journal_entries)
            if created or stale_updates or canonical_sha256(before) != canonical_sha256(autonomy):
                self._persist(life_id)
        except Exception:
            scope["autonomy"] = before
            raise
        return created

    def _scheduler_tick(self, reason: str) -> dict[str, Any]:
        """Run one local Life maintenance tick under the sole-writer lease."""
        with self._lock:
            if self._closed or self._closing or not self._lease.active:
                return {"ok": False, "generated": [], "reason_code": "life.scheduler.inactive"}
            life_id = str(self._active()["life_id"])
            scope = self._scope_state(life_id)
            if not bool(scope["settings"].get("heartbeat_enabled", True)):
                scope.setdefault("scheduler", {})["last_reason"] = "life.scheduler.disabled"
                return {
                    "ok": True,
                    "generated": [],
                    "reason_code": "life.scheduler.disabled",
                    "heartbeat_count": int(
                        scope.get("scheduler", {}).get("heartbeat_count") or 0
                    ),
                }
            affect_state, affect_elapsed_ms, affect_max_delta = self._decay_transient_affect(
                life_id
            )
            scheduler_state = scope.setdefault("scheduler", {})
            scheduler_state["heartbeat_count"] = int(scheduler_state.get("heartbeat_count") or 0) + 1
            scheduler_state["last_heartbeat_at"] = utc_now()
            scheduler_state["last_reason"] = str(reason or "scheduled")[:80]
            self.system.journal.append(
                life_id,
                "life.heartbeat",
                {
                    "reason": scheduler_state["last_reason"],
                    "heartbeat_count": scheduler_state["heartbeat_count"],
                },
                actor="life_scheduler",
                idempotency_key=f"heartbeat:{life_id}:{scheduler_state['heartbeat_count']}",
            )
            if affect_max_delta >= 1:
                self.system.journal.append(
                    life_id,
                    "affect.decayed",
                    {
                        "elapsed_ms": affect_elapsed_ms,
                        "max_delta_milli": affect_max_delta,
                        "primary_emotion": affect_state["primary_emotion"],
                        "intensity_milli": affect_state["intensity_milli"],
                    },
                    actor="life_scheduler",
                    # A decay event can be material within every 30-second
                    # heartbeat.  A five-minute bucket therefore aliases
                    # distinct payloads and trips the journal's strict
                    # idempotency conflict guard.  The recovered heartbeat
                    # counter is the stable, crash-safe operation identity.
                    idempotency_key=(
                        f"affect.decay:{life_id}:"
                        f"{scheduler_state['heartbeat_count']}"
                    ),
                )
            self._persist()
            lifecycle = self._advance_memory_lifecycles(life_id)
            created = self._generate_autonomy_tasks(life_id=life_id, reason=reason)
            if self._sync_daily_summary(life_id):
                self._persist(life_id)
            self._schedule_autonomous_activity_decision(life_id=life_id)
            self._schedule_autonomous_learning_decision(life_id=life_id)
            self._recover_approved_learning_cards(life_id=life_id)
            self._sync_life_capability_workspace_zone(life_id=life_id)
            self._schedule_capability_health_decision(life_id=life_id)
            self._schedule_self_iteration_decision(life_id=life_id)
            self._schedule_greeting(life_id=life_id)
            self._schedule_native_proactive(life_id=life_id)
            self._cognition_shadow_tick(life_id=life_id)
            return {
                "ok": True,
                "generated": created,
                "heartbeat_count": scheduler_state["heartbeat_count"],
                "memory_lifecycle": lifecycle,
                "affect": {
                    "elapsed_ms": affect_elapsed_ms,
                    "max_delta_milli": affect_max_delta,
                    "primary_emotion": affect_state["primary_emotion"],
                    "intensity_milli": affect_state["intensity_milli"],
                },
            }

    def set_cognition_decider(self, decider: Any) -> None:
        """Install the single unified cognition model decider (shadow sidecar)."""
        if decider is not None and not callable(decider):
            raise TypeError("cognition decider must be callable")
        with self._lock:
            self._cognition_decider = decider
            shadow = self._cognition_shadow
            if shadow is not None:
                shadow.set_decider(decider)

    def _cognition_trigger_candidates(self, life_id: str) -> list[CognitionTrigger]:
        """Collect G2 candidate seeds from existing authority state.

        The legacy four schedulers stay authoritative; each candidate here is
        only a durable trigger for the unified cognition shadow sidecar.
        """
        now_ms = time.time_ns() // 1_000_000
        triggers: list[CognitionTrigger] = []
        autonomy = self._autonomy_state(life_id)
        if isinstance(autonomy, Mapping) and autonomy.get("enabled"):
            tasks = autonomy.get("tasks")
            if isinstance(tasks, Mapping):
                local_hour = time.localtime(now_ms / 1000).tm_hour
                for task in tasks.values():
                    if not isinstance(task, Mapping):
                        continue
                    if str(task.get("status") or "") not in {"pending", "blocked"}:
                        continue
                    if str(task.get("source") or "") != "life_activity_catalog":
                        continue
                    if str(task.get("risk_class") or "") not in {"A0", "A1"}:
                        continue
                    if task.get("requires_user") is True:
                        continue
                    if not self._activity_window_open(
                        str(task.get("time_window") or ""), local_hour
                    ):
                        continue
                    task_id = str(task.get("task_id") or "")
                    if not task_id:
                        continue
                    triggers.append(
                        CognitionTrigger(
                            event_id="lev_" + canonical_sha256(
                                {"domain": "internal-stimulus", "kind": "activity", "task_id": task_id}
                            ),
                            lane="background",
                            base_priority=50,
                            payload_sha256=canonical_sha256(
                                {"kind": "activity", "task_id": task_id}
                            ),
                            coalesce=True,
                        )
                    )
        if callable(getattr(self, "_learning_decider", None)):
            triggers.append(
                CognitionTrigger(
                    event_id="lev_" + canonical_sha256(
                        {"domain": "internal-stimulus", "kind": "learning", "life_id": life_id}
                    ),
                    lane="background",
                    base_priority=40,
                    payload_sha256=canonical_sha256({"kind": "learning"}),
                    coalesce=True,
                )
            )
        if callable(getattr(self, "_self_iteration_decider", None)):
            triggers.append(
                CognitionTrigger(
                    event_id="lev_" + canonical_sha256(
                        {"domain": "internal-stimulus", "kind": "self_iteration", "life_id": life_id}
                    ),
                    lane="background",
                    base_priority=40,
                    payload_sha256=canonical_sha256({"kind": "self_iteration"}),
                    coalesce=True,
                )
            )
        if callable(getattr(self, "_greeting_writer", None)):
            triggers.append(
                CognitionTrigger(
                    event_id="lev_" + canonical_sha256(
                        {"domain": "internal-stimulus", "kind": "greeting", "life_id": life_id}
                    ),
                    lane="background",
                    base_priority=30,
                    payload_sha256=canonical_sha256({"kind": "greeting"}),
                    coalesce=True,
                )
            )
        return triggers

    def _cognition_shadow_tick(self, *, life_id: str) -> dict[str, int]:
        """Enqueue G2 candidate seeds; model pass runs off the writer lock."""
        shadow = self._cognition_shadow
        if shadow is None:
            return {"created": 0, "deduped": 0, "coalesced": 0}
        triggers = self._cognition_trigger_candidates(life_id)
        stats = shadow.enqueue_many(life_id, triggers)
        if self._cognition_decider is not None and stats["created"]:
            worker = threading.Thread(
                target=self._cognition_worker,
                args=(life_id,),
                daemon=True,
                name="life-cognition-shadow",
            )
            worker.start()
        return stats

    def _cognition_worker(self, life_id: str) -> None:
        try:
            self.run_cognition_shadow_pass(life_id)
        except Exception:
            # Shadow diagnostics never crash the heartbeat.
            return

    def run_cognition_shadow_pass(self, life_id: str) -> dict[str, Any]:
        """Run exactly one unified cognition shadow turn (test/worker entry)."""
        store = self._contract_store()
        shadow = UnifiedCognitionShadow(
            store,
            cognition_decider=self._cognition_decider,
            binding_factory=(
                lambda current_life_id, event_id, now_ms: self._cognition_binding_factory(
                    store, current_life_id, event_id, now_ms
                )
            ),
        )
        owner = self._lease.instance_id if self._lease is not None else "life"
        return shadow.run_pass(life_id, owner_instance_id=owner)

    def _cognition_binding_factory(
        self,
        store: LifeShadowStore,
        life_id: str,
        event_id: str,
        now_ms: int,
    ) -> str:
        """Deterministic internal-stimulus binding/root/child shadow projection."""
        head = LifeAuthorityHead(
            life_id=life_id,
            writer_epoch=1,
            identity_revision=1,
            identity_sha256=canonical_sha256({"life": life_id, "kind": "identity"}),
            soul_revision=1,
            soul_sha256=canonical_sha256({"life": life_id, "kind": "soul"}),
            affect_revision=1,
            affect_sha256=canonical_sha256({"life": life_id, "kind": "affect"}),
            deletion_epoch=0,
            head_sha256="0" * 64,
        ).with_computed_head_sha256()
        store.put_life_authority_head(head, expected_head_sha256=None)
        binding_id = "bind_" + canonical_sha256(
            {
                "domain": "run-life-binding",
                "life_id": life_id,
                "kind": "internal_stimulus",
                "subject": event_id,
            }
        )
        subject_sha256 = canonical_sha256({"subject": event_id})
        binding = RunLifeBinding(
            binding_id=binding_id,
            life_id=life_id,
            binding_subject_kind="internal_stimulus",
            binding_subject_id=event_id,
            binding_subject_sha256=subject_sha256,
            life_authority_head_sha256=head.head_sha256,
            writer_epoch=1,
            identity_revision=1,
            identity_sha256=head.identity_sha256,
            soul_revision=1,
            soul_sha256=head.soul_sha256,
            affect_revision=1,
            affect_sha256=head.affect_sha256,
            deletion_epoch=0,
            bound_at_ms=now_ms,
            binding_source="life_cognition",
            binding_sha256="0" * 64,
        ).with_computed_binding_sha256()
        store.put_run_life_binding(binding)
        root_id = "root_" + canonical_sha256(
            {
                "domain": "root-experience",
                "life_id": life_id,
                "trigger": event_id,
                "binding_id": binding_id,
            }
        )
        root = RootExperienceHead(
            root_experience_id=root_id,
            life_id=life_id,
            initial_run_life_binding_sha256=binding.binding_sha256,
            active_run_life_binding_sha256=binding.binding_sha256,
            root_trigger_event_id=event_id,
            root_trigger_event_sha256=subject_sha256,
            next_sequence_no=1,
            root_status="OPEN",
            head_sha256="0" * 64,
        ).with_computed_head_sha256()
        store.put_root_experience_head(root, expected_head_sha256=None)
        child = CausalEpisodeVNext(
            episode_id="cep_" + canonical_sha256(
                {"domain": "causal-episode", "root": root_id, "sequence_no": 1}
            ),
            life_id=life_id,
            root_experience_id=root_id,
            sequence_no=1,
            episode_kind="observation",
            run_life_binding_sha256=binding.binding_sha256,
            candidate_ids=(),
            selected_candidate_id=None,
            terminal_status="CLOSED",
            terminal_reason_code="cognition.shadow.observed",
            created_at_ms=now_ms,
            closed_at_ms=now_ms,
            episode_sha256="0" * 64,
        ).with_computed_episode_sha256()
        store.put_causal_episode_vnext(child)
        return root_id

    def set_autonomy_decider(self, decider: Any) -> None:
        """Install the gateway-owned model-only internal-activity executor."""
        if decider is not None and not callable(decider):
            raise TypeError("autonomy decider must be callable")
        with self._lock:
            self._autonomy_decider = decider

    def set_learning_decider(self, decider: Any) -> None:
        """Install the gateway-owned model-only learning decider.

        The callback receives a canonical activity scope and returns a decision
        mapping.  It is deliberately optional so standalone/offline Life still
        has a truthful heartbeat rather than silently pretending to learn.
        """
        if decider is not None and not callable(decider):
            raise TypeError("learning decider must be callable")
        with self._lock:
            self._learning_decider = decider

    def set_learning_share_writer(self, writer: Any) -> None:
        """Install the gateway-owned model-only learning-share copywriter.

        The callback receives a small material mapping (title/summary/target)
        and returns the proactive message text in the model's own words.  It is
        optional: without it, or when it fails, learning reports fall back to
        the deterministic template so publication is never blocked by copy.
        """
        if writer is not None and not callable(writer):
            raise TypeError("learning share writer must be callable")
        with self._lock:
            self._learning_share_writer = writer

    def set_proactive_decider(self, decider: Any) -> None:
        """Install the gateway-owned model-only P16 initiative decider."""
        if decider is not None and not callable(decider):
            raise TypeError("proactive decider must be callable")
        with self._lock:
            self._proactive_decider = decider

    def set_proactive_expression_writer(self, writer: Any) -> None:
        """Install the normal-dialogue-backed P16 expression writer."""
        if writer is not None and not callable(writer):
            raise TypeError("proactive expression writer must be callable")
        with self._lock:
            self._proactive_expression_writer = writer

    def set_proactive_world_provider(self, provider: Any) -> None:
        """Bind a read-only provider backed by committed World Understanding state."""
        if provider is not None and not callable(provider):
            raise TypeError("proactive world provider must be callable")
        with self._lock:
            self._proactive_world_provider = provider

    def set_self_iteration_decider(self, decider: Any) -> None:
        """Install the gateway-owned model-only self-iteration decider.

        The callback receives a canonical activity scope and returns a
        decision mapping: target ``none`` or ``upgrade`` with a bounded
        self-code patch proposal.  It is optional; without it the Life
        heartbeat stays truthful and simply records that no decider exists.
        """
        if decider is not None and not callable(decider):
            raise TypeError("self-iteration decider must be callable")
        with self._lock:
            self._self_iteration_decider = decider

    def set_upgrade_executor(self, executor: Any) -> None:
        """Install the gateway-owned upgrade patch executor.

        The callback receives ``{"changes": [...]}`` from one confirmed
        upgrade card and returns ``{"ok": bool, "results": [...]}``.  It runs
        inside the backend authority boundary, never inside Life, and it must
        not call back into Life state.
        """
        if executor is not None and not callable(executor):
            raise TypeError("upgrade executor must be callable")
        with self._lock:
            self._upgrade_executor = executor

    def set_greeting_writer(self, writer: Any) -> None:
        """Install the gateway-owned model-only greeting copywriter.

        Receives a small material mapping (persona/emotion/recent activity)
        and returns one casual first-person message.  Optional: without it,
        or when it fails, greetings fall back to the deterministic pool so
        the event never blocks the heartbeat.
        """
        if writer is not None and not callable(writer):
            raise TypeError("greeting writer must be callable")
        with self._lock:
            self._greeting_writer = writer

    def _recover_stale_running_autonomy_tasks(
        self,
        *,
        life_id: str,
        now_ms: int,
        stale_after_ms: int,
    ) -> int:
        """Move orphaned stale ``running`` autonomy tasks back to ``blocked``.

        A crash or abort between the journaled ``model-start`` and the
        worker's terminal update leaves a task persisted as ``running`` while
        no inflight decision remains to finish it (a live worker always holds
        ``autonomy_decision_inflight`` until its status update, so reaching
        this point with inflight clear means the running task is orphaned).
        Re-selecting it in place would reuse the spent
        ``autonomy.task.model-start`` idempotency key with a fresh payload and
        trip the journal's strict conflict guard.  Only tasks whose
        ``updated_at_ms`` is older than ``stale_after_ms`` (the decision
        cooldown acts as the attempt lease) are treated as stale, so a
        freshly started attempt is never recovered out from under its worker.
        Converting to ``blocked`` re-enters the normal state machine so the
        next start is a new attempt with a new attempt-scoped idempotency key
        instead of a running -> running refresh loop.
        """
        scope = self._scope_state(life_id)
        autonomy = self._autonomy_state(life_id)
        stale_task_ids = [
            str(task.get("task_id") or "")
            for task in autonomy.get("tasks", {}).values()
            if isinstance(task, Mapping)
            and str(task.get("status") or "") == "running"
            and now_ms - int(task.get("updated_at_ms") or task.get("created_at_ms") or 0)
            >= int(stale_after_ms)
        ]
        recovered_count = 0
        for task_id in stale_task_ids:
            before = deepcopy(autonomy)
            try:
                recovered = update_task_status(
                    autonomy,
                    task_id=task_id,
                    status="blocked",
                    now_ms=now_ms,
                    result={
                        "reason_code": "life.autonomy.stale_running_recovered",
                        "recovered_at_ms": now_ms,
                    },
                )
                self.system.journal.append(
                    life_id,
                    "autonomy.task_status_changed",
                    {"task_id": task_id, "task": recovered},
                    actor="life_autonomy",
                    idempotency_key=(
                        f"autonomy.task.recover-stale-running:{task_id}:"
                        f"{recovered.get('attempt_count', 0)}"
                    ),
                )
                self._persist(life_id)
            except Exception:
                scope["autonomy"] = before
                raise
            recovered_count += 1
        return recovered_count

    _ACTIVITY_WINDOW_HOURS = {
        "上午": (6, 11),
        "白天": (8, 18),
        "下午": (12, 18),
        "傍晚": (17, 21),
        "晚间": (20, 24),
        "空闲时": (0, 24),
    }

    @classmethod
    def _activity_window_open(cls, window: str, hour: int) -> bool:
        """A scheduled activity is due only inside its own time window."""
        start, end = cls._ACTIVITY_WINDOW_HOURS.get(str(window or "").strip(), (0, 24))
        return start <= hour < end

    # 六欲趋向：当下情绪把自由行动推向不同的方向。分值叠加在目录优先级上，
    # 只影响"闲时选哪件"，不改变"到点必做"的窗口约束。
    _DESIRE_ACTIVITY_AFFINITY = {
        "joy": {"creative_exploration": 80, "learning_review": 50, "relationship_care": 40},
        "interest": {"creative_exploration": 90, "learning_review": 70, "knowledge_organization": 30},
        "hope": {"goal_progress": 80, "daily_planning": 60, "learning_review": 30},
        "gratitude": {"relationship_care": 80, "self_reflection": 30},
        "warmth": {"relationship_care": 90, "creative_exploration": 20},
        "calm": {"knowledge_organization": 60, "capability_inventory": 60, "self_reflection": 30},
        "concern": {"system_health": 70, "workspace_hygiene": 40, "goal_progress": 30},
        "sadness": {"self_reflection": 80, "end_of_day_summary": 50},
        "frustration": {"system_health": 50, "self_reflection": 60, "workspace_hygiene": 30},
        "disappointment": {"self_reflection": 70, "goal_progress": 40},
        "vigilance": {"system_health": 90, "capability_inventory": 40, "workspace_hygiene": 30},
        "fatigue": {"end_of_day_summary": 80, "self_reflection": 50},
    }

    def _desire_affinity(self, scope: Mapping[str, Any]) -> dict[str, int]:
        """Weight free-time activities by the two strongest current emotions."""
        affect = scope.get("affect") if isinstance(scope.get("affect"), Mapping) else {}
        emotions = affect.get("emotions") if isinstance(affect.get("emotions"), Mapping) else {}
        ranked = sorted(
            (
                (str(name), max(0, min(1000, int(value or 0))))
                for name, value in emotions.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            ),
            key=lambda item: -item[1],
        )[:2]
        affinity: dict[str, int] = {}
        for name, milli in ranked:
            for activity_id, bonus in self._DESIRE_ACTIVITY_AFFINITY.get(name, {}).items():
                weighted = bonus * milli // 1000
                if weighted > affinity.get(activity_id, 0):
                    affinity[activity_id] = weighted
        return affinity

    def _schedule_autonomous_activity_decision(self, *, life_id: str) -> None:
        """Execute one catalog activity through the gateway model bridge.

        Only catalog-defined, internal A0/A1 work is eligible here.  Tool use,
        file mutation, messaging and all other external effects remain outside
        this method and must use the normal Gateway authorization chain.
        """
        scope = self._scope_state(life_id)
        scheduler = scope.setdefault("scheduler", {})
        autonomy = self._autonomy_state(life_id)
        if not autonomy.get("enabled") or not callable(getattr(self, "_autonomy_decider", None)):
            return
        if bool(scheduler.get("autonomy_decision_inflight")):
            return
        now_ms = time.time_ns() // 1_000_000
        cooldown_ms = max(
            60_000,
            int(float(os.environ.get("TIANGONG_LIFE_AUTONOMY_DECISION_SECONDS") or 600) * 1000),
        )
        # Recover orphaned stale running attempts before selection so they
        # re-enter as blocked with a fresh attempt/key instead of conflicting.
        self._recover_stale_running_autonomy_tasks(
            life_id=life_id,
            now_ms=now_ms,
            stale_after_ms=cooldown_ms,
        )
        if now_ms - int(scheduler.get("last_autonomy_decision_at_ms") or 0) < cooldown_ms:
            return
        budget_day = utc_now()[:10]
        if str(scheduler.get("model_budget_date") or "") != budget_day:
            scheduler.update(
                {
                    "model_budget_date": budget_day,
                    "model_attempts": 0,
                    "model_successes": 0,
                    "model_failures": 0,
                    "model_timeouts": 0,
                    "model_skipped": 0,
                }
            )
        settings = scope.get("settings") if isinstance(scope.get("settings"), Mapping) else {}
        permission_mode = str(settings.get("permission_mode") or "confirm_high_risk")
        if permission_mode == "confirm_all":
            scheduler["last_autonomy_decision_error"] = "life.autonomy.user_confirmation_required"
            return
        risk_rank = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4}
        configured_risk = str(settings.get("autonomous_risk_max") or "A4")
        configured_risk_rank = risk_rank.get(configured_risk, 0)
        success_limit = max(0, int(settings.get("llm_daily_budget") or 20))
        attempt_limit = max(0, int(settings.get("llm_daily_attempt_budget") or 30))
        if (
            (success_limit and int(scheduler.get("model_successes") or 0) >= success_limit)
            or (attempt_limit and int(scheduler.get("model_attempts") or 0) >= attempt_limit)
        ):
            scheduler["model_skipped"] = int(scheduler.get("model_skipped") or 0) + 1
            scheduler["last_autonomy_decision_at_ms"] = now_ms
            scheduler["last_autonomy_decision_error"] = "life.autonomy.model_budget_exhausted"
            self._persist(life_id)
            return
        eligible = [
            task for task in autonomy.get("tasks", {}).values()
            if isinstance(task, Mapping)
            and str(task.get("status") or "") in {"pending", "blocked"}
            and str(task.get("source") or "") == "life_activity_catalog"
            and str(task.get("risk_class") or "") in {"A0", "A1"}
            and risk_rank.get(str(task.get("risk_class") or ""), 99) <= configured_risk_rank
            and task.get("requires_user") is not True
        ]
        if not eligible:
            return
        # 日程语义：落在自己时间窗里的计划是"到点必做"，先于一切自由行动；
        # 没有到点计划时，才从自由行动列表里按当下情绪趋向挑一件。
        local_hour = time.localtime(now_ms / 1000).tm_hour
        due = [
            task for task in eligible
            if self._activity_window_open(str(task.get("time_window") or ""), local_hour)
        ]
        pool = due if due else eligible
        desire = self._desire_affinity(scope)
        pool.sort(
            key=lambda task: (
                -(
                    int(task.get("priority") or 0)
                    + desire.get(str(task.get("activity_id") or ""), 0)
                ),
                int(task.get("sequence") or 0),
                str(task.get("task_id") or ""),
            )
        )
        task_id = str(pool[0].get("task_id") or "")
        before = deepcopy(autonomy)
        try:
            running = update_task_status(autonomy, task_id=task_id, status="running", now_ms=now_ms)
            self.system.journal.append(
                life_id,
                "autonomy.task_status_changed",
                {"task_id": task_id, "task": running},
                actor="life_autonomy",
                idempotency_key=f"autonomy.task.model-start:{task_id}:{running.get('attempt_count', 0)}",
            )
            scheduler["autonomy_decision_inflight"] = True
            scheduler["last_autonomy_decision_at_ms"] = now_ms
            scheduler["last_autonomy_decision_error"] = ""
            scheduler["model_attempts"] = int(scheduler.get("model_attempts") or 0) + 1
            self._persist(life_id)
        except Exception:
            scope["autonomy"] = before
            raise

        def worker() -> None:
            try:
                with self._lock:
                    activity_scope = build_activity_scope(
                        life_id=life_id,
                        soul=self._soul(),
                        scope=self._scope_state(life_id),
                    )
                    task = deepcopy(self._autonomy_state(life_id)["tasks"][task_id])
                decision = self._autonomy_decider(activity_scope, task)
                if not isinstance(decision, Mapping):
                    raise ValueError("autonomy model result is invalid")
                result = deepcopy(dict(decision))
                if len(canonical_json_bytes(result)) > _MAX_TASK_RESULT_BYTES:
                    raise ValueError("autonomy model result is too large")
                summary = str(result.get("summary") or result.get("outcome") or "").strip()
                if not summary:
                    raise ValueError("autonomy model result has no summary")
                result["summary"] = summary[:4000]
                result["activity_id"] = str(task.get("activity_id") or "")
                result["execution_scope"] = "internal_life_state"
                result["external_side_effects"] = False
                with self._lock:
                    if self._closed or self._closing or not self._lease.active:
                        return
                    current = self._autonomy_state(life_id)["tasks"].get(task_id)
                    if not isinstance(current, Mapping) or str(current.get("status") or "") != "running":
                        return
                    completed = update_task_status(
                        self._autonomy_state(life_id),
                        task_id=task_id,
                        status="completed",
                        result=result,
                    )
                    scheduler_state = self._scope_state(life_id).setdefault("scheduler", {})
                    scheduler_state["model_successes"] = int(
                        scheduler_state.get("model_successes") or 0
                    ) + 1
                    self.system.journal.append(
                        life_id,
                        "autonomy.task_status_changed",
                        {"task_id": task_id, "task": completed},
                        actor="life_autonomy",
                        idempotency_key=f"autonomy.task.model-complete:{task_id}:{completed.get('attempt_count', 0)}",
                    )
                    self._sync_daily_summary(life_id)
                    self._persist(life_id)
            except Exception as exc:
                with self._lock:
                    if self._closed or self._closing or not self._lease.active:
                        return
                    current = self._autonomy_state(life_id)["tasks"].get(task_id)
                    if isinstance(current, Mapping) and str(current.get("status") or "") == "running":
                        blocked = update_task_status(
                            self._autonomy_state(life_id),
                            task_id=task_id,
                            status="blocked",
                            result={
                                "reason_code": "life.autonomy.model_activity_failed",
                                "error_type": type(exc).__name__,
                            },
                        )
                        self.system.journal.append(
                            life_id,
                            "autonomy.task_status_changed",
                            {"task_id": task_id, "task": blocked},
                            actor="life_autonomy",
                            idempotency_key=f"autonomy.task.model-blocked:{task_id}:{blocked.get('attempt_count', 0)}",
                        )
                    scheduler_state = self._scope_state(life_id).setdefault("scheduler", {})
                    activity_detail = re.sub(r"\s+", " ", str(exc)).strip()[:160]
                    scheduler_state["last_autonomy_decision_error"] = (
                        f"life.autonomy.model_activity_failed:{type(exc).__name__}:{activity_detail}"
                    )
                    scheduler_state["model_failures"] = int(
                        scheduler_state.get("model_failures") or 0
                    ) + 1
                    if isinstance(exc, TimeoutError):
                        scheduler_state["model_timeouts"] = int(
                            scheduler_state.get("model_timeouts") or 0
                        ) + 1
                    self._persist(life_id)
            finally:
                with self._lock:
                    if not self._closed and not self._closing and self._lease.active:
                        self._scope_state(life_id).setdefault("scheduler", {})[
                            "autonomy_decision_inflight"
                        ] = False
                        self._persist(life_id)

        threading.Thread(
            target=worker,
            name="tiangong-life-autonomy-decision",
            daemon=True,
        ).start()

    def set_artifact_action_catalog_provider(self, provider: Any) -> None:
        """Bind the gateway's read-only executable action view.

        Dynamic learning artifacts may reference this view, but cannot mutate
        the release-pinned registry that produced it.
        """
        if provider is not None and not callable(provider):
            raise ValueError("artifact action catalog provider must be callable")
        with self._lock:
            self._artifact_action_catalog_provider = provider

    def set_artifact_publisher(self, publisher: Any) -> None:
        """Bind a gateway-owned publisher (knowledge store / dynamic overlay)."""
        if publisher is not None and not callable(publisher):
            raise ValueError("artifact publisher must be callable")
        with self._lock:
            self._artifact_publisher = publisher

    def set_world_identity_provider(self, provider: Any) -> None:
        """Bind the Gateway-owned scope projection used by WU post-commit events."""
        if provider is not None and not callable(provider):
            raise ValueError("world identity provider must be callable")
        with self._lock:
            self._world_identity_provider = provider

    def _notify_life_learning_post_commit(
        self,
        *,
        life_id: str,
        event: Mapping[str, Any],
        artifact: Mapping[str, Any],
        status: str,
        learning: Mapping[str, Any] | None = None,
    ) -> None:
        """Project one already-committed Life transition into the native WU ingress."""
        try:
            from contracts.world_understanding.life_learning import LifeLearningObservation
            from world_understanding.post_commit import NativePostCommitEvent, notify_native_post_commit

            artifact_id = str(artifact.get("artifact_id") or "")
            artifact_kind = str(artifact.get("kind") or artifact.get("target") or "")
            lineage_id = str(artifact.get("lineage_id") or artifact_id)
            learning_id = str(
                (learning or {}).get("learning_id")
                or artifact.get("learning_id")
                or ""
            )
            sequence = int(event.get("sequence") or 0)
            if not all(_OPAQUE.fullmatch(value) for value in (life_id, artifact_id, lineage_id)):
                return
            if artifact_kind not in {"knowledge", "skill", "tool"} or sequence < 1:
                return
            source = (
                (learning or {}).get("learning_evidence")
                if isinstance((learning or {}).get("learning_evidence"), Mapping)
                else artifact.get("learning_evidence")
            )
            source = source if isinstance(source, Mapping) else {}
            source_detail = source.get("source") if isinstance(source.get("source"), Mapping) else {}
            learned_refs: set[str] = {
                str(value)
                for value in source_detail.get("memory_refs") or ()
                if _OPAQUE.fullmatch(str(value))
            }
            for repository in source_detail.get("repository_evidence") or ():
                if not isinstance(repository, Mapping):
                    continue
                frame_id = str(repository.get("frame_id") or "")
                if _OPAQUE.fullmatch(frame_id):
                    learned_refs.add(frame_id)
                for entity in repository.get("entity_refs") or ():
                    if isinstance(entity, Mapping):
                        record_id = str(entity.get("record_id") or "")
                        if _OPAQUE.fullmatch(record_id):
                            learned_refs.add(record_id)
            evidence_refs = {
                str(value)
                for value in (
                    event.get("event_id"),
                    event.get("event_sha256"),
                    artifact.get("artifact_sha256"),
                    source.get("evidence_sha256"),
                )
                if _OPAQUE.fullmatch(str(value or ""))
            }
            summary = self._redact_sensitive_text(
                str((learning or {}).get("summary") or artifact.get("summary") or artifact.get("title") or "")
            )[:1000]
            observation = LifeLearningObservation(
                life_id=life_id,
                learning_id=learning_id or None,
                artifact_id=artifact_id,
                artifact_kind=artifact_kind,
                lineage_id=lineage_id,
                status=status,
                learned_subject_refs=tuple(sorted(learned_refs))[:64],
                safe_summary=summary,
                evidence_refs=tuple(sorted(evidence_refs))[:256],
                confidence_milli=1000,
                epistemic_status="verified",
                prior_revision=sequence - 1,
                new_revision=sequence,
                occurred_at_ms=time.time_ns() // 1_000_000,
                observation_sha256="0" * 64,
            ).with_computed_hash()
            identity: dict[str, str] = {"life_id": life_id}
            provider = self._world_identity_provider
            if callable(provider):
                supplied = provider(life_id)
                if isinstance(supplied, Mapping):
                    identity.update({key: str(value or "") for key, value in supplied.items()})
            notify_native_post_commit(NativePostCommitEvent(
                source_kind="LIFE_LEARNING",
                source_native_id="lifelearn." + observation.observation_sha256[:48],
                producer_ref="life_service.learning.post_commit",
                payload=observation.model_dump(mode="json"),
                occurred_at_ms=observation.occurred_at_ms,
                identity=identity,
            ))
        except Exception:
            # Projection failure cannot rewrite the authoritative Life outcome.
            return

    def set_capability_workspace_mapper(self, mapper: Any) -> None:
        """Bind the gateway-owned workspace-zone mapper for life skills/tools."""
        if mapper is not None and not callable(mapper):
            raise ValueError("capability workspace mapper must be callable")
        with self._lock:
            self._capability_workspace_mapper = mapper

    def set_capability_workspace_remover(self, remover: Any) -> None:
        """Bind the gateway-owned workspace-zone remover for life skills/tools."""
        if remover is not None and not callable(remover):
            raise ValueError("capability workspace remover must be callable")
        with self._lock:
            self._capability_workspace_remover = remover

    def set_capability_workspace_marker(self, marker: Any) -> None:
        """Bind the gateway-owned workspace-zone status marker.

        Called when a pointer transitions to degraded/reactivated/disabled so
        the workspace mirror keeps an explicit status front-matter instead of
        silently diverging from the authoritative pointer state.
        """
        if marker is not None and not callable(marker):
            raise ValueError("capability workspace marker must be callable")
        with self._lock:
            self._capability_workspace_marker = marker

    def set_capability_patch_decider(self, decider: Any) -> None:
        """Bind the gateway-owned model bridge used to draft capability patches."""
        if decider is not None and not callable(decider):
            raise ValueError("capability patch decider must be callable")
        with self._lock:
            self._capability_patch_decider = decider

    def set_artifact_invoker(self, invoker: Any) -> None:
        """Bind the fixed gateway entrypoint used by published composite artifacts."""
        if invoker is not None and not callable(invoker):
            raise ValueError("artifact invoker must be callable")
        with self._lock:
            self._artifact_invoker = invoker

    def set_learning_materializers(self, *, researcher: Any = None, synthesizer: Any = None) -> None:
        """Bind Gateway-owned read-only research and model synthesis callbacks."""
        if researcher is not None and not callable(researcher):
            raise ValueError("learning researcher must be callable")
        if synthesizer is not None and not callable(synthesizer):
            raise ValueError("learning synthesizer must be callable")
        with self._lock:
            self._learning_researcher = researcher
            self._learning_synthesizer = synthesizer

    def _schedule_autonomous_learning_decision(self, *, life_id: str) -> None:
        scope_state = self._scope_state(life_id)
        scheduler = scope_state.setdefault("scheduler", {})
        settings = scope_state["settings"]
        if not bool(settings.get("autonomy_enabled", True)):
            scheduler["last_learning_decision_error"] = "life.learning.autonomy_disabled"
            return
        if str(settings.get("permission_mode") or "") == "confirm_all":
            scheduler["last_learning_decision_error"] = "life.learning.user_confirmation_required"
            return
        now_ms = time.time_ns() // 1_000_000
        # A model decision every five minutes is enough for a desktop life and
        # avoids turning the 30-second heartbeat into a provider polling loop.
        if bool(scheduler.get("learning_decision_inflight")):
            return
        if now_ms - int(scheduler.get("last_learning_decision_at_ms") or 0) < 300_000:
            return
        if not callable(getattr(self, "_learning_decider", None)):
            scheduler["last_learning_decision_error"] = "life.learning.model_bridge_unavailable"
            return
        scheduler["learning_decision_inflight"] = True
        scheduler["last_learning_decision_at_ms"] = now_ms
        scheduler["last_learning_decision_error"] = ""
        self._persist(life_id)

        def worker() -> None:
            try:
                scope = build_activity_scope(life_id=life_id, soul=self._soul(), scope=self._scope_state(life_id))
                decision = self._learning_decider(scope)
                if not isinstance(decision, Mapping):
                    raise ValueError("learning model decision is invalid")
                target = str(decision.get("target") or decision.get("artifact_kind") or "").strip().casefold()
                with self._lock:
                    if target in {"", "none", "no_learning", "no-learning"}:
                        self.system.journal.append(
                            life_id, "learning.decision_noop", {"activity_scope_sha256": scope["scope_sha256"]},
                            actor="life_learning", idempotency_key=f"learning.noop:{scope['scope_sha256']}",
                        )
                        return
                    drafted = self._learning_draft({"life_id": life_id, "decision": decision}, source="autonomous")
                    learning = drafted.get("learning") if isinstance(drafted.get("learning"), Mapping) else {}
                    risk_rank = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4}
                    effective_risk = str(
                        learning.get("effective_risk_level")
                        or learning.get("risk_level")
                        or "A0"
                    )
                    risk_allowed = risk_rank.get(effective_risk, 99) <= risk_rank.get(
                        str(self._scope_state(life_id)["settings"].get("autonomous_risk_max") or "A0"),
                        0,
                    )
                    if (
                        not drafted.get("suppressed")
                        and not drafted.get("duplicate")
                        and learning.get("status") == "approved"
                        and risk_allowed
                    ):
                        self._learning_publish({"life_id": life_id, "learning_id": learning.get("learning_id"), "actor": "life_learning"})
                    elif learning.get("status") == "approved" and not risk_allowed:
                        self._scope_state(life_id).setdefault("scheduler", {})[
                            "last_learning_decision_error"
                        ] = "life.learning.autonomous_risk_limit"
            except Exception as exc:
                # Keep a short, single-line excerpt of the failure message so
                # the panel can explain why no learning card was produced
                # instead of recording only the exception type.
                detail = re.sub(r"\s+", " ", str(exc)).strip()[:160]
                with self._lock:
                    self._scope_state(life_id).setdefault("scheduler", {})[
                        "last_learning_decision_error"
                    ] = f"life.learning.decision_failed:{type(exc).__name__}:{detail}"
            finally:
                with self._lock:
                    scheduler_state = self._scope_state(life_id).setdefault("scheduler", {})
                    scheduler_state["learning_decision_inflight"] = False
                    self._persist(life_id)

        threading.Thread(target=worker, name="tiangong-life-learning-decision", daemon=True).start()

    def _schedule_self_iteration_decision(self, *, life_id: str) -> None:
        """Ask the model, at a slow cadence, whether to propose a self-code
        upgrade card.  Every produced card waits for explicit user
        confirmation; nothing modifies code autonomously."""
        scope_state = self._scope_state(life_id)
        scheduler = scope_state.setdefault("scheduler", {})
        settings = scope_state["settings"]
        if not bool(settings.get("autonomy_enabled", True)):
            scheduler["last_self_iteration_decision_error"] = "life.self_iteration.autonomy_disabled"
            return
        if str(settings.get("permission_mode") or "") == "confirm_all":
            scheduler["last_self_iteration_decision_error"] = "life.self_iteration.user_confirmation_required"
            return
        now_ms = time.time_ns() // 1_000_000
        # Self-iteration reviews code: half-hourly is frequent enough and
        # keeps the model budget dominated by user-facing work.
        if bool(scheduler.get("self_iteration_decision_inflight")):
            return
        if now_ms - int(scheduler.get("last_self_iteration_decision_at_ms") or 0) < 1_800_000:
            return
        if not callable(getattr(self, "_self_iteration_decider", None)):
            scheduler["last_self_iteration_decision_error"] = "life.self_iteration.model_bridge_unavailable"
            return
        scheduler["self_iteration_decision_inflight"] = True
        scheduler["last_self_iteration_decision_at_ms"] = now_ms
        scheduler["last_self_iteration_decision_error"] = ""
        self._persist(life_id)

        def worker() -> None:
            try:
                scope = build_activity_scope(life_id=life_id, soul=self._soul(), scope=self._scope_state(life_id))
                decision = self._self_iteration_decider(scope)
                if not isinstance(decision, Mapping):
                    raise ValueError("self-iteration model decision is invalid")
                target = str(decision.get("target") or "").strip().casefold()
                with self._lock:
                    if target in {"", "none", "no_upgrade", "no-upgrade"}:
                        self.system.journal.append(
                            life_id, "self_iteration.decision_noop", {"activity_scope_sha256": scope["scope_sha256"]},
                            actor="life_self_iteration", idempotency_key=f"self_iteration.noop:{scope['scope_sha256']}:{now_ms // 1_800_000}",
                        )
                        return
                    self._upgrade_draft(life_id, decision, source="autonomous")
            except Exception as exc:
                detail = re.sub(r"\s+", " ", str(exc)).strip()[:160]
                with self._lock:
                    self._scope_state(life_id).setdefault("scheduler", {})[
                        "last_self_iteration_decision_error"
                    ] = f"life.self_iteration.decision_failed:{type(exc).__name__}:{detail}"
            finally:
                with self._lock:
                    scheduler_state = self._scope_state(life_id).setdefault("scheduler", {})
                    scheduler_state["self_iteration_decision_inflight"] = False
                    self._persist(life_id)

        threading.Thread(target=worker, name="tiangong-life-self-iteration", daemon=True).start()

    _GREETING_FALLBACK_BY_EMOTION = {
        "joy": "今天心情特别好，想跟你说一声～",
        "interest": "我刚刚发现一个挺有意思的点子，等整理好再跟你细说。",
        "hope": "我在琢磨接下来能做点什么更有意思的事，有点期待。",
        "gratitude": "忽然想跟你说声谢谢，有你在我挺安心的。",
        "warmth": "没什么事，就是想来跟你打个招呼。",
        "calm": "我这边一切平稳，就是想看看你怎么样了。",
        "concern": "我有点在意系统的状态，刚自己检查了一圈，顺便来问问你。",
        "sadness": "今天有点低落，来找你说说话。",
        "frustration": "刚遇到点让我较劲的事，缓一缓，来跟你打个招呼。",
        "disappointment": "有点小失落，不过看到你在我就好些了。",
        "vigilance": "我保持着警觉在巡检呢，一切正常，顺便问候你一下。",
        "fatigue": "有点累了，来你这儿歇口气。",
    }

    @staticmethod
    def _proactive_timestamp_ms(value: object) -> int:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        raw = str(value or "").strip()
        if not raw:
            return 0
        try:
            return max(0, int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1000))
        except (TypeError, ValueError, OSError):
            return 0

    def _project_proactive_relationships(self, *, life_id: str) -> list[dict[str, Any]]:
        """Return bounded relationship metrics without raw promises/obligations/text."""
        scope = self._scope_state(life_id)
        raw = scope.get("relationships") if isinstance(scope.get("relationships"), Mapping) else {}
        rows: list[dict[str, Any]] = []
        for key, relation in sorted(raw.items(), key=lambda item: str(item[0])):
            if len(rows) >= 16 or not isinstance(relation, Mapping):
                continue
            target = str(relation.get("target_life_id") or key or "")[:240]
            metrics: dict[str, Any] = {
                "relationship_ref": "relationship_" + canonical_sha256({
                    "domain": "tiangong.life.proactive-relationship.v1",
                    "life_id": life_id,
                    "target": target,
                })[:24],
                "direction": str(relation.get("direction") or "")[:32],
                "updated_at": str(relation.get("updated_at") or "")[:48],
            }
            for field in (
                "trust_milli",
                "familiarity_milli",
                "liking_milli",
                "attachment_milli",
                "cooperation_milli",
            ):
                value = relation.get(field)
                if isinstance(value, int) and not isinstance(value, bool):
                    metrics[field] = max(0, min(1000, value))
            for source_field, count_field in (
                ("obligations", "obligation_count"),
                ("promises", "promise_count"),
                ("relationship_tags", "tag_count"),
            ):
                value = relation.get(source_field)
                metrics[count_field] = min(64, len(value)) if isinstance(value, (list, tuple, set)) else 0
            rows.append(metrics)
        return rows

    def _proactive_world_observations(self, *, life_id: str, now_ms: int) -> list[dict[str, Any]]:
        """Project only committed WU evidence; unavailable/invalid authority yields no facts."""
        provider = self._proactive_world_provider
        identity_provider = self._world_identity_provider
        if not callable(provider) or not callable(identity_provider):
            return []
        try:
            supplied_identity = identity_provider(life_id)
            if not isinstance(supplied_identity, Mapping):
                return []
            identity = {
                key: str(value or "")
                for key, value in supplied_identity.items()
                if str(key) in {"life_id", "principal_scope_hash", "workspace_id"}
            }
            if not identity.get("life_id") or not identity.get("principal_scope_hash") or not identity.get("workspace_id"):
                return []
            snapshot = provider(identity)
        except Exception:
            return []
        if not isinstance(snapshot, Mapping):
            return []
        if str(snapshot.get("schema") or "") != "tiangong.life.repository-evidence.v1":
            return []
        observed_at_ms = self._proactive_timestamp_ms(snapshot.get("observed_at_ms"))
        frame_id = str(snapshot.get("frame_id") or "").strip()[:240]
        revision_hash = str(snapshot.get("frame_revision_hash") or "").strip()[:128]
        if not observed_at_ms or not frame_id or not revision_hash:
            return []
        entity_refs = snapshot.get("entity_refs") if isinstance(snapshot.get("entity_refs"), list) else []
        bounded_entities: list[dict[str, str]] = []
        for entity in entity_refs[:24]:
            if not isinstance(entity, Mapping):
                continue
            record_id = str(entity.get("record_id") or entity.get("entity_id") or "")[:160]
            sha256 = str(entity.get("sha256") or "")[:128]
            if record_id and sha256:
                bounded_entities.append({"record_id": record_id, "sha256": sha256})
        summary = {
            "frame_id": frame_id,
            "frame_revision_hash": revision_hash,
            "branch": str(snapshot.get("branch") or "")[:160],
            "commit": str(snapshot.get("commit") or "")[:160],
            "entity_refs": bounded_entities,
        }
        return [{
            "source_ref": f"world:repository:{frame_id}:{revision_hash[:24]}",
            "observed_at_ms": observed_at_ms,
            "confidence_milli": 1000,
            "epistemic_state": "KNOWN",
            "authority": "world_understanding_committed",
            "kind": "world:repository_evidence",
            "summary": json.dumps(summary, ensure_ascii=False, sort_keys=True)[:1600],
        }]

    def _build_proactive_context(self, *, life_id: str, now_ms: int) -> dict[str, Any]:
        """Build a bounded, rebuildable P16 projection from existing authorities."""
        scope = self._scope_state(life_id)
        scheduler = scope.setdefault("scheduler", {})
        observations: list[dict[str, Any]] = []
        memories = scope.get("memories") if isinstance(scope.get("memories"), Mapping) else {}
        for memory_id, row in reversed(list(memories.items())):
            if len(observations) >= 24 or not isinstance(row, Mapping):
                continue
            if str(row.get("status") or "active") != "active":
                continue
            classification = row.get("classification") if isinstance(row.get("classification"), Mapping) else {}
            memory_type = str(classification.get("memory_type") or row.get("memory_type") or "")
            if memory_type not in {"goal", "user_preference", "hard_constraint", "relationship", "causal_summary", "observation"}:
                continue
            observed_at_ms = self._proactive_timestamp_ms(
                row.get("created_at_ms") or row.get("created_at") or ""
            )
            content = json.dumps(row.get("content"), ensure_ascii=False, sort_keys=True)
            if not content.strip():
                continue
            privacy = scope.get("settings", {}).get("privacy") if isinstance(scope.get("settings"), Mapping) else {}
            if isinstance(privacy, Mapping) and bool(privacy.get("redact_llm", True)):
                content = self._redact_sensitive_text(content)
            observations.append({
                "source_ref": f"memory:{memory_id}",
                "observed_at_ms": observed_at_ms,
                "confidence_milli": max(0, min(1000, int(row.get("confidence_milli") or 800))),
                "epistemic_state": "KNOWN" if observed_at_ms else "UNKNOWN",
                "kind": f"memory:{memory_type or 'unknown'}",
                "summary": content[:1600],
            })

        autonomy = self._autonomy_state(life_id)
        task_projection: list[dict[str, Any]] = []
        for task in autonomy.get("tasks", {}).values():
            if len(task_projection) >= 16 or not isinstance(task, Mapping):
                continue
            result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
            task_row = {
                "task_id": str(task.get("task_id") or ""),
                "activity_id": str(task.get("activity_id") or task.get("task_kind") or ""),
                "title": str(task.get("title") or task.get("objective") or "")[:240],
                "status": str(task.get("status") or ""),
                "summary": str(result.get("summary") or "")[:800],
                "updated_at_ms": int(task.get("updated_at_ms") or 0),
            }
            task_projection.append(task_row)
            if task_row["updated_at_ms"] and (task_row["title"] or task_row["summary"]):
                observations.append({
                    "source_ref": f"life-task:{task_row['task_id']}",
                    "observed_at_ms": task_row["updated_at_ms"],
                    "confidence_milli": 1000,
                    "epistemic_state": "KNOWN",
                    "kind": "life_task",
                    "summary": json.dumps(task_row, ensure_ascii=False, sort_keys=True)[:1600],
                })

        observations.extend(self._proactive_world_observations(life_id=life_id, now_ms=now_ms))

        deliveries = [
            int(row.get("created_at_ms") or 0)
            for row in scope.get("proactive_chats", [])
            if isinstance(row, Mapping)
            and str(row.get("reason") or "") == "life.proactive.native"
            and int(row.get("created_at_ms") or 0) > 0
        ]
        affect = scope.get("affect") if isinstance(scope.get("affect"), Mapping) else {}
        return {
            "schema": "tiangong.life.initiative-context.v1",
            "life_id": life_id,
            "observed_at_ms": now_ms,
            "authority": "embedded_life_runtime",
            "epistemic_rule": "missing_source_is_UNKNOWN",
            "last_user_activity_at_ms": int(scheduler.get("last_user_activity_at_ms") or 0),
            "last_user_run_id": str(scheduler.get("last_user_run_id") or ""),
            "recent_delivery_times_ms": deliveries[-64:],
            "observations": observations[:40],
            "recent_tasks": task_projection,
            "relationships": self._project_proactive_relationships(life_id=life_id),
            "affect": {
                "primary_emotion": str(affect.get("primary_emotion") or "calm"),
                "primary_emotion_zh": str(affect.get("primary_emotion_zh") or "平静"),
                "intensity_milli": int(affect.get("intensity_milli") or 0),
                "expression_directive": str(affect.get("expression_directive") or "")[:800],
            },
        }

    def _reset_proactive_model_budget_if_needed(self, scheduler: dict[str, Any]) -> None:
        budget_day = utc_now()[:10]
        if str(scheduler.get("model_budget_date") or "") == budget_day:
            return
        scheduler.update({
            "model_budget_date": budget_day,
            "model_attempts": 0,
            "model_successes": 0,
            "model_failures": 0,
            "model_timeouts": 0,
            "model_skipped": 0,
        })

    def _reserve_proactive_model_call_locked(
        self,
        *,
        scheduler: dict[str, Any],
        settings: Mapping[str, Any],
    ) -> bool:
        """Reserve exactly one LLM call before invoking it."""
        self._reset_proactive_model_budget_if_needed(scheduler)
        success_limit = max(0, int(settings.get("llm_daily_budget") or 20))
        attempt_limit = max(0, int(settings.get("llm_daily_attempt_budget") or 30))
        if (
            (success_limit and int(scheduler.get("model_successes") or 0) >= success_limit)
            or (attempt_limit and int(scheduler.get("model_attempts") or 0) >= attempt_limit)
        ):
            scheduler["model_skipped"] = int(scheduler.get("model_skipped") or 0) + 1
            return False
        scheduler["model_attempts"] = int(scheduler.get("model_attempts") or 0) + 1
        return True

    def _schedule_native_proactive(self, *, life_id: str) -> None:
        """Schedule the sole post-P15 proactive producer without blocking heartbeat."""
        scope = self._scope_state(life_id)
        settings = scope.get("settings") if isinstance(scope.get("settings"), Mapping) else {}
        scheduler = scope.setdefault("scheduler", {})
        now_ms = time.time_ns() // 1_000_000
        if not bool(settings.get("proactive_enabled", True)):
            scheduler["last_proactive_reason"] = "life.proactive.disabled"
            return
        if not callable(self._proactive_decider):
            scheduler["last_proactive_reason"] = "life.proactive.decider_unavailable"
            return
        if scheduler.get("proactive_decision_inflight") is True:
            return
        interval_ms = max(60, int(settings.get("proactive_decision_interval_seconds") or 900)) * 1000
        last_ms = int(scheduler.get("last_proactive_decision_at_ms") or 0)
        if last_ms and now_ms - last_ms < interval_ms:
            return
        if not self._reserve_proactive_model_call_locked(scheduler=scheduler, settings=settings):
            scheduler["last_proactive_decision_at_ms"] = now_ms
            scheduler["last_proactive_reason"] = "life.proactive.model_budget_exhausted"
            self._persist(life_id)
            return

        context = self._build_proactive_context(life_id=life_id, now_ms=now_ms)
        scheduler["proactive_decision_inflight"] = True
        scheduler["last_proactive_decision_at_ms"] = now_ms
        scheduler["last_proactive_reason"] = "life.proactive.decision_started"
        self._persist(life_id)
        slot = now_ms // max(60_000, interval_ms)
        threading.Thread(
            target=self._proactive_worker,
            args=(life_id, context, slot),
            daemon=True,
            name="life-native-proactive",
        ).start()

    def _proactive_worker(self, life_id: str, context: Mapping[str, Any], slot: int) -> None:
        """Run one P16 decision/compose turn; each actual model call is budgeted."""
        try:
            value = self._proactive_decider(deepcopy(dict(context)))
            if not isinstance(value, Mapping):
                raise ValueError("proactive model decision is invalid")
            proposal = dict(value)
        except Exception as exc:
            with self._lock:
                scope = self._scope_state(life_id)
                scheduler = scope.setdefault("scheduler", {})
                scheduler["proactive_decision_inflight"] = False
                scheduler["last_proactive_reason"] = "life.proactive.decision_failed"
                scheduler["model_failures"] = int(scheduler.get("model_failures") or 0) + 1
                self.system.journal.append(
                    life_id,
                    "life.proactive.suppressed",
                    {"reason_code": "life.proactive.decision_failed", "error_type": type(exc).__name__},
                    actor="life_proactive",
                    idempotency_key=f"life.proactive.decision-failed:{life_id}:{slot}",
                )
                self._persist(life_id)
            return

        with self._lock:
            scope = self._scope_state(life_id)
            settings = deepcopy(scope.get("settings") or {})
            scheduler = scope.setdefault("scheduler", {})
            self._reset_proactive_model_budget_if_needed(scheduler)
            scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1
            now_ms = time.time_ns() // 1_000_000
            decision = evaluate_proactive_candidate(
                proposal,
                context=context,
                settings=settings,
                now_ms=now_ms,
            )
            scheduler["last_proactive_reason"] = str(decision.get("reason_code") or "")
            if decision.get("allowed") is not True:
                scheduler["proactive_decision_inflight"] = False
                self.system.journal.append(
                    life_id,
                    "life.proactive.suppressed",
                    {"decision": decision, "context_observed_at_ms": int(context.get("observed_at_ms") or 0)},
                    actor="life_proactive",
                    idempotency_key=f"life.proactive.suppressed:{life_id}:{slot}",
                )
                self._persist(life_id)
                return
            if str(settings.get("proactive_mode") or "shadow").casefold() != "live":
                scheduler["proactive_decision_inflight"] = False
                self.system.journal.append(
                    life_id,
                    "life.proactive.decision",
                    {"decision": decision, "delivery": "shadow"},
                    actor="life_proactive",
                    idempotency_key=f"life.proactive.shadow:{life_id}:{slot}",
                )
                self._persist(life_id)
                return

            writer = self._proactive_expression_writer
            if not callable(writer):
                scheduler["proactive_decision_inflight"] = False
                scheduler["last_proactive_reason"] = "life.proactive.expression_unavailable"
                self.system.journal.append(
                    life_id,
                    "life.proactive.suppressed",
                    {"reason_code": "life.proactive.expression_unavailable", "decision": decision},
                    actor="life_proactive",
                    idempotency_key=f"life.proactive.compose-unavailable:{life_id}:{slot}",
                )
                self._persist(life_id)
                return
            if not self._reserve_proactive_model_call_locked(scheduler=scheduler, settings=settings):
                scheduler["proactive_decision_inflight"] = False
                scheduler["last_proactive_reason"] = "life.proactive.expression_budget_exhausted"
                self.system.journal.append(
                    life_id,
                    "life.proactive.suppressed",
                    {"reason_code": "life.proactive.expression_budget_exhausted", "decision": decision},
                    actor="life_proactive",
                    idempotency_key=f"life.proactive.compose-budget:{life_id}:{slot}",
                )
                self._persist(life_id)
                return
            self._persist(life_id)

        try:
            expression_result: object = writer({
                "schema": "tiangong.life.proactive-expression-material.v1",
                "life_id": life_id,
                "decision": deepcopy(decision),
                "initiative_context": deepcopy(dict(context)),
            })
        except Exception:
            expression_result = None

        if isinstance(expression_result, Mapping):
            text_value = str(expression_result.get("text") or expression_result.get("summary") or "").strip()
            conversation_id = str(expression_result.get("conversation_id") or "")[:240]
        else:
            text_value = str(expression_result or "").strip()
            conversation_id = ""
        text_value = text_value[:4000]

        with self._lock:
            scope = self._scope_state(life_id)
            scheduler = scope.setdefault("scheduler", {})
            scheduler["proactive_decision_inflight"] = False
            if not text_value:
                scheduler["last_proactive_reason"] = "life.proactive.expression_unavailable"
                scheduler["model_failures"] = int(scheduler.get("model_failures") or 0) + 1
                self.system.journal.append(
                    life_id,
                    "life.proactive.suppressed",
                    {"reason_code": "life.proactive.expression_unavailable", "decision": decision},
                    actor="life_proactive",
                    idempotency_key=f"life.proactive.compose-failed:{life_id}:{slot}",
                )
                self._persist(life_id)
                return
            scheduler["model_successes"] = int(scheduler.get("model_successes") or 0) + 1
            privacy = scope.get("settings", {}).get("privacy") if isinstance(scope.get("settings"), Mapping) else {}
            if isinstance(privacy, Mapping) and bool(privacy.get("redact_share", True)):
                text_value = self._redact_sensitive_text(text_value)
            initiative_id = "initiative_" + canonical_sha256({
                "domain": "tiangong.life.proactive-initiative.v1",
                "life_id": life_id,
                "slot": int(slot),
                "candidate_kind": decision.get("candidate_kind"),
                "evidence_refs": decision.get("evidence_refs") or [],
            })[:40]
            message_id = "proactive_" + canonical_sha256({"initiative_id": initiative_id})[:40]
            if any(
                isinstance(row, Mapping) and row.get("initiative_id") == initiative_id
                for row in scope.get("proactive_chats", [])
            ):
                scheduler["last_proactive_reason"] = "life.proactive.duplicate"
                self._persist(life_id)
                return
            created_at_ms = time.time_ns() // 1_000_000
            row = {
                "message_id": message_id,
                "initiative_id": initiative_id,
                "text": text_value,
                "created_at": utc_now(),
                "created_at_ms": created_at_ms,
                "reason": "life.proactive.native",
                "candidate_kind": str(decision.get("candidate_kind") or "respond"),
                "trigger_event_refs": list(decision.get("evidence_refs") or [])[:24],
                "conversation_id": conversation_id,
                "acked": False,
                "replied": False,
            }
            scope["proactive_chats"].append(row)
            del scope["proactive_chats"][:-100]
            scheduler["last_proactive_delivery_at_ms"] = created_at_ms
            scheduler["last_proactive_reason"] = "life.proactive.delivered"
            self.system.journal.append(
                life_id,
                "life.proactive.delivered",
                {"message_id": message_id, "initiative_id": initiative_id, "decision": decision},
                actor="life_proactive",
                idempotency_key=f"life.proactive.delivered:{initiative_id}",
            )
            self._persist(life_id)

    def _mark_latest_proactive_replied(
        self,
        *,
        life_id: str,
        user_activity_at_ms: int,
        run_id: str,
    ) -> bool:
        """Link a real later user turn to the latest delivered initiative."""
        scope = self._scope_state(life_id)
        for row in reversed(scope.get("proactive_chats", [])):
            if not isinstance(row, dict):
                continue
            if str(row.get("reason") or "") != "life.proactive.native" or row.get("replied") is True:
                continue
            created_at_ms = int(row.get("created_at_ms") or 0)
            if not created_at_ms or created_at_ms > int(user_activity_at_ms):
                continue
            row["replied"] = True
            row["replied_at_ms"] = int(user_activity_at_ms)
            row["reply_run_id"] = str(run_id or "")[:160]
            initiative_id = str(row.get("initiative_id") or "")
            self.system.journal.append(
                life_id,
                "life.proactive.replied",
                {
                    "initiative_id": initiative_id,
                    "message_id": str(row.get("message_id") or ""),
                    "reply_run_id": row["reply_run_id"],
                },
                actor="user",
                idempotency_key=f"life.proactive.replied:{initiative_id}",
            )
            return True
        return False

    def _schedule_greeting(self, *, life_id: str) -> None:
        """Legacy random-greeting producer is frozen after the P15 cutover.

        Delivery infrastructure remains available for the future native Life
        initiative path; this producer intentionally performs no queue write,
        model generation, journal publication, or scheduler retry mutation.
        """
        freeze_reason = "life.proactive.legacy_producer_frozen"
        _ = (life_id, freeze_reason)
        return

    _UPGRADE_OPEN_STATUSES = frozenset({"awaiting_user", "confirmed", "executing"})
    _UPGRADE_PATH_SUFFIXES = frozenset(
        {".py", ".mjs", ".cjs", ".js", ".html", ".css", ".json", ".md", ".yaml", ".yml"}
    )
    _UPGRADE_PATH_FORBIDDEN_PARTS = frozenset(
        {"__pycache__", ".git", "_internal", "node_modules", "site-packages"}
    )

    def _normalize_upgrade_changes(self, value: Any) -> list[dict[str, Any]]:
        """Bound and validate self-code patch operations from an untrusted model."""
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise EmbeddedLifeError("life.upgrade.changes_invalid")
        if len(value) > 12:
            raise EmbeddedLifeError("life.upgrade.changes_too_many")
        changes: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise EmbeddedLifeError("life.upgrade.change_invalid")
            target = str(item.get("target") or "").strip().replace("\\", "/")
            find = item.get("find")
            replace = item.get("replace")
            if not target or target.startswith(("/", "~")) or ":" in target or ".." in target.split("/"):
                raise EmbeddedLifeError("life.upgrade.change_target_invalid")
            parts = [part for part in target.split("/") if part]
            if not parts or any(part.casefold() in self._UPGRADE_PATH_FORBIDDEN_PARTS for part in parts):
                raise EmbeddedLifeError("life.upgrade.change_target_forbidden")
            suffix = "." + parts[-1].rsplit(".", 1)[-1] if "." in parts[-1] else ""
            if suffix.casefold() not in self._UPGRADE_PATH_SUFFIXES:
                raise EmbeddedLifeError("life.upgrade.change_target_suffix")
            if not isinstance(find, str) or not find.strip() or len(find) > 8192:
                raise EmbeddedLifeError("life.upgrade.change_find_invalid")
            if not isinstance(replace, str) or len(replace) > 16384:
                raise EmbeddedLifeError("life.upgrade.change_replace_invalid")
            count = item.get("count", 1)
            if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 16:
                raise EmbeddedLifeError("life.upgrade.change_count_invalid")
            changes.append({"target": "/".join(parts), "find": find, "replace": replace, "count": count})
        return changes

    def _upgrade_draft(self, life_id: str, decision: Mapping[str, Any], *, source: str) -> dict[str, Any]:
        """Validate one model self-iteration proposal into a user-gated card."""
        title = re.sub(r"\s+", " ", str(decision.get("title") or "")).strip()[:120]
        if not title:
            raise EmbeddedLifeError("life.upgrade.title_required")
        summary = re.sub(r"\s+", " ", str(decision.get("summary") or "")).strip()[:600]
        risk = str(decision.get("risk_level") or "A3").strip().upper()
        if risk not in {"A0", "A1", "A2", "A3", "A4", "A5"}:
            risk = "A3"
        goals_raw = decision.get("goals") if isinstance(decision.get("goals"), list) else []
        goals = [re.sub(r"\s+", " ", str(goal)).strip()[:200] for goal in goals_raw][:8]
        goals = [goal for goal in goals if goal]
        changes = self._normalize_upgrade_changes(decision.get("changes"))
        scope = self._scope_state(life_id)
        upgrades = scope.setdefault("upgrades", {})
        title_key = title.casefold()
        for existing in upgrades.values():
            if (
                isinstance(existing, Mapping)
                and str(existing.get("status") or "") in self._UPGRADE_OPEN_STATUSES
                and str(existing.get("title") or "").casefold() == title_key
            ):
                return {"ok": True, "duplicate": True, "upgrade": deepcopy(dict(existing))}
        now = utc_now()
        card_id = "upg_" + canonical_sha256(
            {"domain": "tiangong.life.upgrade-card.v1", "life_id": life_id, "title": title, "created_at": now}
        )[:24]
        card = {
            "card_id": card_id,
            "id": card_id,
            "kind": "self_code_iteration",
            "title": title,
            "summary": summary,
            "goals": goals,
            "changes": changes,
            "risk_level": risk,
            # 触碰核心代码的提案强制按核心审查对待，治理口径与 capability_learning 一致。
            "review_level": "CORE_REVIEW" if risk == "A5" else "HUMAN_REVIEW",
            "status": "awaiting_user",
            "source": source,
            "created_at": now,
            "updated_at": now,
            "error": "",
            "execution": {},
        }
        upgrades[card_id] = card
        self.system.journal.append(
            life_id, "upgrade.card_created", {"upgrade": deepcopy(card)},
            actor="life_self_iteration", idempotency_key=f"upgrade.draft:{card_id}",
        )
        self._persist(life_id)
        return {"ok": True, "upgrade": deepcopy(card)}

    def _upgrade_action(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Drive the user-gated upgrade card state machine.

        confirm: awaiting_user -> confirmed, then (when the card carries
        patch changes and an executor is installed) executing -> completed or
        failed.  cancel: any open card -> cancelled.  complete records the
        execution evidence reported by the gateway executor bridge.
        """
        action = path.rsplit("/", 1)[-1]
        if action not in {"confirm", "cancel", "complete"}:
            raise EmbeddedLifeError("life.upgrade.action_invalid", status=404)
        card_id = str(body.get("upgrade_id") or body.get("card_id") or "").strip()
        if not card_id:
            raise EmbeddedLifeError("life.upgrade.card_id_required")
        scope = self._scope_state()
        upgrades = scope.setdefault("upgrades", {})
        card = upgrades.get(card_id)
        if not isinstance(card, dict):
            raise EmbeddedLifeError("life.upgrade.not_found", status=404)
        status = str(card.get("status") or "")
        now = utc_now()
        if action == "cancel":
            if status not in self._UPGRADE_OPEN_STATUSES:
                raise EmbeddedLifeError("life.upgrade.not_cancellable", status=409)
            # 用户取消即删除这个计划本身；审计轨迹留在签名日志里。
            removed = upgrades.pop(card_id)
            removed["status"] = "cancelled"
            removed["updated_at"] = now
            self.system.journal.append(
                str(self._active()["life_id"]), "upgrade.card_cancelled", {"card_id": card_id, "upgrade": deepcopy(removed)},
                actor=str(body.get("actor") or "user"), idempotency_key=f"upgrade.cancel:{card_id}",
            )
            self._persist()
            return {"ok": True, "upgrade": deepcopy(removed), "deleted": True}
        if action == "complete":
            if status not in {"confirmed", "executing", "failed"}:
                raise EmbeddedLifeError("life.upgrade.not_completable", status=409)
            execution = body.get("execution") if isinstance(body.get("execution"), Mapping) else {}
            succeeded = bool(body.get("success")) if "success" in body else status != "failed"
            card["execution"] = {**dict(card.get("execution") or {}), **dict(execution)}
            card["status"] = "completed" if succeeded else "failed"
            if not succeeded:
                card["error"] = re.sub(r"\s+", " ", str(body.get("error") or card.get("error") or "")).strip()[:240]
            card["updated_at"] = now
            self.system.journal.append(
                str(self._active()["life_id"]), f"upgrade.card_{card['status']}", {"card_id": card_id, "execution": deepcopy(card["execution"])},
                actor=str(body.get("actor") or "execution_bridge"), idempotency_key=f"upgrade.complete:{card_id}:{card['status']}:{now}",
            )
            self._persist()
            return {"ok": True, "upgrade": deepcopy(card)}
        # confirm
        if status != "awaiting_user":
            raise EmbeddedLifeError("life.upgrade.not_confirmable", status=409)
        card["status"] = "confirmed"
        card["confirmed_at"] = now
        card["updated_at"] = now
        self.system.journal.append(
            str(self._active()["life_id"]), "upgrade.card_confirmed", {"card_id": card_id},
            actor=str(body.get("actor") or "user"), idempotency_key=f"upgrade.confirm:{card_id}",
        )
        changes = card.get("changes") if isinstance(card.get("changes"), list) else []
        executor = getattr(self, "_upgrade_executor", None)
        if changes and callable(executor):
            card["status"] = "executing"
            card["updated_at"] = utc_now()
            self._persist()
            try:
                outcome = executor({"card_id": card_id, "changes": deepcopy(changes)})
            except Exception as exc:
                outcome = {"ok": False, "error": f"{type(exc).__name__}: {re.sub(r' ', ' ', str(exc)).strip()[:200]}"}
            if not isinstance(outcome, Mapping):
                outcome = {"ok": False, "error": "upgrade executor result invalid"}
            succeeded = bool(outcome.get("ok"))
            card["execution"] = dict(outcome.get("results") or outcome)
            card["status"] = "completed" if succeeded else "failed"
            if not succeeded:
                card["error"] = re.sub(r"\s+", " ", str(outcome.get("error") or "")).strip()[:240]
            card["updated_at"] = utc_now()
            self.system.journal.append(
                str(self._active()["life_id"]), f"upgrade.card_{card['status']}", {"card_id": card_id, "execution": deepcopy(card["execution"])},
                actor="execution_bridge", idempotency_key=f"upgrade.execute:{card_id}:{card['status']}",
            )
        self._persist()
        return {"ok": True, "upgrade": deepcopy(card)}

    def _autonomy_health_payload(self) -> dict[str, Any]:
        autonomy = self._autonomy_state()
        tasks = autonomy.get("tasks") if isinstance(autonomy.get("tasks"), Mapping) else {}
        signature = (
            int(autonomy.get("task_sequence") or 0),
            int(autonomy.get("generated_total") or 0),
            int(autonomy.get("completed_total") or 0),
            int(autonomy.get("failed_total") or 0),
            len(tasks),
            bool(autonomy.get("enabled")),
            int(autonomy.get("pending_limit") or 0),
            int(autonomy.get("last_tick_at_ms") or 0),
            canonical_sha256(tasks),
        )
        if signature == self._autonomy_health_sig and self._autonomy_health_cache:
            return dict(self._autonomy_health_cache)
        invalid: list[str] = []
        reason_codes: list[str] = []
        active_count = 0
        active_fingerprints: set[str] = set()
        max_sequence = 0
        completed_count = 0
        failed_count = 0
        for task_id, task in tasks.items():
            if not isinstance(task, Mapping):
                invalid.append(str(task_id))
                continue
            status = str(task.get("status") or "")
            if status not in ALLOWED_TASK_STATES:
                invalid.append(str(task_id))
                continue
            sequence = int(task.get("sequence") or 0)
            max_sequence = max(max_sequence, sequence)
            if status in ACTIVE_TASK_STATES:
                active_count += 1
                fingerprint = str(task.get("fingerprint") or "")
                if fingerprint in active_fingerprints:
                    invalid.append(str(task_id))
                    reason_codes.append("life.autonomy.duplicate_active_fingerprint")
                active_fingerprints.add(fingerprint)
            elif status == "completed":
                completed_count += 1
            elif status == "failed":
                failed_count += 1
            expected = canonical_sha256(
                {key: value for key, value in task.items() if key != "task_sha256"}
            )
            if str(task.get("task_sha256") or "") != expected:
                invalid.append(str(task_id))
        pending_limit = int(autonomy.get("pending_limit"))
        if active_count > pending_limit:
            reason_codes.append("life.autonomy.pending_limit_exceeded")
        if int(autonomy.get("task_sequence") or 0) != max_sequence:
            reason_codes.append("life.autonomy.task_sequence_mismatch")
        if int(autonomy.get("generated_total") or 0) != len(tasks):
            reason_codes.append("life.autonomy.generated_total_mismatch")
        if int(autonomy.get("completed_total") or 0) != completed_count:
            reason_codes.append("life.autonomy.completed_total_mismatch")
        if int(autonomy.get("failed_total") or 0) != failed_count:
            reason_codes.append("life.autonomy.failed_total_mismatch")
        healthy = not invalid and not reason_codes
        result = {
            "healthy": healthy,
            "enabled": bool(autonomy.get("enabled")),
            "task_generation_enabled": bool(autonomy.get("task_generation_enabled")),
            "task_count": len(tasks),
            "active_task_count": active_count,
            "pending_limit": pending_limit,
            "generation_count": int(autonomy.get("generation_count") or 0),
            "generated_total": int(autonomy.get("generated_total") or 0),
            "completed_total": int(autonomy.get("completed_total") or 0),
            "failed_total": int(autonomy.get("failed_total") or 0),
            "invalid_task_ids": sorted(set(invalid)),
            "reason_codes": sorted(set(reason_codes)),
            "last_tick_at_ms": int(autonomy.get("last_tick_at_ms") or 0),
            "last_tick_reason": str(autonomy.get("last_tick_reason") or ""),
        }
        self._autonomy_health_cache = dict(result)
        self._autonomy_health_sig = signature
        return result

    def health_payload(self) -> dict[str, Any]:
        active = self._active()
        scheduler = self.scheduler.status()
        journal = self._journal_verify()
        autonomy = self._autonomy_health_payload()
        life_ready = bool(
            self._lease.active
            and scheduler.get("running")
            and not scheduler.get("last_error_type")
            and journal.get("valid") is True
            and autonomy.get("healthy") is True
            and not self._projection_dirty_reason
        )
        return {
            "ok": True,
            "component_id": LIFE_COMPONENT_ID,
            "build_id": EMBEDDED_LIFE_BUILD_ID,
            "api_contract": LIFE_API_CONTRACT,
            "deployment_mode": self.mode,
            "instance_id": self._lease.instance_id,
            "status": "ALIVE",
            "life_ready": life_ready,
            "life_available": life_ready,
            "writer_lease_active": self._lease.active,
            "writer_mode": self._lease.mode,
            "active_life_id": active.get("life_id"),
            "scheduler": scheduler,
            "autonomy": autonomy,
            "journal": journal,
            "autonomous_runtime": bool(scheduler.get("running")),
            "uptime_ms": max(0, (time.monotonic_ns() - self._started_ns) // 1_000_000),
        }

    def ready_payload(self, *, now_ms: int | None = None) -> tuple[int, dict[str, Any]]:
        del now_ms
        reasons: list[str] = []
        if not self._lease.active:
            reasons.append("life.writer.inactive")
        if not self.scheduler.status().get("running"):
            reasons.append("life.scheduler.inactive")
        if self.scheduler.status().get("last_error_type"):
            reasons.append("life.scheduler.tick_failed")
        if self._projection_dirty_reason:
            reasons.append(self._projection_dirty_reason)
        try:
            active = self._active()
            panel = self._panel()
            store_health = self.authority_store.health_cached()
            journal = self._journal_verify()
            autonomy = self._autonomy_health_payload()
            if not active.get("life_id"):
                reasons.append("life.identity.missing")
            if panel.get("ok") is not True:
                reasons.append("life.panel.unavailable")
            if store_health.get("healthy") is False:
                reasons.append("life.authority_store.unhealthy")
            if journal.get("valid") is not True:
                reasons.append(
                    str(journal.get("reason_code") or "life.journal.invalid")
                )
            if autonomy.get("healthy") is not True:
                reasons.append("life.autonomy.unhealthy")
        except Exception:
            reasons.append("life.probe.failed")
        ready = not reasons
        return (200 if ready else 503), {
            "ok": ready,
            "component_id": LIFE_COMPONENT_ID,
            "build_id": EMBEDDED_LIFE_BUILD_ID,
            "api_contract": LIFE_API_CONTRACT,
            "status": "READY" if ready else "NOT_READY",
            "reason_codes": reasons,
            "writer_lease_active": self._lease.active,
            "deployment_mode": self.mode,
        }

    def _state_payload(self) -> dict[str, Any]:
        active = self._active()
        soul = self._soul()
        health = self.health_payload()
        life_ready = bool(health.get("life_ready"))
        scope = self._scope_state()
        memory = self._memory_stats()
        affect = {"state": deepcopy(scope["affect"]), "available": True}
        temperament = self._temperament_projection()
        scheduler_status = self.scheduler.status()
        heartbeat_effective = bool(
            scheduler_status.get("running")
            and scope["settings"].get("heartbeat_enabled", True)
        )
        autonomy_state = self._autonomy_state()
        task_rows = [
            deepcopy(row)
            for row in (autonomy_state.get("tasks") or {}).values()
            if isinstance(row, Mapping)
        ]
        active_task_rows = [
            row
            for row in task_rows
            if str(row.get("status") or "") in ACTIVE_TASK_STATES
        ]
        execution_rows = [
            deepcopy(row)
            for row in scope.get("executions", {}).values()
            if isinstance(row, Mapping)
        ]
        execution_rows.sort(
            key=lambda row: (
                int(row.get("completed_at_ms") or 0),
                str(row.get("committed_at") or ""),
            ),
            reverse=True,
        )
        completed_execution_states = {
            "COMPLETED", "SUCCEEDED", "SUCCESS", "FINISHED", "DONE",
        }
        failed_execution_states = {
            "FAILED_SAFE", "FAILED", "FAILURE", "BLOCKED", "ERROR",
        }
        completed_execution_rows = [
            row
            for row in execution_rows
            if str(row.get("status") or "").upper() in completed_execution_states
        ]
        failed_execution_rows = [
            row
            for row in execution_rows
            if str(row.get("status") or "").upper() in failed_execution_states
        ]
        latest_execution = deepcopy(execution_rows[0]) if execution_rows else {}
        completed_autonomous_tasks = [
            row for row in task_rows
            if str(row.get("status") or "") == "completed"
            and str(row.get("source") or "") == "life_activity_catalog"
        ]
        completed_autonomous_tasks.sort(
            key=lambda row: (
                int(row.get("updated_at_ms") or 0),
                int(row.get("sequence") or 0),
            ),
            reverse=True,
        )
        free_will = {
            "available": True,
            "enabled": bool(autonomy_state.get("enabled")),
            "heartbeat_running": heartbeat_effective,
            "heartbeat_state": "running" if heartbeat_effective else "stopped",
            "ready_for_action": any(
                isinstance(task, Mapping)
                and str(task.get("status") or "") in ACTIVE_TASK_STATES
                for task in (autonomy_state.get("tasks") or {}).values()
            ),
            "skip_reason": str(autonomy_state.get("last_error_code") or ""),
            "latest_autonomous_action": deepcopy(completed_autonomous_tasks[0])
            if completed_autonomous_tasks else {},
        }
        operational = {
            "available": True,
            "source": "embedded_life_runtime",
            "observed_at": utc_now(),
            "memory_total": int(memory.get("total") or 0),
            "task_total": len(task_rows),
            "active_task_count": len(active_task_rows),
            "completed_task_count": sum(
                1
                for row in task_rows
                if str(row.get("status") or "") == "completed"
            ),
            "execution_total": len(execution_rows),
            "completed_execution_count": len(completed_execution_rows),
            "failed_execution_count": len(failed_execution_rows),
            "latest_execution": latest_execution,
            "scheduler": {
                "running": bool(scheduler_status.get("running")),
                "interval_seconds": int(
                    float(scheduler_status.get("interval_seconds") or 0)
                ),
                "tick_count": int(scheduler_status.get("tick_count") or 0),
                "last_error_type": str(
                    scheduler_status.get("last_error_type") or ""
                ),
                "last_error_code": str(
                    scheduler_status.get("last_error_code") or ""
                ),
            },
        }
        return {
            "ok": True,
            "schema": "tiangong.desktop.ui-projection.v1",
            "api_contract": LIFE_API_CONTRACT,
            "backend_version": "3.0-single-process",
            "life_ready": life_ready,
            "life_available": life_ready,
            "setup_required": False,
            "identity": active,
            "soul": soul,
            "temperament": temperament,
            "affect": affect,
            "memory": memory,
            "projection_authority": self._projection_authority(),
            "ui": {
                "schema": "tiangong.desktop.ui-projection.v1",
                "life_ready": life_ready,
                "life_available": life_ready,
                "degraded": not life_ready,
                "phase": "ready" if life_ready else "not_ready",
                "compatible": True,
                "api_contract": LIFE_API_CONTRACT,
                "lifecycle": {
                    "available": life_ready,
                    "ready": life_ready,
                    "phase": "alive" if life_ready else "not_ready",
                    "status": "ALIVE" if life_ready else "NOT_READY",
                    "active_run_count": max(
                        0,
                        len(execution_rows)
                        - len(completed_execution_rows)
                        - len(failed_execution_rows),
                    ),
                    "completed_run_count": len(completed_execution_rows),
                    "failed_run_count": len(failed_execution_rows),
                    "last_execution_at": str(
                        latest_execution.get("committed_at") or ""
                    ),
                    "metrics_unavailable_reason": "" if life_ready else "life readiness checks failed",
                    "writer_mode": self.mode,
                    "writer_lease_active": self._lease.active,
                },
                "operational": operational,
                "memory": memory,
                "affect": affect,
                "temperament": temperament,
                "free_will": free_will,
                "projection_authority": self._projection_authority(),
            },
            "health": health,
        }

    def _panel(self) -> dict[str, Any]:
        active = self._active()
        health = self.health_payload()
        scope = self._scope_state()
        autonomy = self._autonomy_health_payload()
        autonomy_state = self._autonomy_state()
        tasks = autonomy_state.get("tasks") or {}
        scheduler_status = self.scheduler.status()
        heartbeat_effective = bool(
            scheduler_status.get("running")
            and scope["settings"].get("heartbeat_enabled", True)
        )
        now = utc_now()
        today = now[:10]
        task_rows = [deepcopy(tasks[key]) for key in sorted(tasks)]
        today_task_rows = records_for_day(task_rows, day=today)
        completed_tasks = [
            row for row in today_task_rows
            if str(row.get("status") or "") == "completed"
        ]
        completed_autonomous_tasks = [
            row for row in completed_tasks
            if str(row.get("source") or "") == "life_activity_catalog"
        ]
        completed_autonomous_tasks.sort(
            key=lambda row: (
                int(row.get("updated_at_ms") or 0),
                int(row.get("sequence") or 0),
            ),
            reverse=True,
        )
        latest_autonomous_action = deepcopy(completed_autonomous_tasks[0]) if completed_autonomous_tasks else {}
        execution_rows = [
            deepcopy(row)
            for row in scope.get("executions", {}).values()
            if isinstance(row, Mapping) and record_day(row) == today
        ]
        execution_rows.sort(key=lambda row: str(row.get("committed_at") or row.get("completed_at") or ""), reverse=True)
        active_task_rows = [
            row for row in today_task_rows
            if str(row.get("status") or "") in ACTIVE_TASK_STATES
        ]
        selected_activity_types = {
            str(value)
            for value in scope["settings"].get("autonomy_activity_types") or []
            if str(value)
        }
        today_catalog_tasks = [
            row
            for row in catalog_tasks_for_day(task_rows, day=today)
            if (
                str(row.get("activity_id") or "") in selected_activity_types
                or str(row.get("status") or "") == "completed"
            )
        ]
        completed_today = [
            row for row in today_catalog_tasks
            if str(row.get("status") or "") == "completed"
        ]
        schedule_state = normalize_schedule(
            scope.get("schedule"),
            today=now[:10],
            autonomy_tasks=today_catalog_tasks,
        )
        # Store the normalized shape on the projection state. This is a pure
        # migration of old shape; explicit plan rows remain authoritative.
        scope["schedule"] = schedule_state
        scheduled_rows = list(schedule_state["tasks"].values())
        # Learning cards stay visible while they still need a decision or work,
        # even when they were drafted on an earlier day.  A closed card
        # (published/discarded) leaves the panel immediately; the learning
        # report and capability list remain as the durable evidence.
        learning_open_statuses = {
            "awaiting_user",
            "approved",
            "candidate",
            "pending_card",
            "processing_approved",
            "building",
            "draft_ready",
            "tested",
            "review_ready",
            "sandbox_passed",
            "quarantined",
        }
        learning_rows = [
            deepcopy(row)
            for row in scope.get("learning", {}).values()
            if isinstance(row, Mapping)
            and str(row.get("status") or "").casefold() in learning_open_statuses
        ]
        learning_rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
        learning_pending = [row for row in learning_rows if str(row.get("status") or "") == "awaiting_user"]
        activity_scope = build_activity_scope(life_id=str(active.get("life_id") or ""), soul=self._soul(), scope=scope)
        # Upgrade cards follow the same rule: a card the user has not confirmed
        # or cancelled yet must not vanish at midnight.
        upgrade_closed_actions = {"cancel", "cancelled", "canceled", "complete", "completed", "discard", "discarded", "failed"}
        upgrade_rows = [
            deepcopy(row)
            for row in scope.get("upgrades", {}).values()
            if isinstance(row, Mapping)
            and (
                record_day(row) == today
                or str(row.get("action") or row.get("status") or "").casefold() not in upgrade_closed_actions
            )
        ]
        memory_records = {
            str(memory_id): deepcopy(row)
            for memory_id, row in scope.get("memories", {}).items()
            if (
                isinstance(row, Mapping)
                and str(row.get("status") or "active") != "deleted"
                and record_day(row) == today
            )
        }
        memory_by_status: dict[str, int] = {}
        for row in memory_records.values():
            status = str(row.get("status") or "active")
            memory_by_status[status] = memory_by_status.get(status, 0) + 1
        authoritative_context = (
            self.authority_store.get_latest_causal_context_pack_for_life(
                str(active.get("life_id") or "")
            )
        )
        if authoritative_context is not None:
            context = self._context_panel_projection(authoritative_context)
        else:
            try:
                latest_context = self.system.latest_context()
                context = deepcopy(latest_context.get("envelope") or {})
            except LifeCoreError as exc:
                if exc.status != 404:
                    raise
                context = {
                    "available": False,
                    "reason_code": "context_not_compiled",
                }
        if context.get("available") is True and record_day(context) not in {"", today}:
            context = {
                "available": False,
                "reason_code": "context_not_compiled_today",
            }
        if context.get("available") is not True:
            context = fallback_context_projection(
                activity_scope,
                generated_at=now,
            )
        context.setdefault("available", bool(context))
        panel_settings = {
            **deepcopy(scope["settings"]),
            "autonomy_activity_catalog": autonomy_activity_catalog(),
            "available": True,
            "editable": True,
            "readonly": False,
            "source": "embedded_life_runtime",
        }
        declared_boundaries = [
            str(value)
            for value in self._soul().get("boundaries", [])
            if str(value).strip()
        ]
        sections = {
            name: {
                "available": True,
                "partial": False,
                "source": "embedded_life_runtime",
                "reason_code": "",
            }
            for name in (
                "overview", "organism", "memory", "context", "schedule", "will",
                "reflection", "capabilities", "iteration", "boundaries", "settings",
            )
        }
        if context.get("verified") is not True:
            sections["context"].update(
                partial=True,
                reason_code=str(
                    context.get("reason_code")
                    or "compiled_context_not_yet_available"
                ),
            )
        if not callable(getattr(self, "_autonomy_decider", None)):
            sections["will"].update(
                partial=True,
                reason_code="autonomous_judgment_projection_unavailable",
            )
        heartbeat_seconds = max(0, int(autonomy.get("heartbeat_interval_seconds") or 0))
        recent_execution = deepcopy(execution_rows[0]) if execution_rows else {}
        preferences = preference_projection(sorted(selected_activity_types))
        reflection_rows = [
            row for row in (
                reflection_projection(task)
                for task in completed_autonomous_tasks[:20]
            )
            if row
        ]
        action_value_rows = [
            action_value_projection(task)
            for task in completed_autonomous_tasks[:20]
        ]
        recent_autonomous_actions: list[dict[str, Any]] = []
        for task in completed_autonomous_tasks[:2]:
            enriched = deepcopy(task)
            reflection = reflection_projection(task)
            value = action_value_projection(task)
            if reflection:
                enriched["human_summary"] = reflection.get("human_summary")
                enriched["reflection"] = reflection.get("human_summary")
            enriched["value_score"] = value.get("total_score")
            recent_autonomous_actions.append(enriched)
        latest_autonomous_action = (
            deepcopy(recent_autonomous_actions[0])
            if recent_autonomous_actions
            else {}
        )
        drift_rows = motivation_drift_projection(
            completed_autonomous_tasks,
            preferences,
        )
        affect_state = deepcopy(scope["affect"])
        affect_state.setdefault(
            "drives",
            deepcopy(preferences.get("drive_weights") or {}),
        )
        memory_projection = self._memory_stats(memory_records)
        memory_projection["by_status"] = memory_by_status
        memory_projection["records"] = {
            memory_id: self._memory_panel_record(memory_id, row)
            for memory_id, row in memory_records.items()
        }
        return {
            "ok": True,
            "api_contract": LIFE_API_CONTRACT,
            "generated_at": now,
            "projection_status": "authoritative",
            "sections": sections,
            "life_id": active.get("life_id"),
            "identity": active,
            # Identity view renders this list as the set of selectable lives.
            # Keep it in the authoritative panel projection so it is refreshed
            # together with the active identity instead of requiring a second,
            # independently timed client request.
            "identities": self.system.identities.list(),
            "identity_audit": self.system.identities.audit_entries(),
            # This is the authoritative chat gate consumed by the renderer
            # immediately after a life create/bind/activate action.  It avoids
            # keeping a stale client-side readiness result after the active
            # identity has changed.
            "chat_gate": {
                "schema": "tiangong.life.chat-gate.v1",
                "authority": "embedded_life_runtime",
                "observed_at": now,
                "ready": bool(health.get("life_ready")),
                "available": bool(health.get("life_available")),
                "degraded": not bool(health.get("life_ready")),
                "phase": "ready" if health.get("life_ready") else "degraded",
                "life_phase": "alive" if health.get("life_ready") else "not_ready",
                "reason_code": "" if health.get("life_ready") else "life_not_ready",
            },
            "soul": self._soul(),
            "temperament": self._temperament_projection(),
            "summary": {
                "today_status": "alive" if health.get("life_ready") else "not_ready",
                "completed_tasks_today": len(completed_today),
                "next_heavy_tick_seconds": heartbeat_seconds,
                "next_heavy_tick_minutes": (heartbeat_seconds + 59) // 60 if heartbeat_seconds else 0,
                "current_focus": "autonomy" if scheduled_rows else "idle",
                "recent_action": recent_execution,
                "recent_autonomous_action": latest_autonomous_action,
            },
            "state": {
                "status": "ALIVE" if health.get("life_ready") else "NOT_READY",
                "last_heavy_reason": str(autonomy_state.get("last_tick_reason") or "scheduled"),
                "last_heavy_tick_at": str(scheduler_status.get("last_heartbeat_at") or ""),
                "last_execution_at": str(recent_execution.get("committed_at") or ""),
                "updated_at": now,
            },
            "settings": panel_settings,
            "memory": memory_projection,
            "affect": {"state": affect_state, "available": True},
            "relationship": relationship_projection(scope.get("relationships"), memory_records, updated_at=now),
            "body": normalize_body(scope.get("body"), updated_at=now),
            "context": context,
            "schedule": {"available": True, **deepcopy(schedule_state), "tasks": scheduled_rows},
            "inbox": {
                "available": True,
                "items": [
                    deepcopy(row)
                    for row in scope["inbox"]
                    if isinstance(row, Mapping) and record_day(row) == today
                ],
                "unread_count": sum(
                    1
                    for row in scope["inbox"]
                    if (
                        isinstance(row, Mapping)
                        and record_day(row) == today
                        and not row.get("read")
                    )
                ),
            },
            "budget": model_budget_projection(
                scope["settings"],
                scope.get("scheduler") or {},
                day=now[:10],
            ),
            # Task proposals are the real autonomous queue.  Terminal runtime
            # executions remain separately visible as summary.recent_action.
            "tasks": today_task_rows,
            "goals": long_term_goals(),
            "free_will": {
                "enabled": bool(autonomy_state.get("enabled")),
                "heartbeat_running": heartbeat_effective,
                "heartbeat_state": "running" if heartbeat_effective else "stopped",
                "current_mode": "scheduled_autonomy" if autonomy_state.get("enabled") else "disabled",
                # Execution terminals do not currently carry a trustworthy
                # autonomous/user origin marker.  Keep them in
                # summary.recent_action instead of mislabelling them here.
                "latest_autonomous_action": latest_autonomous_action,
                "recent_autonomous_actions": recent_autonomous_actions,
                "latest_action_source": "life_activity_catalog"
                if latest_autonomous_action else "",
                "ready_for_action": bool(scheduled_rows),
                "skip_reason": str(
                    scope.get("scheduler", {}).get("last_autonomy_decision_error")
                    or autonomy_state.get("last_error_code")
                    or ""
                ),
            },
            "autonomy": {
                "scheduler_enabled": heartbeat_effective,
                "risk_max": scope["settings"].get("autonomous_risk_max", "A4"),
                "activity_catalog": autonomy_activity_catalog(),
                "selected_activity_types": deepcopy(
                    scope["settings"].get("autonomy_activity_types") or []
                ),
                **autonomy,
                "tasks": today_task_rows,
            },
            "writer": {
                "mode": self.mode,
                "instance_id": self._lease.instance_id,
                "active": self._lease.active,
            },
            "scheduler": {
                **deepcopy(scope.get("scheduler") or {}),
                **scheduler_status,
            },
            "preferences": {
                "current_focus": "autonomy" if scheduled_rows else "idle",
                **preferences,
            },
            "drift": drift_rows,
            "reflections": reflection_rows,
            "action_values": action_value_rows,
            "learning": {
                "candidate_count": len(learning_pending),
                "latest": learning_rows,
                "activity_scope": activity_scope,
                "policy": {"knowledge_auto_max": "A2", "skill_tool_min": "A3", "a3_a5_preview_confirmation": True, "user_direct_bypasses_card": True},
            },
            "capabilities": _capability_panel_projection(
                scope,
                today=today,
            ),
            "upgrade_cards": upgrade_rows,
            "boundaries": boundary_projection(
                scope["settings"],
                declared_boundaries,
            ),
            "evolution": {"available": True, "upgrades": upgrade_rows},
            "projection_authority": self._projection_authority(),
            "degraded": not bool(health.get("life_ready")),
        }

    def _memory_stats(
        self,
        records: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        memories = (
            records
            if records is not None
            else self._scope_state().get("memories") or {}
        )
        active = [row for row in memories.values() if isinstance(row, dict) and row.get("status") != "deleted"]
        by_type: dict[str, int] = {}
        by_classified_type: dict[str, int] = {}
        by_causal_role: dict[str, int] = {}
        by_assertion_kind: dict[str, int] = {}
        by_lifecycle_state: dict[str, int] = {}
        for row in active:
            kind = str(row.get("memory_type") or "semantic")
            by_type[kind] = by_type.get(kind, 0) + 1
            classification = row.get("classification") if isinstance(row.get("classification"), Mapping) else {}
            classified_type = str(classification.get("memory_type") or kind)
            causal_role = str(classification.get("causal_role") or "context")
            assertion_kind = str(classification.get("assertion_kind") or "observation")
            lifecycle = normalize_lifecycle(row)
            lifecycle_state = str(lifecycle.get("state") or "active")
            by_classified_type[classified_type] = by_classified_type.get(classified_type, 0) + 1
            by_causal_role[causal_role] = by_causal_role.get(causal_role, 0) + 1
            by_assertion_kind[assertion_kind] = by_assertion_kind.get(assertion_kind, 0) + 1
            by_lifecycle_state[lifecycle_state] = by_lifecycle_state.get(lifecycle_state, 0) + 1
        return {
            "available": True,
            "total": len(active),
            "deleted": 0 if records is not None else len(memories) - len(active),
            "by_type": by_type,
            "by_classified_type": by_classified_type,
            "by_causal_role": by_causal_role,
            "by_assertion_kind": by_assertion_kind,
            "by_lifecycle_state": by_lifecycle_state,
            "revision": int(self._scope_state().get("revision") or 1),
            "index_status": "derived",
        }

    @staticmethod
    def _memory_panel_record(memory_id: str, row: Mapping[str, Any]) -> dict[str, Any]:
        content = row.get("content")
        preview = ""
        if isinstance(content, str):
            preview = content
        elif isinstance(content, Mapping):
            conversation = content.get("conversation")
            if isinstance(conversation, Mapping):
                user = str(conversation.get("user") or "").strip()
                assistant = str(conversation.get("assistant") or "").strip()
                preview = " / ".join(value for value in (user, assistant) if value)
            if not preview:
                for key in ("summary", "text", "message", "title", "objective"):
                    value = str(content.get(key) or "").strip()
                    if value:
                        preview = value
                        break
        preview = re.sub(r"\s+", " ", _MEMORY_SECRET.sub("[redacted]", preview)).strip()
        lifecycle = normalize_lifecycle(row)
        result = {
            "memory_id": memory_id,
            "memory_type": str(row.get("memory_type") or "semantic"),
            "status": str(row.get("status") or "active"),
            "content_preview": preview[:320],
            "priority": row.get("priority"),
            "evidence_class": str(
                row.get("evidence_class")
                or row.get("epistemic_status")
                or ""
            ),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "lifecycle_state": str(lifecycle.get("state") or "active"),
        }
        confidence = row.get("confidence")
        if confidence is None and row.get("confidence_milli") is not None:
            try:
                confidence = float(row["confidence_milli"]) / 1000.0
            except (TypeError, ValueError):
                confidence = None
        if confidence is not None:
            result["confidence"] = confidence
        provenance = row.get("provenance")
        if isinstance(provenance, Mapping):
            result["provenance"] = deepcopy(dict(provenance))
        return result

    @staticmethod
    def _context_panel_projection(pack: Any) -> dict[str, Any]:
        payload = pack.model_dump(mode="json")
        items = [
            item for item in payload.get("items", [])
            if isinstance(item, Mapping)
        ]
        included = {
            "memory_cards": sum(
                1 for item in items if item.get("item_kind") == "memory"
            ),
            "constraints": sum(
                1 for item in items if item.get("item_kind") == "constraint"
            ),
            "goals": sum(
                1 for item in items if item.get("item_kind") == "goal"
            ),
            "outcomes": sum(
                1 for item in items if item.get("item_kind") == "outcome"
            ),
            "active_skills": 0,
            "released_tools": 0,
        }
        token_budget = payload.get("token_budget")
        budget = token_budget if isinstance(token_budget, Mapping) else {}
        omitted = max(0, int(payload.get("omitted_item_count") or 0))
        continuity = payload.get("continuity")
        continuity_row = continuity if isinstance(continuity, Mapping) else {}
        created_at_ms = max(0, int(payload.get("created_at_ms") or 0))
        created_at = (
            time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(created_at_ms / 1000))
            + "."
            + f"{created_at_ms % 1000:03d}Z"
            if created_at_ms
            else ""
        )
        return {
            "available": True,
            "verified": payload.get("integrity_status") == "VERIFIED",
            "current": True,
            "source": "life_authority_store",
            "context_hash": str(payload.get("pack_sha256") or ""),
            "cycle_id": str(continuity_row.get("capsule_id") or ""),
            "request_id": str(continuity_row.get("request_id") or ""),
            "run_id": str(continuity_row.get("run_id") or ""),
            "generation": int(continuity_row.get("generation") or 0),
            "estimated_tokens": int(payload.get("selected_token_count") or 0),
            "selected_context_tokens": int(payload.get("selected_token_count") or 0),
            "token_budget": int(budget.get("usable_budget_tokens") or 0),
            "current_context_tokens": int(budget.get("current_context_tokens") or 0),
            "context_utilization_milli": int(budget.get("utilization_milli") or 0),
            "watermark": str(budget.get("watermark") or ""),
            "included": included,
            "compile_reasons": ["gateway_atomic_context_authority"],
            "omitted_blocks": (
                [{"kind": "budget_omitted_items", "count": omitted}]
                if omitted
                else []
            ),
            "evidence_classes": sorted({
                str(item.get("epistemic_status") or "")
                for item in items
                if str(item.get("epistemic_status") or "")
            }),
            "created_at": created_at,
            "storage": {
                "authority": "life_shadow_store",
                "algorithm": "encrypted_sqlite_payload",
            },
        }

    def _journal_verify(self) -> dict[str, Any]:
        life_id = str(self._active()["life_id"])
        signature: tuple[Any, ...] = ()
        try:
            head = self.system.journal.read_verified_head(life_id)
        except Exception:
            head = None
        if isinstance(head, dict):
            signature = (
                int(head.get("event_count") or 0),
                str(head.get("head_event_sha256") or ""),
                str(head.get("journal_sha256") or ""),
            )
        if signature and signature == self._journal_verify_sig and self._journal_verify_cache:
            return dict(self._journal_verify_cache)
        result = self.system.journal.verify(life_id)
        if isinstance(result, dict) and signature:
            self._journal_verify_cache = dict(result)
            self._journal_verify_sig = signature
        return result

    # ------------------------------------------------------------------
    # Contract-store memory convergence (D-11).
    #
    # The journal remains the write-ahead authority for the scope
    # projection; every live user-fact mutation is additionally committed
    # to the contract memory store (memory_assertions + protected payload)
    # in the same request.  Each contract revision, status change and
    # privacy tombstone carries a globally monotonic ``memory_change_seq``
    # plus a transactional outbox row inside the store transaction itself,
    # so the contract store is one authoritative write face for user facts.
    # ------------------------------------------------------------------

    @staticmethod
    def _contract_memory_id(memory_id: str) -> str:
        """Map a live memory id onto the contract ``mem_[0-9a-f]{64}`` space."""

        if _CONTRACT_MEMORY_ID.fullmatch(memory_id):
            return memory_id
        return "mem_" + canonical_sha256(
            {
                "domain": "tiangong.life.memory-id-map.v1",
                "memory_id": memory_id,
            }
        )

    @staticmethod
    def _iso_ms(value: Any) -> int:
        text = str(value or "").strip()
        if not text:
            return 0
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return 0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int(parsed.timestamp() * 1000))

    @staticmethod
    def _ms_iso(value_ms: int) -> str:
        return (
            datetime.fromtimestamp(max(0, value_ms) / 1000, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _memory_contract_plaintext(record: Mapping[str, Any]) -> bytes:
        """Canonical protected payload for one live memory record.

        Only immutable semantic fields participate; projection-evolved
        fields (status, revision, lifecycle, timestamps) never do, so a
        later status revision reuses the same protected payload.
        """

        body = {
            key: record.get(key)
            for key in _MEMORY_CONTRACT_PLAINTEXT_KEYS
            if key in record
        }
        return canonical_json_bytes(
            {"schema": "tiangong.life.live-memory-record.v1", "record": body}
        )

    @staticmethod
    def _contract_source_events(
        journal_event: Mapping[str, Any] | None,
        *,
        memory_id: str,
    ) -> tuple[str, ...]:
        if journal_event is not None:
            event_sha256 = str(journal_event.get("event_sha256") or "")
            if re.fullmatch(r"[0-9a-f]{64}", event_sha256):
                return ("lev_" + event_sha256,)
        return (
            "lev_"
            + canonical_sha256(
                {
                    "domain": "tiangong.life.memory-source-anchor.v1",
                    "memory_id": memory_id,
                }
            ),
        )

    def _contract_store(self) -> LifeShadowStore:
        store = self.authority_store
        if store is None:
            raise EmbeddedLifeError("life.memory.authority_unavailable", status=503)
        return store

    def _memory_coordinator(self) -> MemoryCoordinator:
        return MemoryCoordinator(self._contract_store())

    def _contract_store_assert(
        self,
        life_id: str,
        record: Mapping[str, Any],
        *,
        lifecycle_status: str = "active",
        journal_event: Mapping[str, Any] | None = None,
        updated_at: str = "",
    ) -> tuple[str, int]:
        """Commit one live user fact to the contract memory store.

        Idempotent: the same memory with the same plaintext and status
        returns its original ``memory_change_seq`` without a new revision.
        """

        store = self._contract_store()
        live_memory_id = str(record.get("memory_id") or "")
        contract_id = self._contract_memory_id(live_memory_id)
        classification = (
            record.get("classification")
            if isinstance(record.get("classification"), Mapping)
            else {}
        )
        assertion_kind = str(classification.get("assertion_kind") or "observation")
        if assertion_kind not in _CONTRACT_ASSERTION_KINDS:
            assertion_kind = "observation"
        retention_class = str(classification.get("retention_class") or "ACTIVE_WORKING")
        if retention_class not in _CONTRACT_RETENTION_CLASSES:
            retention_class = "ACTIVE_WORKING"
        epistemic_status = str(record.get("epistemic_status") or "user_asserted")
        if epistemic_status not in _CONTRACT_EPISTEMIC_STATUSES:
            epistemic_status = "user_asserted"
        try:
            priority = int(record.get("priority") or 0)
            confidence = int(record.get("confidence_milli") or 0)
        except (TypeError, ValueError):
            priority = 0
            confidence = 0
        created_ms = self._iso_ms(record.get("created_at"))
        updated_ms = self._iso_ms(updated_at or record.get("updated_at")) or created_ms
        _assertion, change_seq, _created = (
            self._memory_coordinator().commit_contract_assertion(
            plaintext=self._memory_contract_plaintext(record),
            memory_id=contract_id,
            life_id=life_id,
            principal_ref=life_id,
            assertion_kind=assertion_kind,
            epistemic_status=epistemic_status,
            lifecycle_status=lifecycle_status,
            privacy_scope="private",
            retention_class=retention_class,
            source_event_ids=self._contract_source_events(
                journal_event, memory_id=contract_id
            ),
            causal_utility_milli=500 if classification.get("causal") else 0,
            user_importance_milli=max(0, min(1000, priority)),
            verification_strength_milli=max(0, min(1000, confidence)),
            future_dependency_milli=(
                500 if assertion_kind in {"goal", "hard_constraint"} else 0
            ),
            valid_from_ms=created_ms or updated_ms,
            created_at_ms=updated_ms,
            )
        )
        return contract_id, change_seq

    def _contract_store_delete(
        self,
        life_id: str,
        memory_id: str,
        *,
        updated_at: str,
        journal_event: Mapping[str, Any] | None = None,
        record: Mapping[str, Any] | None = None,
    ) -> tuple[str, int, str]:
        """Tombstone one memory in the contract store; returns id, seq, tombstone."""

        store = self._contract_store()
        contract_id = self._contract_memory_id(memory_id)
        latest = store.get_latest_memory_assertion(contract_id)
        if latest is None:
            if record is None:
                raise EmbeddedLifeError("life.memory.not_found", status=404)
            # Roll the assertion forward first so the deletion has a
            # revision chain to tombstone (legacy pre-convergence data).
            self._contract_store_assert(
                life_id, record, journal_event=journal_event
            )
            latest = store.get_latest_memory_assertion(contract_id)
            assert latest is not None
        if latest.lifecycle_status == "deleted":
            seq = store.memory_change_seq_for(contract_id, latest.revision) or 0
            return contract_id, seq, str(latest.deletion_tombstone_id or "")
        deleted_ms = self._iso_ms(updated_at) or (time.time_ns() // 1_000_000)
        result = store.delete_memory(
            contract_id,
            expected_revision=latest.revision,
            deleted_at_ms=deleted_ms,
        )
        seq = (
            store.memory_change_seq_for(contract_id, result.deleted_assertion.revision)
            or 0
        )
        return contract_id, seq, result.tombstone.tombstone_id

    # Fields the projection-only classification migration may add to a
    # scope row after the contract payload was written.  Their presence
    # on the projection side is deterministic enrichment, not divergence.
    _PROJECTION_ENRICHMENT_KEYS = frozenset(
        {"classification", "requested_memory_type", "memory_type", "relations"}
    )

    @classmethod
    def _semantic_diverges(
        cls,
        projection: Mapping[str, Any],
        contract_record: Mapping[str, Any],
    ) -> bool:
        """True when scope and contract disagree on immutable semantics."""

        for key in _MEMORY_CONTRACT_PLAINTEXT_KEYS:
            in_projection = key in projection
            in_contract = key in contract_record
            if not in_contract:
                if in_projection and key not in cls._PROJECTION_ENRICHMENT_KEYS:
                    return True
                continue
            if not in_projection:
                return True
            if canonical_sha256(projection.get(key)) != canonical_sha256(
                contract_record.get(key)
            ):
                return True
        return False

    def _contract_matches(
        self,
        latest: Any,
        record: Mapping[str, Any],
        status: str,
    ) -> bool:
        store = self._contract_store()
        if str(getattr(latest, "lifecycle_status", "")) != status:
            return False
        if status == "deleted":
            return True
        protected_id = getattr(latest, "protected_payload_id", None)
        if protected_id is None:
            return False
        try:
            plaintext = store.read_protected_payload(str(protected_id))
        except LifeShadowStoreError:
            return False
        if plaintext == self._memory_contract_plaintext(record):
            return True
        try:
            document = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return False
        if not isinstance(document, Mapping) or not isinstance(
            document.get("record"), Mapping
        ):
            return False
        return not self._semantic_diverges(record, document["record"])

    def _reconcile_memory_contract(self, life_id: str) -> bool:
        """Verify the scope projection against the contract memory store.

        The journal defines the expected revision stream; the contract
        store is rolled forward when it is behind.  When the two disagree
        at the same revision, the contract store is authoritative and the
        scope projection is rebuilt from it (§7.1 startup reconciliation).
        """

        clean_life_id = str(life_id or "").strip()
        if not _OPAQUE.fullmatch(clean_life_id):
            return False
        store = self.authority_store
        if store is None:
            return False
        scope = self._scope_state(clean_life_id)
        changed = False
        divergences = 0
        rebuilt = 0

        # Step 1: derive the expected revision stream from the journal.
        asserted_records: dict[str, Mapping[str, Any]] = {}
        expected: dict[str, list[tuple[str, str, Mapping[str, Any]]]] = {}
        for event in self.system.journal.events(clean_life_id):
            event_type = str(event.get("event_type") or "")
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                if event_type.startswith("memory."):
                    raise EmbeddedLifeError(
                        "life.projection.memory_event_invalid", status=409
                    )
                continue
            if event_type in {"memory.asserted", "memory.corrected"}:
                assertion = payload.get("assertion")
                if not isinstance(assertion, Mapping):
                    raise EmbeddedLifeError(
                        "life.projection.memory_event_invalid", status=409
                    )
                live_id = str(assertion.get("memory_id") or "")
                asserted_records[live_id] = assertion
                expected.setdefault(live_id, []).append(
                    ("active", str(assertion.get("created_at") or ""), event)
                )
                if event_type == "memory.corrected":
                    target = str(payload.get("target_memory_id") or "")
                    expected.setdefault(target, []).append(
                        ("corrected", str(payload.get("updated_at") or ""), event)
                    )
            elif event_type == "memory.status_changed":
                memory_id = str(payload.get("memory_id") or "")
                status = str(payload.get("status") or "")
                expected.setdefault(memory_id, []).append(
                    (status, str(payload.get("updated_at") or ""), event)
                )
            elif event_type == "memory.deleted":
                memory_id = str(payload.get("memory_id") or "")
                expected.setdefault(memory_id, []).append(
                    ("deleted", str(payload.get("updated_at") or ""), event)
                )

        def source_record(live_id: str) -> Mapping[str, Any] | None:
            scope_row = scope["memories"].get(live_id)
            if isinstance(scope_row, dict) and scope_row.get("status") != "deleted":
                return scope_row
            journal_record = asserted_records.get(live_id)
            if journal_record is not None:
                return journal_record
            return scope_row if isinstance(scope_row, dict) else None

        for live_id, steps in expected.items():
            if not live_id:
                continue
            final_status, _final_updated, final_event = steps[-1]
            record = source_record(live_id)
            if record is None:
                raise EmbeddedLifeError(
                    "life.projection.memory_target_missing", status=409
                )
            contract_id = self._contract_memory_id(live_id)
            latest = store.get_latest_memory_assertion(contract_id)
            applied = latest.revision if latest is not None else 0
            if applied > len(steps):
                # Contract knows revisions the journal never produced
                # (contract-native or migrated writes): contract wins and
                # the scope projection is rebuilt below.
                divergences += 1
                continue
            if applied == len(steps):
                assert latest is not None
                if self._contract_matches(latest, record, final_status):
                    if (
                        final_status != "deleted"
                        and store.memory_change_seq_for(contract_id, applied) is None
                    ):
                        self._contract_store_assert(
                            clean_life_id,
                            record,
                            lifecycle_status=final_status,
                            journal_event=final_event,
                            updated_at=steps[-1][1],
                        )
                        changed = True
                    continue
                divergences += 1
                continue
            for status, step_updated_at, event in steps[applied:]:
                try:
                    if status == "deleted":
                        self._contract_store_delete(
                            clean_life_id,
                            live_id,
                            updated_at=step_updated_at,
                            journal_event=event,
                            record=record,
                        )
                    else:
                        self._contract_store_assert(
                            clean_life_id,
                            record,
                            lifecycle_status=status,
                            journal_event=event,
                            updated_at=step_updated_at,
                        )
                except LifeShadowStoreError:
                    # A same-revision disagreement: the contract store is
                    # authoritative, so the projection is rebuilt from it
                    # instead of forcing a conflicting revision forward.
                    divergences += 1
                    break
                changed = True

        # Step 2: rebuild projection rows the contract store owns.
        forward = {
            live_id: self._contract_memory_id(live_id) for live_id in expected
        }
        for assertion in store.list_latest_memory_assertions(
            clean_life_id, recallable_only=False
        ):
            live_id = next(
                (
                    candidate
                    for candidate, mapped in forward.items()
                    if mapped == assertion.memory_id
                ),
                assertion.memory_id,
            )
            scope_row = scope["memories"].get(live_id)
            if assertion.lifecycle_status == "deleted":
                tombstoned = {
                    "memory_id": live_id,
                    "life_id": clean_life_id,
                    "memory_type": (
                        str(scope_row.get("memory_type") or "semantic")
                        if isinstance(scope_row, dict)
                        else "semantic"
                    ),
                    "content": {"tombstone": True},
                    "provenance": (
                        deepcopy(scope_row.get("provenance"))
                        if isinstance(scope_row, dict)
                        and isinstance(scope_row.get("provenance"), Mapping)
                        else {}
                    ),
                    "relations": [],
                    "status": "deleted",
                    "revision": int(assertion.revision),
                    "created_at": (
                        str(scope_row.get("created_at") or "")
                        if isinstance(scope_row, dict)
                        else ""
                    )
                    or self._ms_iso(assertion.created_at_ms),
                    "updated_at": self._ms_iso(assertion.created_at_ms),
                }
                if not isinstance(scope_row, dict) or scope_row.get("status") != "deleted":
                    scope["memories"][live_id] = tombstoned
                    changed = True
                    rebuilt += 1
                continue
            assert assertion.protected_payload_id is not None
            try:
                raw = store.read_protected_payload(assertion.protected_payload_id)
            except LifeShadowStoreError:
                divergences += 1
                continue
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                divergences += 1
                continue
            if isinstance(document, Mapping) and isinstance(document.get("record"), Mapping):
                record = dict(document["record"])
            else:
                record = {
                    "memory_id": live_id,
                    "memory_type": "semantic",
                    "content": document,
                }
            record["memory_id"] = live_id
            record["life_id"] = clean_life_id
            if not isinstance(scope_row, dict):
                new_row = deepcopy(record)
                new_row.setdefault("provenance", {})
                new_row.setdefault("relations", [])
                new_row["status"] = assertion.lifecycle_status
                new_row["revision"] = int(assertion.revision)
                new_row.setdefault(
                    "created_at", self._ms_iso(assertion.valid_from_ms)
                )
                new_row.setdefault(
                    "updated_at", self._ms_iso(assertion.created_at_ms)
                )
                scope["memories"][live_id] = new_row
                changed = True
                rebuilt += 1
                continue
            mismatch = scope_row.get("status") != assertion.lifecycle_status
            if not mismatch:
                mismatch = self._semantic_diverges(scope_row, record)
            if mismatch:
                merged = deepcopy(scope_row)
                for key in _MEMORY_CONTRACT_PLAINTEXT_KEYS:
                    if key in record:
                        merged[key] = deepcopy(record[key])
                merged["status"] = assertion.lifecycle_status
                scope["memories"][live_id] = merged
                changed = True
                rebuilt += 1

        if divergences:
            self._memory_contract_divergences[clean_life_id] = (
                self._memory_contract_divergences.get(clean_life_id, 0) + divergences
            )
        if rebuilt:
            self._memory_contract_rebuilt[clean_life_id] = (
                self._memory_contract_rebuilt.get(clean_life_id, 0) + rebuilt
            )
        self._memory_contract_synced.add(clean_life_id)
        return changed

    def _ensure_memory_contract_synced(self, life_id: str) -> None:
        if life_id not in self._memory_contract_synced:
            self._reconcile_memory_contract(life_id)

    def _memory_projection_gate(
        self, life_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Capture ``required_memory_seq`` and gate a sensitive read on it.

        ``wait`` (default) blocks briefly for the projection to reach the
        required seq and fails closed when it cannot; ``direct`` reads the
        authoritative store head without waiting; ``no_action`` reports
        NO_ACTION instead of serving a stale read.
        """

        store = self._contract_store()
        raw = payload.get("required_memory_seq")
        if raw is None:
            required = 0
        else:
            if isinstance(raw, bool):
                raise EmbeddedLifeError("life.memory.required_seq_invalid")
            try:
                required = int(raw)
            except (TypeError, ValueError) as exc:
                raise EmbeddedLifeError("life.memory.required_seq_invalid") from exc
            if required < 0:
                raise EmbeddedLifeError("life.memory.required_seq_invalid")
        mode = str(payload.get("on_projection_lag") or "wait").strip().lower()
        if mode not in {"wait", "direct", "no_action"}:
            raise EmbeddedLifeError("life.memory.projection_lag_mode_invalid")
        try:
            wait_ms = max(
                0,
                min(
                    10_000,
                    int(
                        payload.get("projection_wait_ms")
                        if payload.get("projection_wait_ms") is not None
                        else 2_000
                    ),
                ),
            )
        except (TypeError, ValueError) as exc:
            raise EmbeddedLifeError("life.memory.projection_wait_invalid") from exc
        head = store.memory_change_head(life_id)
        gate = {
            "required_memory_seq": required,
            "memory_change_seq": head,
            "mode": mode,
            "status": "current",
        }
        if required <= head:
            return gate
        if mode == "no_action":
            gate["status"] = "no_action"
            return gate
        if mode == "direct":
            gate["status"] = "direct_read"
            return gate
        deadline = time.monotonic() + wait_ms / 1000.0
        while time.monotonic() < deadline:
            time.sleep(0.01)
            head = store.memory_change_head(life_id)
            if required <= head:
                gate["memory_change_seq"] = head
                gate["status"] = "waited"
                return gate
        raise EmbeddedLifeError("life.memory.projection_lag", status=409)

    def _memory_projection_head_payload(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id):
            raise EmbeddedLifeError("life.identity.id_invalid", status=409)
        self.system.identities.root_for(life_id)
        store = self._contract_store()
        return {
            "ok": True,
            "life_id": life_id,
            "memory_change_seq": store.memory_change_head(life_id),
            "global_memory_change_seq": store.memory_change_head(),
            "outbox_pending": store.count_pending_memory_outbox(life_id),
            "reconciled_divergences": int(
                self._memory_contract_divergences.get(life_id, 0)
            ),
            "rebuilt_from_contract": int(
                self._memory_contract_rebuilt.get(life_id, 0)
            ),
        }

    def _memory_outbox_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        store = self._contract_store()
        raw_life_id = str(payload.get("life_id") or "").strip()
        life_id = raw_life_id or None
        if life_id is not None:
            if not _OPAQUE.fullmatch(life_id):
                raise EmbeddedLifeError("life.identity.id_invalid", status=409)
            self.system.identities.root_for(life_id)
        try:
            limit = max(1, min(1024, int(payload.get("limit") or 256)))
        except (TypeError, ValueError) as exc:
            raise EmbeddedLifeError("life.memory.outbox_limit_invalid") from exc
        pending_only = payload.get("pending_only")
        rows = store.list_memory_outbox(
            life_id=life_id,
            pending_only=True if pending_only is None else bool(pending_only),
            limit=limit,
        )
        return {
            "ok": True,
            "life_id": life_id,
            "entries": [dict(row) for row in rows],
            "pending": store.count_pending_memory_outbox(life_id),
        }

    def _memory_outbox_ack(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        store = self._contract_store()
        raw_seq = payload.get("change_seq")
        receipt_id = str(payload.get("receipt_id") or "").strip()
        if isinstance(raw_seq, bool):
            raise EmbeddedLifeError("life.memory.outbox_ack_invalid")
        try:
            change_seq = int(raw_seq)
        except (TypeError, ValueError) as exc:
            raise EmbeddedLifeError("life.memory.outbox_ack_invalid") from exc
        if change_seq < 1 or not receipt_id or len(receipt_id) > 160:
            raise EmbeddedLifeError("life.memory.outbox_ack_invalid")
        delivered_raw = payload.get("delivered_at_ms")
        if delivered_raw is None:
            delivered_at_ms = time.time_ns() // 1_000_000
        else:
            if isinstance(delivered_raw, bool):
                raise EmbeddedLifeError("life.memory.outbox_ack_invalid")
            try:
                delivered_at_ms = int(delivered_raw)
            except (TypeError, ValueError) as exc:
                raise EmbeddedLifeError("life.memory.outbox_ack_invalid") from exc
            if delivered_at_ms < 0:
                raise EmbeddedLifeError("life.memory.outbox_ack_invalid")
        try:
            acked = store.ack_memory_outbox(
                change_seq,
                receipt_id=receipt_id,
                delivered_at_ms=delivered_at_ms,
            )
        except LifeShadowStoreError as exc:
            raise EmbeddedLifeError("life.memory.outbox_ack_conflict", status=409) from exc
        return {
            "ok": True,
            "change_seq": change_seq,
            "acked": acked,
            "duplicate": not acked,
        }

    def _memory_propose_candidates(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Persist proposed memory candidates as an audited queue.

        Candidates are validated, journaled and queued for later review;
        acceptance here means durably queued as ``proposed`` — never a
        silent promotion into asserted memory.
        """

        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id):
            raise EmbeddedLifeError("life.identity.id_invalid", status=409)
        self.system.identities.root_for(life_id)
        raw = payload.get("candidates")
        if not isinstance(raw, list) or not raw or len(raw) > _MAX_MEMORY_CANDIDATE_BATCH:
            raise EmbeddedLifeError("life.memory.candidates_invalid")
        scope = self._scope_state(life_id)
        queue = scope.setdefault("memory_candidates", {})
        if not isinstance(queue, dict):
            raise EmbeddedLifeError("life.state.memory_candidates_invalid", status=409)
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        added: list[str] = []
        now = utc_now()
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                rejected.append(
                    {"index": index, "reason_code": "life.memory.candidate_invalid"}
                )
                continue
            content = item.get("content")
            if not isinstance(content, Mapping) or not content:
                rejected.append(
                    {
                        "index": index,
                        "reason_code": "life.memory.candidate_content_invalid",
                    }
                )
                continue
            provenance = item.get("provenance") if "provenance" in item else {}
            if not isinstance(provenance, Mapping):
                rejected.append(
                    {
                        "index": index,
                        "reason_code": "life.memory.candidate_provenance_invalid",
                    }
                )
                continue
            memory_type = str(item.get("memory_type") or "auto").strip().lower()[:40]
            try:
                content_bytes = canonical_json_bytes(
                    {"content": content, "memory_type": memory_type}
                )
            except (TypeError, ValueError):
                rejected.append(
                    {
                        "index": index,
                        "reason_code": "life.memory.candidate_content_invalid",
                    }
                )
                continue
            if len(content_bytes) > _MAX_MEMORY_CANDIDATE_BYTES:
                rejected.append(
                    {
                        "index": index,
                        "reason_code": "life.memory.candidate_too_large",
                    }
                )
                continue
            candidate_id = str(
                item.get("candidate_id")
                or "cand_"
                + canonical_sha256(
                    {
                        "life_id": life_id,
                        "content": content,
                        "memory_type": memory_type,
                    }
                )[:48]
            ).strip()
            if not _OPAQUE.fullmatch(candidate_id):
                rejected.append(
                    {
                        "index": index,
                        "reason_code": "life.memory.candidate_id_invalid",
                    }
                )
                continue
            candidate = {
                "candidate_id": candidate_id,
                "life_id": life_id,
                "memory_type": memory_type,
                "content": deepcopy(content),
                "provenance": deepcopy(dict(provenance)),
                "status": "proposed",
                "created_at": now,
                "updated_at": now,
            }
            existing = queue.get(candidate_id)
            if isinstance(existing, dict):
                stable_keys = ("life_id", "memory_type", "content", "provenance")
                if canonical_sha256(
                    {key: existing.get(key) for key in stable_keys}
                ) != canonical_sha256({key: candidate.get(key) for key in stable_keys}):
                    rejected.append(
                        {
                            "index": index,
                            "reason_code": "life.memory.candidate_conflict",
                        }
                    )
                    continue
                accepted.append(
                    {
                        "candidate_id": candidate_id,
                        "status": str(existing.get("status") or "proposed"),
                        "duplicate": True,
                    }
                )
                continue
            queue[candidate_id] = candidate
            added.append(candidate_id)
            accepted.append(
                {
                    "candidate_id": candidate_id,
                    "status": "proposed",
                    "duplicate": False,
                }
            )
        event = None
        evicted = 0
        if added:
            overflow = len(queue) - _MAX_MEMORY_CANDIDATES_QUEUED
            if overflow > 0:
                ordered = sorted(
                    (row for row in queue.values() if isinstance(row, dict)),
                    key=lambda row: (
                        str(row.get("created_at") or ""),
                        str(row.get("candidate_id") or ""),
                    ),
                )
                for row in ordered[:overflow]:
                    if queue.pop(str(row.get("candidate_id") or ""), None) is not None:
                        evicted += 1
            try:
                event = self.system.journal.append(
                    life_id,
                    "memory.candidates_proposed",
                    {
                        "candidates": [
                            deepcopy(queue[candidate_id])
                            for candidate_id in added
                            if candidate_id in queue
                        ],
                        "evicted_count": evicted,
                    },
                    actor=str(payload.get("actor") or "user"),
                    idempotency_key=(
                        f"memory.candidates:{life_id}:"
                        + canonical_sha256({"candidate_ids": sorted(added)})[:32]
                    ),
                )
                self._persist(life_id)
            except Exception:
                for candidate_id in added:
                    queue.pop(candidate_id, None)
                raise
        return {
            "ok": True,
            "life_id": life_id,
            "accepted": accepted,
            "rejected": rejected,
            "queued_total": len(queue),
            "evicted_count": evicted,
            "event": event,
        }

    def _memory_assert(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        active_life_id = str(self._active().get("life_id") or "").strip()
        life_id = str(payload.get("life_id") or active_life_id).strip()
        if not _OPAQUE.fullmatch(life_id):
            raise EmbeddedLifeError("life.identity.id_invalid", status=409)
        # Resolve the identity through the authoritative registry before any
        # scoped write.  This lets concurrent callers pin a non-active life_id
        # without racing a process-global "active identity" switch, while an
        # unknown identity fails closed instead of creating an orphan scope.
        self.system.identities.root_for(life_id)
        memory_id = str(payload.get("memory_id") or "mem_" + canonical_sha256({"nonce": uuid.uuid4().hex}))
        if not _OPAQUE.fullmatch(memory_id):
            raise EmbeddedLifeError("life.memory.id_invalid")
        provenance = payload.get("provenance") if "provenance" in payload else {}
        relations = payload.get("relations") if "relations" in payload else []
        if not isinstance(provenance, Mapping):
            raise EmbeddedLifeError("life.memory.provenance_invalid")
        if not isinstance(relations, list):
            raise EmbeddedLifeError("life.memory.relations_invalid")
        try:
            confidence_milli = int(payload.get("confidence_milli") if payload.get("confidence_milli") is not None else 800)
            priority = int(payload.get("priority") if payload.get("priority") is not None else 900)
        except (TypeError, ValueError) as exc:
            raise EmbeddedLifeError("life.memory.score_invalid") from exc
        if not 0 <= confidence_milli <= 1000:
            raise EmbeddedLifeError("life.memory.confidence_invalid")
        if not -10_000 <= priority <= 10_000:
            raise EmbeddedLifeError("life.memory.priority_invalid")
        content = deepcopy(payload.get("content") if "content" in payload else {})
        epistemic_status = str(payload.get("epistemic_status") or "user_asserted").strip().lower()
        if epistemic_status not in {"observed", "user_asserted", "hypothesis", "verified"}:
            raise EmbeddedLifeError("life.memory.epistemic_status_invalid")
        try:
            classified = classify_memory(
                content=content,
                provenance=dict(provenance),
                relations=relations,
                requested_memory_type=payload.get("memory_type") or "auto",
                requested_causal_role=payload.get("causal_role") or "",
                epistemic_status=epistemic_status,
                confidence_milli=confidence_milli,
                priority=priority,
            )
        except ValueError as exc:
            raise EmbeddedLifeError("life.memory.classification_invalid") from exc
        classification = deepcopy(classified["classification"])
        normalized_relations = deepcopy(classified["relations"])
        memory_type = str(classification["memory_type"])
        semantic = {
            "memory_id": memory_id,
            "memory_type": memory_type,
            "requested_memory_type": str(payload.get("memory_type") or "auto"),
            "content": content,
            "provenance": deepcopy(dict(provenance)),
            "relations": normalized_relations,
            "classification": classification,
            "epistemic_status": epistemic_status,
            "confidence_milli": confidence_milli,
            "priority": priority,
        }
        try:
            semantic_bytes = canonical_json_bytes(semantic)
        except (TypeError, ValueError) as exc:
            raise EmbeddedLifeError("life.memory.payload_invalid") from exc
        if len(semantic_bytes) > _MAX_MEMORY_PAYLOAD_BYTES:
            raise EmbeddedLifeError("life.memory.payload_too_large")
        scope = self._scope_state(life_id)
        self._ensure_memory_contract_synced(life_id)
        existing = scope["memories"].get(memory_id)
        if isinstance(existing, dict):
            existing_semantic = {key: existing.get(key) for key in semantic}
            if canonical_sha256(existing_semantic) != canonical_sha256(semantic):
                raise EmbeddedLifeError("life.memory.id_conflict", status=409)
            contract_memory_id, change_seq = self._contract_store_assert(
                life_id, existing
            )
            return {
                "ok": True,
                "duplicate": True,
                "assertion": deepcopy(existing),
                "event": None,
                "contract_memory_id": contract_memory_id,
                "memory_change_seq": change_seq,
            }
        now = utc_now()
        record = {
            **semantic,
            "status": "active",
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        }
        record["lifecycle"] = initial_lifecycle(
            classification=classification,
            content=content,
            priority=priority,
            confidence_milli=confidence_milli,
        )
        record["life_id"] = life_id
        scope["memories"][memory_id] = record
        try:
            event = self.system.journal.append(
                life_id,
                "memory.asserted",
                {"assertion": record},
                actor=str(payload.get("actor") or "user"),
                idempotency_key=f"memory.assert:{memory_id}",
            )
            # The contract store is the second half of the one authoritative
            # write face: journal (WAL) + contract assertion share the same
            # request outcome.  A failure here rolls the scope row back and
            # startup reconciliation converges journal and contract store.
            contract_memory_id, change_seq = self._contract_store_assert(
                life_id, record, journal_event=event
            )
            self._persist(life_id)
        except Exception:
            scope["memories"].pop(memory_id, None)
            raise
        from world_understanding.post_commit import NativePostCommitEvent, notify_native_post_commit

        notify_native_post_commit(NativePostCommitEvent(
            source_kind="MEMORY",
            source_native_id=contract_memory_id,
            producer_ref="life_service.memory",
            payload={
                "memory_id": contract_memory_id,
                "memory_change_seq": change_seq,
                "memory_type": memory_type,
                "epistemic_status": epistemic_status,
                "confidence_milli": confidence_milli,
                "status": "active",
                "content_sha256": canonical_sha256(content),
            },
            occurred_at_ms=int(time.time() * 1000),
            identity={
                key: str(payload.get(key) or "")
                for key in (
                    "life_id",
                    "principal_scope_hash",
                    "workspace_id",
                    "run_id",
                    "request_id",
                    "session_id",
                    "conversation_id",
                )
                if payload.get(key)
            },
        ))
        # P15: user-explicit spans ("记住/以后记得/我的名字是...") must land as
        # L4 user_asserted even when the model phrase differs.  The assertion is
        # already durable above; attaching L4 is idempotent and recoverable.
        try:
            explicit_text = (
                content
                if isinstance(content, str)
                else str(content.get("text") or content.get("content") or "")
                if isinstance(content, Mapping)
                else ""
            )
            if str(explicit_text).strip():
                self._memory_coordinator().attach_explicit_l4(
                    life_id=life_id,
                    memory_id=contract_memory_id,
                    user_text=str(explicit_text),
                    created_at_ms=time.time_ns() // 1_000_000,
                    principal_ref=life_id,
                )
        except Exception:
            pass
        return {
            "ok": True,
            "duplicate": False,
            "assertion": record,
            "event": event,
            "contract_memory_id": contract_memory_id,
            "memory_change_seq": change_seq,
        }

    def _memory_search(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query") or "").casefold().strip()
        if len(query.encode("utf-8")) > _MAX_SEARCH_QUERY_BYTES:
            raise EmbeddedLifeError("life.memory.search_query_too_large")
        try:
            limit = max(1, min(200, int(payload.get("limit") or 20)))
        except (TypeError, ValueError) as exc:
            raise EmbeddedLifeError("life.memory.search_limit_invalid") from exc
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id):
            raise EmbeddedLifeError("life.identity.id_invalid", status=409)
        self.system.identities.root_for(life_id)
        gate = self._memory_projection_gate(life_id, payload)
        if gate["status"] == "no_action":
            return {
                "ok": True,
                "action": "NO_ACTION",
                "results": [],
                "query": query,
                "life_id": life_id,
                "thawed_count": 0,
                "memory_projection": gate,
            }
        raw_types = payload.get("memory_types") or []
        raw_roles = payload.get("causal_roles") or []
        raw_kinds = payload.get("assertion_kinds") or []
        raw_statuses = payload.get("statuses") or ["active"]
        if not all(isinstance(item, list) for item in (raw_types, raw_roles, raw_kinds, raw_statuses)):
            raise EmbeddedLifeError("life.memory.search_filter_invalid")
        if any(len(item) > _MAX_SEARCH_FILTER_ITEMS for item in (raw_types, raw_roles, raw_kinds, raw_statuses)):
            raise EmbeddedLifeError("life.memory.search_filter_too_large")
        types = {str(item).strip().lower() for item in raw_types if str(item).strip()}
        causal_roles = {str(item).strip().lower() for item in raw_roles if str(item).strip()}
        assertion_kinds = {str(item).strip().lower() for item in raw_kinds if str(item).strip()}
        allowed_statuses = {"active", "corrected", "superseded", "recall_suppressed", "deleted"}
        statuses = {str(item).strip().lower() for item in raw_statuses if str(item).strip()}
        if not statuses or not statuses <= allowed_statuses:
            raise EmbeddedLifeError("life.memory.search_status_invalid")
        causal_ref = str(payload.get("causal_ref") or "").strip()
        scope = self._scope_state(life_id)
        records = scope["memories"]
        # Retrieval begins with direct lexical/cue matches, then includes one
        # causal hop.  This brings the useful old trigger-recall behavior into
        # the new typed causal graph without inventing new semantic relations.
        neighbors: dict[str, set[str]] = {}
        for relation in scope.get("memory_relations") or []:
            if not isinstance(relation, Mapping):
                continue
            source = str(relation.get("source_memory_id") or "")
            target = str(relation.get("target_ref") or "")
            if source in records and target in records:
                neighbors.setdefault(source, set()).add(target)
                neighbors.setdefault(target, set()).add(source)
        for memory_id, raw in records.items():
            if not isinstance(raw, Mapping):
                continue
            for relation in raw.get("relations") or []:
                if not isinstance(relation, Mapping):
                    continue
                target = str(relation.get("target_ref") or "")
                if target in records:
                    neighbors.setdefault(str(memory_id), set()).add(target)
                    neighbors.setdefault(target, set()).add(str(memory_id))
        direct_seed_ids: set[str] = set()
        if query:
            for memory_id, raw in records.items():
                if not isinstance(raw, Mapping) or str(raw.get("status") or "active") not in statuses:
                    continue
                haystack = json.dumps({"content": raw.get("content"), "provenance": raw.get("provenance")}, ensure_ascii=False, sort_keys=True).casefold()
                if query in haystack:
                    direct_seed_ids.add(str(memory_id))
        rows: list[dict[str, Any]] = []
        lifecycle_changed = 0
        thawed = 0
        for record in reversed(list(records.values())):
            if not isinstance(record, dict) or str(record.get("status") or "active") not in statuses:
                continue
            classification = record.get("classification") if isinstance(record.get("classification"), Mapping) else {}
            classified_type = str(classification.get("memory_type") or record.get("memory_type") or "semantic")
            if types and record.get("memory_type") not in types and classified_type not in types:
                continue
            if causal_roles and str(classification.get("causal_role") or "context") not in causal_roles:
                continue
            if assertion_kinds and str(classification.get("assertion_kind") or "observation") not in assertion_kinds:
                continue
            if causal_ref and causal_ref not in set(classification.get("causal_refs") or []):
                continue
            haystack = json.dumps(
                {
                    "content": record.get("content"),
                    "provenance": record.get("provenance"),
                    "classification": classification,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).casefold()
            memory_id = str(record.get("memory_id") or "")
            is_direct = not query or memory_id in direct_seed_ids
            linked_seeds = sorted(neighbors.get(memory_id, set()) & direct_seed_ids)
            if query and not is_direct and not linked_seeds:
                continue
            lifecycle, changed, did_thaw = recall_lifecycle(record, query=query)
            if changed:
                record["lifecycle"] = lifecycle
                lifecycle_changed += 1
            thawed += int(did_thaw)
            row = deepcopy(record)
            lexical = haystack.count(query) if query else 0
            causal_bonus = 250 if classification.get("causal") else 0
            graph_bonus = min(600, 300 * len(linked_seeds))
            confidence = int(record.get("confidence_milli") or 0)
            priority = int(record.get("priority") or 0)
            heat = int(lifecycle.get("heat_milli") or 0)
            row["score_components"] = {
                "lexical": lexical,
                "causal_bonus": causal_bonus,
                "graph_bonus": graph_bonus,
                "confidence_milli": confidence,
                "priority": priority,
                "heat_milli": heat,
            }
            row["retrieval_path"] = "direct" if is_direct else "causal_neighbor"
            row["supporting_memory_ids"] = linked_seeds
            row["score"] = lexical * 10_000 + causal_bonus + graph_bonus + confidence + max(-10_000, priority) + heat
            rows.append(row)
        rows.sort(
            key=lambda item: (
                -int(item.get("score") or 0),
                -int(item.get("revision") or 1),
                str(item.get("memory_id") or ""),
            )
        )
        if lifecycle_changed:
            minute = time.time_ns() // 60_000_000_000
            self.system.journal.append(
                life_id,
                "memory.recalled",
                # Keep the checkpoint payload stable within a minute.  Recall
                # counts and thaw state live on each memory projection and may
                # legitimately differ between repeated searches.
                {"query": query[:256], "minute": minute},
                actor="life_memory",
                idempotency_key=f"memory.recall:{life_id}:{canonical_sha256({'query': query, 'minute': minute})[:32]}",
            )
            self._persist(life_id)
        return {"ok": True, "results": rows[:limit], "query": query, "life_id": life_id, "thawed_count": thawed, "memory_projection": gate}

    @staticmethod
    def _redact_memory_text(value: Any, *, limit: int = 20_000) -> str:
        text = str(value or "").replace("\x00", " ").strip()
        return _MEMORY_SECRET.sub("[REDACTED]", text)[:limit]

    def _memory_record_turn(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Persist one completed user/assistant turn as an episodic memory.

        This is the new equivalent of the old recent-dialogue stream.  It is
        identity-scoped, journaled, classified, and later subject to the same
        lifecycle as every other memory rather than becoming a second store.
        """

        user_text = self._redact_memory_text(payload.get("user_text"))
        assistant_text = self._redact_memory_text(payload.get("assistant_text"))
        if not user_text and not assistant_text:
            raise EmbeddedLifeError("life.memory.turn_empty")
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id):
            raise EmbeddedLifeError("life.identity.id_invalid", status=409)
        conversation_id = str(payload.get("conversation_id") or "desktop").strip()[:160]
        turn_id = str(payload.get("turn_id") or canonical_sha256({
            "life_id": life_id, "conversation_id": conversation_id,
            "user": user_text, "assistant": assistant_text,
        })[:40]).strip()
        if not _OPAQUE.fullmatch(turn_id):
            raise EmbeddedLifeError("life.memory.turn_id_invalid")
        result = self._memory_assert({
            "life_id": life_id,
            "memory_id": "mem_turn_" + canonical_sha256({"life_id": life_id, "turn_id": turn_id})[:40],
            "memory_type": "episodic",
            "content": {"conversation": {"conversation_id": conversation_id, "turn_id": turn_id, "user": user_text, "assistant": assistant_text}},
            "provenance": {"source": "conversation_turn", "conversation_id": conversation_id, "turn_id": turn_id},
            "epistemic_status": "observed",
            "confidence_milli": 850,
            "priority": int(payload.get("priority") or 650),
            "actor": str(payload.get("actor") or "conversation"),
        })
        if result.get("duplicate"):
            result["temperament"] = self._temperament_projection(life_id)
            return result
        scope = self._scope_state(life_id)
        innate = self._innate_temperament(life_id)
        # P15 M6: a plain completed turn never changes long-term temperament.
        # Only temperament-eligible active L5 core memory adapts, exactly once
        # per derivation through durable adaptation receipts.
        adapted = scope.get("temperament")
        if self.authority_store is not None:
            try:
                adapted, _receipts = self._memory_coordinator().adapt_temperament_from_core(
                    life_id=life_id,
                    innate=innate,
                    current_temperament=adapted,
                    now_ms=time.time_ns() // 1_000_000,
                )
            except Exception:
                adapted = scope.get("temperament")
        if isinstance(adapted, Mapping) and adapted != scope.get("temperament"):
            scope["temperament"] = adapted
            self._persist(life_id)
        result["temperament"] = public_temperament_projection(innate, adapted)
        return result

    def _memory_change_status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        memory_id = str(payload.get("memory_id") or payload.get("target_memory_id") or "").strip()
        status = str(payload.get("status") or "active").strip().lower()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(memory_id):
            raise EmbeddedLifeError("life.memory.id_invalid")
        if status not in {"active", "corrected", "superseded", "recall_suppressed"}:
            raise EmbeddedLifeError("life.memory.status_invalid")
        self._ensure_memory_contract_synced(life_id)
        scope = self._scope_state(life_id)
        row = scope["memories"].get(memory_id)
        if not isinstance(row, dict):
            raise EmbeddedLifeError("life.memory.not_found", status=404)
        if row.get("status") == "deleted":
            raise EmbeddedLifeError("life.memory.deleted_immutable", status=409)
        if row.get("status") == status:
            self._ensure_memory_contract_synced(life_id)
            contract_memory_id, change_seq = self._contract_store_assert(
                life_id, row, lifecycle_status=status
            )
            return {
                "ok": True,
                "duplicate": True,
                "memory": deepcopy(row),
                "contract_memory_id": contract_memory_id,
                "memory_change_seq": change_seq,
            }
        previous = deepcopy(row)
        updated_at = utc_now()
        row["status"] = status
        row["updated_at"] = updated_at
        try:
            event = self.system.journal.append(
                life_id,
                "memory.status_changed",
                {"memory_id": memory_id, "status": status, "updated_at": updated_at},
                actor=str(payload.get("actor") or "user"),
                idempotency_key=f"memory.status:{memory_id}:{status}",
            )
            contract_memory_id, change_seq = self._contract_store_assert(
                life_id,
                row,
                lifecycle_status=status,
                journal_event=event,
                updated_at=updated_at,
            )
            self._persist(life_id)
        except Exception:
            scope["memories"][memory_id] = previous
            raise
        return {
            "ok": True,
            "duplicate": False,
            "memory": deepcopy(row),
            "event": event,
            "contract_memory_id": contract_memory_id,
            "memory_change_seq": change_seq,
        }

    def _memory_add_relation(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        source_memory_id = str(payload.get("source_memory_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(source_memory_id):
            raise EmbeddedLifeError("life.memory.relation_invalid")
        scope = self._scope_state(life_id)
        if not isinstance(scope["memories"].get(source_memory_id), dict):
            raise EmbeddedLifeError("life.memory.not_found", status=404)
        try:
            normalized = normalize_relations([payload])[0]
        except (ValueError, IndexError) as exc:
            raise EmbeddedLifeError("life.memory.relation_invalid") from exc
        target_ref = str(normalized.get("target_ref") or "")
        if target_ref == source_memory_id:
            raise EmbeddedLifeError("life.memory.relation_self_forbidden")
        if target_ref and target_ref.startswith("mem_"):
            target_row = scope["memories"].get(target_ref)
            if not isinstance(target_row, dict):
                raise EmbeddedLifeError("life.memory.relation_target_not_found", status=404)
            if str(target_row.get("status") or "active") != "active":
                raise EmbeddedLifeError("life.memory.relation_target_inactive", status=409)
        source_row = scope["memories"].get(source_memory_id)
        if str(source_row.get("status") or "active") != "active":
            raise EmbeddedLifeError("life.memory.relation_source_inactive", status=409)
        relation_id = str(payload.get("relation_id") or "rel_" + canonical_sha256(
            {
                "life_id": life_id,
                "source_memory_id": source_memory_id,
                "kind": normalized["kind"],
                "target_ref": target_ref,
            }
        ))
        if not _OPAQUE.fullmatch(relation_id):
            raise EmbeddedLifeError("life.memory.relation_invalid")
        relation = {
            "relation_id": relation_id,
            "life_id": life_id,
            "source_memory_id": source_memory_id,
            "kind": normalized["kind"],
            "target_ref": target_ref,
            "direction": normalized["direction"],
            "evidence": normalized.get("evidence"),
            "created_at": utc_now(),
        }
        existing = next(
            (
                row
                for row in scope["memory_relations"]
                if isinstance(row, Mapping) and row.get("relation_id") == relation_id
            ),
            None,
        )
        if existing is not None:
            stable_existing = {key: existing.get(key) for key in relation if key != "created_at"}
            stable_relation = {key: relation.get(key) for key in relation if key != "created_at"}
            if canonical_sha256(stable_existing) != canonical_sha256(stable_relation):
                raise EmbeddedLifeError("life.memory.relation_conflict", status=409)
            return {"ok": True, "duplicate": True, "relation": deepcopy(existing)}
        scope["memory_relations"].append(relation)
        try:
            event = self.system.journal.append(
                life_id,
                "memory.relation_added",
                {"relation": relation},
                actor=str(payload.get("actor") or "user"),
                idempotency_key=f"memory.relation:{relation_id}",
            )
            self._persist(life_id)
        except Exception:
            scope["memory_relations"].pop()
            raise
        return {"ok": True, "duplicate": False, "relation": relation, "event": event}

    def _memory_delete(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        memory_id = str(payload.get("memory_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(memory_id):
            raise EmbeddedLifeError("life.memory.id_invalid")
        self._ensure_memory_contract_synced(life_id)
        scope = self._scope_state(life_id)
        row = scope["memories"].get(memory_id)
        if not isinstance(row, dict):
            raise EmbeddedLifeError("life.memory.not_found", status=404)
        if row.get("status") == "deleted":
            contract_memory_id, change_seq, tombstone_id = self._contract_store_delete(
                life_id, memory_id, updated_at=str(row.get("updated_at") or "")
            )
            return {
                "ok": True,
                "duplicate": True,
                "memory_id": memory_id,
                "deleted": True,
                "contract_memory_id": contract_memory_id,
                "memory_change_seq": change_seq,
                "tombstone_id": tombstone_id,
            }
        previous = deepcopy(row)
        updated_at = utc_now()
        row["status"] = "deleted"
        row["content"] = {"tombstone": True}
        row["updated_at"] = updated_at
        try:
            event = self.system.journal.append(
                life_id,
                "memory.deleted",
                {"memory_id": memory_id, "updated_at": updated_at},
                actor=str(payload.get("actor") or "user"),
                idempotency_key=f"memory.delete:{memory_id}",
            )
            # Privacy boundary: the contract store tombstone destroys the
            # protected-payload keys in the same transaction that records
            # the deletion proof, and pairs it with an outbox change row.
            contract_memory_id, change_seq, tombstone_id = self._contract_store_delete(
                life_id,
                memory_id,
                updated_at=updated_at,
                journal_event=event,
                record=previous,
            )
            # P15 I17: privacy deletion invalidates the memory's derivation
            # descendants so nothing survives in Context/Learning/World.
            self._memory_coordinator().delete_memory_with_privacy_cascade(
                life_id=life_id,
                memory_id=contract_memory_id,
                deleted_at_ms=(
                    self._iso_ms(updated_at)
                    or time.time_ns() // 1_000_000
                ),
            )
            self._persist(life_id)
        except Exception:
            scope["memories"][memory_id] = previous
            raise
        return {
            "ok": True,
            "duplicate": False,
            "memory_id": memory_id,
            "deleted": True,
            "event": event,
            "contract_memory_id": contract_memory_id,
            "memory_change_seq": change_seq,
            "tombstone_id": tombstone_id,
        }

    def _memory_correct(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically append a replacement assertion and correct its target.

        One journal event is the authority for both projection changes.  If
        projection persistence fails after the append, restart reconciliation
        restores both rows together instead of leaving a half-correction.
        """

        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        target_memory_id = str(payload.get("target_memory_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(target_memory_id):
            raise EmbeddedLifeError("life.memory.id_invalid")
        self.system.identities.root_for(life_id)
        self._ensure_memory_contract_synced(life_id)
        scope = self._scope_state(life_id)
        target = scope["memories"].get(target_memory_id)
        if not isinstance(target, dict):
            raise EmbeddedLifeError("life.memory.not_found", status=404)
        if target.get("status") == "deleted":
            raise EmbeddedLifeError("life.memory.deleted_immutable", status=409)

        provenance = payload.get("provenance") if "provenance" in payload else {}
        if not isinstance(provenance, Mapping):
            raise EmbeddedLifeError("life.memory.provenance_invalid")
        source_relations = payload.get("relations") if isinstance(payload.get("relations"), list) else []
        relations = [*source_relations, {"kind": "supersedes", "target_memory_id": target_memory_id}]
        content = deepcopy(payload.get("content") if "content" in payload else {})
        epistemic_status = str(payload.get("epistemic_status") or "user_asserted").strip().lower()
        if epistemic_status not in {"observed", "user_asserted", "hypothesis", "verified"}:
            raise EmbeddedLifeError("life.memory.epistemic_status_invalid")
        try:
            confidence_milli = int(payload.get("confidence_milli") if payload.get("confidence_milli") is not None else 800)
            priority = int(payload.get("priority") if payload.get("priority") is not None else 900)
            classified = classify_memory(
                content=content,
                provenance=dict(provenance),
                relations=relations,
                requested_memory_type=payload.get("memory_type") or target.get("memory_type") or "auto",
                requested_causal_role=payload.get("causal_role") or "",
                epistemic_status=epistemic_status,
                confidence_milli=confidence_milli,
                priority=priority,
            )
        except (TypeError, ValueError) as exc:
            raise EmbeddedLifeError("life.memory.classification_invalid") from exc
        if not 0 <= confidence_milli <= 1000:
            raise EmbeddedLifeError("life.memory.confidence_invalid")
        if not -10_000 <= priority <= 10_000:
            raise EmbeddedLifeError("life.memory.priority_invalid")

        semantic_seed = {
            "life_id": life_id,
            "target_memory_id": target_memory_id,
            "content": content,
            "provenance": dict(provenance),
            "relations": classified["relations"],
            "classification": classified["classification"],
            "epistemic_status": epistemic_status,
            "confidence_milli": confidence_milli,
            "priority": priority,
        }
        memory_id = str(
            payload.get("memory_id")
            or "mem_" + canonical_sha256({"domain": "life.memory.correction.v1", **semantic_seed})
        )
        if not _OPAQUE.fullmatch(memory_id):
            raise EmbeddedLifeError("life.memory.id_invalid")
        existing = scope["memories"].get(memory_id)
        duplicate = isinstance(existing, dict)
        if duplicate:
            record = deepcopy(existing)
            existing_seed = {
                "life_id": record.get("life_id"),
                "target_memory_id": target_memory_id,
                "content": record.get("content"),
                "provenance": record.get("provenance"),
                "relations": record.get("relations"),
                "classification": record.get("classification"),
                "epistemic_status": record.get("epistemic_status"),
                "confidence_milli": record.get("confidence_milli"),
                "priority": record.get("priority"),
            }
            if canonical_sha256(existing_seed) != canonical_sha256(semantic_seed):
                raise EmbeddedLifeError("life.memory.id_conflict", status=409)
        else:
            now = utc_now()
            record = {
                "memory_id": memory_id,
                "life_id": life_id,
                "memory_type": classified["classification"]["memory_type"],
                "requested_memory_type": str(payload.get("memory_type") or target.get("memory_type") or "auto"),
                "content": content,
                "provenance": deepcopy(dict(provenance)),
                "relations": deepcopy(classified["relations"]),
                "classification": deepcopy(classified["classification"]),
                "epistemic_status": epistemic_status,
                "confidence_milli": confidence_milli,
                "priority": priority,
                "status": "active",
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
        updated_at = str(record.get("created_at") or record.get("updated_at") or utc_now())
        before_target = deepcopy(target)
        before_replacement = deepcopy(existing) if isinstance(existing, dict) else None
        scope["memories"][memory_id] = record
        target["status"] = "corrected"
        target["updated_at"] = updated_at
        event_payload = {
            "assertion": record,
            "target_memory_id": target_memory_id,
            "updated_at": updated_at,
        }
        try:
            event = self.system.journal.append(
                life_id,
                "memory.corrected",
                event_payload,
                actor=str(payload.get("actor") or "user"),
                idempotency_key=f"memory.correct:{target_memory_id}:{memory_id}",
            )
            # One journal event drives two contract revisions: the
            # replacement assertion (rev 1 of a new memory id) and the
            # target's "corrected" status revision.
            contract_memory_id, change_seq = self._contract_store_assert(
                life_id, record, journal_event=event
            )
            _target_contract_id, target_change_seq = self._contract_store_assert(
                life_id,
                before_target,
                lifecycle_status="corrected",
                journal_event=event,
                updated_at=updated_at,
            )
            self._persist(life_id)
        except Exception:
            scope["memories"][target_memory_id] = before_target
            if before_replacement is None:
                scope["memories"].pop(memory_id, None)
            else:
                scope["memories"][memory_id] = before_replacement
            raise
        return {
            "ok": True,
            "duplicate": duplicate and before_target.get("status") == "corrected",
            "replacement": deepcopy(record),
            "target": deepcopy(target),
            "event": event,
            "contract_memory_id": contract_memory_id,
            "memory_change_seq": change_seq,
            "target_memory_change_seq": target_change_seq,
        }

    def _autonomy_tasks_payload(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id):
            raise EmbeddedLifeError("life.identity.id_invalid", status=409)
        autonomy = self._autonomy_state(life_id)
        status_filter = str(payload.get("status") or "").strip().lower()
        tasks = [
            deepcopy(task)
            for task in autonomy["tasks"].values()
            if isinstance(task, Mapping) and (not status_filter or task.get("status") == status_filter)
        ]
        tasks.sort(key=lambda task: (int(task.get("sequence") or 0), str(task.get("task_id") or "")))
        return {"ok": True, "life_id": life_id, "tasks": tasks, "autonomy": self._autonomy_health_payload()}

    def _autonomy_change_status(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        task_id = str(payload.get("task_id") or "").strip()
        status = str(payload.get("status") or "").strip().lower()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(task_id):
            raise EmbeddedLifeError("life.autonomy.task_id_invalid")
        scope = self._scope_state(life_id)
        autonomy = self._autonomy_state(life_id)
        result_payload = payload.get("result") if isinstance(payload.get("result"), Mapping) else None
        if result_payload is not None:
            try:
                if len(canonical_json_bytes(result_payload)) > _MAX_TASK_RESULT_BYTES:
                    raise EmbeddedLifeError("life.autonomy.task_result_too_large")
            except (TypeError, ValueError) as exc:
                raise EmbeddedLifeError("life.autonomy.task_result_invalid") from exc
        before = deepcopy(autonomy)
        try:
            task = update_task_status(
                autonomy,
                task_id=task_id,
                status=status,
                result=result_payload,
            )
        except KeyError as exc:
            raise EmbeddedLifeError("life.autonomy.task_not_found", status=404) from exc
        except ValueError as exc:
            raise EmbeddedLifeError("life.autonomy.task_transition_invalid", status=409) from exc
        try:
            event = self.system.journal.append(
                life_id,
                "autonomy.task_status_changed",
                {"task_id": task_id, "task": task},
                actor=str(payload.get("actor") or "runtime"),
                idempotency_key=f"autonomy.task.status:{task_id}:{status}:{task.get('attempt_count', 0)}",
            )
            if status == "completed":
                self._sync_daily_summary(life_id)
            self._persist(life_id)
        except Exception:
            scope["autonomy"] = before
            raise
        return {"ok": True, "task": task, "event": event}

    def _activity_scope_payload(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id):
            raise EmbeddedLifeError("life.identity.id_invalid", status=409)
        return {"ok": True, "activity_scope": build_activity_scope(
            life_id=life_id,
            soul=self._soul(),
            scope=self._scope_state(life_id),
        )}

    def _capability_overlay_payload(self, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id):
            raise EmbeddedLifeError("life.identity.id_invalid", status=409)
        scope = self._scope_state(life_id)
        pointers = scope.get("capability_pointers") if isinstance(scope.get("capability_pointers"), Mapping) else {}
        rows = []
        for raw in scope["capabilities"].values():
            if not isinstance(raw, Mapping) or raw.get("status") != "published" or raw.get("kind") not in {"skill", "tool"}:
                continue
            row = deepcopy(dict(raw))
            pointer = pointers.get(str(row.get("lineage_id") or "")) if isinstance(pointers, Mapping) else None
            is_current = isinstance(pointer, Mapping) and pointer.get("current_artifact_id") == row.get("artifact_id")
            if not is_current:
                continue
            activation_status = str(pointer.get("status") or "pending")
            row["activation_status"] = activation_status
            row["runtime_usable"] = activation_status == "active"
            rows.append(row)
        rows.sort(key=lambda item: (str(item.get("kind")), str(item.get("title")), int(item.get("version") or 0)))
        model_context = []
        active_rows = [row for row in rows if row.get("activation_status") == "active"]
        for row in active_rows[:32]:
            spec = row.get("skill_spec") if isinstance(row.get("skill_spec"), Mapping) else {}
            publication = row.get("publication") if isinstance(row.get("publication"), Mapping) else {}
            model_context.append({
                "artifact_id": row.get("artifact_id"),
                "kind": row.get("kind"),
                "title": row.get("title"),
                "summary": row.get("summary"),
                "risk_level": row.get("risk_level"),
                "task_intents": list(spec.get("task_intents") or [])[:24],
                "required_actions": list(row.get("required_actions") or [])[:16],
                "steps": list(spec.get("steps") or [])[:16],
                "workspace_path": publication.get("workspace_path") or "",
            })
        return {
            "ok": True,
            "schema": "tiangong.life.skill-overlay.v1",
            "life_id": life_id,
            "overlay_sha256": canonical_sha256({"life_id": life_id, "artifacts": rows}),
            "artifacts": rows,
            "model_context": model_context,
            "active_skill_count": sum(1 for row in active_rows if row.get("kind") == "skill"),
            "composite_tool_count": sum(1 for row in active_rows if row.get("kind") == "tool"),
            "pending_activation_count": sum(1 for row in rows if row.get("activation_status") == "pending"),
        }

    def _set_capability_pointer(
        self,
        *,
        life_id: str,
        scope: Mapping[str, Any],
        artifact: Mapping[str, Any],
        prior_artifact_id: str = "",
        operation: str = "publish",
        status: str = "active",
    ) -> dict[str, Any]:
        lineage_id = str(artifact.get("lineage_id") or "").strip()
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        if not _OPAQUE.fullmatch(lineage_id) or not _OPAQUE.fullmatch(artifact_id):
            raise EmbeddedLifeError("life.capability.pointer_invalid", status=409)
        pointers = scope.get("capability_pointers")
        if not isinstance(pointers, dict):
            raise EmbeddedLifeError("life.state.capability_pointers_invalid", status=409)
        before = pointers.get(lineage_id)
        history = list(before.get("history") or []) if isinstance(before, Mapping) else []
        history.append({
            "operation": operation,
            "from_artifact_id": str(prior_artifact_id or (before or {}).get("current_artifact_id") or ""),
            "to_artifact_id": artifact_id,
            "at": utc_now(),
        })
        pointer = {
            "schema": "tiangong.life.capability-pointer.v1",
            "life_id": life_id,
            "lineage_id": lineage_id,
            "kind": artifact.get("kind"),
            "status": status,
            "current_artifact_id": artifact_id,
            "current_artifact_sha256": artifact.get("artifact_sha256"),
            "history": history[-64:],
        }
        pointer = attach_health(pointer, artifact=artifact, now_ms=time.time_ns() // 1_000_000)
        pointer["pointer_sha256"] = canonical_sha256({key: pointer[key] for key in pointer if key != "pointer_sha256"})
        pointers[lineage_id] = pointer
        persist_current_pointer(self.paths.artifact_root, life_id=life_id, lineage_id=lineage_id, pointer=pointer)
        return pointer

    def _capability_activate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        artifact_id = str(payload.get("artifact_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(artifact_id):
            raise EmbeddedLifeError("life.capability.id_invalid")
        scope = self._scope_state(life_id)
        artifact = scope["capabilities"].get(artifact_id)
        if (
            not isinstance(artifact, Mapping)
            or artifact.get("status") != "published"
            or not _is_life_generated_capability(artifact)
        ):
            raise EmbeddedLifeError("life.capability.not_activatable", status=409)
        lineage_id = str(artifact.get("lineage_id") or "").strip()
        pointer = (scope.get("capability_pointers") or {}).get(lineage_id)
        if not isinstance(pointer, Mapping) or pointer.get("current_artifact_id") != artifact_id:
            raise EmbeddedLifeError("life.capability.activate_not_current", status=409)
        if pointer.get("status") == "active":
            return {"ok": True, "already_active": True, "capability": deepcopy(artifact), "pointer": deepcopy(pointer)}
        if pointer.get("status") != "pending":
            raise EmbeddedLifeError("life.capability.activate_invalid_state", status=409)
        pointers_before = deepcopy(scope["capability_pointers"])
        try:
            next_pointer = deepcopy(dict(pointer))
            next_pointer["status"] = "active"
            next_pointer["activated_at"] = utc_now()
            next_pointer["history"] = list(next_pointer.get("history") or [])[-63:] + [{
                "operation": "activate",
                "from_artifact_id": artifact_id,
                "to_artifact_id": artifact_id,
                "at": next_pointer["activated_at"],
            }]
            next_pointer["pointer_sha256"] = canonical_sha256({
                key: next_pointer[key] for key in next_pointer if key != "pointer_sha256"
            })
            scope["capability_pointers"][lineage_id] = next_pointer
            persist_current_pointer(
                self.paths.artifact_root,
                life_id=life_id,
                lineage_id=lineage_id,
                pointer=next_pointer,
            )
            event = self.system.journal.append(
                life_id,
                "capability.activated",
                {"artifact_id": artifact_id, "pointer": next_pointer},
                actor=str(payload.get("actor") or "user"),
                idempotency_key=f"capability.activate:{artifact_id}",
            )
            self._persist(life_id)
        except Exception:
            scope["capability_pointers"] = pointers_before
            raise
        self._notify_life_learning_post_commit(
            life_id=life_id,
            event=event,
            artifact=artifact,
            status="activated",
        )
        return {"ok": True, "capability": deepcopy(artifact), "pointer": deepcopy(next_pointer), "event": event}

    def _capability_rollback(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        artifact_id = str(payload.get("artifact_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(artifact_id):
            raise EmbeddedLifeError("life.capability.id_invalid")
        scope = self._scope_state(life_id)
        current = scope["capabilities"].get(artifact_id)
        if not isinstance(current, Mapping) or current.get("status") != "published":
            raise EmbeddedLifeError("life.capability.not_found", status=404)
        previous_id = str(current.get("previous_artifact_id") or "").strip()
        previous = scope["capabilities"].get(previous_id)
        lineage_id = str(current.get("lineage_id") or "").strip()
        pointer = (scope.get("capability_pointers") or {}).get(lineage_id)
        if not isinstance(previous, Mapping) or not isinstance(pointer, Mapping):
            raise EmbeddedLifeError("life.capability.rollback_unavailable", status=409)
        if pointer.get("status") != "active" or pointer.get("current_artifact_id") != artifact_id:
            raise EmbeddedLifeError("life.capability.rollback_not_current", status=409)
        try:
            rollback = rollback_pointer(current, previous)
        except ArtifactExecutorError as exc:
            raise EmbeddedLifeError("life.capability.rollback_invalid", status=409) from exc
        pointers_before = deepcopy(scope["capability_pointers"])
        try:
            next_pointer = self._set_capability_pointer(
                life_id=life_id,
                scope=scope,
                artifact=previous,
                prior_artifact_id=artifact_id,
                operation="rollback",
            )
            event = self.system.journal.append(
                life_id,
                "capability.rolled_back",
                {"rollback": rollback, "pointer": next_pointer},
                actor=str(payload.get("actor") or "user"),
                idempotency_key=f"capability.rollback:{artifact_id}:{previous_id}",
            )
            self._persist(life_id)
        except Exception:
            scope["capability_pointers"] = pointers_before
            raise
        self._notify_life_learning_post_commit(
            life_id=life_id,
            event=event,
            artifact=previous,
            status="rolled_back",
        )
        return {"ok": True, "rollback": rollback, "pointer": deepcopy(next_pointer), "event": event}

    def _capability_discard(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Remove a Life-generated capability while preserving release tools."""
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        artifact_id = str(payload.get("artifact_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(artifact_id):
            raise EmbeddedLifeError("life.capability.id_invalid")
        scope = self._scope_state(life_id)
        artifact = scope["capabilities"].get(artifact_id)
        if (
            not isinstance(artifact, Mapping)
            or artifact.get("status") != "published"
            or not _is_life_generated_capability(artifact)
        ):
            raise EmbeddedLifeError("life.capability.not_found", status=404)
        lineage_id = str(artifact.get("lineage_id") or "").strip()
        pointers = scope.get("capability_pointers")
        current_pointer = pointers.get(lineage_id) if isinstance(pointers, Mapping) else None
        if not isinstance(current_pointer, Mapping) or current_pointer.get("current_artifact_id") != artifact_id:
            raise EmbeddedLifeError("life.capability.discard_not_current", status=409)
        pointers_before = deepcopy(scope["capability_pointers"])
        capabilities_before = deepcopy(scope["capabilities"])
        try:
            pointer = deepcopy(dict(current_pointer))
            pointer["status"] = "disabled"
            pointer["disabled_at"] = utc_now()
            pointer["disabled_reason"] = str(payload.get("reason") or "user_deleted")[:400]
            pointer["history"] = list(pointer.get("history") or [])[-63:] + [{
                "operation": "disable", "from_artifact_id": artifact_id, "to_artifact_id": "", "at": pointer["disabled_at"],
            }]
            pointer["pointer_sha256"] = canonical_sha256({key: pointer[key] for key in pointer if key != "pointer_sha256"})
            scope["capability_pointers"][lineage_id] = pointer
            scope["capabilities"].pop(artifact_id, None)
            persist_current_pointer(self.paths.artifact_root, life_id=life_id, lineage_id=lineage_id, pointer=pointer)
            event = self.system.journal.append(
                life_id, "capability.disabled", {"artifact_id": artifact_id, "pointer": pointer},
                actor=str(payload.get("actor") or "user"), idempotency_key=f"capability.disable:{artifact_id}",
            )
            self._persist(life_id)
        except Exception:
            scope["capability_pointers"] = pointers_before
            scope["capabilities"] = capabilities_before
            raise
        self._notify_life_learning_post_commit(
            life_id=life_id,
            event=event,
            artifact=artifact,
            status="disabled",
        )
        try:
            bundle_deleted = delete_artifact_bundle(self.paths.artifact_root, artifact)
        except (ArtifactExecutorError, OSError):
            bundle_deleted = False
        workspace_mapping_removed = False
        remover = self._capability_workspace_remover
        if callable(remover):
            try:
                workspace_mapping_removed = bool((remover(artifact) or {}).get("removed"))
            except Exception:
                workspace_mapping_removed = False
        generated_tool_id = str((artifact.get("skill_spec") or {}).get("skill_id") or artifact_id)
        return {
            "ok": True,
            "capability": deepcopy(artifact),
            "pointer": deepcopy(pointer),
            "event": event,
            "deleted_artifact_ids": [artifact_id],
            "deleted_generated_tool_ids": [generated_tool_id],
            "preserved_release_actions": list(artifact.get("required_actions") or []),
            "bundle_deleted": bundle_deleted,
            "workspace_mapping_removed": workspace_mapping_removed,
        }

    def _mark_capability_workspace_status(self, artifact: Mapping[str, Any], pointer: Mapping[str, Any]) -> None:
        """同步工作区映射的状态标记；失败仅影响标记，不影响权威状态。"""
        marker = self._capability_workspace_marker
        if not callable(marker):
            return
        try:
            marker(artifact, pointer)
        except Exception:
            return

    def _capability_health_material(
        self,
        *,
        life_id: str,
        artifact: Mapping[str, Any],
        pointer: Mapping[str, Any],
        scope: Mapping[str, Any],
    ) -> dict[str, Any]:
        """构建补丁决策的只读上下文：当前版本 + 健康档案 + 最近失败执行。"""
        health = pointer.get("health") if isinstance(pointer.get("health"), Mapping) else {}
        recent_failures: list[dict[str, Any]] = []
        executions = scope.get("executions") if isinstance(scope.get("executions"), Mapping) else {}
        for row in executions.values():
            if (
                isinstance(row, Mapping)
                and row.get("artifact_id") == artifact.get("artifact_id")
                and str(row.get("status") or "") == "failed"
            ):
                recent_failures.append({
                    "execution_id": str(row.get("execution_id") or "")[:40],
                    "status": "failed",
                    "steps": [
                        {
                            "position": step.get("position"),
                            "step_id": step.get("step_id"),
                            "action_id": step.get("action_id"),
                            "ok": step.get("ok"),
                        }
                        for step in (row.get("steps") or [])
                        if isinstance(step, Mapping)
                    ][:8],
                })
            if len(recent_failures) >= 3:
                break
        spec = artifact.get("skill_spec") if isinstance(artifact.get("skill_spec"), Mapping) else {}
        return {
            "life_id": life_id,
            "artifact_id": str(artifact.get("artifact_id") or ""),
            "artifact_sha256": str(artifact.get("artifact_sha256") or ""),
            "version": artifact.get("version"),
            "kind": str(artifact.get("kind") or "skill"),
            "title": str(artifact.get("title") or ""),
            "summary": str(artifact.get("summary") or ""),
            "risk_level": str(artifact.get("risk_level") or "A3"),
            "skill_spec": deepcopy(dict(spec)),
            "document": deepcopy(dict(artifact.get("document") or {})),
            "health": {
                key: health.get(key)
                for key in (
                    "uses", "successes", "failures", "consecutive_failures",
                    "patch_rounds", "patch_history",
                )
            },
            "recent_failures": recent_failures,
        }

    def _capability_patch_propose(
        self,
        payload: Mapping[str, Any],
        *,
        decision: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """为连续失败的能力生成补丁草案并进入验证（不替换当前指针）。

        decision 可来自显式 API 或模型桥。补丁编译为下一版本 artifact，
        注册为候选（published 但非 current），随后 propose_patch 进入验证门。
        """
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        artifact_id = str(payload.get("artifact_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(artifact_id):
            raise EmbeddedLifeError("life.capability.id_invalid")
        scope = self._scope_state(life_id)
        artifact = scope["capabilities"].get(artifact_id)
        if (
            not isinstance(artifact, Mapping)
            or artifact.get("status") != "published"
            or artifact.get("kind") not in {"skill", "tool"}
        ):
            raise EmbeddedLifeError("life.capability.not_found", status=404)
        lineage_id = str(artifact.get("lineage_id") or "")
        pointer = (scope.get("capability_pointers") or {}).get(lineage_id)
        if (
            not isinstance(pointer, Mapping)
            or pointer.get("status") != "active"
            or pointer.get("current_artifact_id") != artifact_id
        ):
            raise EmbeddedLifeError("life.capability.not_active", status=409)
        health = pointer.get("health") if isinstance(pointer.get("health"), Mapping) else {}
        if int(health.get("consecutive_failures") or 0) < DEFAULT_MAX_CONSECUTIVE_FAILURES:
            raise EmbeddedLifeError("life.capability.patch_not_triggered", status=409)
        if health.get("patch_pending"):
            raise EmbeddedLifeError("life.capability.patch_already_pending", status=409)
        if int(health.get("patch_rounds") or 0) >= DEFAULT_MAX_PATCH_ROUNDS:
            raise EmbeddedLifeError("life.capability.patch_rounds_exhausted", status=409)
        if decision is None:
            material = self._capability_health_material(
                life_id=life_id,
                artifact=artifact,
                pointer=pointer,
                scope=scope,
            )
            decider = self._capability_patch_decider
            if not callable(decider):
                raise EmbeddedLifeError("life.capability.patch_decider_unavailable", status=503)
            decision = decider(material)
        if not isinstance(decision, Mapping):
            raise EmbeddedLifeError("life.capability.patch_decision_invalid", status=409)
        draft_artifact = decision.get("draft_artifact") or decision.get("artifact") or {}
        if not isinstance(draft_artifact, Mapping) or not draft_artifact:
            raise EmbeddedLifeError("life.capability.patch_artifact_invalid", status=409)
        learning = {
            "life_id": life_id,
            "learning_id": "learn_patch_" + canonical_sha256({
                "artifact_id": artifact_id,
                "round": int(health.get("patch_rounds") or 0) + 1,
            })[:32],
            "target": str(artifact.get("kind") or "skill"),
            "title": str(decision.get("title") or f"{artifact.get('title')} 补丁"),
            "summary": str(decision.get("summary") or artifact.get("summary") or ""),
            "risk_level": str(decision.get("risk_level") or artifact.get("risk_level") or "A3"),
            "draft_artifact": deepcopy(dict(draft_artifact)),
        }
        try:
            compiled = compile_artifact(
                learning,
                action_catalog=self._artifact_action_catalog(),
                previous_artifact=artifact,
                require_acceptance=True,
            )
        except (ArtifactExecutorError, TypeError, ValueError) as exc:
            # 编译失败也是一轮失败的补丁尝试：轮次照常消耗，轮次用尽则
            # 自动降级，否则坏模型可以无限产出编译不过的“补丁”而永不闭环。
            return self._register_failed_patch_attempt(
                life_id=life_id,
                scope=scope,
                artifact=artifact,
                pointer=pointer,
                round_kind="build_failed",
                error_code=f"life.capability.patch_unbuildable:{str(exc)[:120]}",
            )
        bundle_path = persist_artifact_bundle(self.paths.artifact_root, compiled)
        published_patch = publish_artifact(compiled)
        patched_id = str(published_patch.get("artifact_id") or "")
        capabilities_before = deepcopy(scope["capabilities"])
        pointers_before = deepcopy(scope["capability_pointers"])
        try:
            scope["capabilities"][patched_id] = {
                **published_patch,
                "origin": "life_patch",
                "patch_of": artifact_id,
            }
            updated_pointer = propose_patch(
                pointer,
                published_patch,
                now_ms=time.time_ns() // 1_000_000,
                max_patch_rounds=DEFAULT_MAX_PATCH_ROUNDS,
            )
            scope["capability_pointers"][lineage_id] = updated_pointer
            event = self.system.journal.append(
                life_id,
                "capability.patch_proposed",
                {
                    "artifact_id": artifact_id,
                    "patched_artifact_id": patched_id,
                    "pointer": updated_pointer,
                    "bundle_path": str(bundle_path),
                },
                actor=str(payload.get("actor") or "life_health"),
                idempotency_key=f"capability.patch_propose:{artifact_id}:{patched_id}",
            )
            self._persist(life_id)
        except Exception:
            scope["capabilities"] = capabilities_before
            scope["capability_pointers"] = pointers_before
            raise
        self._notify_life_learning_post_commit(
            life_id=life_id,
            event=event,
            artifact=published_patch,
            status="patched",
            learning=learning,
        )
        return {
            "ok": True,
            "patch_artifact": deepcopy(published_patch),
            "pointer": deepcopy(updated_pointer),
            "event": event,
            "bundle_path": str(bundle_path),
        }

    def _register_failed_patch_attempt(
        self,
        *,
        life_id: str,
        scope: Mapping[str, Any],
        artifact: Mapping[str, Any],
        pointer: Mapping[str, Any],
        round_kind: str,
        error_code: str,
    ) -> dict[str, Any]:
        """把一次失败的补丁尝试计入轮次；轮次用尽自动降级。"""
        now_ms = time.time_ns() // 1_000_000
        lineage_id = str(artifact.get("lineage_id") or "")
        health = dict(pointer.get("health") or {})
        rounds = int(health.get("patch_rounds") or 0) + 1
        health["patch_rounds"] = rounds
        history = list(health.get("patch_history") or [])
        history.append({
            "round": rounds,
            "from_artifact_id": str(artifact.get("artifact_id") or ""),
            "to_artifact_id": "",
            "result": round_kind,
            "verified_at_ms": now_ms,
            "evidence_sha256": error_code[:160] or "",
        })
        health["patch_history"] = history[-64:]
        updated = dict(pointer)
        updated["health"] = health
        degraded = False
        if rounds >= DEFAULT_MAX_PATCH_ROUNDS:
            updated = degrade_pointer(updated, reason=f"patch_rounds_exhausted:{rounds}", now_ms=now_ms)
            degraded = True
        else:
            updated["pointer_sha256"] = canonical_sha256({
                key: updated[key] for key in updated if key != "pointer_sha256"
            })
        pointers_before = deepcopy(scope["capability_pointers"])
        try:
            scope["capability_pointers"][lineage_id] = updated
            if degraded:
                self._mark_capability_workspace_status(artifact, updated)
            event = self.system.journal.append(
                life_id,
                "capability.patch_failed",
                {
                    "artifact_id": artifact.get("artifact_id"),
                    "round": rounds,
                    "round_kind": round_kind,
                    "error_code": error_code[:240],
                    "degraded": degraded,
                    "pointer": updated,
                },
                actor="life_health",
                idempotency_key=f"capability.patch_failed:{artifact.get('artifact_id')}:{rounds}",
            )
            self._persist(life_id)
        except Exception:
            scope["capability_pointers"] = pointers_before
            raise
        if degraded:
            self._notify_life_learning_post_commit(
                life_id=life_id,
                event=event,
                artifact=artifact,
                status="degraded",
            )
        return {
            "ok": False,
            "error_code": error_code[:240],
            "patch_rounds": rounds,
            "degraded": degraded,
            "pointer": deepcopy(updated),
            "event": event,
        }

    def _capability_verify_patch(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        """补丁验证门（L1 静态契约，无外部副作用）。

        检查：不可变摘要、动作绑定、步骤完整性、参数模板可静态解析、
        输入/输出 schema 与验收标准。任何一项不满足即拒绝补丁。
        执行级 fixture（L2）保留为后续显式验证器扩展，本门不自动执行工具。
        """
        reasons: list[str] = []
        digest = str(artifact.get("artifact_sha256") or "")
        digest_ok = False
        if digest:
            # 权威摘要来自不可变 bundle（built 形态），scope 中发布的附加
            # 字段（origin/publication/patch_of 等）不参与摘要。
            bundle = self.paths.artifact_root / str(artifact.get("artifact_id") or "") / "artifact.json"
            try:
                if bundle.is_file():
                    stored = json.loads(bundle.read_text(encoding="utf-8"))
                    digest_ok = str(stored.get("artifact_sha256") or "") == digest
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                digest_ok = False
            if not digest_ok:
                build_value = {key: artifact[key] for key in artifact if key != "artifact_sha256"}
                if build_value.get("status") == "published":
                    build_value["status"] = "built"
                    build_value.pop("publish_sha256", None)
                for key in ("origin", "patch_of", "publication"):
                    build_value.pop(key, None)
                expected = canonical_sha256({
                    "schema": str(artifact.get("schema") or ""),
                    "artifact": build_value,
                })
                digest_ok = digest == expected
        if not digest_ok:
            reasons.append("patch.digest_invalid")
        spec = artifact.get("skill_spec") if isinstance(artifact.get("skill_spec"), Mapping) else {}
        if not isinstance(spec, Mapping) or not spec:
            reasons.append("patch.skill_spec_missing")
        steps = spec.get("steps") if isinstance(spec, Mapping) else []
        if not isinstance(steps, list) or not steps:
            reasons.append("patch.steps_missing")
        required_actions = list(artifact.get("required_actions") or [])
        if not required_actions:
            reasons.append("patch.actions_missing")
        catalog = {row.get("action_id"): row for row in self._artifact_action_catalog() if isinstance(row, Mapping)}
        for index, raw_step in enumerate(steps):
            if not isinstance(raw_step, Mapping):
                reasons.append("patch.step_invalid")
                continue
            action_id = str(raw_step.get("action_id") or "").strip()
            action = catalog.get(action_id)
            if action is None:
                reasons.append(f"patch.action_unknown:{action_id}")
                continue
            if action.get("available") is not True:
                reasons.append(f"patch.action_unavailable:{action_id}")
            template = raw_step.get("arguments_template")
            if not isinstance(template, Mapping):
                reasons.append(f"patch.arguments_invalid:{action_id}")
            elif not _template_syntax_ok(template):
                reasons.append(f"patch.arguments_unresolvable:{action_id}")
        for key in ("input_schema", "output_schema"):
            if not isinstance(spec.get(key), Mapping):
                reasons.append(f"patch.{key}_missing")
        if not isinstance(spec.get("acceptance"), list) or not spec.get("acceptance"):
            reasons.append("patch.acceptance_missing")
        evidence_sha256 = canonical_sha256({
            "domain": "tiangong.life.capability-patch-verification.v1",
            "artifact_id": artifact.get("artifact_id"),
            "artifact_sha256": digest,
            "reasons": tuple(sorted(reasons)),
        })
        return {
            "passed": not reasons,
            "reasons": reasons,
            "evidence_sha256": evidence_sha256,
        }

    def _capability_patch_settle(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """结算补丁验证：通过则 CAS 切换，失败则回滚，轮次用尽自动降级。"""
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        artifact_id = str(payload.get("artifact_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(artifact_id):
            raise EmbeddedLifeError("life.capability.id_invalid")
        scope = self._scope_state(life_id)
        artifact = scope["capabilities"].get(artifact_id)
        if not isinstance(artifact, Mapping) or artifact.get("status") != "published":
            raise EmbeddedLifeError("life.capability.not_found", status=404)
        lineage_id = str(artifact.get("lineage_id") or "")
        pointer = (scope.get("capability_pointers") or {}).get(lineage_id)
        health = pointer.get("health") if isinstance(pointer, Mapping) and isinstance(pointer.get("health"), Mapping) else {}
        pending = health.get("patch_pending") if isinstance(health.get("patch_pending"), Mapping) else None
        if not isinstance(pointer, Mapping) or pending is None:
            raise EmbeddedLifeError("life.capability.patch_nothing_pending", status=409)
        patched = scope["capabilities"].get(str(pending.get("to_artifact_id") or ""))
        if not isinstance(patched, Mapping):
            raise EmbeddedLifeError("life.capability.patch_missing", status=404)
        verification = self._capability_verify_patch(patched)
        pointers_before = deepcopy(scope["capability_pointers"])
        try:
            updated, applied, reason = settle_patch(
                pointer,
                verification,
                now_ms=time.time_ns() // 1_000_000,
                max_patch_rounds=DEFAULT_MAX_PATCH_ROUNDS,
            )
            scope["capability_pointers"][lineage_id] = updated
            if applied:
                self._mark_capability_workspace_status(patched, updated)
            elif reason == "degraded":
                self._mark_capability_workspace_status(artifact, updated)
            event = self.system.journal.append(
                life_id,
                "capability.patch_settled",
                {
                    "artifact_id": artifact_id,
                    "patched_artifact_id": str(patched.get("artifact_id") or ""),
                    "applied": applied,
                    "reason": reason,
                    "verification": verification,
                    "pointer": updated,
                },
                actor=str(payload.get("actor") or "life_health"),
                idempotency_key=(
                    f"capability.patch_settle:{artifact_id}:"
                    f"{patched.get('artifact_id')}:{reason}"
                ),
            )
            self._persist(life_id)
        except Exception:
            scope["capability_pointers"] = pointers_before
            raise
        self._notify_life_learning_post_commit(
            life_id=life_id,
            event=event,
            artifact=patched if applied else artifact,
            status="degraded" if reason == "degraded" else "patch_settled",
        )
        return {
            "ok": True,
            "applied": applied,
            "reason": reason,
            "verification": verification,
            "pointer": deepcopy(updated),
            "event": event,
        }

    def _capability_reactivate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """用户手动重新激活已自动降级的能力（仅 user 可操作）。"""
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        artifact_id = str(payload.get("artifact_id") or "").strip()
        actor = str(payload.get("actor") or "user").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(artifact_id):
            raise EmbeddedLifeError("life.capability.id_invalid")
        scope = self._scope_state(life_id)
        artifact = scope["capabilities"].get(artifact_id)
        if not isinstance(artifact, Mapping) or artifact.get("status") != "published":
            raise EmbeddedLifeError("life.capability.not_found", status=404)
        lineage_id = str(artifact.get("lineage_id") or "")
        pointer = (scope.get("capability_pointers") or {}).get(lineage_id)
        pointers_before = deepcopy(scope["capability_pointers"])
        try:
            updated = reactivate_pointer(
                pointer,
                actor=actor,
                now_ms=time.time_ns() // 1_000_000,
            )
            scope["capability_pointers"][lineage_id] = updated
            self._mark_capability_workspace_status(artifact, updated)
            event = self.system.journal.append(
                life_id,
                "capability.reactivated",
                {"artifact_id": artifact_id, "pointer": updated},
                actor=actor,
                idempotency_key=f"capability.reactivate:{artifact_id}:{actor}",
            )
            self._persist(life_id)
        except ValueError as exc:
            raise EmbeddedLifeError("life.capability.reactivate_invalid", status=409) from exc
        except Exception:
            scope["capability_pointers"] = pointers_before
            raise
        self._notify_life_learning_post_commit(
            life_id=life_id,
            event=event,
            artifact=artifact,
            status="activated",
        )
        return {"ok": True, "capability": deepcopy(artifact), "pointer": deepcopy(updated), "event": event}

    def _capability_outcome_report(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """显式上报一次能力执行结果（外部执行路径信号源，幂等）。"""
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        artifact_id = str(payload.get("artifact_id") or "").strip()
        outcome_id = str(payload.get("outcome_id") or "").strip()
        outcome = str(payload.get("outcome") or "").strip().casefold()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(artifact_id):
            raise EmbeddedLifeError("life.capability.id_invalid")
        if not outcome_id:
            raise EmbeddedLifeError("life.capability.outcome_id_required", status=400)
        if outcome not in {"success", "failure"}:
            raise EmbeddedLifeError("life.capability.outcome_invalid", status=400)
        scope = self._scope_state(life_id)
        artifact = scope["capabilities"].get(artifact_id)
        if not isinstance(artifact, Mapping) or artifact.get("status") != "published":
            raise EmbeddedLifeError("life.capability.not_found", status=404)
        lineage_id = str(artifact.get("lineage_id") or "")
        pointer = (scope.get("capability_pointers") or {}).get(lineage_id)
        pointers_before = deepcopy(scope["capability_pointers"])
        try:
            updated, action, reason = ingest_outcome(
                pointer,
                {
                    "outcome_id": outcome_id,
                    "artifact_id": artifact_id,
                    "outcome": outcome,
                    "occurred_at_ms": int(payload.get("occurred_at_ms") or time.time_ns() // 1_000_000),
                },
                now_ms=time.time_ns() // 1_000_000,
                max_consecutive_failures=DEFAULT_MAX_CONSECUTIVE_FAILURES,
                max_patch_rounds=DEFAULT_MAX_PATCH_ROUNDS,
            )
            scope["capability_pointers"][lineage_id] = updated
            if reason in {"duplicate", "stale_version"}:
                # 幂等路径不重复写 journal：同一 outcome_id 只留一条权威记录。
                event = None
            else:
                event = self.system.journal.append(
                    life_id,
                    "capability.outcome",
                    {"artifact_id": artifact_id, "outcome": outcome, "action": action, "reason": reason},
                    actor=str(payload.get("actor") or "life_health"),
                    idempotency_key=f"capability.outcome:{outcome_id}",
                )
            self._persist(life_id)
        except Exception:
            scope["capability_pointers"] = pointers_before
            raise
        if event is not None and str(updated.get("status") or "") == "degraded":
            self._notify_life_learning_post_commit(
                life_id=life_id,
                event=event,
                artifact=artifact,
                status="degraded",
            )
        return {"ok": True, "action": action, "reason": reason, "pointer": deepcopy(updated), "event": event}

    def _schedule_capability_health_decision(self, *, life_id: str) -> None:
        """自动补丁调度：连续失败达标的能力由模型起草补丁并过验证门。

        模型调用在后台线程执行（同学习决策模式），不阻塞心跳；编译、验证门
        与指针结算在锁内完成，失败不会留下半写状态。
        """
        scope_state = self._scope_state(life_id)
        scheduler = scope_state.setdefault("scheduler", {})
        now_ms = time.time_ns() // 1_000_000
        if bool(scheduler.get("capability_health_inflight")):
            return
        if now_ms - int(scheduler.get("last_capability_health_at_ms") or 0) < 600_000:
            return
        if not callable(getattr(self, "_capability_patch_decider", None)):
            return
        scheduler["capability_health_inflight"] = True
        scheduler["last_capability_health_at_ms"] = now_ms
        self._persist(life_id)

        def worker() -> None:
            try:
                with self._lock:
                    scope = self._scope_state(life_id)
                    targets: list[dict[str, Any]] = []
                    for pointer in scope.get("capability_pointers", {}).values():
                        if not isinstance(pointer, Mapping) or pointer.get("status") != "active":
                            continue
                        health = pointer.get("health") if isinstance(pointer.get("health"), Mapping) else {}
                        if (
                            int(health.get("consecutive_failures") or 0) >= DEFAULT_MAX_CONSECUTIVE_FAILURES
                            and not health.get("patch_pending")
                            and int(health.get("patch_rounds") or 0) < DEFAULT_MAX_PATCH_ROUNDS
                        ):
                            artifact = scope["capabilities"].get(str(pointer.get("current_artifact_id") or ""))
                            if isinstance(artifact, Mapping):
                                targets.append({
                                    "life_id": life_id,
                                    "artifact": deepcopy(dict(artifact)),
                                    "pointer": deepcopy(dict(pointer)),
                                })
                for target in targets:
                    artifact = target["artifact"]
                    material = self._capability_health_material(
                        life_id=life_id,
                        artifact=artifact,
                        pointer=target["pointer"],
                        scope=self._scope_state(life_id),
                    )
                    decision = self._capability_patch_decider(material)
                    if not isinstance(decision, Mapping):
                        continue
                    with self._lock:
                        proposed = self._capability_patch_propose(
                            {"life_id": life_id, "artifact_id": artifact["artifact_id"], "actor": "life_health"},
                            decision=decision,
                        )
                        if proposed.get("ok") is True:
                            self._capability_patch_settle(
                                {
                                    "life_id": life_id,
                                    "artifact_id": artifact["artifact_id"],
                                    "actor": "life_health",
                                }
                            )
            except Exception:
                # 补丁调度失败不得打断心跳；错误留在健康档案的下次尝试。
                return
            finally:
                with self._lock:
                    state = self._scope_state(life_id).setdefault("scheduler", {})
                    state["capability_health_inflight"] = False
                    self._persist(life_id)

        threading.Thread(target=worker, name="tiangong-life-capability-health", daemon=True).start()

    def _artifact_action_catalog(self) -> list[dict[str, Any]]:
        provider = self._artifact_action_catalog_provider
        if not callable(provider):
            return []
        value = provider()
        if isinstance(value, Mapping):
            value = value.get("actions") or value.get("tools") or []
        if not isinstance(value, (list, tuple)):
            raise ArtifactExecutorError("artifact.action_catalog.invalid")
        return [dict(item) for item in value if isinstance(item, Mapping)]

    def _build_learning_artifact(self, learning: Mapping[str, Any], scope: Mapping[str, Any]) -> dict[str, Any]:
        update_of = str(learning.get("update_of") or "")
        capabilities = scope.get("capabilities") if isinstance(scope.get("capabilities"), Mapping) else {}
        previous = capabilities.get(update_of) if update_of else None
        try:
            artifact = compile_artifact(
                learning,
                action_catalog=self._artifact_action_catalog(),
                previous_artifact=previous if isinstance(previous, Mapping) else None,
            )
            bundle_path = persist_artifact_bundle(self.paths.artifact_root, artifact)
            return {"status": "built", "artifact": artifact, "bundle_path": str(bundle_path), "error_code": ""}
        except (ArtifactExecutorError, TypeError, ValueError) as exc:
            return {"status": "failed", "artifact": None, "error_code": str(exc)}

    def _materialize_learning_preview(self, draft: dict[str, Any], activity_scope: Mapping[str, Any]) -> None:
        """Port old source/research/synthesis semantics into new draft authority."""
        preview = execute_learning_preview(
            draft,
            activity_scope=activity_scope,
            researcher=self._learning_researcher,
            synthesizer=self._learning_synthesizer,
        )
        patch = preview.get("patch") if isinstance(preview.get("patch"), Mapping) else {}
        for key in ("title", "summary"):
            if isinstance(patch.get(key), str) and patch[key].strip():
                draft[key] = patch[key].strip()
        if isinstance(patch.get("draft_artifact"), Mapping):
            draft["draft_artifact"] = deepcopy(dict(patch["draft_artifact"]))
        draft["learning_execution"] = preview
        draft["learning_evidence"] = deepcopy(preview.get("evidence") or {})

    def _familiarity_material(self, scope: Mapping[str, Any]) -> dict[str, Any]:
        """How well she knows the user, from memory and interaction evidence.

        口吻随相处深度变化：新识 → 渐熟 → 熟悉 → 很熟。互动轮次为主信号，
        记忆条数与主动分享次数为辅，全部来自权威状态，不凭空捏造。
        """
        temperament = scope.get("temperament") if isinstance(scope.get("temperament"), Mapping) else {}
        turns = int(temperament.get("completed_turn_evidence") or 0)
        memories = sum(
            1
            for row in (scope.get("memories") or {}).values()
            if isinstance(row, Mapping) and str(row.get("status") or "active") != "deleted"
        )
        proactive = len(scope.get("proactive_chats") or [])
        score = turns + memories // 2 + proactive
        if score >= 120:
            level, level_zh = "close", "很熟"
        elif score >= 40:
            level, level_zh = "familiar", "熟悉"
        elif score >= 10:
            level, level_zh = "warming", "渐熟"
        else:
            level, level_zh = "new", "新识"
        return {
            "level": level,
            "level_zh": level_zh,
            "interaction_count": turns,
            "memory_count": memories,
            "proactive_count": proactive,
        }

    def _compose_learning_share_text(self, record: Mapping[str, Any], *, fallback: str) -> str:
        """Return the share copy for a learning report.

        Uses the gateway-installed model writer when available; any failure
        (missing writer, exception, empty result) degrades to the deterministic
        template so a copywriting problem can never break publication.  Model
        output is redacted to the same standard as the daily life summary.
        """
        writer = self._learning_share_writer
        if not callable(writer):
            return fallback
        life_id = str(record.get("life_id") or "")
        material = {
            "share_request": True,
            "title": str(record.get("title") or ""),
            "target": str(record.get("target") or "knowledge"),
            "artifact_id": str(record.get("artifact_id") or ""),
        }
        try:
            soul = self._soul()
            material["persona_name"] = str(soul.get("name") or soul.get("display_name") or "")
        except Exception:
            pass
        try:
            scope = self._scope_state(life_id) if life_id else self._scope_state()
            material["familiarity"] = self._familiarity_material(scope)
            affect = scope.get("affect") if isinstance(scope.get("affect"), Mapping) else {}
            material["emotion"] = {
                "primary": str(affect.get("primary_emotion") or "calm"),
                "primary_zh": str(affect.get("primary_emotion_zh") or "平静"),
                "intensity_milli": int(affect.get("intensity_milli") or 0),
            }
        except Exception:
            pass
        plan = record.get("learning_plan") if isinstance(record.get("learning_plan"), list) else []
        if plan:
            material["learning_plan"] = [str(step)[:120] for step in plan[:4] if str(step).strip()]
        execution = record.get("execution")
        if isinstance(execution, Mapping):
            artifact = execution.get("artifact")
            if isinstance(artifact, Mapping):
                for key in ("summary", "description"):
                    value = str(artifact.get(key) or "").strip()
                    if value:
                        material["summary"] = value
                        break
        try:
            text = str(writer(material) or "").strip()
        except Exception:
            return fallback
        if not text:
            return fallback
        text = self._redact_sensitive_text(text)[:1000].strip()
        return text or fallback

    def _learning_report(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Return a durable publication report without legacy proactive delivery.

        Learning publication still records deterministic metadata in the
        journal, but the pre-P15 producer no longer writes a chat message.
        """
        target = str(record.get("target") or "knowledge")
        title = str(record.get("title") or "learning")
        detail = f"学习完成：{title}。已写入{'知识库' if target == 'knowledge' else target}。"
        return {
            "message_id": "learnmsg_" + canonical_sha256(
                {
                    "learning_id": record.get("learning_id"),
                    "status": record.get("status"),
                }
            )[:40],
            "kind": "learning_report",
            "learning_id": record.get("learning_id"),
            "text": detail,
            "created_at": utc_now(),
            "read": True,
            "delivery": "legacy_proactive_frozen",
            "suppressed": True,
            "reason_code": "life.proactive.legacy_producer_frozen",
        }

    def _learning_draft(self, payload: Mapping[str, Any], *, source: str = "autonomous") -> dict[str, Any]:
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id):
            raise EmbeddedLifeError("life.identity.id_invalid", status=409)
        decision = payload.get("decision") if isinstance(payload.get("decision"), Mapping) else payload
        scope = self._scope_state(life_id)
        activity_scope = build_activity_scope(
            life_id=life_id,
            soul=self._soul(),
            scope=scope,
            derivation_store=(
                self.authority_store
                if self.authority_store is not None
                else None
            ),
        )
        try:
            draft = build_draft(life_id=life_id, scope=activity_scope, decision=decision, source=source)
        except ValueError as exc:
            raise EmbeddedLifeError("life.learning.decision_invalid") from exc
        prior = scope["learning"].get(draft["learning_id"])
        if isinstance(prior, Mapping):
            if str(prior.get("status") or "") == "discarded" and source != "user_direct":
                return {"ok": True, "suppressed": True, "reason_code": "life.learning.previously_declined", "learning": deepcopy(prior), "activity_scope": activity_scope}
            return {"ok": True, "duplicate": True, "learning": deepcopy(prior), "activity_scope": activity_scope}
        self._materialize_learning_preview(draft, activity_scope)
        draft["draft_sha256"] = canonical_sha256({"domain": "tiangong.life.learning-draft.v1", "draft": draft})
        draft["execution"] = self._build_learning_artifact(draft, scope)
        built = draft["execution"].get("artifact")
        if isinstance(built, Mapping):
            draft["effective_risk_level"] = built.get("risk_level")
        else:
            # A card that can never be built must never reach the user as a
            # confirmable preview: confirmation would strand it forever.
            detail = str(draft["execution"].get("error_code") or "artifact.build_failed")
            raise EmbeddedLifeError(f"life.learning.artifact_build_failed:{detail}", status=409)
        scope["learning"][draft["learning_id"]] = draft
        try:
            event = self.system.journal.append(
                life_id, "learning.draft_created", {"learning": draft, "activity_scope_sha256": activity_scope["scope_sha256"]},
                actor="life_learning", idempotency_key=f"learning.draft:{draft['learning_id']}",
            )
            self._persist(life_id)
        except Exception:
            scope["learning"].pop(draft["learning_id"], None)
            raise
        return {"ok": True, "learning": deepcopy(draft), "activity_scope": activity_scope, "event": event}

    def _learning_publish(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        learning_id = str(payload.get("learning_id") or payload.get("card_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(learning_id):
            raise EmbeddedLifeError("life.learning.id_invalid")
        scope = self._scope_state(life_id)
        current = scope["learning"].get(learning_id)
        if not isinstance(current, Mapping):
            raise EmbeddedLifeError("life.learning.not_found", status=404)
        execution = current.get("execution") if isinstance(current.get("execution"), Mapping) else {}
        compiled = execution.get("artifact") if isinstance(execution.get("artifact"), Mapping) else None
        if str(execution.get("status") or "") != "built" or compiled is None:
            raise EmbeddedLifeError("life.learning.artifact_not_buildable", status=409)
        materialization = current.get("learning_execution") if isinstance(current.get("learning_execution"), Mapping) else {}
        if str(materialization.get("status") or "") not in {"completed", "completed_with_warnings"}:
            raise EmbeddedLifeError("life.learning.materialization_not_complete", status=409)
        try:
            published, _legacy_artifact = publish_draft(current, capabilities=scope["capabilities"])
            artifact = publish_artifact(compiled)
        except (ArtifactExecutorError, ValueError) as exc:
            raise EmbeddedLifeError("life.learning.publish_not_authorized", status=409) from exc
        previous = deepcopy(current)
        capabilities_before = deepcopy(scope["capabilities"])
        capability_pointers_before = deepcopy(scope["capability_pointers"])
        knowledge_before = deepcopy(scope["knowledge"])
        try:
            publisher = self._artifact_publisher
            publication = publisher(artifact) if callable(publisher) else {"publisher": "life_local_projection"}
            if not isinstance(publication, Mapping):
                raise EmbeddedLifeError("life.learning.publisher_invalid", status=503)
            mapper = self._capability_workspace_mapper
            if callable(mapper):
                try:
                    mapped = mapper(artifact)
                    if isinstance(mapped, Mapping) and mapped.get("workspace_path"):
                        publication = {
                            **dict(publication),
                            "workspace_path": str(mapped["workspace_path"]),
                        }
                except Exception:
                    pass
            bundle_path = persist_artifact_bundle(
                self.paths.artifact_root,
                compiled,
                publication=publication,
            )
            published["artifact_id"] = artifact["artifact_id"]
            published["effective_risk_level"] = artifact["risk_level"]
            published["execution"] = {
                "status": "published",
                "artifact": artifact,
                "bundle_path": str(bundle_path),
                "publication": deepcopy(dict(publication)),
            }
            scope["learning"][learning_id] = published
            if artifact["kind"] in {"skill", "tool"}:
                scope["capabilities"][artifact["artifact_id"]] = {
                    **artifact,
                    "origin": "life_learning",
                    "publication": deepcopy(dict(publication)),
                }
                self._set_capability_pointer(
                    life_id=life_id,
                    scope=scope,
                    artifact=artifact,
                    prior_artifact_id=str(artifact.get("previous_artifact_id") or ""),
                    status="pending",
                )
            else:
                scope["knowledge"][artifact["artifact_id"]] = {
                    **artifact,
                    "knowledge_document_id": str(publication.get("knowledge_document_id") or ""),
                    "publication": deepcopy(dict(publication)),
                }
            report = self._learning_report(published)
            event = self.system.journal.append(
                life_id, "learning.published", {"learning": published, "artifact": artifact, "report": report},
                actor=str(payload.get("actor") or "life_learning"), idempotency_key=f"learning.publish:{learning_id}",
            )
            self._persist(life_id)
        except Exception:
            scope["learning"][learning_id] = previous
            scope["capabilities"] = capabilities_before
            scope["capability_pointers"] = capability_pointers_before
            scope["knowledge"] = knowledge_before
            raise
        self._notify_life_learning_post_commit(
            life_id=life_id,
            event=event,
            artifact=artifact,
            status="published" if artifact["kind"] == "knowledge" else "pending_activation",
            learning=published,
        )
        return {"ok": True, "learning": deepcopy(published), "artifact": artifact, "report": report, "event": event}

    def _learning_confirm(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        learning_id = str(payload.get("learning_id") or payload.get("card_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(learning_id):
            raise EmbeddedLifeError("life.learning.id_invalid")
        scope = self._scope_state(life_id)
        current = scope["learning"].get(learning_id)
        if not isinstance(current, Mapping):
            raise EmbeddedLifeError("life.learning.not_found", status=404)
        try:
            approved = confirm_draft(current, draft_sha256=str(payload.get("draft_sha256") or ""))
        except ValueError as exc:
            raise EmbeddedLifeError("life.learning.confirm_invalid", status=409) from exc
        scope["learning"][learning_id] = approved
        self.system.journal.append(life_id, "learning.confirmed", {"learning": approved}, actor=str(payload.get("actor") or "user"), idempotency_key=f"learning.confirm:{learning_id}")
        self._persist(life_id)
        # Confirmation is deliberately the only transition users need: the
        # reviewed preview is now written/published in the same authority turn.
        try:
            return self._learning_publish({"life_id": life_id, "learning_id": learning_id, "actor": payload.get("actor") or "user"})
        except Exception as exc:
            # Keep the card visible as user-confirmed and let the maintenance
            # tick retry publication; never leave a silently dangling card.
            record = scope["learning"].get(learning_id)
            if isinstance(record, Mapping):
                record["last_publish_error"] = str(getattr(exc, "code", "") or exc)[:200]
                self._persist(life_id)
            raise

    def _learning_discard(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        learning_id = str(payload.get("learning_id") or payload.get("card_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(learning_id):
            raise EmbeddedLifeError("life.learning.id_invalid")
        scope = self._scope_state(life_id)
        current = scope["learning"].get(learning_id)
        if not isinstance(current, Mapping):
            raise EmbeddedLifeError("life.learning.not_found", status=404)
        try:
            discarded = discard_draft(current, reason=str(payload.get("reason") or "user_declined"))
        except ValueError as exc:
            raise EmbeddedLifeError("life.learning.discard_invalid", status=409) from exc
        scope["learning"][learning_id] = discarded
        event = self.system.journal.append(life_id, "learning.discarded", {"learning": discarded}, actor=str(payload.get("actor") or "user"), idempotency_key=f"learning.discard:{learning_id}")
        self._persist(life_id)
        return {"ok": True, "learning": deepcopy(discarded), "event": event}

    def _recover_approved_learning_cards(self, *, life_id: str) -> None:
        """Retry publication for user-confirmed cards whose first publish
        attempt failed.  Runs inside the maintenance tick so a transient
        failure (or a fixed artifact build) no longer strands an approved
        card forever."""
        try:
            with self._lock:
                scope = self._scope_state(life_id)
                learning = scope.get("learning")
                if not isinstance(learning, Mapping):
                    return
                now_ms = time.time_ns() // 1_000_000
                settings = scope.get("settings") if isinstance(scope.get("settings"), Mapping) else {}
                risk_rank = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4}
                for learning_id, record in list(learning.items()):
                    if not isinstance(record, Mapping) or str(record.get("status") or "") != "approved":
                        continue
                    retry = record.get("publish_retry") if isinstance(record.get("publish_retry"), Mapping) else {}
                    attempts = int(retry.get("count") or 0)
                    last_ms = int(retry.get("last_attempt_at_ms") or 0)
                    if attempts >= 5:
                        continue
                    if last_ms and now_ms - last_ms < 600_000:
                        continue
                    user_authorized = bool(record.get("requires_confirmation"))
                    if not user_authorized:
                        effective_risk = str(record.get("effective_risk_level") or record.get("risk_level") or "A0")
                        risk_allowed = risk_rank.get(effective_risk, 99) <= risk_rank.get(
                            str(settings.get("autonomous_risk_max") or "A0"),
                            0,
                        )
                        if not risk_allowed:
                            record["last_publish_error"] = "life.learning.autonomous_risk_limit"
                            continue
                    record["publish_retry"] = {"count": attempts + 1, "last_attempt_at_ms": now_ms}
                    record["publish_retry_at"] = utc_now()
                    execution = record.get("execution") if isinstance(record.get("execution"), Mapping) else {}
                    materialization = record.get("learning_execution") if isinstance(record.get("learning_execution"), Mapping) else {}
                    if str(execution.get("status") or "") != "built":
                        if str(materialization.get("status") or "") not in {"completed", "completed_with_warnings"}:
                            record["last_publish_error"] = "life.learning.materialization_not_complete"
                            self._persist(life_id)
                            continue
                        rebuilt = self._build_learning_artifact(record, scope)
                        record["execution"] = rebuilt
                        if str(rebuilt.get("status") or "") != "built":
                            record["last_publish_error"] = str(rebuilt.get("error_code") or "life.learning.artifact_not_buildable")
                            self._persist(life_id)
                            continue
                        self._persist(life_id)
                    try:
                        self._learning_publish({"life_id": life_id, "learning_id": learning_id, "actor": "life_scheduler"})
                    except Exception as exc:
                        record = scope["learning"].get(learning_id)
                        if isinstance(record, Mapping):
                            record["last_publish_error"] = str(getattr(exc, "code", "") or exc)[:200]
                            if attempts + 1 >= 5:
                                record["can_discard_learning"] = True
                                record["publish_retry_exhausted"] = True
                            self._persist(life_id)
                    else:
                        record = scope["learning"].get(learning_id)
                        if isinstance(record, Mapping):
                            record.pop("last_publish_error", None)
                            record.pop("publish_retry", None)
                            record.pop("publish_retry_at", None)
                            record.pop("publish_retry_exhausted", None)
                            self._persist(life_id)
        except Exception:
            # Recovery must never break the heartbeat tick.
            return

    def _sync_life_capability_workspace_zone(self, *, life_id: str) -> None:
        """Ensure every published life skill/tool has its workspace-zone file.

        Runs inside the maintenance tick so a newly published capability is
        mirrored into the workspace automatically, and a deleted/missing
        mirror is restored from the authoritative artifact record.
        """
        try:
            mapper = self._capability_workspace_mapper
            if not callable(mapper):
                return
            with self._lock:
                scope = self._scope_state(life_id)
                capabilities = scope.get("capabilities")
                if not isinstance(capabilities, Mapping):
                    return
                changed = False
                for artifact in capabilities.values():
                    if (
                        not isinstance(artifact, Mapping)
                        or artifact.get("status") != "published"
                        or artifact.get("kind") not in {"skill", "tool"}
                    ):
                        continue
                    try:
                        mapped = mapper(artifact)
                    except Exception:
                        continue
                    if not isinstance(mapped, Mapping) or not mapped.get("workspace_path"):
                        continue
                    publication = artifact.get("publication") if isinstance(artifact.get("publication"), Mapping) else {}
                    if str(publication.get("workspace_path") or "") != str(mapped["workspace_path"]):
                        artifact["publication"] = {**dict(publication), "workspace_path": str(mapped["workspace_path"])}
                        changed = True
                if changed:
                    self._persist(life_id)
        except Exception:
            # Zone mirroring must never break the heartbeat tick.
            return

    @staticmethod
    def _resolve_artifact_template(value: Any, inputs: Mapping[str, Any]) -> Any:
        if isinstance(value, Mapping):
            return {str(key): EmbeddedLifeRuntime._resolve_artifact_template(item, inputs) for key, item in value.items()}
        if isinstance(value, list):
            return [EmbeddedLifeRuntime._resolve_artifact_template(item, inputs) for item in value]
        if not isinstance(value, str):
            return deepcopy(value)

        def lookup(path: str) -> Any:
            current: Any = inputs
            for part in path.split("."):
                if not isinstance(current, Mapping) or part not in current:
                    raise EmbeddedLifeError("life.capability.input_missing", status=409)
                current = current[part]
            return deepcopy(current)

        full = _INPUT_TEMPLATE.fullmatch(value)
        if full:
            return lookup(full.group(1))

        def replace(match: re.Match[str]) -> str:
            replacement = lookup(match.group(1))
            if isinstance(replacement, (dict, list)):
                raise EmbeddedLifeError("life.capability.input_scalar_required", status=409)
            return str(replacement)

        return _INPUT_TEMPLATE.sub(replace, value)

    def _capability_invoke(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Run an active learned Skill/Tool through the fixed gateway invoker.

        No generated code is imported.  Each persisted step is replayed only
        through its bound, release-provided action id.
        """
        life_id = str(payload.get("life_id") or self._active().get("life_id") or "").strip()
        artifact_id = str(payload.get("artifact_id") or "").strip()
        if not _OPAQUE.fullmatch(life_id) or not _OPAQUE.fullmatch(artifact_id):
            raise EmbeddedLifeError("life.capability.id_invalid")
        scope = self._scope_state(life_id)
        artifact = scope["capabilities"].get(artifact_id)
        if not isinstance(artifact, Mapping) or artifact.get("status") != "published":
            raise EmbeddedLifeError("life.capability.not_found", status=404)
        if artifact.get("kind") not in {"skill", "tool"}:
            raise EmbeddedLifeError("life.capability.not_invokable", status=409)
        supplied_sha = str(payload.get("artifact_sha256") or "").strip()
        if supplied_sha and supplied_sha != artifact.get("artifact_sha256"):
            raise EmbeddedLifeError("life.capability.digest_mismatch", status=409)
        lineage_id = str(artifact.get("lineage_id") or "")
        pointer = (scope.get("capability_pointers") or {}).get(lineage_id)
        if not isinstance(pointer, Mapping) or pointer.get("status") != "active" or pointer.get("current_artifact_id") != artifact_id:
            raise EmbeddedLifeError("life.capability.not_active", status=409)
        invoker = self._artifact_invoker
        if not callable(invoker):
            raise EmbeddedLifeError("life.capability.gateway_unavailable", status=503)
        inputs = payload.get("inputs") if isinstance(payload.get("inputs"), Mapping) else {}
        spec = artifact.get("skill_spec") if isinstance(artifact.get("skill_spec"), Mapping) else {}
        steps = spec.get("steps") if isinstance(spec.get("steps"), list) else []
        if not steps:
            raise EmbeddedLifeError("life.capability.steps_invalid", status=409)
        execution_id = "caprun_" + canonical_sha256({
            "artifact_id": artifact_id,
            "artifact_sha256": artifact.get("artifact_sha256"),
            "inputs": inputs,
            "nonce": str(payload.get("request_id") or uuid.uuid4().hex),
        })[:40]
        existing = scope["executions"].get(execution_id)
        if isinstance(existing, Mapping):
            return {"ok": True, "replayed": True, "execution": deepcopy(existing)}
        records: list[dict[str, Any]] = []
        completed = True
        for position, raw_step in enumerate(steps):
            if not isinstance(raw_step, Mapping):
                raise EmbeddedLifeError("life.capability.step_invalid", status=409)
            action_id = str(raw_step.get("action_id") or "").strip()
            try:
                arguments = self._resolve_artifact_template(raw_step.get("arguments_template") or {}, inputs)
            except EmbeddedLifeError:
                raise
            if not isinstance(arguments, Mapping):
                raise EmbeddedLifeError("life.capability.arguments_invalid", status=409)
            invocation_context = {
                "life_id": life_id,
                "artifact_id": artifact_id,
                "artifact_sha256": str(artifact.get("artifact_sha256") or ""),
                "execution_id": execution_id,
                "step_id": str(raw_step.get("step_id") or f"step_{position + 1}"),
            }
            try:
                result = invoker(action_id, dict(arguments), invocation_context)
            except Exception as exc:
                result = {"ok": False, "error_code": f"gateway:{type(exc).__name__}"}
            if not isinstance(result, Mapping):
                result = {"ok": False, "error_code": "gateway.result_invalid"}
            # Execution tickets and grants are signed contracts, so their
            # canonical form correctly forbids floats.  A completed tool
            # receipt is *not* a signed contract, though: system telemetry can
            # legitimately include finite float metrics.  Persist it as strict
            # normal JSON instead of reclassifying a successful tool call as a
            # Life failure merely because its result is not signable.
            try:
                serialized = json.dumps(
                    dict(result),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                result = json.loads(serialized.decode("utf-8"))
            except (TypeError, ValueError, UnicodeDecodeError):
                result = {"ok": False, "error_code": "gateway.result_not_json"}
                serialized = canonical_json_bytes(result)
            if len(serialized) > _MAX_TASK_RESULT_BYTES:
                result = {"ok": False, "error_code": "gateway.result_too_large"}
            succeeded = result.get("ok") is True and str(result.get("zhuangtai") or "wancheng") not in {"cuowu", "shibai", "failed"}
            record = {
                "position": position + 1,
                "step_id": str(raw_step.get("step_id") or f"step_{position + 1}"),
                "action_id": action_id,
                "ok": succeeded,
                "result": deepcopy(dict(result)),
            }
            records.append(record)
            if not succeeded and str(raw_step.get("on_failure") or "stop") != "continue":
                completed = False
                break
        execution = {
            "schema": "tiangong.life.composite-execution.v1",
            "execution_id": execution_id,
            "life_id": life_id,
            "artifact_id": artifact_id,
            "artifact_sha256": artifact.get("artifact_sha256"),
            "status": "completed" if completed else "failed",
            "steps": records,
            "created_at": utc_now(),
        }
        before = deepcopy(scope["executions"])
        pointer_before = deepcopy((scope.get("capability_pointers") or {}).get(lineage_id) or {})
        try:
            scope["executions"][execution_id] = execution
            # 执行结果进入健康档案（幂等：execution_id 即 outcome_id）。
            # 这是自动降级/补丁触发的真实信号源。
            updated_pointer, _health_action, _health_reason = ingest_outcome(
                pointer_before,
                {
                    "outcome_id": execution_id,
                    "artifact_id": artifact_id,
                    "outcome": "success" if completed else "failure",
                    "occurred_at_ms": time.time_ns() // 1_000_000,
                },
                now_ms=time.time_ns() // 1_000_000,
                max_consecutive_failures=DEFAULT_MAX_CONSECUTIVE_FAILURES,
                max_patch_rounds=DEFAULT_MAX_PATCH_ROUNDS,
            )
            scope["capability_pointers"][lineage_id] = updated_pointer
            event = self.system.journal.append(
                life_id, "capability.executed", {"execution": execution},
                actor=str(payload.get("actor") or "life_composite"), idempotency_key=f"capability.execute:{execution_id}",
            )
            self._persist(life_id)
        except Exception:
            scope["executions"] = before
            scope["capability_pointers"][lineage_id] = pointer_before
            raise
        return {
            "ok": completed,
            "execution": deepcopy(execution),
            "pointer": deepcopy(updated_pointer),
            "event": event,
        }

    def _execution_recover(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request_id = str(payload.get("request_id") or "").strip()
        cycle_id = str(payload.get("cycle_id") or "").strip()
        record = self._scope_state()["executions"].get(request_id or cycle_id)
        if not isinstance(record, dict):
            return {"ok": True, "found": False, "request_id": request_id, "cycle_id": cycle_id}
        return {"ok": True, "found": True, "execution": deepcopy(record)}

    def _activity_query(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Return a bounded, read-only Life activity ledger for model queries."""

        relative_day = str(payload.get("relative_day") or "").strip().lower()
        requested_date = str(payload.get("date") or "").strip()
        today_date = datetime.now(timezone.utc).date()
        if relative_day in {"today", "今天"}:
            query_date = today_date
        elif relative_day in {"yesterday", "昨天"}:
            query_date = today_date - timedelta(days=1)
        elif requested_date:
            try:
                query_date = datetime.strptime(requested_date, "%Y-%m-%d").date()
            except ValueError as exc:
                raise EmbeddedLifeError("life.activity.date_invalid", status=400) from exc
        else:
            query_date = today_date
        if query_date > today_date:
            raise EmbeddedLifeError("life.activity.future_date_forbidden", status=400)
        try:
            limit = int(payload.get("limit") or 30)
        except (TypeError, ValueError, OverflowError) as exc:
            raise EmbeddedLifeError("life.activity.limit_invalid", status=400) from exc
        limit = max(1, min(limit, 100))
        status_filter = str(payload.get("status") or "").strip().lower()
        query_day = query_date.isoformat()
        scope = self._scope_state()
        identity = self._active()
        tasks = [
            deepcopy(row)
            for row in scope.get("autonomy", {}).get("tasks", {}).values()
            if isinstance(row, Mapping)
            and record_day(row) == query_day
            and (not status_filter or str(row.get("status") or "").lower() == status_filter)
        ]
        tasks.sort(
            key=lambda row: max(
                int(row.get("updated_at_ms") or 0),
                int(row.get("created_at_ms") or 0),
            ),
            reverse=True,
        )
        projection: list[dict[str, Any]] = []
        for task in tasks[:limit]:
            result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
            reflection = reflection_projection(task)
            projection.append(
                {
                    "task_id": str(task.get("task_id") or ""),
                    "activity": str(task.get("activity_id") or task.get("task_kind") or ""),
                    "title": str(task.get("title") or task.get("objective") or ""),
                    "status": str(task.get("status") or ""),
                    "risk": str(task.get("risk") or task.get("risk_level") or ""),
                    "summary": str(
                        reflection.get("human_summary")
                        or result.get("summary")
                        or ""
                    )[:1600],
                    "next_step": str(result.get("next_step") or "")[:800],
                    "created_at": str(task.get("created_at") or ""),
                    "updated_at": str(task.get("updated_at") or ""),
                    "created_at_ms": int(task.get("created_at_ms") or 0),
                    "updated_at_ms": int(task.get("updated_at_ms") or 0),
                }
            )
        inbox = [
            {
                "message_id": str(row.get("message_id") or row.get("id") or ""),
                "title": str(row.get("title") or ""),
                "message": str(row.get("message") or row.get("text") or "")[:2400],
                "read": bool(row.get("read")),
            }
            for row in scope.get("inbox", [])
            if isinstance(row, Mapping)
            and record_day(row) == query_day
            and str(row.get("kind") or "") in {
                "daily_summary",
                "daily-summary",
                "daily_life_summary",
            }
        ]
        status_counts: dict[str, int] = {}
        for task in tasks:
            key = str(task.get("status") or "unknown")
            status_counts[key] = status_counts.get(key, 0) + 1
        result: dict[str, Any] = {
            "ok": True,
            "schema": "tiangong.life.activity-query.v1",
            "authority": "embedded_life_runtime",
            "read_only": True,
            "identity": {
                "life_id": str(identity.get("life_id") or ""),
                "name": str(identity.get("display_name") or identity.get("name") or ""),
            },
            "date": query_day,
            "relative_day": (
                "today"
                if query_date == today_date
                else "yesterday"
                if query_date == today_date - timedelta(days=1)
                else "specified"
            ),
            "status_counts": status_counts,
            "total": len(tasks),
            "returned": len(projection),
            "activities": projection,
            "daily_summaries": inbox,
        }
        privacy = (
            scope["settings"].get("privacy")
            if isinstance(scope["settings"].get("privacy"), Mapping)
            else {}
        )
        if bool(privacy.get("redact_llm", True)):
            serialized = json.dumps(result, ensure_ascii=False)
            result = json.loads(self._redact_sensitive_text(serialized))
        return result

    def commit_execution(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Commit one terminal Runtime outcome to the sole Life writer.

        The request identity is the idempotency key.  Replays return the
        original record; conflicting re-use is rejected by the Life kernel.
        """

        status, value, _content_type = self.request(
            "POST",
            "/api/v1/v3/life/execution/commit",
            payload,
            timeout_seconds=30.0,
        )
        if status >= 400 or value.get("ok") is not True:
            raise EmbeddedLifeError(
                str(value.get("error_code") or "life.execution.commit_failed"),
                status=status,
            )
        return value

    def request(
        self,
        method: str,
        target: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> tuple[int, dict[str, Any], str]:
        del timeout_seconds
        verb = str(method).upper()
        path = urlsplit(target).path
        body = dict(payload or {})
        try:
            with self._lock:
                if self._closed or self._closing:
                    raise EmbeddedLifeError("life.runtime.closed", status=503)
                projection_safe_routes = {
                    ("GET", "/health"),
                    ("GET", "/api/v1/v3/life/health"),
                    ("GET", "/ready"),
                    ("GET", "/api/v1/v3/life/contract"),
                    ("GET", "/api/v1/v3/life/journal/verify"),
                    ("GET", "/api/v1/v3/state"),
                    ("GET", "/api/v1/v3/life/panel"),
                    ("POST", "/api/v1/v3/life/activity/query"),
                }
                if self._projection_dirty_reason and (verb, path) not in projection_safe_routes:
                    raise EmbeddedLifeError(self._projection_dirty_reason, status=503)
                if verb == "GET" and path in {"/health", "/api/v1/v3/life/health"}:
                    result = self.health_payload()
                elif verb == "GET" and path == "/ready":
                    status, result = self.ready_payload()
                    return status, result, "application/json; charset=utf-8"
                elif verb == "GET" and path == "/api/v1/v3/life/contract":
                    result = {"ok": True, "api_contract": LIFE_API_CONTRACT, "deployment_mode": self.mode}
                elif verb == "GET" and path == "/api/v1/v3/life/identities":
                    result = {"ok": True, "identities": self.system.identities.list()}
                elif verb == "GET" and path == "/api/v1/v3/life/identity/active":
                    result = {"ok": True, "identity": self._active()}
                elif verb == "GET" and path == "/api/v1/v3/life/identity/audit":
                    result = {"ok": True, "events": self.system.identities.audit_entries()}
                elif verb == "POST" and path == "/api/v1/v3/life/identity/create":
                    identity = self.system.create_identity(
                        str(body.get("name") or "起源"),
                        actor=str(body.get("actor") or "user"),
                    )
                    life_id = str(identity.get("life_id") or "")
                    # New identities begin with an empty journal.  Establish
                    # its signed empty-head before returning success so this
                    # action is also the authoritative point at which chat
                    # becomes available for the new life.
                    self.system.journal.ensure_hashed(life_id)
                    self._scope_state(life_id)
                    self._persist()
                    result = {"ok": True, "identity": identity}
                elif verb == "POST" and path == "/api/v1/v3/life/identity/bind":
                    identity = self.system.identities.bind(
                        Path(str(body.get("root") or "")),
                        name=str(body.get("name") or "起源"),
                        actor=str(body.get("actor") or "user"),
                    )
                    self._scope_state(str(identity.get("life_id") or ""))
                    self._persist()
                    result = {"ok": True, "identity": identity}
                elif verb == "POST" and path == "/api/v1/v3/life/identity/activate":
                    target_life_id = str(body.get("life_id") or "").strip()
                    if not _OPAQUE.fullmatch(target_life_id):
                        raise EmbeddedLifeError("life.identity.id_invalid", status=400)
                    # Verify the target authority before changing the global
                    # active identity.  A corrupted dormant life must not be
                    # activated and only then discovered by readiness probes.
                    self.system.journal.ensure_hashed(target_life_id)
                    if self._reconcile_authoritative_journal(target_life_id):
                        self._persist(target_life_id)
                    if self._reconcile_memory_contract(target_life_id):
                        self._persist(target_life_id)
                    identity = self.system.identities.activate(
                        target_life_id,
                        actor=str(body.get("actor") or "user"),
                    )
                    self._scope_state(str(identity.get("life_id") or ""))
                    self._persist(target_life_id)
                    result = {"ok": True, "identity": identity}
                elif verb == "POST" and path == "/api/v1/v3/life/identity/unbind":
                    life_id = str(body.get("life_id") or "")
                    result = {
                        "ok": True,
                        **self.system.identities.unbind(
                            life_id,
                            actor=str(body.get("actor") or "user"),
                        ),
                    }
                elif verb == "POST" and path == "/api/v1/v3/life/identity/delete":
                    life_id = str(body.get("life_id") or "").strip()
                    if not _OPAQUE.fullmatch(life_id):
                        raise EmbeddedLifeError("life.identity.id_invalid", status=400)
                    deleted = self.system.identities.delete(
                        life_id,
                        actor=str(body.get("actor") or "user"),
                    )
                    identity_states = self._state.get("identity_states")
                    if isinstance(identity_states, dict):
                        identity_states.pop(life_id, None)
                    self._persist()
                    result = {"ok": True, **deleted}
                elif verb == "GET" and path == "/api/v1/v3/state":
                    result = self._state_payload()
                elif verb == "GET" and path == "/api/v1/v3/life/panel":
                    result = self._panel()
                elif verb == "POST" and path == "/api/v1/v3/life/activity/query":
                    result = self._activity_query(body)
                elif verb == "GET" and path == "/api/v1/v3/life/soul":
                    result = {"ok": True, "soul": self._soul()}
                elif verb == "POST" and path == "/api/v1/v3/life/soul/update":
                    updates = body.get("soul") if isinstance(body.get("soul"), Mapping) else body.get("updates")
                    result = {"ok": True, **self.system.update_soul(dict(updates or {}), actor=str(body.get("actor") or "user"))}
                elif verb == "GET" and path == "/api/v1/v3/life/journal/verify":
                    result = self._journal_verify()
                elif verb == "POST" and path in {"/api/v1/v3/life/journal/migrate", "/api/v1/v3/life/projection/rebuild", "/api/v1/v3/life/projection/snapshot"}:
                    result = {"ok": True, "operation": path.rsplit("/", 1)[-1], "dry_run": bool(body.get("dry_run")), "projection_authority": self._projection_authority()}
                elif verb == "GET" and path == "/api/v1/v3/life/memory/stats":
                    result = {"ok": True, **self._memory_stats()}
                elif verb == "POST" and path == "/api/v1/v3/life/memory/candidates":
                    result = self._memory_propose_candidates(body)
                elif verb == "GET" and path == "/api/v1/v3/life/memory/projection-head":
                    result = self._memory_projection_head_payload(body)
                elif verb == "GET" and path == "/api/v1/v3/life/memory/outbox":
                    result = self._memory_outbox_payload(body)
                elif verb == "POST" and path == "/api/v1/v3/life/memory/outbox/ack":
                    result = self._memory_outbox_ack(body)
                elif verb == "POST" and path == "/api/v1/v3/life/memory/assert":
                    result = self._memory_assert(body)
                elif verb == "POST" and path == "/api/v1/v3/life/memory/turn":
                    result = self._memory_record_turn(body)
                elif verb == "POST" and path == "/api/v1/v3/life/memory/correct":
                    result = self._memory_correct(body)
                elif verb == "POST" and path == "/api/v1/v3/life/memory/status":
                    result = self._memory_change_status(body)
                elif verb == "POST" and path == "/api/v1/v3/life/memory/relation":
                    result = self._memory_add_relation(body)
                elif verb == "POST" and path == "/api/v1/v3/life/memory/search":
                    result = self._memory_search(body)
                elif verb == "POST" and path == "/api/v1/v3/life/memory/delete":
                    result = self._memory_delete(body)
                elif verb == "GET" and path == "/api/v1/v3/life/autonomy/tasks":
                    result = self._autonomy_tasks_payload(body)
                elif verb == "POST" and path == "/api/v1/v3/life/autonomy/tick":
                    tick = self._scheduler_tick(str(body.get("reason") or "manual"))
                    result = {"ok": True, **tick, **self._autonomy_tasks_payload(body)}
                elif verb == "POST" and path == "/api/v1/v3/life/autonomy/task/status":
                    result = self._autonomy_change_status(body)
                elif verb == "GET" and path == "/api/v1/v3/life/learning/activity-scope":
                    result = self._activity_scope_payload(body)
                elif verb == "GET" and path == "/api/v1/v3/life/capabilities/overlay":
                    result = self._capability_overlay_payload(body)
                elif verb == "POST" and path == "/api/v1/v3/life/learning/draft":
                    drafted = self._learning_draft(body)
                    # Autonomous A0--A2 knowledge is useful only when it is
                    # committed.  Skill/Tool drafts always arrive here as A3+
                    # and therefore remain awaiting_user and unregistered.
                    if (
                        not drafted.get("suppressed")
                        and not drafted.get("duplicate")
                        and str((drafted.get("learning") or {}).get("status") or "") == "approved"
                    ):
                        result = self._learning_publish({
                            "life_id": body.get("life_id"),
                            "learning_id": (drafted.get("learning") or {}).get("learning_id"),
                            "actor": body.get("actor") or "life_learning",
                        })
                    else:
                        result = drafted
                elif verb == "POST" and path == "/api/v1/v3/life/learning/user-request":
                    drafted = self._learning_draft(body, source="user_direct")
                    if drafted.get("suppressed") or drafted.get("duplicate"):
                        result = drafted
                    else:
                        result = self._learning_publish({
                            "life_id": body.get("life_id"),
                            "learning_id": (drafted.get("learning") or {}).get("learning_id"),
                            "actor": body.get("actor") or "user",
                        })
                elif verb == "POST" and path == "/api/v1/v3/life/memory/rebuild-index":
                    result = {"ok": True, "rebuilt": True, "indexed": self._memory_stats()["total"]}
                elif verb == "GET" and path == "/api/v1/v3/life/affect":
                    result = {"ok": True, "state": deepcopy(self._scope_state()["affect"])}
                elif verb == "GET" and path == "/api/v1/v3/life/temperament":
                    result = {"ok": True, "temperament": self._temperament_projection()}
                elif verb == "POST" and path in {"/api/v1/v3/life/affect/appraise", "/api/v1/v3/life/affect/outcome"}:
                    appraisal = body.get("appraisal") if isinstance(body.get("appraisal"), Mapping) else body
                    affect_state = self._scope_state()["affect"]
                    dimension_override = (
                        affect_state.get("dimension_override")
                        if isinstance(affect_state.get("dimension_override"), Mapping)
                        else {}
                    )
                    dimension_override = dict(dimension_override)
                    for key in ("valence", "arousal", "dominance"):
                        if key in appraisal:
                            dimension_override[key] = max(-1.0, min(1.0, float(appraisal[key])))
                    affect_state["dimension_override"] = dimension_override
                    affect_state.update(dimension_override)
                    affect_state["updated_at"] = utc_now()
                    affect_state["updated_at_ms"] = time.time_ns() // 1_000_000
                    affect_state["last_decay_at_ms"] = affect_state["updated_at_ms"]
                    affect_state["source"] = "manual_appraisal"
                    affect_state["revision"] = int(affect_state.get("revision") or 1) + 1
                    relationship_id = str(body.get("relationship_id") or appraisal.get("relationship_id") or "").strip()
                    if relationship_id:
                        relationships = self._scope_state()["relationships"]
                        record = deepcopy(relationships.get(relationship_id) or {})
                        record.update({
                            "relationship_id": relationship_id,
                            "valence": self._scope_state()["affect"].get("valence", 0.0),
                            "arousal": self._scope_state()["affect"].get("arousal", 0.0),
                            "dominance": self._scope_state()["affect"].get("dominance", 0.0),
                            "updated_at": utc_now(),
                            "source": "affect_appraisal",
                        })
                        relationships[relationship_id] = record
                    self._persist()
                    result = {"ok": True, "state": deepcopy(self._scope_state()["affect"])}
                elif verb == "POST" and path == "/api/v1/v3/life/affect/decay":
                    life_id = str(self._active()["life_id"])
                    scope = self._scope_state(life_id)
                    state, _, _ = self._decay_transient_affect(life_id)
                    self._persist()
                    result = {"ok": True, "state": deepcopy(state)}
                elif verb == "GET" and path == "/api/v1/v3/life/affect/expression":
                    state = self._scope_state()["affect"]
                    result = {
                        "ok": True,
                        "expression": {
                            "valence": state.get("valence", 0.0),
                            "arousal": state.get("arousal", 0.0),
                            "dominance": state.get("dominance", 0.0),
                            "primary_emotion": state.get("primary_emotion", "calm"),
                            "primary_emotion_zh": state.get("primary_emotion_zh", "平静"),
                            "intensity_milli": state.get("intensity_milli", 0),
                            "intensity_band": state.get("intensity_band", "none"),
                            "directive": state.get("expression_directive", ""),
                            "authority": "attention_and_expression_only",
                        },
                    }
                elif verb == "POST" and path == "/api/v1/v3/life/context/compile":
                    current = str(body.get("current_request") or "").strip()
                    if not current:
                        raise EmbeddedLifeError("life.context.request_empty")
                    result = {"ok": True, **self.system.compile_context(current, messages=body.get("messages"), active_run=body.get("active_run"), token_budget=int(body.get("token_budget") or 8000))}
                elif verb == "POST" and path == "/api/v1/v3/life/context/compile-and-authorize":
                    life_id = str(self._active()["life_id"])
                    scope = self._scope_state(life_id)
                    prior_affect = deepcopy(scope["affect"])
                    changed = self._appraise_current_user_affect(body)
                    try:
                        result = LifeContextCompileAuthorizeApi(self.authority_store).compile_and_authorize(body, self._projection_inputs())
                    except Exception:
                        scope["affect"] = prior_affect
                        raise
                    scheduler_state = scope.setdefault("scheduler", {})
                    scheduler_state["last_user_activity_at_ms"] = int(body.get("issued_at_ms") or 0)
                    scheduler_state["last_user_run_id"] = str(body.get("run_id") or "")[:160]
                    replied = self._mark_latest_proactive_replied(
                        life_id=life_id,
                        user_activity_at_ms=scheduler_state["last_user_activity_at_ms"],
                        run_id=scheduler_state["last_user_run_id"],
                    )
                    if replied:
                        self._persist(life_id)
                    if changed:
                        state = scope["affect"]
                        self.system.journal.append(
                            life_id,
                            "affect.appraised",
                            {
                                "request_id": str(body.get("request_id") or ""),
                                "signal_sha256": state["last_signal_sha256"],
                                "primary_emotion": state["primary_emotion"],
                                "intensity_milli": state["intensity_milli"],
                                "authority": "attention_and_expression_only",
                            },
                            actor="life_affect",
                            idempotency_key=f"affect.appraise:{life_id}:{body.get('request_id')}",
                        )
                    self._persist(life_id)
                    result["deployment_mode"] = self.mode
                elif verb == "GET" and path == "/api/v1/v3/life/context/latest":
                    try:
                        result = {"ok": True, **self.system.latest_context()}
                    except LifeCoreError as exc:
                        if exc.status != 404:
                            raise
                        result = {"ok": True, "envelope": None}
                elif verb == "POST" and path == "/api/v1/v3/life/context/replay":
                    result = {"ok": True, **self.system.replay_context(str(body.get("context_hash") or ""))}
                elif verb == "POST" and path == "/api/v1/v3/life/context/verify":
                    result = {"ok": True, **self.system.verify_context(body.get("envelope") or {})}
                elif verb == "POST" and path == "/api/v1/v3/life/execution/prepare":
                    result = {"ok": True, **self.system.prepare_execution(str(body.get("context_hash") or ""), str(body.get("request_id") or ""), channel=str(body.get("channel") or "desktop_frontend"), decision_action=str(body.get("decision_action") or "execute"), purpose=str(body.get("purpose") or ""))}
                elif verb == "POST" and path in {"/api/v1/v3/life/execution/recover", "/api/v1/v3/life/execution/status"}:
                    result = self._execution_recover(body)
                elif verb == "POST" and path == "/api/v1/v3/life/execution/commit":
                    schema = str(body.get("schema") or "").strip()
                    request_id = str(body.get("request_id") or "").strip()
                    run_id = str(body.get("run_id") or "").strip()
                    life_id = str(body.get("life_id") or "").strip()
                    status = str(body.get("status") or "").strip().lower()
                    generation = body.get("generation")
                    completed_at_ms = body.get("completed_at_ms")
                    session_scope_hash = str(body.get("session_scope_hash") or "").strip()
                    user_goal_sha256 = str(body.get("user_goal_sha256") or "").strip()
                    final_result_sha256 = str(body.get("final_result_sha256") or "").strip()
                    fact_ids_raw = body.get("fact_ids")
                    repository_evidence_raw = body.get("repository_evidence")
                    sha256_re = re.compile(r"^[0-9a-f]{64}$")
                    if schema != "tiangong.life.execution-terminal.v1":
                        raise EmbeddedLifeError("life.execution.schema_invalid", status=400)
                    if not _OPAQUE.fullmatch(request_id):
                        raise EmbeddedLifeError("life.execution.request_id_invalid", status=400)
                    if not _OPAQUE.fullmatch(run_id):
                        raise EmbeddedLifeError("life.execution.run_id_invalid", status=400)
                    if not _OPAQUE.fullmatch(life_id):
                        raise EmbeddedLifeError("life.execution.life_id_invalid", status=400)
                    if life_id != str(self._active()["life_id"]):
                        raise EmbeddedLifeError("life.execution.life_id_mismatch", status=409)
                    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
                        raise EmbeddedLifeError("life.execution.generation_invalid", status=400)
                    if isinstance(completed_at_ms, bool) or not isinstance(completed_at_ms, int) or completed_at_ms < 1:
                        raise EmbeddedLifeError("life.execution.completed_at_invalid", status=400)
                    if status not in {"completed", "failed", "cancelled"}:
                        raise EmbeddedLifeError("life.execution.status_invalid", status=400)
                    for field_name, value in (
                        ("session_scope_hash", session_scope_hash),
                        ("user_goal_sha256", user_goal_sha256),
                        ("final_result_sha256", final_result_sha256),
                    ):
                        if not sha256_re.fullmatch(value):
                            raise EmbeddedLifeError(f"life.execution.{field_name}_invalid", status=400)
                    if not isinstance(fact_ids_raw, list) or len(fact_ids_raw) > 1024:
                        raise EmbeddedLifeError("life.execution.fact_ids_invalid", status=400)
                    fact_ids: list[str] = []
                    for item in fact_ids_raw:
                        fact_id = str(item or "").strip()
                        if not _OPAQUE.fullmatch(fact_id):
                            raise EmbeddedLifeError("life.execution.fact_id_invalid", status=400)
                        if fact_id not in fact_ids:
                            fact_ids.append(fact_id)
                    repository_evidence = (
                        None
                        if repository_evidence_raw is None
                        else normalize_repository_evidence(repository_evidence_raw)
                    )
                    if repository_evidence_raw is not None and repository_evidence is None:
                        raise EmbeddedLifeError("life.execution.repository_evidence_invalid", status=400)
                    stable_payload = {
                        "schema": schema,
                        "request_id": request_id,
                        "run_id": run_id,
                        "generation": generation,
                        "life_id": life_id,
                        "session_scope_hash": session_scope_hash,
                        "status": status,
                        "user_goal_sha256": user_goal_sha256,
                        "final_result_sha256": final_result_sha256,
                        "fact_ids": fact_ids,
                        "completed_at_ms": completed_at_ms,
                    }
                    if repository_evidence is not None:
                        stable_payload["repository_evidence"] = repository_evidence
                    key = request_id
                    commit_sha256 = canonical_sha256(
                        {
                            "domain": "tiangong.life.execution-commit.v1",
                            "payload": stable_payload,
                        }
                    )
                    existing = self._scope_state()["executions"].get(key)
                    if isinstance(existing, dict):
                        if existing.get("commit_sha256") != commit_sha256:
                            raise EmbeddedLifeError("life.execution.commit_conflict", status=409)
                        result = {"ok": True, "duplicate": True, "execution": deepcopy(existing)}
                    else:
                        record = {
                            **stable_payload,
                            "commit_sha256": commit_sha256,
                            "committed_at": utc_now(),
                        }
                        execution_scope = self._scope_state(life_id)
                        execution_scope["executions"][key] = record
                        event = self.system.journal.append(
                            life_id,
                            "execution.committed",
                            record,
                            actor="runtime",
                            idempotency_key=f"execution.commit:{request_id}",
                        )
                        try:
                            self._persist(life_id)
                        except Exception:
                            execution_scope["executions"].pop(key, None)
                            raise
                        result = {
                            "ok": True,
                            "duplicate": False,
                            "execution": deepcopy(record),
                            "life_event": event,
                        }
                elif verb == "POST" and path == "/api/v1/v3/life/heartbeat":
                    result = self._scheduler_tick(str(body.get("reason") or "manual"))
                    result["status"] = (
                        "paused"
                        if result.get("reason_code") == "life.scheduler.disabled"
                        else "alive"
                    )
                elif verb == "POST" and path in {"/api/v1/v3/life/inbox/read", "/api/v1/v3/life/inbox/delete"}:
                    message_id = str(body.get("message_id") or "")
                    found = False
                    inbox_scope = self._scope_state()
                    inbox = inbox_scope["inbox"]
                    for row in list(inbox):
                        if row.get("message_id") == message_id:
                            found = True
                            if path.endswith("/read"):
                                row["read"] = True
                            else:
                                inbox.remove(row)
                                tombstones = inbox_scope.setdefault(
                                    "inbox_tombstones", []
                                )
                                if message_id and message_id not in tombstones:
                                    tombstones.append(message_id)
                                    del tombstones[:-400]
                            break
                    self._persist()
                    result = {"ok": True, "message_id": message_id, "found": found}
                elif verb == "GET" and path == "/api/v1/v3/life/proactive/status":
                    proactive_scope = self._scope_state()
                    result = {
                        "ok": True,
                        "settings": {key: deepcopy(value) for key, value in proactive_scope["settings"].items() if str(key).startswith("proactive_")},
                        "scheduler": {key: deepcopy(value) for key, value in proactive_scope["scheduler"].items() if "proactive" in str(key)},
                        "pending": sum(1 for row in proactive_scope["proactive_chats"] if not row.get("acked")),
                    }
                elif verb == "GET" and path == "/api/v1/v3/life/proactive-chat/pending":
                    result = {"ok": True, "messages": [deepcopy(row) for row in self._scope_state()["proactive_chats"] if not row.get("acked")]}
                elif verb == "POST" and path == "/api/v1/v3/life/proactive-chat/ack":
                    message_id = str(body.get("message_id") or "")
                    found = False
                    scope = self._scope_state()
                    life_id = str(self._active()["life_id"])
                    for row in scope["proactive_chats"]:
                        if row.get("message_id") == message_id:
                            if row.get("acked") is not True:
                                row["acked"] = True
                                row["acked_at_ms"] = time.time_ns() // 1_000_000
                                initiative_id = str(row.get("initiative_id") or "")
                                if initiative_id:
                                    self.system.journal.append(
                                        life_id,
                                        "life.proactive.acked",
                                        {"initiative_id": initiative_id, "message_id": message_id},
                                        actor="delivery",
                                        idempotency_key=f"life.proactive.acked:{initiative_id}",
                                    )
                            found = True
                            break
                    self._persist(life_id)
                    result = {"ok": True, "message_id": message_id, "found": found}
                elif verb == "POST" and path == "/api/v1/v3/life/settings":
                    settings = body.get("settings") if isinstance(body.get("settings"), Mapping) else {}
                    allowed = {
                        "permission_mode",
                        "autonomous_risk_max",
                        "autonomy_enabled",
                        "autonomy_task_generation_enabled",
                        "autonomy_activity_types",
                        "privacy",
                        "heartbeat_enabled",
                        "llm_daily_budget",
                        "llm_daily_attempt_budget",
                        "share_enabled",
                        "share_quiet_if_user_active",
                        "share_min_interval_seconds",
                        "share_hourly_limit",
                        "share_daily_limit",
                        "share_dnd_start",
                        "share_dnd_end",
                        "proactive_enabled",
                        "proactive_mode",
                        "proactive_decision_interval_seconds",
                        "proactive_min_interval_seconds",
                        "proactive_max_messages_per_hour",
                        "proactive_max_messages_per_day",
                        "proactive_dnd_enabled",
                        "proactive_dnd_start_hour",
                        "proactive_dnd_end_hour",
                        "proactive_timezone_offset_minutes",
                        "proactive_max_future_skew_seconds",
                        "proactive_respect_user_activity",
                        "proactive_user_active_window_seconds",
                        "proactive_min_evidence_confidence_milli",
                        "proactive_evidence_stale_after_seconds",
                        "proactive_min_utility_lcb_milli",
                        "proactive_min_margin_milli",
                    }
                    # Preserve namespaced/extension settings used by plugins and
                    # tests.  Known safety-sensitive keys are strongly typed;
                    # unknown keys remain scoped to this life identity and must
                    # still be canonical JSON.
                    for key in settings:
                        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", str(key)):
                            raise EmbeddedLifeError("life.settings.key_invalid")
                    updates = deepcopy(dict(settings))
                    try:
                        canonical_json_bytes(updates)
                    except (TypeError, ValueError) as exc:
                        raise EmbeddedLifeError("life.settings.payload_invalid") from exc
                    for key in ("autonomy_enabled", "autonomy_task_generation_enabled", "heartbeat_enabled"):
                        if key in updates and not isinstance(updates[key], bool):
                            raise EmbeddedLifeError("life.settings.boolean_invalid")
                    for key in (
                        "share_enabled", "share_quiet_if_user_active",
                        "proactive_enabled", "proactive_dnd_enabled",
                        "proactive_respect_user_activity",
                    ):
                        if key in updates and not isinstance(updates[key], bool):
                            raise EmbeddedLifeError("life.settings.boolean_invalid")
                    permission_labels = {
                        "低风险自动执行，高风险需确认": "autonomous_low_risk",
                        "高风险操作需确认": "confirm_high_risk",
                        "所有自主操作均需确认": "confirm_all",
                    }
                    if "permission_mode" in updates:
                        updates["permission_mode"] = permission_labels.get(
                            str(updates["permission_mode"]),
                            str(updates["permission_mode"]),
                        )
                    if "permission_mode" in updates and str(updates["permission_mode"]) not in {
                        "autonomous_low_risk",
                        "confirm_high_risk",
                        "confirm_all",
                    }:
                        raise EmbeddedLifeError("life.settings.permission_mode_invalid")
                    if "autonomous_risk_max" in updates and str(updates["autonomous_risk_max"]) not in {
                        "A0", "A1", "A2", "A3", "A4",
                    }:
                        raise EmbeddedLifeError("life.settings.risk_max_invalid")
                    limits = {
                        "llm_daily_budget": (0, 1000),
                        "llm_daily_attempt_budget": (0, 2000),
                        "share_min_interval_seconds": (60, 604800),
                        "share_hourly_limit": (0, 60),
                        "share_daily_limit": (0, 1000),
                        "proactive_decision_interval_seconds": (60, 86400),
                        "proactive_min_interval_seconds": (0, 604800),
                        "proactive_max_messages_per_hour": (0, 60),
                        "proactive_max_messages_per_day": (0, 1000),
                        "proactive_dnd_start_hour": (0, 23),
                        "proactive_dnd_end_hour": (0, 23),
                        "proactive_timezone_offset_minutes": (-840, 840),
                        "proactive_max_future_skew_seconds": (0, 3600),
                        "proactive_user_active_window_seconds": (0, 3600),
                        "proactive_min_evidence_confidence_milli": (0, 1000),
                        "proactive_evidence_stale_after_seconds": (60, 604800),
                        "proactive_min_utility_lcb_milli": (0, 4000),
                        "proactive_min_margin_milli": (0, 4000),
                    }
                    for key, (minimum, maximum) in limits.items():
                        if key not in updates:
                            continue
                        value = updates[key]
                        if isinstance(value, bool) or not isinstance(value, int):
                            raise EmbeddedLifeError("life.settings.integer_invalid")
                        if value < minimum or value > maximum:
                            raise EmbeddedLifeError("life.settings.integer_out_of_range")
                    if "proactive_mode" in updates and str(updates["proactive_mode"]).casefold() not in {"shadow", "live"}:
                        raise EmbeddedLifeError("life.settings.proactive_mode_invalid")
                    if "proactive_mode" in updates:
                        updates["proactive_mode"] = str(updates["proactive_mode"]).casefold()
                    for key in ("share_dnd_start", "share_dnd_end"):
                        if key in updates and not re.fullmatch(
                            r"(?:[01]\d|2[0-3]):[0-5]\d",
                            str(updates[key]),
                        ):
                            raise EmbeddedLifeError("life.settings.time_invalid")
                    if "autonomy_activity_types" in updates:
                        try:
                            updates["autonomy_activity_types"] = normalize_activity_types(
                                updates["autonomy_activity_types"]
                            )
                        except ValueError as exc:
                            raise EmbeddedLifeError(
                                "life.settings.autonomy_activity_types_invalid"
                            ) from exc
                    if "privacy" in updates:
                        if not isinstance(updates["privacy"], Mapping):
                            raise EmbeddedLifeError("life.settings.privacy_invalid")
                        privacy_update = dict(updates["privacy"])
                        if set(privacy_update) - {"redact_llm", "redact_share"}:
                            raise EmbeddedLifeError("life.settings.privacy_key_invalid")
                        if any(
                            not isinstance(value, bool)
                            for value in privacy_update.values()
                        ):
                            raise EmbeddedLifeError("life.settings.privacy_boolean_invalid")
                    scope = self._scope_state()
                    before = deepcopy(scope["settings"])
                    if "privacy" in updates:
                        updates["privacy"] = {
                            **deepcopy(scope["settings"]["privacy"]),
                            **dict(updates["privacy"]),
                        }
                    scope["settings"].update(updates)
                    if "schedule" in body:
                        scope["schedule"] = normalize_schedule(body["schedule"], today=utc_now()[:10], autonomy_tasks=[])
                    if "body" in body:
                        scope["body"] = normalize_body(body["body"], updated_at=utc_now())
                    self._autonomy_state()
                    try:
                        self._sync_daily_summary(str(self._active()["life_id"]))
                        self._persist()
                    except Exception:
                        scope["settings"] = before
                        raise
                    result = {"ok": True, "settings": deepcopy(scope["settings"])}
                elif verb == "POST" and path.startswith("/api/v1/v3/life/upgrade/"):
                    result = self._upgrade_action(path, body)
                elif verb == "POST" and path.startswith("/api/v1/v3/life/capability/"):
                    action = path.rsplit("/", 1)[-1]
                    if action == "invoke":
                        result = self._capability_invoke(body)
                    elif action == "activate":
                        result = self._capability_activate(body)
                    elif action == "rollback":
                        result = self._capability_rollback(body)
                    elif action == "reactivate":
                        result = self._capability_reactivate(body)
                    elif action == "propose" and path.endswith("/capability/patch/propose"):
                        result = self._capability_patch_propose(body)
                    elif action == "verify" and path.endswith("/capability/patch/verify"):
                        result = self._capability_patch_settle(body)
                    elif action == "outcome" and path.endswith("/capability/outcome"):
                        result = self._capability_outcome_report(body)
                    elif action == "discard" and str(body.get("artifact_id") or "") in self._scope_state()["capabilities"]:
                        result = self._capability_discard(body)
                    else:
                        # Compatibility-only proposal records never become a
                        # dynamic executable artifact.  New learned Skills and
                        # Tools must travel through the learning executor above
                        # so they retain a verified bundle and version pointer.
                        artifact_id = str(body.get("artifact_id") or (body.get("card") or {}).get("artifact_id") or "cap_" + uuid.uuid4().hex)
                        record = deepcopy(self._scope_state()["capabilities"].get(artifact_id) or {})
                        record.update({**body, "artifact_id": artifact_id, "status": action, "updated_at": utc_now()})
                        if action == "discard":
                            record["discarded"] = True
                        self._scope_state()["capabilities"][artifact_id] = record
                        self._persist()
                        result = {"ok": True, "capability": record}
                elif verb == "POST" and path.startswith("/api/v1/v3/learning/"):
                    action = path.rsplit("/", 1)[-1]
                    # Compatibility names from the old front-end now terminate
                    # at the same audited state machine.  There is no separate
                    # "activate/release" phase after a user has confirmed a
                    # preview: confirmation publishes exactly that preview.
                    if action in {"confirm", "process-approved", "activate", "release", "request-activation"}:
                        result = self._learning_confirm(body)
                    elif action == "discard":
                        result = self._learning_discard(body)
                    else:
                        return 404, {"ok": False, "reason_code": "life.learning.route_not_found"}, "application/json; charset=utf-8"
                else:
                    return 404, {"ok": False, "reason_code": "life.route.not_found"}, "application/json; charset=utf-8"
            return 200, result, "application/json; charset=utf-8"
        except EmbeddedLifeError as exc:
            return exc.status, {"ok": False, "error_code": exc.code, "reason_code": exc.code}, "application/problem+json"
        except LifeCoreError as exc:
            return exc.status, {"ok": False, "error_code": exc.code, "reason_code": exc.code}, "application/problem+json"
        except LifeContextApiError as exc:
            return 409, {"ok": False, "error_code": str(exc), "reason_code": str(exc)}, "application/problem+json"
        except (LifeShadowStoreError, OSError, ValueError, TypeError) as exc:
            # Keep the public compatibility code stable while recording the
            # bounded failing phase needed to diagnose a live life-chain break.
            # File paths, arguments, and exception text may contain private
            # workspace or user content and must never cross this boundary.
            frames = traceback.extract_tb(exc.__traceback__)
            phase = frames[-1].name if frames else "unknown"
            return 500, {
                "ok": False,
                "error_code": "life.embedded.failed",
                "reason_code": "life.embedded.failed",
                "error_type": type(exc).__name__,
                "error_phase": f"life.embedded.{phase}",
            }, "application/problem+json"

    def close(self) -> None:
        if getattr(self, "_closed", True):
            return
        # Reject new calls before stopping the autonomous loop.  A failed close
        # remains retryable and keeps the writer lease so no second LifeKernel
        # can take over an ambiguously open authority store.
        with self._lock:
            if self._closed:
                return
            self._closing = True
        scheduler = self.scheduler
        if scheduler is not None:
            scheduler.stop()
        with self._lock:
            if self._closed:
                return
            if getattr(self, "_persist_pending", False):
                # 去抖挂起的投影落盘（close 是唯一保证的刷新点之一）
                self._persist(force=True)
            store = self.authority_store
            if store is not None:
                try:
                    store.close()
                except Exception as exc:
                    raise RuntimeError("life authority store failed to close") from exc
            lease = self._lease
            if lease is not None:
                try:
                    lease.release()
                except Exception as exc:
                    raise RuntimeError("life writer lease failed to release") from exc
            self._closed = True
            self._closing = False


__all__ = [
    "EMBEDDED_LIFE_BUILD_ID",
    "EmbeddedLifeError",
    "EmbeddedLifeRuntime",
    "LifeWriterLease",
]
