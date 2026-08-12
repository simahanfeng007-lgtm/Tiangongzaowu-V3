# P15 M0 — 基线冻结记录

日期：2026-08-12
分支：`agent/p15-memory-ssot-life-world-closure-v0.1`

## 权威基线

- `main@34fa46161a3b2bef6c5c1220be65118147a6e667`
- 工作区在建分支前为干净状态（`git status` 无改动）。
- 与计划书声明的一致（P15-FINAL 权威基线）。

## 基线测试（M0 重点清单，建分支后、写代码前实测）

结果：**340 passed / 1 skipped**（24s，6 组 subtests）。

覆盖：`test_life_event_ingress`、`test_causal_memory_contracts`、
`test_causal_memory_store`、`test_memory_contract_convergence`、
`test_memory_lifecycle_fusion`、`test_life_embedded_lifecycle`、
`test_life_temperament`、`test_learning_executor`、`test_life_learning_workflow`、
`test_world_cognition_*`、`test_world_understanding_p13_*`、
`test_life_repository_bridge_p14`、`test_repository_*p14*`。

既有 warning（非 P15 引入）：`WorldContractModel` 子类 `schema` 字段遮蔽告警。

## M1 落地后的完整回归

`pytest tests`：**2381 passed / 17 skipped / 0 failed**（782 subtests）。

新增 P15 测试：55 个（M0 架构守卫 9 + M1 契约 18 + M1 store 17 + M1 scope 隔离 11）。

## 为适配 schema v13→v14 修订的既有测试

- `tests/test_causal_memory_store.py`（v2→v3 降级模拟补摘 P14 四表）
- `tests/test_atomic_context_p10.py`（v6→v7 降级模拟补摘 P14 四表）
- `tests/test_affect_external_intake.py`（v3→v4 降级模拟补摘 P14 四表）

这三处是旧版本模拟器（手工摘表 + 改 user_version/metadata），与新增表无关的
既有行为未被修改；M0 退出条件“没有把既有错误误判为 P15 引入”成立。

## 对计划书的两处事实修正（M0 核对结论）

1. 第十二节“每约 2000 条瘦身”在基线中不存在既有实现（全库与镜像均无
   `current_memory_change_seq` / `last_compaction_seq` / 2000 触发逻辑）；
   该机制应视为 P15 **新增**能力，第十五节已明确用 watermark 而非 `count % 2000`。
2. `src/contracts` 与 `src/life_service` 的运行时镜像不在 `readable-python-source`
   下；按 `source-ownership.json` 实际目标是
   `app/life-service/runtime314/*`（已跟踪）与
   `app/runtime/python312/Lib/site-packages/*`（构建期生成、git-ignored）。

## M1 决策记录

- `contracts/schema.py` 的 schema bundle **本轮不纳入新契约**：
  `REVIEWED_SCHEMA_BASELINE_SHA256` 是已评审基线（`test_contract_artifacts.py`
  钉死），纳入 bundle 会破坏 M1“不改变现有生产行为”的退出条件；
  新契约已从 `contracts` 包导出，bundle 评审留到收尾阶段。
- `src/life_service/__init__.py` 无需修改：派生能力是
  `LifeShadowStore` 实例方法，不新增包级导出。
- active-head 唯一键按计划书 I15：`(life_id, principal_ref, claim_key, layer)`，
  不含 privacy（同一 claim 的隐私不同版本会替换 head，历史保留在 derivation 表）。
