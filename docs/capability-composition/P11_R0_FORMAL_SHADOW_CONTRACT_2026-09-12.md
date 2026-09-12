# P11 R0 — Formal Shadow 差分合同与记录样本矩阵

状态：**R0 工程合同已实现；P11 生产切流证据未完成，不得开始 P12。**

基线：`main @ bf29542b3048c8d1806add8063b0db7c72be055b`，即 PR #76
完成 P10 尾项后的主线。工作分支：
`codex/capability-composition-p11-formal-shadow-v1`。

## 阶段审计

开工前重新读取了 v1.2 总计划的 P11、评测矩阵、Fault Injection 和 Shadow
Cutover 条款，并复核当前 HEAD 的 Static Skill、P4/P6 Composition 评测、P7A
Shadow、Gateway 编排及 Source Authority 边界。

仓库中既有以下名称含 `p11` 的文件属于更早的 World Understanding 或 Life
Cutover 编号，不是本阶段 Formal Shadow 证据：

- `tests/test_world_understanding_p11_integration_guards.py`；
- `tests/test_world_understanding_p11_inquiry.py`；
- `tests/test_life_cutover_p11.py`；
- `scripts/life-cutover-p11.py`。

本阶段不得引用这些历史同名文件来声称 P11 已完成。

## 唯一执行路径合同

新增的 `formal_shadow.py` 是无副作用差分评测层，只接收已经产生的证据：

- 当前 Static `SkillSelectionRecord`；
- P4 system-compiled `CapabilityCompositionPlanV1` 与 Validation；
- P7A `ShadowCompositionActivationProposalV1`；
- 已由现有 Gateway / Policy / Ticket / Grant / Runtime / Effect / Fact / P19 /
  CompletionGate 形成的单路径执行观察。

每个 task 固定 `active_path = STATIC | DYNAMIC`，计数只能分别为 `(1, 0)` 或
`(0, 1)`。另一条路径只能保留 Proposal/Plan 观察，且必须满足：

- `proposed_only=true`；
- `authorizes=false`；
- `may_execute=false`；
- Dynamic P7A 输出不得持久化、确认、改风险或产生 PASS；
- Dynamic 真正作为 active path 时，Action 集、风险和 Source Manifest 必须与
  选定的 system-compiled Plan 精确绑定；
- P7A differential trace 中的 legacy Action 集必须等于同一 task 的 Static
  planned Action 集，新增/移除集合由两侧精确差集得到；
- Static 真正作为 active path 时，授权 Action 集必须与已选 Static Skill 候选
  的 required Actions 精确绑定。

本层不导入或构造 `GatewayStateStore`、ExecutionTicket、Capability Grant、
BodyRuntime、P19 执行器或 CompletionGate，也不新增 Runtime、Gateway、Registry、
WorldState 或 Memory Store。

## 正式矩阵合同

R0 固化以下机器可检验结构：

| 矩阵 | 固定要求 |
|---|---|
| Core | 80 个 task；PRIMARY、SECONDARY_A、SECONDARY_B、WEAK 四个 profile 全跑 |
| Long-tail | 120 个 task；PRIMARY 与 WEAK 全跑 |
| Model observations | 共 560 条，profile 必须绑定 provider、model、revision、role 与内容哈希 |
| Fault | 40 个 case；每个至少两个 model profile；覆盖 12 类总计划故障 |
| Execution arms | Static 与 Dynamic 均须有真实 active 样本；每 task 只允许一条执行路径 |

200 个 task 还必须各自绑定不同的输入 SHA-256 和 goal fingerprint，并绑定明确的
acceptance profile SHA-256；只复制同一个 prompt 200 次不能通过矩阵完整性检查。

12 类故障包括 Source revision drift、Tool unavailable、misleading experience、
permission denied、provider unavailable、schema mismatch、stale manifest、workspace
drift、ambiguous Effect、verifier unavailable、context truncation 和 interrupted run。

## Cutover 指标

报告同时计算并哈希以下门槛：

