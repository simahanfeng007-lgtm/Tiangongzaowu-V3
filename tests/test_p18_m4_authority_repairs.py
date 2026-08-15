from __future__ import annotations

from test_p18_m4_corruption_matrix import CorruptionRig, _effect_payload


def test_m4_dispatch_uses_existing_action_fence_permit_and_releases_on_receipt() -> None:
    rig = CorruptionRig()
    try:
        prepared = rig.provider(
            _effect_payload(rig, "prepare_effect", step=1, attempt=1, now_ms=3_000)
        )
        assert rig.store.action_fence_status()["inflight_count"] == 0
        started = rig.provider(
            rig.payload(
                "start_effect",
                now_ms=3_010,
                epoch_index=0,
                effect_id=prepared["effect_id"],
                logical_effect_id=prepared["logical_effect_id"],
                attempt_id=prepared["attempt_id"],
                step_id=prepared["step_id"],
            )
        )
        assert started["dispatch_permitted"] is True
        assert rig.store.action_fence_status()["inflight_count"] == 1
        finished = rig.provider(
            rig.payload(
                "finish_effect",
                now_ms=3_020,
                epoch_index=0,
                effect_id=prepared["effect_id"],
                logical_effect_id=prepared["logical_effect_id"],
                attempt_id=prepared["attempt_id"],
                step_id=prepared["step_id"],
                outcome="succeeded",
                result_summary={"verified": True},
            )
        )
        assert finished["effect_state"] == "SUCCEEDED"
        assert rig.store.action_fence_status()["inflight_count"] == 0
    finally:
        rig.close()


def test_m4_applied_reconciliation_promotes_head_to_reconciled_for_checkpoint_projection() -> None:
    rig = CorruptionRig()
    try:
        prepared = rig.provider(
            _effect_payload(rig, "prepare_effect", step=1, attempt=1, now_ms=3_100)
        )
        started = rig.provider(
            rig.payload(
                "start_effect",
                now_ms=3_110,
                epoch_index=0,
                effect_id=prepared["effect_id"],
                logical_effect_id=prepared["logical_effect_id"],
                attempt_id=prepared["attempt_id"],
                step_id=prepared["step_id"],
            )
        )
        assert started["dispatch_permitted"] is True
        rig.provider(
            rig.payload(
                "finish_effect",
                now_ms=3_120,
                epoch_index=0,
                effect_id=prepared["effect_id"],
                logical_effect_id=prepared["logical_effect_id"],
                attempt_id=prepared["attempt_id"],
                step_id=prepared["step_id"],
                outcome="ambiguous",
                error_code="response_lost",
                result_summary={"transport": "lost"},
            )
        )
        assert rig.store.get_effect(prepared["effect_id"]).state == "AMBIGUOUS"
        reconciled = rig.provider(
            rig.payload(
                "reconcile_effect",
                now_ms=3_130,
                epoch_index=0,
                effect_id=prepared["effect_id"],
                logical_effect_id=prepared["logical_effect_id"],
                attempt_id=prepared["attempt_id"],
                step_id=prepared["step_id"],
                verdict="APPLIED",
                evidence={"readback": "postcondition_matched"},
            )
        )
        assert reconciled["logical_committed"] is True
        assert rig.store.get_effect(prepared["effect_id"]).state == "RECONCILED"
        frontier = rig.frontier(version=1, global_step=10)
        assert frontier.pending_effect_ids == ()
        assert frontier.ambiguous_effect_ids == ()
    finally:
        rig.close()
