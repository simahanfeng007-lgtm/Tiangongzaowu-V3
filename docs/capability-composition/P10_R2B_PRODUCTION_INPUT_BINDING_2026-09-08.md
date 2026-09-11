# P10 R2-B：生产学习输入与机器执行证据接线检查点

状态：**本次生产调用点已接入并完成本地定向回归；R2-B 尚未全部完成。**
尤其不能把机器证据审计当成 P5 成功经验准入或 Memory 已写入。未合并 main，未部署用户生产环境。

## 基线与 Windows 核验

仓库 `simahanfeng007-lgtm/Tiangongzaowu-V3`，工作分支
`codex/capability-composition-p10-life-learning-cutover-v1`。
父工程基线为 `79a4f8ba4719fbf4ebb72bce2f9b19944e203f22`；main 仍为
`b1ea3e9d511aae9cabcc816e071ad3da645ae753`。

本次重新读取 R2-A workflow `34176382522`，两端均已 SUCCESS。
Windows job `101906510346`：901 passed、11 skipped、11 subtests passed，零失败/错误。
Ubuntu job `101906510148`：902 passed、10 skipped、11 subtests passed，零失败/错误。
原始 Windows artifact `10037648825` SHA-256：
`a8d2a413e14625a8b166a34366ce826d9a0bb448327409d76d926d33bfea9b45`。
原始 Ubuntu artifact `10037462649` SHA-256：
`dd3f1be91a60a88aa738bb93551563881f41f0b82bf3c0c9e91a6052e0823a96`。
已核对两端 ZIP、HEAD、JUnit、日志、UPDATE 标志及全部 1,087 个输入哈希。
平台跳过不是通过；两端和子集结果不相加为独立任务数。该成功只覆盖 R2-A 提交。

## 本次接通的既有生产调用点

在原 `GatewayRuntime.start()` 安装一个内部绑定对象，通过原 typed Life wiring
接到学习准备回调，并用包装回调接续原 worker 的 `life_execution_commit`。
没有新 Runtime、HTTP/Tool 路由、Registry、学习状态机或 Memory 数据库。
测试实际调用 Gateway.start，检查已安装的回调，而不是只手工调用孤立函数。

### 当前 Life 记录 → Knowledge 原发布链

适配器只接收 Life/learning ID，从原 Life 实例读取当前学习卡、原 journal 和
配置的主体/工作区身份。检查 journal 的 valid 与 journal_head_signed，再将
卡片与最后一个已提交学习事件比较。只排除明确的恢复/UI 临时字段；未知字段
不自动忽略。模型不能同时提交一份卡片及其自行计算的 expected hash 作为权威。

原草稿创建、确认和发布入口现调用 R2-A 准备器。Knowledge 仍经过原确认、
风险检查、原编译/持久化及 Gateway 导入；未批准的高风险知识不能自动发布。
不因准备器结果带有自洽 hash 就重启 Skill/Tool 发布。R1 冻结继续生效。

准备结果以 `learning.output_prepared` 审计事件写入原签名 journal，幂等键绑定
完整材料，不另建发布记录表。无新材料的重复调用不重复追加；审计失败保留
已提交草稿，明确报错，原入口可重试。没有删除原卡、来源或失败证据。

### Source Evolution → 已有 World/Git 操作边界

学习卡只提供严格的引用选择项：当前 WorldState ID，以及 Tool 候选提交/Action
范围，或 Method 归档摘要。实际当前世界状态、Life/主体/工作区、仓库、工作树
以及观察者/审核者配置来自既有 P9 resolver，不从模型产物读取信任公钥。

Tool 复用 P8 原生 Git 候选读取；Method 复用 P9 归档重建、签名、当前头及
权威 Git 字节校验。自洽材料但缺少实际 Git 文件、跨主体/旧状态或额外 selector
字段都会拒绝。这里只验证并准备，不调用 World ingress 发布或换当前版本。
尚未配置 operator、缺少 selector 或 live frame 未跟上持久状态时明确等待。
上游实际生成 selector 的学习策略和自动完成签审发布仍须后续接线。

锁使用原 WorldStateStore 的事务锁；不在 Life 锁内获取 World Runtime 锁，
避免与 World 提交后调用 Life 的观察路径形成反向锁等待。新回归用例让另一
线程持有 World Runtime 锁，准备仍能完成；不是多进程锁或总体并发正确性证明。

### 真实 worker 终态 → 机器证据审计

包装器先调用原 `Life.commit_execution`。原执行提交成功后，才依据真实
request/run/generation 从现有 GatewayStateStore 读取已登记计划，并复用
原 CompositionStepExecutionCoordinator.finalize_plan 的 DAG/Fact/Object 校验。
它不会再次派发工具，不使用模型提供的 observation/PASS/expected hash。

采集实际 Effect、已校验 Fact batch/对象、原 CompletionDecision 和 P19
权威 readiness；检查作用域、终态与事实引用。原 Life execution 必须匹配
原 journal 中的 execution.commit，完成时间不得早于机器最终记录。

结果以 `learning.execution_evidence` 写入同一个 Life journal，包含机器摘要
及原提交事件引用。重复终态回调幂等；后置审计失败不抹除已经持久完成的执行，
返回 LEARNING_EVIDENCE_DEFERRED，明确重复回调可重试，但没有另设恢复 worker。
未登记 composition 的历史任务继续返回原执行结果。

## 仍然不能写入成功经验的三道边界

机器审计结果始终 `may_write_memory=false`，保留下列明确阻塞项：

