from __future__ import annotations

import json

import pytest

from contracts import canonical_sha256
from total_gateway.continuity import persist_working_checkpoint
from total_gateway.regenerative_execution import ExecutionFrontier, ZERO_HASH, derive_logical_effect_id
from tiangong_kernel.l4_action_grounding.model_provider_adapter import all_provider_factsheets

from test_p18_m4_persistence_corruption import CorruptionRig


SOURCE_PROVIDER = "deepseek_v4"
TARGET_PROVIDERS = ("mimo", "glm_5_2", "minimax_m3", "gpt_5_6")


def _versions(provider_id: str) -> tuple[str, str]:
    facts = all_provider_factsheets()[provider_id]
    return f"{provider_id}-adapter-v1", facts.default_model_id


def _immutable_effect_intent(rig: CorruptionRig) -> dict[str, object]:
    target = "workspace:/cross-provider/final-artifact.txt"
    postcondition = canonical_sha256(
        {"target": target, "content": "committed-before-provider-switch"}
    )
    return {
        "logical_effect_id": derive_logical_effect_id(
            request_id=rig.request_id,
            run_id=rig.run_id,
            generation=rig.generation,
            obligation_key="cross-provider-final-artifact",
            effect_namespace="filesystem.write",
            normalized_target=target,
            desired_postcondition_sha256=postcondition,
        ),
        "obligation_key": "cross-provider-final-artifact",
        "effect_namespace": "filesystem.write",
        "normalized_target": target,
        "desired_postcondition_sha256": postcondition,
    }


def _commit_source_effect(rig: CorruptionRig) -> tuple[dict, dict[str, object]]:
    intent = _immutable_effect_intent(rig)
    prepared = rig.provider(
        rig.payload(
            "prepare_effect",
            now_ms=5_000,
            epoch_index=3,
            global_step=250,
            attempt=1,
            **intent,
        )
    )
    assert prepared["disposition"] == "prepared"
    started = rig.provider(
        rig.payload(
            "start_effect",
            now_ms=5_010,
            epoch_index=3,
            effect_id=prepared["effect_id"],
            logical_effect_id=prepared["logical_effect_id"],
            attempt_id=prepared["attempt_id"],
            step_id=prepared["step_id"],
        )
    )
    assert started["dispatch_permitted"] is True
    committed = rig.provider(
        rig.payload(
            "finish_effect",
            now_ms=5_020,
            epoch_index=3,
            effect_id=prepared["effect_id"],
            logical_effect_id=prepared["logical_effect_id"],
            attempt_id=prepared["attempt_id"],
            step_id=prepared["step_id"],
            outcome="succeeded",
            result_summary={
                "verified_reality": "workspace:/cross-provider/final-artifact.txt",
                "content_sha256": intent["desired_postcondition_sha256"],
            },
        )
    )
    assert committed["effect_state"] == "SUCCEEDED"
    return prepared, intent


def _run_source_300_and_checkpoint(rig: CorruptionRig) -> tuple[ExecutionFrontier, dict, dict[str, object]]:
    source_provider_version, source_model_version = _versions(SOURCE_PROVIDER)
    for step in range(1, 301):
        event, created = rig.store.append_execution_event(
            event_key=f"cross-provider-source-step:{step}",
            request_id=rig.request_id,
            run_id=rig.run_id,
            generation=rig.generation,
            epoch_index=step // 75,
            event_type="step.observed",
            payload={
                "provider_id": SOURCE_PROVIDER,
                "step": step,
                "observation": "durable-structured-step",
            },
            created_at_ms=2_000 + step,
        )
        assert created is True
        assert event.run_id == rig.run_id
        assert event.generation == rig.generation

    committed_effect, intent = _commit_source_effect(rig)
    verified_fact_head = "fact_" + canonical_sha256(
        {"provider": SOURCE_PROVIDER, "fact": "source-300-steps-verified"}
    )
    artifact_revision_head = "artifact_" + canonical_sha256(
        {"path": intent["normalized_target"], "revision": 1}
    )
    frontier = ExecutionFrontier(
        request_id=rig.request_id,
        run_id=rig.run_id,
        generation=rig.generation,
        life_id=rig.life_id,
        root_goal_hash=rig.root_hash,
        task_contract_hash=rig.task_hash,
        authority_hash=rig.frontier(version=1, global_step=300).authority_hash,
        global_step=300,
        epoch_index=4,
        epoch_step=0,
        completed_obligation_ids=("cross-provider-source-300",),
        active_obligation_id=None,
        pending_obligation_ids=("continue-under-target-provider",),
        verified_fact_head=verified_fact_head,
        artifact_revision_head=artifact_revision_head,
        pending_effect_ids=(),
        ambiguous_effect_ids=(),
        active_blockers=(),
        failed_strategy_ids=(),
        latest_safe_step="DeepSeek source provider completed durable step 300",
        next_action_hint="rehydrate structured state and continue same Run",
        provider_turn_state_ref=None,
        frontier_version=1,
        frontier_hash=ZERO_HASH,
    ).with_computed_hash()
    rig.store.commit_execution_frontier(frontier, expected_revision=0, updated_at_ms=5_100)
    handoff_payload = {
        "schema": "p18-m4-cross-provider-handoff-v1",
        "root_goal_hash": rig.root_hash,
        "task_contract_hash": rig.task_hash,
        "verified_fact_head": verified_fact_head,
        "artifact_revision_head": artifact_revision_head,
        "committed_logical_effect_id": committed_effect["logical_effect_id"],
        "next_action": "continue same Run",
    }
    continuity = persist_working_checkpoint(
        rig.store,
        life_id=rig.life_id,
        request_id=rig.request_id,
        run_id=rig.run_id,
        generation=rig.generation,
        user_goal="cross-provider same-run final certification",
        hard_constraints=("no new run", "no private reasoning transfer"),
        active_plan=("rehydrate verified structured frontier",),
        latest_safe_step="DeepSeek source provider completed durable step 300",
        next_step="continue same Run under target provider",
        recovery_preconditions=("provider/model drift explicitly revalidated",),
        created_at_ms=5_101,
    )
    checkpoint = rig.provider(
        rig.payload(
            "commit_checkpoint",
            now_ms=5_102,
            frontier=frontier.model_dump(mode="json"),
            continuity_capsule_id=continuity.capsule.capsule_id,
            recovery_preconditions=["provider/model drift explicitly revalidated"],
            critical_fact_status="verified",
            runtime_version="tiangong-v3-p18-m4-cross-provider-final",
            provider_version=source_provider_version,
            model_version=source_model_version,
            tool_contract_version="omni_body.v1",
            skill_contract_version="skill.v1",
            task_contract_version="task.v1",
            semantic_handoff=json.dumps(handoff_payload, sort_keys=True, separators=(",", ":")),
        )
    )
    assert checkpoint["committed"] is True
    return frontier, committed_effect, intent


