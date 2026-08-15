# P18-M1 长程执行链治理工程封板报告

日期：2026-08-15  
分支：`agent/p18-m1-long-execution-chain-governance`  
权威基线：`e24039d96cbe46a23dddb80494e0983a2edeaf77`  
完成代码候选：`567fae3a0c1a5164578349c8d909f227ca8c2801`  
验证工作流：`P18 M1 validation` / run `31855199223`  
阶段结论：**M1 通过，可进入 M2。**

---

## 1. M1 目标与不可破坏约束

本阶段只解决“同一权威任务如何跨越多个局部执行窗口持续推进”的生产问题，不重建 Runtime，不新增第二 Scheduler，不新增第二 Agent Loop，不新增第二 Gateway，不创建第二 Continuity SSoT，也不通过前端重复发请求模拟续跑。

保持唯一生产主链：

`Total Gateway → EmbeddedBackendRuntime / Zongdiaodu → existing model/tool/Omni Body → Fact / Effect / Continuity / Life`

M1 核心语义冻结为：

- Request / Run / Generation / Life 权威身份跨 Epoch 不变；
- Execution Epoch 只是同一 Run 内的有界执行窗口，不是新 Run；
- Epoch 局部预算耗尽属于 `checkpoint_continue`，不是任务终态；
- Global budget、Gateway effect deadline、Authority 失效、用户取消、不可恢复执行失败仍然可以形成终态；
- Canonical checkpoint 未成功时禁止 rollover；
- 已提交 Effect / Fact / Continuity 不因 Epoch 切换丢失、重置或重新授权。

---

## 2. 生产改造结果

### 2.1 Execution Epoch / Dual Budget

`app/backend/tiangong-backend/v3/runtime_turn_orchestration.py`

`TurnLoopState` 在保留旧全局计数的基础上增加：

- `epoch_index`
- `epoch_action_rounds`
- `epoch_iteration_count`

全局计数继续保留：

- `action_rounds`
- `iteration_count`

新增 `decide_schedule()` 三态决策：

- `CONTINUE`
- `CHECKPOINT_CONTINUE`
- `GLOBAL_EXHAUSTED`

新增 `begin_next_epoch()`：只清理局部 Epoch 计数与窗口内 repeat 状态，不重置 Request / Run / Generation / 全局工具进度。

### 2.2 真实生产工具路径接线

`app/backend/tiangong-backend/v3/zongdiaodu.py`

已经移除真实生产路径中两处旧语义：

`turn_loop.can_schedule(...) == false → force_stopped`

并将以下两条路径统一接入 `_simple_chain_prepare_tool_budget()`：

- single tool
- parallel tool batch

默认预算语义：

- Epoch tool-round admission budget：75
- Global tool-round budget：1000
- Epoch loop-turn budget：180
- wall-clock / Gateway effect deadline：继续作为全局硬边界，不因 Epoch rollover 重置

精确边界语义：

- 74 → 可执行第 75 个局部工具轮次；
- 75 → 下一次工具调度触发 checkpoint + rollover，不终止 Run；
- rollover 后全局仍为 75，Epoch 局部回到 0；
- 随后执行得到全局 76 / 新 Epoch 局部 1；
- 999 → 允许最后一步到全局 1000；
- 1000 后再请求新工具轮次 → `GLOBAL_EXHAUSTED`，终态。

Parallel batch 在旧 Epoch 剩余容量不足时，会先完成 checkpoint/rollover，再由新 Epoch 接纳批次；不会因为局部 75 边界直接把任务标成 `force_stopped`。

### 2.3 180-turn 隐藏切断点修复

原 `_SIMPLE_CHAIN_MAX_LOOP_TURNS=180` 使用全局 `iteration_count`，即使工具预算已支持 Epoch，也会在长链中形成第二个旧式全局切断点。

M1 已将此语义改为 **Epoch-local turn/context budget**：

- 局部 turn budget 到达边界 → canonical checkpoint / next Epoch；
- `iteration_count` 全局值持续增长，用于审计与观测；
- `epoch_iteration_count` 才参与局部窗口预算；
- wall clock 和 Gateway effect deadline 仍按整次权威执行全局约束。

因此本阶段不是“把 75 直接改成 1000”，而是建立了真正的局部窗口 + 全局预算双层治理。

---

## 3. Canonical Continuity 接线

### 3.1 不新建第二状态库

M1 没有在 backend 创建 `GatewayStateStore.open()`，没有增加独立 Continuity 数据库，也没有增加第二套 checkpoint 存储权威。

唯一 canonical continuity 仍由：

- `src/total_gateway/continuity.py`
- Total Gateway 已打开的唯一 `GatewayStateStore`

负责。

### 3.2 Gateway → Embedded Backend provider 注入

`src/total_gateway/embedded_backend.py` 增加薄 provider 注入边界：

`set_continuity_checkpoint_provider(...)`

`src/total_gateway/runtime.py` 增加：

`_gateway_execution_epoch_checkpoint(runtime, payload)`

该函数只使用现有 `runtime.store`，先读取当前 active `TaskContinuityCapsule`，再校验：

- request_id
- run_id
- generation
- life_id

