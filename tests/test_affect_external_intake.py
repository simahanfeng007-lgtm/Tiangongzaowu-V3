from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from pydantic import ValidationError

from contracts import (
    AffectCandidateDimensions,
    AffectSignal,
    AffectSourcePolicySnapshot,
    LifeEventEnvelope,
    canonical_sha256,
)
from life_service.store import SHADOW_STORE_SCHEMA_VERSION, LifeShadowStore, LifeShadowStoreError
from life_service import store as life_store_module
from life_service.affect import (
    evaluate_affect_gate,
    system_health_candidate,
    task_outcome_candidate,
)
from tests.life_contract_support import HASH_ZERO, viability_state


LIFE_ID = "life_affect_external"


def policy(**overrides) -> AffectSourcePolicySnapshot:
    values = {
        "life_id": LIFE_ID,
        "revision": 1,
        "supersedes_policy_sha256": None,
        "news_enabled": True,
        "news_subscription_refs": ("news_subscription",),
        "allowed_news_sources": ("trusted_news",),
        "allowed_news_topics": ("animal_welfare", "world_events"),
        "weather_enabled": True,
        "weather_subscription_ref": "weather_subscription",
        "allowed_weather_sources": ("trusted_weather",),
        "authorized_weather_location_ref": "beijing_authorized",
        "news_max_effect_milli": 200,
        "weather_max_effect_milli": 60,
        "effective_at_ms": 500,
        "policy_sha256": HASH_ZERO,
    }
    values.update(overrides)
    return AffectSourcePolicySnapshot(**values)


def candidate(**overrides) -> AffectCandidateDimensions:
    values = {
        "novelty_milli": 500,
        "goal_congruence_milli": -200,
        "threat_milli": 300,
        "loss_milli": 700,
        "obstruction_milli": 100,
        "certainty_milli": 800,
        "controllability_milli": 300,
        "social_warmth_milli": 100,
        "social_trust_milli": 500,
        "intensity_milli": 300,
        "impact_on_others_milli": 800,
        "norm_relevance_milli": 700,
        "urgency_milli": 200,
    }
    values.update(overrides)
    return AffectCandidateDimensions(**values)


def event(
    sequence: int,
    previous_hash: str | None,
    *,
    content_sha256: str,
) -> LifeEventEnvelope:
    return LifeEventEnvelope(
        event_id="lev_" + f"{sequence:064x}",
        life_id=LIFE_ID,
        sequence=sequence,
        writer_epoch=1,
        source_service="affect_test",
        source_kind="system_health",
        event_kind="world.signal.observed",
        occurred_at_ms=1_000 + sequence,
        observed_at_ms=1_000 + sequence,
        principal_ref="principal_test",
        subject_refs=("subject_affect",),
        evidence_class="observed",
        source_credibility_milli=1000,
        privacy_scope="private",
        content_object_id="object_affect_" + str(sequence),
        content_sha256=content_sha256,
        dedupe_key=canonical_sha256({"event": sequence}),
        causation_id=None,
        correlation_id="affect_correlation",
        previous_event_hash=previous_hash,
        event_hash=HASH_ZERO,
        signer_key_id="test_signer",
        signature="a" * 128,
    ).with_computed_event_hash()


def signal(
    source_event: LifeEventEnvelope,
    *,
    family: str,
    source_stream_id: str,
    source_sequence: int,
    source_name: str,
    subscription_ref: str | None = None,
    topic_ref: str | None = None,
    location_ref: str | None = None,
    dedupe_key: str | None = None,
    prompt_injection_detected: bool = False,
    content_verification: str = "corroborated",
    credibility: int = 1000,
    relevance: int = 1000,
    dimensions: AffectCandidateDimensions | None = None,
) -> AffectSignal:
    identity = {
        "domain": "tiangong.life.affect-signal.v1",
        "life_id": LIFE_ID,
        "source_epoch": 1,
        "source_event_id": source_event.event_id,
        "source_sequence": source_sequence,
        "source_stream_id": source_stream_id,
    }
    return AffectSignal(
        signal_id="afg_" + canonical_sha256(identity),
        life_id=LIFE_ID,
        source_event_id=source_event.event_id,
        source_event_hash=source_event.event_hash,
        source_family=family,
        source_stream_id=source_stream_id,
        source_epoch=1,
        source_sequence=source_sequence,
        source_name=source_name,
        subscription_ref=subscription_ref,
        topic_ref=topic_ref,
        location_ref=location_ref,
        content_sha256=source_event.content_sha256,
        dedupe_key=dedupe_key or canonical_sha256({"content": source_event.content_sha256}),
        content_verification=content_verification,
        prompt_injection_detected=prompt_injection_detected,
        source_credibility_milli=credibility,
        self_relevance_milli=relevance,
        candidate=dimensions or candidate(),
        occurred_at_ms=source_event.occurred_at_ms,
        observed_at_ms=source_event.observed_at_ms,
        signal_sha256=HASH_ZERO,
    ).with_computed_signal_identity()


class AffectExternalIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "affect.shadow.sqlite3"
        self.store = LifeShadowStore.open(self.path, create=True, now_ms=100)
        state = viability_state().model_copy(
            update={"life_id": LIFE_ID, "state_sha256": HASH_ZERO}
        ).with_computed_state_sha256()
        self.store.put_viability_state(state)
        self.store.put_affect_source_policy(policy().with_computed_policy_sha256())
        self.previous_event_hash: str | None = None
        self.event_sequence = 0

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def append_event(self, marker: str):
        self.event_sequence += 1
        value = event(
            self.event_sequence,
            self.previous_event_hash,
            content_sha256=canonical_sha256({"marker": marker}),
        )
        self.store.append_event(value)
        self.previous_event_hash = value.event_hash
        return value

    def test_policy_and_signal_contracts_reject_implicit_or_overstated_sources(self) -> None:
        with self.assertRaises(ValidationError):
            policy(news_enabled=False)
        with self.assertRaises(ValidationError):
            AffectSignal.model_validate(
                {
                    **signal(
                        self.append_event("invalid"),
                        family="news",
                        source_stream_id="news_stream",
                        source_sequence=1,
                        source_name="trusted_news",
                        subscription_ref="news_subscription",
                        topic_ref="animal_welfare",
                    ).model_dump(mode="python"),
                    "content_verification": "single_source",
                    "source_credibility_milli": 900,
                },
                strict=True,
            )

    def test_machine_task_and_health_mappings_are_fixed_and_conservative(self) -> None:
        succeeded = task_outcome_candidate("succeeded")
        failed = task_outcome_candidate("failed_final")
        degraded = system_health_candidate("degraded")
        recovered = system_health_candidate("recovered")
        self.assertGreater(succeeded.goal_congruence_milli, 0)
        self.assertLess(failed.goal_congruence_milli, 0)
        self.assertGreater(degraded.threat_milli, recovered.threat_milli)
        self.assertGreater(recovered.goal_congruence_milli, 0)
        with self.assertRaises(ValueError):
            task_outcome_candidate("model_says_success")

    def test_news_repetition_habituates_and_duplicate_retry_is_idempotent(self) -> None:
        repeated_key = canonical_sha256({"same_news": True})
        first_event = self.append_event("news-1")
        first_signal = signal(
            first_event,
            family="news",
            source_stream_id="news_stream",
            source_sequence=1,
            source_name="trusted_news",
            subscription_ref="news_subscription",
            topic_ref="animal_welfare",
            dedupe_key=repeated_key,
        )
        policy_value = self.store.get_latest_affect_source_policy(LIFE_ID)
        intensities = tuple(
            evaluate_affect_gate(
                first_signal, policy_value, repetition_count=count
            ).effective_intensity_milli
            for count in range(1, 21)
        )
        self.assertEqual(intensities, tuple(sorted(intensities, reverse=True)))
        self.assertEqual(intensities[-1], 0)
        first = self.store.ingest_affect_signal(first_signal, received_at_ms=2_000)
        self.assertTrue(first.receipt.accepted)
        self.assertEqual(first.receipt.effective_intensity_milli, 200)
        duplicate = self.store.ingest_affect_signal(first_signal, received_at_ms=2_100)
        self.assertTrue(duplicate.receipt.duplicate)
        self.assertFalse(duplicate.signal_created)
        self.assertEqual(duplicate.state, first.state)

        second_event = self.append_event("news-2")
        second = self.store.ingest_affect_signal(
            signal(
                second_event,
                family="news",
                source_stream_id="news_stream",
                source_sequence=2,
                source_name="trusted_news",
                subscription_ref="news_subscription",
                topic_ref="animal_welfare",
                dedupe_key=repeated_key,
            ),
            received_at_ms=3_000,
        )
        self.assertEqual(second.receipt.repetition_count, 2)
        self.assertEqual(second.receipt.effective_intensity_milli, 150)
        self.assertEqual(second.state.revision, first.state.revision + 1)
        self.assertLessEqual(second.state.emotions.sadness, 305)
        self.assertEqual(self.store.health()["schema_version"], SHADOW_STORE_SCHEMA_VERSION)

    def test_health_rejects_affect_repetition_ledger_tampering(self) -> None:
        source_event = self.append_event("tamper")
        self.store.ingest_affect_signal(
            signal(
                source_event,
                family="system",
                source_stream_id="tamper_stream",
                source_sequence=1,
                source_name="health_monitor",
                content_verification="machine_verified",
            ),
            received_at_ms=2_000,
        )
        self.store._connection.execute(  # noqa: SLF001
            "UPDATE affect_dedupe SET occurrence_count = 99"
        )
        with self.assertRaisesRegex(LifeShadowStoreError, "repetition ledger"):
            self.store.health()

    def test_fake_injected_news_and_wrong_weather_location_never_change_state(self) -> None:
        injected_event = self.append_event("injected-news")
        injected = self.store.ingest_affect_signal(
            signal(
                injected_event,
                family="news",
                source_stream_id="bad_news_stream",
                source_sequence=1,
                source_name="untrusted_news",
                subscription_ref="news_subscription",
                topic_ref="animal_welfare",
                prompt_injection_detected=True,
            ),
            received_at_ms=2_000,
        )
        self.assertFalse(injected.receipt.accepted)
        self.assertEqual(injected.receipt.reason_code, "affect.rejected.prompt_injection")
        self.assertIsNone(injected.state)
        self.assertIsNone(self.store.get_latest_affective_state(LIFE_ID))

        weather_event = self.append_event("wrong-location")
        wrong_weather = self.store.ingest_affect_signal(
            signal(
                weather_event,
                family="weather",
                source_stream_id="weather_stream",
                source_sequence=1,
                source_name="trusted_weather",
                subscription_ref="weather_subscription",
                location_ref="shanghai_not_authorized",
            ),
            received_at_ms=3_000,
        )
        self.assertFalse(wrong_weather.receipt.accepted)
        self.assertEqual(wrong_weather.receipt.reason_code, "affect.rejected.location")
        self.assertIsNone(self.store.get_latest_affective_state(LIFE_ID))

        unverified_event = self.append_event("fake-news")
        unverified = self.store.ingest_affect_signal(
            signal(
                unverified_event,
                family="news",
                source_stream_id="fake_news_stream",
                source_sequence=1,
                source_name="trusted_news",
                subscription_ref="news_subscription",
                topic_ref="animal_welfare",
                content_verification="unverified",
                credibility=0,
            ),
            received_at_ms=4_000,
        )
        self.assertFalse(unverified.receipt.accepted)
        self.assertEqual(unverified.receipt.effective_intensity_milli, 0)

    def test_weather_is_low_amplitude_and_internal_task_events_are_visible(self) -> None:
        weather_event = self.append_event("rain")
        weather = self.store.ingest_affect_signal(
            signal(
                weather_event,
                family="weather",
                source_stream_id="weather_stream",
                source_sequence=1,
                source_name="trusted_weather",
                subscription_ref="weather_subscription",
                location_ref="beijing_authorized",
                dimensions=candidate(intensity_milli=1000, loss_milli=500),
            ),
            received_at_ms=2_000,
        )
        self.assertTrue(weather.receipt.accepted)
        self.assertLessEqual(weather.receipt.effective_intensity_milli, 60)

        task_event = self.append_event("task-success")
        task = self.store.ingest_affect_signal(
            signal(
                task_event,
                family="task",
                source_stream_id="task_stream",
                source_sequence=1,
                source_name="completion_gate",
                content_verification="machine_verified",
                dimensions=candidate(
                    goal_congruence_milli=800,
                    loss_milli=0,
                    threat_milli=0,
                    intensity_milli=500,
                    controllability_milli=900,
                ),
            ),
            received_at_ms=3_000,
        )
        self.assertGreater(task.state.emotions.joy, weather.state.emotions.joy)
        self.assertEqual(task.state.authority, "attention_and_expression_only")
        self.assertFalse(task.state.may_change_facts)
        self.assertFalse(task.state.may_change_permissions)
        self.assertFalse(task.state.may_claim_experience)

    def test_out_of_order_source_fails_without_consuming_the_missing_offset(self) -> None:
        gap_event = self.append_event("gap")
        gap_signal = signal(
            gap_event,
            family="system",
            source_stream_id="system_stream",
            source_sequence=2,
            source_name="health_monitor",
            content_verification="machine_verified",
        )
        with self.assertRaisesRegex(LifeShadowStoreError, "discontinuous"):
            self.store.ingest_affect_signal(gap_signal, received_at_ms=2_000)
        first_event = self.append_event("system-first")
        accepted = self.store.ingest_affect_signal(
            signal(
                first_event,
                family="system",
                source_stream_id="system_stream",
                source_sequence=1,
                source_name="health_monitor",
                content_verification="machine_verified",
            ),
            received_at_ms=3_000,
        )
        self.assertTrue(accepted.receipt.accepted)


