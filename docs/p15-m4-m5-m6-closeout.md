# P15 M4/M5/M6 — Learning 闭环 / Context 分层 / Temperament 收口记录

日期：2026-08-12
分支：`agent/p15-memory-ssot-life-world-closure-v0.1`

## M4 — Learning Result 回 Memory 闭环

- 新增 [life_learning_memory.py](../../src/life_service/life_learning_memory.py)：
  指数 backoff（60s 起步、2 倍增长、24h 封顶）、有界学习作用域
  （active L3 ≤16、repository ≤8、world ≤4）、Learning Result 的
  LifeEvent/L1/refined-L3 确定性 id。
- [memory_coordinator.py](../../src/life_service/memory_coordinator.py)
  `commit_learning_result`：Result → LifeEvent → L1 audit + 新 L3 refined
  experience（父边=source L3 + L1 audit，evidence roots 全继承）；
  `open_learning`/`record_zero_gain`/`can_open_learning` 账本。
- `activity_scope.build_activity_scope` 新增 `active_l3_refs`（仅 active L3，
  有界）；`learning_executor._source` 优先消费 active_l3_refs
  （生产不再把 recent_memories 不分层全量塞入），legacy 调用兼容。
- 反自证：`fold_independence` 改为“共享任一 lineage root 即同组”
  （I11），Learning refined 与父 L3 永远同组，不会自我加置信度。

## M5 — Context / Recall / Privacy / Injection

- 新增 [memory_context.py](../../src/life_service/memory_context.py)：
  层级权重（L5=2500/L4=2200/L3=1400/L2=700/L1=300）、lineage 连通分量
  去重（每 lineage 一个代表）、principal/privacy/expired/invalidated 过滤、
  指令权威渲染（仅 OPERATING_RULE/USER_PREFERENCE 权威进 INSTRUCTION；
  外部记忆进 DATA；注入标记进 EVIDENCE 且永不成为指令）。
- `select_layered_memories` 直接读 store（active_only + 失效/过期/越权跳过），
  返回 (instruction, data, evidence, skipped) 三个分区。

## M6 — Temperament / Self Cognition

- [temperament.py](../../src/life_service/temperament.py) 新增
  `adapt_from_core_memory`：只接受 evidence refs，每证据有界 delta
  （上限 100 micro），soul-independent 基线不变。
- store schema 15→16：新增 `temperament_adaptation_receipts` 表
  （exactly-once 持久化凭证）。`coordinator.adapt_temperament_from_core`
  只消费 active + `temperament_eligible` 的 L5，每 L5 恰好一次。
- `embedded_runtime._memory_record_turn` 退休 `adapt_from_completed_turn`
  生产接线：普通 turn 永不改长期气质（既有测试同步更新为
  `completed_turn_evidence == 0`）。
- Self Cognition 权威门：用户显式 SELF_IDENTITY 永不进 self-cognition；
  L5 SELF_IDENTITY 只要有 USER_EXPLICIT 父就保持 `self_cognition_eligible=False`。

## 测试与回归

- 新增 P15 测试：M4 4 文件 30 用例、M5 5 文件 52 用例、M6 3 文件 19 用例，
  合计 **101 个新用例**（含 100 次普通 turn 不改长期性格、20 轮 crash、
  30 LifeEvent 分类等）。
- 适配既有测试：schema 16 的三处降级模拟 + M1 store 测试 + 守卫测试 +
  `test_life_temperament`（turn 语义反转）。
- 全量回归：`pytest tests` = **2544 passed / 17 skipped / 0 failed**。

## 关键事实

1. `fold_independence` 按共享 root 连通分量折叠（修正了“根集完全相等”的
   初版实现），保证反复总结/refined 记录永不新增独立证据组。
2. “今天先叫我 A”这类 L4 带 expiry，永不晋升 L5；expiry 判定用显式
   `now_ms`，不读墙钟。
3. 气质只由 active 且 `temperament_eligible` 的 L5 驱动，凭证表保证
   exactly-once；情绪/单轮互动只走 transient affect，不碰长期性格。
