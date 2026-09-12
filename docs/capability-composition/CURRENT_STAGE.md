# 当前工程检查点导航

更新：2026-09-12。进度导航不是 Source 发布、权限、生产零使用或产品验收权威。

| 项目 | 当前记录 |
|---|---|
| main 工程基线 | `a13979a99b8f01f9ee8ff2d0a2269e6118eadee9`（PR #75 已合并） |
| 已合并阶段 | P0–P10，共 11/18 = 61.1%；仅表示工程阶段已合并 |
| 当前收口分支 | `codex/capability-composition-p10-final-closeout-20260912` |
| P10 状态 | 工程实现已合并；代表性工作负载独立复核完成，兼容层继续保留 |
| 下一阶段 | P11 Formal Shadow；尚未开始 |
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

## 历史导航

- R0–R2 的冻结、产出准备、Source 绑定和机器经验写回分别见对应 P10 文档。
- R3-A/B/C 的迁移、18 入口遥测和原始代表性 fixture 见对应 2026-09-09/10 文档；
  这些文件保留当时“未独立复核、未合并”的历史状态，不回写历史。
- R4 预验证、原生 AppContainer 修复和 PR 前门禁见
  `P10_R4_CLOSEOUT_2026-09-10.md`；最终合并与复核结果以上述最终收口记录为准。
- P8/P9 的未结真实任务、签审、打包和生产回退债继续在总台账中单独保留。
