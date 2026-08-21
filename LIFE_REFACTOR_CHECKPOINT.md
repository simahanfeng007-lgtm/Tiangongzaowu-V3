# 天工造物 v3 生命架构改造断点

> 本文件是压缩恢复入口。恢复时只读本文件，再读总计划中“当前阶段之后”的章节。  
> 权威总计划：LIFE_CAUSAL_ARCHITECTURE_REFACTOR_PLAN.md  
> 当前状态：P0–P11 已完成，改造计划闭环  
> 最近更新：2026-07-17

## 已完成：P0 冻结基线与源码所有权

已完成内容：

1. 建立 baselines/life-runtime-p0.json，绑定：
   - 7175 正式 EXE。
   - Python 3.14 runtime。
   - 11 个冻结 life_*.pyc。
   - 可读 bootstrap 和 runtime fixes。
2. 建立 source-owned 的 src/life_service P0 骨架。
3. 骨架默认只能 status_only：
   - 不监听端口。
   - 不取得 writer lease。
   - 不运行 scheduler。
   - 不读取或修改真实生命数据。
   - 不代理执行。
4. 建立 app/life-service/runtime314/life_service 精确部署镜像。
5. 新增 scripts/sync-life-source.ps1，负责 source/runtime 字节镜像检查。
6. 新增 scripts/test-life.ps1，自动设置 PYTHONPATH，不依赖操作者环境。
7. 发布清单新增 life-source，并把 src/life_service 纳入 Python 包。
8. 增加 UTF-8 与关键中文上下文标签门禁。
9. 桌面完成路径接入与微信/飞书相同的机器 CompletionGate；尚未在 P0 持久化 CompletionDecision，持久化属于 P3。

## P0 质检证据

针对性生命门：

    .\scripts\test-life.ps1

结果：33/33 通过。

仓库总门：

    .\scripts\check.ps1

结果：

- JavaScript syntax：39 个文件通过。
- Python syntax：通过。
- unittest：526/526 通过。
- git diff --check：通过。

P0 关键不变量：

- 冻结字节码未被修改。
- 真实生命数据未被读取或迁移。
- 新生命源码尚未接生产流量。
- 旧 7175 仍是唯一 writer。

## 已完成：P1 公共契约与影子存储

已完成内容：

1. 新增 src/contracts/life.py：
   - LifeEventEnvelope
   - ViabilityState
   - AppraisalVectorV3
   - TaskContinuityCapsule
2. 新增 src/contracts/causal.py：
   - CausalEpisode
   - CausalHypothesis
   - evidence/relation/status 枚举
3. 新增 src/contracts/agency.py：
   - ActionImpact
   - AgencyDecision
   - ReflectionCard
   - CapabilityProfile
4. 所有权威分数使用 0–1000 整数，不允许浮点。
5. 将新契约加入 schema bundle 和公共导出。
6. 新增 src/life_service/store.py：
   - strict SQLite
   - WAL、FULL、foreign_keys
   - schema version 和迁移哈希
   - 仅临时影子库，禁止真实数据路径
7. 新增确定性数学与事件重放器。
8. 新增契约、数学、重放、并发、迁移和损坏测试。
9. 新增 23 张严格影子表、不可变/幂等写入、schema 迁移账本、payload 损坏检测和单活动胶囊约束。
10. source/runtime 生命源码镜像扩展到 8 个文件。

## P1 质检证据

- 同一事件序列重放摘要完全一致；乱序、断链、writer epoch 回退全部 fail closed。
- 非有限数、浮点、越界、重复集合、非法时间全部 fail closed。
- 影子库损坏、payload 篡改和 schema 漂移全部 fail closed。
- A5 永不执行；A4 必须确认；能力熟练度与风险分离。
- 不接 7175 网络、不取得 writer lease、不写真实数据。
- `scripts/test-life.ps1`：63/63 通过。
- `scripts/check.ps1`：39 个 JavaScript 文件语法通过，Python 语法通过，unittest 556/556 通过。
- `scripts/sync-life-source.ps1`：8 个文件一致。
- `git diff --check`：通过。

