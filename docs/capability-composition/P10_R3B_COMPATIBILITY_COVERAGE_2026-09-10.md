# P10 R3-B：旧兼容面真实入口覆盖与观察窗口边界

状态：**R3-B 代码实现与本地定向验证完成；R3 整体仍未完成。**
本检查点补齐 R3-A 明确列出的四类未观测兼容面，但不把测试流量或零计数解释为
生产零使用证明，不删除兼容代码，不进入 R4。

## 基线与范围

- 仓库：`simahanfeng007-lgtm/Tiangongzaowu-V3`。
- 分支：`codex/capability-composition-p10-life-learning-cutover-v1`。
- R3-A exact focused head：`3ec70b1cc10d17e8e9debfe7491c38c716ac0412`；Windows/Ubuntu 均 SUCCESS。
- main 仍为 `b1ea3e9d511aae9cabcc816e071ad3da645ae753`，本阶段不修改 main。
- R3-B 开工通过临时只读 Actions 导出 current Git bundle，本地 verify/fsck 后开发；
  临时导出工作流在正式提交中删除，不保留长期开发旁路。

R3-A 已覆盖 Embedded Life 的 14 个旧 mutation API，但明确留下四个 V3 兼容面：

1. `v3.duihua_qiaojie.legacy_learning_callbacks`；
2. `v3.jineng.jirou_ceng._xuexi_liucheng`；
3. `v3.zhili.nengli_zhuche.raw_registry_compatibility`；
4. `v3.l0_ability_projection.legacy_projection`。

R3-B 仅补这些已知兼容面的**实际入口观测**和覆盖边界，不新增学习/发布/执行功能。

## 唯一遥测落点

新增 `v3/legacy_learning_telemetry.py` 是无存储的进程内桥。它只有一个由现有
Total Gateway 安装的 callable；自身没有数据库、CURRENT、队列、权限、审批、
执行器或后台 worker。Duihua、旧 muscle、raw registry 和 L0 projection 在真正
进入这些兼容函数时调用 bridge；bridge 再调用现有 `EmbeddedLifeRuntime`，最终
仍写入**同一个签名 Life journal**和同一个 projection。

Gateway 关闭时清空 observer，防止同进程后续实例继承旧 callback。observer 抛错
被 bridge 吞掉，旧兼容入口原本的冻结、只读或清理返回不改变；遥测不能把冻结
入口重新授权，也不能让本来允许的历史停用/读取因为统计故障失败。

## 覆盖时间不能倒填

R3-A 原 14 个入口的 coverage start 继续绑定其原 usage-window 事件。R3-B 新四个
external surface **只有在 Gateway 成功安装 observer 后**才写
`learning.legacy_usage_coverage_extended`，从该真实时间点向后计量，绝不把 R3-A
更早的 observation start 倒填给新覆盖面。

若 Gateway 启动时 coverage 持久化暂时失败，Gateway 本身继续启动；projection 仍
明确显示这些 surface 未覆盖。随后某个真实兼容调用到来时，只为实际到达的 surface
重试 coverage，再写 usage observation，其他三个仍保持 uninstrumented。这样故障
不会制造“我们一直在观测”的虚假历史。

usage-window 事件的 `coverage_sha256` 现在显式校验为原 R3-A 14 个入口集合，避免
R3-B 扩展常量后把旧 observation window 偷换成 18 个入口。已存在 surface 的
coverage start 不允许被重放或更早时间覆盖。观察时间被限制为不早于 coverage
start 和已有最后观察时间，时钟回拨不会生成窗口外记录。

新增 coverage journal 事件是 replayable projection；删除 state 中的 migration /
usage projection 后，正常 journal replay 能恢复外部 surface 的 coverage 与计数。

## 迁移/残留报告

原 `legacy_migration_summary` 继续报告旧学习卡、active/published artifact、pending
patch、unknown ownership、历史 capability usage，不删除或重新归属任何记录。
R3-B 增加：

