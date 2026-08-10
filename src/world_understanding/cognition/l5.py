"""L5 projection wrapper for legacy CognitionStatement revisions.

Cognition stability is cognitive consolidation only. C4 never becomes empirical evidence,
authorization, confirmation, execution permission, or reality truth through this wrapper.
"""
from __future__ import annotations
from dataclasses import dataclass
from contracts.cognition_statement import CognitionStatement
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.cognition_compat import CognitionStatementRef
from contracts.world_understanding.scope import WorldScope

@dataclass(frozen=True, slots=True)
class CognitionL5View:
    scope: WorldScope
    statement: CognitionStatement
    statement_ref: CognitionStatementRef
    empirical_evidence_weight_milli: int = 0
    context_only: bool = True
    may_authorize: bool = False
    may_execute: bool = False
    confirms: bool = False
    changes_risk: bool = False
    c4_is_empirical_fact: bool = False


def to_l5_view(statement: CognitionStatement, *, scope: WorldScope) -> CognitionL5View:
    if statement.life_id != scope.life_id or statement.world_scope_hash != scope.world_scope_hash or statement.principal_scope_hash != scope.principal_scope_hash:
        raise ValueError("COGNITION_SCOPE_MISMATCH")
    if statement.privacy_scope != scope.privacy_scope:
        raise ValueError("COGNITION_PRIVACY_SCOPE_MISMATCH")
    if not statement.has_valid_statement_sha256():
        raise ValueError("COGNITION_STATEMENT_HASH_INVALID")
    record_ref = WorldRecordRef(record_type="world_cognition", record_id=statement.cognition_id,
                                revision=statement.revision, sha256=statement.statement_sha256)
    ref = CognitionStatementRef(cognition_id=statement.cognition_id, revision=statement.revision,
                                statement_sha256=statement.statement_sha256, life_id=statement.life_id,
                                world_scope_hash=statement.world_scope_hash,
                                principal_scope_hash=statement.principal_scope_hash,
                                privacy_scope=statement.privacy_scope, record_ref=record_ref)
    return CognitionL5View(scope, statement, ref)

__all__ = ["CognitionL5View", "to_l5_view"]