## 已完成：P2 source-owned 7175 兼容影子服务

已完成内容：

1. 身份、Soul、writer lease 和事件 head 只读适配。
2. 现有 Life API v2 只读兼容，不增加行为。
3. 旧 projection 与新 projection 对比器。
4. 旧 memory/context 只读解密适配。
5. 单独的 bearer 鉴权 loopback 影子端口；7175、scheduler、writer lease 和所有副作用永久禁用。
6. 只接受 manifest 绑定、原子捕获、SQLite 已检查点的离线 snapshot_copy；不发现或读取真实数据路径。
7. 记录字段差异、缺失语义和不可还原信息。
8. 原协议验证 Ed25519 身份/Soul/事件链和 AES-256-GCM memory/context AAD，不以字段近似代替兼容。
9. 现有 Gateway LifeClient 已直接通过影子 API 固定同一权威快照。

## P2 质检证据

- 核心身份、Soul、事件 head、记忆数量、上下文 hash 一致。
- 损坏签名、断链、密文篡改、重复 JSON 键、非原子快照、WAL/SHM、树漂移全部 fail closed。
- 影子故障不写快照，不连接旧 7175；不存在双写者。
- 所有写路由统一返回 405；只读查询前后快照 tree hash 不变。
- `scripts/test-life.ps1`：76/76 通过。
- `scripts/check.ps1`：39 个 JavaScript 文件语法通过，Python 语法通过，unittest 569/569 通过。
- `scripts/sync-life-source.ps1`：10 个文件一致。
- `git diff --check`：通过。

## 已完成：P3 统一事件总线、完成语义与连续性胶囊

已完成内容：

1. Gateway outbox 新增 LIFE_EVENT，接受事件经 ObjectStore、object_owners 和 outbox 原子绑定；缺失投递可按源序列恢复。
2. source-owned 7175 影子 ingest 实现 Ed25519 源签名验证、严格连续 offset、幂等 receipt、dedupe 和单 writer 事件签名。
3. Gateway schema v12 新增 completion_decisions、request_capsules、object_owners，并保持 v6–v11 历史迁移可验证。
4. 桌面、微信、飞书与自主任务统一使用 CompletionGate；传输失败也不能绕开机器完成证据。
5. 正常完成先写 CompletionDecision 和 TERMINAL_RESULT，再改变请求终态；终态响应丢失后的重试复用原胶囊。
6. 执行/交付异常写 WORKING_CHECKPOINT；上下文编译发生裁剪时写 COMPRESSION_CHECKPOINT。
7. 正常工作内容去重保留，压缩后只保留恢复所需因果状态；终态移除工具过程、活动计划、待执行副作用和 next step。
8. 旧上下文投影与持久胶囊投影并行比较，明确保持 model_input_switched=false。
9. 生命契约根模型增加到 71 个；生命影子库 schema 升级到 v2；source/runtime 镜像增加到 11 个文件。

## P3 质检证据

- 崩溃发生在远端 durable commit 后、响应返回前时，重试不重写事件、不乱序；被拒绝的 Gateway 事件不制造 source offset 空洞。
- 伪造签名、序列跳跃、payload 篡改、终态冲突和未知副作用全部 fail closed。
- 桌面终态持久化顺序、自主任务完成/缺证据、微信/飞书成功与歧义、压缩回调、历史 schema 迁移均有测试。
- `scripts/test-life.ps1`：107/107 通过。
- `scripts/check.ps1`：39 个 JavaScript 文件语法通过，Python 语法通过，unittest 582/582 通过。
- `scripts/sync-life-source.ps1`：11 个文件一致。
- `git diff --check`：通过。

## 已完成：P4 因果记忆、语义压缩与保留策略

已完成内容：

