# P10 R2-A：三类学习产出的准备与校验适配

状态：**R2-A 已实现并完成本地定向验证；R2 整体尚未完成，生产接线仍待 R2-B。**
本次没有合并 main，没有启用模型调用、生产签审或真实能力发布。

## 当前基线与上次进度更正

- 仓库：`simahanfeng007-lgtm/Tiangongzaowu-V3`。
- 分支：`codex/capability-composition-p10-life-learning-cutover-v1`。
- 本次远端父提交：`3b4d0af33beec03c35e4637460533ae485545bbd`。
- main：`b1ea3e9d511aae9cabcc816e071ad3da645ae753`，本次未修改。

上一条聊天回复称 R1 未实施且仍在 R0，这与仓库不符。重新读取最新分支、
实际源码和 CI 后确认：R1 冻结代码已经存在，本轮不重复实施或覆盖它。
原 R1 定向运行 `34169948282` 两端已成功。独立下载原始产物并核对外层摘要、
HEAD、JUnit、日志及输入清单：Ubuntu 821 passed / 10 skipped / 11 subtests passed；
Windows 820 passed / 11 skipped / 11 subtests passed。两端 1,085 个原有 Python
输入均与本次本地基线字节一致。平台跳过不算通过，两个平台不相加算独立任务。

## 本次实现范围

只新增 Gateway 内的 `learning_output_preparation.py`，复用已经存在的
Knowledge、P8、P9 和 P5 API。它不是新 Runtime、Publisher、Registry、Memory
或学习状态机，也不开放新的模型、HTTP 或 Tool 调用入口。

| 学习类别 | 本次准备逻辑 | 仍由原路径负责的后续步骤 |
|---|---|---|
| Knowledge | 检查当前学习卡身份，复用原纯编译器，得到 built 预览 | 原有确认、风险处理、知识导入和持久化 |
| Source Evolution / Tool | 调用 P8 原生 Git 候选检查，绑定确切 base/candidate 与 Action 范围；不导入候选代码 | 隔离构建、权限差异、独立审查、签审发布和版本选择 |
| Source Evolution / Method | 重建原方法快照与生命周期计划，核验源码字节、来源和独立观察者签名；ADD/UPDATE/REMOVE 均需原始正反案例 | P9 独立审核、Git/WorldFrame 绑定、发布与在途任务源码锁定 |
| Composition Experience | 重算 P5 归因与准入，检查主体/隐私/源码/前态，再准备原 P5 聚合和 Memory intent | 原机器证据采集、父派生记录验证、MemoryCoordinator 提交与并发控制 |

上述是三类产出，其中 Source Evolution 有 Tool 和 Method 两种子类型；不是
新增第四类学习权威。结果统一为不可变规范 JSON，状态 LEARNING_OUTPUT_PREPARED，
上下文 DATA，may_publish/may_authorize/may_execute/may_write_store 全部为 false。
返回的摘要只标识材料，不证明材料真实、不授予权限、不代表已写入记忆。

## 已落实的边界

当前学习卡使用完整内容 pin，不把旧 draft_sha256 误当作变更后卡片身份。
主体、学习 ID、活动作用域和完整卡片摘要必须匹配外部基准。已发布、丢弃或
未知状态不能当作新材料；Knowledge 改名不能带入 skill_spec 等能力结构。
普通正文提及 Skill/Tool/SKILL.md 不影响正常知识预览。

Method 沿用 P9 的规范 JSON 编译器和签名观察验证器，不另写批准规则。
过期基线、自洽但错误的图、签名替换、缺失或多余证据、候选源码漂移、版本
回退和方法步骤/路径冲突均拒绝。测试重新送入原 P9 审核器得到同一计划摘要。
本适配器没有审核者批准，也不把模拟证据当作原生 Git 发布证据。

Experience 沿用 P5 的成功/失败分池和 PROBATION 策略，不降低准入阈值。
归因由同一计划及 trace 重算；修改 trace 后保留旧 PASS 即使重新计算外层
哈希也会拒绝。精确源码、主体或隐私不匹配不能累计成功经验。已计入的观察
不生成第二份 Memory intent，新观察时间不得使前态时间倒退。旧观察 ID 的
原始字节与真实来源仍须由机器采集端核验，不能仅靠重复 ID 推断材料真实。

## 必须由 R2-B 接通的信任来源

本次的 expected_* pin、主体信息及观察者公钥是**可信调用方输入**。它们不是
模型可以自填的批准材料。R2-A 本身不从生产 GatewayStateStore、FactLedger、
World 或 Memory 读取这些基准，也没有被 Life 学习主流程实际调用。

