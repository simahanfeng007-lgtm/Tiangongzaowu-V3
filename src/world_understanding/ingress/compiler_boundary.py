"""Uniform post-compiler boundary: concrete compilers never own scope decisions."""
from __future__ import annotations
from contracts.world_understanding.ingress import WorldIngressEnvelope
from contracts.world_understanding.known import DirectKnownRecord
from world_understanding.scope_guard import require_direct_known_scope

def validate_compiler_output(envelope: WorldIngressEnvelope, result: object | None) -> tuple[DirectKnownRecord, ...]:
    if result is None:
        return ()
    if isinstance(result, DirectKnownRecord):
        rows=(result,)
    elif isinstance(result, tuple):
        rows=result
    else:
        raise TypeError("source compiler must return DirectKnownRecord, tuple[DirectKnownRecord,...], or None")
    seen:set[str]=set()
    for row in rows:
        if not isinstance(row, DirectKnownRecord): raise TypeError("source compiler returned non-DirectKnownRecord")
        require_direct_known_scope(envelope,row)
        if row.known_id in seen: raise ValueError("source compiler returned duplicate known_id")
        seen.add(row.known_id)
    return rows

__all__=["validate_compiler_output"]
