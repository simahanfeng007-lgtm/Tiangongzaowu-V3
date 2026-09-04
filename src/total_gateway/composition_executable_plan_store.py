"""Row codecs for P7C.0 executable composition plans.

The records in this module are projections of rows owned by the existing
``GatewayStateStore``.  This module owns no connection, migration runner,
transaction boundary, Policy decision, Ticket, Grant, Runtime call,
verification verdict, or Completion decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts import canonical_json_bytes

from .composition_activation_store import (
    LimitedActivationBundleRegistration,
    LimitedActivationStoreRecord,
    computed_limited_activation_lifecycle_sha256,
)
from .composition_executable_plan import (
    MAX_STORED_EXECUTABLE_PLAN_JSON_BYTES,
    ExecutableCompositionPlanV1,
)


def canonical_executable_plan_json(plan: ExecutableCompositionPlanV1) -> str:
    """Return the one canonical UTF-8 JSON representation stored by Gateway."""

    encoded = canonical_json_bytes(plan.model_dump(mode="json"))
    if len(encoded) > MAX_STORED_EXECUTABLE_PLAN_JSON_BYTES:
        raise ValueError("executable composition plan exceeds the stored byte limit")
    return encoded.decode("utf-8")


def _matches_registration(
    plan: ExecutableCompositionPlanV1,
    registration_record: LimitedActivationStoreRecord,
) -> bool:
    """Check every authority projection inherited from the P7B registration."""

    if not isinstance(registration_record, LimitedActivationStoreRecord):
        return False
    registration = registration_record.registration
    return (
        registration_record.has_valid_lifecycle()
        and registration.has_valid_identity()
        and plan.registration_id == registration.registration_id
        and plan.registration_sha256 == registration.registration_sha256
        and plan.registration_lifecycle_sha256
        == computed_limited_activation_lifecycle_sha256(
            registration_id=registration.registration_id,
            registration_sha256=registration.registration_sha256,
            state="ACTIVE",
            expires_at_ms=registration.expires_at_ms,
            expired_at_ms=None,
        )
        and plan.composition_activation_id
        == registration.composition_activation_id
        and plan.composition_activation_sha256
        == registration.composition_activation_sha256
        and plan.composition_plan_id == registration.composition_plan_id
        and plan.composition_plan_sha256
        == registration.composition_plan_sha256
        and plan.action_registry_sha256 == registration.action_registry_sha256
        and plan.verification_registry_sha256
        == registration.verification_registry_sha256
        and plan.verification_plan_id == registration.verification_plan_id
        and plan.verification_plan_sha256
        == registration.verification_plan_sha256
        and plan.verification_plan_activation_id
        == registration_record.verification_plan_activation_id
        and plan.request_id == registration.request_id
        and plan.run_id == registration.run_id
        and plan.generation == registration.generation
        and plan.principal_scope_hash == registration.principal_scope_hash
        and plan.world_state_sha256 == registration.world_state_sha256
        and plan.source_manifest_sha256 == registration.source_manifest_sha256
        and plan.capability_manifest_sha256
        == registration.capability_manifest_sha256
        and plan.sealed_at_ms == registration.registered_at_ms
        and plan.expires_at_ms == registration.expires_at_ms
    )


@dataclass(frozen=True, slots=True)
class ExecutableCompositionPlanStoreRecord:
    """Fully revalidated projection of one Gateway Store plan row."""

    executable_plan: ExecutableCompositionPlanV1
    created_by_this_call: bool = False
    duplicate: bool = False
    recovered_after_restart: bool = False

    def __post_init__(self) -> None:
        if not isinstance(
            self.executable_plan, ExecutableCompositionPlanV1
        ) or not self.executable_plan.has_valid_identity():
            raise ValueError("executable composition plan identity is invalid")
        if self.created_by_this_call and self.duplicate:
            raise ValueError("executable composition plan creation flags disagree")
        if self.recovered_after_restart and (
            self.created_by_this_call or self.duplicate
        ):
            raise ValueError("recovered executable plan cannot be newly created")

    def active_at(
        self,
        now_ms: int,
        registration_record: LimitedActivationStoreRecord,
    ) -> bool:
        """Return whether this immutable plan still inherits live P7B eligibility."""

        if now_ms < 0:
            raise ValueError("executable composition plan read time is invalid")
        plan = self.executable_plan
        return (
            plan.has_valid_identity()
            and plan.sealed_at_ms <= now_ms < plan.expires_at_ms
            and _matches_registration(plan, registration_record)
            and registration_record.active_at(now_ms)
        )


@dataclass(frozen=True, slots=True)
class ExecutableCompositionBundleRegistration:
    """Atomic P7B registration plus its immutable executable-plan projection."""

    activation_bundle: LimitedActivationBundleRegistration
    record: ExecutableCompositionPlanStoreRecord
    created_by_this_call: bool
    duplicate: bool

    def __post_init__(self) -> None:
        if not isinstance(
            self.activation_bundle, LimitedActivationBundleRegistration
        ) or not isinstance(self.record, ExecutableCompositionPlanStoreRecord):
            raise ValueError("executable composition bundle has the wrong record type")
        if self.created_by_this_call == self.duplicate:
            raise ValueError("executable composition bundle creation flags disagree")
        if (
            self.record.created_by_this_call != self.created_by_this_call
            or self.record.duplicate != self.duplicate
            or self.record.recovered_after_restart
        ):
            raise ValueError("executable composition bundle record flags disagree")
        if (
            self.activation_bundle.created_by_this_call
            != self.created_by_this_call
            or self.activation_bundle.duplicate != self.duplicate
            or self.activation_bundle.record.created_by_this_call
            != self.created_by_this_call
            or self.activation_bundle.record.duplicate != self.duplicate
            or self.activation_bundle.record.recovered_after_restart
        ):
            raise ValueError(
                "executable composition bundle activation flags disagree"
            )

        plan = self.record.executable_plan
        registration_record = self.activation_bundle.record
        registration = registration_record.registration
        receipt = self.activation_bundle.receipt
        if (
            not _matches_registration(plan, registration_record)
            or not registration_record.active_at(plan.sealed_at_ms)
            or plan.registration_id != receipt.registration_id
            or plan.registration_sha256 != receipt.registration_sha256
            or plan.registration_id != registration.registration_id
            or plan.composition_activation_id
            != receipt.composition_activation_id
            or plan.request_id != receipt.request_id
            or plan.run_id != receipt.run_id
            or plan.generation != receipt.generation
            or plan.verification_plan_activation_id
            != self.activation_bundle.verification_plan_activation_id
            or receipt.idempotent_replay != self.duplicate
        ):
            raise ValueError("executable composition bundle authorities disagree")


def executable_plan_record_from_row(
    row: Any,
    *,
    created_by_this_call: bool = False,
    duplicate: bool = False,
    recovered_after_restart: bool = False,
) -> ExecutableCompositionPlanStoreRecord:
    """Parse and fully revalidate one canonical Gateway Store plan row."""

    payload = row["executable_plan_json"]
    if (
        not isinstance(payload, str)
        or len(payload.encode("utf-8")) > MAX_STORED_EXECUTABLE_PLAN_JSON_BYTES
    ):
        raise ValueError(
            "stored executable composition plan payload exceeds the byte limit"
        )
    try:
        plan = ExecutableCompositionPlanV1.model_validate_json(
            payload, strict=True
        )
    except Exception as exc:
        raise ValueError(
            "stored executable composition plan payload is invalid"
        ) from exc
    if not plan.has_valid_identity():
        raise ValueError("stored executable composition plan identity is invalid")
    canonical_json = canonical_executable_plan_json(plan)
    if canonical_json != row["executable_plan_json"]:
        raise ValueError("stored executable composition plan JSON is not canonical")

    expected = {
        "executable_plan_id": plan.executable_plan_id,
        "registration_id": plan.registration_id,
        "registration_sha256": plan.registration_sha256,
        "composition_activation_id": plan.composition_activation_id,
        "composition_activation_sha256": plan.composition_activation_sha256,
        "composition_plan_id": plan.composition_plan_id,
        "composition_plan_sha256": plan.composition_plan_sha256,
        "execution_bindings_sha256": plan.execution_bindings_sha256,
        "action_registry_sha256": plan.action_registry_sha256,
        "verification_registry_sha256": plan.verification_registry_sha256,
        "verification_plan_id": plan.verification_plan_id,
        "verification_plan_sha256": plan.verification_plan_sha256,
        "request_id": plan.request_id,
        "run_id": plan.run_id,
        "generation": plan.generation,
        "principal_scope_hash": plan.principal_scope_hash,
        "world_state_sha256": plan.world_state_sha256,
        "source_manifest_sha256": plan.source_manifest_sha256,
        "capability_manifest_sha256": plan.capability_manifest_sha256,
        "workspace_id": plan.workspace.workspace_id,
        "workspace_scope_hash": plan.workspace.workspace_scope_sha256,
        "sealed_at_ms": plan.sealed_at_ms,
        "expires_at_ms": plan.expires_at_ms,
        "step_count": len(plan.step_bindings),
        "executable_plan_json": canonical_json,
        "executable_plan_sha256": plan.executable_plan_sha256,
    }
    for field, value in expected.items():
        if row[field] != value:
            raise ValueError(
                "stored executable composition plan column disagrees with "
                "canonical payload: "
                + field
            )

    return ExecutableCompositionPlanStoreRecord(
        executable_plan=plan,
        created_by_this_call=created_by_this_call,
        duplicate=duplicate,
        recovered_after_restart=recovered_after_restart,
    )


__all__ = [
    "MAX_STORED_EXECUTABLE_PLAN_JSON_BYTES",
    "ExecutableCompositionBundleRegistration",
    "ExecutableCompositionPlanStoreRecord",
    "canonical_executable_plan_json",
    "executable_plan_record_from_row",
]
