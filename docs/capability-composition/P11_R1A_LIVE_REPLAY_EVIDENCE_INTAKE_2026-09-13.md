# P11 R1A — 真实模型回放证据接入

状态：**只读证据接入工程实现与精确提交门禁已完成；真实模型矩阵、生产 Gateway/P19 联结与独立复核仍待完成。**

基线为 P11 R0 远端精确 HEAD
`a421c1ff347f57f3a42cb9630252c6bfca574d1b`。R0 的 13/13 个精确提交工作和
跨平台记录 artifact 已通过，但它只证明记录合同。R1A 不改变
`cutover_gate_passed=false`，也不授权 P12 开工。

## 审计修复

R0 观察模型最初把 `goal_fingerprint_sha256` 为空等同于 Plan 失败，同时 task
合同要求每条动态观察的 goal 与 task goal 相同。两条约束叠加后，合法 200-task
矩阵无法包含 parse/plan failure，`final_parse_failure_milli <= 50` 门槛实际上不可
触达。

R1A 将 goal 改为每条观察的必备 task 身份；parse/plan 失败保留原 goal，并用
`plan_succeeded`、错误码、空 Action 集和空 Plan 绑定表达失败。新增测试证明：

- 560 条模型观察中的 1 条 parse failure 可形成合法报告并通过 5% 门槛；
- 560 条中的 29 条失败为 51 milli，必须阻断 Formal Gate；
- 没有 Plan 的 observer 若不显式提供 goal，必须 fail closed。

## 只读回放链

新增 `p11_live_replay_bridge.py`，复用现有 append-only `RunObservation`，不另建
模型调用或执行体系。每条接入证据同时绑定：

1. task ID 与输入 SHA-256；
2. Static path 的规范字节与输出 SHA-256；
3. provider、model 与 revision；既有 observation 中的 model 字段固定采用
   `<model_id>@<model_revision>`；
4. candidate snapshot 与原始模型输出；
5. 至多一次 repair observation，且必须保持 cohort、pair、Static 输出和时间顺序；
6. 严格解析得到的 Proposal 与 system-compiled Plan；编译结果必须证明来自同一
   Proposal，而不能只匹配 goal；
7. 最终 Dynamic observation 与整个 live replay binding 的内容哈希。

非记录报告不能只填一个 64 位 binding 字符串。报告构建器要求提供与全部动态观察
一一对应的完整 binding 对象，逐项验证其内容哈希、task、model profile 和 Plan，
缺项、重复、额外对象或观察不一致均拒绝。

候选原文缺失、repair 原文缺失、非 completed pair、record hash 损坏、模型
revision 漂移、Static 对照漂移、跨 pair repair、无 Proposal 的 Plan，以及由
另一份 Proposal 编译的 Plan 均 fail closed。未修复的 parse failure 仍绑定 task
goal，不能从正式矩阵中消失。

## Authority 边界

接入层不拥有或构造 provider client、`RunObservationStore`、Gateway Store、Policy、
Ticket、Grant、Runtime、Verifier 或 Completion authority，不写业务 Store，也不
执行 Static/Dynamic 任一路径。它只读取并验证已经存在的 append-only observation。

因此 `LIVE_PROVIDER_REPLAY` 只能证明真实模型原文与 Proposal/Plan 的可追溯关系。
只有后续把同一 task、input、goal、Plan 和 selected active path 与现有
Gateway/Effect/Fact/P19/Completion trace 精确联结，报告才有资格进入
`PRODUCTION_SHADOW_TRACE` 检查；即使结构和指标通过，producer 原始报告仍不得
自行批准切流。

## 当前验证与未结项

本候选 focused 范围覆盖 P4 parser/compiler/validator、P4 hardening、P6、P7A、
Static Skill selection、P11 Formal Shadow 和 R1A bridge。R1A 远端源码冻结点为
`318b8bd7fc43c2c27e02ba202cdb3cb4287426c2`，tree 为
`7a3423999a051cf550bdb5b3344939f574ade1d0`；本地候选 tree 与其完全一致。

该精确提交的 Architecture、P11 focused、P14、P19 共 13/13 个工作成功。P11
Ubuntu/Windows 各为 71 passed、0 failed、0 skipped；Source Authority 为
17 independent / 1 alias / 24 generated targets / 1 closed-world，committed mirror
检查通过。两平台 `identity.json` 与 `recorded-matrix.json` 分别逐字节一致：

- `identity.json` SHA-256：
  `4aa7505fe029a3201a15e74eea9e6c9ddf362245dc89a13e5429dc0607470370`；
- `recorded-matrix.json` SHA-256：
  `a2da83557da223f3f12ebb12097bce6400f276233e901600cd283e154f07ec2c`；
- 内部 report SHA-256：
  `45d84396b4ea7b7cd91b2607d4aa1e103f31f6429686e905e19e9c940860ae67`。

这些结果完成 R1A 工程候选的精确提交和跨平台复现证明；artifact 仍为
`RECORDED_FIXTURE`，不构成真实模型或生产 Cutover 证据。

仍然待完成：

- 精确 provider/model/revision 的 Core 80×4 与 Long-tail 120×PRIMARY/WEAK；
- 40 个多模型真实/受控 Fault case；
- 每 task 唯一 active path 的生产 Gateway/Effect/Fact/P19/Completion 联结；
- 全部 Cutover 指标、生产证据候选的 Ubuntu/Windows exact-head artifact 与独立复核；
- 合并、合并后 main 回归及 P11 正式关闭。

缺少模型访问、运行授权、预算或生产遥测时保持 pending，不用记录 fixture、模拟、
单元测试或 skipped 测试替代。