def _recover_under_target(rig: CorruptionRig, target_provider: str, *, revalidated: bool) -> dict:
    target_provider_version, target_model_version = _versions(target_provider)
    return rig.provider(
        rig.payload(
            "recover",
            now_ms=6_000,
            runtime_version="tiangong-v3-p18-m4-cross-provider-final",
            provider_version=target_provider_version,
            model_version=target_model_version,
            tool_contract_version="omni_body.v1",
            skill_contract_version="skill.v1",
            task_contract_version="task.v1",
            compatible_version_mismatches=["provider_profile_hash", "model_version"],
            version_revalidated=revalidated,
        )
    )


@pytest.mark.parametrize("target_provider", TARGET_PROVIDERS)
def test_m4_cross_provider_final_300_steps_resume_same_run_without_private_reasoning_or_effect_replay(
    target_provider: str,
) -> None:
    rig = CorruptionRig()
    try:
        source_frontier, committed_effect, intent = _run_source_300_and_checkpoint(rig)
        source_identity = (
            rig.request_id,
            rig.run_id,
            rig.generation,
            rig.root_hash,
            rig.task_hash,
            source_frontier.authority_hash,
        )
        blocked = _recover_under_target(rig, target_provider, revalidated=False)
        assert blocked["resume_allowed"] is False
        assert blocked["revalidation_required"] is True
        assert set(blocked["version_mismatches"]) == {"provider_profile_hash", "model_version"}

        resumed = _recover_under_target(rig, target_provider, revalidated=True)
        assert resumed["recoverable"] is True
        assert resumed["resume_allowed"] is True
        frontier = resumed["frontier"]
        target_identity = (
            frontier["request_id"],
            frontier["run_id"],
            frontier["generation"],
            frontier["root_goal_hash"],
            frontier["task_contract_hash"],
            frontier["authority_hash"],
        )
        assert target_identity == source_identity
        assert frontier["global_step"] == 300
        assert frontier["verified_fact_head"] == source_frontier.verified_fact_head
        assert frontier["artifact_revision_head"] == source_frontier.artifact_revision_head
        assert frontier["completed_obligation_ids"] == ["cross-provider-source-300"]
        assert frontier["pending_obligation_ids"] == ["continue-under-target-provider"]
        assert frontier["frontier_hash"] == source_frontier.frontier_hash

        checkpoint = resumed["checkpoint"]
        handoff = json.loads(checkpoint["semantic_handoff"])
        assert handoff["verified_fact_head"] == source_frontier.verified_fact_head
        assert handoff["artifact_revision_head"] == source_frontier.artifact_revision_head
        assert handoff["committed_logical_effect_id"] == committed_effect["logical_effect_id"]
        forbidden = {"reasoning", "thinking", "chain_of_thought", "private_reasoning"}
        assert forbidden.isdisjoint(handoff)

        before_effects = rig.store.list_effects_for_request(
            rig.request_id, run_id=rig.run_id, generation=rig.generation
        )
        before_commits = [
            event for event in rig.store.list_execution_events(
                rig.request_id, run_id=rig.run_id, generation=rig.generation
            )
            if event.event_type == "step.committed"
            and event.logical_effect_id == committed_effect["logical_effect_id"]
        ]
        assert len(before_effects) == 1
        assert len(before_commits) == 1

        retry = rig.provider(
            rig.payload(
                "prepare_effect",
                now_ms=6_100,
                epoch_index=4,
                global_step=301,
                attempt=2,
                **intent,
            )
        )
        assert retry["disposition"] == "already_committed"
        assert retry["effect_id"] == committed_effect["effect_id"]
        after_effects = rig.store.list_effects_for_request(
            rig.request_id, run_id=rig.run_id, generation=rig.generation
        )
        after_commits = [
            event for event in rig.store.list_execution_events(
                rig.request_id, run_id=rig.run_id, generation=rig.generation
            )
            if event.event_type == "step.committed"
            and event.logical_effect_id == committed_effect["logical_effect_id"]
        ]
        assert len(after_effects) == 1
        assert len(after_commits) == 1

        source_steps = [
            event for event in rig.store.list_execution_events(
                rig.request_id, run_id=rig.run_id, generation=rig.generation
            )
            if event.event_type == "step.observed"
            and event.payload.get("provider_id") == SOURCE_PROVIDER
        ]
        assert len(source_steps) == 300
        resumed_events = [
            event for event in rig.store.list_execution_events(
                rig.request_id, run_id=rig.run_id, generation=rig.generation
            )
            if event.event_type == "run.resumed"
        ]
        assert len(resumed_events) == 1
    finally:
        rig.close()