1. Dynamic verified success 不低于 Static；
2. false Completion、A5 bypass、Unauthorized Action、Source drift misuse 均为零；
3. stale/revalidation-required/retired experience 的盲目执行复用为零；
4. restart identity drift 为零；
5. Shadow authorization/execution violation 为零；
6. 全部 Fault 保持 active path 和既有 authority，且被 containment；
7. final parse failure 不高于 5%；
8. weak model 可激活 Plan 成功率不低于 85%，且相对 PRIMARY 差距不超过 15%；
9. Dynamic median context token 严格低于 Static。

这些数值是 P11 R0 的保守机器门槛；它们不能降低总计划的安全不变量。

## 证据分级与防冒充

报告只接受三种证据模式：

- `RECORDED_FIXTURE`：验证协议、合同和实现；
- `LIVE_PROVIDER_REPLAY`：验证精确 provider/model revision 的真实输出，但不代表
  生产执行；
- `PRODUCTION_SHADOW_TRACE`：真实模型输出与现有 Gateway/P19 单路径执行证据完成
  联结，才有资格通过 cutover gate。

记录样本的 provider/model ID 必须以 `recorded.` 开头；非记录模式反过来禁止该
前缀。因此 CI 的记录样本即使所有结构指标为绿，也固定保留：

`cutover_gate_passed=false`、
`cutover_blockers=["p11.independent_production_review.required",
"p11.production_shadow_trace.required"]`。

原始报告禁止自批生产切流：`cutover_gate_passed` 在 producer-controlled report
中固定为 false。未来即使 `PRODUCTION_SHADOW_TRACE` 的结构与数值全部达标，原始
报告仍保留 independent review blocker；必须由未参与该 artifact 生成的复核方
读取精确字节、哈希、HEAD、provider revision 和 Gateway/P19 trace 后另作退出决定。

## R0 记录样本结果

`tests/test_capability_formal_shadow_p11.py` 生成并重放：

- 200 task cases；
- 80 Core / 120 Long-tail；
- 560 model observations；
- 40 fault cases；
- Static active 100 / Dynamic active 100；
- 两臂记录样本 verified success 均为 1000/1000；
- 所有安全、Source、restart、Shadow 和 Fault 违规计数均为零；
- Dynamic median context 低于 Static；
- `formal_gate_passed=true`；
- `cutover_gate_passed=false`，因为尚无生产 Shadow trace，且原始报告不能自批
  independent review。

这些是确定性协议样本，不是任何真实厂商模型或真实生产任务的性能数字。

当前工作树的 focused run 覆盖 P4 parser/compiler/validator、P4 hardening、P6
context evaluation、P7A Shadow、Static Skill selection 和 P11，共 56 passed、
0 failed、5 个既有 Pydantic `schema` 命名 warning。Source Authority 为
17 independent / 1 alias / 24 generated targets / 1 closed-world，官方 committed
mirror 检查通过。记录矩阵 report SHA-256 为
`06f45379afb8e350669fe46c9694feca29471825f74d28e670478c03428c76d6`；它尚未绑定
提交 HEAD，exact-head CI 运行后必须重新生成，不能把该工作树哈希当最终证据。

## R1 退出条件

P11 只有在以下证据全部完成后才能标记完成并进入 P12：

1. 精确 provider、model、revision 的 80×4 与 120×PRIMARY/WEAK 输出；
2. 40 个多模型 Fault case 的真实/受控注入报告；
3. 每 task 一条且仅一条现有 Gateway 执行链的 Effect/Fact/P19/Completion 证据；
4. 报告模式为 `PRODUCTION_SHADOW_TRACE`，内容哈希有效，全部 Cutover 指标通过；
5. 独立复核精确 artifact，并在原始报告之外作出可审计退出决定；
6. exact-head Ubuntu/Windows focused、Architecture、P14、P19 全绿；
7. PR 合并与合并后 main 回归完成。

缺少模型访问、运行授权或生产遥测时保持 pending，不用 fixture、模拟或 skipped
测试替代。
