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

_PREFIX = "method-plan:"


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
        # The pin is written under the runtime/store locks by the verified
        # reader below. Eviction before that boundary fails; no latest fallback.
        state = self.world.store.get(state_id)
        if state is None or state.state_ref.sha256 != plan.world_state_sha256:
            raise ValueError("METHOD_RUN_WORLD_UNAVAILABLE")
        ref = state.state_ref
        if expected_state_ref is not None and expected_state_ref != ref:
            raise ValueError("METHOD_RUN_CALLER_WORLD_MISMATCH")
        methods = self.world.method_world_for_state(
            ref, scope=scope, retention_owner=_PREFIX+plan.executable_plan_id,
            expected_method_source_refs=plan.legacy_plan.method_source_refs)
        self._generation(plan)  # Recheck after slow archive verification.
        return methods

    def reconcile(self) -> tuple[str, ...]:
        """Release only verifiably cancelled/released/superseded generations.

        Missing/corrupt Gateway data raises and retains references. Expired
        leases, grants or reviews alone are not evidence of task completion.
        This can run on operator installation/restart or explicit lifecycle
        maintenance; it owns no timer, daemon or autonomous task scheduler.
        """
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
                releasable.append(pin)
        released=[]
        # Validate the entire sweep before mutating retention metadata.
        for pin in releasable:
            if self.world.store.release_retained_state(pin):
                released.append(pin.owner_id)
        return tuple(released)
