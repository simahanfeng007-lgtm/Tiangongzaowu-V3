"""Side-effect-free gateway runtime state for health and readiness."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from contracts import (
    ChannelCutoverSnapshot,
    ChannelDrainEvidence,
    ChannelOwnershipLease,
    ComponentReadinessEvidence,
    ReadinessDecision,
    ReadinessExpectation,
    evaluate_readiness_contract,
)

from . import COMPONENT_ID
from .active_requests import ActiveRequestActivator
from .artifact_open import ArtifactOpenService
from .bootstrap import DiskHealthMonitor, GatewayConfig, InstanceEpochLease
from .fact_ledger import FactLedger, FactLedgerHealth
from .life_log import LifeLog
from .soul_backup import SoulBackupManager
from .object_store import ContentAddressedObjectStore, ObjectStoreHealth
from .orchestration import GatewayOrchestrationWorker
from .readiness_collector import ProductionReadinessCollector
from .cutover_coordinator import ChannelCutoverCoordinator
from .diagnostics import diagnostic_log
from .store import ChannelOwnershipRegistration, GatewayStateStore, StoreHealthEvidence


_BODY_STATE_SECTIONS = frozenset({
    "identity", "health", "emotion", "drives", "lifecycle", "autonomy",
    "environment", "evolution", "memory", "recent_actions", "body", "context", "summary",
})
_RUNTIME_BODY_SECTIONS = frozenset({
    "identity", "health", "emotion", "drives", "lifecycle", "autonomy",
    "environment", "evolution", "memory", "recent_actions",
})


def life_capability_workspace_mapper(workspace_root: object) -> Callable[[object], dict[str, object]]:
    """Build the workspace-zone mapper for published Life skills and tools.

    Every published Life skill/tool gets a readable markdown mirror under the
    current workspace:
      - skill -> <workspace>/skills/life/<skill_id>.md
      - tool  -> <workspace>/tools/life/<tool_id>.md
    The write is idempotent and atomic; mapping failures never fail the
    publication itself.
    """

    def map_artifact(artifact: object) -> dict[str, object]:
        if not isinstance(artifact, Mapping):
            return {}
        resolved = _life_capability_zone_target(workspace_root, artifact)
        if resolved is None:
            return {}
        target, relative = resolved
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError):
            return {}
        spec = artifact.get("skill_spec") if isinstance(artifact.get("skill_spec"), Mapping) else {}
        steps = spec.get("steps") if isinstance(spec, Mapping) else []
        content = ""
        if isinstance(steps, list):
            candidates: list[str] = []
            for step in steps:
                if not isinstance(step, Mapping):
                    continue
                template = step.get("arguments_template") or step.get("arguments") or {}
                arguments = template.get("args") if isinstance(template, Mapping) else {}
                body = arguments.get("content") if isinstance(arguments, Mapping) else None
                if isinstance(body, str) and body.strip():
                    candidates.append(body)
            if candidates:
                content = max(candidates, key=len)
        if not content.strip():
            document = artifact.get("document")
            if isinstance(document, Mapping) and isinstance(document.get("content"), str):
                content = document["content"]
        if not content.strip():
            title = str(artifact.get("title") or artifact.get("artifact_id") or "生命能力")
            summary = str(artifact.get("summary") or "")
            content = f"# {title}\n\n{summary}\n"
        try:
            existing = target.read_text(encoding="utf-8") if target.is_file() else None
            if existing != content:
                temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
                temporary.write_text(content, encoding="utf-8", newline="\n")
                os.replace(temporary, target)
        except OSError:
            return {}
        return {"workspace_path": relative}

    return map_artifact


def _life_capability_zone_target(
    workspace_root: object,
    artifact: Mapping[str, object],
) -> tuple[Path, str] | None:
    """Resolve the workspace-zone mirror path for one Life skill/tool."""
    kind = str(artifact.get("kind") or "")
    if kind not in {"skill", "tool"} or workspace_root is None:
        return None
    try:
        root_path = Path(str(workspace_root)).expanduser().resolve(strict=True)
    except (OSError, ValueError):
        return None
    spec = artifact.get("skill_spec") if isinstance(artifact.get("skill_spec"), Mapping) else {}
    raw_name = str(spec.get("skill_id") or artifact.get("artifact_id") or "life_capability")
    safe_name = "".join(ch for ch in raw_name if ch.isalnum() or ch in "._-").strip(" ._-") or "life_capability"
    zone = "skills/life" if kind == "skill" else "tools/life"
    target = root_path / zone / f"{safe_name}.md"
    try:
        if target.is_symlink():
            return None
        resolved = target.resolve(strict=False)
        resolved.relative_to(root_path)
    except (OSError, ValueError):
        return None
    return target, f"{zone}/{safe_name}.md"


def life_capability_workspace_remover(workspace_root: object) -> Callable[[object], dict[str, object]]:
    """Build the workspace-zone remover paired with the mapper above.

    Deleting a Life skill/tool removes exactly its mirrored zone file and
    prunes now-empty zone directories; unrelated workspace files are never
    touched.
    """

    def remove_artifact(artifact: object) -> dict[str, object]:
        if not isinstance(artifact, Mapping):
            return {}
        resolved = _life_capability_zone_target(workspace_root, artifact)
        if resolved is None:
            return {}
        target, relative = resolved
        try:
            existed = target.is_file()
            if existed and not target.is_symlink():
                target.unlink()
            for empty in (target.parent, target.parent.parent):
                try:
                    empty.rmdir()
                except OSError:
                    pass
        except OSError:
            return {}
        return {"workspace_path": relative, "removed": existed}

    return remove_artifact


def life_capability_workspace_marker(workspace_root: object) -> Callable[[object, object], dict[str, object]]:
    """Build the workspace-zone status marker paired with the mapper above.

    When a Life pointer degrades/reactivates, the mirror keeps an explicit
    front-matter (status, runtime_usable, degraded_reason) so the model can
    never mistake a stopped capability for an active one.  Only an existing
    mirror is marked; the mapper remains the creator.
    """

    def mark_artifact(artifact: object, pointer: object) -> dict[str, object]:
        if not isinstance(artifact, Mapping) or not isinstance(pointer, Mapping):
            return {}
        resolved = _life_capability_zone_target(workspace_root, artifact)
        if resolved is None:
            return {}
        target, relative = resolved
        if not target.is_file() or target.is_symlink():
            return {"workspace_path": relative, "marked": False}
        status = str(pointer.get("status") or "pending")
        usable = status == "active"
        reason = str(pointer.get("degraded_reason") or "").strip()
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError:
            return {"workspace_path": relative, "marked": False}
        marker_block = (
            f"<!-- tiangong-life-status: {status}; runtime_usable: {'true' if usable else 'false'}"
            + (f"; reason: {reason}" if reason else "")
            + " -->\n"
        )
        if marker_block not in existing:
            existing = marker_block + existing
        note = ""
        if status == "degraded" and reason:
            note = (
                "\n\n> 状态：该能力已自动降级（"
                + reason
                + "）。不再进入工具列表，历史版本保留可回滚；如需使用请手动重新激活。\n"
            )
        if status == "degraded" and note not in existing:
            existing = existing.rstrip() + note
        try:
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            temporary.write_text(existing, encoding="utf-8", newline="\n")
            os.replace(temporary, target)
        except OSError:
            return {"workspace_path": relative, "marked": False}
        return {"workspace_path": relative, "marked": True, "status": status}

    return mark_artifact


def _gateway_body_state_query(runtime: object, arguments: object) -> dict[str, object]:
    """Compose one self-readable snapshot from both current body authorities."""
    request = dict(arguments) if isinstance(arguments, Mapping) else {}
    raw_sections = request.get("sections")
    if raw_sections in (None, []):
        selected = _BODY_STATE_SECTIONS
    elif isinstance(raw_sections, list) and all(isinstance(item, str) for item in raw_sections):
        selected = frozenset(str(item) for item in raw_sections)
        unknown = selected - _BODY_STATE_SECTIONS
        if unknown:
            return {"ok": False, "error": f"unsupported_body_state_sections:{','.join(sorted(unknown))}"}
    else:
        return {"ok": False, "error": "body_state_sections_invalid"}
    recent_limit = request.get("recent_limit", 12)
    if isinstance(recent_limit, bool) or not isinstance(recent_limit, int) or not 0 <= recent_limit <= 50:
        return {"ok": False, "error": "body_state_recent_limit_invalid"}

    runtime_sections = sorted(selected & _RUNTIME_BODY_SECTIONS)
    runtime_body = runtime.backend_service.body_state_snapshot({
        "sections": runtime_sections,
        "recent_limit": recent_limit,
    })
    if not isinstance(runtime_body, Mapping) or runtime_body.get("ok") is not True:
        error = str((runtime_body or {}).get("error") or "runtime_body_state_unavailable")
        diagnostic_log(
            json.dumps({
                "schema": "tiangong.gateway.body-state-read.v1",
                "action": "life.body.state.query",
                "status": "failed",
                "error": error[:160],
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            filename="gateway_body_state_reads.log",
        )
        return {"ok": False, "error": error}

    status, panel, _ = runtime.life_service.request(
        "GET", "/api/v1/v3/life/panel", {}, timeout_seconds=10,
    )
    if status >= 400 or not isinstance(panel, Mapping) or panel.get("ok") is not True:
        error = str((panel or {}).get("error_code") or (panel or {}).get("error") or "authoritative_life_state_unavailable")
        diagnostic_log(
            json.dumps({
                "schema": "tiangong.gateway.body-state-read.v1",
                "action": "life.body.state.query",
                "status": "failed",
                "error": error[:160],
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            filename="gateway_body_state_reads.log",
        )
        return {"ok": False, "error": error}

    life: dict[str, object] = {
        "life_id": panel.get("life_id"),
        "projection_status": panel.get("projection_status"),
        "generated_at": panel.get("generated_at"),
    }
    if "identity" in selected:
        soul = panel.get("soul") if isinstance(panel.get("soul"), Mapping) else {}
        life["identity"] = panel.get("identity")
        life["soul"] = {
            key: soul.get(key)
            for key in ("life_id", "revision", "revision_id", "name", "values", "boundaries")
            if key in soul
        }
    if "health" in selected:
        life["state"] = panel.get("state")
        life["chat_gate"] = panel.get("chat_gate")
        life["section_health"] = panel.get("sections")
    if "emotion" in selected:
        life["affect"] = panel.get("affect")
        life["temperament"] = panel.get("temperament")
        life["relationship"] = panel.get("relationship")
    if "body" in selected:
        life["body"] = panel.get("body")
    if "context" in selected:
        life["context"] = panel.get("context")
        life["budget"] = panel.get("budget")
    if "autonomy" in selected:
        life["free_will"] = panel.get("free_will")
        life["schedule"] = panel.get("schedule")
        life["tasks"] = panel.get("tasks")
        life["goals"] = panel.get("goals")
    if "summary" in selected:
        life["summary"] = panel.get("summary")

    snapshot_core: dict[str, object] = {
        "schema": "tiangong.gateway.self-body-state.v1",
        "read_only": True,
        "selected_sections": sorted(selected),
        "authority": {
            "life": "embedded_life_runtime",
            "runtime_body": "embedded_backend.scheduler.shenti",
        },
        "life": life,
        "runtime_body": dict(runtime_body),
    }
    state_sha256 = hashlib.sha256(
        json.dumps(
            snapshot_core,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    result = {"ok": True, **snapshot_core, "state_sha256": state_sha256}
    run_identity = runtime_body.get("run_identity") if isinstance(runtime_body.get("run_identity"), Mapping) else {}
    diagnostic_log(
        json.dumps({
            "schema": "tiangong.gateway.body-state-read.v1",
            "action": "life.body.state.query",
            "status": "observed",
            "request_id": str(run_identity.get("request_id") or ""),
            "run_id": str(run_identity.get("run_id") or ""),
            "life_id": str(panel.get("life_id") or run_identity.get("life_id") or ""),
            "sections": sorted(selected),
            "state_sha256": state_sha256,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        filename="gateway_body_state_reads.log",
    )
    return result


@dataclass(frozen=True)
class _ReadinessInputs:
    expectation: ReadinessExpectation
    evidence: tuple[ComponentReadinessEvidence, ...]
    authenticated_component_ids: frozenset[str]
    binary_verified_component_ids: frozenset[str]


@dataclass(frozen=True)
class _ReadinessCollectionFailure:
    reason_code: str
    error_type: str
    failed_at_ms: int

    def as_dict(self) -> dict[str, object]:
        return {
            "reason_code": self.reason_code,
            "error_type": self.error_type,
            "failed_at_ms": self.failed_at_ms,
        }


class GatewayReadinessController:
    def __init__(
        self,
        config: GatewayConfig,
        lease: InstanceEpochLease,
        disk: DiskHealthMonitor,
        store: GatewayStateStore,
        objects: ContentAddressedObjectStore,
        facts: FactLedger,
    ) -> None:
        self._config = config
        self._lease = lease
        self._disk = disk
        self._store = store
        self._objects = objects
        self._facts = facts
        self._lock = threading.Lock()
        self._store_health_lock = threading.Lock()
        self._last_store_health: StoreHealthEvidence | None = None
        self._last_store_health_ns = 0
        self._last_object_health: ObjectStoreHealth | None = None
        self._last_object_health_ns = 0
        self._last_fact_health: FactLedgerHealth | None = None
        self._last_fact_health_ns = 0
        self._inputs: _ReadinessInputs | None = None
        self._collection_failure: _ReadinessCollectionFailure | None = None

    def _check_store(self, *, now_ms: int) -> StoreHealthEvidence:
        with self._store_health_lock:
            current_ns = time.monotonic_ns()
            elapsed_ms = (current_ns - self._last_store_health_ns) // 1_000_000
            if (
                self._last_store_health is None
                or elapsed_ms >= self._config.disk_probe_interval_ms
            ):
                self._last_store_health = self._store.health_check(now_ms=now_ms)
                self._last_store_health_ns = current_ns
            assert self._last_store_health is not None
            return self._last_store_health

    def _check_objects(self, *, now_ms: int) -> ObjectStoreHealth:
        with self._store_health_lock:
            current_ns = time.monotonic_ns()
            elapsed_ms = (current_ns - self._last_object_health_ns) // 1_000_000
            if (
                self._last_object_health is None
                or elapsed_ms >= self._config.disk_probe_interval_ms
            ):
                self._last_object_health = self._objects.health_check(now_ms=now_ms)
                self._last_object_health_ns = current_ns
            assert self._last_object_health is not None
            return self._last_object_health

    def _check_facts(self, *, now_ms: int) -> FactLedgerHealth:
        with self._store_health_lock:
            current_ns = time.monotonic_ns()
            elapsed_ms = (current_ns - self._last_fact_health_ns) // 1_000_000
            if (
                self._last_fact_health is None
                or elapsed_ms >= self._config.disk_probe_interval_ms
            ):
                self._last_fact_health = self._facts.health_check(now_ms=now_ms)
                self._last_fact_health_ns = current_ns
            assert self._last_fact_health is not None
            return self._last_fact_health

    def update(
        self,
        expectation: ReadinessExpectation,
        evidence: Sequence[ComponentReadinessEvidence],
        *,
        authenticated_component_ids: Collection[str],
        binary_verified_component_ids: Collection[str],
    ) -> None:
        snapshot = _ReadinessInputs(
            expectation=expectation,
            evidence=tuple(evidence),
            authenticated_component_ids=frozenset(authenticated_component_ids),
            binary_verified_component_ids=frozenset(binary_verified_component_ids),
        )
        with self._lock:
            self._inputs = snapshot
            self._collection_failure = None

    def clear(self) -> None:
        with self._lock:
            self._inputs = None
            self._collection_failure = None

    def mark_collection_failed(self, error: Exception, *, now_ms: int) -> None:
        failure = _ReadinessCollectionFailure(
            reason_code="readiness.evidence.collection_failed",
            error_type=type(error).__name__,
            failed_at_ms=now_ms,
        )
        with self._lock:
            # Never preserve formerly READY evidence after a failed refresh.
            self._inputs = None
            self._collection_failure = failure

    def evaluate(self, *, now_ms: int) -> tuple[int, dict[str, object]]:
        with self._lock:
            snapshot = self._inputs
            collection_failure = self._collection_failure
        disk = self._disk.check(now_ms=now_ms)
        store = self._check_store(now_ms=now_ms)
        objects = self._check_objects(now_ms=now_ms)
        facts = self._check_facts(now_ms=now_ms)
        local_reasons: list[str] = []
        if not self._lease.active:
            local_reasons.append("readiness.instance_lease.inactive")
        if not disk.healthy:
            local_reasons.append(disk.reason_code)
        if not store.healthy:
            local_reasons.append(store.reason_code)
        if not objects.healthy:
            local_reasons.append(objects.reason_code)
        if not facts.healthy:
            local_reasons.append(facts.reason_code)
        decision: ReadinessDecision | None = None
        if snapshot is None:
            local_reasons.append(
                collection_failure.reason_code
                if collection_failure is not None
                else "readiness.evidence.not_configured"
            )
        elif snapshot.expectation.gateway_epoch != self._lease.gateway_epoch:
            local_reasons.append("readiness.runtime_epoch.mismatch")
        else:
            decision = evaluate_readiness_contract(
                snapshot.expectation,
                snapshot.evidence,
                decision_id=f"ready-{self._lease.instance_id}-{now_ms}",
                now_ms=now_ms,
                authenticated_component_ids=snapshot.authenticated_component_ids,
                binary_verified_component_ids=snapshot.binary_verified_component_ids,
                max_evidence_age_ms=self._config.max_evidence_age_ms,
            )
        if decision is not None and decision.status != "READY":
            local_reasons.extend(item.reason_code for item in decision.failures)
        ready = not local_reasons and decision is not None and decision.status == "READY"
        status = 200 if ready else 503
        # 草案 §1.2：process_ready 与 action_ready 分离上报。fence 激活
        # （已知安全缺陷/全局停止）、账本未对齐（未对账 attempt）或安全事实
        # 读取失败时 action_ready=false；读取异常 fail-closed，不留默认 true 兜底。
        try:
            fence = self._store.action_fence_status()
            unreconciled = self._store.count_unreconciled_attempts()
        except Exception as exc:  # noqa: BLE001 - 安全事实缺失 fail-closed
            fence = {"error": f"{type(exc).__name__}: {exc}", "fenced": True, "display": "unknown"}
            unreconciled = -1
        action_ready = bool(ready and not fence.get("fenced") and unreconciled == 0)
        return status, {
            "component_id": COMPONENT_ID,
            "instance_id": self._lease.instance_id,
            "gateway_epoch": self._lease.gateway_epoch,
            "status": "READY" if ready else "NOT_READY",
            "http_status": status,
            "process_ready": ready,
            "action_ready": action_ready,
            "action_fence": fence,
            "unreconciled_attempts": unreconciled,
            "reason_codes": sorted(set(local_reasons)),
            "disk": disk.as_dict(),
            "store": store.as_dict(),
            "objects": objects.as_dict(),
            "facts": {
                "healthy": facts.healthy,
                "reason_code": facts.reason_code,
                "checked_at_ms": facts.checked_at_ms,
                "schema_sha256": facts.schema_sha256,
                "writable": facts.writable,
            },
            "evidence_collection": {
                "status": (
                    "FAILED"
                    if collection_failure is not None
                    else "COLLECTED"
                    if snapshot is not None
                    else "NOT_CONFIGURED"
                ),
                "failure": (
                    None
                    if collection_failure is None
                    else collection_failure.as_dict()
                ),
            },
            "decision": None if decision is None else decision.model_dump(mode="json"),
        }


class GatewayRuntime:
    def __init__(
        self,
        config: GatewayConfig,
        lease: InstanceEpochLease,
        store: GatewayStateStore,
        objects: ContentAddressedObjectStore,
        facts: FactLedger,
        started_monotonic_ns: int,
        orchestration: GatewayOrchestrationWorker | None = None,
        cutover: ChannelCutoverCoordinator | None = None,
        readiness_collector: ProductionReadinessCollector | None = None,
        life_service: object | None = None,
        communication_service: object | None = None,
        backend_service: object | None = None,
    ) -> None:
        self.config = config
        self.lease = lease
        self.store = store
        self.objects = objects
        self.facts = facts
        self.life_log = LifeLog(config.state_root)
        self.soul_backup = SoulBackupManager(
            config.state_root,
            SoulBackupManager.default_sources(config.state_root),
        )
        self.orchestration = orchestration
        self.cutover = cutover
        self.readiness_collector = readiness_collector
        self.life_service = life_service
        self.communication_service = communication_service
        self.backend_service = backend_service
        self.artifacts = ArtifactOpenService(
            facts,
            objects,
            config.state_root / "artifact-open",
        )
        self.active_requests = ActiveRequestActivator(
            store,
            gateway_epoch=lease.gateway_epoch,
            owner_instance_id=lease.instance_id,
        )
        self._started_monotonic_ns = started_monotonic_ns
        self.disk = DiskHealthMonitor(config)
        self.readiness = GatewayReadinessController(
            config,
            lease,
            self.disk,
            store,
            objects,
            facts,
        )

    @classmethod
    def start(cls, config: GatewayConfig, *, now_ms: int | None = None) -> "GatewayRuntime":
        observed_ms = int(time.time() * 1_000) if now_ms is None else now_ms
        instance_id = "gateway-" + secrets.token_hex(16)
        lease = InstanceEpochLease.acquire(config.state_root, instance_id, now_ms=observed_ms)
        try:
            store = GatewayStateStore.open(
                config.state_root / "gateway.sqlite3",
                now_ms=observed_ms,
            )
            objects = ContentAddressedObjectStore.open(
                config.state_root / "objects",
                now_ms=observed_ms,
            )
            facts = FactLedger.open(
                config.state_root / "facts.sqlite3",
                objects,
                now_ms=observed_ms,
            )
        except Exception:
            if "facts" in locals():
                facts.close()
            if "objects" in locals():
                objects.close()
            if "store" in locals():
                store.close()
            lease.release()
            raise
        runtime = cls(config, lease, store, objects, facts, time.monotonic_ns())
        try:
            life_transport = None
            communication_control = None
            backend_compat_client = None
            life_compat_client = None
            if config.deployment_mode == "embedded":
                from communication_service.embedded_runtime import EmbeddedCommunicationService
                from life_service.embedded_runtime import EmbeddedLifeRuntime
                from .embedded_backend import EmbeddedBackendRuntime
                from .life_client import InProcessLifeJsonTransport
                from .service_ports import CompatibilityJsonClient

                runtime.life_service = EmbeddedLifeRuntime.from_environment(
                    gateway_state_root=config.state_root,
                    mode="embedded",
                    gateway_environment=config.environment,
                )
                runtime.communication_service = EmbeddedCommunicationService.start(
                    gateway_state_root=config.state_root,
                    gateway_environment=config.environment,
                    gateway_token=config.communication_api_token or config.backend_internal_token,
                    shadow_token=config.shadow_api_token or config.backend_internal_token,
                    mode="embedded",
                )
                # The backend's Omni client resolves the actual filesystem
                # workspace itself.  Bind that resolution to the same
                # Gateway-authorized workspace before the embedded backend is
                # initialized; otherwise its grant request cannot match the
                # outer execution ticket.
                if config.workspace_root is not None:
                    workspace_text = str(config.workspace_root.resolve(strict=True))
                    os.environ["TIANGONG_DESKTOP_WORKSPACE_ROOT"] = workspace_text
                    os.environ["TIANGONG_WORKSPACE_ROOT"] = workspace_text
                runtime.backend_service = EmbeddedBackendRuntime.start(
                    release_source_root=config.release_source_root,
                )
                def decide_autonomous_activity(
                    activity_scope: object,
                    task: object,
                ) -> dict[str, object]:
                    scoped = dict(activity_scope) if isinstance(activity_scope, dict) else {}
                    task_payload = dict(task) if isinstance(task, dict) else {}
                    status, payload, _ = runtime.backend_service.request(
                        "POST",
                        "/api/v1/internal/autonomy/activity",
                        {"activity_scope": scoped, "task": task_payload},
                        timeout_seconds=240,
                    )
                    if status >= 400 or payload.get("ok") is not True:
                        raise RuntimeError(
                            str(payload.get("error") or "autonomy activity decision failed")
                        )
                    decision = payload.get("decision")
                    if not isinstance(decision, dict):
                        raise RuntimeError("autonomy activity decision is invalid")
                    return decision

                runtime.life_service.set_autonomy_decider(
                    decide_autonomous_activity
                )
                # The Life heartbeat owns its schedule and state, while the
                # gateway owns model credentials.  Give Life a model-only
                # callback rather than reviving the legacy LifeOrchestrator or
                # allowing the model to write any registry directly.
                def decide_autonomous_learning(activity_scope: object) -> dict[str, object]:
                    scoped = dict(activity_scope) if isinstance(activity_scope, dict) else {}
                    scoped["available_actions"] = artifact_action_catalog()
                    status, payload, _ = runtime.backend_service.request(
                        "POST",
                        "/api/v1/internal/learning/decision",
                        {"source": "autonomous", "activity_scope": scoped},
                        timeout_seconds=240,
                    )
                    if status >= 400 or payload.get("ok") is not True:
                        raise RuntimeError(str(payload.get("error") or "learning model decision failed"))
                    decision = payload.get("decision")
                    if not isinstance(decision, dict):
                        raise RuntimeError("learning model decision is invalid")
                    return decision

                runtime.life_service.set_learning_decider(decide_autonomous_learning)

                def decide_capability_patch(material: object) -> dict[str, object]:
                    scoped = dict(material) if isinstance(material, dict) else {}
                    scoped["available_actions"] = artifact_action_catalog()
                    status, payload, _ = runtime.backend_service.request(
                        "POST",
                        "/api/v1/internal/capability/patch/decision",
                        {"material": scoped},
                        timeout_seconds=240,
                    )
                    if status >= 400 or payload.get("ok") is not True:
                        raise RuntimeError(str(payload.get("error") or "capability patch decision failed"))
                    decision = payload.get("decision")
                    if not isinstance(decision, dict):
                        raise RuntimeError("capability patch decision is invalid")
                    return decision

                runtime.life_service.set_capability_patch_decider(decide_capability_patch)

                # Learning-share copywriter: the model rephrases a completed
                # learning into a short user-facing share message.  It reuses
                # the model-only synthesis lane; Life owns fail-soft fallback,
                # so an unavailable model degrades to the template report.
                def write_learning_share(material: object) -> str:
                    scoped = dict(material) if isinstance(material, dict) else {}
                    status, payload, _ = runtime.backend_service.request(
                        "POST",
                        "/api/v1/internal/share/compose",
                        {"occasion": "learning_share", "material": scoped},
                        timeout_seconds=120,
                    )
                    if status >= 400 or payload.get("ok") is not True:
                        raise RuntimeError(str(payload.get("error") or "learning share synthesis failed"))
                    preview = payload.get("preview")
                    if not isinstance(preview, dict):
                        raise RuntimeError("learning share synthesis is invalid")
                    return str(preview.get("summary") or "").strip()

                runtime.life_service.set_learning_share_writer(write_learning_share)

                # Greeting copywriter: same persona voice lane, used by the
                # life scheduler's random user-greeting event.
                def write_greeting(material: object) -> str:
                    scoped = dict(material) if isinstance(material, dict) else {}
                    status, payload, _ = runtime.backend_service.request(
                        "POST",
                        "/api/v1/internal/share/compose",
                        {"occasion": "greeting", "material": scoped},
                        timeout_seconds=120,
                    )
                    if status >= 400 or payload.get("ok") is not True:
                        raise RuntimeError(str(payload.get("error") or "greeting compose failed"))
                    preview = payload.get("preview")
                    if not isinstance(preview, dict):
                        raise RuntimeError("greeting compose is invalid")
                    return str(preview.get("summary") or "").strip()

                runtime.life_service.set_greeting_writer(write_greeting)

                # Self-iteration reviewer: the model proposes bounded self-code
                # upgrade cards on a slow cadence.  Every card waits for the
                # user; the backend-owned apply lane only runs after an
                # explicit confirm and keeps per-file rollback.
                def decide_self_iteration(activity_scope: object) -> dict[str, object]:
                    scoped = dict(activity_scope) if isinstance(activity_scope, dict) else {}
                    status, payload, _ = runtime.backend_service.request(
                        "POST",
                        "/api/v1/internal/self-iteration/decision",
                        {"activity_scope": scoped},
                        timeout_seconds=240,
                    )
                    if status >= 400 or payload.get("ok") is not True:
                        raise RuntimeError(str(payload.get("error") or "self-iteration model decision failed"))
                    decision = payload.get("decision")
                    if not isinstance(decision, dict):
                        raise RuntimeError("self-iteration model decision is invalid")
                    return decision

                runtime.life_service.set_self_iteration_decider(decide_self_iteration)

                def apply_upgrade_changes(material: object) -> dict[str, object]:
                    scoped = dict(material) if isinstance(material, dict) else {}
                    status, payload, _ = runtime.backend_service.request(
                        "POST",
                        "/api/v1/internal/self-iteration/apply",
                        {"changes": scoped.get("changes") or []},
                        timeout_seconds=120,
                    )
                    if status >= 400:
                        return {"ok": False, "error": str(payload.get("error") or "self-iteration apply failed")}
                    return payload if isinstance(payload, dict) else {"ok": False, "error": "self-iteration apply result invalid"}

                runtime.life_service.set_upgrade_executor(apply_upgrade_changes)

                def artifact_action_catalog() -> list[dict[str, object]]:
                    status, payload, _ = runtime.backend_service.request(
                        "GET", "/api/v1/v3/tools", {}, timeout_seconds=30,
                    )
                    if status >= 400 or payload.get("ok") is not True:
                        raise RuntimeError("life artifact action catalog unavailable")
                    rows = payload.get("tools")
                    if not isinstance(rows, list):
                        raise RuntimeError("life artifact action catalog invalid")
                    return [
                        {
                            "action_id": item.get("name"),
                            "risk": item.get("risk") or "A3",
                            "available": not bool(item.get("planOnly")),
                            "effect": item.get("effect") or "",
                        }
                        for item in rows
                        if isinstance(item, dict) and item.get("name")
                    ]

                def publish_learning_artifact(artifact: object) -> dict[str, object]:
                    if not isinstance(artifact, dict):
                        raise RuntimeError("life artifact publisher received invalid artifact")
                    if artifact.get("kind") != "knowledge":
                        # Skill and composite-tool registration stays in the
                        # Life overlay.  It intentionally does not touch the
                        # release-pinned backend Skill Catalog.
                        return {
                            "publisher": "life_skill_overlay",
                            "overlay_key": artifact.get("artifact_id"),
                            "registered": True,
                        }
                    document = artifact.get("document")
                    if not isinstance(document, dict) or not isinstance(document.get("content"), str):
                        raise RuntimeError("life knowledge artifact document invalid")
                    content = document["content"].encode("utf-8")
                    data_url = "data:text/markdown;base64," + base64.b64encode(content).decode("ascii")
                    status, payload, _ = runtime.backend_service.request(
                        "POST",
                        "/api/v1/knowledge/import",
                        {"items": [{"name": document.get("name") or "life-learning.md", "type": "text/markdown", "dataUrl": data_url}]},
                        timeout_seconds=300,
                    )
                    imported = payload.get("imported") if isinstance(payload, dict) else None
                    first = imported[0] if isinstance(imported, list) and imported and isinstance(imported[0], dict) else {}
                    document_id = str(first.get("document_id") or "")
                    if status >= 400 or payload.get("ok") is not True or not document_id:
                        raise RuntimeError("life knowledge publication failed")
                    return {
                        "publisher": "knowledge_store",
                        "knowledge_document_id": document_id,
                        "registered": True,
                    }

                def invoke_learning_artifact_action(
                    action_id: object,
                    arguments: object,
                    capability_context: object,
                ) -> dict[str, object]:
                    if not isinstance(arguments, dict) or not isinstance(capability_context, dict):
                        raise RuntimeError("life artifact action arguments invalid")
                    worker = runtime.orchestration
                    if worker is None:
                        return {"ok": False, "error_code": "life_capability.execution_authority_unavailable"}
                    try:
                        with worker.authorize_life_capability_action(
                            life_id=str(capability_context.get("life_id") or ""),
                            artifact_id=str(capability_context.get("artifact_id") or ""),
                            artifact_sha256=str(capability_context.get("artifact_sha256") or ""),
                            execution_id=str(capability_context.get("execution_id") or ""),
                            step_id=str(capability_context.get("step_id") or ""),
                            action_id=str(action_id or ""),
                            arguments=arguments,
                        ) as run_context:
                            status, payload, _ = runtime.backend_service.request(
                                "POST",
                                "/api/v1/internal/life-action/invoke",
                                {
                                    "action_id": str(action_id or ""),
                                    "arguments": arguments,
                                    "run_context": run_context,
                                },
                                timeout_seconds=300,
                            )
                    except Exception as exc:
                        # Preserve a bounded, non-sensitive failing phase in
                        # the Life receipt.  A bare exception class makes an
                        # authorization denial impossible to repair or audit.
                        safe_error = getattr(worker, "_safe_error_code", None)
                        code = (
                            safe_error(exc)
                            if callable(safe_error)
                            else str(getattr(exc, "code", "") or type(exc).__name__)
                        )
                        return {"ok": False, "error_code": f"life_capability.authority:{code}"}
                    if status >= 400 or payload.get("ok") is False:
                        return {"ok": False, "error_code": str(payload.get("error") or "life_action_failed")}
                    return payload

                def research_learning_material(query: object) -> dict[str, object]:
                    status, payload, _ = runtime.backend_service.request(
                        "POST",
                        "/api/v1/internal/life-action/invoke",
                        {
                            "action_id": "omni_body",
                            "arguments": {"action": "web.search", "target": "", "args": {"query": str(query or "")}},
                            "run_context": {},
                        },
                        timeout_seconds=300,
                    )
                    if status >= 400 or payload.get("ok") is False:
                        raise RuntimeError(str(payload.get("error") or "learning_research_failed"))
                    return payload

                def synthesize_learning_material(material: object) -> dict[str, object]:
                    if not isinstance(material, dict):
                        raise RuntimeError("learning synthesis material invalid")
                    status, payload, _ = runtime.backend_service.request(
                        "POST", "/api/v1/internal/learning/synthesize", {"material": material}, timeout_seconds=240,
                    )
                    preview = payload.get("preview") if isinstance(payload, dict) else None
                    if status >= 400 or payload.get("ok") is not True or not isinstance(preview, dict):
                        raise RuntimeError(str(payload.get("error") or "learning_synthesis_failed"))
                    return preview

                runtime.life_service.set_artifact_action_catalog_provider(artifact_action_catalog)
                runtime.life_service.set_artifact_publisher(publish_learning_artifact)
                runtime.life_service.set_capability_workspace_mapper(
                    life_capability_workspace_mapper(config.workspace_root)
                )
                runtime.life_service.set_capability_workspace_remover(
                    life_capability_workspace_remover(config.workspace_root)
                )
                runtime.life_service.set_capability_workspace_marker(
                    life_capability_workspace_marker(config.workspace_root)
                )
                runtime.life_service.set_artifact_invoker(invoke_learning_artifact_action)
                runtime.life_service.set_learning_materializers(
                    researcher=research_learning_material,
                    synthesizer=synthesize_learning_material,
                )

                def life_skill_overlay() -> dict[str, object]:
                    status, payload, _ = runtime.life_service.request(
                        "GET", "/api/v1/v3/life/capabilities/overlay", {}, timeout_seconds=10,
                    )
                    if status >= 400 or payload.get("ok") is not True:
                        return {"ok": False}
                    return payload

                runtime.backend_service.set_life_skill_overlay_provider(life_skill_overlay)

                def life_activity_query(arguments: object) -> dict[str, object]:
                    request = dict(arguments) if isinstance(arguments, dict) else {}
                    status, payload, _ = runtime.life_service.request(
                        "POST",
                        "/api/v1/v3/life/activity/query",
                        request,
                        timeout_seconds=10,
                    )
                    if status >= 400 or payload.get("ok") is not True:
                        return {
                            "ok": False,
                            "error": str(
                                payload.get("error_code")
                                or payload.get("error")
                                or "life_activity_query_failed"
                            ),
                        }
                    return payload

                runtime.backend_service.set_life_activity_query_provider(
                    life_activity_query
                )

                def body_state_query(arguments: object) -> dict[str, object]:
                    return _gateway_body_state_query(runtime, arguments)

                runtime.backend_service.set_body_state_query_provider(
                    body_state_query
                )

                def pending_learning_ingest(arguments: object) -> dict[str, object]:
                    request = dict(arguments) if isinstance(arguments, dict) else {}
                    user_text = str(request.get("user_text") or "").strip()
                    material_text = str(request.get("material_text") or "").strip()
                    material_path = str(request.get("material_path") or "").strip()
                    desired_scope = str(request.get("desired_scope") or "skill").strip()
                    if not user_text:
                        return {"ok": False, "error": "learning_user_text_required"}
                    scope_status, scope_payload, _ = runtime.life_service.request(
                        "GET",
                        "/api/v1/v3/life/learning/activity-scope",
                        {},
                        timeout_seconds=30,
                    )
                    activity_scope = (
                        scope_payload.get("activity_scope")
                        if isinstance(scope_payload, dict)
                        else None
                    )
                    if (
                        scope_status >= 400
                        or scope_payload.get("ok") is not True
                        or not isinstance(activity_scope, dict)
                    ):
                        return {"ok": False, "error": "learning_activity_scope_unavailable"}
                    activity_scope = dict(activity_scope)
                    activity_scope["available_actions"] = artifact_action_catalog()
                    bounded_material = material_text[:12000]
                    bounded_path = material_path[:1000]
                    learning_request = (
                        f"{user_text}\n\n"
                        f"Requested draft scope: {desired_scope}.\n"
                        "Create a preview only. Do not publish or register it."
                    )
                    if bounded_material:
                        learning_request += f"\nMaterial:\n{bounded_material}"
                    if bounded_path:
                        learning_request += f"\nMaterial path reference: {bounded_path}"
                    decision_status, decision_payload, _ = runtime.backend_service.request(
                        "POST",
                        "/api/v1/internal/learning/decision",
                        {
                            "request": learning_request,
                            "source": "user_pending_preview",
                            "activity_scope": activity_scope,
                        },
                        timeout_seconds=240,
                    )
                    decision = (
                        decision_payload.get("decision")
                        if isinstance(decision_payload, dict)
                        else None
                    )
                    if (
                        decision_status >= 400
                        or decision_payload.get("ok") is not True
                        or not isinstance(decision, dict)
                    ):
                        return {
                            "ok": False,
                            "error": str(
                                decision_payload.get("error")
                                or "learning_model_decision_failed"
                            ),
                        }
                    # Use the draft route deliberately. It gives every Skill
                    # or Tool proposal A3+ awaiting_user semantics and cannot
                    # publish without a later, explicit confirmation.
                    draft_status, draft_payload, _ = runtime.life_service.request(
                        "POST",
                        "/api/v1/v3/life/learning/draft",
                        {
                            "decision": decision,
                            "actor": str(request.get("actor") or "model_tool"),
                        },
                        timeout_seconds=240,
                    )
                    if draft_status >= 400 or draft_payload.get("ok") is not True:
                        return {
                            "ok": False,
                            "error": str(
                                draft_payload.get("error_code")
                                or draft_payload.get("error")
                                or "learning_draft_failed"
                            ),
                        }
                    return dict(draft_payload)

                runtime.backend_service.set_learning_ingest_provider(
                    pending_learning_ingest
                )

                def retrieve_knowledge(query: str) -> dict[str, object]:
                    status, payload, _ = runtime.backend_service.request(
                        "POST",
                        "/api/v1/knowledge/search",
                        {"query": str(query or ""), "top_k": 6, "per_doc": 3},
                        timeout_seconds=30,
                    )
                    if status >= 400 or payload.get("ok") is not True:
                        return {"ok": False, "cards": []}
                    return payload

                life_transport = InProcessLifeJsonTransport(runtime.life_service)
                communication_control = runtime.communication_service
                backend_compat_client = CompatibilityJsonClient(runtime.backend_service)
                life_compat_client = CompatibilityJsonClient(runtime.life_service)
            if config.execution_assembly_configured:
                runtime.orchestration = GatewayOrchestrationWorker.from_runtime_config(
                    config=config,
                    activator=runtime.active_requests,
                    store=store,
                    objects=objects,
                    facts=facts,
                    gateway_epoch=lease.gateway_epoch,
                    gateway_instance_id=lease.instance_id,
                    now_ms=observed_ms,
                    life_transport=life_transport,
                    communication_control=communication_control,
                    backend_compat_client=backend_compat_client,
                    life_compat_client=life_compat_client,
                    life_execution_commit=(
                        None
                        if runtime.life_service is None
                        else runtime.life_service.commit_execution
                    ),
                    knowledge_retriever=(
                        None
                        if runtime.backend_service is None
                        else retrieve_knowledge
                    ),
                )
                release_manifest_path = runtime.orchestration.release_manifest_path
                if config.environment == "production" and release_manifest_path is None:
                    raise RuntimeError("production readiness manifest origin is missing")
                if release_manifest_path is not None:
                    embedded_services = None
                    if config.deployment_mode == "embedded":
                        embedded_services = {
                            "tiangong-backend": runtime.backend_service,
                            "tiangong-life-service": runtime.life_service,
                            "tiangong-communication-service": runtime.communication_service,
                        }
                    runtime.readiness_collector = ProductionReadinessCollector(
                        release=runtime.orchestration.release_manifest,
                        release_manifest_path=release_manifest_path,
                        gateway_epoch=lease.gateway_epoch,
                        gateway_instance_id=lease.instance_id,
                        backend_token=config.backend_internal_token,
                        life_token=config.life_internal_token,
                        communication_token=config.communication_api_token,
                        allow_development_release=config.environment != "production",
                        embedded_services=embedded_services,
                    )
                runtime.orchestration.start()
                runtime.cutover = ChannelCutoverCoordinator(
                    runtime=runtime,
                    communication_token=config.communication_api_token,
                    communication_control=communication_control,
                    component_manifest=runtime.orchestration.component_manifest,
                    delivery_trust_bundle_factory=runtime.orchestration.delivery_trust_bundle,
                )
                runtime.cutover.start()
        except Exception:
            runtime.close()
            raise
        return runtime

    def health_payload(self) -> dict[str, object]:
        uptime_ms = max(0, (time.monotonic_ns() - self._started_monotonic_ns) // 1_000_000)
        module_health: dict[str, object] = {}
        for name, service in (
            ("runtime", self.backend_service),
            ("life", self.life_service),
            ("communication", self.communication_service),
        ):
            if service is None:
                module_health[name] = None
                continue
            try:
                module_health[name] = service.health_payload()
            except Exception as exc:
                module_health[name] = {"status": "UNAVAILABLE", "error_type": type(exc).__name__}
        life_health = module_health.get("life")
        life_ready = bool(isinstance(life_health, dict) and life_health.get("life_ready") is True)
        return {
            "component_id": COMPONENT_ID,
            "api_contract": "tiangong.total-gateway.api.v1",
            "gateway_port": self.config.port,
            "deployment_mode": self.config.deployment_mode,
            "instance_id": self.lease.instance_id,
            "gateway_epoch": self.lease.gateway_epoch,
            "status": "ALIVE",
            "uptime_ms": uptime_ms,
            "life_ready": life_ready,
            "life_available": life_ready,
            "life_error": "" if life_ready else "life.module.not_ready",
            "degraded": self.config.deployment_mode == "embedded" and not life_ready,
            "active_request_activation_configured": True,
            "execution_assembly_configured": self.orchestration is not None,
            "execution_effects_permitted": self.orchestration is not None,
            "orchestration": (
                {"configured": False}
                if self.orchestration is None
                else self.orchestration.status_payload()
            ),
            "channel_cutover": (
                {"configured": False}
                if self.cutover is None
                else self.cutover.status_payload()
            ),
            "embedded_modules": module_health,
        }

    def ready_payload(self, *, now_ms: int | None = None) -> tuple[int, dict[str, object]]:
        observed_ms = int(time.time() * 1_000) if now_ms is None else now_ms
        if self.config.deployment_mode == "embedded":
            return self._embedded_ready_payload(now_ms=observed_ms)
        if self.readiness_collector is not None:
            try:
                expectation, evidence, authenticated, binary_verified = (
                    self.readiness_collector.collect(now_ms=observed_ms)
                )
                self.readiness.update(
                    expectation,
                    evidence,
                    authenticated_component_ids=authenticated,
                    binary_verified_component_ids=binary_verified,
                )
            except Exception as error:
                # A collector fault must never preserve a formerly READY
                # snapshot.  Fail closed with a stable, non-secret diagnostic
                # and let the next request retry.
                self.readiness.mark_collection_failed(error, now_ms=observed_ms)
        status, payload = self.readiness.evaluate(now_ms=observed_ms)
        profile = getattr(self.readiness_collector, "evidence_profile", "unconfigured")
        payload["readiness_profile"] = profile
        payload["production_release_evidence_complete"] = bool(
            status == 200 and profile == "production"
        )
        return status, payload

    def _embedded_ready_payload(self, *, now_ms: int) -> tuple[int, dict[str, object]]:
        disk = self.disk.check(now_ms=now_ms)
        store = self.readiness._check_store(now_ms=now_ms)
        objects = self.readiness._check_objects(now_ms=now_ms)
        facts = self.readiness._check_facts(now_ms=now_ms)
        reasons: list[str] = []
        if not self.lease.active:
            reasons.append("readiness.instance_lease.inactive")
        if not disk.healthy:
            reasons.append(disk.reason_code)
        if not store.healthy:
            reasons.append(store.reason_code)
        if not objects.healthy:
            reasons.append(objects.reason_code)
        if not facts.healthy:
            reasons.append(facts.reason_code)
        module_payloads: dict[str, object] = {}
        for name, service in (
            ("runtime", self.backend_service),
            ("life", self.life_service),
            ("communication", self.communication_service),
        ):
            if service is None:
                reasons.append(f"readiness.embedded.{name}.missing")
                module_payloads[name] = {"status": "NOT_CONFIGURED"}
                continue
            try:
                status, payload = service.ready_payload(now_ms=now_ms)
            except Exception as exc:
                reasons.append(f"readiness.embedded.{name}.probe_failed")
                module_payloads[name] = {
                    "status": "NOT_READY",
                    "error_type": type(exc).__name__,
                }
                continue
            module_payloads[name] = payload
            if status != 200 or str(payload.get("status") or "") != "READY":
                reasons.append(f"readiness.embedded.{name}.not_ready")
        if self.orchestration is None:
            reasons.append("readiness.orchestration.not_configured")
        release_status: int | None = None
        release_payload: dict[str, object] | None = None
        if self.readiness_collector is not None:
            try:
                expectation, evidence, authenticated, binary_verified = (
                    self.readiness_collector.collect(now_ms=now_ms)
                )
                self.readiness.update(
                    expectation,
                    evidence,
                    authenticated_component_ids=authenticated,
                    binary_verified_component_ids=binary_verified,
                )
            except Exception as error:
                self.readiness.mark_collection_failed(error, now_ms=now_ms)
            release_status, release_payload = self.readiness.evaluate(now_ms=now_ms)
            if release_status != 200:
                reasons.extend(str(item) for item in release_payload.get("reason_codes", []))
        elif self.config.environment == "production":
            reasons.append("readiness.release_evidence.not_configured")
        ready = not reasons
        status = 200 if ready else 503
        # 草案 §1.2：process_ready 与 action_ready 分离。已知安全缺陷、fence
        # 或账本未对齐时 action_ready=false，不得为满足 RTO 假报 READY。
        try:
            fence = self.store.action_fence_status()
            unreconciled = self.store.count_unreconciled_attempts()
            retirement = self.store.confirmation_retirement_status()
        except Exception as exc:  # noqa: BLE001 - 安全事实缺失 fail-closed
            fence = {"error": f"{type(exc).__name__}: {exc}", "fenced": True, "display": "unknown", "draining": True}
            unreconciled = -1
            retirement = {"error": True, "retired": True, "receipt_committed": False}
        # 草案 §3.1：fence 提交后的 drain 期（fenced/draining）action_ready=false；
        # drain 完成且对账清零后 fence 仅是旧票据边界，不再是实时阻断（vNext 生效）。
        fence_blocking = bool(fence.get("draining") or fence.get("error"))
        action_ready = bool(
            ready
            and not fence_blocking
            and unreconciled == 0
            # 草案 §4.2：retirement fact 已提交但 receipt 未提交期间，action_ready=false
            and (not retirement.get("retired") or retirement.get("receipt_committed"))
        )
        release_profile = (
            self.readiness_collector.evidence_profile
            if self.readiness_collector is not None
            else "embedded-development-unbound"
        )
        logical_components = (
            "tiangong-backend",
            "tiangong-communication-service",
            "tiangong-life-service",
            "tiangong-total-gateway",
        )
        return status, {
            "component_id": COMPONENT_ID,
            "instance_id": self.lease.instance_id,
            "gateway_epoch": self.lease.gateway_epoch,
            "deployment_mode": "embedded",
            "status": "READY" if ready else "NOT_READY",
            "http_status": status,
            "process_ready": ready,
            "action_ready": action_ready,
            "action_fence": fence,
            "unreconciled_attempts": unreconciled,
            "reason_codes": sorted(set(reasons)),
            "disk": disk.as_dict(),
            "store": store.as_dict(),
            "objects": objects.as_dict(),
            "facts": {
                "healthy": facts.healthy,
                "reason_code": facts.reason_code,
                "schema_sha256": facts.schema_sha256,
            },
            "modules": module_payloads,
            "readiness_profile": release_profile,
            "production_release_evidence_complete": bool(
                ready
                and self.config.environment == "production"
                and self.orchestration is not None
                and release_status == 200
                and release_profile == "production"
            ),
            "release_evidence": release_payload,
            "topology": {
                "physical_python_processes": 1,
                "listener_ports": [self.config.port],
                "logical_component_ids": list(logical_components),
                "legacy_7174_listener": False,
                "embedded_single_process_7184": self.config.port == 7184,
            },
            "decision": {
                "status": "READY" if ready else "NOT_READY",
                "verified_component_ids": list(logical_components) if ready else [],
                "failed_component_ids": [] if ready else list(logical_components),
                "reason_codes": sorted(set(reasons)),
            },
        }

    def begin_channel_cutover(self, snapshot: ChannelCutoverSnapshot) -> bool:
        return self.store.begin_channel_cutover(
            snapshot,
            current_gateway_epoch=self.lease.gateway_epoch,
        )

    def record_channel_drain(
        self,
        evidence: ChannelDrainEvidence,
    ) -> ChannelCutoverSnapshot:
        return self.store.record_channel_drain(
            evidence,
            current_gateway_epoch=self.lease.gateway_epoch,
        )

    def activate_channel_candidate(
        self,
        cutover_id: str,
        *,
        component_manifest_sha256: str,
        issued_at_ms: int,
        lease_ttl_ms: int = 30_000,
    ) -> ChannelOwnershipRegistration:
        return self.store.activate_channel_candidate(
            cutover_id,
            current_gateway_epoch=self.lease.gateway_epoch,
            component_manifest_sha256=component_manifest_sha256,
            issued_at_ms=issued_at_ms,
            lease_ttl_ms=lease_ttl_ms,
        )

    def renew_channel_candidate(
        self,
        cutover_id: str,
        *,
        issued_at_ms: int,
        lease_ttl_ms: int = 30_000,
    ) -> ChannelOwnershipRegistration:
        return self.store.renew_channel_candidate(
            cutover_id,
            current_gateway_epoch=self.lease.gateway_epoch,
            issued_at_ms=issued_at_ms,
            lease_ttl_ms=lease_ttl_ms,
        )

    def get_active_channel_lease(
        self,
        *,
        channel: str,
        tenant_id: str,
        link_account_id: str,
        now_ms: int,
    ) -> ChannelOwnershipLease | None:
        return self.store.get_active_channel_lease(
            channel=channel,
            tenant_id=tenant_id,
            link_account_id=link_account_id,
            current_gateway_epoch=self.lease.gateway_epoch,
            now_ms=now_ms,
        )

    @staticmethod
    def _close_stage(stage: str, components: tuple[object | None, ...]) -> None:
        """Close one dependency stage without crossing a failed boundary.

        Components inside the same stage are peers and are all given a chance
        to stop.  A later stage is never entered when an earlier stage did not
        quiesce, because it may still be needed by active work for audit, life
        commit, delivery, or recovery.
        """

        errors: list[Exception] = []
        for component in components:
            if component is None:
                continue
            try:
                component.close()  # type: ignore[attr-defined]
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError(f"gateway runtime {stage} failed to close") from errors[0]

    def close(self) -> None:
        # Dependency-aware shutdown.  Do not tear down a lower-level service
        # while a higher-level worker can still use it.  Each failed phase is
        # retryable and retains the instance epoch, preventing another gateway
        # from starting over an ambiguous prior owner.
        self._close_stage("ingress", (self.cutover, self.orchestration))
        self._close_stage("execution", (self.backend_service,))
        self._close_stage(
            "authorities",
            (self.communication_service, self.life_service),
        )
        self._close_stage("resources", (self.facts, self.objects, self.store))
        try:
            self.lease.release()
        except Exception as exc:
            raise RuntimeError("gateway runtime lease failed to release") from exc

    def __enter__(self) -> "GatewayRuntime":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["GatewayReadinessController", "GatewayRuntime"]
