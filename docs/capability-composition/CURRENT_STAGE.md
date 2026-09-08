# 当前工程检查点导航

更新：2026-09-08。此文件是进度导航，不是运行时发布、权限或产品验收权威。

| 项目 | 当前记录 |
|---|---|
| main 工程基线 | `b1ea3e9d511aae9cabcc816e071ad3da645ae753` |
| 上一检查点 | P9，PR #74 已正常合并；最终候选 `28dd2bdacf86d093ba2c7e04e23b5d967aa72ec8` |
| 当前阶段 | P10，第 11/18 阶段；尚未合并 |
| 当前工作分支 | `codex/capability-composition-p10-life-learning-cutover-v1` |
| R1 状态 | 冻结源码已提交；`3b4d0af3` 两端定向 CI 成功，原始产物已独立复核 |
| 当前工作包 | R2-A：三类学习产出的准备与校验适配；本地 902 passed，10 skipped，11 subtests passed；不是生产接线 |
| 下一工程步骤 | R2-B：从既有生产权威取得可信基准，接回 Life/Gateway/MemoryCoordinator；R2 整体未完成 |
| 合并检查点比例 | 10/18 = 55.6%，不是产品验收完成率 |

上次聊天中“R1 未实施、仍在 R0”的描述错误，以当前源码和确切提交的证据为准。
R1 已修改确认、直接发布、补丁、维护重试、CURRENT、旧兼容写入和 projection。
Knowledge 与已授权历史任务未被删除，旧完整 Skill/Tool 发布冻结继续保持。

R2-A 只准备 Knowledge、Tool/Method Source 候选及 P5 经验 intent，不直接发布、
授权、执行或写 Memory。可信 expected pin 必须来自生产权威，不能由模型自填。
具体边界见 `P10_R2A_LEARNING_OUTPUT_PREPARATION_2026-09-08.md`。
远端跨平台结果按实际提交另行核对，本地结果不等于 Windows 或全阶段验收。

Verification Plane 保持 1.8，原冻结/Golden/诊断指纹/权限不变；本次未迁移数据库。
R1 新 journal 事件与 P9 World index v2 仍要求生产升级前备份和兼容回退方案。

P8/P9 历史报告、未应用的打包补丁、真实任务/生产签审/风险审批/版本切换及
运维债项仍保留，不因工程合并而通过。没有增加第二套 Runtime/WorldState/
Registry/Memory。P11 Shadow、P12 Planner 退役及 P13 兼容权威移除未开始。