1. 从旧记忆建立 causal node；普通关系原样迁移，绝不自动升级为 causes。
2. 实现候选因果边、反证、替代解释和 revision。
3. 实现 CausalContextPack 有界邻域检索。
4. 上下文优先当前目标、硬约束、断点和高价值因果链。
5. 引入 tokenizer-aware 预算和 75/85/92 水位。
6. 胶囊替换前做完整性验证，失败则继续使用旧投影。
7. ObjectStore GC 仅做 dry-run。
8. 隐私删除贯穿索引、胶囊、上下文和 payload key。

P4 质检证据：

- 旧记忆迁移生成保护载荷、记忆断言与 causal node；包括名为 causes 的旧弱关系也只保留为 ordinary relation，不生成因果事实。
- 100k、500k、1000k token 等价长链均保留当前目标、硬约束与 next step；75/85/92 水位使用精确整数与可插拔 tokenizer。
- 上下文包绑定已持久化胶囊并加密；摘要、密文、索引或成员篡改均 fail closed，失败继续使用旧投影。
- 隐私删除销毁全部历史 payload key、关联 causal node 与 context pack key，清除搜索索引，只留无明文墓碑。
- ObjectStore GC 仍为 dry-run；owner、revision、legal hold、未过期及共享活跃内容从不标记。
- 生命影子库 schema v3；契约根 77 个，schema hash `cd2c2e142d75637b178cd3c2cc7d8440db7d923b44c0fb37a8cc7d492fd28343`。
- `scripts/test-life.ps1`：127/127 通过。
- `scripts/check.ps1`：39 个 JavaScript 文件语法通过，Python 语法通过，unittest 602/602 通过。
- `scripts/sync-life-source.ps1`：13 个文件一致。
- `git diff --check`：通过。

## 已完成：P5 情感外部输入与表达案例

已完成内容：

1. 统一 LifeEvent intake、appraisal gate、严格源 offset、幂等 receipt 和确定性重放。
2. 接入任务成功/失败、系统健康/降级/恢复的机器固定映射，模型不能自行夸大强度。
3. 新闻、天气必须有显式订阅、授权位置、来源策略、主题或位置绑定。
4. 实现可信度与相关性限幅、重复去重、指数习惯化、乱序拒绝和提示注入拒绝。
5. 建立 12 情感 × 3 强度 × 6 触发来源的 216 案例、648 个中文表达位；每轮只检索 3–8 个案例。
6. 情感契约在结构上只能影响注意与表达，禁止修改事实、权限或声称虚假经历。
7. 生命影子库升级到严格 schema v4，共 39 张表；契约根增加到 83 个。

## P5 质检证据

- 同一事件序列在两个独立存储中产生完全相同的情感状态。
- 假新闻、未验证源、提示注入、错误天气位置不改变状态；重复新闻最终习惯化到零，重复投递不增加 revision。
- 低强度只轻微改变措辞，高 concern 仍受边界约束；表达案例不能改变事实、权限或虚构体验。
- 契约根 83 个，schema hash `1a35923dd6d2318142212cb423ac1364e16859b58dcff973e303f26bc2485a42`。
- `scripts/test-life.ps1`：139/139 通过。
- `scripts/check.ps1`：39 个 JavaScript 文件语法通过，Python 语法通过，unittest 614/614 通过。
- `scripts/sync-life-source.ps1`：15 个文件一致。
- `git diff --check`：通过。

## 已完成：P6 唯一 PolicyEngine 与 Omni 收口

已完成内容：

