from __future__ import annotations

from total_gateway.continuity import persist_working_checkpoint

from test_p18_m4_persistence_corruption import CorruptionRig


SOURCE_PROVIDER = "deepseek-v4-adapter-v1"
SOURCE_MODEL = "deepseek-v4"
TARGET_PROVIDER = "gpt-5.6-adapter-v1"
TARGET_MODEL = "gpt-5.6"


def _commit_source_provider_checkpoint(rig: CorruptionRig):
    rig.append_event("m4.cross-provider.source", now_ms=1400)
    frontier = rig.frontier(version=1, global_step=300)
    rig.store.commit_execution_frontier(frontier, expected_revision=0, updated_at_ms=1500)
    continuity = persist_working_checkpoint(
        rig.store,
        life_id=rig.life_id,
        request_id=rig.request_id,
        run_id=rig.run_id,
        generation=rig.generation,
        user_goal="cross-provider resume certification",
        hard_constraints=("same run and generation",),
        active_plan=("rehydrate structured frontier",),
        latest_safe_step="deepseek completed step 300",
        next_step="resume with target provider",
        recovery_preconditions=("version compatibility explicitly revalidated",),
        created_at_ms=1501,
    )
    result = rig.provider(
        rig.payload(
            "commit_checkpoint",
            now_ms=1502,
            frontier=frontier.model_dump(mode="json"),
            continuity_capsule_id=continuity.capsule.capsule_id,
            recovery_preconditions=["version compatibility explicitly revalidated"],
            critical_fact_status="verified",
            runtime_version="tiangong-v3-p18-m4",
            provider_version=SOURCE_PROVIDER,
            model_version=SOURCE_MODEL,
            tool_contract_version="omni_body.v1",
            skill_contract_version="skill.v1",
            task_contract_version="task.v1",
            semantic_handoff="structured frontier only; no provider-private reasoning transfer",
        )
    )
    assert result["committed"] is True
    return frontier


def _target_recovery_payload(rig: CorruptionRig, *, revalidated: bool):
    return rig.payload(
        "recover",
        now_ms=1700 if revalidated else 1600,
        runtime_version="tiangong-v3-p18-m4",
        provider_version=TARGET_PROVIDER,
        model_version=TARGET_MODEL,
        tool_contract_version="omni_body.v1",
        skill_contract_version="skill.v1",
        task_contract_version="task.v1",
        compatible_version_mismatches=["provider_profile_hash", "model_version"],
        version_revalidated=revalidated,
    )


def test_m4_cross_provider_switch_requires_explicit_revalidation_before_resume() -> None:
    rig = CorruptionRig()
    try:
        _commit_source_provider_checkpoint(rig)
        before = [
            event for event in rig.store.list_execution_events(
                rig.request_id, run_id=rig.run_id, generation=rig.generation
            ) if event.event_type == "run.resumed"
        ]
        blocked = rig.provider(_target_recovery_payload(rig, revalidated=False))
        after = [
            event for event in rig.store.list_execution_events(
                rig.request_id, run_id=rig.run_id, generation=rig.generation
            ) if event.event_type == "run.resumed"
        ]
        assert blocked["recoverable"] is True
        assert blocked["resume_allowed"] is False
        assert blocked["revalidation_required"] is True
        assert set(blocked["version_mismatches"]) == {"provider_profile_hash", "model_version"}
        assert len(after) == len(before)
    finally:
        rig.close()


def test_m4_cross_provider_resume_preserves_run_generation_contract_and_frontier() -> None:
    rig = CorruptionRig()
    try:
        source_frontier = _commit_source_provider_checkpoint(rig)
        request_id = rig.request_id
        run_id = rig.run_id
        generation = rig.generation
        root_goal_hash = rig.root_hash
        task_contract_hash = rig.task_hash
        authority_hash = source_frontier.authority_hash

        resumed = rig.provider(_target_recovery_payload(rig, revalidated=True))
        assert resumed["recoverable"] is True
        assert resumed["resume_allowed"] is True
        assert resumed["reconcile_required"] is False
        assert set(resumed["version_mismatches"]) == {"provider_profile_hash", "model_version"}

        frontier = resumed["frontier"]
        assert frontier["request_id"] == request_id
        assert frontier["run_id"] == run_id
        assert frontier["generation"] == generation
        assert frontier["root_goal_hash"] == root_goal_hash
        assert frontier["task_contract_hash"] == task_contract_hash
        assert frontier["authority_hash"] == authority_hash
        assert frontier["global_step"] == 300
        assert frontier["frontier_hash"] == source_frontier.frontier_hash

        events = rig.store.list_execution_events(
            request_id, run_id=run_id, generation=generation
        )
        resumed_events = [event for event in events if event.event_type == "run.resumed"]
        assert len(resumed_events) == 1
        assert all(event.run_id == run_id and event.generation == generation for event in events)

        checkpoint = resumed["checkpoint"]
        assert checkpoint["semantic_handoff"] == (
            "structured frontier only; no provider-private reasoning transfer"
        )
        assert checkpoint["provider_version"] == SOURCE_PROVIDER
        assert checkpoint["model_version"] == SOURCE_MODEL
    finally:
        rig.close()
