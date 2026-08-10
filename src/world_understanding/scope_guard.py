"""Shared life/world-scope invariants. No persistence and no closure logic."""
from __future__ import annotations
from collections.abc import Iterable
from dataclasses import dataclass
from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.known import DirectKnownRecord
from contracts.world_understanding.scope import WorldScope

@dataclass(frozen=True, slots=True)
class ScopeMismatchError(ValueError):
    reason_code: str = "SCOPE_MISMATCH"
    def __str__(self) -> str: return self.reason_code

def require_same_world_scope(expected: WorldScope, actual: WorldScope) -> None:
    if actual.life_id != expected.life_id or actual.world_scope_hash != expected.world_scope_hash:
        raise ScopeMismatchError()
    if actual.principal_scope_hash != expected.principal_scope_hash:
        raise ScopeMismatchError("PRINCIPAL_SCOPE_MISMATCH")

def require_envelope_scope(envelope: WorldIngressEnvelope) -> None:
    if envelope.life_id != envelope.scope_hint.life_id:
        raise ScopeMismatchError()
    if envelope.principal_scope_hash is not None and envelope.principal_scope_hash != envelope.scope_hint.principal_scope_hash:
        raise ScopeMismatchError("PRINCIPAL_SCOPE_MISMATCH")

def require_direct_known_scope(envelope: WorldIngressEnvelope, known: DirectKnownRecord) -> None:
    require_envelope_scope(envelope)
    require_same_world_scope(envelope.scope_hint, known.world_scope)
    if known.source_envelope_id != envelope.envelope_id:
        raise ScopeMismatchError("SOURCE_ENVELOPE_MISMATCH")
    if known.source_kind != envelope.source_kind or known.source_native_id != envelope.source_native_id:
        raise ScopeMismatchError("SOURCE_LINEAGE_MISMATCH")
    if known.source_payload_hash != envelope.payload_sha256:
        raise ScopeMismatchError("SOURCE_PAYLOAD_MISMATCH")

def require_same_scope_parents(target_scope: WorldScope, parent_records: Iterable[DirectKnownRecord]) -> None:
    """P4 precondition only: K*_life = Closure(K0_life). Does not derive anything."""
    for parent in parent_records:
        require_same_world_scope(target_scope, parent.world_scope)

__all__=["ScopeMismatchError","require_same_world_scope","require_envelope_scope","require_direct_known_scope","require_same_scope_parents"]
