from __future__ import annotations

import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app" / "backend" / "tiangong-backend"))

from contracts.canonical import canonical_sha256
from contracts.cognition_statement import CognitionStatement, CognitionValue, derive_cognition_id
from contracts.world_understanding import (
    ScopeBinding, WorldScope, WorldTime, WorldIngressEnvelope,
    derive_world_id, derive_world_scope_hash, derive_ingress_dedup_key, derive_ingress_envelope_id,
)
from contracts.world_understanding._base import WorldRecordRef, WorldValue, WorldClaim
from contracts.world_understanding.entity import WorldEntity, derive_entity_id
from contracts.world_understanding.relation import WorldRelation, derive_relation_id
from contracts.world_understanding.event import WorldEvent, derive_world_event_id
from contracts.world_understanding.hypothesis import WorldHypothesis
from world_understanding.source_compilers import build_p3_compilers
from world_understanding.known.set import known_ref
from world_understanding.cognition.bridge import adapt_world_record_to_evidence, CognitionWorldEvidenceError
from world_understanding.cognition.l5 import to_l5_view

P = "a" * 64

def scope(life: str = "life.A") -> WorldScope:
    bindings = (ScopeBinding(key="repository", value="repo.main"),)
    world_id = derive_world_id(life_id=life, namespace_anchor="primary")
    return WorldScope(
        life_id=life,
        world_id=world_id,
        domain_id="software",
        scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(life_id=life, world_id=world_id, domain_id="software", scope_bindings=bindings),
        principal_scope_hash=P,
        privacy_scope="system",
    )

def wt() -> WorldTime:
    return WorldTime(valid_from_ms=1, observed_at_ms=1, recorded_at_ms=2)

def known(sc: WorldScope | None = None, native: str = "p7.known"):
    sc = sc or scope()
    payload = {"text": "hello"}
    payload_sha = canonical_sha256(payload)
    dedup = derive_ingress_dedup_key(
        envelope_kind="SOURCE_RECORD", source_kind="FACT_EXECUTION", source_native_id=native,
        payload_sha256=payload_sha, world_scope_hash=sc.world_scope_hash,
    )
    envelope = WorldIngressEnvelope(
        envelope_id=derive_ingress_envelope_id(dedup_key=dedup), envelope_kind="SOURCE_RECORD",
        source_kind="FACT_EXECUTION", source_native_id=native, producer_ref="p7.test",
        payload_inline=payload, payload_sha256=payload_sha, source_time=wt(), life_id=sc.life_id,
        principal_scope_hash=sc.principal_scope_hash, scope_hint=sc, correlation_id="corr.p7", dedup_key=dedup,
    )
    return build_p3_compilers()["FACT_EXECUTION"](envelope)[0]

def entity(sc: WorldScope, parent):
    anchor = canonical_sha256({"domain": "p7.entity", "parent": parent.record_hash})
    entity_id = derive_entity_id(life_id=sc.life_id, domain_id=sc.domain_id, identity_anchor_hash=anchor)
    return WorldEntity(
        entity_id=entity_id, scope=sc, entity_type="Function", identity_anchor_hash=anchor,
        canonical_name="function.main", aliases=(), attributes=(), location_refs=(),
        source_observation_refs=(known_ref(parent),), truth_state="TRUE", epistemic_state="CURRENT",
        lifecycle="ACTIVE", replacement_refs=(), revision=1, supersedes_entity_sha256=None,
        time=wt(), entity_sha256="0" * 64,
    ).with_computed_hash()

def relation(sc: WorldScope, ent: WorldEntity, parent):
    ent_ref = WorldRecordRef(record_type="world_entity", record_id=ent.entity_id, revision=1, sha256=ent.entity_sha256)
    value = WorldValue(kind="entity_ref", entity_ref=ent.entity_id)
    relation_id = derive_relation_id(world_scope_hash=sc.world_scope_hash, subject_ref=ent_ref, predicate="USES", value=value, condition_sha256=None)
    return WorldRelation(
        relation_id=relation_id, scope=sc, subject_ref=ent_ref, predicate="USES", value=value,
        extraction_mode="observed", materialization_class="MATERIALIZED",
        source_observation_refs=(known_ref(parent),), derivation_refs=(), truth_state="TRUE",
        epistemic_state="CURRENT", empirical_evidence_weight_milli=500, revision=1,
        supersedes_relation_sha256=None, time=wt(), relation_sha256="0" * 64,
    ).with_computed_hash()

def event(sc: WorldScope, parent):
    event_id = derive_world_event_id(
        world_scope_hash=sc.world_scope_hash, event_kind="test.event", subject_refs=(known_ref(parent),),
        source_refs=parent.provenance_refs, sequence=1, time=wt(),
    )
    return WorldEvent(
        event_id=event_id, scope=sc, event_kind="test.event", subject_refs=(known_ref(parent),),
        source_refs=parent.provenance_refs, sequence=1, time=wt(), empirical_evidence_weight_milli=800,
        event_sha256="0" * 64,
    ).with_computed_hash()

