"""Fail-closed validation for the single World Understanding ingress."""
from __future__ import annotations

from dataclasses import dataclass

from contracts.world_understanding.ingress import WorldIngressEnvelope


@dataclass(frozen=True, slots=True)
class IngressValidationError(Exception):
    reason_code: str

    def __str__(self) -> str:
        return self.reason_code


def validate_ingress_envelope(envelope: WorldIngressEnvelope) -> WorldIngressEnvelope:
    """Re-validate a typed envelope so model_copy/update cannot bypass P1 invariants."""
    if not isinstance(envelope, WorldIngressEnvelope):
        raise IngressValidationError("INVALID_ENVELOPE_TYPE")
    try:
        return WorldIngressEnvelope.model_validate(envelope.model_dump(mode="python"))
    except Exception as exc:  # Pydantic validation errors are intentionally normalized.
        raise IngressValidationError("MALFORMED_ENVELOPE") from exc


__all__ = ["IngressValidationError", "validate_ingress_envelope"]
