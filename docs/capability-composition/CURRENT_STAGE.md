# 当前工程检查点导航

更新：2026-09-08。此文件是进度导航，不是运行时发布、权限或产品验收权威。

| 项目 | 当前记录 |
|---|---|
| main 工程基线 | `b1ea3e9d511aae9cabcc816e071ad3da645ae753` |
| 上一检查点 | P9，PR #74 已正常合并；最终候选 `28dd2bdacf86d093ba2c7e04e23b5d967aa72ec8` |
| 当前阶段 | P10，第 11/18 阶段；尚未合并 |
| 当前工作分支 | `codex/capability-composition-p10-life-learning-cutover-v1` |
| 当前工作包 | R1：已实现旧式新完整 Skill/Tool 发布冻结，本地扩大定向验证通过；精确提交跨平台结果另行记录 |
| 下一工程步骤 | R2：沿既有路径接通 Knowledge / Source Evolution / Composition Experience |
| 合并检查点比例 | 10/18 = 55.6%，不是产品验收完成率 |

R0 的静态盘点不等于运行时使用统计。R1 已修改实际确认、直接发布、补丁、
维护重试、CURRENT、旧兼容写入和 projection 接口；Knowledge 与已授权历史
任务不因新发布冻结而被删除。源码冻结不是已部署生产、全量任务通过或零使用证明。

当前细节见 `P10_R1_LEGACY_PUBLICATION_FREEZE_2026-09-08.md`；R0 报告保留原始
时点。Verification Plane 1.8 明确声明并继承全部冻结覆盖，新事件使用原 journal。
生产升级前备份数据；旧二进制不认识新事件，回退须兼容重放器或审查后的备份恢复。

`P8_P17_PROGRESS.md` 及 P8/P9 历史报告保留原时点和证据缺口，不能用其中的旧
未开始／未合并状态代替当前 Git 记录。真实模型任务、正式签审、风险审批、
Source 发布、运行中版本锁及运维回退等缺口不会因工程合并自动通过。

不依据本文件关闭保护检查、扩大权限、删除未知归属记录或增加第二套
Gateway/Runtime/WorldState/Registry/Memory。P11 Shadow、P12 Static Skill Planner
退役和 P13 旧兼容权威移除仍属于后续阶段，不在 R1 提前宣布完成。
