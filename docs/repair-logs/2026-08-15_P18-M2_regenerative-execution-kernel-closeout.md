# P18-M2 可再生执行内核封板记录

日期：2026-08-15  
分支：`agent/p18-m2-regenerative-execution-kernel`  
M1 基线：`8dd94f9da744405e81c1a3ef31e218847eaf4d3e`  
最终四门验收代码/干净树候选：`372495c9dad73a4dfb3911f92f883216cff9ada4`  
永久四门验收 Run：`31860978753`  
状态：**M2 CLOSED — M3 ADMISSION PASS**

> `372495c9...` 是实际经过 Ubuntu/Windows focused + full repository 四门验证的代码树。本文档封板提交发生在四门之后，仅改变本 closeout 文档，不改变 Runtime、Gateway、Store、Effect、Continuity、Completion 或启动链。

## 1. 四门最终结果

1. **Ubuntu focused M2 + inherited regressions：SUCCESS**  
   - `119 passed / 0 failed / 2 warnings`
   - Source Authority：`16 independent authorities / 1 alias / 24 generated targets / 1 closed-world boundary`，PASS
   - M2 architecture invariants：PASS

2. **Windows focused M2 + inherited regressions：SUCCESS**  
   - `119 passed / 0 failed / 2 warnings`
   - Source Authority：`16 independent authorities / 1 alias / 24 generated targets / 1 closed-world boundary`，PASS
   - M2 architecture invariants：PASS

3. **Ubuntu full repository pytest：SUCCESS**  
   - `2944 passed / 35 skipped / 4 warnings / 807 subtests passed / 0 failed`

4. **Windows full repository pytest：SUCCESS**  
   - `2949 passed / 30 skipped / 4 warnings / 804 subtests passed / 0 failed`

四门：**4 / 4 SUCCESS**。

现存 4 条 full-suite warning 均来自既有 `world_understanding` contracts 的 `schema` 字段 shadow warning；M2 未新增该类 warning，未形成 M2 correctness blocker。

## 2. M2 → M3 八项准入

- **300-Step：PASS** — 300 个 execution events 单调写入、hash-chained，重复 event key 保持幂等。
- **5+ Epoch：PASS** — 300-step deterministic fixture 覆盖 6 个 Epoch。
- **Durable Ledger：PASS** — append-only ledger、monotonic sequence、event hash integrity、torn-tail detection/recovery 已验证。
- **Durable Checkpoint：PASS** — structured checkpoint checksum/hash、current/previous known-good、continuity capsule reference、bounded frontier 已验证。
- **Crash Resume：PASS** — Store reopen 后从 latest valid checkpoint rehydrate，并 replay ledger tail；PREPARED / STARTED / terminal 跨表崩溃窗口均有恢复矩阵。
- **Transactional Effect：PASS** — durable prepare/start 先于 physical handler；logical effect 与 physical attempt 分离；committed/in-flight/ambiguous 均禁止盲目重复副作用；reconciliation 生效。
- **False Completion Rejection：PASS** — pending obligation、未满足 reality/critical-fact 条件会拒绝完成；clean frontier 才生成 completion proof / `chain.completed`。
- **Existing Regression：PASS** — Ubuntu/Windows 两套完整仓库 pytest 均 0 failed。

结论：P18 v2.1 定义的 **M2 → M3 全部准入项 PASS**。

## 3. M2 二十项硬验收

1. 300-step deterministic task：PASS。
2. 至少 5 Epoch：PASS，实际 6 Epoch。
3. Request / Run / Generation 不变：PASS，checkpoint/restart assertions 保持同一 identity。
4. Epoch budget 耗尽不 terminal：PASS，继承 M1 dual-budget/checkpoint-continue regressions。
5. Context size 有界：PASS，bounded live working-set / history 投影生效。
6. Run snapshot 大小有界：PASS，Frontier payload bounded，300-step fixture 仍低于硬上限。
7. Ledger 可 replay：PASS。
8. Ledger seq 单调且无并发静默重复：PASS，event idempotency + CAS/revision guard。
9. torn Ledger tail 可发现并恢复：PASS。
10. Checkpoint checksum/hash 生效：PASS。
11. current checkpoint 损坏可回退 previous known-good：PASS。
12. 进程/Store 重启恢复 latest safe execution state：PASS。
13. PREPARED 未 durable 不允许 physical dispatch：PASS，生产 wrapper 顺序锁定为 prepare → start → handler → finish。
14. COMMITTED logical_effect_id 不重复：PASS，handler 不再执行。
15. AMBIGUOUS 先 reconcile：PASS；in-flight 同样 hard-block 新 physical attempt。
16. false completion 必须拒绝：PASS。
17. stale/false critical fact 的 Checkpoint Reality Audit：PASS。
18. 并发 revision conflict 不得静默覆盖：PASS，Frontier CAS 冲突显式失败。
19. Task Contract 同 Generation 内不可改变：PASS，immutable binding regression。
20. 原测试全部通过：PASS，Ubuntu/Windows full suite 均 0 failed。