- `known_entrypoints`：当前明确知道的 18 个旧入口；
- `instrumented_entrypoints`：已经有真实 coverage start 的入口；
- `coverage_started_at_ms`：逐入口开始时间；
- `uninstrumented_compatibility_surfaces`：仍未真正建立覆盖的外部面；
- `full_coverage_window_ms`：所有已知入口都覆盖后，从最晚 coverage start 起算；
- `zero_usage_blockers`：未覆盖、已经观测到旧使用、独立审查窗口不足等阻塞。

`zero_usage_proven` 继续固定为 false。即使 18 个入口在测试中全部覆盖且调用数为 0，
也不能把测试/AST/短时间窗口当成生产零使用。P13 删除兼容权威必须消费后续独立
审查的真实部署观察证据，而不是本模块自证。

## Source Authority 与 Verification Plane 1.12

新增 backend bridge 被纳入**原 `v3-backend-main` closed-world authority**，没有新增
独立 authority。Source Authority 仍为 17 independent / 1 alias / 24 generated /
1 closed-world。Life authoritative source 只经 `sync-generated-sources.py --write`
同步 runtime314 mirror。

Verification Plane 从 1.11 显式升级为 **1.12**。103 条 inherited freeze path 全部
保留，新增 legacy telemetry bridge 与 L0 projection 两条，共 105。原 guard 在刷新前
按预期报告 version/authority-map/authority-surface drift，fingerprint 报 authority-map
drift；随后只用既有 `UPDATE_FREEZE=1` / `UPDATE_FINGERPRINT=1` 生成器刷新，并在
UPDATE_FREEZE / UPDATE_FINGERPRINT / UPDATE_GOLDEN 全部关闭时重新验证通过。
Golden baseline、CompletionGate、P5/P15 阈值、ActionPermission、Memory/Gateway
schema 和 Source 发布规则没有放松。

## 原开发环境的验证及失败记录

本节是恢复补丁内保留的原开发记录，不代表本次 Windows 续跑结果。续跑已复现
路径启动失败并修复，Plane 已升级 1.13；当前证据见
`P10_R3B_RESUME_VALIDATION_2026-09-10.md`。

- R3-A + R3-B 专项：26 passed，0 failed/errors/skipped；3 条既有 warning。
- 学习冻结、journal、Life lifecycle、source authority、release/foundation 等支撑组：
  155 passed / 29 subtests，0 failed/errors。
- foundation + P19 freeze/calibration 后段：79 passed / 1 existing skip / 3 subtests，
  0 failed/errors；5 条既有 Pydantic warning。
- Source Authority PASS；官方生成镜像 `--check` 与 `--check-committed` PASS。

开发中保留两类失败：一项新故障用例最初错误期待“单个真实 surface 调用会自动
宣称另外三个也已覆盖”，已改为逐 surface 真实 coverage；这是测试预期错误，不是
降低门禁。扩大组第一次失败是 Plane 1.12 版本变化后 total_gateway 生成镜像尚未
再次同步，随后只用官方 generator 修复并重跑后段通过。

一次与 GitHub focused gate 完全同选择范围的本地大组被开发容器时限终止，仅产生
partial 点号输出，**不计 PASS**。正式提交后仍需以 exact Head 的 Ubuntu/Windows
focused CI 判断 R3-B 平台结果，不能沿用 R3-A 绿灯。

## R3 剩余退出条件

R3-B 解决“已知兼容面不可观测”的工程缺口，但没有真实生产窗口。因此 R3 还需
R3-C／验收动作：

1. 在真实部署或经批准的代表性工作负载中，从最晚 coverage start 后形成足够长、
   可复核的 observation window；
2. 对窗口起止时的 legacy record、pending patch、unknown ownership、active history
   做数量与归属快照，差异只能通过现有事件解释；
3. 核对窗口内实际 legacy usage；有调用就保留对应兼容面，不因项目计划而删除；
4. 对中断残留和未知归属继续保守保留；
5. 抽查迁移前后在途任务的 P9 WorldState/Method Source pin 未被切换。

这些证据未完成前，不进入 R4，不声明零使用，也不删除 Static Skill Planner、旧
registry 或 compatibility authority。P8/P9 的真实任务、正式签审和运维回退债继续
独立保留。工程检查点仍为 10/18=55.6%；P10 是第 11/18 阶段，尚未合并。
