"""P15 M8: deterministic legacy-memory -> layer migration.

Legacy contract assertions already live in the LifeShadowStore; this module
only attaches append-only derivation metadata.  Mapping is conservative:

- old turn episodic / working rows      -> L1 legacy-derived
- checkpoint / terminal summaries       -> L2 legacy diary candidate
- LONG_TERM_MEMORY without explicit     -> L3 legacy candidate (never L5)
- explicit user provenance (with events)-> L4 migration

Legacy rows never become L5 by retention class alone, and re-running the
migration is an idempotent no-op.
"""

from __future__ import annotations

from contracts import (
    MemoryAssertionV3,
    MemoryDerivationV1,
    canonical_sha256,
)


LEGACY_MIGRATION_POLICY = "p15-legacy-migration-v1"

_DOMAIN_BY_ASSERTION_KIND = {
    "observation": "SYSTEM",
    "user_preference": "USER_PREFERENCE",
    "hard_constraint": "OPERATING_RULE",
    "goal": "LONG_TERM_GOAL",
    "relationship": "RELATIONSHIP",
    "skill": "CAPABILITY_SELF",
    "causal_summary": "OTHER",
    "legacy": "OTHER",
}
_EXPLICIT_KINDS = frozenset(
    {"user_preference", "hard_constraint", "goal", "relationship"}
)


def legacy_layer_for_assertion(assertion: MemoryAssertionV3) -> str:
    """Map one legacy assertion onto a conservative maturity layer."""

    if (
        assertion.retention_class == "LONG_TERM_MEMORY"
        and assertion.epistemic_status == "user_asserted"
        and assertion.assertion_kind in _EXPLICIT_KINDS
        and assertion.source_event_ids
    ):
        return "L4_EXPLICIT"
    if assertion.retention_class == "LONG_TERM_MEMORY":
        return "L3_EXPERIENCE"
    if assertion.retention_class in {"CHECKPOINT", "TERMINAL_RESULT"}:
        return "L2_DIARY"
    return "L1_STREAM"


def migration_id(
    *,
    life_id: str,
    memory_id: str,
    revision: int,
    layer: str,
    policy_version: str = LEGACY_MIGRATION_POLICY,
) -> str:
    return canonical_sha256(
        {
            "domain": "tiangong.life.legacy-migration-id.v1",
            "life_id": life_id,
            "memory_id": memory_id,
            "memory_revision": revision,
            "layer": layer,
            "policy_version": policy_version,
        }
    )


def legacy_derivation_id(
    *,
    life_id: str,
    memory_id: str,
    revision: int,
    layer: str,
    policy_version: str = LEGACY_MIGRATION_POLICY,
) -> str:
    return "mdr_" + canonical_sha256(
        {
            "domain": "tiangong.life.legacy-derivation-id.v1",
            "life_id": life_id,
            "memory_id": memory_id,
            "memory_revision": revision,
            "layer": layer,
            "policy_version": policy_version,
        }
    )


def build_legacy_derivation(
    assertion: MemoryAssertionV3,
    *,
    layer: str,
    created_at_ms: int,
    policy_version: str = LEGACY_MIGRATION_POLICY,
) -> MemoryDerivationV1:
    """Build the migration derivation (origin=MIGRATION) for one assertion."""

    if layer == "L5_CORE":
        raise ValueError("legacy migration never creates L5 core")
    semantic_domain = _DOMAIN_BY_ASSERTION_KIND.get(
        assertion.assertion_kind, "OTHER"
    )
    derivation_id = legacy_derivation_id(
        life_id=assertion.life_id,
        memory_id=assertion.memory_id,
        revision=assertion.revision,
        layer=layer,
        policy_version=policy_version,
    )
    return MemoryDerivationV1(
        derivation_id=derivation_id,
        life_id=assertion.life_id,
        memory_id=assertion.memory_id,
        memory_revision=assertion.revision,
        memory_assertion_sha256=assertion.assertion_sha256,
        layer=layer,
        semantic_domain=semantic_domain,
        origin="MIGRATION",
        principal_ref=assertion.life_id,
        workspace_ref=None,
        privacy_scope=assertion.privacy_scope,
        claim_key="legacy:" + assertion.memory_id,
        parent_memory_refs=(),
        source_event_ids=assertion.source_event_ids,
        lineage_root_event_ids=assertion.source_event_ids,
        external_evidence_refs=(),
        promotion_policy_version=policy_version,
        promotion_reason_codes=(
            "legacy_migration",
            "migration:" + migration_id(
                life_id=assertion.life_id,
                memory_id=assertion.memory_id,
                revision=assertion.revision,
                layer=layer,
                policy_version=policy_version,
            ),
        ),
        valid_from_ms=assertion.valid_from_ms,
        expires_at_ms=assertion.expires_at_ms,
        context_eligible=True,
        learning_eligible=False,
        temperament_eligible=False,
        self_cognition_eligible=False,
        world_candidate_eligible=(layer == "L4_EXPLICIT" and semantic_domain == "WORLD"),
        created_at_ms=created_at_ms,
        derivation_sha256="0" * 64,
    ).with_computed_derivation_sha256()


__all__ = [
    "LEGACY_MIGRATION_POLICY",
    "build_legacy_derivation",
    "legacy_derivation_id",
    "legacy_layer_for_assertion",
    "migration_id",
]