**20 / 20 PASS**。

## 4. 生产执行链变化

M2 将原有限 simple-chain 接入现有权威主链中的 regenerative execution boundary：

`Gateway existing authority/store`
→ `RegenerativeExecutionAuthority(runtime.store)`
→ `ExecutionFrontier / Execution Ledger / Regenerative Checkpoint`
→ `Simple Chain single/parallel tool step`
→ `Durable PREPARED/STARTED`
→ `physical tool handler`
→ `terminal Effect fact`
→ `Frontier / checkpoint / Completion Proof`
→ `same Run regeneration`

Epoch rollover 前必须先完成 regenerative checkpoint；旧的直接 `_jineng_zhixing` production dispatch seam 已被永久架构测试禁止回流。

## 5. 数据契约变化

M2 在既有 `GatewayStateStore` schema v21 内扩展：

- `ExecutionFrontier`
- append-only Execution Ledger event contract
- Regenerative Checkpoint（current + previous known-good）
- immutable execution task-contract binding
- logical effect / physical attempt / step identity linkage
- crash-recovery / reconciliation projection
- completion-proof supporting state

没有建立第二个 Continuity Capsule；结构化 regenerative checkpoint 引用既有 canonical continuity authority。

## 6. Authority / SSoT 结论

**Authority unchanged。**

- Single Gateway：保持。
- Single Runtime：保持。
- Single Scheduler Authority：保持。
- Single GatewayStateStore：保持；provider/runtime boundary 禁止自行 `GatewayStateStore.open`。
- Gate / Ticket / A5：未绕过。
- Request / Run / Generation / Life identity：不因 Epoch 或 crash resume 重建第二套身份。
- Frontend：未成为 execution owner。
- Continuity：仍由现有 canonical continuity SSoT 负责。

永久 CI 已显式验证 `RegenerativeExecutionAuthority(runtime.store)`，并拒绝 provider/backend boundary 新建 Store。

## 7. Transactional Effect / Crash Recovery 结论

已验证以下窗口：

- CLAIMED/PREPARED 后、dispatch 前崩溃；
- Effect STARTED 已 durable、`step.dispatched` 未落盘；
- Effect SUCCEEDED 已落盘、`step.committed` 未落盘；
- Effect AMBIGUOUS 已落盘、`step.ambiguous` 未落盘；
- Effect FAILED_FINAL 已落盘、`step.failed` 未落盘；
- handler timeout / unresolved in-flight；
- committed logical effect 再次遇到同一 logical id。

恢复从 Durable State / canonical Effect Ledger 出发，不从模型“记忆中的最后一步”恢复；不确定副作用进入 reconciliation，而不是盲重试。

## 8. Final tree hygiene

相对 M1 基线，最终 M2 生产范围只保留：

- 永久 M2 validation workflow；
- regenerative execution / provider / store / runtime / embedded-backend 生产实现；
- `zongdiaodu` 主链接线；
- Source Authority 更新；
- M2/M1 inherited regression tests；
- migration fixture compatibility；
- 本 closeout。

一次性 patch workflow、临时 apply script、Windows failure diagnostic 均已从最终树删除。

## 9. 风险分级

- **P0：0 open**。
- **P1：0 blocking M3**。
- **P2：既有 4 条 `world_understanding` Pydantic `schema` shadow warning；非 M2 新增，不影响 M2→M3 准入，可作为后续代码卫生项独立处理。**

## 10. 阶段结论

P18 v2.1 规定 M2 → M3 必须通过：300-Step、5+ Epoch、Durable Ledger、Durable Checkpoint、Crash Resume、Transactional Effect、False Completion Rejection、Existing Regression。

上述项目已全部通过，M2 硬验收 20 / 20 PASS，最终干净树永久四门 4 / 4 SUCCESS，且无 P0。

**最终结论：M2 正式 CLOSED。**  
**M3 ADMISSION：PASS。**  
**允许从本阶段封板 HEAD 创建 M3 开发分支，进入 Adaptive Control + Semantic Drift Detection + Resource Governor + Poisoning Defense。**