四元绑定全部一致后，才调用既有 `persist_working_checkpoint()`。

checkpoint 会继承已有 canonical capsule 的：

- user goal
- hard constraints
- active plan
- verified facts
- artifact refs
- pending effects
- recovery preconditions

因此 Epoch rollover 不会另起一份任务状态，也不会丢掉已有待确认 Effect。

### 3.3 Backend fail-closed 规则

Gateway-authorized production run 可通过 `outer_execution_ticket_id` 识别。

当生产 Run 到达 Epoch 边界时：

1. 先写本地 `checkpoint_requested` 快照；
2. 调用 injected canonical continuity provider；
3. provider 返回结果必须重新绑定同一 request/run/generation/life，并返回 canonical capsule_id；
4. canonical commit 成功后才允许标记 `checkpoint_committed`；
5. 然后 `begin_next_epoch()`；
6. 最后发出 next epoch / run continued 事件。

如果 provider 缺失、异常、身份不匹配或 canonical commit 失败：

- 状态为 `canonical_checkpoint_unavailable` / `canonical_checkpoint_failed`；
- 不执行 Epoch rollover；
- 不把本地 JSON 当作 canonical SSoT 继续跑；
- fail closed。

---

## 4. 事件链

复用现有 `simple_chain_events.py`，未增加第二事件系统。

新增非终态事件：

- `epoch.started`
- `epoch.checkpoint_requested`
- `epoch.checkpoint_committed`
- `epoch.completed`
- `run.continuation_requested`
- `run.continued`

这些事件没有加入 terminal event set。

已有真正终态事件与 Gateway / Authority 终止语义保持不变。

---

## 5. 1000-step / 中断恢复验证

新增 deterministic governance test 覆盖：

- 75-step Epoch rollover；
- 多 Epoch 累积到 1000 global steps；
- 1000 后 global budget terminal；
- 13 次 Epoch checkpoint 后仍保持同一 Request / Run / Generation / Life；
- canonical capsule 链持续 supersede，不另起 SSoT；
- interruption checkpoint 后在同一 canonical chain 上继续；
- pending_effect_ids 跨 checkpoint / interruption / resume 保留；
- wrong generation / wrong life fail closed；
- Gateway-authorized run 缺少 canonical provider 时禁止退化成本地续跑。

同时直接调用真实生产预算桥验证：

- single tool path；
- parallel tool batch path；
- 74 / 75 / 76；
- 999 / 1000；
- canonical checkpoint failure。

说明：1000-step 用例验证的是生产治理状态机与 canonical continuity 边界，不伪造 1000 次外部真实副作用；真实 single/parallel production bridge 另有直接路径测试覆盖。

---

## 6. Source Authority / 架构守卫

永久增加 `.github/workflows/p18-m1-validation.yml`，对 Ubuntu + Windows 同时执行：

- `scripts/check-source-authority.py`
- `scripts/sync-generated-sources.py --check-committed`
- M1 production seams `py_compile`
- focused M1 regression
- inherited P17 orchestration regression
- M1 architecture invariant checks
- full repository pytest

架构守卫确认：

- `zongdiaodu.py` 不再出现生产 `turn_loop.can_schedule(`；
- single / parallel 都进入 dual-budget bridge；
- Epoch turn budget 已进入生产链；
- canonical provider 缺失有 fail-closed 状态；
- Embedded backend 只注入 provider；
- backend 没有新建 GatewayStateStore；
- 没有第二 Runtime / Scheduler / startup path。

P17 的一条旧架构测试曾要求生产函数必须出现 `turn_loop.can_schedule`。该断言与 M1 新语义冲突，已按工程计划升级为要求 `_simple_chain_prepare_tool_budget`，同时明确断言旧 `turn_loop.can_schedule` 不得回归；旧测试没有删除。

---

## 7. 最终验证结果

已验证代码候选：`567fae3a0c1a5164578349c8d909f227ca8c2801`

GitHub Actions run：`31855199223`

### Focused / inherited regression

Ubuntu：

- 93 passed
- 0 failed
- source authority PASS
- generated mirrors PASS
- py_compile PASS
- M1 architecture invariants PASS

Windows：

- 93 passed
- 0 failed
- source authority PASS
- generated mirrors PASS
- py_compile PASS
- M1 architecture invariants PASS

### Full repository pytest

Ubuntu 24.04 / Python 3.12：

- 2915 passed
- 35 skipped
- 807 subtests passed
- 0 failed
- 4 warnings

Windows Server 2025 / Python 3.12：

- 2920 passed
- 30 skipped
- 804 subtests passed
- 0 failed
- 4 warnings

Ubuntu / Windows 的 pass/skip/subtest 数差异来自平台条件测试；两个平台都为 0 failed。

4 个 warning 均为仓库既有 world_understanding Pydantic `schema` 字段 shadow warning，不由 P18-M1 引入，不属于 M1 阻塞缺陷。

---

## 8. 最终代码差异范围

相对权威基线 `e24039d...`，已验证代码候选共 22 个提交，净变更 11 个文件：

