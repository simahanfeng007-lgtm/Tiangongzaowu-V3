# 天工造物 V3 — 终态工程态势总册

编制：2026-09-21。基线：main `2887423ac8f0f8c7b4ae4309234868c27a4f873e`（源码树 `48bb2dd`）。
性质：**接手指南**——任何新执行者从 main 加此一册即可了解全部工程事实、当前状态与下一步所需。不替代台账（细节在台账），本册是**一页可读的全景**。

## 一、本执行者完成了什么（21 个 PR，全部合入，每个合并后双绿复核）

| PR | 内容 | 阶段 |
|---|---|---|
| #79 | R1C3：Source→P4 结果接入原 P7 登记/封存/准入 | P12 |
| #80 | P12→P17 执行台账 + 导航更新 | 文档 |
| #81–82 | R1C4：受控全链执行（授权→执行→Effect/Fact→P19→Completion）+ 反例矩阵闭合 | P12 |
| #83 | R1D：编排接入受控规划回合（显式 opt-in、失败不回退） | P12 |
| #84 | R1E（对等表）+ R1F（经验闭环）+ R1G-prep（P11 评测件整合） | P12 |
| #85 | P15-A：Tool 发布→原 World 失效链（验证平面 1.22） | P15 |
| #86 | P15-B：Source 变化→经验过期（双权威桥） | P15 |
| #87 | P15-D：时态源策略核验 + P15 收口记录 + P16-A 方案冻结 v1.0 | P15/P16 |
| #88 | P13-A：旧权威退役矩阵（20 面） | P13 |
| #89 | P17-B：条款追踪种子（20 条）+ 欠项扫描（0 待审） | P17 |
| #90 | P16 框架 + P15-C 受控重验 + 测试树深扫 | P16/P15/P17 |
| #91 | P16-C 中断注入器 + P13-C 机制核查 + 导航刷新 | P16/P13 |
| #92 | Admission 物化器 + 生产经验召回缝（验证平面 1.23） | P12 |
| #93 | Pin 携带 verifiers + 模型异常传播 + P16 合体 | P12/P16 |
| #94 | P11 正式矩阵输入侧冻结（200 任务 + 40 故障） | P11 |
| #95 | P14-A 模式权威草案（OFF/SHADOW/LIMITED/DEFAULT） | P14 |
| #96 | P14-B/C 预研：TurnPolicy + 受治理回合 | P14 |
| #97 | P17-A 动态面预审计 + P13-D 删除演练（矩阵自纠） | P17/P13 |
| #98 | P17-A 深核：121 处动态命中全注记 | P17 |
| #99 | P13-C 深核：life_learning_memory 归类修正 | P13 |

## 二、当前验证平面与 Store

- 验证平面：**1.23**；Gateway Store：**v33**。
- 合并后 main 上 Architecture gate 与 P19 golden 双绿（每个 PR 合并后独立复核）。

## 三、全维度零未解释欠项

| 维度 | 状态 |
|---|---|
| 权威树 TODO/NotImplemented/pass-only 扫描 | **0 needs_review**（6 命中全为显式功能） |
| 测试树 pass-only 扫描 | **0 needs_review** |
| 动态面（importlib/getattr 链/env 分支） | **121 命中全注记（verified）**，退役候选×动态目标 = 0 |
| 退役矩阵 | 20 面、1 真退役候选（capability_lifecycle，删除演练已通过） |

## 四、BLOCKED 项及其所需（逐一在册）

| 项 | 所需资源 | 说明 |
|---|---|---|
| R1G 正式验收 | R03 四模型凭据 + R04 签审 + R06 独立复核 | P11 的 200×4 矩阵、560 维度、40 故障 |
| R1H 旧规划退役 | P11 正式退出 + R05 生产零使用 | P12 最后一步 |
| P13-B 零使用 | R05 生产观测窗口 | 所有旧入口删除的先决 |
| P13-D 实际删除 | P13-B 通过 | capability_lifecycle 已删除安全 |
| P14 真实切换 | P11 正式退出 | 规则草案+TurnPolicy+受治理回合已备 |
| P16 真实 150 轮 | R02 原生环境 + R03 真实模型 | 方案冻结+框架+注入器+150 轮规模验证已备 |
| P17-B 母版逐字对齐 | v1.2 母版原件（SHA-256 `691e857e…f895`） | 种子版以交接书内嵌清单为准源 |
| P17-C 正式交付 | R07 发行签名/LFS/安装机 | |
| P17-D 最终独立审查 | R06 独立复核方 | |

## 五、新执行者接手步骤

1. `git clone https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3.git && cd Tiangongzaowu-V3`
2. `python scripts/install-python-dependencies.py --requirements requirements-source.lock`
3. `python -m pytest tests/ -q`（全库回归应在 CI 分片全绿）
4. 读 `docs/capability-composition/P12_TO_P17_EXECUTION_LEDGER_2026-09-20.md`（工作包台账）
5. 读 `docs/capability-composition/CURRENT_STAGE.md`（当前检查点）
6. 本册 + 台账 = 全部决策上下文

## 六、工程纪律（始终遵守）

- 不建第二权威（Store/Registry/Runtime/Completion 各只一个）
- 不为绿灯扩大超时、跳过测试、降低冻结保护
- 冻结面变更必须显式推进验证平面版本
- 删除前必须演练、删除须零使用证明
- 模型永远不可选模式、不可提供来源路径/哈希/权限
- 每次合并后独立复核 main 的 CI 结论
