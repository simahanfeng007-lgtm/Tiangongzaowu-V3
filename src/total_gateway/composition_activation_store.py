"""P7B.2 persistence records for limited composition activation.

The records in this module are projections of rows owned by the existing
``GatewayStateStore``.  This module owns no connection, migration runner,
transaction boundary, scheduler, Policy decision, Ticket, Grant, Runtime call,
verification verdict, or Completion decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from contracts import canonical_json_bytes, canonical_sha256

from .composition_activation_registration import (
    LimitedActivationRegistrationReceiptV1,
    LimitedCompositionActivationRegistrationV1,
)


LimitedActivationLifecycle = Literal["ACTIVE", "EXPIRED"]


def canonical_registration_json(
    registration: LimitedCompositionActivationRegistrationV1,
) -> str:
    return canonical_json_bytes(
        registration.model_dump(mode="json")
    ).decode("utf-8")


def canonical_string_tuple_json(values: tuple[str, ...]) -> str:
    return canonical_json_bytes(list(values)).decode("utf-8")


def computed_limited_activation_lifecycle_sha256(
    *,
    registration_id: str,
    registration_sha256: str,
    state: LimitedActivationLifecycle,
    expires_at_ms: int,
    expired_at_ms: int | None,
) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.composition-limited-activation-lifecycle.v1",
            "registration_id": registration_id,
            "registration_sha256": registration_sha256,
            "state": state,
            "expires_at_ms": expires_at_ms,
            "expired_at_ms": expired_at_ms,
        }
    )


@dataclass(frozen=True, slots=True)
class LimitedActivationStoreRecord:
    registration: LimitedCompositionActivationRegistrationV1
    verification_plan_activation_id: str
    state: LimitedActivationLifecycle
    expired_at_ms: int | None
    lifecycle_sha256: str
    created_by_this_call: bool = False
    duplicate: bool = False
    recovered_after_restart: bool = False

    def has_valid_lifecycle(self) -> bool:
        if not self.registration.has_valid_identity():
            return False
        if self.state == "ACTIVE" and self.expired_at_ms is not None:
            return False
        if self.state == "EXPIRED" and (
            self.expired_at_ms is None
            or self.expired_at_ms < self.registration.expires_at_ms
        ):
            return False
        return self.lifecycle_sha256 == (
            computed_limited_activation_lifecycle_sha256(
                registration_id=self.registration.registration_id,
                registration_sha256=self.registration.registration_sha256,
                state=self.state,
                expires_at_ms=self.registration.expires_at_ms,
                expired_at_ms=self.expired_at_ms,
            )
        )

    def active_at(self, now_ms: int) -> bool:
        if now_ms < 0:
            raise ValueError("limited activation read time is invalid")
        return (
            self.has_valid_lifecycle()
            and self.state == "ACTIVE"
            and self.registration.registered_at_ms <= now_ms
            and now_ms < self.registration.expires_at_ms
        )


@dataclass(frozen=True, slots=True)
class LimitedActivationBundleRegistration:
    record: LimitedActivationStoreRecord
    receipt: LimitedActivationRegistrationReceiptV1
    registry_created: bool
    verification_plan_created: bool
    verification_plan_activation_id: str
    created_by_this_call: bool
    duplicate: bool

    def __post_init__(self) -> None:
        if not self.record.has_valid_lifecycle():
            raise ValueError("limited activation bundle contains an invalid row")
        if not self.receipt.has_valid_identity():
            raise ValueError("limited activation bundle contains an invalid receipt")
        if (
            self.record.registration.registration_id
            != self.receipt.registration_id
            or self.record.registration.registration_sha256
            != self.receipt.registration_sha256
            or self.record.verification_plan_activation_id
            != self.verification_plan_activation_id
        ):
            raise ValueError("limited activation bundle identities disagree")
        if self.created_by_this_call == self.duplicate:
            raise ValueError("limited activation bundle creation flags disagree")


def limited_activation_record_from_row(
    row: Any,
    *,
    created_by_this_call: bool = False,
    duplicate: bool = False,
    recovered_after_restart: bool = False,
) -> LimitedActivationStoreRecord:
    """Parse and fully revalidate one canonical Gateway Store row."""

    try:
        registration = (
            LimitedCompositionActivationRegistrationV1.model_validate_json(
                row["registration_json"], strict=True
            )
        )
    except Exception as exc:
        raise ValueError("stored limited activation payload is invalid") from exc
    if not registration.has_valid_identity():
        raise ValueError("stored limited activation identity is invalid")
    if canonical_registration_json(registration) != row["registration_json"]:
        raise ValueError("stored limited activation JSON is not canonical")

    expected = {
        "registration_id": registration.registration_id,
        "composition_activation_id": registration.composition_activation_id,
        "composition_activation_sha256": (
            registration.composition_activation_sha256
        ),
        "shadow_proposal_sha256": registration.shadow_proposal_sha256,
        "differential_trace_sha256": registration.differential_trace_sha256,
        "composition_plan_id": registration.composition_plan_id,
        "composition_plan_sha256": registration.composition_plan_sha256,
        "verification_plan_id": registration.verification_plan_id,
        "verification_plan_sha256": registration.verification_plan_sha256,
        "validation_mode": registration.validation_mode,
        "validation_sha256": registration.validation_sha256,
        "request_id": registration.request_id,
        "run_id": registration.run_id,
        "generation": registration.generation,
        "principal_scope_hash": registration.principal_scope_hash,
        "world_state_sha256": registration.world_state_sha256,
        "source_manifest_sha256": registration.source_manifest_sha256,
        "capability_manifest_sha256": (
            registration.capability_manifest_sha256
        ),
        "action_registry_sha256": registration.action_registry_sha256,
        "verification_registry_sha256": (
            registration.verification_registry_sha256
        ),
        "allowed_action_ids_json": canonical_string_tuple_json(
            registration.allowed_action_ids
        ),
        "allowed_action_versions_json": canonical_string_tuple_json(
            registration.allowed_action_versions
        ),
        "issued_at_ms": registration.issued_at_ms,
        "expires_at_ms": registration.expires_at_ms,
        "registered_at_ms": registration.registered_at_ms,
        "provisional_verification_required": int(
            registration.provisional_verification_required
        ),
        "registration_sha256": registration.registration_sha256,
    }
    for field, value in expected.items():
        if row[field] != value:
            raise ValueError(
                "stored limited activation column disagrees with canonical payload: "
                + field
            )

    state = str(row["state"])
    if state not in {"ACTIVE", "EXPIRED"}:
        raise ValueError("stored limited activation lifecycle is invalid")
    expired_at_ms = row["expired_at_ms"]
    lifecycle_sha256 = str(row["lifecycle_sha256"])
    record = LimitedActivationStoreRecord(
        registration=registration,
        verification_plan_activation_id=str(
            row["verification_plan_activation_id"]
        ),
        state=state,
        expired_at_ms=(
            None if expired_at_ms is None else int(expired_at_ms)
        ),
        lifecycle_sha256=lifecycle_sha256,
        created_by_this_call=created_by_this_call,
        duplicate=duplicate,
        recovered_after_restart=recovered_after_restart,
    )
    if not record.has_valid_lifecycle():
        raise ValueError("stored limited activation lifecycle digest is invalid")
    return record


__all__ = [
    "LimitedActivationBundleRegistration",
    "LimitedActivationLifecycle",
    "LimitedActivationStoreRecord",
    "canonical_registration_json",
    "canonical_string_tuple_json",
    "computed_limited_activation_lifecycle_sha256",
    "limited_activation_record_from_row",
]
