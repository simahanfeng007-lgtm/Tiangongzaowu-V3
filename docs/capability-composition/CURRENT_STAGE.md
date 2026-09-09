# 当前工程检查点导航

更新：2026-09-09。进度导航不是 Source 发布、权限或产品验收权威。

| 项目 | 当前记录 |
|---|---|
| main 工程基线 | `b1ea3e9d511aae9cabcc816e071ad3da645ae753` |
| 当前阶段 | P10，第 11/18 阶段；未合并 |
| 当前分支 | `codex/capability-composition-p10-life-learning-cutover-v1` |
| 已完成工作包 | R1 冻结；R2-A 三类产出准备；R2-B 机器经验准入与 Memory 写回闭环 |
| 当前工作包 | R3-A：旧记录幂等迁移、重启恢复和实际 legacy mutation 入口遥测 |
| 下一工程步骤 | R3-B：真实观察窗口、未观测兼容面证据、历史/残留数量与归属核对 |
| 合并检查点比例 | 10/18 = 55.6%，不是产品验收率 |

R3-A 使用原 Life 签名 journal 和 projection，不新增迁移数据库。旧待发布 Skill/Tool
卡片转 migration_required；历史 active/published、pending patch、未知归属及在途
source pin 均保守保留。实际 Embedded Life 兼容 mutation 入口记录观察起点、调用
次数和 workload class；历史能力执行量复用现有 capability health，不重复记账。

已知仍有 V3 legacy callbacks、旧 muscle learning、raw registry compatibility、
legacy L0 projection 四类未纳入该 journal 遥测，因此 `zero_usage_proven=false`。
静态 AST 清单和测试中的 0 次调用均不能替代真实部署零使用证据。

Verification Plane 1.11 继承 102 条既有冻结并增加 R3 迁移/遥测模块为 103 条；
Golden、权限、Memory/Gateway schema、P5/P15 阈值与 Source ownership 不放松。

R3 未完成，不进入 R4；不开始 P11/P12/P13。P8/P9 的真实任务、正式签审、旧打包
补丁、生产回退等验收债继续单独保留。不得删除未知归属或根据缺失遥测猜测任务结束。
