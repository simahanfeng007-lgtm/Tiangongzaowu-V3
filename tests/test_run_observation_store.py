"""RunObservation 隔离 shadow store 回归测试（D-12）。

覆盖：
- 记录完整性：gold_version/candidate/legacy 完整 hash、输出、时延、终端状态
- 隔离性：独立 SQLite 文件，绝不在 gateway.sqlite3 开表
- OBSERVE_ONLY 纪律：模块不 import/持有任何业务 store；append-only；
  数据库触发器拒绝 UPDATE/DELETE
- 完整性校验：record_sha256 篡改检测
- 统计口径：completed pair / incomplete 永久保留计数 / timeout 质量失败计数
"""

from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path

import total_gateway.run_observation as run_observation_module
from total_gateway.run_observation import (
    AppendOnlyViolationError,
    ModelSnapshot,
    ObservationConflictError,
    RunObservationStore,
    build_run_observation,
    derive_cohort_id,
)

_HASHES = {name: ch * 64 for name, ch in (
    ("input", "a"), ("context", "b"), ("registry", "c"),
    ("policy", "d"), ("coverage", "e"), ("router", "f"),
    ("prompt", "1"), ("tool_schema", "2"),
)}


def _snapshot(**overrides) -> ModelSnapshot:
    fields = {
        "provider": "provider-a",
        "model": "model-a",
        "temperature": "0.0",
        "top_p": "1.0",
        "seed_strategy": "fixed",
        "tool_schema_sha256": _HASHES["tool_schema"],
        "timeout_ms": 30_000,
        "retry_limit": 1,
        "fallback": "none",
    }
    fields.update(overrides)
    return ModelSnapshot(**fields)


def _observation(**overrides):
    fields = {
        "scenario_cluster_id": "cluster-1",
        "gold_id": "gold-1",
        "gold_version": 3,
        "input_sha256": _HASHES["input"],
        "context_sha256": _HASHES["context"],
        "memory_revision": 7,
        "registry_sha256": _HASHES["registry"],
        "policy_sha256": _HASHES["policy"],
        "coverage_sha256": _HASHES["coverage"],
        "model_snapshot": _snapshot(),
        "router_code_sha256": _HASHES["router"],
        "prompt_sha256": _HASHES["prompt"],
        "candidate_output": "candidate 输出",
        "legacy_output": "legacy 输出",
        "latency_ms": 42,
        "terminal_state": "completed",
        "created_at_ms": 1_000,
    }
    fields.update(overrides)
    return build_run_observation(**fields)


