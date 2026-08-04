"""Durable fact-execution journal for Omni Body actions.

This source implementation restores the contract consumed by the V3 body
runtime: deterministic capability manifests, re-entrant execution protection,
idempotent replay, and an append-only per-operation evidence journal.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

_ACTIVE_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "tiangong_fact_execution_depth", default=0
)


def fact_execution_active() -> bool:
    return _ACTIVE_DEPTH.get() > 0


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _canonical(value: Any) -> bytes:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class CompiledCapability:
    """One normalized capability row consumed by the body runtime."""

    id: str
    risk: str
    summary: str
    effect: str
    declared_status: str
    handler: str
    alias_to: str
    implemented: bool
    dynamic: bool
    executable: bool
    reason: str
    metadata_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "risk": self.risk,
            "summary": self.summary,
            "effect": self.effect,
            "declared_status": self.declared_status,
            "handler": self.handler,
            "alias_to": self.alias_to,
            "implemented": self.implemented,
            "dynamic": self.dynamic,
            "executable": self.executable,
            "reason": self.reason,
            "metadata_sha256": self.metadata_sha256,
        }


@dataclass(frozen=True, slots=True)
class CompiledCapabilityManifest:
    """Deterministic capability manifest with object and JSON projections."""

    runtime_class: str
    capabilities: Mapping[str, CompiledCapability]
    dynamic_actions: tuple[str, ...]
    source_hash: str
    validation: Mapping[str, Any]

    def to_dict(self, *, exposed_only: bool = False) -> dict[str, Any]:
        selected = {
            name: capability.to_dict()
            for name, capability in sorted(self.capabilities.items())
            if not exposed_only or capability.executable
        }
        executable = sum(1 for capability in self.capabilities.values() if capability.executable)
        return {
            "schema": "tiangong.v3.capability_manifest.v1",
            "runtime_class": self.runtime_class,
            "source_hash": self.source_hash,
            "total": len(self.capabilities),
            "executable": executable,
            "unavailable": len(self.capabilities) - executable,
            "dynamic_actions": list(self.dynamic_actions),
            "capabilities": selected,
            "validation": _jsonable(dict(self.validation)),
        }


def compile_manifest(
    actions: Mapping[str, Mapping[str, Any]],
    runtime_class: type,
    *,
    dynamic_actions: Iterable[str] = (),
) -> CompiledCapabilityManifest:
    """Compile the action registry into the object contract used by BodyRuntime.

    An action is exposed only when a real execution route exists: a concrete
    runtime handler, a loaded dynamic delivery handler, or a resolvable alias.
    Metadata alone never promotes a planned capability to executable.
    """

    runtime_name = f"{runtime_class.__module__}.{runtime_class.__qualname__}"
    dynamic = {str(item) for item in dynamic_actions if str(item)}
    normalized: dict[str, dict[str, Any]] = {
        str(name): dict(metadata or {})
        for name, metadata in actions.items()
        if str(name)
    }

    route_state: dict[str, tuple[bool, str, str]] = {}

    def resolve_route(name: str, trail: tuple[str, ...] = ()) -> tuple[bool, str, str]:
        cached = route_state.get(name)
        if cached is not None:
            return cached
        if name in trail:
            result = (False, "", "alias cycle")
            route_state[name] = result
            return result
        metadata = normalized.get(name)
        if metadata is None:
            result = (False, "", "alias target is absent")
            route_state[name] = result
            return result
        if not bool(metadata.get("implemented", False)):
            result = (False, "", str(metadata.get("unavailable_reason") or "declared unavailable"))
            route_state[name] = result
            return result

        handler_name = "_action_" + name.replace(".", "_").replace("-", "_")
        if name in dynamic:
            result = (True, "dynamic", "")
        elif callable(getattr(runtime_class, handler_name, None)):
            result = (True, handler_name, "")
        else:
            alias_to = str(metadata.get("alias_to") or "").strip()
            if alias_to:
                alias_ok, _alias_handler, alias_reason = resolve_route(alias_to, trail + (name,))
                result = (
                    alias_ok,
                    f"alias:{alias_to}" if alias_ok else "",
                    "" if alias_ok else f"alias unavailable: {alias_reason}",
                )
            else:
                result = (False, "", "no healthy execution route")
        route_state[name] = result
        return result

    capabilities: dict[str, CompiledCapability] = {}
    for name in sorted(normalized):
        metadata = normalized[name]
        executable, handler, reason = resolve_route(name)
        implemented = bool(metadata.get("implemented", False))
        risk = str(metadata.get("risk") or "A0")
        declared_effect = str(metadata.get("effect") or "").strip()
        effect = declared_effect if declared_effect in {"read", "verify", "create", "write", "update", "execute"} else ("read" if risk == "A0" else "write")
        summary = str(metadata.get("summary") or metadata.get("description") or metadata.get("desc") or "")
        capabilities[name] = CompiledCapability(
            id=name,
            risk=risk,
            summary=summary,
            effect=effect,
            declared_status="implemented" if implemented else "planned",
            handler=handler,
            alias_to=str(metadata.get("alias_to") or ""),
            implemented=implemented,
            dynamic=name in dynamic,
            executable=executable,
            reason=reason,
            metadata_sha256=_sha256(metadata),
        )

    executable_without_route = sorted(
        name
        for name, capability in capabilities.items()
        if capability.executable and not capability.handler
    )
    source_payload = {
        "runtime_class": runtime_name,
        "dynamic_actions": sorted(dynamic),
        "capabilities": {name: capability.to_dict() for name, capability in capabilities.items()},
    }
    source_hash = _sha256(source_payload)
    validation = {
        "ok": not executable_without_route,
        "source_hash": source_hash,
        "executable_without_route": executable_without_route,
    }
    return CompiledCapabilityManifest(
        runtime_class=runtime_name,
        capabilities=capabilities,
        dynamic_actions=tuple(sorted(name for name in dynamic if name in capabilities)),
        source_hash=source_hash,
        validation=validation,
    )


class FactExecutionKernel:
    """Execute one action and persist the observed result as the fact authority.

    The journal is intentionally independent from the user workspace.  It does
    not infer success from intent: the legacy action result remains authoritative
    and its exact normalized payload is recorded before it is returned.
    """

    def __init__(
        self,
        workspace: str | os.PathLike[str],
        ledger_root: str | os.PathLike[str],
        run_id: str,
        *,
        request_id: str = "",
        session_id: str = "",
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.ledger_root = Path(ledger_root).expanduser().resolve()
        self.run_id = str(run_id or "").strip() or f"run_{uuid.uuid4().hex}"
        self.request_id = str(request_id or self.run_id)
        self.session_id = str(session_id or "")
        self._operations = self.ledger_root / "operations"
        self._idempotency = self.ledger_root / "idempotency"
        self._events = self.ledger_root / "events.jsonl"
        for directory in (self.ledger_root, self._operations, self._idempotency):
            directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def _safe(value: str, fallback: str = "item") -> str:
        text = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value or ""))
        return text[:160] or fallback

    @staticmethod
    def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # The final operation record deliberately has a long immutable ID.
        # Do not repeat it in a staging name: deep Windows state roots then
        # exceed the legacy path limit before the atomic replace can occur.
        temp = path.with_name(f"~{uuid.uuid4().hex[:8]}.tmp")
        rendered = json.dumps(
            _jsonable(payload), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        ) + "\n"
        try:
            with temp.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def _idempotency_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._idempotency / f"{digest}.json"

    def _append_event(self, payload: Mapping[str, Any]) -> None:
        line = json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._events.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _replay(self, idempotency_key: str) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        path = self._idempotency_path(idempotency_key)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            result = record.get("result")
            transaction = record.get("fact_transaction")
            if not isinstance(result, dict) or not isinstance(transaction, dict):
                return None
            replayed = _jsonable(result)
            replay_tx = dict(transaction)
            replay_tx["idempotent_replay"] = True
            replay_tx["replayed_at_ms"] = int(time.time() * 1000)
            replayed["fact_transaction"] = replay_tx
            return replayed
        except Exception:
            return None

    def execute(
        self,
        action: str,
        target: str | None,
        args: Mapping[str, Any] | None,
        executor: Callable[[str], Mapping[str, Any]],
        *,
        step_id: str = "",
        task_node_id: str = "",
        expected_version: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        if not callable(executor):
            raise TypeError("executor must be callable")
        action_name = str(action or "").strip()
        if not action_name:
            raise ValueError("action is required")
        normalized_target = str(target or "")
        normalized_args = _jsonable(dict(args or {}))
        normalized_key = str(idempotency_key or "").strip()

        with self._lock:
            replay = self._replay(normalized_key)
            if replay is not None:
                return replay

            started_at_ms = int(time.time() * 1000)
            operation_seed = {
                "domain": "tiangong.v3.fact-operation.v1",
                "run_id": self.run_id,
                "request_id": self.request_id,
                "action": action_name,
                "target": normalized_target,
                "args": normalized_args,
                "idempotency_key": normalized_key,
                "nonce": uuid.uuid4().hex,
            }
            operation_id = "op_" + _sha256(operation_seed)[:32]
            token = _ACTIVE_DEPTH.set(_ACTIVE_DEPTH.get() + 1)
            error: BaseException | None = None
            try:
                raw = executor(operation_id)
                result = dict(raw) if isinstance(raw, Mapping) else {
                    "success": False,
                    "ok": False,
                    "status": "INVALID_RESULT",
                    "error": "action executor returned a non-object result",
                    "raw_result": str(raw),
                }
            except BaseException as exc:  # preserve KeyboardInterrupt/SystemExit after journaling
                error = exc
                result = {
                    "success": False,
                    "ok": False,
                    "status": "EXECUTION_EXCEPTION",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            finally:
                _ACTIVE_DEPTH.reset(token)

            completed_at_ms = int(time.time() * 1000)
            success = bool(result.get("success", result.get("ok", False)))
            transaction = {
                "schema": "tiangong.v3.fact-transaction.v1",
                "operation_id": operation_id,
                "run_id": self.run_id,
                "request_id": self.request_id,
                "session_id": self.session_id,
                "step_id": str(step_id or ""),
                "task_node_id": str(task_node_id or step_id or ""),
                "action": action_name,
                "target": normalized_target,
                "expected_version": str(expected_version or ""),
                "idempotency_key": normalized_key,
                "idempotent_replay": False,
                "state": "OBSERVED" if success else "FAILED",
                "started_at_ms": started_at_ms,
                "completed_at_ms": completed_at_ms,
                "duration_ms": max(0, completed_at_ms - started_at_ms),
                "input_sha256": _sha256({"action": action_name, "target": normalized_target, "args": normalized_args}),
                "result_sha256": _sha256(result),
            }
            returned = _jsonable(result)
            returned["fact_transaction"] = transaction
            record = {
                "schema": "tiangong.v3.fact-operation-record.v1",
                "fact_transaction": transaction,
                "input": {"action": action_name, "target": normalized_target, "args": normalized_args},
                "result": returned,
            }
            self._atomic_json(self._operations / f"{self._safe(operation_id)}.json", record)
            self._append_event({"fact_transaction": transaction})
            if normalized_key:
                self._atomic_json(self._idempotency_path(normalized_key), record)
            if error is not None and isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise error
            return returned


__all__ = [
    "CompiledCapability",
    "CompiledCapabilityManifest",
    "FactExecutionKernel",
    "compile_manifest",
    "fact_execution_active",
]