1. 建立了确定性 ActionImpact、机器 risk floor 和唯一 PolicyEngine；身份、Soul、记忆密钥、策略与核心代码影响不能被模型降权。
2. ExecutionTicket v2 精确绑定 decision、impact、confirmation、Skill activation、workspace、对象授权、副作用与资源信封。
3. 生命 scheduler 不再获得 7174 执行凭据，只能向固定 7184 提交 ActionIntent；冻结候选暂以安全拒绝收口。
4. Omni 的绝对路径、Shell、Python 均默认关闭；模型传入 `confirmed=true` 和旧 CLI 默认 Python 旁路已删除。
5. 所有 Omni 动作均必须消费 Gateway 签名的 60 秒内 grant，绑定 ticket、action、args、workspace、principal、Skill、manifest、epoch 和一次性 nonce。
6. 从当前 action registry 动态枚举 283 个 executable actions；权限分布为 A0=52、A1=6、A2=90、A3=121、A4=14。
7. 实现持久化 OperationalTrustStore，验签执行 PREPARE→ACTIVATE→RETIRE，紧急撤销必须使用离线恢复密钥并严格 epoch+1。
8. 契约根增加到 90 个，ExecutionTicket v2 的有意破坏边界与 P5 v1 历史投影分开校验。

P6 质检证据：

- 无 Gateway 签名 grant 时 Omni 不会初始化 BodyRuntime，因此普通写入也无法绕过 authority。
- ticket/grant 篡改、重放、跨 action/args/workspace/principal/Skill/epoch 使用全部 fail closed。
- 路径对抗覆盖绝对路径、`..`、UNC、device path、Unicode NFD、symlink/reparse 和 hardlink。
- 契约根 90 个，schema hash `a13b5fca4bb6925e9829416ff1c913ae813483a3fbe00a7445dd3fb85f352ec7`；action registry hash `51a980f3b58cc296524372136524b5e7bf104394049fed50e1c81f10259fef29`。
- `scripts/test-life.ps1`：159/159 通过。
- `scripts/check.ps1`：39 个 JavaScript 文件语法通过，Python 语法通过，unittest 634/634 通过。
- `scripts/sync-life-source.ps1`：16 个文件一致；Python 3.12 冻结 Omni 镜像已重建且两份 hash 一致。
- `git diff --check`：通过。

## 已完成：P7 因果分级能动性

已完成内容：

1. 建立来源绑定的 ViabilityObservation 与确定性 ViabilityState 采集器；按 evidence class 机器限幅置信度，拒绝未来、缺维度、重复身份和跨生命输入。
2. 建立严格 ActionCandidate；模型不能提交 risk、confirmed 或授权字段，ActionImpact、risk floor 和 utility_lcb 全由机器计算。
3. 同一动作的稳态价值按目标带缺口、预测 delta 和来源置信度计算；关键风险维度取最大值，不能被平均值掩盖。
4. 实现 observe、reflect、ask_user、wait、execute、reject 状态机，并纳入 L0-L5、暂停、关停、隐私、时间窗、动作/工作区范围、日预算、资源预算、频率、冷却与 Skill activation。
5. 低置信动作降级为观察、询问或 A0/A1 最小实验；A5 永不执行，L5 永不自主执行。
6. 生命影子库升级到 schema v5、43 张严格表，持久化 observation、candidate、policy 和 usage revision 链。
7. 执行决策与预算消耗同一 `BEGIN IMMEDIATE` 事务提交；共享旧快照的第二个 Agent 必须 CAS 失败，不能超额。
8. 契约根增加到 94 个，schema hash `4d16ad4e6364bc301b01554514f1a610507c8bacde24eee376fe26f7b60ff10a`。

P7 质检证据：

- 模型伪造风险/确认、候选/impact/生命/策略/hash 交叉绑定、过期候选和未知范围全部 fail closed。
- 稳态缺口互换时动作排序按对应 viability delta 可解释互换；关键风险从 0 到 1000 单调不降。
- 关停、隐私锁、暂停、时间窗、预算、资源、频率、冷却、Skill 缺失和低置信均阻止普通自主执行。
- 两个 Agent 基于同一预算快照竞争时仅一个原子提交成功；重试幂等，另一个明确 compare-and-swap 失败。
- `scripts/test-life.ps1`：168/168 通过。
- `scripts/check.ps1`：39 个 JavaScript 文件语法通过，Python 语法通过，unittest 643/643 通过。
- `scripts/sync-life-source.ps1`：16 个文件一致；`git diff --check` 通过。

