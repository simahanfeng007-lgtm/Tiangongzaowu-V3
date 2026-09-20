# P17-B 追踪矩阵种子（v0.1）与欠项扫描结清记录

编制：2026-09-20。基线：main `7452995`。性质：**种子**——按总任务书第 18 章最终验收清单逐条建立「条款→实现→测试→证据→验收状态」的追踪骨架；母版 v1.2 原文逐字条款的对齐在取得原件后（校验其登记 SHA-256 `691e857e…f895`）补全，本种子以总任务书内嵌的清单与阶段定义为准源。

## 一、机械欠项扫描结清（P17_B_OBLIGATION_SCAN_2026-09-20.json）

权威树标记类扫描（TODO/FIXME/XXX、NotImplementedError、pass-only 定义）共 6 命中，**全部核验为显式功能用途，needs_review = 0**：

| 命中 | 性质 |
|---|---|
| `src/omni_body_skill/tools/delivery_kernel.py:602` 等 3 处 TODO/TBD | 占位文本**检测正则**（质检功能本身） |
| `v3/novel_system.py:76` | 同上（小说占位检测） |
| `v3/jineng/jirou_ceng.py:333` | `except NotImplementedError` 的能力探测回退 |
| `duihua_qiaojie.py:2346`、`gateway_links.py:2463` 的 `log_message: pass` | http.server 标准日志静默覆写 |

无恒定 PASS 断言扫描命中于权威树（测试树按 P17-B 完整版另扫）。

## 二、最终验收清单追踪（20 条）

| # | 条款（18 章） | 当前实现 | 测试承载 | 精确证据 | 生产/独立验收状态 |
|---|---|---|---|---|---|
| 1 | main 与发行工件绑定同一可核验源码 | 单一权威源 + 生成镜像（source-ownership） | check-source-authority / sync 检查 | 每 PR 必过 | 发行工件属 P17-C（R07）→ **BLOCKED** |
| 2 | Source→World→候选/经验→Proposal→P4→P7→执行→P19→Completion→Memory/Life 闭环成立 | R1C2/R1C3/R1C4/R1D/R1F 全链接线 | test_source_*、test_composition_* 系列 | PR #78/#79/#81-84 | 受控验证完成；真实模型链属 R1G → **BLOCKED** |
| 3 | 各域只有原权威（无第二 Store/Registry/Runtime/Completion） | 架构守卫 + 守卫测试 | test_p17_m4_architecture_guards 等 | 每 PR | ✅ 持续生效 |
| 4 | P4/P6/P7/P8/P9 真实任务与发布/部署欠项结清 | 工程侧就位（P8 bundle、P9 归档、P7 链） | 各专项测试 | 见各 PR | D01–D05 → **BLOCKED**（R03/R04/R07） |
| 5 | P11 四模型/200 任务/560 维度/40 故障独立复核 | 评测件已整合 main | 47 项 P11 测试 | PR #84 | **BLOCKED**（R03/R04/R06） |
| 6 | P12 旧静态规划停止、能力无损 | R1C3–R1F 全合；对等表 79/79 动作覆盖；行为对等 34 项 REQUIRES_TASK_PARITY | parity 表 6 测试 + 全链测试 | PR #79/#81-84/#85 | 退役（R1H）**BLOCKED** 于 P11 退出与零使用 |
| 7 | P13 旧权威清理、历史只读不授权 | 20 面退役矩阵（2 零调用候选） | 6 守卫测试 | PR #88 | P13-B/C/D **BLOCKED**（R05 生产观测） |
| 8 | P14 动态默认、无隐式 fallback | 受控回合显式 opt-in、失败不回退 | 11 项 R1D 测试 | PR #83 | 默认切换 **BLOCKED**（P11 退出先决） |
| 9 | P15 漂移→失效/过期/重验/版本策略 | A/B/D MERGED；C 机制保证 | 15 项 P15 系列测试 | PR #85/#86/#87 | C 完整验收 OPEN；阶段未关 |
| 10 | 冷启动可用；Embedding/Shadow 无硬权威 | 空经验/空图路径为合法冷启动 | R1F 冷启动测试 | PR #84 | ✅ 工程侧；真实冷启动部署随 P14-D |
| 11 | 经验正负分池/独立情境/过滤/统计/失效 | P5 原策略 + R1F 槽口 + P15-B 过期 | 78 项 P5/R1F 回归 | PR #84/#86 | ✅ 工程侧；统计真实性随真实任务（R1G） |
| 12 | P16 真实 150 轮连续证据 | 方案冻结 v1.0 | — | PR #87 | 执行 **BLOCKED**（R02/R03） |
| 13 | 原生 Windows 边界 + Ubuntu 回归 | CI 双平台全库门禁 | 每分片 + 汇总 | 每 PR | Ubuntu ✅；原生桌面实机属 P16-EXIT（R02） |
| 14 | 新老任务不重复副作用；AMBIGUOUS 先对账 | R1C4 反例矩阵 | 7 项往返测试 | PR #81/#82 | ✅ 受控验证 |
| 15 | P17 清理：无临时脚本/孤立模块 | 欠项扫描 0 needs_review；镜像官方同步 | 扫描快照 + 守卫 | 本批 | 最终清理 diff 随 P17-A 完整版 |
| 16 | 全部要求绑定正反例/集成/真实运行/独立决策 | 本矩阵 + 各专项 | 见各行 | 见各行 | 独立决策 **BLOCKED**（R06） |
| 17 | 最终候选全门禁通过 | 双平台+专项工作流 | 每 PR 精确候选 | 见台账 | 最终冻结候选待资源解锁后 |
| 18 | 正常 PR 合并 + main 复核 | 全程受保护合并 + 后置双绿 | — | PR #78–#88 | ✅ 持续生效 |
| 19 | 签名/LFS/安装/回退交付 | release 工作流存在 | release 相关测试 | 历史 PR | **BLOCKED**（R07：签名/LFS 本体/安装机） |
| 20 | 无未解释 BLOCKED/UNVERIFIED；维护交接 | 本矩阵逐条解释 + 台账 | — | 本批 | 交接书待最终验收后（P17-EXIT） |

## 三、诚实边界

- 本种子以总任务书内嵌清单为准源；**母版原文逐字对齐待原件**（登记哈希 `691e857e…f895`）——取得后升级为 v1.0 并逐字校验。
- 「✅ 工程侧」表示受控验证完成；不替代真实生产/独立验收（各行右列如实标注）。
- 测试树（tests/）的恒定 PASS 深扫属 P17-B 完整版；本批覆盖权威树。
