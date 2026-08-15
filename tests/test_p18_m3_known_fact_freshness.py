"""P18-M3.8 production Known-fact freshness regressions."""
from __future__ import annotations

import pytest

from contracts.canonical import canonical_sha256
from contracts.world_understanding.ingress import (
    WorldIngressEnvelope,
    derive_ingress_dedup_key,
    derive_ingress_envelope_id,
)
from contracts.world_understanding.scope import (
    ScopeBinding,
    WorldScope,
    derive_world_id,
    derive_world_scope_hash,
)
from contracts.world_understanding.time import WorldTime
from world_understanding.known import (
    KnownSet,
    StaleKnownDependency,
    record_source_versions,
)
from world_understanding.source_compilers import SPECS
from world_understanding.source_compilers.base import make_direct_known


PRINCIPAL = "a" * 64


def scope() -> WorldScope:
    bindings = (ScopeBinding(key="repository", value="repo.p18-m3"),)
    life_id = "life.p18-m3.freshness"
    world_id = derive_world_id(life_id=life_id, namespace_anchor="primary")
    return WorldScope(
        life_id=life_id,
        world_id=world_id,
        domain_id="software",
        scope_bindings=bindings,
        world_scope_hash=derive_world_scope_hash(
            life_id=life_id,
            world_id=world_id,
            domain_id="software",
            scope_bindings=bindings,
        ),
        principal_scope_hash=PRINCIPAL,
        privacy_scope="system",
    )


def volatile_fact(*, valid_until_ms: int | None = None, native: str = "runtime.1"):
    sc = scope()
    payload = {"status": "running", "pid": 123}
    payload_hash = canonical_sha256(payload)
    dedup = derive_ingress_dedup_key(
        envelope_kind="SOURCE_RECORD",
        source_kind="RUNTIME_ENVIRONMENT",
        source_native_id=native,
        payload_sha256=payload_hash,
        world_scope_hash=sc.world_scope_hash,
    )
    envelope = WorldIngressEnvelope(
        envelope_id=derive_ingress_envelope_id(dedup_key=dedup),
        envelope_kind="SOURCE_RECORD",
        source_kind="RUNTIME_ENVIRONMENT",
        source_native_id=native,
        producer_ref="p18.m3.freshness.test",
        payload_inline=payload,
        payload_sha256=payload_hash,
        source_time=WorldTime(
            valid_from_ms=1000,
            valid_until_ms=valid_until_ms,
            observed_at_ms=1000,
            recorded_at_ms=1000,
        ),
        life_id=sc.life_id,
        principal_scope_hash=PRINCIPAL,
        scope_hint=sc,
        correlation_id="corr.p18.m3.freshness",
        dedup_key=dedup,
    )
    return make_direct_known(
        envelope,
        SPECS["RUNTIME_ENVIRONMENT"],
        proposition_type="RUNTIME_PROCESS_STATE",
        predicate="runtime.process_state",
        subject_ref=native,
        object_text="running",
        authority_ceiling_milli=900,
        empirical_evidence_weight_milli=900,
    )


def stored_versions(record) -> dict[str, str]:
    return {key: version for key, version, _kind in record_source_versions(record)}


def test_volatile_known_cannot_be_reused_without_revalidation() -> None:
    record = volatile_fact()
    known = KnownSet(scope(), (record,))
    with pytest.raises(StaleKnownDependency) as raised:
        known.get_hash_for_dependency(record.record_hash, now_ms=1100)
    assert any("volatile_source_revalidation_required" in reason for reason in raised.value.decision.reasons)


def test_matching_current_source_version_is_revalidation_proof() -> None:
    record = volatile_fact()
    known = KnownSet(scope(), (record,))
    reused = known.get_hash_for_dependency(
        record.record_hash,
        now_ms=1100,
        current_source_versions=stored_versions(record),
    )
    assert reused is record


def test_changed_source_version_marks_old_known_stale() -> None:
    record = volatile_fact()
    known = KnownSet(scope(), (record,))
    versions = stored_versions(record)
    key = sorted(versions)[0]
    versions[key] = "sha256:" + "f" * 64
    with pytest.raises(StaleKnownDependency) as raised:
        known.get_hash_for_dependency(
            record.record_hash,
            now_ms=1100,
            current_source_versions=versions,
        )
    assert raised.value.decision.source_version_changed
    assert any("source_version_changed" in reason for reason in raised.value.decision.reasons)


def test_expired_validity_window_cannot_silently_reuse_old_fact() -> None:
    record = volatile_fact(valid_until_ms=1050)
    known = KnownSet(scope(), (record,))
    with pytest.raises(StaleKnownDependency) as raised:
        known.get_hash_for_dependency(
            record.record_hash,
            now_ms=1100,
            current_source_versions=stored_versions(record),
        )
    assert raised.value.decision.stale
    assert "validity_window_expired" in raised.value.decision.reasons


def test_introspection_lookup_remains_available_for_audit_without_authorizing_reuse() -> None:
    record = volatile_fact(valid_until_ms=1050)
    known = KnownSet(scope(), (record,))
    assert known.get_hash(record.record_hash) is record
    with pytest.raises(StaleKnownDependency):
        known.get_hash_for_dependency(record.record_hash, now_ms=1100)