## 已完成：P8 因果反思与能力学习

已完成内容：

1. 建立 EpisodeOutcomeEvidence；终态 outcome、CausalEpisode revision、ReflectionCard 和问题决策在同一事务提交。
2. prediction error 使用预测成功率与机器终态质量计算；失败按输入、推理、工具、环境、策略、权限、陈旧上下文、偏好和未知分类。
3. 成功若缺受支持因果机制、存在反证或替代解释，只按相关/可能巧合处理，不生成合格成功证据。
4. 失败反思保留反事实动作和最小实验；方法正确但环境失败不错误惩罚能力，归因到能力的验证失败才进入失败样本。
5. 用户问题只在价值信息、偏好不确定或风险达到阈值时产生；同一偏好域有 24 小时持久冷却。
6. 能力学习使用合格成功/失败、多样独立 context fingerprint、整数 Hoeffding 95% 单侧置信下界和校准误差。
7. 单次成功、相关性成功、同一场景重复和失败均不能发布能力；候选按 capability/version 合并并设 7 天升级冷却。
8. A3/A4 要人工审核，A5/核心代码要求 CORE_REVIEW；低风险达到门槛也只进入 SANDBOX 候选。
9. 验证回归生成 CapabilityRollbackRecord、熟练度下界归零，并通过 invalidation overlay 使相关 context pack 与 Skill activation 失效。
10. 生命影子库升级到 schema v6、48 张严格表；契约根增加到 99 个，schema hash `9e9f02d464b87f7ad7ee14dd7119c011802535c425a98f5350f91ded836a1a35`。

P8 质检证据：

- 终态重试幂等；episode/outcome/reflection/question 不会部分提交或一对多分叉。
- 单次成功 lower bound 为零；10 个多样低风险成功仅进入 sandbox，A3 进入人工审核，核心代码 30 个样本仍只进入核心审核。
- 同一 context 的重复成功不满足多样性；增加能力归因失败只会降低均值，绝不提高熟练度。
- 回滚缺验证失败证据或引用不存在的 context/Skill 时 fail closed；合法回滚 profile revision、record 和 invalidation 同事务提交。
- `scripts/test-life.ps1`：175/175 通过。
- `scripts/check.ps1`：39 个 JavaScript 文件语法通过，Python 语法通过，unittest 650/650 通过。
- `scripts/sync-life-source.ps1`：18 个文件一致；`git diff --check` 通过。

### 补记：P8.1 反思链生产接线（2026-08-21，P8 收尾补课）

P8 建成了完整的因果反思与能力学习合约层，但生产运行时零调用——反思链从未
在真实任务/能力执行中运转。P8.1 补上完整链（评价驱动 QC，详见
`docs/repair-logs/2026-08-21_P8.1_reflection-lane-production-wiring.md`）：

1. store 增 5 个只读方法（零 schema 变更）；`episode_builder.py` 纯函数构建
   预测快照/链形事件/OPEN episode/结果证据/影响面/九类失败映射。
2. 任务源四挂点：worker 执行前以活动真实完成历史做确定性预测并 OPEN
   episode 落账（先于执行、事后可审计）；完成/异常原子闭环反思；陈旧恢复
   把孤儿 OPEN episode 以 ABORTED 收尾。
3. 能力源两挂点：pointer.health 成功率做基线预测 + 影响面 + OPEN；执行后
   闭环反思 → 能力证据 → 学习（仅 eligible 证据；correlation_only 成功按
   诚实基线不进学习）→ verified 失败累积 ≥3 且 A0-A2 自动回滚（熟练度归
   零，pointer 不动，双体系互斥）。
4. 消费面：面板新键 `reflection_cards`；能力 overlay 按双体系综合分
   `max(health_score_milli, proficiency_lower_bound_milli)` 排序；activity
   scope 注入最近 5 条反思摘要供决策模型感知。
