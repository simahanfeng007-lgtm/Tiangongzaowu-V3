# P10 最终工程收口与独立代表性工作负载复核

状态：**P10 工程收口完成；允许开始 P11 前置审计。生产兼容删除未获授权。**

本记录追加在历史 R3/R4 文件之后，不修改当时的事实。复核环境从已合并 main
重新取得精确候选的 GitHub Actions 原始 artifact，未使用实现者本机的
`output/p10-r3-review-20260911/`，也未修改 artifact 字节或产品数据。

## 身份与 CI

| 项目 | 固定值 / 结果 |
|---|---|
| P10 exact candidate | `a23c4fe1e559483b7be2b3f4d3534d45de58650f` |
| candidate tree | `3562410166779a610f4ba5d875189578ffc60317` |
| merged main | `a13979a99b8f01f9ee8ff2d0a2269e6118eadee9` |
| P10 focused run | [34469275421](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/34469275421) |
| Windows artifact | ID `10149561356`; ZIP SHA-256 `1b6eddc6bedafc278c2335208d58778b474cf2e11798fbebb3a0fe82557e5eb6` |
| Ubuntu artifact | ID `10149108859`; ZIP SHA-256 `6dc0c90e3727dcd12132bc447e5d2cc3d1c59ebe72343e61959a5db8d82f7370` |
| Windows raw report | SHA-256 `8fd711cc53d115256d87dfe21fd27f8b531985d4e37e728986641e06a3923407` |
| Ubuntu raw report | SHA-256 `6dbbafd61470dcfdc469c55acb226ba8861334be457143427ab017a49a8b0b56` |
| Source identity | 两平台 1,097 个测试输入 SHA-256 完全相同；运行中未改变 |
| Focused result | Windows 1608 passed / 11 skipped；Ubuntu 1581 passed / 38 skipped |

PR #75 记录的 `0d98174b...` 是 2026-09-11 实现者本机 4,431 ms 复核报告，
不是上述 CI artifact 中两次独立平台运行的报告摘要。三份证据来源和时长不同，
摘要不同是预期结果，不能互相冒充。

候选上的 Architecture、P14、P19、P10 focused 共 13 个工作全部成功。合并后的
Architecture run 34549607615 六个工作及 P19 run 34549607558 两个工作也全部
成功，证明合并树没有引入新的主线门禁失败。

## 只读独立复核断言

两平台报告分别在原始字节上解析，以下断言全部通过：

1. `head` 精确等于候选，worktree clean；测试退出码为 0，更新 baseline 的环境
   变量不存在，测试输入前后不变。
2. 18 个已知旧入口全部建立 coverage，每个入口恰好观测一次；14 个 Life API
   和四个外部兼容面都来自实际入口调用，不是 AST 推断。
3. strict journal reader 读到恰好 18 条 `learning.legacy_usage_observed`，sequence
   连续为 9–26，入口集合与报告计数完全相同。
4. 迁移起点、终点和磁盘重放后，四类记录保持：冻结 Source Evolution pending、
   历史 active artifact、pending patch、unknown ownership 各一条；记录哈希与
   原归属不变。
5. Gateway effect 始终为 `SIDE_EFFECT_STARTED`；在 70 次 World 更新、历史裁剪、
   迁移和重放后，原 WorldState、Method snapshot 和 Method Source refs 仍可从
   磁盘读取，`preserved_across_migration_update_pruning_and_replay=true`。
6. 两份报告都保留 `production_zero_usage_proven=false`、
   `independent_window_review_completed=false`、`r3_exit_ready=false`，没有用测试
   自己签发验收结论。

Windows/Ubuntu 的观察窗口分别为 3,561 ms 和 2,878 ms。该长度不能推断长期
生产无调用；它足以复核事件完备性、迁移保留性、重放恢复和在途 Source pin
不漂移，因为这些断言依赖确定的事件/状态边界，而不是把短时“未发生”外推到生产。

## 退出决定

R3-B 定义了“真实部署 **或经批准的代表性工作负载**”分支。本次复核批准后者，
仅用于 P10 工程退出：

