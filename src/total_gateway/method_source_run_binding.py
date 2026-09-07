"""Read Method Sources from existing sealed Gateway plans and retain their World refs.

No task, generation, grant or expiry state is stored here. The Gateway remains
that authority; WorldState retention is garbage-collection metadata only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.scope import WorldScope
from world_understanding.production import ProductionWorldUnderstandingRuntime
from .store import GatewayStateStore
from .store_unit_of_work import gateway_store_write_transaction
from .composition_executable_plan import ExecutableCompositionPlanV1
from world_understanding.world_state.retention import RetainedWorldState

_PREFIX = "method-plan:"


def requires_method_retention(plan, *, configured: bool) -> bool:
    """P9 admission scope comes from the sealed source/World identity.

    Concrete WorldState references and native JSON sources always require the
    lifecycle, even on an unconfigured connection. Once configured, ALL Method
    plans do. Historical non-World P7 migration fixtures/compatibility remain
    on their old route; this is not the later P12 legacy-planner cutover.
    """
    refs = plan.legacy_plan.method_source_refs
    return bool(refs) and (
        configured or plan.legacy_plan.world_state_ref.startswith("wst_")
        or any(path.lower().endswith(".json") for ref in refs for path in ref.source_files)
    )


@dataclass(frozen=True, slots=True)
class MethodRunSourceResolver:
    gateway: GatewayStateStore
    world: ProductionWorldUnderstandingRuntime

    def __post_init__(self) -> None:
        if not isinstance(self.gateway, GatewayStateStore) or type(self.world) is not ProductionWorldUnderstandingRuntime:
            raise TypeError("METHOD_RUN_EXISTING_AUTHORITIES_REQUIRED")

    def _generation(self, plan) -> dict:
        current = self.gateway.get_request_generation_binding(plan.request_id)
        if (current is None or current["run_id"] != plan.run_id
                or current["current_generation"] != plan.generation
                or current["status"] != "ACTIVE"):
            raise ValueError("METHOD_RUN_GENERATION_NOT_ACTIVE")
        return current

    def read(self, *, request_id: str, run_id: str, generation: int,
             scope: WorldScope, expected_state_ref: WorldRecordRef | None = None):
        """Resolve the sealed plan's exact source set, never a caller's latest state.

        This is a read/retention boundary, not dispatch authorization. The normal
        Policy/Ticket/Grant checks must still run independently for every step.
        """
        # The existing SQLite UoW excludes a second connection's terminal or
        # generation commit until verification and pin selection finish.
        with self.gateway._lock, self.gateway._write_transaction():
            return self._read_locked(request_id=request_id, run_id=run_id, generation=generation,
                                     scope=scope, expected_state_ref=expected_state_ref)

    def _read_locked(self, *, request_id: str, run_id: str, generation: int,
                     scope: WorldScope, expected_state_ref: WorldRecordRef | None):
        if (type(request_id) is not str or re.fullmatch(r"req_[0-9a-f]{64}", request_id) is None
                or type(run_id) is not str or re.fullmatch(r"run_[0-9a-f]{64}", run_id) is None
                or type(generation) is not int or generation < 0 or type(scope) is not WorldScope):
            raise ValueError("METHOD_RUN_IDENTITY_INVALID")
        # Reads from the existing Store revalidate immutable plan projections.
        record = self.gateway.get_executable_composition_plan_for_request(
            request_id, run_id=run_id, generation=generation)
        if record is None:
            raise ValueError("METHOD_RUN_REGISTERED_PLAN_REQUIRED")
        plan = record.executable_plan
        self._generation(plan)
        workspace = {b.key:b.value for b in scope.scope_bindings}.get("workspace_id")
        if (plan.principal_scope_hash != scope.principal_scope_hash
                or plan.workspace.workspace_id != workspace
                or not plan.legacy_plan.method_source_refs):
            raise ValueError("METHOD_RUN_PLAN_SCOPE_MISMATCH")
        state_id = plan.legacy_plan.world_state_ref
        if re.fullmatch(r"wst_[0-9a-f]{64}", state_id) is None:
            raise ValueError("METHOD_RUN_WORLD_ID_INVALID")
        # Configured plans were pinned before registration committed. The
        # legacy explicit reader may pin here; missing data never selects latest.
        state = self.world.store.get(state_id)
        if state is None or state.state_ref.sha256 != plan.world_state_sha256:
            raise ValueError("METHOD_RUN_WORLD_UNAVAILABLE")
        ref = state.state_ref
        if expected_state_ref is not None and expected_state_ref != ref:
            raise ValueError("METHOD_RUN_CALLER_WORLD_MISMATCH")
        owner = _PREFIX+plan.executable_plan_id
        if self.gateway._method_source_resolver is not None:
            if RetainedWorldState(owner, ref, scope) not in self.world.store.retained_states():
                raise ValueError("METHOD_RETENTION_REQUIRED_BEFORE_READ")
            owner = None  # Configured task reads never repair a lost admission pin.
        methods = self.world.method_world_for_state(
            ref, scope=scope, retention_owner=owner,
            expected_method_source_refs=plan.legacy_plan.method_source_refs)
        self._generation(plan)  # Recheck after slow archive verification.
        return methods

    def _plan_pin(self, plan) -> RetainedWorldState:
        if type(plan) is not ExecutableCompositionPlanV1 or not plan.has_valid_identity():
            raise ValueError("METHOD_RETENTION_PLAN_IDENTITY_INVALID")
        self._generation(plan)
        state_id = plan.legacy_plan.world_state_ref
        if re.fullmatch(r"wst_[0-9a-f]{64}", state_id) is None:
            raise ValueError("METHOD_RUN_WORLD_ID_INVALID")
        state = self.world.store.get(state_id)
        if state is None or state.state_ref.sha256 != plan.world_state_sha256:
            raise ValueError("METHOD_RUN_WORLD_UNAVAILABLE")
        scope = state.state.scope
        workspace = {b.key: b.value for b in scope.scope_bindings}.get("workspace_id")
        if (scope.principal_scope_hash != plan.principal_scope_hash
                or workspace != plan.workspace.workspace_id
                or not plan.legacy_plan.method_source_refs):
            raise ValueError("METHOD_RUN_PLAN_SCOPE_MISMATCH")
        return RetainedWorldState(_PREFIX + plan.executable_plan_id, state.state_ref, scope)

    def retain_before_admission(self, plan) -> RetainedWorldState | None:
        """Called inside the existing Gateway registration transaction.

        Verify immutable source bytes, then durably pin before SQLite commits.
        Lock order is Gateway -> WorldStore, with no Runtime lock or callback.
        Returns ONLY a newly created pin, for known-rollback cleanup.
        """
        with self.gateway._lock, self.world.store.retention_transaction():
            pin = self._plan_pin(plan)
            existing = next((p for p in self.world.store.retained_states()
                             if p.owner_id == pin.owner_id), None)
            if existing is not None and existing != pin:
                raise ValueError("METHOD_RETENTION_OWNER_REBOUND")
            self.world.method_world_for_state(
                pin.state_ref, scope=pin.scope, retention_owner=pin.owner_id,
                expected_method_source_refs=plan.legacy_plan.method_source_refs)
            self._generation(plan)
            return pin if existing is None else None

    def require_retained_plan(self, plan) -> None:
        """Dispatch checks an existing pin; it never repairs a missing admission."""
        with self.gateway._lock, self.world.store.retention_transaction():
            pin = self._plan_pin(plan)
            if pin not in self.world.store.retained_states():
                raise ValueError("METHOD_RETENTION_REQUIRED_BEFORE_DISPATCH")
            self.world.method_world_for_state(
                pin.state_ref, scope=pin.scope,
                expected_method_source_refs=plan.legacy_plan.method_source_refs)
            self._generation(plan)

    def release_rolled_back_admissions(self, pins: tuple[RetainedWorldState, ...]) -> None:
        """Only clean references proven newly created by this failed local UoW.

        A missing row discovered after PROCESS RESTART is not such proof and
        remains an unresolved orphan. Neither absence nor expiry is completion.
        """
        with self.gateway._lock:
            if self.gateway._connection.in_transaction:
                raise ValueError("METHOD_RETENTION_ROLLBACK_NOT_FINAL")
            # Hold the existing SQLite writer exclusion while observing absence
            # and releasing. Otherwise another connection can commit the same
            # plan using the old pin between these two operations.
            with gateway_store_write_transaction(self.gateway._connection):
                self._release_rolled_back_locked(pins)

    def _release_rolled_back_locked(self, pins):
        removable = []
        for pin in pins:
            if type(pin) is not RetainedWorldState or not pin.owner_id.startswith(_PREFIX):
                raise ValueError("METHOD_RETENTION_ROLLBACK_PIN_INVALID")
            record = self.gateway.get_executable_composition_plan_record(pin.owner_id[len(_PREFIX):])
            if record is None:
                removable.append(pin)
            elif (record.executable_plan.legacy_plan.world_state_ref != pin.state_ref.record_id
                  or record.executable_plan.world_state_sha256 != pin.state_ref.sha256):
                raise ValueError("METHOD_RETENTION_ROLLBACK_OWNER_MISMATCH")
        for pin in removable:
            self.world.store.release_retained_state(pin)

    def reconcile(self) -> tuple[str, ...]:
        """Release only verifiably cancelled/released/superseded generations.

        Missing/corrupt Gateway data raises and retains references. Expired
        leases, grants or reviews alone are not evidence of task completion.
        This can run on operator installation/restart or explicit lifecycle
        maintenance; it owns no timer, daemon or autonomous task scheduler.
        """
        with self.gateway._lock, gateway_store_write_transaction(self.gateway._connection):
            return self._reconcile_locked()

    def _reconcile_locked(self) -> tuple[str, ...]:
        releasable=[]
        for pin in self.world.store.retained_states():
            if not pin.owner_id.startswith(_PREFIX):
                continue
            executable_id=pin.owner_id[len(_PREFIX):]
            record=self.gateway.get_executable_composition_plan_record(executable_id)
            if record is None:
                raise ValueError("METHOD_RUN_RETENTION_OWNER_MISSING")
            plan=record.executable_plan
            if (pin.state_ref.record_id!=plan.legacy_plan.world_state_ref
                    or pin.state_ref.sha256!=plan.world_state_sha256
                    or pin.scope.principal_scope_hash!=plan.principal_scope_hash
                    or dict((b.key,b.value) for b in pin.scope.scope_bindings).get("workspace_id")!=plan.workspace.workspace_id):
                raise ValueError("METHOD_RUN_RETENTION_OWNER_MISMATCH")
            current=self.gateway.get_request_generation_binding(plan.request_id)
            if current is None or current["status"] not in {"ACTIVE", "CANCELLED", "RELEASED"}:
                raise ValueError("METHOD_RUN_RETENTION_GENERATION_UNKNOWN")
            if current["run_id"]!=plan.run_id:
                # Do not guess run ordering or infer a terminal state.
                raise ValueError("METHOD_RUN_RETENTION_RUN_MISMATCH")
            if current["current_generation"] < plan.generation:
                raise ValueError("METHOD_RUN_RETENTION_GENERATION_REGRESSED")
            if (current["current_generation"]>plan.generation
                    or current["status"] in {"CANCELLED", "RELEASED"}):
                if not self.gateway.method_source_has_unresolved_execution(plan):
                    releasable.append(pin)
        released=[]
        # Validate the entire sweep before mutating retention metadata.
        for pin in releasable:
            if self.world.store.release_retained_state(pin):
                released.append(pin.owner_id)
        return tuple(released)
