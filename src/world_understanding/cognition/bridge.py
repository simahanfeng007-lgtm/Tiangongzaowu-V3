"""P7 bridge from first-class World Understanding records into existing Cognition evidence.

The bridge is reference-only: it does not embed world objects into cognition evidence,
does not execute reality operations, and does not grant Cognition empirical authority.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from contracts.canonical import canonical_sha256
from contracts.cognition_evidence import CognitionEvidence, CognitionSourceRef, derive_cognition_evidence_id
from contracts.world_understanding._base import WorldRecordRef
from contracts.world_understanding.scope import WorldScope
from contracts.world_understanding.known import DirectKnownRecord, DerivedKnownRecord
from contracts.world_understanding.event import WorldEvent
from contracts.world_understanding.entity import WorldEntity
from contracts.world_understanding.relation import WorldRelation
from contracts.world_understanding.hypothesis import WorldHypothesis
from world_understanding.common.epistemic import EpistemicPlane
from world_understanding.common.scope import require_exact_scope

_ALLOWED_RECORD_TYPES = frozenset({"world_known", "world_event", "world_entity", "world_relation", "world_hypothesis"})
_RECORD_CLASSES = {
    "world_known": (DirectKnownRecord, DerivedKnownRecord),
    "world_event": (WorldEvent,),
    "world_entity": (WorldEntity,),
    "world_relation": (WorldRelation,),
    "world_hypothesis": (WorldHypothesis,),
}
_RECORD_FIELDS = {
    "world_known": ("known_id", "record_hash", "world_scope", None),
    "world_event": ("event_id", "event_sha256", "scope", None),
    "world_entity": ("entity_id", "entity_sha256", "scope", "revision"),
    "world_relation": ("relation_id", "relation_sha256", "scope", "revision"),
    "world_hypothesis": ("hypothesis_id", "hypothesis_sha256", "scope", None),
}

class CognitionEvidenceSink(Protocol):
    def ingest(self, evidence: CognitionEvidence) -> bool: ...

class CognitionWorldEvidenceError(ValueError):
    pass

@dataclass(frozen=True, slots=True)
class AdaptedWorldEvidence:
    world_ref: WorldRecordRef
    evidence: CognitionEvidence
    empirical_contribution_milli: int
    reference_only: bool = True


def _world_scope(record_type: str, record: object) -> WorldScope:
    field = _RECORD_FIELDS[record_type][2]
    scope = getattr(record, field, None)
    if not isinstance(scope, WorldScope):
        raise CognitionWorldEvidenceError("WORLD_RECORD_SCOPE_MISSING")
    return scope


def _record_identity(record_type: str, record: object) -> tuple[str, str, int | None]:
    id_field, hash_field, _, revision_field = _RECORD_FIELDS[record_type]
    record_id = str(getattr(record, id_field, ""))
    record_hash = str(getattr(record, hash_field, ""))
    revision = None if revision_field is None else int(getattr(record, revision_field))
    return record_id, record_hash, revision


def _validate_ref(record_type: str, ref: WorldRecordRef, record: object) -> None:
    if record_type not in _ALLOWED_RECORD_TYPES or ref.record_type != record_type:
        raise CognitionWorldEvidenceError("WORLD_RECORD_TYPE_MISMATCH")
    if not isinstance(record, _RECORD_CLASSES[record_type]):
        raise CognitionWorldEvidenceError("WORLD_RECORD_CLASS_MISMATCH")
    record_id, record_hash, revision = _record_identity(record_type, record)
    if ref.record_id != record_id or ref.sha256 != record_hash:
        raise CognitionWorldEvidenceError("WORLD_RECORD_REFERENCE_MISMATCH")
    if revision is not None and ref.revision != revision:
        raise CognitionWorldEvidenceError("WORLD_RECORD_REVISION_MISMATCH")
    if revision is None and ref.revision is not None:
        raise CognitionWorldEvidenceError("UNREVISIONED_WORLD_RECORD_HAS_REVISION")
    has_valid_hash = getattr(record, "has_valid_hash", None)
    if callable(has_valid_hash) and not bool(has_valid_hash()):
        raise CognitionWorldEvidenceError("WORLD_RECORD_HASH_INVALID")


def _lineage_roots(record_type: str, ref: WorldRecordRef, record: object) -> tuple[str, ...]:
    roots: set[str] = set()
    for field in ("provenance_refs", "source_observation_refs", "source_refs", "basis_refs"):
        for item in tuple(getattr(record, field, ()) or ()):
            sha = getattr(item, "sha256", None)
            if isinstance(sha, str) and len(sha) == 64:
                roots.add(sha)
    if not roots:
        roots.add(ref.sha256)
    return tuple(sorted(roots))


def _time(record_type: str, record: object) -> tuple[int, int, int | None]:
    time = getattr(record, "time", None)
    if time is not None:
        observed = getattr(time, "observed_at_ms", None)
        recorded = int(getattr(time, "recorded_at_ms"))
        valid_from = int(getattr(time, "valid_from_ms"))
        valid_until = getattr(time, "valid_until_ms", None)
        return recorded if observed is None else int(observed), valid_from, None if valid_until is None else int(valid_until)
    created = int(getattr(record, "created_at_ms", 0))
    valid_until = getattr(record, "valid_until_ms", None)
    return created, created, None if valid_until is None else int(valid_until)


def _authority(record_type: str, record: object) -> tuple[str, str, int, int]:
    if record_type == "world_hypothesis":
        return "model_synthesis", "model_inference", 0, 0
    empirical = max(0, min(1000, int(getattr(record, "empirical_evidence_weight_milli", 0))))
    if record_type == "world_known":
        ceiling = max(0, min(1000, int(getattr(record, "authority_ceiling_milli", empirical))))
        return "code_perception", "observed", min(empirical, ceiling), ceiling
    if record_type == "world_event":
        return "fact_execution", "observed", empirical, empirical
    if record_type == "world_relation":
        return "code_perception", "observed", empirical, empirical
    return "code_perception", "observed", 0, 0


def adapt_world_record_to_evidence(*, expected_scope: WorldScope, world_ref: WorldRecordRef,
                                   record: object, domain: str = "software",
                                   epistemic_plane: EpistemicPlane | None = None) -> AdaptedWorldEvidence:
    record_type = world_ref.record_type
    if record_type not in _ALLOWED_RECORD_TYPES:
        raise CognitionWorldEvidenceError("WORLD_RECORD_TYPE_NOT_ALLOWED")
    _validate_ref(record_type, world_ref, record)
    scope = _world_scope(record_type, record)
    require_exact_scope(expected_scope, scope)
    gamma = epistemic_plane or EpistemicPlane()
    if record_type == "world_known":
        decision = gamma.evaluate_known(record, expected_scope=expected_scope)
        if not decision.admissible or not decision.stable_promotion:
            raise CognitionWorldEvidenceError("GAMMA_REJECTED_WORLD_KNOWN")
    elif record_type in {"world_entity", "world_relation"}:
        if getattr(record, "truth_state", None) != "TRUE" or getattr(record, "epistemic_state", None) != "CURRENT":
            raise CognitionWorldEvidenceError("UNSTABLE_WORLD_GRAPH_RECORD")
    elif record_type == "world_hypothesis":
        gamma.validate_non_evidence_object(record)
    roots = _lineage_roots(record_type, world_ref, record)
    source_kind, evidence_class, credibility, ceiling = _authority(record_type, record)
    observed_at, valid_from, valid_until = _time(record_type, record)
    object_id = f"{record_type}.{world_ref.record_id}"
    if len(object_id) > 160:
        object_id = f"wref.{canonical_sha256({'record_type': record_type, 'record_id': world_ref.record_id})}"
    source_ref = CognitionSourceRef(
        source_kind=source_kind,
        object_id=object_id,
        object_revision=world_ref.revision or 1,
        sha256=world_ref.sha256,
    )
    independence = canonical_sha256({"domain": "tiangong.cognition.world-evidence-independence.v1", "lineage_roots": roots})
    material = dict(
        life_id=expected_scope.life_id,
        domain=domain,
        world_scope_hash=expected_scope.world_scope_hash,
        principal_scope_hash=expected_scope.principal_scope_hash,
        privacy_scope=expected_scope.privacy_scope,
        source_ref=source_ref,
        evidence_class=evidence_class,
        source_credibility_milli=credibility,
        authority_ceiling_milli=ceiling,
        provenance_integrity_milli=1000,
        observation_mode="positive",
        observation=f"world-reference:{record_type}:{world_ref.record_id}@{world_ref.sha256}",
        coverage_milli=1000,
        search_scope_hash=None,
        independence_group_hash=independence,
        lineage_root_hashes=roots,
        derived_from_evidence_ids=(),
        ancestor_cognition_ids=(),
        content_object_id=object_id,
        content_sha256=world_ref.sha256,
        extractor_kind="llm_synthesis" if record_type == "world_hypothesis" else "deterministic",
        observed_at_ms=observed_at,
        valid_from_ms=valid_from,
        valid_until_ms=valid_until,
        volatility_class="structural",
    )
    evidence_id = derive_cognition_evidence_id(**material)
    evidence = CognitionEvidence(evidence_id=evidence_id, evidence_sha256="0" * 64, **material).with_computed_evidence_sha256()
    return AdaptedWorldEvidence(world_ref, evidence, min(credibility, ceiling), True)

class CognitionL5Bridge:
    __slots__ = ("sink", "epistemic_plane")
    def __init__(self, sink: CognitionEvidenceSink, *, epistemic_plane: EpistemicPlane | None = None) -> None:
        self.sink = sink
        self.epistemic_plane = epistemic_plane or EpistemicPlane()
    def ingest_world_record(self, *, expected_scope: WorldScope, world_ref: WorldRecordRef,
                            record: object, domain: str = "software") -> AdaptedWorldEvidence:
        adapted = adapt_world_record_to_evidence(expected_scope=expected_scope, world_ref=world_ref, record=record,
                                                 domain=domain, epistemic_plane=self.epistemic_plane)
        self.sink.ingest(adapted.evidence)
        return adapted

__all__ = ["AdaptedWorldEvidence", "CognitionEvidenceSink", "CognitionL5Bridge", "CognitionWorldEvidenceError", "adapt_world_record_to_evidence"]