- P5 的真实 provenance / observation 构建与归因准入尚未完成。
- 当前源码重新验证尚未闭环，不能仅凭旧运行计划就累计新版本成功经验。
- Memory 父派生记录读取、聚合前态比较和原事务中的幂等写入尚未完成。

缺少 P19 或 sealed Completion 时再增加对应阻塞。即使测试经过真实
CompletionGate 得到 sealed decision，也不能因此绕过上述三个要求。
本轮没有调用 MemoryCoordinator 写 L3 成功经验，没有把 R2-A 准备结果假称
为生产记忆。R2-B 下一步必须补完这条提交链，之后才可进入 R3/R4 收口。

本轮也没有增加自动扫描过去漏审计执行的重启任务；显式重复回调可恢复
不等于所有历史/兼容执行尾部都已接入。老记录迁移、长期故障恢复与真实工作负载
遥测继续留待验收，未知归属数据不能自动删除。

## 本地验证、失败记录与权限版本

最终扩大回归：**937 passed / 10 existing skips / 11 subtests passed，零失败/错误**。
包含 35 项新增测试及原 R1、R2-A、Life/Knowledge、P5、P8/P9、完整 P19 Golden
目录；67 个选择项，其中一个是目录。1,089 个 Python 输入运行前后和提交后
原生回读完全一致。Linux / Python 3.13.5，pytest 295.74 秒；5 条既有 Pydantic
警告保留。不是全仓 Python/Node、Windows 原生产品或真实模型任务验收。

新用例使用实际临时 Git、SQLite、journal、Fact/Object 和 Gateway/World 实例。
backend 响应及签审材料仍为测试夹具，没有生产审批密钥或真实模型调用。
已验证实际 Knowledge 确认发布调用点、原任务完成包装、记录篡改、假 journal
验证、写入失败后的重试、旧 World/Git 拒绝、原始事实篡改和未满足经验条件拒绝。

原始失败均保留。初期 21 项新用例有 5 项失败：夹具错误地将 A0 知识当成
等待确认的预览，以及预期直接抛出异常而忽略原 HTTP 包装返回。扩展 29 项时
一项仍使用错误返回预期；新增确认测试又用错已有路由，均修正新夹具而非降门禁。
第一次扩大组为 933 passed、2 failed、10 skipped：两个终态夹具用了 2000ms
却引用真实时钟的机器 Fact。改为实际 final.completed_at_ms 后再加正反边界
用例完成最终回归；生产时间检查没有放松。两次外部超时的局部日志保留为 partial。

Verification Plane 声明 **1.9**，在原 AUTHORITY_MAP 中说明收紧的来源/调用点。
原冻结守卫先拒绝未同步变化，再用原生成流程同步 freeze 和独立 fingerprint；
最终验收清除 UPDATE_FREEZE/UPDATE_FINGERPRINT/UPDATE_GOLDEN。
原 92 条冻结文件全部保留，增加 typed wiring、R2-A preparation、R2-B binding
三项为 95 条。不是新增三个独立运行时权威。原 13 份 Golden baseline、
CompletionGate、权限、RepairPolicy、source-ownership 和数据库 schema 未改。
3 个 Life 镜像与生成标记由原官方生成器生成，--check-committed 通过。
Source Authority：17 authorities / 1 alias / 24 generated targets / 1 closed-world。

两个新审计事件登记在原 journal replay 表。数据库 schema 未变化，但旧二进制
不认识这些事件；部署前备份，回退需要兼容重放或审查后的备份恢复。本次不迁移
用户实际数据库。

## 可核验提交与证据

本地基于已核验的原生 Git bundle ed0b48dd 加 R2-A 两个源码/测试，先核对
远端 79a4f8ba 两端 1,087 项字节一致。不是冒称完整 79a Git checkout。
15 文件补丁在独立干净原生 worktree 做 --check/--index 应用并核对全部 blob。
直连 DNS 不可用时，通过分支限定的临时开发任务传输 hash-pinned 补丁，
没有提交重建旧整树覆盖后续修改，也没有管理员绕过或强推。

补丁 SHA-256：`68fa2f75e63f43ce4022f985e014da1dd2aaffb60bca945f56c52e6f87617373`。
实际源码提交：`30e8927ed4de3adccb49fef4daf7efccab26d107`。
临时任务 `34189074809` 的产物 `10041542195` 外层 SHA-256：
`58ab9f295d17c58808703d488aacd280e2891960744625cc6cd7e48a69bd90cb`。
下载后重新 clone 该原生 bundle，fsck、父提交和所有 15 文件以及 1,089 项测试
输入核验一致。临时任务不运行产品测试，不能当成通过记录；收尾将其删除。
收尾只改文档和现有只读 focused gate 的本模块触发路径，不改变已测产品字节。

最终 JUnit SHA-256：`7d7287853835e99fce3dc0286ef278cf33d9ed06ed0a3335605b95342e8ab8ca`。
最终日志 SHA-256：`32635e42f666eeef087a01ad014089005b00842c3c8eb7a100abd724595ea612`。
输入清单 SHA-256：`6dc4efb892bfa7370e2314e6941d204c2615ea49dde56f67c37652b5592671e8`。

本检查点的 Windows/Ubuntu 结果按最终提交另行记录，不沿用 R2-A 绿灯。
未修改既有 Architecture/P14/P19 门禁；未合并 main，未进入 P11。
P8/P9 实际任务、正式签审、旧打包补丁及部署回退等债项仍单独保留。
合并工程检查点仍为 **10/18=55.6%**，位置为 P10 第11/18阶段、R2-B继续中。
