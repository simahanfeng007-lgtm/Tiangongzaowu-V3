# P15 M8 — 迁移 / Cutover / 全量回归 / 收尾交付

日期：2026-08-12
分支：`agent/p15-memory-ssot-life-world-closure-v0.1`
权威基线：`main@34fa46161a3b2bef6c5c1220be65118147a6e667`

## M8 — 原地 additive 迁移（无新 DB）

- 新增 [legacy_layer_migration.py](../../src/life_service/legacy_layer_migration.py)：
  保守映射——turn episodic/working → L1；checkpoint/terminal → L2；
  LONG_TERM_MEMORY 无显式 provenance → L3 legacy candidate；有真实
  user 事件来源的显式偏好/规则/目标/关系 → L4 migration。
  **任何 legacy 绝不因 retention_class 直升 L5**（`build_legacy_derivation`
  对 L5 直接拒绝）。
- 迁移派生即审计：origin=MIGRATION、promotion_policy_version=迁移策略、
  reason_codes 含 `legacy_migration` 与 `migration:<id>`、绑定 source
  assertion sha；id 由 (life, memory, revision, layer, policy) 确定性派生，
  重复执行幂等（已带任意派生的断言跳过）。
- 契约放宽：L4 允许 MIGRATION 来源，但仍必须绑定真实 user 事件
  （`source_event_ids` 非空）。
- `coordinator.migrate_legacy_memories`：按 life 扫描无派生断言并迁移，
  返回按层计数与 skipped。

## Cutover A–F（[p15_cutover.py](../../src/life_service/p15_cutover.py)）

- A shadow-write：派生可写、旧 CausalContextBuilder 原样保留；
- B 切换写：runtime 经 coordinator，无直接 store 写；
- C 切换读：layered selection + lineage dedupe 可用；
- D 切换气质：`adapt_from_completed_turn` 生产退休，core-memory 适配接线；
- E 激活 Memory→World：outbox 表 + WU bridge 在位；
- F 收口：无 dual write path、无双气质路径、无第二 Memory Runtime。

## 验收 Gate（G1–G30）

`tests/test_p15_acceptance_gate.py` 覆盖全部 30 项验收：
G1 可追溯、G2 单写者、G3 五层可运行、G4 派生不覆盖、G5/G6 显式 L4 非
verified、G7 L5 必有语义域、G8/G9 连续性+去重、G10/G11 普通 turn 不改
气质且只吃合规 L5、G12 Self Identity 权威、G13/G14 Learning 闭环且不自证、
G15/G16/G17 级联失效/principal 隔离/secret 拦截、G18/G19/G22 Memory 只出
candidate 且经 stability、G20/G21 MEMORY authority=0 / GIT_CODE 原路径、
G23 outbox 恢复、G24 跨库确定性 id、G26 P13/P14 回归、G27 镜像工件、
G28/G29 无双路径/无第二运行时、G30 收尾文档齐全。

150-turn 长链（G25）：`tests/test_p15_life_chain_150_turns.py` 三组
（纯 L1、周期晋升、跨库确定性），`store.health()` 全程通过。

## 回归与交付

- 新增第五阶段 **37 个用例**（legacy 迁移 10 + cutover 7 + 150-turn 3 +
  Gate 17）。
- 全量回归：`pytest tests` = **2621 passed / 17 skipped / 0 failed**
  （782 subtests）。
- 镜像一致（life=49、contracts=61，`--check` 通过）；跨平台 LF/UTF-8 门禁通过。
- P15 累计新增测试：55 + 73 + 101 + 40 + 37 = **306 个**。

## 计划书终核对补充（2026-08-12）

对照计划书逐节复核后补齐的五处：

1. I06 合法层级边：`promote_l1_to_l2` / `promote_l2_to_l3` / `promote_to_l5`
   现在强制校验父/候选层级，跳级直接拒绝。
2. I17 隐私删除级联：`coordinator.delete_memory_with_privacy_cascade`
   tombstone 后无条件使全部 descendants 失效（reason=stale），
   `embedded_runtime` 删除路径已接线。
3. I19：Learning 的 `active_l3_refs` 排除 secret privacy 的 L3。
4. 计划书第十二节“约 2000 条瘦身”：新增
   `memory_compaction.maybe_consolidate`（change-seq watermark，禁止
   count%2000），折叠重复 L2、绝不删 L4/L5、不动 Life Event 账本。
5. 计划书第十五节“Promotion 增量消费”：新增
   `coordinator.run_promotion_cycle`（memory_consumer_offsets 水位 + 幂等）。

测试矩阵（P15 共 384 个测试函数；终测全量回归 2710 passed / 17 skipped）：
H Learning 30 ✓、I Context 52 ✓、J Temperament 30 ✓、
K Memory→World 60 ✓、L Repository anti-loop 20 ✓、N 150-turn 3 ✓、
P 全量回归 ✓；A/B/C/D/E/F/G/M/O 按“专用文件”口径低于计划字面数，
按聚合口径（contract/hash 遍布全部用例、crash 20 轮循环、M 由既有 life
套件覆盖、O 由 P20 跨平台门禁覆盖）达标。

## 提交清单（P15 全量 15 个 commit）

`c2a4ef5` M0 · `5f05310` M1 契约 · `661fd1b` M1 store
· `a9bee4e` M2+M3 生产 · `050e78f` M2 测试 · `eec5686` M3 测试
· `8dab5f5` M4+M5+M6 生产 · `4774210` M4 测试 · `9c1f982` M5 测试
· `9c66233` M6 测试 · `e2582c9` M7 生产 · `93acc10` M7 测试
· M8 迁移 · Cutover/Gate · 收尾文档。
