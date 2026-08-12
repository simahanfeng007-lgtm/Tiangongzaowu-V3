# P15 M2/M3 — MemoryCoordinator / L1 / 五层晋升闭环收尾记录

日期：2026-08-12
分支：`agent/p15-memory-ssot-life-world-closure-v0.1`

## M2 — 唯一 MemoryCoordinator / LifeEvent → L1

- 新增 [memory_coordinator.py](../../src/life_service/memory_coordinator.py)：
  唯一生产 Memory 写入口。`commit_life_event_l1`（原子+幂等）、
  `commit_user_explicit`（L4，权威绑定真实 user_message 事件）、
  `commit_contract_assertion`（旧端点适配器，返回三元组不变）、
  `_ensure_l1_derivation`（崩溃恢复幂等）、晋升物化与 `correct_claim`。
- `store.put_live_memory_assertion` 扩展：支持 `expires_at_ms` 与可选
  `derivation`/`activate_head`，断言+protected payload+outbox+派生+head
  在**同一事务**提交；派生自动重绑真实 `memory_assertion_sha256` 与
  `memory_revision`（store 本地受保护 payload id 随机，派生摘要跨库不同，
  但 memory/derivation id 确定）。
- `embedded_runtime._contract_store_assert` 改为经 coordinator 提交；
  `/api/v1/v3/life/memory/{assert,turn,correct}` 路径与响应形状不变。
- 静态守卫验证：生产代码中 `put_memory_derivation`/`put_live_memory_assertion`
  只出现在 `store.py`、`memory_coordinator.py`、`memory_migration.py`（legacy）。

## M3 — L1–L5 状态机 / 晋升 / 显式 / 纠错

- 新增 [memory_promotion.py](../../src/life_service/memory_promotion.py)：
  整数 noisy-or、lineage root 折叠（共享 root 只算一组）、L2/L3/L5 阈值与
  三条 L5 路径（stability / reconfirm / fusion），temporary expiry 禁止 L5。
- 新增 [explicit_memory.py](../../src/life_service/explicit_memory.py)：
  确定性显式意图检测（记住/以后记得/长期保存/不要忘记/长期偏好/以后一直/称呼别名），
  今天/这次/暂时/本轮 expiry 窗口；L4 恒为 `user_asserted`（I14）。
- 新增 [memory_invalidation.py](../../src/life_service/memory_invalidation.py)：
  纠错级联，只有失去全部独立父证据的 descendants 才 `stale`。
- store schema 13→14→15：新增 `memory_derivation_invalidations` 表 +
  `is_derivation_active` / `put_memory_invalidation` / `list_memory_invalidations` /
  `clear_active_head(derivation_id=...)`；active-head 与 active 列表自动排除失效项。

## 测试

- 新增 P15 测试：M2 4 个文件（LifeEvent→L1 30 类事件/20 重复、单写者守卫、
  旧端点适配器、20 轮 crash/retry 原子性）+ M3 6 个文件
  （证据数学/折叠、L4 显式、L5 三条路径、promotion 幂等、并发单胜者、
  纠错级联/双父存活）= **73 个用例**。
- 适配既有测试：v14 降级模拟迁移补摘 invalidation 表（3 处）；M1 store
  测试升级到 schema 15。
- 全量回归：`pytest tests` = **2444 passed / 17 skipped / 0 failed**。

## 关键设计事实（已固化到代码）

1. promotion_key（I08）不含目标 assertion；同一 claim 的不同事件因 lineage
   roots 不同而 key 不同，`UNIQUE(promotion_key)` 保证同一次晋升只成功一次。
2. `SELF_BEHAVIOR_PATTERN` L5 只进 Temperament，不进 Self Cognition
   （8.3 域集为 SELF_IDENTITY/CAPABILITY_SELF/LONG_TERM_GOAL/OPERATING_RULE）。
3. L4 “明确记住”只提升持久化权威，epistemic 保持 `user_asserted`；
   L5 仅在全部父断言 verified/observed 时才 verified。
4. 跨库确定性：memory/derivation id 由事件+策略确定；派生摘要绑定
   store 本地受保护 payload id，同一 store 重放一致（满足 G24 语义）。
