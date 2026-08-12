"""P15 M1 contract tests: layers, domains, hashes, DAG and scope rules."""

from __future__ import annotations

import unittest

from contracts import (
    MEMORY_DERIVATION_SCHEMA_VERSION,
    MemoryDerivationOrigin,
    MemoryDerivationV1,
    MemoryInvalidationRecord,
    MemoryLayer,
    MemoryParentRef,
    MemoryPromotionDisposition,
    MemorySemanticDomain,
    derive_promotion_key,
)


LIFE_ID = "life_p15_contract"
PRINCIPAL = "principal_alice"
PRIVACY = "privacy_alice_v1"
MEMORY_ID = "mem_" + "a" * 64
EVENT_ID = "lev_" + "b" * 64


def parent_ref(
    *, derivation_id: str | None = None, revision: int = 1
) -> MemoryParentRef:
    return MemoryParentRef(
        parent_derivation_id=derivation_id,
        memory_id=MEMORY_ID,
        memory_revision=revision,
        assertion_sha256="11" * 32,
        parent_ref_sha256="0" * 64,
    ).with_computed_parent_ref_sha256()


def derivation(**overrides) -> MemoryDerivationV1:
    values = dict(
        derivation_id="mdr_" + "d" * 64,
        life_id=LIFE_ID,
        memory_id=MEMORY_ID,
        memory_revision=1,
        memory_assertion_sha256="22" * 32,
        layer="L1_STREAM",
        semantic_domain="SYSTEM",
        origin="LIFE_EVENT",
        principal_ref=PRINCIPAL,
        workspace_ref=None,
        privacy_scope=PRIVACY,
        claim_key="event:" + EVENT_ID,
        parent_memory_refs=(),
        source_event_ids=(EVENT_ID,),
        lineage_root_event_ids=(EVENT_ID,),
        external_evidence_refs=(),
        promotion_policy_version="p15-layers-v1",
        promotion_reason_codes=(),
        valid_from_ms=1_000,
        expires_at_ms=None,
        context_eligible=True,
        learning_eligible=False,
        temperament_eligible=False,
        self_cognition_eligible=False,
        world_candidate_eligible=False,
        created_at_ms=2_000,
        derivation_sha256="0" * 64,
    )
    values.update(overrides)
    return MemoryDerivationV1(**values).with_computed_derivation_sha256()


