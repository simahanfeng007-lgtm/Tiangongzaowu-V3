# P11 R1B — 回放证据闭合检查与真实验收接入单

状态：本批是 P11 收尾修复。P11 生产验收尚未完成，P12 尚未获准开始。
基线为 PR #77 的 `484da9ccfa676c5e257f8831ad4f057ad5caea0c`；main 仍为
`bf29542b3048c8d1806add8063b0db7c72be055b`。

## 本批修复及复现

R1A 报告构建器核对了 binding 的 task ID、profile 和 Plan，但遗漏了同一 task
的输入 SHA-256 与 Static 对照输出 SHA-256。审计用例只替换这两者之一、重算
关联哈希，旧代码仍可通过 Formal Gate。生产切换门禁始终为 false，没有发生
生产放行。本批增加上述两项逐 task 核对。

另一个反例是把四个 role/profile 指向同一 provider/model/revision。旧代码只数
profile ID，误将其视为四模型矩阵。本批要求四组不同的精确模型身份。

修复前，针对这两个问题的五个新增反例全部失败：四个输入/Static 篡改组合未被
拒绝，一个重复模型身份用例被误判为完整矩阵。修复后均纳入 focused gate。

本批直接修改 `src/world_understanding/capability_composition/formal_shadow.py`，
沿用 Source Authority 官方同步；直接消费者为 P11 报告调用方、R1A binding
协议、P11 测试及 focused workflow。不增加模型调用、业务存储或执行权威。

提交前本地 focused 结果为 86 passed / 0 failed / 0 skipped；五个既有 Pydantic
`schema` 命名 warning。Source Authority 检查为 17 independent / 1 alias /
24 generated / 1 closed-world，committed mirrors 通过。这是本地候选验证，
提交后两平台的最终计数、SHA 和 workflow 身份另记在 PR #77。

## 可直接运行的离线证据检查

新增 `scripts/check-p11-evidence.py`，从导出文件重建既有 P11 合同，再调用原
报告构建器重算每个指标和 blocker。检查严格类型、重复 JSON key、子对象哈希、
identity 中的 HEAD、完整 binding sidecar 和重新计算后的完整报告。

命令适用于 Python 3.12，依赖使用仓库的 `requirements-source.lock`。在已经配置
好源码测试环境的机器执行；以下命令在 PowerShell / Bash 均可单行运行：

```text
python scripts/check-p11-evidence.py --report evidence/report.json --identity evidence/identity.json --expected-head <精确40位提交SHA> --bindings evidence/live-bindings.json --output evidence/audit.json
```

记录样本没有 live binding，省略 `--bindings`。CI 下载的文件名为
`recorded-matrix.json`，可直接替换上例的 report 路径。

增加 `--require-production` 可输出正式退出缺项：

```text
python scripts/check-p11-evidence.py --report evidence/report.json --identity evidence/identity.json --expected-head <精确40位提交SHA> --bindings evidence/live-bindings.json --require-production --output evidence/production-precheck.json
```

退出码约定：

| 退出码 | 含义 |
|---|---|
| 0 | 导出字节、声明身份和重算指标一致，Formal Gate 通过；仅结构/哈希核验 |
| 1 | 输入缺失、格式/哈希/绑定/重算不符，必须修正证据来源 |
| 2 | 已完成读取和重算，但指标或正式退出条件未满足 |

原始报告永远保留独立复核 blocker。因此 `--require-production` 对尚未经外部
验收的原始报告返回 2 是正确结果。命令固定 `p12_authorized=false`，不会生成
独立复核、真实 provider 收据或生产链路证明；即使输入自称 production，它也
只验证结构和内容哈希。HEAD 应从可信的 GitHub 提交记录取得，不能从待验文件
自行抄取后声称来源已验证。

输出包含三个输入文件的字节 SHA-256、内部 report SHA-256、模式、矩阵计数、
重算结果及全部 blockers。命令仅写显式指定的输出文件，不打开 Gateway/Runtime
或 SQLite，不联网；禁止把输出路径设为输入证据路径。

## 交付给验收环境的最小证据包

