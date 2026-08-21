from __future__ import annotations

import tempfile
from pathlib import Path

from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.episode_builder import build_prediction


def runtime(root: Path) -> EmbeddedLifeRuntime:
    life = EmbeddedLifeRuntime(
        data_root=root / "life-data",
        runtime_root=root / "runtime",
        mode="embedded",
    )
    life.scheduler.stop(timeout_seconds=2)
    return life


def test_zero_sub_budget_is_exhausted_before_first_call() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            scope = life._scope_state()
            scheduler = scope["scheduler"]
            settings = scope["settings"]
            settings["llm_daily_budget"] = 0
            settings["llm_daily_attempt_budget"] = 0
            settings["learning_llm_daily_budget"] = 0
            settings["learning_llm_daily_attempt_budget"] = 0
            assert life._sub_model_budget_exhausted(
                scheduler, settings=settings, pool="learning"
            ) is True
            assert life._reserve_sub_model_call_locked(
                scheduler=scheduler, settings=settings, pool="learning"
            ) is False
        finally:
            life.close()


def test_evicted_open_episode_closes_from_store_ssot() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            with life._lock:
                for index in range(33):
                    life._open_runtime_episode_locked(
                        life_id=life_id,
                        source="autonomy",
                        ref_id=f"task_qc_{index:02d}",
                        event_kind="autonomy.task.attempt.started",
                        intention=f"QC task {index}",
                        context_sha256=(f"{index + 1:064x}"[-64:]),
                        prediction=build_prediction(
                            basis_inputs={"source": "autonomy", "task_id": f"task_qc_{index:02d}"}
                        ),
                        action_risk="A0",
                    )
            registry = life._scope_state(life_id)["scheduler"]["open_episodes"]
            assert len(registry) == 32
            assert all(row["ref"] != "task_qc_00" for row in registry)
            assert len(life._contract_store().open_causal_episodes(life_id, limit=64)) == 33
            with life._lock:
                life._abort_runtime_episode_locked(
                    life_id=life_id,
                    source="autonomy",
                    ref_id="task_qc_00",
                    reason="qc_eviction_recovery",
                )
            opens = life._contract_store().open_causal_episodes(life_id, limit=64)
            assert len(opens) == 32
            assert all(episode.episode_id for episode in opens)
        finally:
            life.close()


def test_registry_loss_reconciles_all_open_episodes_from_store() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            with life._lock:
                for index in range(3):
                    life._open_runtime_episode_locked(
                        life_id=life_id,
                        source="autonomy",
                        ref_id=f"task_restart_{index}",
                        event_kind="autonomy.task.attempt.started",
                        intention=f"restart task {index}",
                        context_sha256=(f"{index + 10:064x}"[-64:]),
                        prediction=build_prediction(
                            basis_inputs={"source": "autonomy", "task_id": f"task_restart_{index}"}
                        ),
                        action_risk="A0",
                    )
                life._scope_state(life_id)["scheduler"]["open_episodes"] = []
                recovered = life._reconcile_open_reflection_episodes(life_id)
            assert recovered == 3
            assert life._contract_store().open_causal_episodes(life_id, limit=64) == ()
            cards = life._contract_store().list_reflection_cards(life_id, limit=10)
            assert len(cards) == 3
            assert all("restart_recovery" in card.observed_outcome for card in cards)
        finally:
            life.close()


def test_idle_sort_is_explicit_before_composite_score() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/life_service/embedded_runtime.py").read_text(encoding="utf-8")
    sort_block = source[source.index("        rows.sort(\n            key=lambda item: (\n", source.index("def _capability_overlay_payload")):]
    sort_block = sort_block[:800]
    assert sort_block.index('int(bool(item.get("idle")))') < sort_block.index('composite_score_milli')


def test_capability_patch_timeout_is_accounted_as_timeout() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/life_service/embedded_runtime.py").read_text(encoding="utf-8")
    start = source.index("decision = self._capability_patch_decider(material)")
    block = source[start:start + 900]
    assert "except Exception as exc:" in block
    assert "timeout=isinstance(exc, TimeoutError)" in block



def test_reconcile_does_not_count_failed_abort_as_recovered() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        life = runtime(Path(temporary))
        try:
            life_id = str(life._active()["life_id"])
            with life._lock:
                life._open_runtime_episode_locked(
                    life_id=life_id,
                    source="autonomy",
                    ref_id="task_failed_recovery_metric",
                    event_kind="autonomy.task.attempt.started",
                    intention="verify exact recovery accounting",
                    context_sha256="a" * 64,
                    prediction=build_prediction(
                        basis_inputs={
                            "source": "autonomy",
                            "task_id": "task_failed_recovery_metric",
                        }
                    ),
                    action_risk="A0",
                )
                life._scope_state(life_id)["scheduler"]["open_episodes"] = []
                original_abort = life._abort_runtime_episode_locked
                life._abort_runtime_episode_locked = lambda **_kwargs: None
                try:
                    recovered = life._reconcile_open_reflection_episodes(life_id)
                finally:
                    life._abort_runtime_episode_locked = original_abort
            assert recovered == 0
            opens = life._contract_store().open_causal_episodes(life_id, limit=8)
            assert len(opens) == 1
            assert life._contract_store().is_causal_episode_open(
                life_id, opens[0].episode_id
            ) is True
        finally:
            life.close()