5. 熔断 `TIANGONG_LIFE_REFLECTION_CHAIN=0`；所有链操作在 runtime 锁内，
   失败 journal 不重试；新 journal 类型由权威重放静默跳过。
6. 同批评价驱动修复：F1 proactive 忽略率门禁（消费 replied 信号）、F2
   live 模式 UI 开关、F4 motivation_drift 接入自由行动排序、F5 能力正向
   强化与闲置淘汰、F6 三调度器子预算记账。

质检证据：`test_episode_builder.py` B1-B5、`test_life_reflection_chain_wiring.py`
T1-T4/C1-C3/P1/P2/S1/S2 全绿；三不碰守卫（不进记忆晋升/不产生用户提问/
不进 proactive）由测试锁死。

## 已完成：P9 Skill 双通道合一

已完成内容：

1. 建立唯一 SkillAuthority；系统推荐与模型 route/list/get/read 共用同一不可变 catalog、capability manifest 和匹配算法。
2. Gateway store 升级到 schema v13，严格持久化 SkillSelectionRecord、SkillActivationGrant 及 activation→ExecutionTicket 绑定。
3. 7184 新增仅后端 token 可访问的内部 Skill API；拒绝 Origin、重复 JSON 键、非有限数、截断、错误 Content-Type、未知字段和非 POST 方法。
4. Omni skill_router 删除本地目录、评分和文件读取，成为固定 127.0.0.1:7184 的薄客户端。
5. activation 精确绑定 selection、generation、principal、catalog hash、capability manifest、Skill 内容 hash 和完整 required actions。
6. PolicyEngine 同时校验 Skill catalog 与 capability manifest；目录漂移、缺动作、跨代、跨主体和过期 activation 均 fail closed。
7. skill.step.check 只从 Gateway FactLedger 计算完成、失败、待办和下一动作，忽略模型伪造的 completed_actions、QC 或 artifact 声明。
8. Python 3.12 frozen/legacy Omni 镜像已由可读源码重建，两份 skill_router 字节码 hash 一致。

P9 质检证据：

- 系统/模型双通道候选、兼容性和目录 hash 差分为零；候选永不等同激活。
- activation 与 ticket、generation、principal、manifest、catalog、action 全量交叉绑定；缺动作 Skill 两路均不可激活。
- Gateway HTTP 兼容装配回归已修复；无 orchestration 或兼容测试运行体不会因 Skill API 装配崩溃。
- 契约根 99 个，schema hash `d1ff912e165d67757cc436e1bd3afd183f8ac275f2290a2039522d133b7cb420`；Gateway store schema v13。
- `scripts/test-life.ps1`：180/180 通过。
- `scripts/check.ps1`：39 个 JavaScript 文件语法通过，Python 语法通过，unittest 650/650 通过。
- `scripts/sync-life-source.ps1`：18 个文件一致；`git diff --check` 通过。

## 已完成：P10 上下文原子接口与前端

1. 新增严格 compile-and-authorize API；Gateway 激活请求时只进行一次生命服务调用，同时固定身份、Soul、上下文和授权 revision。
2. 首次对话无需 latest_context；上下文包、授权收据和 revision vector 在同一事务提交，提交前 revision 漂移即 fail closed。
3. LifeSnapshot 扩展 causal、viability、policy、reflection、capability revision 与授权绑定；冻结 7174 只消费 Gateway 内容寻址对象，不再二次编译或读取生命服务。
4. 影子库升级到 v7、49 张严格表；v2–v6 迁移保持历史数据和签名不变。
5. 前端建立来源门控 view model；缺 revision/source 的旧投影只显示迁移空态，不自行推导事实或跳过原因。
6. 用户头像、callsign 和 `user:primary` 关系身份统一；缺图或加载失败使用 callsign 首字，不再固定显示“你”。
7. 生命卡片增加 container query、长文本折行、窄屏和高缩放约束，覆盖自主意志、反思、能力、日程、设置与身份卡片。