| R3 条件 | 复核结果 |
|---|---|
| 经批准的代表性工作负载 | PASS — 双平台、同一候选、相同输入身份 |
| 起止残留与归属快照 | PASS — 四类记录均保守保留，差异可解释 |
| 实际旧入口使用核对 | PASS — 18/18 均被观测，因此结论是保留兼容层 |
| 中断/未知归属保留 | PASS — pending 与 unknown 均跨重放保留 |
| 在途 P9 Source pin | PASS — World/Method/Source refs 跨 70 更新与重放不变 |

因此，P10 状态记为 **ENGINEERING CLOSED**，P11 仍须从 main 新分支开始，先做
前置条件与唯一执行路径审计。本决定不声称部署验证通过，也不修改历史原始报告中
由 fixture 固定为 false 的三个字段。

## 明确保留的债务

- `production_zero_usage_proven=false`：真实部署的长期使用观察仍未提供。
- Static Skill Planner、旧 registry 和 compatibility authority 不删除；代表性流量
  已实际证明这些入口可达。
- 生产零使用、删除范围、回滚和审计证据在 P13 前重新独立验证，不能由本记录授权。
- P8/P9 的真实模型任务、正式签审、打包、部署/回滚继续是总计划债务。
- P11 不继承任何“生产零使用已完成”的假设，也不包含 P10 兼容删除。

## 本次收口工作树验证

- 按 `p10-learning-freeze-focused.yml` 的相同选择规则运行 117 个文件：
  1581 passed、38 skipped、11 subtests passed、0 failed/errors，293.33 秒。
- 1,097 个 `src`、`tests`、V3 backend 与官方 runtime314 镜像输入在测试前后
  SHA-256 完全相同。
- Source Authority：17 independent / 1 alias / 24 generated targets /
  1 closed-world，PASS；committed mirrors 检查 PASS。
- P10 静态 publication inventory 固定到 `a23c4fe`：17 files / 64 symbols，
  `may_authorize=false`、`may_execute=false`、`production_usage_verified=false`。
- workflow YAML 和 `git diff --check` 均通过。首次使用容器默认 Python 时因没有
  安装 `pytest` 而在收集前退出；随后只按 `requirements-source.lock` 建立隔离环境
  并完成上述原范围回归。该首次退出不计产品失败，也不被记成 PASS。

## 清理检查

- main 跟踪树中没有 P10 transfer workflow、patch chunk、bundle、ZIP、JUnit、日志
  或 `output/` 证据残留。
- P10 focused workflow 不再引用已删除的 `p10-r1-transfer.yml`，并允许后续 P10
  维护分支正常触发；artifact 保留期延长到 90 天。
- 历史 R3/R4 文档保持原日期和原结论；当前导航与总台账只追加最终状态，不伪造
  当时已经合并或已经独立复核。

## 2026-09-12 尾项合并追加记录

上述复核结论、导航修正和 P10 focused 维护门随后固定在 PR #76：

- exact head：`ada993dc9eff4cc566e248cda620148903c797a8`；
- exact tree：`f5a0ce54213a6922eeb0f419a14e7b999188d30f`；
- merged main：`bf29542b3048c8d1806add8063b0db7c72be055b`，合并树与 exact tree 相同；
- exact-head P10 run 34703058105：Windows 1608 passed / 11 skipped，Ubuntu
  1581 passed / 38 skipped；两平台均有 11 subtests；
- exact-head Architecture、P14、P19 runs 34703094587、34703094609、
  34703094591 共 11 个工作全部成功；
- post-merge Architecture run 34705569241 六个工作、P19 run 34705569215 两个
  工作全部成功；Windows full Python 为 5203 passed / 32 skipped / 839 subtests。

新 exact-head P10 artifact 保留 90 天。Windows artifact ID 10301173255，ZIP
SHA-256 `70dae0c8b7f8a7360a8cccb4d2e7aa3e3e7d69be568fd3fc19797bebf5879768`；
Ubuntu artifact ID 10300592040，ZIP SHA-256
`e30edeb8a54d7d020e40b68a5bf600af7a811e43e87a780f330b287166bcc2cf`。
两份原始报告继续保留三个 false 字段，未把 fixture 或短窗口改写成生产零使用。
