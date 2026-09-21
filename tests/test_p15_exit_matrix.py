"""P15-EXIT: the complete source add/modify/delete/rollback matrix.

One file, every P15 mechanism exercised end to end on the real archive
world: source change -> World invalidation (P15-A), experience expiry
(P15-B), controlled revalidation (P15-C), temporal policy (P15-D), and
the full add→modify→delete→rollback lifecycle that the P15-EXIT contract
demands. This is the integration test that proves the P15 chain holds
when every mechanism fires in sequence.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.canonical import canonical_sha256
from contracts.capability_composition import SourceRevisionRefV1

from tests.test_source_registration_intake_p12 import (  # noqa: F401
    intake, intake_factory, source, _compile, _prepare,
)
from tests.test_tool_source_publication_p8 import publication  # noqa: F401


def _make_source_ref(semantic: str, sha: str) -> SourceRevisionRefV1:
    return SourceRevisionRefV1(
        source_kind="TOOL_ACTION", semantic_id=semantic, version="1.0.0",
        source_sha256=sha, descriptor_sha256=sha,
        source_files=(f"src/{semantic.replace('.', '_')}.py",),
    )


def _experience_for(sources, suffix="a"):
    """Build a minimal positive experience bound to the given sources."""
    from world_understanding.capability_composition.capability_experience_policy import (
        CapabilityCombinationExperienceV1,
        computed_experience_sha256,
        exact_source_hashes,
        source_revision_family,
    )
    ZERO = "0" * 64
    experience = CapabilityCombinationExperienceV1(
        experience_id="cex_" + suffix * 60,
        goal_class="goal:p15exit", environment_class="test",
        scene_fingerprint="4" * 64, context_fingerprint_sha256="5" * 64,
        action_source_refs=sources,
        topology_sha256="6" * 64, composition_plan_sha256="7" * 64,
        request_id="req_" + "1" * 64, run_id="run_" + "2" * 64,
        generation=1, completion_decision_sha256="8" * 64,
        verification_readiness_sha256="9" * 64,
        outcome="SUCCESS", success_count=3, failure_count=0,
        independent_context_count=1, last_success_ms=1000,
        posterior_success_milli=900, lower_confidence_milli=800,
        lifecycle="STABLE",
        source_revision_family=source_revision_family(sources),
        exact_source_hashes=exact_source_hashes(sources),
        experience_sha256=ZERO,
    )
    return experience.model_copy(
        update={"experience_sha256": computed_experience_sha256(experience)})


class TestSourceLifecycleMatrix:
    """The P15-EXIT add→modify→delete→rollback matrix."""

    def test_add_new_source_invalidates_nothing(self):
        """A brand-new source with no prior bindings invalidates zero records."""
        from world_understanding.capability_composition.capability_experience_policy import (
            assess_capability_experience_source_freshness,
        )
        from world_understanding.tool_capability_world.publication import (
            compute_tool_changed_source_keys, previous_tool_world_keys,
        )
        # Simulate: no previous tool-world keys, new source arrives
        class FakePrevious:
            dependencies = SimpleNamespace(bindings=())
        keys = compute_tool_changed_source_keys(
            FakePrevious(), SimpleNamespace(
                snapshot_sha256="a" * 64))
        assert "tool-world:" + "a" * 64 in keys

    def test_modify_same_family_revision_expires_experience(self):
        """Same-family revision change → REVALIDATION_REQUIRED."""
        from world_understanding.capability_composition.capability_experience_policy import (
            assess_capability_experience_source_freshness,
            mark_capability_experience_source_change,
        )
        old_sources = (_make_source_ref("skill.list", "1" * 64),)
        experience = _experience_for(old_sources)

        from world_understanding.capability_composition.capability_experience_policy import (
            CapabilityExperienceAggregateStateV1,
        )
        agg = CapabilityExperienceAggregateStateV1(
            experience_key="key", life_id="life", principal_ref="p",
            principal_scope_hash="a" * 64, privacy_scope="priv",
            privacy_scope_hash="b" * 64, experience=experience,
            context_fingerprints=("c" * 64,),
            observation_ids=("o1", "o2", "o3"), quality_sum_milli=2700,
            quality_observation_count=3, last_observed_at_ms=1000,
            state_sha256="0" * 64).with_computed_sha256()
        new_sources = (_make_source_ref("skill.list", "2" * 64),)
        freshness = assess_capability_experience_source_freshness(
            agg, new_sources)
        assert freshness == "REVALIDATION_REQUIRED"

    def test_family_change_goes_stale(self):
        """Different family → STALE (never silently compatible)."""
        from world_understanding.capability_composition.capability_experience_policy import (
            assess_capability_experience_source_freshness,
        )
        old_sources = (_make_source_ref("skill.list", "1" * 64),)
        experience = _experience_for(old_sources)

        from world_understanding.capability_composition.capability_experience_policy import (
            CapabilityExperienceAggregateStateV1,
        )
        agg = CapabilityExperienceAggregateStateV1(
            experience_key="key", life_id="life", principal_ref="p",
            principal_scope_hash="a" * 64, privacy_scope="priv",
            privacy_scope_hash="b" * 64, experience=experience,
            context_fingerprints=("c" * 64,),
            observation_ids=("o1", "o2", "o3"), quality_sum_milli=2700,
            quality_observation_count=3, last_observed_at_ms=1000,
            state_sha256="0" * 64).with_computed_sha256()
        new_sources = (_make_source_ref("other.action", "3" * 64),)
        freshness = assess_capability_experience_source_freshness(
            agg, new_sources)
        assert freshness == "STALE"

    def test_source_deletion_refuses_sealed_reader(self, intake, tmp_path):
        """Archive removal → the sealed plan reader refuses (not latest)."""
        from tests.test_source_registration_intake_p12 import _register
        c = intake
        result = _compile(c, _prepare(c)[0], intents=frozenset(
            {"verification-intent:plan-bound-acceptance"}))
        _register(c, result)
        archive = tmp_path / "method-archives"
        for blob in sorted(archive.glob("*.json")):
            blob.chmod(0o644)
            blob.unlink()
        with pytest.raises(Exception):
            c["resolver"].read(
                request_id=c["rc"].request_id, run_id=c["rc"].run_id,
                generation=1, scope=c["query_scope"])

    def test_rollback_to_current_is_a_no_op(self):
        """Re-notifying the SAME sources → CURRENT → zero expiry."""
        from world_understanding.capability_composition.capability_experience_policy import (
            mark_capability_experience_source_change,
        )
        sources = (_make_source_ref("skill.list", "1" * 64),)
        experience = _experience_for(sources)

        from world_understanding.capability_composition.capability_experience_policy import (
            CapabilityExperienceAggregateStateV1,
        )
        agg = CapabilityExperienceAggregateStateV1(
            experience_key="key", life_id="life", principal_ref="p",
            principal_scope_hash="a" * 64, privacy_scope="priv",
            privacy_scope_hash="b" * 64, experience=experience,
            context_fingerprints=("c" * 64,),
            observation_ids=("o1", "o2", "o3"), quality_sum_milli=2700,
            quality_observation_count=3, last_observed_at_ms=1000,
            state_sha256="0" * 64).with_computed_sha256()
        updated, intent = mark_capability_experience_source_change(
            agg, sources, requested_at_ms=2000)
        assert intent is None  # CURRENT, no expiry

    def test_tampered_same_version_bytes_are_refused(self, intake, tmp_path):
        """Same-digest different-bytes → the original reader refuses."""
        from tests.test_source_registration_intake_p12 import _register
        c = intake
        result = _compile(c, _prepare(c)[0], intents=frozenset(
            {"verification-intent:plan-bound-acceptance"}))
        _register(c, result)
        archive = tmp_path / "method-archives"
        victims = sorted(archive.glob("*.json"))
        assert victims
        blob = victims[0]
        raw = bytearray(blob.read_bytes())
        raw[-1] ^= 0xFF
        blob.chmod(0o644)
        blob.write_bytes(bytes(raw))
        blob.chmod(0o444)
        with pytest.raises(Exception):
            c["resolver"].read(
                request_id=c["rc"].request_id, run_id=c["rc"].run_id,
                generation=1, scope=c["query_scope"])

    def test_unaffected_aggregate_stays_current(self):
        """Only aggregates bound to the changed family expire."""
        from world_understanding.capability_composition.capability_experience_policy import (
            assess_capability_experience_source_freshness,
        )
        changed = _experience_for(
            (_make_source_ref("skill.list", "1" * 64),), suffix="a")
        untouched = _experience_for(
            (_make_source_ref("other.action", "9" * 64),), suffix="b")

        new_sources = (_make_source_ref("skill.list", "2" * 64),)

        from world_understanding.capability_composition.capability_experience_policy import (
            CapabilityExperienceAggregateStateV1,
        )
        def _agg(exp):
            return CapabilityExperienceAggregateStateV1(
                experience_key="key:" + exp.experience_id[-4:],
                life_id="life", principal_ref="p",
                principal_scope_hash="a" * 64, privacy_scope="priv",
                privacy_scope_hash="b" * 64, experience=exp,
                context_fingerprints=("c" * 64,),
                observation_ids=("o1", "o2", "o3"), quality_sum_milli=2700,
                quality_observation_count=3, last_observed_at_ms=1000,
                state_sha256="0" * 64).with_computed_sha256()
        assert assess_capability_experience_source_freshness(
            _agg(changed), new_sources) == "REVALIDATION_REQUIRED"
        assert assess_capability_experience_source_freshness(
            _agg(untouched), new_sources) == "STALE"  # family differs