| 文件/数据 | 来源与必备绑定 |
|---|---|
| report.json | `P11FormalShadowReportV1.payload()` 加 `report_sha256`；不能手改统计数字 |
| identity.json | 同一次冻结提交与报告身份；字段见下方 |
| live-bindings.json | 全部 560 个 `P11LiveReplayBindingV1.payload()` 加 `binding_sha256` 的 JSON 数组；不得缺项、重复或跨 task |
| 原始 RunObservation | 既有 append-only 存储导出，含 primary/repair 原文、时间、模型快照、cohort/pair/record 哈希 |
| P4/P7A 输入与输出 | candidate snapshot、Proposal、system-compiled Plan、Validation 和只读 Shadow，供按原始字节重放 |
| 同任务生产链路 | 唯一 Gateway 的 request/run、Policy/Ticket/Grant、Effect/Fact、P19 记录和 CompletionDecision；每 task 只有一条执行路径 |
| Fault 证据 | 40 个 case、12 类故障、每 case 至少两个模型角色；包含注入方式、原始失败、containment 与恢复证据 |
| 独立复核记录 | 复核者身份、未参与生成的声明、精确提交/所有文件哈希、逐项证据判定及最终退出决定 |

identity.json 的必备字段沿用 focused workflow：`head`、`evidence_mode`、
`formal_gate_passed`、`cutover_gate_passed`、`cutover_blockers`、`task_count`、
`model_observation_count`、`fault_case_count`、`report_sha256`。
后三项计数分别应为 200、560、40。完整 binding 不嵌在报告中，所以 live sidecar
必须和报告一起归档；CLI 为 sidecar 另算字节哈希，独立复核还须读取对应原始记录。

既有 observation 默认路径为
`runtime/state/execution_shadow/run-observations.sqlite3`，部署可注入其他位置。
从实际运行实例导出一致性快照，保留原始数据；不能创建一个空库充当运行证据。

## 当前必须接通的真实资源

2026-09-14 在当前执行环境检查了后端支持的 20 个 API key 环境变量名，配置数为
0；工作目录未发现实际运行的 observation/Gateway SQLite 数据。两份上传 ZIP 为
7 月基线，不含本轮 live matrix。GitHub PR #77 仍为 Draft，审查提交数为 0。
这些检查仅说明本执行环境可见的资源，不推断其他机器是否已配置。

| 资源 | 具体需要 |
|---|---|
| 模型访问 | PRIMARY、SECONDARY_A、SECONDARY_B、WEAK 各自 provider ID、model ID、精确 revision、endpoint/协议和可用凭据 |
| 调用配额 | Core 80×4 + Long-tail 120×2，共 560 条正常规划输出；40 个 Fault 至少 80 个模型维度观察，修复/重试另计 |
| 运行环境 | 可运行现有天工造物主链的验收机器/实例及观测窗口；200 个真实且不同的 task input/goal 和验收条件 |
| 数据读取 | 对应 RunObservation 与生产 Gateway/Effect/Fact/P19/Completion 的只读导出或访问 |
| 复核人 | 未参与生成该批 artifact 的复核方，对上述冻结证据作退出决定 |

调用预算可按约 640 次尝试起估算，但 provider unavailable / permission denied
等故障可能在发送模型请求前被正确阻断，必须按实际失败记录，不把它们冒充为
成功的真实 provider 响应。

凭据继续由已有桌面“模型设置”入口进入 Electron safeStorage，或在验收环境配置
现有后端支持的环境变量。常见映射为 `TIANGONG_OPENAI_API_KEY`、
`TIANGONG_DEEPSEEK_API_KEY`、`TIANGONG_ZHIPU_API_KEY`、
`TIANGONG_MINIMAX_API_KEY`；自定义 endpoint 继续使用既有 provider identity
绑定。不要把密钥加入本 JSON 证据包、Git 提交或聊天。

## 接通后的执行次序和退出门槛

1. 冻结四个精确模型身份、真实任务矩阵、Source/World/Policy/验收条件和提交。
2. 使用既有 provider 和 Shadow 记录机制取得原文，经 R1A 接入编译/验证；parse
   失败、provider 失败和至多一次 repair 都保留，不能删失败样本凑 560 条成功。
3. 经现有 Gateway 对每 task 选择唯一 active path；另一条仅观察。联结原始
   Effect/Fact/P19/Completion 证据，完成 40 个多模型受控故障。
4. 构建 `PRODUCTION_SHADOW_TRACE` 报告与 live sidecar，运行本检查命令。
   独立复核读取原始 provider/trace 字节并验证 provenance；单靠自述字段和哈希
   不构成真实生产证明。
5. 全部切换指标通过、独立复核完成，运行最终候选的 Ubuntu/Windows focused、
   Architecture/P14/P19 和阶段要求的完整回归。完成后再将 PR 转 Ready、合并、
   复核 main 回归及 ancestry，才正式进入 P12。

本批本地证据和远端新提交的 CI 结果应分别记录在 PR #77，不继承前一提交的绿色
状态。不合并未验收的生产退出决定；兼容层继续保留。回退点为上述 R1A 基线。
总计划工程合并进度仍为 P0–P10，11/18 = 61.1%。P8/P9 既有产品/部署未结项不变。