def hypothesis(sc: WorldScope, parent):
    claim = WorldClaim(subject_ref=known_ref(parent), predicate="maybe.role", value=WorldValue(kind="string", string_value="boundary"))
    created_at = 2
    hypothesis_id = "whyp_" + canonical_sha256({
        "domain": "tiangong.world.hypothesis-id.v1", "world_scope_hash": sc.world_scope_hash,
        "claim": claim.model_dump(mode="json"), "hypothesis_kind": "semantic.role",
        "proposal_origin": "deterministic_pattern", "basis_refs": [known_ref(parent).model_dump(mode="json")],
        "created_at_ms": created_at,
    })
    return WorldHypothesis(
        hypothesis_id=hypothesis_id, scope=sc, claim=claim, hypothesis_kind="semantic.role",
        proposal_origin="deterministic_pattern", basis_refs=(known_ref(parent),), uncertainty_milli=500,
        created_at_ms=created_at, hypothesis_sha256="0" * 64,
    ).with_computed_hash()

def world_ref(kind: str, obj: object) -> WorldRecordRef:
    id_field, hash_field, revision_field = {
        "world_known": ("known_id", "record_hash", None),
        "world_event": ("event_id", "event_sha256", None),
        "world_entity": ("entity_id", "entity_sha256", "revision"),
        "world_relation": ("relation_id", "relation_sha256", "revision"),
        "world_hypothesis": ("hypothesis_id", "hypothesis_sha256", None),
    }[kind]
    return WorldRecordRef(
        record_type=kind, record_id=getattr(obj, id_field),
        revision=None if revision_field is None else getattr(obj, revision_field), sha256=getattr(obj, hash_field),
    )

@pytest.mark.parametrize("kind", ["world_known", "world_event", "world_entity", "world_relation", "world_hypothesis"])
def test_p7_accepts_first_class_world_evidence_refs_without_embedding(kind):
    sc = scope(); k = known(sc); ent = entity(sc, k)
    objects = {"world_known": k, "world_event": event(sc, k), "world_entity": ent,
               "world_relation": relation(sc, ent, k), "world_hypothesis": hypothesis(sc, k)}
    record = objects[kind]; ref = world_ref(kind, record)
    adapted = adapt_world_record_to_evidence(expected_scope=sc, world_ref=ref, record=record)
    assert adapted.reference_only is True
    assert adapted.evidence.content_sha256 == ref.sha256
    assert ref.record_id in adapted.evidence.content_object_id
    assert adapted.evidence.observation.startswith("world-reference:")
    if kind in {"world_hypothesis", "world_entity"}:
        assert adapted.empirical_contribution_milli == 0

def test_p7_cross_life_evidence_fails_closed():
    a, b = scope("life.A"), scope("life.B")
    row = known(b)
    with pytest.raises(Exception):
        adapt_world_record_to_evidence(expected_scope=a, world_ref=world_ref("world_known", row), record=row)

def test_p7_tampered_reference_fails_closed():
    sc = scope(); row = known(sc)
    bad = world_ref("world_known", row).model_copy(update={"sha256": "b" * 64})
    with pytest.raises(CognitionWorldEvidenceError):
        adapt_world_record_to_evidence(expected_scope=sc, world_ref=bad, record=row)

def test_p7_stale_known_cannot_be_promoted_to_cognition_evidence():
    sc = scope(); row = known(sc).model_copy(update={"epistemic_state": "STALE"}).with_computed_hash()
    with pytest.raises(CognitionWorldEvidenceError):
        adapt_world_record_to_evidence(expected_scope=sc, world_ref=world_ref("world_known", row), record=row)

def test_p7_c4_l5_view_is_context_only_zero_empirical_and_non_authorizing():
    sc = scope()
    cognition_id = derive_cognition_id(
        life_id=sc.life_id, domain="software", world_scope_hash=sc.world_scope_hash,
        principal_scope_hash=sc.principal_scope_hash, claim_kind="boundary", subject_ref="subject.1",
        predicate="is_boundary", condition_sha256=None,
    )
    statement = CognitionStatement(
        cognition_id=cognition_id, life_id=sc.life_id, domain="software", world_scope_hash=sc.world_scope_hash,
        principal_scope_hash=sc.principal_scope_hash, privacy_scope=sc.privacy_scope, claim_kind="boundary",
        subject_ref="subject.1", predicate="is_boundary", value=CognitionValue(kind="boolean", boolean_value=True),
        proposal_origin="deterministic_extraction", status="CORE", stability_level="C4", confidence_milli=999,
        supporting_evidence_ids=("cev_" + "1" * 64, "cev_" + "2" * 64, "cev_" + "3" * 64),
        counterevidence_ids=(), prior_ids=(), valid_from_ms=1, last_verified_at_ms=2, revision=1,
        statement_sha256="0" * 64,
    ).with_computed_statement_sha256()
    view = to_l5_view(statement, scope=sc)
    assert view.empirical_evidence_weight_milli == 0
    assert view.context_only is True
    assert view.may_authorize is False and view.may_execute is False and view.confirms is False
    assert view.changes_risk is False and view.c4_is_empirical_fact is False

def test_p7_legacy_modules_reexport_same_canonical_implementation():
    from v3.world_cognition.consolidator import CognitionConsolidator as legacy
    from world_understanding.cognition.consolidator import CognitionConsolidator as canonical
    assert legacy is canonical

def test_p7_canonical_package_does_not_export_second_facade():
    import world_understanding.cognition as cognition
    assert not hasattr(cognition, "WorldCognitionFacade")
