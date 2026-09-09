# P10 R3-A：旧记录迁移清单、重启恢复与真实入口遥测

状态：**R3-A 实现并完成本地定向验证；R3 整体未完成。**
本检查点不宣称旧兼容入口已经零使用，不删除历史 Skill/Tool，不进入 R4。

## 基线

- 仓库：`simahanfeng007-lgtm/Tiangongzaowu-V3`。
- 分支：`codex/capability-composition-p10-life-learning-cutover-v1`。
- 已核验 R2-B 产品树：`a4cc40064a3d051405fb24039572ce5c1a26150a`。
- R2-B 完成提交：`e68e2731ee1828b6984a7f7ee396f2eb449af63b`；其 Windows/Ubuntu focused gate 均 SUCCESS。
- 当前 main 仍为 `b1ea3e9d511aae9cabcc816e071ad3da645ae753`，本阶段不修改 main。

为了避免再次出现本地/远端基线错位，R3 开工前通过临时只读 Actions 导出当前
P10 exact Git bundle，并在本地执行 bundle verify/fsck。导出工作流在本次正式
提交中删除，不作为产品运行时或长期写入旁路。

## 迁移原则

R3-A 不创建第二数据库、第二 Registry 或迁移状态机。迁移事实写入原 Life 签名
journal，并由原 projection replay 重建。旧记录采用保守策略：

- `awaiting_user` / `approved` 等旧完整 Skill/Tool 学习卡通过既有 R1 冻结边界
  转为 `migration_required`；草稿、来源、失败和证据不删除。
- 已经 frozen 的卡片只登记幂等迁移事实，不重复写入。
- 已发布历史能力、active/degraded/disabled 指针和对应 artifact 原样保留。
- `health.patch_pending` 不自动 settle、不切 CURRENT，登记为 `PENDING_PATCH_RETAINED`。
- 指针引用缺失 artifact 或来源不属于已知 life_learning/life_patch 时标记
  `UNKNOWN_OWNERSHIP_RETAINED`，不猜归属、不删除、不换新版。
- 迁移记录绑定 family/id/content hash；记录变化后产生新事实，相同内容重启不重复。
- 所有迁移记录明确 `destructive_change=false`、`source_pin_retained=true`，不能成为授权。

启动阶段在原 journal replay 之后执行一次迁移；每次 Life heartbeat 再执行同一
幂等函数，以覆盖启动后出现的兼容残留。journal 已提交但 state JSON 未落盘时，
下一次启动仍由原 WAL 重放恢复迁移投影。

## 真实入口遥测

R0 的 AST 清单不能证明真实使用。R3-A 在 Embedded Life 的实际 POST 路由入口
记录明确枚举的旧 mutation 入口，写入同一签名 journal：

- learning confirm / process-approved / request-activation / activate / release；
- legacy capability propose / approve / build / publish；
- capability activate / reactivate / rollback；
- capability patch propose / verify。

每次真实入口记录 sequence、entrypoint、workload class 和 observed time；projection
给出 observation start、总调用次数、按入口和 workload 的计数。遥测写入失败不会
改变原业务结果，也绝不能把冻结入口重新授权。

历史已激活能力的正常执行不再创建第二份计数；直接复用现有 capability health
`uses` / `last_outcome_at_ms`，因此面板同时展示历史 active 数量和已有执行使用量。

以下已知兼容面不在 Embedded Life journal 的可观测边界，当前明确列为
`uninstrumented_compatibility_surfaces`：V3 legacy learning callbacks、旧 muscle
learning pipeline、raw registry compatibility、legacy L0 projection。由于存在这些
未覆盖面，`zero_usage_proven` 固定为 false。R3 后续必须取得实际部署观察窗口和
这些兼容面的独立使用证据，不能因计数为 0 就提前进入 P12/P13 删除。

## Projection 与可见性

新增 journal 事件：

- `learning.legacy_migration_recorded`：replayable projection；
- `learning.legacy_usage_window_started`：replayable projection；
- `learning.legacy_usage_observed`：replayable projection。

`/api/v1/v3/life/panel` 的 learning 区域增加只读 `legacy_migration` 摘要，包含迁移
数量、unknown ownership、pending migration、观察窗口、真实 mutation calls、
历史能力 usage 及 instrumented/uninstrumented coverage。它不是权限或删除授权。

## Verification Plane 1.11

Verification Plane 从 1.10 声明为 **1.11**。102 条既有冻结路径全部保留，新增
`src/life_service/legacy_learning_migration.py`，总计 103 条 authority surface。
原 13 份 Golden baseline、CompletionGate、P5/P15 阈值、ActionPermission、
Memory/Gateway schema、Source ownership 均不放松。

正式刷新前，原 guard 按预期失败：freeze drift 包含 version/authority-map/surface，
fingerprint drift 包含 authority map。随后仅在显式 `UPDATE_FREEZE=1` 和
`UPDATE_FINGERPRINT=1` 下用原测试生成器刷新，正常无 UPDATE 标志重新运行通过。
Life runtime314 镜像只通过 `scripts/sync-generated-sources.py --write` 生成。

## 本地验证

- 新 R3-A 迁移/遥测用例：19 passed，0 failed/errors/skipped。
- R1 freeze、freeze recovery、journal reducer、R3-A、P19 freeze 的交叉定向组：
  93 passed，0 failed/errors；2 条已存在 warning。
- P19 freeze + calibration 正常模式：18 passed；5 条已存在 Pydantic warnings。
- Source Authority：17 authorities / 1 alias / 24 generated targets / 1 closed-world PASS。
- `sync-generated-sources.py --check-committed` PASS。
- 一次更大的 R1/R2-B/Memory/P19 组合回归被当前开发容器外部时限截断；它保留为
  partial，不计 PASS。R3 提交后由既有 Windows/Ubuntu focused workflow 在 exact
  Head 上继续验证，不能沿用 R2-B 绿灯。

新测试覆盖：启动迁移、二次迁移幂等、pending patch 保留、unknown ownership
保留、全部 learning alias 实际计数、多个 capability mutation 实际计数、panel
不声称零使用、纯分类不修改状态、历史 capability 使用复用原 health，以及故意
删除 state projection 后由 journal 重启恢复迁移/usage 计数。

## R3 剩余

R3-A 只是建立可迁移、可恢复、可观测基础。R3 仍需：

1. 在真实部署/代表性工作负载中形成可审计观察窗口；
2. 为当前列出的四类未观测兼容面补充实际入口证据或证明不可达；
3. 核对历史/失败 worker、pending patch、旧 active record 的真实数量与归属；
4. 对中断残留和不明归属持续保守保留并输出报告；
5. 确认在途任务的 P9 source pin 在迁移期间持续不变。

在这些退出条件未完成前，不进入 R4，不声明零使用，不删除 Static Skill Planner
或旧 registry/compatibility authority。P8/P9 历史验收债继续独立保留。

工程检查点仍为 10/18 = 55.6%；P10 是第 11/18 阶段，尚未合并。
