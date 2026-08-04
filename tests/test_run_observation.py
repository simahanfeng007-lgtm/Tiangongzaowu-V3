"""G4 路由影子 RunObservation 隔离存储测试（草案 §5.2）。

覆盖：写入/查询/统计、append-only 拒绝 UPDATE/DELETE、不完整 observation
永久保留且不计入完整 pair、record_sha256 篡改检测、cohort 变化隔离。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from contracts import canonical_json_bytes
from total_gateway.run_observation import (
    AppendOnlyViolationError,
    ModelSnapshot,
    ObservationConflictError,
    RunObservation,
    RunObservationStore,
    build_run_observation,
)
from total_gateway.shadow_api import (
    RUN_OBSERVATION_PATH,
    RUN_OBSERVATION_STATS_PATH,
    ShadowApiError,
    ShadowApiRouter,
)


TOKEN = "shadow-token-" + "b" * 40


def _snapshot(**overrides: object) -> ModelSnapshot:
    base = {
        "provider": "provider-a",
        "model": "model-x",
        "temperature": "0.7",
        "top_p": "1.0",
        "seed_strategy": "fixed:42",
        "tool_schema_sha256": "a" * 64,
        "timeout_ms": 30_000,
        "retry_limit": 2,
        "fallback": "none",
    }
    base.update(overrides)
    return ModelSnapshot(**base)


def _observation(
    *,
    snapshot: ModelSnapshot | None = None,
    created_at_ms: int = 1_000,
    terminal_state: str = "completed",
    candidate_output: str | None = "候选输出",
    legacy_output: str | None = "旧版输出",
    incomplete_reasons: tuple[str, ...] = (),
    gold_id: str = "gold-1",
    scenario_cluster_id: str = "cluster-1",
) -> RunObservation:
    return build_run_observation(
        scenario_cluster_id=scenario_cluster_id,
        gold_id=gold_id,
        gold_version=1,
        input_sha256="b" * 64,
        context_sha256="c" * 64,
        memory_revision=7,
        registry_sha256="d" * 64,
        policy_sha256="e" * 64,
        coverage_sha256="f" * 64,
        model_snapshot=snapshot or _snapshot(),
        router_code_sha256="1" * 64,
        prompt_sha256="2" * 64,
        candidate_output=candidate_output,
        legacy_output=legacy_output,
        latency_ms=120,
        terminal_state=terminal_state,
        incomplete_reasons=incomplete_reasons,
        created_at_ms=created_at_ms,
    )


@pytest.fixture()
def store(tmp_path):
    store = RunObservationStore.open(tmp_path / "shadow" / "run-observations.sqlite3")
    yield store
    store.close()


# ---- 写入 + 查询 + 统计 ----


def test_append_query_and_stats(store: RunObservationStore) -> None:
    completed = _observation(created_at_ms=1_000)
    incomplete = _observation(
        created_at_ms=2_000,
        terminal_state="incomplete",
        candidate_output=None,
        incomplete_reasons=("telemetry_missing",),
    )
    timeout = _observation(created_at_ms=3_000, terminal_state="timeout", candidate_output=None)
    store.append(completed)
    store.append(incomplete)
    store.append(timeout)

    cohort_id = completed.cohort_id
    assert store.get(completed.observation_id) == completed
    assert {item.observation_id for item in store.query_by_cohort(cohort_id)} == {
        completed.observation_id,
        incomplete.observation_id,
        timeout.observation_id,
    }
    assert {item.observation_id for item in store.query_by_cluster("cluster-1")} == {
        completed.observation_id,
        incomplete.observation_id,
        timeout.observation_id,
    }
    assert store.complete_pair_count(cohort_id) == 1
    assert store.incomplete_observation_count(cohort_id) == 1
    assert store.incomplete_observation_count() == 1
    assert store.timeout_observation_count(cohort_id) == 1
    assert store.integrity_check() == ()


def test_append_rejects_duplicate_and_bad_digest(store: RunObservationStore) -> None:
    obs = _observation()
    store.append(obs)
    with pytest.raises(ObservationConflictError):
        store.append(obs)
    forged = obs.model_copy(update={"record_sha256": "0" * 64})
    with pytest.raises(ValueError):
        store.append(forged)


# ---- append-only：UPDATE/DELETE 拒绝 ----


def test_append_only_rejects_update_and_delete(store: RunObservationStore) -> None:
    obs = _observation()
    store.append(obs)
    with pytest.raises(AppendOnlyViolationError):
        store.update(obs)
    with pytest.raises(AppendOnlyViolationError):
        store.delete(obs.observation_id)
    # 数据库层触发器同样拒绝（不依赖 Python 层自觉）。
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "UPDATE run_observation SET terminal_state = 'incomplete'"
            " WHERE observation_id = ?",
            (obs.observation_id,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "DELETE FROM run_observation WHERE observation_id = ?",
            (obs.observation_id,),
        )
    store._conn.rollback()
    assert store.get(obs.observation_id) == obs


# ---- 不完整 observation 永久保留且不计入完整 pair ----


def test_incomplete_observation_is_permanent_and_excluded(
    store: RunObservationStore,
) -> None:
    incomplete = _observation(
        terminal_state="incomplete",
        candidate_output=None,
        legacy_output=None,
        incomplete_reasons=("hash_mismatch", "terminal_envelope_incomplete"),
    )
    store.append(incomplete)
    cohort_id = incomplete.cohort_id
    assert store.complete_pair_count(cohort_id) == 0
    assert store.incomplete_observation_count(cohort_id) == 1
    # 永久保留：任何删除路径都被拒绝，记录仍在审计全集。
    with pytest.raises(AppendOnlyViolationError):
        store.delete(incomplete.observation_id)
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("DELETE FROM run_observation")
    store._conn.rollback()
    assert store.get(incomplete.observation_id) == incomplete
    assert store.incomplete_observation_count(cohort_id) == 1


# ---- record_sha256 篡改检测 ----


def test_record_sha256_tamper_detection(store: RunObservationStore) -> None:
    obs = _observation()
    store.append(obs)
    assert store.integrity_check() == ()
    # 模拟绕过触发器的文件级篡改（攻击者先卸触发器再改行）。
    store._conn.execute("DROP TRIGGER run_observation_no_update")
    payload = json.loads(
        store._conn.execute(
            "SELECT record_json FROM run_observation WHERE observation_id = ?",
            (obs.observation_id,),
        ).fetchone()[0]
    )
    payload["legacy_output"] = "被篡改的输出"
    store._conn.execute(
        "UPDATE run_observation SET record_json = ? WHERE observation_id = ?",
        (json.dumps(payload, ensure_ascii=False), obs.observation_id),
    )
    store._conn.commit()
    assert store.integrity_check() == (obs.observation_id,)


# ---- cohort 隔离：model_snapshot 任一字段变化即新 cohort ----


def test_cohort_change_isolates_queries(store: RunObservationStore) -> None:
    base = _observation(created_at_ms=1_000)
    store.append(base)
    for index, override in enumerate(
        [
            {"temperature": "0.8"},
            {"top_p": "0.9"},
            {"seed_strategy": "none"},
            {"model": "model-y"},
            {"retry_limit": 3},
        ],
        start=2,
    ):
        changed = _observation(
            snapshot=_snapshot(**override),
            created_at_ms=index * 1_000,
        )
        assert changed.cohort_id != base.cohort_id
        store.append(changed)

    # pair_key 跨 cohort 对齐（同 gold 场景），cohort 查询互相隔离。
    base_cohort_items = store.query_by_cohort(base.cohort_id)
    assert [item.observation_id for item in base_cohort_items] == [base.observation_id]
    assert store.complete_pair_count(base.cohort_id) == 1
    all_items = store.query_by_cluster("cluster-1")
    assert len(all_items) == 6
    changed_cohorts = {item.cohort_id for item in all_items} - {base.cohort_id}
    assert len(changed_cohorts) == 5
    for cohort_id in changed_cohorts:
        items = store.query_by_cohort(cohort_id)
        assert len(items) == 1
        assert items[0].pair_key == base.pair_key


# ---- HTTP 面：POST /api/v1/shadow/observations 与 GET stats ----


def _router(store: RunObservationStore) -> ShadowApiRouter:
    # ShadowApiRouter 首个参数是网关主库；本测试不触碰它，传 None 占位。
    return ShadowApiRouter(None, TOKEN, observation_store=store)  # type: ignore[arg-type]


def test_api_post_and_stats(store: RunObservationStore) -> None:
    router = _router(store)
    obs = _observation()
    body = canonical_json_bytes(obs.model_dump(mode="json"))
    response = router.dispatch(
        "POST",
        RUN_OBSERVATION_PATH,
        {"Content-Type": "application/json"},
        body,
        now_ms=5_000,
    )
    assert response.status == 200
    assert response.payload["observation_id"] == obs.observation_id
    assert response.payload["request_created"] is False
    assert response.payload["effects_permitted"] is False

    stats = router.dispatch(
        "GET",
        f"{RUN_OBSERVATION_STATS_PATH}?cohort_id={obs.cohort_id}",
        {},
        b"",
        now_ms=5_001,
    )
    assert stats.status == 200
    assert stats.payload["complete_pair_count"] == 1
    assert stats.payload["incomplete_observation_count"] == 0
    assert stats.payload["timeout_observation_count"] == 0


def test_api_rejects_browser_origin_and_bad_digest(store: RunObservationStore) -> None:
    router = _router(store)
    obs = _observation()
    body = canonical_json_bytes(obs.model_dump(mode="json"))
    with pytest.raises(ShadowApiError) as origin_error:
        router.dispatch(
            "POST",
            RUN_OBSERVATION_PATH,
            {"Content-Type": "application/json", "Origin": "https://evil.example"},
            body,
        )
    assert origin_error.value.status == 403

    forged = obs.model_copy(update={"latency_ms": obs.latency_ms + 1})
    forged_body = canonical_json_bytes(forged.model_dump(mode="json"))
    with pytest.raises(ShadowApiError) as digest_error:
        router.dispatch(
            "POST",
            RUN_OBSERVATION_PATH,
            {"Content-Type": "application/json"},
            forged_body,
        )
    assert digest_error.value.status == 400
    assert digest_error.value.reason_code == "shadow_api.observation.digest_invalid"


def test_api_observation_store_isolated_from_gateway_tables(store: RunObservationStore) -> None:
    # 隔离声明：独立 SQLite 文件中只有 run_observation 一张业务表。
    tables = {
        row[0]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert tables == {"run_observation"}