P10 质检证据：

- 首次消息、原子重试、跨主体重绑、revision 漂移、事务故障、内容对象篡改和旧库迁移全部有 fail-closed 测试。
- 契约根 101 个，schema hash `0b396d2526c20002a978a18b4699d19ee2b80ae568e27f11a87d350db5902bb6`；生命影子库 schema v7。
- `scripts/test-life.ps1`：190/190 通过。
- `scripts/check.ps1`：40 个 JavaScript 文件语法通过，Python 语法通过，unittest 661/661 通过。
- `scripts/sync-life-source.ps1`：20 个文件一致；`git diff --check` 通过。

## 已完成：P11 影子迁移、切流、发布与回滚演练

已完成内容：

1. 对旧 journal、memory、context 和 capability 建立 COW 导入；完整验证身份、Soul、事件链、memory/context 密文与旧 writer authority，不修改旧快照和历史签名。
2. 旧记忆迁入受保护 overlay；召回投影自动脱敏凭据。旧上下文只保留有界的最终结果或断点状态，不导入工具参数、工具输出、系统提示和过程噪声。
3. 最终 delta 必须精确延续旧 sequence/hash/identity/Soul/epoch；非上下文语义变化没有对应因果事件时拒绝导入。
4. 建立投影、情感、召回、上下文、决策和性能比较证据；迁移期效果与 scheduler 始终关闭，采用旧基线与新 overlay 双读、新 overlay 单写。
5. drain 要求 scheduler pending、in-flight 均为零且旧 writer 已停止；Ed25519 handoff 必须 writer epoch 精确加一，续租必须同 epoch 且绑定上一 permit。
6. source-owned 服务只允许绑定 loopback 7175，逐请求验证 writer lease、时钟偏差和私有 token；首次消息仍走一次原子 compile-and-authorize。
7. 切流后配置缺失、产物不完整或验证失败时只启动不可写的 legacy compatibility fallback，禁止旧 writer 静默恢复。
8. 根级 trust.json 固定切流公钥 hash；不可变旧基线持久复制到 install root，runtime 不依赖临时 snapshot 路径。
9. 全新安装、覆盖安装、升级、恢复和回滚均验证 writer stopped、release hash、overlay identity 与 epoch；升级前后新增数据在回滚时继续保留。
10. 回滚必须以新 epoch 和精确 compatibility replay 清单签名，不能丢弃旧服务无法理解的事件，也不能形成双 writer。
11. source、runtime314、contracts、bootstrap、app/main.js、release manifest 与运维 CLI 已完成代码级同步；全程未操作桌面 GUI 或真实生命数据。

## P11 质检证据

- P11 专项对抗测试：12/12 通过。
- 生命链门：202/202 通过。
- 完整仓库门：40 个 JavaScript 文件语法通过，Python 语法通过，unittest 673/673 通过。
- source/runtime 精确镜像：life 22 个文件、contracts 26 个文件；嵌入式 Python 可独立导入生产服务与契约。
- life source tree SHA-256：`2743c150ed220729bcfe2f9f9aca3ec6d20191e4fe1df905d3bc89c0d86fb73c`。
- contracts source tree SHA-256：`80cb60c30f7dba10a1c14322d204fda93ffd3ba1ebe8c8935cc467b9f8ccb0f9`。
- P11 source release manifest SHA-256：`1ef133069e1fc40f59ea8c0d5dd3b29f0b3500fea1a23030eb1cb37a9faa3b33`；life-source 发布树 SHA-256：`9c9b9ff850c97b8b959211266b7194869c9b151daae43039fc856adeea5422ee`。
- `git diff --check`：通过。

## 当前阶段

P0–P11 全部完成；没有剩余改造阶段。生产切流仍必须由运维流程提供真实 drain evidence、签名 permit 和受信任安装根，不允许测试或源码状态自动接管真实 7175。