后续必须在现有编排内读取真实学习卡、已登记计划、机器 Effect/Fact/P19/
Completion 证据、精确源码和 Memory 前态/父派生记录，再调用本适配器。
不得把模型给出的 observation 与同一模型给出的 expected hash 一起当作
机器验证，不得以此模块的存在宣称三类学习生产链已经全部接通。

本次只是准备，不把现有 migration_required 学习卡自动改成 published，
不写经验计数或 CURRENT，不重启旧完整 Skill/Tool 发布入口。R2-B 还需在
原存储事务/幂等边界处理提交、失败恢复和前态比较，并保留原授权路径。

## 实际验证

| 运行 | 结果 |
|---|---|
| 第一轮新增测试 | 46 passed / 2 failed；均为新夹具错误，日志保留 |
| 第二轮新增测试 | 47 passed / 1 failed；临时数据库文件后缀不符合原 Store 合同 |
| 修正后新增测试及进一步对抗检查 | 57 passed / 0 failed/errors/skipped |
| 最终扩大定向组 | 902 passed / 10 existing skips / 11 subtests passed；0 failed/errors |
| 最终输入清单 | 1,087 个 Python 源码/测试，运行前后完全一致 |
| 原 Source Authority | 17 authorities / 1 alias / 24 generated targets / 1 closed-world：PASS |
| 原生成源检查 | --check-committed：PASS；没有调用生成器写入 |

第一轮夹具把旧源码哈希改成了相同值，未形成真正漂移；另一个夹具遗漏了
LifeShadowStore.open 的既有必填参数。随后修正临时文件为 .shadow.sqlite3，
并使用真实 MemoryCoordinator API。没有修改任何原有测试或生产合同来放行。

57 个新增案例已包含在 902 项扩大组中，不能相加。扩大组包含 R1 冻结、
Knowledge/Memory、P5 经验、P8/P9、原完整 P19 Golden 目录等选定回归；不是
全仓 Python/Node 或真实模型任务。10 个原环境跳过和 5 条 Pydantic 警告保留。
本地环境为 Linux / Python 3.13.5 / pytest 9.0.2，pytest 154.34 秒。

测试使用真实临时 Git 对象库和实际临时 Life SQLite/MemoryCoordinator。
测试确认准备过程不写库，随后显式调用原 MemoryCoordinator 可提交 L3 DATA。
这只是适配兼容性验证，不代表生产自动接线；签名和观察记录是测试夹具，
不是正式独立观察、人的审批或真实模型完成证据。未读取/生成生产凭据。

最终 JUnit SHA-256：`4e46c6c92e1e16384dd043515aca7f04ab50a9b98c908ff043ad8791c897887a`。
最终日志 SHA-256：`d833398a6ec7ed529e7e737021bb43732851ebe16cd5b3dcc43399ee05194103`。
输入清单 SHA-256：`1daefb903df5d33a615b85368560ebec99fb42d8470bf71f1257ce995578560e`。

## 工作区、提交与 CI 边界

本地从已下载的原生 Git bundle 创建工作区，fsck 通过，checkout 为 ed0b48dd。
GitHub 实时比较 ed0b48dd..3b4d0af3 仅有四份文档/工作流差异，产品源码与
测试无差异；再与 R1 两端 1,085 项输入哈希逐个核对全部一致。本地测试使用
该源码加两个新增文件，不谎称在完整 3b4d0af3 Git checkout 上执行。
远端提交基于实际 3b4d0af3 树构建，保留四项后续差异，不上传旧整树覆盖。

本次只新增一个准备模块、一个测试文件及本文，更新阶段导航；在既有只读
定向工作流中追加本模块触发路径和 P5 经验回归范围。未减少原测试选择，
未改 Architecture/P14/P19、保护规则或运行时生成镜像。Verification Plane
仍为 1.8；原冻结清单、诊断指纹、Golden、权限和数据库版本字节不变。
远端 Python3.12 Windows/Ubuntu 结果按本次确切提交实际运行另行记录，不能
由本地成功推断。未创建临时写入工作流、未强推、未合并或部署。

## 接续与总进度

下一单 R2-B：从既有生产权威采集可信输入，并将本适配器接回现有 Life /
Gateway / Knowledge / P8/P9 / MemoryCoordinator 流程。首先确保经验输入
不是 LLM 总结冒充机器证据，并在同一存储边界实现幂等和前态比较。
然后进入 R3 旧记录迁移及使用遥测，最后 R4 固定候选全量验收。

P10 尚未完成或合并。P8/P9 实际任务、正式签审、版本切换和运维回退等债项
仍独立保留，不能被单元测试/工程合并替代。未进入 P11/P12/P13。
工程检查点仍为 10/18 = 55.6%，当前是第 11/18 阶段，不是产品验收率。
