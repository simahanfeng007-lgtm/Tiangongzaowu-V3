# 当前工程检查点导航

更新：2026-09-08。进度导航不是 Source 发布、权限或产品验收权威。

| 项目 | 当前记录 |
|---|---|
| main 工程基线 | `b1ea3e9d511aae9cabcc816e071ad3da645ae753` |
| 当前阶段 | P10，第 11/18 阶段；未合并 |
| 当前分支 | `codex/capability-composition-p10-life-learning-cutover-v1` |
| 本次工作包 | R2-B：受约束机器经验的 P5 准入、Memory 写回与原 worker 恢复尾部 |
| 验收结果 | 按 `P10_R2B_EXPERIENCE_WRITEBACK_2026-09-08.md` 及精确提交 CI 原始记录核对 |
| 合并检查点比例 | 10/18 = 55.6%，不是产品验收率 |

R1 的旧式新完整 Skill/Tool 发布冻结继续保留。R2-A 的准备器与 R2-B 前一检查点
机器审计仍为零权限材料；不能把它们的 hash 或 flags 当作写入授权。

当前链路通过实际 Gateway/Life/World 读取机器基准，复核当前 source 后调用原
P5 和唯一 MemoryCoordinator。L3 DATA 写入在原 SQLite 事务内 CAS 并重查父链；
原 worker 已完成记录的恢复分支也接入重试，不重执行工具或重提 Life terminal。
正经验限闭合、可完整证明的现有 P7 A0 read/verify 任务，未知/不支持来源延后。
原 P5 成功/失败分池和 PROBATION 等统计策略不变，没有生成执行权限或第二套记忆。

L1、L3、journal 是分阶段持久化，不宣称跨库分布式原子事务；后置错误明确为
UNCONFIRMED。旧 World 或父证据被回收/失效时不得改用新版。长期待处理记录全量
扫描、所有兼容/失败 worker 的自动采集、Source selector 生成与 operator 部署
仍需独立核验，不能用单元测试代替真实工作负载与正式签审。

Verification Plane 1.10 保留95条原冻结并增加7条。Golden、权限、原数据库
schema 不变。新增 journal 审计事件需兼容重放或备份恢复，启用前备份数据。

本轮不进入 R3/R4，不合并 main，不部署用户环境。P8/P9 的真实任务、源码发布、
风险审批、旧打包补丁及运维回退等债项单独保留。不得增建第二 Runtime/Gateway/
WorldState/Registry/Memory，不得降低原保护门禁。P11/P12/P13 未开始。