class AffectReplayDeterminismTests(unittest.TestCase):
    def test_identical_event_sequence_replays_to_identical_affective_state(self) -> None:
        def run(root: Path):
            with LifeShadowStore.open(root / "life.shadow.sqlite3", create=True, now_ms=100) as store:
                viable = viability_state().model_copy(
                    update={"life_id": LIFE_ID, "state_sha256": HASH_ZERO}
                ).with_computed_state_sha256()
                store.put_viability_state(viable)
                store.put_affect_source_policy(policy().with_computed_policy_sha256())
                previous = None
                for sequence, family in enumerate(("news", "system", "relationship"), 1):
                    content_hash = canonical_sha256({"sequence": sequence})
                    source_event = event(sequence, previous, content_sha256=content_hash)
                    store.append_event(source_event)
                    previous = source_event.event_hash
                    external = family == "news"
                    store.ingest_affect_signal(
                        signal(
                            source_event,
                            family=family,
                            source_stream_id=family + "_stream",
                            source_sequence=1,
                            source_name="trusted_news" if external else family + "_source",
                            subscription_ref="news_subscription" if external else None,
                            topic_ref="world_events" if external else None,
                            content_verification="corroborated" if external else "machine_verified",
                        ),
                        received_at_ms=2_000 + sequence,
                    )
                return store.get_latest_affective_state(LIFE_ID)

        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            state_a = run(Path(first))
            state_b = run(Path(second))
        self.assertEqual(state_a, state_b)
        self.assertEqual(state_a.state_sha256, state_b.state_sha256)

    def test_v3_to_v4_affect_migration_preserves_event_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "life.shadow.sqlite3"
            source_event = event(
                1,
                None,
                content_sha256=canonical_sha256({"migration": "v3-v4"}),
            )
            with LifeShadowStore.open(path, create=True, now_ms=100) as store:
                store.append_event(source_event)
                before = bytes(
                    store._connection.execute(  # noqa: SLF001
                        "SELECT envelope FROM life_events WHERE event_id = ?",
                        (source_event.event_id,),
                    ).fetchone()["envelope"]
                )
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("BEGIN IMMEDIATE")
                for table in (
                    "root_continuation_bindings",
                    "root_experience_heads",
                    "run_life_bindings",
                    "life_authority_heads",
                    "causal_episodes_vnext",
                    "stimulus_inbox",
                    "cognition_lane_leases",
                    "cognition_state",
                    "model_attempt_shadow",
                    "life_turn_commits",
                    "capability_candidate_artifacts",
                    "capability_pointer_heads",
                    "memory_change_log",
                    "memory_outbox",
                    "context_authorizations",
                    "capability_invalidations",
                    "capability_learning_decisions",
                    "capability_rollbacks",
                    "episode_outcomes",
                    "reflection_question_decisions",
                    "autonomy_usage_snapshots",
                    "autonomy_policies",
                    "action_candidates",
                    "viability_observations",
                    "affect_dedupe",
                    "affect_source_offsets",
                    "affect_signal_receipts",
                    "affect_source_policies",
                ):
                    connection.execute(f"DROP TABLE {table}")
                connection.execute("DELETE FROM schema_migrations WHERE version >= 4")
                connection.execute(
                    "UPDATE schema_metadata SET value = ? WHERE key = 'schema_sha256'",
                    (life_store_module._P3_SCHEMA_SHA256,),  # noqa: SLF001
                )
                connection.execute("PRAGMA user_version=3")
                connection.execute("COMMIT")
            finally:
                connection.close()
            with LifeShadowStore.open(path, create=False, now_ms=2_000) as migrated:
                after = bytes(
                    migrated._connection.execute(  # noqa: SLF001
                        "SELECT envelope FROM life_events WHERE event_id = ?",
                        (source_event.event_id,),
                    ).fetchone()["envelope"]
                )
                self.assertEqual(after, before)
                self.assertEqual(migrated.health()["schema_version"], SHADOW_STORE_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