class _StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "runtime" / "state" / "execution_shadow" / "run-observations.sqlite3"
        self.store = RunObservationStore.open(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()


class TestRecordIntegrity(_StoreCase):
    def test_append_and_get_round_trip(self):
        obs = _observation()
        self.store.append(obs)
        loaded = self.store.get(obs.observation_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.gold_version, 3)
        self.assertEqual(loaded.memory_revision, 7)
        self.assertEqual(loaded.input_sha256, _HASHES["input"])
        self.assertEqual(loaded.context_sha256, _HASHES["context"])
        self.assertEqual(loaded.registry_sha256, _HASHES["registry"])
        self.assertEqual(loaded.policy_sha256, _HASHES["policy"])
        self.assertEqual(loaded.coverage_sha256, _HASHES["coverage"])
        self.assertEqual(loaded.router_code_sha256, _HASHES["router"])
        self.assertEqual(loaded.prompt_sha256, _HASHES["prompt"])
        self.assertEqual(loaded.model_snapshot.tool_schema_sha256, _HASHES["tool_schema"])
        self.assertEqual(loaded.candidate_output, "candidate 输出")
        self.assertEqual(loaded.legacy_output, "legacy 输出")
        self.assertEqual(loaded.latency_ms, 42)
        self.assertEqual(loaded.terminal_state, "completed")
        self.assertTrue(loaded.has_valid_sha256())

    def test_append_rejects_invalid_digest(self):
        obs = _observation().model_copy(update={"latency_ms": 43})
        with self.assertRaises(ValueError):
            self.store.append(obs)

    def test_duplicate_append_conflicts(self):
        obs = _observation()
        self.store.append(obs)
        with self.assertRaises(ObservationConflictError):
            self.store.append(obs)

    def test_cohort_changes_with_any_frozen_field(self):
        base = derive_cohort_id(
            model_snapshot=_snapshot(),
            registry_sha256=_HASHES["registry"],
            policy_sha256=_HASHES["policy"],
            coverage_sha256=_HASHES["coverage"],
            router_code_sha256=_HASHES["router"],
            prompt_sha256=_HASHES["prompt"],
        )
        changed = derive_cohort_id(
            model_snapshot=_snapshot(temperature="0.5"),
            registry_sha256=_HASHES["registry"],
            policy_sha256=_HASHES["policy"],
            coverage_sha256=_HASHES["coverage"],
            router_code_sha256=_HASHES["router"],
            prompt_sha256=_HASHES["prompt"],
        )
        self.assertNotEqual(base, changed)

    def test_terminal_state_discipline(self):
        # completed 必须双侧输出齐全
        with self.assertRaises(ValueError):
            _observation(candidate_output=None)
        # incomplete 必须携带残缺原因
        with self.assertRaises(ValueError):
            _observation(terminal_state="incomplete", candidate_output=None)
        # timeout 不得携带残缺原因
        with self.assertRaises(ValueError):
            _observation(terminal_state="timeout", candidate_output=None,
                         legacy_output=None, incomplete_reasons=("shadow.timeout",))
        ok = _observation(terminal_state="incomplete", candidate_output=None,
                          incomplete_reasons=("shadow.telemetry_missing",))
        self.assertEqual(ok.terminal_state, "incomplete")


class TestIsolationAndObserveOnly(_StoreCase):
    def test_store_file_is_isolated_sqlite(self):
        # 独立文件落盘，且库内只有 run_observation 一张业务表
        self.store.append(_observation())
        self.assertTrue(self.db_path.is_file())
        self.assertEqual(self.store.path, self.db_path)
        self.assertEqual(self.db_path.parent.name, "execution_shadow")
        tables = {
            row[0]
            for row in self.store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertEqual(tables, {"run_observation"})

    def test_module_holds_no_business_store_reference(self):
        # OBSERVE_ONLY 结构断言：模块不得 import 任何权威业务 store
        # （request/effect/task/life/memory），写权威恒为 0 不靠自觉。
        source = Path(run_observation_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = ("total_gateway.store", "total_gateway.effects",
                     "total_gateway.orchestration", "life_service", "v3")
        for prefix in forbidden:
            self.assertFalse(
                any(name == prefix or name.startswith(prefix + ".")
                    or name.endswith("." + prefix.split(".")[-1])
                    for name in imported),
                f"observe-only store must not import {prefix}: {sorted(imported)}",
            )

    def test_append_only_api(self):
        with self.assertRaises(AppendOnlyViolationError):
            self.store.update("anything")
        with self.assertRaises(AppendOnlyViolationError):
            self.store.delete("anything")

    def test_database_triggers_reject_update_and_delete(self):
        obs = _observation()
        self.store.append(obs)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store._conn.execute(
                "UPDATE run_observation SET terminal_state = 'timeout'"
            )
        self.store._conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store._conn.execute("DELETE FROM run_observation")
        self.store._conn.rollback()
        # 原始连接同样被触发器拦截（不依赖封装自觉）
        raw = sqlite3.connect(str(self.db_path))
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                raw.execute("DELETE FROM run_observation")
            raw.rollback()
        finally:
            raw.close()


class TestIntegrityCheck(_StoreCase):
    def test_integrity_check_clean_store(self):
        self.store.append(_observation())
        self.assertEqual(self.store.integrity_check(), ())

    def test_integrity_check_detects_tampering(self):
        obs = _observation()
        self.store.append(obs)
        # 模拟离线篡改：去掉触发器后改写 record_json，再恢复触发器
        raw = sqlite3.connect(str(self.db_path))
        try:
            raw.execute("DROP TRIGGER run_observation_no_update")
            raw.execute(
                "UPDATE run_observation SET record_json = ? WHERE observation_id = ?",
                ('{"forged": true}', obs.observation_id),
            )
            raw.commit()
            raw.execute(
                "CREATE TRIGGER run_observation_no_update "
                "BEFORE UPDATE ON run_observation BEGIN "
                "SELECT RAISE(ABORT, 'run_observation.append_only'); END"
            )
            raw.commit()
        finally:
            raw.close()
        tampered = self.store.integrity_check()
        self.assertIn(obs.observation_id, tampered)


class TestStatistics(_StoreCase):
    def test_pair_and_incomplete_and_timeout_counts(self):
        self.store.append(_observation(created_at_ms=1))
        self.store.append(_observation(created_at_ms=2, terminal_state="timeout",
                                       candidate_output=None, legacy_output=None))
        self.store.append(_observation(created_at_ms=3, terminal_state="incomplete",
                                       candidate_output=None,
                                       incomplete_reasons=("shadow.unpaired",)))
        cohort = _observation(created_at_ms=1).cohort_id
        # 仅 completed 构成完整 pair；timeout 单独计为质量失败；
        # incomplete 永久保留并计数、不进入完整 pair 统计
        self.assertEqual(self.store.complete_pair_count(cohort), 1)
        self.assertEqual(self.store.timeout_observation_count(cohort), 1)
        self.assertEqual(self.store.incomplete_observation_count(cohort), 1)
        self.assertEqual(self.store.incomplete_observation_count(), 1)
        self.assertEqual(
            [o.terminal_state for o in self.store.query_by_cohort(cohort)],
            ["completed", "timeout", "incomplete"],
        )
        self.assertEqual(len(self.store.query_by_cluster("cluster-1")), 3)


if __name__ == "__main__":
    unittest.main()
