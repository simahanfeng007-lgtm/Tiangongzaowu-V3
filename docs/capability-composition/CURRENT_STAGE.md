# 当前工程检查点导航

更新：2026-09-08。此文件仅为进度导航，不是发布、权限或产品验收权威。

| 项目 | 当前记录 |
|---|---|
| main 工程基线 | `b1ea3e9d511aae9cabcc816e071ad3da645ae753` |
| 上一已合并检查点 | P9，PR #74；正式任务及运维债仍保留 |
| 当前阶段 | P10，第11/18阶段；尚未合并 |
| 当前工作分支 | `codex/capability-composition-p10-life-learning-cutover-v1` |
| 已确认基线 | R2-A `79a4f8ba`，两端定向 CI 与原始产物均已核验 |
| 本轮工程检查点 | R2-B：签名 Life 输入、Knowledge 原路径、World/Git 候选准备、worker 机器证据后置审计已接入 |
| 未完成的关键项 | P5 来源归因、当前源码再验证、Memory 父派生记录与聚合前态比较及幂等提交 |
| 下一步 | 继续 R2-B 的经验提交闭环，不提前声明 R2 完成或进入 R3/R4 |
| 合并检查点进度 | 10/18=55.6%，不是产品验收率 |

R1 旧式完整 Skill/Tool 发布冻结继续生效。Knowledge 确认/风险处理和历史任务
授权不取消。新机器证据审计始终 may_write_memory=false；它不是成功经验，
不替代 sealed Completion、P19、P5 归因或 MemoryCoordinator 准入。

详见 `P10_R2B_PRODUCTION_INPUT_BINDING_2026-09-08.md`。本轮已有调用点不是
全负载部署证明；签审配置、上游 Source selector 生成、旧记录迁移、自动补审计
和生产遥测仍需验收。原审计事件使用同一 journal，不新建第二存储或状态机。

Verification Plane 1.9 继承全部冻结覆盖；新增事件要求部署前备份，旧版本回退
须兼容重放或审查后的恢复。本轮未迁移用户生产数据。

P8/P9 的真实 Source 发布/风险批准/运行锁/任务矩阵及未应用打包补丁仍单独保留。
P11 Shadow、P12 Planner 退役、P13 兼容权威移除没有提前启动。
