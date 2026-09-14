# 当前工程检查点导航

更新：2026-09-14。进度导航不是 Source 发布、权限、生产零使用或产品验收权威。

| 项目 | 当前记录 |
|---|---|
| main 工程基线 | `bf29542b3048c8d1806add8063b0db7c72be055b`（PR #76 已合并） |
| 已合并阶段 | P0–P10，共 11/18 = 61.1%；仅表示工程阶段已合并 |
| 当前工作分支 | `codex/capability-composition-p11-formal-shadow-v1` |
| P10 状态 | 工程实现及尾项 PR #76 已合并；代表性工作负载独立复核完成，兼容层继续保留 |
| 当前阶段 | P11 R1B 修复回放输入/Static 绑定与重复模型身份，增加离线证据重算；生产 Cutover 验收输入未完成 |
| 仍然开放 | P8/P9 真实模型任务、正式签审、打包、部署/回滚；P13 前生产零使用证明 |

## P10 最终边界

P10 的固定候选为 `a23c4fe1e559483b7be2b3f4d3534d45de58650f`，测试合并树与该候选
完全一致；PR #75 于 2026-09-11 合并为 main `a13979a`。候选上的 Architecture、
P14、P19 和 P10 focused 共 13 个工作均成功。合并后的 Architecture
[34549607615](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/34549607615)
六个工作和 P19
[34549607558](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/34549607558)
两个工作也全部成功。

2026-09-12 的独立复核直接读取精确候选的 Windows/Ubuntu P10 artifact。两平台
测试输入 1,097 个 SHA-256 完全相同，Source Authority 与 committed mirrors 均
通过；focused 结果分别为 1608 passed / 11 skipped 和 1581 passed / 38 skipped。
两份原始代表性报告的 SHA-256、复核断言和范围见
`P10_FINAL_CLOSEOUT_2026-09-12.md`。

代表性工作负载覆盖 18/18 个已知旧入口，保留四类残留及原归属，严格读取连续的
18 条 usage journal 事件，并证明已经 `SIDE_EFFECT_STARTED` 的 P9 任务在 70 次
World 更新、历史裁剪、迁移和磁盘重放后仍固定到原 World/Method Source。该证据
足以完成 P10 的代表性工作负载工程复核。

它不证明真实部署长期零使用。原始报告中的
`production_zero_usage_proven=false` 保持不变，而且代表性流量实际触发了全部旧
入口，所以旧兼容面必须继续保留。生产观察、零使用证明和删除授权是 P13 退出前
的独立门槛；不得在 P11 中把它写成已通过，也不得据此删除 Static Skill Planner、
旧 registry 或 compatibility authority。

P10 尾项由 PR #76 在精确候选 `ada993dc9eff4cc566e248cda620148903c797a8`
完成，合并为当前 main `bf29542b`；候选树与测试树一致。候选 13 个工作、合并后
Architecture run 34705569241 六个工作及 P19 run 34705569215 两个工作均成功。
这次尾项只更新最终证据、当前导航和 P10 focused 维护门，不改变上述兼容保留
决定。

## P11 当前边界

P11 R0 新增 Static/Dynamic Formal Shadow 差分合同。记录样本固定覆盖 200 个
task、560 条模型规划观察和 40 个故障场景，并强制每 task 只有 Static 或 Dynamic
一条执行路径。该样本只证明合同行为：`formal_gate_passed=true`，但由于不是
真实模型与生产 Gateway/P19 trace，`cutover_gate_passed=false`。

R0 精确远端 HEAD `a421c1ff347f57f3a42cb9630252c6bfca574d1b` 的
Architecture、P11 focused、P14、P19 共 13/13 个工作成功。Ubuntu/Windows P11
artifact 的 `identity.json` 与 `recorded-matrix.json` 字节一致；该结论只绑定这个
精确 HEAD。

R1A 已把既有 append-only `RunObservation` 中的真实候选原文、Static 对照输出、
provider/model/revision 与 P4 Proposal/Plan 绑定为只读、内容寻址的回放证据。远端
源码冻结点 `318b8bd7fc43c2c27e02ba202cdb3cb4287426c2` 的 Architecture、P11、P14、
P19 共 13/13 个工作成功，P11 两平台各 71 passed / 0 failed / 0 skipped，且记录
artifact 的身份与矩阵字节跨平台一致。

接入层不拥有模型客户端、业务 Store、Gateway、Runtime 或权限；它也不能代替生产
Gateway/P19 单路径 trace。R1A 工程检查点之后，2026-09-14 的 R1B 审计发现了
回放输入/Static 对照绑定遗漏以及重复模型身份误计问题，本批已修复并纳入回归。
新增 `scripts/check-p11-evidence.py` 可读取导出报告与完整 live binding sidecar，
重算指标并校验身份；它只做结构与哈希检查，不批准生产退出。新候选与 CI 证据
见 PR #77，不能继承上一个提交的门禁结论。接通资源与验收命令见
`P11_R1B_ACCEPTANCE_HANDOFF_2026-09-14.md`。

P11 仍需取得以下外部验收输入：
四个精确模型角色的真实输出、40 个多模型 Fault、同 task 的生产单路径 trace 与
未参与生成的独立复核。完整范围见
`P11_R0_FORMAL_SHADOW_CONTRACT_2026-09-12.md` 与
`P11_R1A_LIVE_REPLAY_EVIDENCE_INTAKE_2026-09-13.md`；在 R1 证据和独立复核完成前
不进入 P12。

## 历史导航

- R0–R2 的冻结、产出准备、Source 绑定和机器经验写回分别见对应 P10 文档。
- R3-A/B/C 的迁移、18 入口遥测和原始代表性 fixture 见对应 2026-09-09/10 文档；
  这些文件保留当时“未独立复核、未合并”的历史状态，不回写历史。
- R4 预验证、原生 AppContainer 修复和 PR 前门禁见
  `P10_R4_CLOSEOUT_2026-09-10.md`；最终合并与复核结果以上述最终收口记录为准。
- P8/P9 的未结真实任务、签审、打包和生产回退债继续在总台账中单独保留。
