# 当前工程检查点导航

更新：2026-09-10。进度导航不是 Source 发布、权限或产品验收权威。

| 项目 | 当前记录 |
|---|---|
| main 工程基线 | `b1ea3e9d511aae9cabcc816e071ad3da645ae753` |
| 当前阶段 | P10，第 11/18 阶段；未合并 |
| 当前分支 | `codex/capability-composition-p10-life-learning-cutover-v1` |
| 已完成工作包 | R1 冻结；R2-A 三类产出准备；R2-B 机器经验准入/Memory 写回；R3-A 旧记录迁移与 Embedded Life 遥测 |
| 当前工作包 | R3-B 完整双平台验证；R3-C 代表性观察证据代码已实现 |
| 下一工程步骤 | 固定候选 CI、R3-C 部署观察复核与退出检查；之后才进入 R4 |
| 合并检查点比例 | 10/18 = 55.6%，不是产品验收率 |

R3-B 用无存储进程内 bridge 将 Duihua legacy callbacks、旧 muscle learning、raw
registry compatibility、legacy L0 projection 的实际调用转入原签名 Life journal。
Coverage 只从 observer 真正安装后开始，不能倒填 R3-A 的更早窗口；遥测故障不
改变旧入口业务结果，未覆盖面仍明确报告。

当前已知 18 个旧入口可分别报告 coverage start、调用数与 workload。历史 active/
published、pending patch、unknown ownership 和能力既有 usage 继续保守保留。
`zero_usage_proven=false` 仍固定成立；测试/AST/短窗口为 0 不能替代生产零使用证据。

Verification Plane 1.13 继承 R3-B 的 105 条冻结，包含 telemetry bridge 与 L0 projection，
共 105 条；独立 Source Authority 数仍为 17。Golden、权限、Memory/Gateway schema、
P5/P15 阈值和 Source 发布规则不放松。

R3 尚未完成，不进入 R4；R3-C 必须取得经批准的代表性或真实部署观察窗口，核对
起止残留/归属和在途 P9 source pin。有真实 legacy 调用就继续保留对应兼容面。
P8/P9 的真实任务、正式签审、旧打包补丁和生产回退债继续单独保留。

续跑恢复与 Windows 路径修复见
`P10_R3B_RESUME_VALIDATION_2026-09-10.md`。316 项本机回归通过；本候选的完整
P10 Windows/Ubuntu CI 尚待取得，不沿用 R3-A 的 CI 结果。

R3-C 工程证据见 `P10_R3C_REPRESENTATIVE_EVIDENCE_2026-09-10.md`：同一 Life
身份下，18 个真实入口调用、残留快照、签名 journal 重放与已经开始执行的 P9
任务来源保留联动验证。用户确认真实部署在其他电脑，本机负责计划内代码；短时
fixture 窗口不替代该部署的持续观察或独立复核。
