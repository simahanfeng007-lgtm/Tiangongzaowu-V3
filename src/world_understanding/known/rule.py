"""Deterministic rule contracts for P4 Known Mathematics."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from contracts.world_understanding._base import WorldValue, WorldRecordRef
from contracts.world_understanding.known import DirectKnownRecord, DerivedKnownRecord
from contracts.world_understanding.authority import AuthorityDomain
from .set import KnownSet, KnownRecord

@dataclass(frozen=True, slots=True)
class RuleSpec:
    rule_id: str
    version: str
    output_authority_domain: AuthorityDomain | None = None
    accepted_parent_domains: tuple[AuthorityDomain, ...] = ()
    allows_transitivity: bool = False

@dataclass(frozen=True, slots=True)
class DerivedCandidate:
    parents: tuple[KnownRecord, ...]
    proposition_type: str
    subject_ref: str
    predicate: str
    object_value: WorldValue | None = None
    object_ref: WorldRecordRef | None = None

class DeterministicRule(Protocol):
    spec: RuleSpec
    def apply(self, known: KnownSet, delta: tuple[KnownRecord, ...]) -> tuple[DerivedCandidate, ...]: ...

@dataclass(frozen=True, slots=True)
class ClosureDiagnostic:
    rule_id: str
    reason_code: str
    detail: str = ""
