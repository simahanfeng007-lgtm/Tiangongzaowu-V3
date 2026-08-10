"""Fail-closed validation for the single World Understanding ingress."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.world_understanding.ingress import WorldIngressEnvelope
from world_understanding.scope_guard import ScopeMismatchError,require_envelope_scope
@dataclass(frozen=True,slots=True)
class IngressValidationError(Exception):
    reason_code:str
    def __str__(self)->str: return self.reason_code
def validate_ingress_envelope(envelope:WorldIngressEnvelope)->WorldIngressEnvelope:
    if not isinstance(envelope,WorldIngressEnvelope): raise IngressValidationError('INVALID_ENVELOPE_TYPE')
    try:
        require_envelope_scope(envelope)
        return WorldIngressEnvelope.model_validate(envelope.model_dump(mode='python'))
    except ScopeMismatchError as exc:
        raise IngressValidationError(exc.reason_code) from exc
    except Exception as exc:
        raise IngressValidationError('MALFORMED_ENVELOPE') from exc
__all__=['IngressValidationError','validate_ingress_envelope']