- `.github/workflows/p18-m1-validation.yml`
- `app/backend/tiangong-backend/v3/runtime_turn_orchestration.py`
- `app/backend/tiangong-backend/v3/simple_chain_events.py`
- `app/backend/tiangong-backend/v3/zongdiaodu.py`
- `src/total_gateway/embedded_backend.py`
- `src/total_gateway/runtime.py`
- `tests/test_p18_m1_canonical_continuity.py`
- `tests/test_p18_m1_delivery_boundaries.py`
- `tests/test_p18_m1_execution_epoch.py`
- `tests/test_simple_chain_loop_budget.py`
- `tests/test_zongdiaodu_p17_m2_02.py`

没有新增运行时依赖；继续使用仓库既有 `requirements-source.lock`。

历史中存在若干一次性工程 patch carrier / validation carrier 提交，用于在 GitHub Actions 中 fail-closed 地修改和验证生产源文件。成功生产提交已经从最终树删除这些 carrier；它们不属于 Runtime、Scheduler、Gateway、启动入口或发布资产。

---

## 9. 提交记录

1. `88dacea42c969e95815a9189a40919dabe3f4562` — P18-M1: add execution epoch dual-budget state
2. `768df521c2ca723f62bce8da6a7cac72e781f713` — P18-M1: cover execution epoch dual budgets
3. `6c1a26a587fc47bcbe5c60fc051a919e324979ce` — P18-M1: extend simple-chain epoch continuation events
4. `fcbbfc4e822201de4194f39fe6bbcd27a2340ba6` — P18-M1: bootstrap production epoch budget wiring
5. `c285cf4574f66faf04413d1a16cb63a9b891d4e4` — P18-M1: fix epoch budget production patch runner
6. `de5a33586ca0945698b7b18531e083edf9f3f7f8` — P18-M1: make production budget patch boundary-safe
7. `d248da458fc4ddbff807f3c8c0bf069d8aee4ed0` — P18-M1: stage indentation-safe production patch
8. `f01906f65dd3acf362d0d88f629f01606c794bfe` — P18-M1: run indentation-safe production patch
9. `ae543b1041bffaf39c23ff295078d15af34e7aa1` — P18-M1: run budget validation with source runtime lock
10. `7b319c3852c355d08c60bf7f84d8ca77262007c6` — P18-M1: wire epoch continuation into production simple chain
11. `294a44470cc3161f9da43a0dbcef3ea9c811f863` — P18-M1: stage epoch turn-budget production patch
12. `515717a1d4dba7b957779da22ac5bb8993e1bfdb` — P18-M1: validate epoch-local turn budget
13. `b4916a3f792b6cc4d3cd217079ddeee0f6cb6c31` — P18-M1: scope legacy budget-test patch anchor
14. `87fae5249f30978112c954f6ea17bc9850d22b3e` — P18-M1: force-remove one-shot validation carriers
15. `bae96272c77c558e246ed123045c17877a870c9f` — P18-M1: make loop-turn budget epoch-local
16. `0996807922b3927e5d206ee6162bb84328a25f73` — P18-M1: stage canonical continuity bridge patch
17. `b411e3f5e41993bbfe92b575ff7d16bf50d90bc2` — P18-M1: validate canonical continuity bridge
18. `6006c8b539088ade23798f2d280fd43d913a6acd` — P18-M1: use monotonic continuity test timestamp
19. `5dffd8f48c0b1783f0e51db3c1955c7a9fd7b358` — P18-M1: bind execution epochs to canonical continuity
20. `08177ac18267e0411d80bdc0aa0dd0b099e697e8` — P18-M1: add delivery boundary regressions
21. `cde9fd034c6fad971a42777f323b343289374c18` — P18-M1: add cross-platform delivery validation
22. `567fae3a0c1a5164578349c8d909f227ca8c2801` — P18-M1: update inherited budget authority regression

本封板文档提交为第 23 个阶段提交，只改变工程文档，不改变已验证 Runtime 代码候选。

---

## 10. 残余问题分级

### P0

**0 项。**

未发现：

- Epoch 到点误终止；
- 180 turn 旧式全局切断；
- canonical checkpoint 绕过；
- 第二 Continuity store；
- Request / Run / Generation / Life 漂移；
- single / parallel 生产路径旁路；
- Windows/Linux M1 回归失败。

### P1

**0 项 M1 范围内未关闭问题。**

### P2

**0 项 M1 范围内必须带入下一阶段的问题。**

仓库仍有 4 条既有 Pydantic warning，但与本阶段无因果关联，不计为 M1 residual defect。

---

## 11. M2 Admission

**PASS / ALLOW。**

M1 已满足进入 M2 的工程条件：

- 真实生产主链已支持多 Epoch；
- local exhaustion 非终态；
- global / Authority / deadline 边界仍 fail closed；
- canonical continuity 已接回唯一 Gateway SSoT；
- 1000-step governance、interrupt/resume、74/75/76、999/1000、single/parallel 均有回归；
- Source Authority 未破坏；
- Ubuntu / Windows focused + full repository 均 0 failed；
- P0=0，P1=0。

因此后续可以在不重新发明长程执行底座的前提下进入 P18-M2。