class MemoryLayerContractTests(unittest.TestCase):
    def test_layer_domain_origin_literals_are_complete(self) -> None:
        self.assertEqual(
            MemoryLayer.__args__,
            (
                "L1_STREAM",
                "L2_DIARY",
                "L3_EXPERIENCE",
                "L4_EXPLICIT",
                "L5_CORE",
            ),
        )
        self.assertIn("WORLD", MemorySemanticDomain.__args__)
        self.assertIn("SELF_BEHAVIOR_PATTERN", MemorySemanticDomain.__args__)
        self.assertEqual(
            MemoryDerivationOrigin.__args__,
            (
                "LIFE_EVENT",
                "PROMOTION",
                "USER_EXPLICIT",
                "LEARNING_RESULT",
                "MIGRATION",
            ),
        )

    def test_derivation_schema_version_is_v1(self) -> None:
        self.assertEqual(
            MEMORY_DERIVATION_SCHEMA_VERSION,
            "tiangong.life.memory-derivation.v1",
        )
        self.assertEqual(
            derivation().schema_version,
            MEMORY_DERIVATION_SCHEMA_VERSION,
        )

    def test_parent_ref_hash_deterministic_and_valid(self) -> None:
        first = parent_ref(derivation_id="mdr_" + "e" * 64)
        second = parent_ref(derivation_id="mdr_" + "e" * 64)
        self.assertTrue(first.has_valid_parent_ref_sha256())
        self.assertEqual(first.parent_ref_sha256, second.parent_ref_sha256)

    def test_parent_ref_hash_changes_with_fields(self) -> None:
        base = parent_ref(derivation_id="mdr_" + "e" * 64, revision=1)
        other = parent_ref(derivation_id="mdr_" + "e" * 64, revision=2)
        self.assertNotEqual(
            base.parent_ref_sha256, other.parent_ref_sha256
        )

    def test_derivation_hash_deterministic(self) -> None:
        first = derivation()
        second = derivation()
        self.assertTrue(first.has_valid_derivation_sha256())
        self.assertEqual(first.derivation_sha256, second.derivation_sha256)

    def test_derivation_hash_sensitive_to_layer_and_claim(self) -> None:
        base = derivation()
        layered = derivation(layer="L2_DIARY", origin="PROMOTION")
        claimed = derivation(claim_key="event:" + "f" * 64)
        self.assertNotEqual(base.derivation_sha256, layered.derivation_sha256)
        self.assertNotEqual(base.derivation_sha256, claimed.derivation_sha256)

    def test_json_round_trip_preserves_digest(self) -> None:
        original = derivation()
        encoded = original.model_dump_json()
        restored = MemoryDerivationV1.model_validate_json(encoded)
        self.assertEqual(restored, original)
        self.assertTrue(restored.has_valid_derivation_sha256())

    def test_set_like_fields_must_be_sorted_unique(self) -> None:
        with self.assertRaises(ValueError):
            derivation(source_event_ids=(EVENT_ID, EVENT_ID))
        with self.assertRaises(ValueError):
            derivation(
                lineage_root_event_ids=(
                    "lev_" + "1" * 64,
                    "lev_" + "0" * 64,
                )
            )

    def test_text_fields_must_be_nfc_and_control_free(self) -> None:
        with self.assertRaises(ValueError):
            derivation(principal_ref="principal\x00alice")
        with self.assertRaises(ValueError):
            derivation(claim_key="claim\u0301bad")

    def test_l1_requires_life_event_origin_and_source_events(self) -> None:
        with self.assertRaises(ValueError):
            derivation(layer="L1_STREAM", origin="USER_EXPLICIT")
        with self.assertRaises(ValueError):
            derivation(layer="L1_STREAM", source_event_ids=())

    def test_l4_requires_user_explicit_bound_to_user_message(self) -> None:
        with self.assertRaises(ValueError):
            derivation(layer="L4_EXPLICIT", origin="PROMOTION")
        with self.assertRaises(ValueError):
            derivation(
                layer="L4_EXPLICIT",
                origin="USER_EXPLICIT",
                source_event_ids=(),
            )

    def test_non_migration_requires_lineage_roots(self) -> None:
        with self.assertRaises(ValueError):
            derivation(lineage_root_event_ids=())
        migrated = derivation(
            layer="L1_STREAM",
            origin="MIGRATION",
            source_event_ids=(),
            lineage_root_event_ids=(),
        )
        self.assertTrue(migrated.has_valid_derivation_sha256())

    def test_expiry_must_follow_validity_start(self) -> None:
        with self.assertRaises(ValueError):
            derivation(expires_at_ms=1_000)
        with self.assertRaises(ValueError):
            derivation(expires_at_ms=500)

    def test_temperament_eligibility_requires_l5_self_behavior_pattern(self) -> None:
        with self.assertRaises(ValueError):
            derivation(
                layer="L3_EXPERIENCE",
                origin="PROMOTION",
                temperament_eligible=True,
            )
        with self.assertRaises(ValueError):
            derivation(
                layer="L5_CORE",
                origin="PROMOTION",
                semantic_domain="USER_PREFERENCE",
                temperament_eligible=True,
            )

    def test_self_cognition_eligibility_requires_l5_self_domain(self) -> None:
        with self.assertRaises(ValueError):
            derivation(
                layer="L5_CORE",
                origin="PROMOTION",
                semantic_domain="USER_PROFILE",
                self_cognition_eligible=True,
            )
        valid = derivation(
            layer="L5_CORE",
            origin="PROMOTION",
            semantic_domain="SELF_IDENTITY",
            self_cognition_eligible=True,
        )
        self.assertTrue(valid.has_valid_derivation_sha256())

    def test_world_candidate_eligibility_requires_world_domain_and_mature_layer(
        self,
    ) -> None:
        with self.assertRaises(ValueError):
            derivation(
                layer="L1_STREAM",
                semantic_domain="WORLD",
                world_candidate_eligible=True,
            )
        with self.assertRaises(ValueError):
            derivation(
                layer="L3_EXPERIENCE",
                origin="PROMOTION",
                semantic_domain="USER_PROFILE",
                world_candidate_eligible=True,
            )
        valid = derivation(
            layer="L3_EXPERIENCE",
            origin="PROMOTION",
            semantic_domain="WORLD",
            world_candidate_eligible=True,
        )
        self.assertTrue(valid.has_valid_derivation_sha256())

    def test_promotion_key_i08_is_canonical_and_order_independent(self) -> None:
        kwargs = dict(
            policy_version="p15-layers-v1",
            life_id=LIFE_ID,
            target_layer="L3_EXPERIENCE",
            parent_assertion_sha256=("11" * 32, "22" * 32),
            semantic_domain="WORLD",
            claim_key="claim:earth",
            lineage_root_event_ids=(EVENT_ID, "lev_" + "0" * 64),
        )
        reversed_kwargs = dict(
            kwargs,
            parent_assertion_sha256=("22" * 32, "11" * 32),
            lineage_root_event_ids=("lev_" + "0" * 64, EVENT_ID),
        )
        self.assertEqual(
            derive_promotion_key(**kwargs),
            derive_promotion_key(**reversed_kwargs),
        )
        self.assertNotEqual(
            derive_promotion_key(**kwargs),
            derive_promotion_key(**{**kwargs, "claim_key": "claim:mars"}),
        )
        self.assertEqual(len(derive_promotion_key(**kwargs)), 64)

    def test_promotion_disposition_hash_and_parent_requirement(self) -> None:
        disposition = MemoryPromotionDisposition(
            promotion_key="1" * 64,
            life_id=LIFE_ID,
            principal_ref=PRINCIPAL,
            target_layer="L3_EXPERIENCE",
            claim_key="claim:earth",
            semantic_domain="WORLD",
            policy_version="p15-layers-v1",
            parent_assertion_sha256=("11" * 32,),
            lineage_root_event_ids=(EVENT_ID,),
            allowed=True,
            reason_codes=("l2_support",),
            support_milli=700,
            counter_milli=100,
            independence_group_count=2,
            recurrence_count=2,
            valid_from_ms=1_000,
            created_at_ms=2_000,
            disposition_sha256="0" * 64,
        ).with_computed_disposition_sha256()
        self.assertTrue(disposition.has_valid_disposition_sha256())
        with self.assertRaises(ValueError):
            MemoryPromotionDisposition(
                promotion_key="1" * 64,
                life_id=LIFE_ID,
                principal_ref=PRINCIPAL,
                target_layer="L5_CORE",
                claim_key="claim:earth",
                semantic_domain="WORLD",
                policy_version="p15-layers-v1",
                parent_assertion_sha256=(),
                lineage_root_event_ids=(EVENT_ID,),
                allowed=True,
                reason_codes=(),
                support_milli=900,
                counter_milli=0,
                independence_group_count=1,
                recurrence_count=1,
                valid_from_ms=1_000,
                created_at_ms=2_000,
                disposition_sha256="0" * 64,
            ).with_computed_disposition_sha256()

    def test_invalidation_record_hash_and_sorted_descendants(self) -> None:
        record = MemoryInvalidationRecord(
            invalidation_id="miv_" + "9" * 64,
            life_id=LIFE_ID,
            principal_ref=PRINCIPAL,
            derivation_id="mdr_" + "d" * 64,
            memory_id=MEMORY_ID,
            memory_revision=1,
            assertion_sha256="22" * 32,
            reason="corrected",
            source_trigger_ref="33" * 32,
            invalidated_at_ms=3_000,
            descendant_derivation_ids=("mdr_" + "1" * 64,),
            invalidation_sha256="0" * 64,
        ).with_computed_invalidation_sha256()
        self.assertTrue(record.has_valid_invalidation_sha256())
        with self.assertRaises(ValueError):
            MemoryInvalidationRecord(
                invalidation_id="miv_" + "8" * 64,
                life_id=LIFE_ID,
                principal_ref=PRINCIPAL,
                derivation_id="mdr_" + "d" * 64,
                memory_id=MEMORY_ID,
                memory_revision=1,
                assertion_sha256="22" * 32,
                reason="corrected",
                source_trigger_ref=None,
                invalidated_at_ms=3_000,
                descendant_derivation_ids=(
                    "mdr_" + "1" * 64,
                    "mdr_" + "1" * 64,
                ),
                invalidation_sha256="0" * 64,
            ).with_computed_invalidation_sha256()


if __name__ == "__main__":
    unittest.main()
