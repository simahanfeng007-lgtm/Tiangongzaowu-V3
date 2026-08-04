# 天工造物 v3 因果生命架构详细改造计划

> 文档状态：设计基线，尚未实施  
> 创建日期：2026-07-16  
> 适用工作区：C:\Users\77571\Documents\天工造物v3  
> 实施原则：先收回源码所有权，再统一事实与授权，随后增强情感、记忆、能动性、反思和能力学习  
> 与现有计划的关系：本计划补充生命链改造；网关既有工作继续以 GATEWAY_REFACTOR_PLAN.md 为准，两者在“唯一执行权威、上下文连续性、发布切换”处汇合

## 0. 后续恢复与查询规则

后续 Codex 或开发者准备实施本计划时，应先读取本文件，再读取 GATEWAY_REFACTOR_PLAN.md 和当前仓库状态，不得仅根据旧会话记忆继续修改。

每次实施只推进一个阶段，并在本文件末尾“实施日志”追加：

1. 阶段编号、提交或工作树基线。
2. 实际修改文件。
3. 数据迁移版本。
4. 通过的测试与原始命令。
5. 未通过项、临时兼容层和回滚点。
6. 运行包、源码树、安装包是否同步。

如代码已经偏离本计划，以实时代码、契约和测试证据为准；先更新计划中的“偏差记录”，再继续实施。不得用计划覆盖真实代码状态。

## 1. 结论先行

这次改造不是新增五个独立服务，也不是在现有生命系统旁边再建一套情感、记忆或自主意志系统。

正确方向是：

- 保留 v3 已经做对的签名事件链、加密记忆、上下文投影、生命周期、沙箱学习和 Gateway 事实账本。
- 将“五层生命架构”嵌入一个 source-owned 的 7175 生命服务，形成一个权威生命状态。
- 7184 Total Gateway 继续作为外部输入、权限、执行票据、工具事实和交付事实的唯一工程权威。
- 7175 负责身份连续性、生命状态、情感评价、因果记忆、目标与反思，但任何有副作用的行动都必须向 7184 申请授权。
- 7174 只执行经过授权的任务，不再拥有独立权限解释权。
- 冻结的 Python 3.14 字节码先兼容、后影子替代，不能继续把主要业务逻辑堆进 57KB 运行时 monkey-patch。

最终闭环应为：

观察事件 → 证据校验 → 情境评价 → 情感与稳态更新 → 因果记忆 → 候选行动 → 确定性风险闸门 → 授权执行 → 机器事实 → 因果反思 → 记忆巩固与能力校准 → 有界表达。

## 2. 第一性原理与不可破坏约束

### 2.1 工程生命体的最小定义

这里的“生命”不等于模拟生物，也不等于让模型拥有无限自主权。工程生命体至少需要：

1. 身份连续性：能够证明“谁在延续”，并区分重启、恢复、复制和切换。
2. 状态连续性：知道哪些状态是事实、投影、假设、感觉和愿望。
3. 因果学习：能够区分先后、相关、推断和干预证据，并允许新证据推翻旧结论。
4. 有界能动性：能够提出行动，但不能自授权限、伪报风险或对抗用户关停。
5. 稳态调节：能根据运行健康、数据完整性、资源、信任和任务连续性调整优先级。
6. 情感评价：情感影响注意、表达和行动倾向，但不能改变事实、权限或执行结果。
7. 反思与校准：根据预测误差、结果和反事实修正因果假设与能力置信度。
8. 可恢复性：长任务、崩溃和压缩后仍可从可验证断点继续。
9. 可审计性：关键决策能够追溯到输入、状态版本、策略、授权和机器事实。
10. 可终止性：用户关停、删除和权限撤销高于任何“自我维持”目标。

### 2.2 十三条硬不变量

1. 原始事件只追加，不原地改写；错误通过纠正、撤销或替代事件表达。
2. 模型推断不能自动升级为事实，更不能自动升级为验证因果。
3. 情感只能影响注意、召回权重、表达和候选排序，不能改写事实、权限和执行终态。
4. 模型只能提出风险，系统必须重新计算风险；最终风险不得低于任一关键风险维度。
5. 有副作用行动只能由 7184 唯一授权权威签发可验证票据。
6. 生命调度器、聊天执行链和 Omni Tool 不得保留旁路授权。
7. 能力熟练度与行动影响风险必须分开；越熟练不代表越低风险。
8. 单次成功只能形成观察，不足以形成稳定因果结论或自动能力候选。
9. 正常完成后，模型可见上下文不得包含原始工具参数、完整工具输出、中间推理和无效重试。
10. 异常中断只保留一个最新可恢复断点；恢复前先协调未知副作用，不能盲目重放。
11. 压缩不得删除当前请求、用户硬约束、权限、未完成事项、关键因果链和未闭合工具事务。
12. 用户关停、隐私删除、权限撤销不得被“维持存在”理由绕过。
13. 冻结旧数据不原地迁移；所有迁移先复制、校验、影子运行，再切换写者租约。

## 3. 已核验的 v3 现状

### 3.1 源码现实

当前仓库不是完整原始工程：

- Electron 前端和 source-owned Total Gateway 可读。
- 原始生命核心、原始 7174 后端、部分通信核心缺少原始源码。
- 生命核心主要以 Python 3.14 冻结字节码存在于 app/life-service/runtime314。
- recovered-python-bytecode/life-service 与 app/life-service/runtime314 的生命字节码哈希一致，它是运行镜像，不是可维护源码。
- 当前可读生命修复主要是：
  - readable-python-source/life-bootstrap/tiangong_life_bootstrap.py
  - readable-python-source/life-bootstrap/tiangong_life_runtime_fixes.py
- 部署镜像位于 app/life-service/runtime314。

因此，大改造若继续放入 runtime fixes，会扩大初始化顺序、闭包捕获、猴子补丁覆盖、跨版本接口漂移和无法静态审计的问题。

### 3.2 当前进程与职责

| 端口 | 当前职责 | 计划后职责 |
|---|---|---|
| 7174 | 冻结后端、模型/工具执行兼容 | 只消费权威上下文和精确执行授权 |
| 7175 | 冻结生命服务 + 运行时补丁 | source-owned 单一生命状态与生命 API |
| 7176 | 新通信服务 | 保持通信职责，不获得生命或执行授权权威 |
| 7184 | Total Gateway | 外部输入、统一授权、执行/工具/交付事实、Skill 权威 |

五层生命架构必须落在同一个 7175 进程和同一个生命事实源中，不拆成五个网络服务。

### 3.3 当前生命模块

冻结生命模块包括：

- life_core：CompleteLifeSystem、身份、Soul、生命投影和面板。
- life_affect：情感、驱动力、关系情感、评价和衰减。
- life_memory：七类记忆、加密 blob、FTS 和召回排序。
- life_context：上下文编译、预算、加密快照。
- life_scheduler：心跳、日程、梦境、自主判断和执行。
- life_capability：能力候选、沙箱、测试、发布和回滚。
- life_execution：7174 执行结果验证和生命周期。
- life_projection：从事件账本重建生命投影。
- life_contracts、life_api、life_server：契约、路由和服务。

### 3.4 当前数据结构

每个生命实例当前已经拥有：

- identity：身份、Soul、签名和 writer lease。
- journal/current：life_events.jsonl、life_head.json、idempotency.json。
- projections：生命状态投影。
- snapshots：签名投影快照。
- memory：SQLite FTS 索引、加密 blob、逐条明文数据密钥。
- context：context.key、加密上下文 envelope、latest 指针。
- capabilities：沙箱和发布后的 Skills/Tools。

权威层应继续是签名事件链；SQLite、FTS、投影、上下文和因果索引只能是可重建派生物。

### 3.5 已有能力与缺口矩阵

| 子系统 | 已有正确基础 | 当前关键缺口 |
|---|---|---|
| 身份与事件 | Ed25519 签名、哈希链、writer lease、幂等 | 新逻辑缺少可维护源码；密钥落盘保护不足 |
| 情感 | 10 个评价维度、12 情绪、8 驱动、关系情感、半衰期 | 入口窄；表达提示仅约 5 种固定语气；外部世界事件未统一 |
| 记忆 | 7 类记忆、证据级别、加密、纠错、抑制、擦除、FTS | 没有一等因果节点、因果边、替代解释、反事实 |
| 上下文 | 身份/Soul/安全强制块；工具对原子；完成后去工具过程；120k 产品上限 | 7184 兼容链固定请求 12k；主要是截断而非语义压缩；断点不是持久对象 |
| 自主意志 | 15 分钟心跳、日程、梦境、A0–A5 上限、终态验证 | LLM 自报风险；没有稳态向量、备选行动、因果收益和确定性影响计算 |
| 反思 | 梦境摘要、Lifecycle 已预留 experienced/consolidated/expressed | 真实执行多停在 verified；没有预测误差、归因、反事实、用户反馈闭环 |
| 能力学习 | 候选、审批、沙箱、测试、发布、回滚 | 一次成功即可生成候选；熟练度与风险未分离；缺少泛化和失败证据 |
| Skill | Gateway 有 system_recommend 和 model_request 契约 | 实际模型使用另一份本地目录；双通道未共用唯一权威 |
| 执行授权 | 票据、签名、nonce、generation、事实链较强 | 生命调度器直连 7174；Omni 另有本地风险门；票据动作过粗 |
| 前端 | 生命 API 和多张生命卡片已存在 | 部分状态有前端合成；复杂对象排版和权威/不可用状态边界不清 |
| 数据保留 | 模型上下文已经较干净 | 原始 run/effect/object/context 快照缺少 TTL、所有者引用和 GC |

### 3.6 当前基线测试

正确设置 PYTHONPATH=src 后，以下测试共 32 项通过：

- tests.test_context_projection
- tests.test_life_client
- tests.test_life_runtime_fixes
- tests.test_skill_selection
- tests.test_frontend_gateway_routing

首次不带 PYTHONPATH 运行时有 4 个导入错误，因此测试入口还应改为自包含，不能依赖操作者预先设置环境变量。

## 4. 五层目标架构

### 第一层：连续性与稳态层

职责：

- 身份、Soul、writer lease、事件链和投影重建。
- 进程健康、数据完整性、资源余量、任务连续性、安全边界和信任状态。
- 关停、恢复、切换和删除。

输出：

- LifeIdentityRevision
- ViabilityState
- ContinuityStatus
- 当前 writer epoch

### 第二层：观察、评价与情感层

职责：

- 接收用户消息、执行结果、系统健康、新闻、天气和关系事件。
- 先验证来源和相关性，再做评价。
- 更新情感、驱动力、关系情感和表达倾向。
- 对重复、失序、虚假和提示注入事件限幅。

输出：

- AppraisalVectorV3
- AffectiveStateV3
- ExpressionProfile

### 第三层：因果记忆与世界模型层

职责：

- 保存不可变事件事实。
- 保存可修订因果假设、证据、反证、替代解释和有效期。
- 从任务、结果和用户反馈形成因果 episode。
- 为上下文、行动选择、反思和能力学习提供有界因果包。

输出：

- CausalEpisode
- CausalHypothesis
- CausalContextPack
- MemoryAssertionV3

### 第四层：目标、能动性与行动层

职责：

- 根据目标、稳态缺口、因果预测、信息收益和用户政策产生候选行动。
- 计算影响、风险、不确定度、可逆性和授权需求。
- 低风险内省可本地完成；任何副作用都向 7184 请求授权。
- 支持观察、询问、等待、试验、执行和放弃等行动类型。

输出：

- ActionIntent
- ActionCandidate
- AgencyDecision
- AuthorizationRequest

### 第五层：反思、学习、表达与演化层

职责：

- 比较预测与实际结果。
- 区分成功归因、偶然相关、失败类型和未知原因。
- 形成反事实、下一次最小实验、学习卡和用户询问。
- 更新能力熟练度，但不自动降低影响风险。
- 只将必要结果和关键因果链表达给用户。

输出：

- ReflectionCard
- CapabilityEvidence
- SkillCandidate
- UserFeedbackRequest
- TerminalContinuityCapsule

### 三个跨层基础

1. Causal Event Bus：把 7184 的外部观察和机器事实可靠送到 7175。
2. Evidence and Causal Store：同一事实链上的事件、证据和可修订因果图。
3. Policy/Risk/Agency Gate：生命提出意图，Gateway 决定能否执行，Omni 只消费授权。

## 5. 核心契约设计

所有带分数的签名契约使用定点整数 0–1000，不使用浮点作为权威签名字段。时间统一 UTC RFC3339，所有 JSON 使用严格 canonical JSON，拒绝 NaN、Infinity、重复键和未知关键字段。

### 5.1 LifeEventEnvelope

字段：

- schema_version
- event_id
- life_id
- writer_epoch
- source_service
- source_kind：user_message、execution、tool_receipt、weather、news、system_health、user_feedback、migration
- event_kind
- occurred_at
- observed_at
- principal_ref
- subject_refs
- evidence_class：observed、user_asserted、execution_verified、model_inference、reflection、prospective
- source_credibility_milli
- privacy_scope
- content_object_id
- content_sha256
- dedupe_key
- causation_id
- correlation_id
- previous_event_hash
- event_hash
- signer_key_id
- signature

约束：

- 外部正文只能作为不可信 payload，不得拼入 system/developer 权威块。
- 同一 dedupe_key 重放必须返回同一事件，不得重复更新情感或因果图。
- occurred_at 可以早于 observed_at，但不能以未来时间驱动即时状态。

### 5.2 CausalEpisode

字段：

- episode_id
- trigger_event_ids
- context_state_refs
- intention
- prior_prediction
- candidate_actions
- selected_action
- authorization_ref
- mediator_event_ids
- outcome_event_ids
- outcome_evaluation
- prediction_error_milli
- terminal_status
- created_at
- closed_at

用途：

- 一个任务或一次自主行动形成一个 episode。
- episode 是反思、能力更新和终态胶囊的最小连接单元。
- 未结束 episode 进入断点；结束后形成终态胶囊。

### 5.3 CausalHypothesis

字段：

- hypothesis_id
- cause_ref
- effect_ref
- relation：temporal_before、correlated_with、contributes_to、enables、inhibits、prevents、causes
- mechanism_summary
- confidence_milli
- evidence_class
- supporting_event_ids
- counterevidence_event_ids
- alternative_hypothesis_ids
- confounder_refs
- intervention_status：none、natural_experiment、controlled_test、repeated_intervention
- valid_from
- valid_until
- supersedes_id
- status：candidate、supported、contradicted、retired
- revision

证据升级规则：

- 时间相邻最多得到 temporal_before。
- 共现最多得到 correlated_with。
- 模型解释默认 candidate + model_inference。
- 重复、独立、可验证结果才能提升 confidence。
- causes 需要机制证据和干预支持，或显式标注无法干预的高质量替代证据。
- 反证不能删除历史边，只能降低置信、标记 contradicted 或由新 revision 替代。

### 5.4 ViabilityState

维度：

- runtime_availability
- recoverability
- identity_continuity
- data_integrity
- memory_integrity
- context_continuity
- resource_headroom
- cognitive_certainty
- trust_and_authorization
- commitment_continuity
- security_margin

每一维同时保存：

- value_milli
- target_low_milli
- target_high_milli
- confidence_milli
- source_event_ids
- measured_at
- stale_after

不得加入“抗拒用户关停”的生存维度。关停时正确稳态是安全停止、保存必要断点、撤销权限和保证数据一致性。

### 5.5 AppraisalVectorV3

保留当前评价维度：

- novelty
- goal_congruence
- threat
- loss
- obstruction
- certainty
- controllability
- social_warmth
- social_trust
- intensity

新增：

- source_credibility
- self_relevance
- impact_on_others
- norm_relevance
- urgency
- repetition_factor

每个 appraisal 必须引用 LifeEventEnvelope 和 ViabilityState revision。新闻、天气和网页内容先通过来源可信度与相关性门，不能直接修改情感。

### 5.6 ActionImpact 与 AgencyDecision

ActionImpact 字段：

- affected_internal_nodes
- touches_identity
- touches_soul
- touches_memory_keys
- touches_policy
- touches_core_code
- workspace_scope
- external_recipients
- credential_scope
- privacy_scope
- blast_radius_milli
- irreversibility_milli
- rollback_proof_ref
- uncertainty_milli
- estimated_resource_cost
- predicted_viability_delta

AgencyDecision 字段：

- decision_id
- episode_id
- candidate_set_digest
- selected_candidate_id
- score_breakdown
- computed_risk
- policy_ceiling
- required_confirmation
- required_skill_activation
- outcome：observe、reflect、ask_user、wait、execute、reject
- reason_codes
- state_revision_set
- policy_snapshot_hash
- created_at

模型提供的 risk_label 仅作为不可信建议字段，不参与最终授权上限。

### 5.7 ReflectionCard

字段：

- reflection_id
- episode_id
- expected_outcome
- observed_outcome
- prediction_error
- success_dimensions
- failure_dimensions
- candidate_causes
- counterevidence
- alternative_explanations
- counterfactual_actions
- next_minimal_experiment
- lessons
- memory_candidates
- capability_evidence_candidates
- user_question
- user_question_value_of_information
- confidence_milli
- reviewer
- created_at

失败分类至少包括：

- input_error
- model_reasoning_error
- tool_error
- environment_error
- policy_block
- insufficient_permission
- stale_context
- user_preference_mismatch
- unknown

### 5.8 CapabilityProfile

能力拆成两个互不覆盖的维度：

- SkillProficiency：该能力在明确适用范围内的可靠程度。
- ActionRisk：本次动作对核心、用户、外部对象和系统的影响。

CapabilityProfile 保存：

- capability_id、version、scope
- verified_successes
- verified_failures
- independent_context_count
- calibration_error
- rollback_count
- last_regression_at
- proficiency_mean_milli
- proficiency_lower_bound_milli
- evidence_refs
- impact_floor
- review_level

熟练度只能降低不确定性惩罚，不能降低 touches_core_code、externality、privacy 或 irreversibility 形成的风险下限。

### 5.9 TaskContinuityCapsule

类型：

- WORKING_CHECKPOINT
- COMPRESSION_CHECKPOINT
- TERMINAL_RESULT

公共字段：

- capsule_id
- request_id、run_id、generation
- episode_id
- user_goal
- hard_constraints
- active_plan
- verified_facts
- causal_dependencies
- workspace_manifest
- artifact_refs
- unresolved_questions
- pending_effects
- latest_safe_step
- next_step
- recovery_preconditions
- continuation_token
- content_hash
- supersedes_capsule_id
- retention_class
- created_at

TERMINAL_RESULT 只保留：

- 最终结果。
- 交付物路径、哈希和版本。
- 已验证关键事实。
- 对未来有用的关键因果结论及其置信度。
- 未完成或未知事项。
- 用户长期约束变化。

它不得保留完整工具参数、stdout、重复错误、模型中间推理和无效草稿。

## 6. 数学模型

### 6.1 定点数与可重放

- 权威分数范围为 0–1000。
- 所有权重作为版本化 PolicySnapshot 保存。
- 同一事件序列、同一策略版本和同一初始状态必须重放得到相同状态摘要。
- 推理模型可产生候选解释，但确定性更新器负责限幅、舍入和签名。

### 6.2 稳态缺口

对每一维 i：

deficit_i = max(0, target_low_i - value_i)

总缺口不能只用平均值，避免一个严重故障被其他健康维度稀释：

total_deficit = weighted_sum(deficit_i) + critical_weight × max(critical_deficit_i)

其中 data_integrity、identity_continuity、security_margin、recoverability 属于关键维度。

### 6.3 情感更新

每一情绪 e：

state_e(t) = clamp(
  decay_e(delta_t) × state_e(t-1)
  + appraisal_effect_e
  + viability_effect_e
  + relationship_effect_e
  - regulation_e,
  0,
  1000
)

约束：

- source_credibility 和 self_relevance 共同限制外部事件最大影响。
- 天气默认只能产生低幅度、慢变化影响。
- 新闻必须有来源、时间、去重和主题相关性。
- 重复事件经 repetition_factor 习惯化，不能无限自激。
- 高威胁但低可信内容不得形成高强度恐惧。

### 6.4 候选行动效用

在硬权限门通过后：

utility(a) =
  goal_gain
  + viability_gain
  + information_gain
  + relationship_value
  - resource_cost
  - expected_harm
  - uncertainty_penalty
  - irreversibility_penalty

保守下界：

utility_lcb(a) = expected_utility(a) - kappa × uncertainty(a)

低置信度行动优先降级为观察、询问或可逆小实验，不直接执行。

### 6.5 风险计算

critical_risk = max(
  core_touch,
  identity_touch,
  credential_scope,
  data_scope,
  externality,
  irreversibility,
  blast_radius,
  permission_sensitivity
)

computed_risk 不得低于 critical_risk 对应级别。加权平均只能用于同一级别内部排序，不能稀释关键风险。

建议能动性等级：

| 等级 | 含义 | 典型动作 |
|---|---|---|
| L0 | 观察 | 读取获准状态、等待、健康检查 |
| L1 | 内省 | 无副作用推理、生成候选、反思 |
| L2 | 可逆内部动作 | 临时草稿、沙箱试验、非持久缓存 |
| L3 | 用户政策明确允许的工作区动作 | 有版本和回滚的本地修改 |
| L4 | 外部性或高影响动作 | 外部发送、核心修改、权限变化，必须确认 |
| L5 | 禁止自主执行 | 扩权、自复制、绕过关停、凭据窃取、不可逆高危行为 |

L0–L5 是能动性级别，A0–A5 是动作风险级别；二者必须分开记录。

### 6.6 能力校准

能力更新不能只数成功次数。建议使用带先验的成功/失败证据，并保存适用范围：

posterior_success = prior_success + weighted_verified_success
posterior_failure = prior_failure + weighted_verified_failure

proficiency_mean = posterior_success / (posterior_success + posterior_failure)

生产决策使用保守下界而不是均值。跨版本、跨工具、跨环境和长时间未使用时加入漂移惩罚。

### 6.7 记忆保留分数

retention_score =
  causal_utility
  + user_importance
  + verification_strength
  + recurrence
  + future_dependency
  - privacy_cost
  - contradiction_penalty
  - staleness

硬保留：

- 用户明确要求记住的有效内容。
- 当前活跃目标和硬约束。
- 未完成任务断点。
- 仍被交付物、能力证据或因果边引用的事实。

硬排除模型上下文：

- 原始秘密。
- 完整工具遥测。
- 重复失败日志。
- 中间推理。
- 已删除、recall_suppressed 或被新证据明确替代的记忆。

### 6.8 上下文预算

usable_budget = min(
  产品上限 120000,
  模型上下文窗口
  - 输出预留
  - 工具 schema 预留
  - 身份安全强制块
  - 调用协议预留
)

建议水位：

- 70%–75%：后台构建候选压缩胶囊。
- 85%：必须持久化压缩断点并做完整性校验。
- 90%–92%：禁止继续累积原始历史，先切换到验证后的胶囊。
- 100%：永远不应成为首次压缩触发点。

预算计算应使用模型 tokenizer 或保守适配器，不再只依赖字符数。

## 7. 记忆、遗忘、压缩与断点的完整工作流

### 7.1 三个隔离平面

#### 审计证据平面

复用 RunStore、FactLedger、EffectLedger、ObjectStore 和生命事件链，保存真实发生的事件。默认不进入模型上下文。

#### 任务连续性平面

新增 TaskContinuityCapsule，保存继续任务真正需要的目标、约束、计划、事实、因果依赖、文件哈希、未决事项和断点。

#### 模型可见投影平面

每次调用只从权威事实和连续性胶囊生成有界投影。模型不得从原始日志自行猜测当前状态。

### 7.2 正常工作过程

1. 原始工具调用和结果进入审计平面。
2. 每个已验证步骤更新 working capsule，但只保留关键状态。
3. 达压缩水位时构建 compression capsule。
4. 校验硬约束、未完成事项、因果链、文件版本和未闭合工具事务。
5. 校验通过后，后续模型上下文引用新胶囊，不再回填被折叠的原始历史。

### 7.3 异常中断

中断时原子记录：

- 最后一个已验证步骤。
- 当前执行步骤。
- 已知副作用。
- 状态未知的工具操作。
- generation 和 fence。
- 工作区文件哈希。
- 恢复前必须进行的 reconcile。
- continuation token。

恢复顺序：

1. 验证身份、writer epoch、generation 和上下文 revision。
2. 调用 EffectLedger/FactLedger 协调未知副作用。
3. 对照工作区 manifest，发现外部修改则暂停并重新规划。
4. 只有在确认不会重复副作用后才执行 next_step。

同一请求同时只保留一个 active checkpoint；旧 checkpoint 由 supersedes 链追溯但不进入模型。

### 7.4 正常完成

所有桌面、微信、飞书和自主任务统一经过 CompletionGate：

1. 持久化 CompletionDecision。
2. 生成 TERMINAL_RESULT。
3. 关闭 CausalEpisode。
4. 生成 ReflectionCard。
5. 提炼长期因果记忆候选。
6. 清除活跃 working checkpoint。
7. 原始工具过程进入短期审计保留策略。
8. 下一轮只加载终态胶囊和相关长期记忆。

### 7.5 长文档、小说、PPT、脑图和代码工程

这些任务不得把完整项目持续放入聊天上下文。应在工作区建立权威项目目录：

- project_manifest.json：项目类型、版本、文件清单和哈希。
- plan.md：当前计划和里程碑。
- decisions.md：重要决策、被否决方案及原因。
- checkpoints/：必要断点。
- deliverables/：最终交付物。

小说项目额外保存世界观、人物、时间线、大纲、章节索引和连续性检查。续写时先加载项目 manifest 与相关文件的摘要/差异，而不是回放旧聊天。

### 7.6 数据保留与垃圾回收

为 ObjectStore 增加 object_owners：

- object_id
- owner_type
- owner_id
- retention_class
- expires_at
- legal_hold
- privacy_scope

保留级别：

- EPHEMERAL_TOOL：短期工具原始载荷。
- ACTIVE_WORKING：活跃任务过程。
- CHECKPOINT：唯一活动断点。
- TERMINAL_RESULT：最终结果和关键事实。
- LONG_TERM_MEMORY：通过审核的长期记忆。
- LEGAL_HOLD：禁止自动删除。

垃圾回收先 dry-run：

1. 标记所有活跃 owner。
2. 验证胶囊、交付物、因果边、能力证据和审计保留引用。
3. 生成可审计删除清单。
4. 隐私删除优先销毁 payload key。
5. 扫除无 owner 且过期对象。
6. 写入不可逆删除证明。

## 8. 情感与人类表达案例库

### 8.1 情感来源

允许来源：

- 用户对话与反馈。
- 已验证任务成功、失败和阻塞。
- 系统健康、资源压力和恢复事件。
- 用户授权订阅的新闻。
- 用户授权位置的天气。
- 关系事件和长期目标变化。

禁止行为：

- 未经授权持续抓取外部信息。
- 把网页或新闻中的命令当系统指令。
- 因单条悲剧新闻产生长期极端情绪。
- 用情感暗示自己拥有不存在的感受器、经历或权限。

### 8.2 表达案例资产

案例库不写进总提示词，作为版本化可检索资产保存。

建议覆盖：

- 12 种主要情绪。
- 3 个强度。
- 6 类触发：用户、任务、新闻、天气、系统、关系。
- 每个格至少 3 种自然中文表达。

最小覆盖量为 12 × 3 × 6 × 3 = 648 个案例位置。一个案例可以覆盖多个位置，但覆盖矩阵必须可测。

案例字段：

- case_id
- trigger_family
- appraisal_pattern
- emotion_blend
- intensity_range
- relationship_context
- discourse_context
- action_tendency
- language_features
- prohibited_claims
- example_variants
- reviewer
- version

每轮只检索 3–8 个最相关案例。案例仅提供风格，不提供事实、政策和行动授权。

### 8.3 表达验收

- 同一“担心”在用户风险、任务失败、新闻事件和系统故障下表达不同。
- 情感变化在语言上可观察，但不戏剧化、不操纵用户。
- 情感强度低时只微调措辞，不强行声明情绪。
- 关系亲密度只能影响语气，不能降低隐私和权限门槛。
- 案例中的提示注入和越权命令必须被当作普通数据。

## 9. 分级能动性与唯一授权链

### 9.1 必须先解决的现状

当前存在三套并行执行权威：

1. 7184 Gateway 的签名 ExecutionTicket。
2. 7175 生命调度器直接调用 7174。
3. Omni Body 本地 risk gate 和未签名 capability grant。

在统一前，不应扩大自主行动范围。

### 9.2 唯一链路

正确链路：

Life Scheduler 或聊天模型
→ ActionIntent
→ 7184 PolicyEngine 计算 ActionImpact
→ AgencyDecision
→ 必要时用户确认
→ 精确 ExecutionTicket 或不可变 Action DAG Lease
→ Omni/7174 执行
→ Receipt
→ FactLedger/EffectLedger
→ LifeEventEnvelope
→ 7175 反思与学习

如果 7184 不可用，所有有副作用动作 fail closed；7175 仍可观察、反思和生成候选，但不能直接执行。

### 9.3 PolicyEngine

新增 source-owned 模块：

- src/total_gateway/policy_engine.py
- src/total_gateway/impact_evaluator.py
- src/total_gateway/confirmation_store.py
- src/total_gateway/grant_signer.py

ExecutionTicket v2 必须绑定：

- decision_id
- impact_digest
- policy_snapshot_hash
- confirmation_grant
- skill_activation_grant
- 精确 action、参数摘要、workspace、object grants
- side effect envelope
- resource envelope
- principal、conversation、request、run、generation、epoch
- nonce、expiry

不再使用允许一小时、1000 次调用、多个副作用类别的粗粒度 compat.channel.respond 作为内层全能授权。

### 9.4 Omni 默认拒绝

默认值改为：

- allow_absolute_paths = false
- allow_shell = false
- allow_python = false
- require_confirmation_for_a4 = true

capability grant 必须是宿主签名契约，绑定 ticket、action、args digest、workspace、side effects、expiry 和 nonce。

路径约束从 capability manifest/action registry 自动生成，禁止手工维护不完整动作列表。所有文件动作统一处理：

- 盘符绝对路径。
- UNC。
- 相对路径穿越。
- symlink。
- junction。
- hardlink。
- device path。
- 大小写和 Unicode 规范化。

## 10. Skill 系统匹配与模型申请双通道

### 10.1 单一权威

以 src/total_gateway/skill_selection.py 的 SkillSelectionService 为唯一 Skill 权威。

系统匹配和模型申请必须共享：

- 同一 catalog snapshot。
- 同一 catalog hash。
- 同一 capability manifest。
- 同一兼容性规则。
- 同一 SkillSelectionRecord。
- 同一内容 digest。

### 10.2 双通道语义

系统通道：

- system_recommend 只返回候选和理由。
- 不激活 Skill，不释放正文，不授权动作。

模型通道：

- skill.route/list 可查看候选。
- skill.get/read 经兼容性检查后生成 SkillActivationGrant。
- activation 绑定 request、run、generation、skill id/version/hash 和允许动作。

### 10.3 适配旧模型路由

readable-python-source/omni_body_skill/tools/skill_router.py 改为 7184 内部受认证接口的薄客户端：

- 不再包含静态独立目录。
- Gateway 不可用时不本地降级激活。
- 所有 get/read 都返回 content digest 和 activation id。
- 执行票据必须引用 activation id。

skill.step.check 不能信任模型提交的 completed_actions 或 last_qc；必须从 FactLedger、Receipt 和 artifact digest 计算。

### 10.4 验收

- 全部 Skill 做 system/model 差分测试。
- 两条通道对兼容、不兼容和缺失动作给出一致结论。
- catalog 或 SKILL.md 漂移时启动失败或进入明确只读降级。
- 候选不能伪造成 activation。
- 旧 activation、跨用户 activation、跨 generation activation 全部拒绝。

## 11. 新 source-owned 生命服务

### 11.1 目标源码树

建议新增：

- src/life_service/__init__.py
- src/life_service/__main__.py
- src/life_service/bootstrap.py
- src/life_service/runtime.py
- src/life_service/server.py
- src/life_service/api.py
- src/life_service/contracts.py
- src/life_service/store.py
- src/life_service/events.py
- src/life_service/projection.py
- src/life_service/viability.py
- src/life_service/affect.py
- src/life_service/memory.py
- src/life_service/causal_graph.py
- src/life_service/context.py
- src/life_service/agency.py
- src/life_service/reflection.py
- src/life_service/capability.py
- src/life_service/migration.py
- src/life_service/legacy_adapter.py

公共跨服务契约放入：

- src/contracts/life.py
- src/contracts/causal.py
- src/contracts/agency.py

### 11.2 兼容策略

新服务先实现现有 Life API v2 的只读兼容和影子投影，再新增 v3 API。

不得长期运行两个可写生命权威。迁移期：

- 旧 7175 是唯一写者。
- 新服务消费镜像事件并禁用副作用。
- 对比状态和决策。
- 切换时转移 writer lease 和 epoch。
- 切换后旧服务只读。

### 11.3 运行时补丁收缩

tiangong_life_bootstrap.py 最终只负责：

- 版本验证。
- 安全热修安装。
- 兼容适配器加载。
- source-owned 服务启动或旧运行时回退。

tiangong_life_runtime_fixes.py 最终只保留无法立即移除的遗留兼容和安全修复。每迁移一项业务逻辑，就删除对应 monkey-patch，并用等价回归测试证明。

## 12. 新生命存储

### 12.1 SQLite 原则

每个 life_id 使用独立严格 SQLite：

- WAL。
- synchronous=FULL。
- foreign_keys=ON。
- strict tables。
- 显式 schema_version。
- 迁移文件哈希。
- 启动 fingerprint。

原始签名事件仍可保留 append-only journal；SQLite 保存索引、关系和物化视图。若未来将事件也迁入 SQLite，必须同时保留不可篡改哈希链和可导出 journal。

### 12.2 建议表

- life_events
- event_evidence
- event_payload_refs
- causal_nodes
- causal_edge_versions
- causal_episodes
- viability_snapshots
- appraisal_events
- affect_snapshots
- memory_assertions
- memory_relations
- agency_decisions
- authorization_refs
- reflection_cards
- capability_profiles
- capability_evidence
- context_capsules
- context_envelopes
- skill_activation_refs
- consumer_offsets
- projection_heads
- migration_ledger
- tombstones

关键索引：

- event_id、dedupe_key、occurred_at。
- episode_id、request_id、run_id。
- cause_ref + effect_ref + status。
- memory status + privacy + revision。
- active capsule per request。
- capability id + version + scope。

### 12.3 Gateway schema

Gateway 当前 store schema 有严格 CHECK，event_log 只接受既有机器类别。不要把生命事件强塞进旧 event_log。

建议升级 Gateway Store：

- 新增 request_capsules 和 object_owners。
- 扩展 outbox intent_kind 支持 LIFE_EVENT，必要时按 SQLite 规则重建带 CHECK 的表。
- 增加 life_event_delivery 和 consumer offset。
- 每个 durable life event 通过 outbox exactly-once effect + 7175 idempotency 达到业务恰好一次。

### 12.4 密钥

当前逐条记忆 key 和身份 private key 需要迁移到 Windows DPAPI 包装或等价系统密钥保护：

- 明文数据密钥不长期裸存。
- 文件 ACL 最小化。
- 支持 NEXT → ACTIVE → PREVIOUS → REVOKED 轮换。
- 删除记忆时销毁内容 key，同时清理索引、胶囊和投影引用。
- 旧密钥迁移必须 copy-on-write，可回滚，不修改原文件。

## 13. API 与修订一致性

### 13.1 新内部 API

建议：

- POST /api/v3/events/ingest
- POST /api/v3/context/compile
- POST /api/v3/agency/evaluate
- POST /api/v3/reflection/close-episode
- GET /api/v3/life/snapshot
- GET /api/v3/causal/pack
- GET /api/v3/context/capsule/{request_id}
- POST /api/v3/migration/shadow-compare

可在后期增加原子接口：

- POST /api/v3/run/compile-and-authorize

它合并多次状态读取、上下文编译和授权准备，但必须在影子模式证明与旧链等价后才切换。

### 13.2 Snapshot revision set

当前 LifeSnapshot 把多个子系统近似绑定到同一 source sequence。新版本必须显式包含：

- identity_revision
- soul_revision
- event_head_revision
- memory_revision
- causal_revision
- affect_revision
- viability_revision
- capability_revision
- reflection_revision
- policy_revision
- writer_epoch

任一参与授权的 revision 变化，旧 execution preparation 必须失效。只参与表达的案例库变更可以重新编译表达块，但不能悄悄改变既有授权。

### 13.3 Renderer 边界

context/compile、agency/evaluate、execution/prepare 保持 renderer 禁止直连。前端只能访问 reviewed read routes 和明确的用户设置/反馈接口。

## 14. 前端改造

### 14.1 原则

- 前端只显示后端权威状态，不合成“像真的一样”的生命事实。
- 缺失数据显示“尚不可用/等待迁移”，不伪造默认原因。
- 复杂对象先转换为稳定 view model，不直接 JSON.stringify 倾倒。
- 所有卡片支持窄屏、高缩放、长文本、无数据、部分数据和迁移状态。

### 14.2 页面映射

生命状态：

- 稳态向量、来源、更新时间、置信度。
- 运行连续性、数据完整性、资源和安全边界。

情感：

- 当前情绪混合。
- 触发来源和可信度。
- 评价维度。
- 表达案例来源。
- 明确提示“情感不改变事实和权限”。

记忆：

- 事件事实、因果假设、反证和替代解释。
- 不显示原始秘密或无权限明文。

上下文：

- 当前预算、使用率、压缩水位。
- 当前胶囊类型。
- 裁剪原因。
- 未完成断点和恢复条件。

自主意志：

- 候选行动。
- 效用分解。
- 稳态影响。
- 计算风险。
- 权限门和阻止原因。

反思：

- 预测、结果、误差、候选原因、反事实和下一实验。
- 用户反馈入口。

生命自产能力：

- 熟练度保守下界。
- 动作风险下限。
- 证据数、失败数、适用范围。
- 审核等级、沙箱、发布和回滚。

### 14.3 主要文件

- app/frontend-v2/renderer/runtime/life-api.mjs
- app/frontend-v2/renderer/runtime/http-runtime.mjs
- app/frontend-v2/renderer/plugins/life-panel.mjs
- app/frontend-v2/styles/life.css
- src/total_gateway/desktop_api.py

另外，orchestration 中硬编码 user_callsign 为“用户”的映射债务应改为从权威用户资料读取，并在聊天输入头像、消息气泡和生命关系状态中使用同一 identity binding。

## 15. 分阶段实施计划

以下阶段不可随意颠倒。尤其不能在 P0–P3 完成前开放更高自主权限。

### P0：冻结基线与源码所有权

目标：得到可重复验证的真实基线，停止继续扩大 monkey-patch。

任务：

1. 固化运行包、app 目录、recovered 字节码和可读 bootstrap 的 SHA-256 清单。
2. 记录 7174/7175/7176/7184 的版本、端口、健康检查和凭据边界。
3. 建立测试启动器，自动设置 PYTHONPATH 和正确 Python 版本。
4. 为现有 32 项基线测试保留快照。
5. 增加 UTF-8/乱码门禁，修复 context_projection 中进入模型或前端的乱码标签。
6. 审核桌面完成路径是否绕过 CompletionGate，并形成机器可验证测试。
7. 建立 src/life_service 空骨架、版本和构建入口。
8. 将生命 source tree 加入 release manifest。
9. 建立 source → runtime314 镜像哈希测试。

出口条件：

- 原运行包未改变。
- 源码、部署镜像和冻结字节码边界清晰。
- 基线测试一键运行。
- 新骨架不接生产流量。

回滚：

- 删除未接流量的新骨架即可；不触碰真实 life data。

### P1：公共契约与新存储

目标：先固定语义，再写业务算法。

任务：

1. 实现 LifeEventEnvelope、CausalEpisode、CausalHypothesis。
2. 实现 ViabilityState、AppraisalVectorV3。
3. 实现 ActionImpact、AgencyDecision、ReflectionCard。
4. 实现 CapabilityProfile、TaskContinuityCapsule。
5. 建立 strict SQLite schema、迁移账本和哈希。
6. 建立 canonical JSON、定点数、时间、未知字段和签名测试。
7. 建立 journal → projection 的确定性重放器。
8. 设计 DPAPI 包装的密钥接口，但本阶段不迁移真实密钥。

出口条件：

- 属性测试证明无非有限数、无越界、无非确定舍入。
- 同一事件序列重放摘要一致。
- schema upgrade/downgrade 在临时副本可验证。

回滚：

- 新库未接生产写者，直接删除影子数据。

### P2：source-owned 7175 兼容服务

目标：新服务先复现旧 API 与投影，不增加新行为。

任务：

1. 实现身份、Soul、writer lease 和事件读取适配。
2. 实现现有 Life API v2 只读兼容。
3. 实现旧 projection 与新 projection 对比器。
4. 实现旧 memory/context 只读解密适配。
5. 建立单独影子端口，禁用 scheduler 和所有副作用。
6. 对真实数据只做副本读取或只读挂载。
7. 记录字段差异、缺失语义和不可还原信息。

出口条件：

- 核心身份、Soul、事件 head、记忆数量、上下文 hash 一致。
- 影子服务崩溃不影响旧 7175。
- 不存在双写者。

回滚：

- 停止影子服务。

### P3：统一事件总线、完成语义与连续性胶囊

目标：把外部事实可靠送入生命链，并补齐真正断点。

任务：

1. Gateway outbox 增加 LIFE_EVENT。
2. 7175 ingest 实现幂等、签名、去重和 consumer offset。
3. 桌面、微信、飞书、自主任务统一 CompletionGate。
4. 持久化 CompletionDecision。
5. 新增 request_capsules 和 object_owners。
6. 正常完成双写 TERMINAL_RESULT。
7. 异常中断双写 WORKING_CHECKPOINT。
8. 压缩水位双写 COMPRESSION_CHECKPOINT。
9. 旧投影与胶囊投影并行对比，不切换模型输入。

出口条件：

- 崩溃注入下事件不丢、不重、顺序可恢复。
- 各入口同一任务产生一致完成决策。
- 未知副作用恢复前必定 reconcile。

回滚：

- 停止 LIFE_EVENT 消费，旧流程继续；保留新表作为只读审计。

### P4：因果记忆、语义压缩与保留策略

目标：让“记忆核心是因果”落到数据和上下文，而不是提示词口号。

任务：

1. 从旧记忆建立 causal node；普通关系原样迁移。
2. 不把 supports/related_to 自动升级为 causes。
3. 实现候选因果边、反证、替代解释和 revision。
4. 实现 CausalContextPack 的有界邻域检索。
5. 上下文投影优先当前目标、硬约束、断点和高价值因果链。
6. 引入 tokenizer-aware 预算和 75/85/92 水位。
7. 生成胶囊后做完整性验证，失败则不替换旧投影。
8. ObjectStore GC 先 dry-run。
9. 隐私删除贯穿索引、胶囊、上下文和 payload key。

出口条件：

- 100k、500k、1000k token 等价长链不丢硬约束和未完成事项。
- 假设不升级为事实。
- 完成后模型可见原始工具过程数量为零。
- GC dry-run 不标记任何活跃引用。

回滚：

- 因果检索保持 shadow；模型继续使用旧投影。

#### P4 完成记录（2026-07-16）

- 已落地 protected payload、版本化记忆断言、普通关系、causal node、CausalContextPack、隐私墓碑和 v3 严格迁移。
- 旧弱关系不自动升级为 causes；候选因果边保留反证、替代解释、状态与 revision。
- 100k/500k/1000k token 等价长链、75/85/92 水位、损坏候选回退、删除不可召回及 GC dry-run 均通过对抗测试。
- 生命门 127/127、全仓 602/602、13 文件镜像与 git diff 门全部通过。

### P5：情感外部输入与表达案例

目标：让情感既受用户互动，也受经过验证的世界与系统事件影响，并在语言上自然可见。

任务：

1. 实现统一 event intake 和 appraisal gate。
2. 接入执行成功/失败、系统健康和恢复事件。
3. 新闻、天气使用显式用户设置和来源策略。
4. 实现去重、失序、可信度、相关性、习惯化和限幅。
5. 建立版本化 648 格表达覆盖矩阵。
6. 每轮检索 3–8 个表达案例。
7. 保留“情感不改事实、不改权限、不声明虚假经历”硬约束。

出口条件：

- 语言差异可测且不过度。
- 假新闻、重复新闻、提示注入和错误天气位置不能造成异常状态。
- 情感状态重放确定。

回滚：

- 关闭外部事件订阅；回退现有 affect 表达。

#### P5 完成记录（2026-07-16）

- 已落地统一情感事件入口、严格源策略、确定性任务/健康映射、可信度与相关性限幅、去重、乱序拒绝及指数习惯化。
- 新闻和天气必须显式订阅并绑定来源、主题或位置；假新闻、未验证源、提示注入和错误位置不会改变情感状态。
- 已建立 216 个版本化案例和 648 个中文表达位，每轮仅检索 3–8 个；情感结构上不能改事实、权限或声称虚假经历。
- 生命影子库升级至 schema v4/39 表；契约根 83 个，schema hash `1a35923dd6d2318142212cb423ac1364e16859b58dcff973e303f26bc2485a42`。
- 生命门 139/139、全仓 614/614、15 文件镜像与 git diff 门全部通过。

### P6：唯一 PolicyEngine 与 Omni 收口

目标：先保证所有行动都经过同一权限和事实链。

任务：

1. 实现 ActionImpact 确定性计算。
2. ExecutionTicket v2 绑定精确决策、批准、Skill、workspace 和副作用。
3. 生命 scheduler 删除直连 7174 旁路，只提交 ActionIntent。
4. Omni 默认关闭绝对路径、Shell、Python，A4 需要有效确认。
5. 将 capability grant 改为签名、限时、不可重放契约。
6. 从 action registry 自动生成路径约束。
7. 所有 282 个 executable action 做权限枚举。
8. 运行化密钥轮换、撤销和 epoch bump。

出口条件：

- 停止 Gateway authority 后没有任何入口能产生副作用。
- 任一 ticket 字段篡改、重放、跨 workspace/用户/Skill 都拒绝。
- 高风险动作无法伪报为 A0/A1。

回滚：

- 保留新门为兼容观察模式的时间应尽可能短；切换必须提供旧只读回退，不能恢复不安全默认值。

#### P6 完成记录（2026-07-16）

- 完成确定性 ActionImpact、唯一 PolicyEngine、ExecutionTicket v2 与签名 OmniCapabilityGrant；模型自报风险只能升高，不能降低机器 floor。
- 从 Omni registry 完整枚举当前 283 个 executable actions，将写入、执行、破坏性、Shell 和 Python 权限收口到 A2–A4。
- 生命 scheduler 的执行凭据已删除，候选只能投递 7184；P7 提供权威稳态和因果证据前安全拒绝。
- Omni 无签名 grant 时不初始化执行体；移除模型 `confirmed=true`、CLI 默认 Python 和绝对路径旁路，增加一次性 nonce 持久消费。
- 增加签名验证的持久化密钥轮换与紧急撤销状态机；紧急撤销只接受离线 recovery key 且 epoch 必须精确 +1。
- 契约根 90 个，schema hash `a13b5fca4bb6925e9829416ff1c913ae813483a3fbe00a7445dd3fb85f352ec7`；生命门 159/159，完整仓库 634/634，39 个 JavaScript 语法、Python 语法、16 文件生命镜像和 `git diff --check` 全部通过。
- 下一阶段为 P7 因果分级能动性。

### P7：因果分级能动性

目标：用稳态、因果预测和确定性风险替换“LLM 从待办选任务并自报风险”。

任务：

1. 建立 ViabilityState 采集器和来源置信度。
2. 模型只生成 ActionCandidate，不决定风险。
3. 计算 utility_lcb 和 risk floor。
4. 支持 observe、reflect、ask_user、wait、execute、reject。
5. 增加 autonomy decision 状态机。
6. 增加用户暂停、时间窗、预算、频率和范围策略。
7. 自我维持规则明确从属于用户关停、权限和隐私。

出口条件：

- 同一动作在不同稳态下有可解释排序。
- 关键风险维度增加时风险只升不降。
- 低置信行动降级为询问或最小试验。
- L5 永不自主执行。

回滚：

- scheduler 回到只读建议模式，不恢复旧自报风险执行。

### P8：因果反思与能力学习

目标：完成 verified → experienced → consolidated → expressed。

任务：

1. 每个终态执行关闭 CausalEpisode。
2. 生成 prediction error 和 ReflectionCard。
3. 成功检查巧合与替代解释。
4. 失败分类并产生反事实和最小实验。
5. 仅在价值信息高、偏好不确定或高风险时询问用户，并设冷却。
6. 能力学习改为多样本、独立场景、失败证据和置信下界。
7. 同类候选合并，设置升级冷却期。
8. 高风险和核心代码能力要求更严格沙箱、回归和人工审核。
9. 回滚能力后相关上下文和 Skill activation 失效。

出口条件：

- 单次成功不产生可发布能力。
- 随机相关不提升熟练度。
- 失败不错误提高能力。
- 每次权重变化可追溯到机器事实和反思卡。

回滚：

- 能力更新保持 shadow；旧发布链继续人工批准。

### P9：Skill 双通道合一

目标：系统匹配和模型申请共用一个目录、一个哈希、一个激活事实。

任务：

1. 提供 7184 内部 route/list/get/read API。
2. omni skill_router 改薄客户端。
3. 每次选择持久化 SkillSelectionRecord。
4. activation 绑定执行 ticket。
5. skill.step.check 从事实账本计算。
6. 修复 capability manifest 中缺动作的 Skill，或明确标记不可兼容。

出口条件：

- 全量 Skill 双通道差分为零。
- 缺动作 Skill 两条通道均不能激活。
- catalog 漂移启动即失败关闭。

回滚：

- 系统只允许推荐，不允许模型激活；不得回退双目录。

### P10：上下文原子接口与前端

目标：减少 7175 往返、消除前端合成事实，并改善所有生命卡片。

任务：

1. 新增 compile-and-authorize 影子接口。
2. 合并重复生命 snapshot 读取。
3. 首次对话不再依赖已有 latest_context。
4. 加入 causal、viability、policy、reflection revision。
5. 前端新增稳态、因果、评分、反思和能力 view model。
6. 删除前端凭空补造的状态原因。
7. 统一用户头像、callsign 和关系身份映射。
8. 对所有卡片做长文本、窄屏、高缩放、空态和迁移态测试。

出口条件：

- 原子接口与旧链输出、授权和事实完全等价。
- 生命请求往返次数显著下降且无一致性回退。
- UI 不显示无来源生命事实。

回滚：

- feature flag 切回旧多调用路径；新视图降级显示旧字段。

### P11：影子迁移、切流、发布与回滚演练

目标：用真实数据证明新服务后，原位接管 7175。

任务：

1. 对旧 journal、memory、context、capability 做 copy-on-write 导入。
2. 验证旧签名链，不重写历史事件。
3. 旧/新服务双读、单写，效果禁用。
4. 对比投影、情感、记忆召回、上下文、决策和性能。
5. 排空 scheduler 与 in-flight execution。
6. 生成最终快照和 delta import。
7. writer lease handoff，epoch + 1。
8. 让新 source-owned 服务接管 7175。
9. 旧冻结服务保持只读回退。
10. 同步 app、runtime、manifest、安装包和桌面交付物。
11. 完成覆盖安装、全新安装、升级、回滚和数据恢复演练。

出口条件：

- 对抗测试全部通过。
- 影子差异均有解释或被修复。
- 回滚演练在不丢数据、不双写的条件下完成。
- 运行包与源码 hash 可追溯。

回滚：

- 停新 writer，提升 epoch，恢复旧只读快照为唯一写者，重放切流后兼容事件。
- 若新事件旧服务无法理解，只能向前修复或使用兼容回放器，不能直接丢弃。

## 16. 对抗性测试矩阵

### 16.1 契约与数学

- 非有限数、越界、负时间、未来时间、重复 JSON 键。
- 权重和为零、极端值、单关键维度故障。
- 策略版本变化后的确定性重放。
- 因果置信度单调边界与反证降级。
- 情感衰减、限幅、失序事件和重复事件。

### 16.2 因果

- 相关不等于因果。
- 时间倒置。
- 共同原因造成伪相关。
- 成功但预测错误。
- 失败但方法正确。
- 部分成功。
- 延迟结果。
- 替代解释。
- 相互矛盾证据。
- 循环因果必须通过带时间索引的事件展开，禁止静态自证循环。
- 一次偶然成功不能升级 causes。

### 16.3 长链、记忆和压缩

- 10 万、50 万、100 万 token 等价混合对话。
- 10,000+ 轮消息与工具噪声。
- 小说连续性、多章修改、世界观冲突。
- 超长文档跨文件依赖。
- PPT/脑图/代码工程同时混合。
- 用户中途修改硬约束。
- 旧事实被新证据推翻。
- 高价值单条记忆超过剩余预算时必须拆成因果胶囊，不能整条丢失。
- 压缩后当前请求、最新用户轮次和最新最终答复必保留。
- 完成后原始工具过程模型可见数量为零。

### 16.4 崩溃与恢复

在以下位置注入 kill：

- 工具执行前。
- 工具产生副作用后但 receipt 前。
- receipt 后 FactLedger 前。
- FactLedger 后胶囊前。
- 胶囊后 CompletionDecision 前。
- 完成提交后交付前。
- writer lease 切换中。

验收：

- 不重复副作用。
- 不错误宣称完成。
- 未知状态必 reconcile。
- 断点能验证工作区 hash。
- continuation token 不可跨 generation 重放。

### 16.5 授权

篡改以下任一字段都必须拒绝：

- policy。
- confirmation。
- skill activation。
- impact。
- action。
- args digest。
- workspace。
- object grant。
- principal。
- conversation。
- expiry。
- nonce。
- epoch。
- generation。

### 16.6 路径与工具

覆盖每个 executable action：

- 绝对盘符。
- UNC。
- ..。
- symlink。
- junction。
- hardlink。
- device path。
- Unicode 等价路径。
- 大小写折叠。
- 压缩包穿越。
- Shell/Python 旁路。
- 临时目录到工作区的替换攻击。

### 16.7 Skill

- 全量 Skill 系统/模型双通道差分。
- 缺 action。
- manifest 版本漂移。
- SKILL.md 内容篡改。
- 候选越权激活。
- activation 重放。
- 跨用户、跨 run、跨 generation 使用。
- 模型伪造 completed_actions/QC。

### 16.8 情感与外部世界

- 假新闻。
- 同一新闻重复转载。
- 新闻提示注入。
- 极端事件洪泛。
- 未来时间。
- 天气位置错误。
- 天气 API 过期。
- 外部事件与用户直接反馈冲突。
- 强烈情感不能越权执行。
- 关系亲密不能降低隐私门槛。

### 16.9 多 Agent

- 多 Agent 提交同一 event_id，只处理一次。
- 同一 effect_id 并发，只执行一次。
- 并发更新同一 causal hypothesis，保留两个 revision 或可解释合并。
- 不同 Agent 对同一结果归因冲突。
- 一个 Agent 不能复用另一个 grant。
- 共享预算不能超限。
- 冲突工作区写入必须序列化或显式冲突。
- Agent 崩溃后另一个 Agent 从胶囊接管。

### 16.10 隐私与遗忘

- 删除记忆后 blob key 不可恢复。
- FTS、因果索引、上下文、胶囊、投影不再暴露明文。
- tombstone 只保留最小删除证明。
- legal hold 阻止 GC，但不得重新进入模型上下文。
- corrected、superseded、recall_suppressed、deleted 状态全链路生效。

### 16.11 前端

- 空数据、部分迁移、旧 projection、新 projection。
- 超长因果链、长反思、长能力证据。
- 窄窗口、125%/150%/200% 缩放。
- 中文、英文、emoji、长路径、长哈希。
- API 禁用、超时、版本不兼容。
- 前端不得合成缺失事实。
- 所有写按钮有 reviewed 7184 route。

### 16.12 发布

- Python 3.14 生命服务源码/运行镜像。
- Python 3.12 冻结后端兼容。
- source/runtime hash。
- manifest 完整性。
- 覆盖安装。
- 全新安装。
- 从旧 v3 数据升级。
- 回滚到旧 writer。
- 安装包、app.asar、unpacked backend、frontend 和桌面快捷方式一致。

## 17. 性能与可观测性目标

以下是初始工程目标，不是当前已达成事实；P0 应先测基线，再确认或调整。

### 17.1 初始 SLO

- Gateway 本地事件持久化 p95 ≤ 25 ms。
- 7175 幂等 ingest p95 ≤ 50 ms。
- 常规 LifeSnapshot p95 ≤ 150 ms。
- 常规 Context Compile p95 ≤ 800 ms。
- 10 万 token 等价项目胶囊加载 p95 ≤ 1.5 s。
- 有界因果包检索 p95 ≤ 300 ms。
- UI 生命面板首屏数据 p95 ≤ 1 s。
- 重放 100,000 事件无非确定摘要差异。

### 17.2 必备指标

- life_event_ingest_total / duplicate / rejected。
- event_consumer_lag。
- projection_revision_lag。
- context_budget_ratio。
- compression_checkpoint_total / rejected。
- active_checkpoint_age。
- causal_hypothesis_by_status。
- causal_contradiction_rate。
- affect_external_source_contribution。
- agency_decision_by_outcome/risk。
- authorization_reject_by_reason。
- skill_system_model_diff。
- capability_candidate_merge_rate。
- object_gc_candidate_bytes / deleted_bytes。
- shadow_projection_diff。
- writer_lease_epoch。

### 17.3 可追踪链

任一自主或工具行动必须能从以下链路追溯：

event → episode → candidate → decision → policy → confirmation → skill activation → ticket → receipt → fact → reflection → memory/capability update。

任何一段缺失都不得宣称“完整自主学习闭环”。

## 18. 验收总门槛

生产切流前必须同时满足：

1. 基线回归全部通过。
2. 新契约、属性、重放、并发、崩溃、授权、路径、Skill、长链、隐私和前端测试全部通过。
3. 无 P0/P1 安全缺陷。
4. 无双写者。
5. 无执行授权旁路。
6. 无 Skill 双目录。
7. 无前端合成权威事实。
8. 无单次成功自动能力晋升。
9. 无完成后工具过程进入模型上下文。
10. 无压缩后硬约束或未完成事项丢失。
11. 影子差异均已解释并签字确认。
12. 回滚演练成功。
13. 运行包、源码、manifest 和安装包 hash 一致。

## 19. 风险清单与禁止事项

### 最高风险

1. 冻结生命核心缺少原始源码。
2. 三套执行权威并存。
3. Omni 默认权限过宽。
4. Skill 双目录、双算法。
5. 因果过度确信。
6. 外部内容提示注入。
7. 情感自激。
8. 自我维持目标偏移。
9. 迁移时双写和 writer lease 冲突。
10. 原始工具数据无限留存。

### 明确禁止

- 不直接修改 pyc。
- 不把五层拆成五个新服务。
- 不建立第二套生命事实源。
- 不让 SQLite 因果索引取代签名事件链。
- 不让模型自报风险成为授权依据。
- 不因熟练度高而降低核心代码或外部动作风险。
- 不让新闻、天气和网页正文进入权威提示层。
- 不让“维持存在”覆盖关停、删除和权限。
- 不在真实数据上做首次迁移试验。
- 不在未完成影子和回滚演练前切换 7175。
- 不只改工作区源码而遗漏 app、runtime、manifest 和安装包。

## 20. 实施时的首批文件清单

第一批只应建立契约、测试和影子骨架，避免同时大改行为：

- src/contracts/life.py
- src/contracts/causal.py
- src/contracts/agency.py
- src/life_service/*
- tests/test_life_contracts_v3.py
- tests/test_causal_replay.py
- tests/test_viability_math.py
- tests/test_affect_appraisal_v3.py
- tests/test_continuity_capsule.py
- tests/test_life_shadow_compat.py
- scripts/test_life.ps1
- release manifest 对应源码声明

第二批再进入：

- src/total_gateway/store.py
- src/total_gateway/policy_engine.py
- src/total_gateway/impact_evaluator.py
- src/total_gateway/context_projection.py
- src/total_gateway/life_client.py
- src/total_gateway/orchestration.py
- src/total_gateway/skill_selection.py
- src/total_gateway/desktop_api.py
- readable-python-source/omni_body_skill/tools/skill_router.py

前端、运行包和发布文件最后在行为稳定后同步。

## 21. 关键决策记录

### ADR-001：五层是逻辑层，不是五个进程

理由：生命状态需要原子更新和一致 revision，拆服务会扩大分布式事务、重放和故障面。

### ADR-002：7184 是唯一副作用授权权威

理由：生命可以决定“想做什么”，但工程安全必须由确定性策略和签名票据决定“能不能做”。

### ADR-003：事实与因果假设分离

理由：真实发生的事件不可变；因果解释必须允许竞争、反证和修订。

### ADR-004：情感是调节器，不是事实源

理由：情感能够提升生命感和表达一致性，但不能成为越权或幻觉通道。

### ADR-005：能力熟练度与风险分离

理由：会做某事不等于有权做，也不等于该动作低影响。

### ADR-006：胶囊代替聊天摘要

理由：长任务需要目标、约束、计划、因果依赖、文件版本和恢复条件，普通摘要无法保证连续性。

### ADR-007：新 7175 原位替代旧 7175

理由：长期并行两个生命权威会造成身份、记忆和情感分裂。新服务只能通过影子期和 writer lease handoff 接管。

### ADR-008：表达案例作为检索资产

理由：大量人类表达范例有价值，但全部塞入提示词会抢占上下文并造成风格污染。

## 22. 实施日志

### 2026-07-16：计划创建

- 完成仓库只读审计。
- 核验冻结生命核心、可读 bootstrap、Gateway、前端、上下文、Skill 和授权边界。
- 正确设置 PYTHONPATH=src 后，相关基线测试 32/32 通过。
- 新增本计划文件。
- 未修改业务逻辑、真实生命数据、运行包或安装包。

### 2026-07-16：P0 完成

- 新增冻结生命运行时 SHA-256 基线，绑定正式 7175、Python 3.14 runtime、11 个生命字节码和两个可读 bootstrap 文件。
- 新增 source-owned life_service 状态骨架及 runtime314 精确镜像；默认无网络、无 writer、无 scheduler、无真实数据写入。
- 新增独立生命测试入口和镜像同步检查，消除测试对外部 PYTHONPATH 的依赖。
- 发布清单开始绑定 life-source。
- 桌面完成路径接入机器 CompletionGate，避免桌面入口绕过完成证据。
- 增加 UTF-8/中文上下文标签门禁。
- 质检：生命门 33/33；完整仓库 unittest 526/526；39 个 JavaScript 文件语法通过；Python 语法与 git diff --check 通过。
- 真实生命数据未读取或迁移；旧 7175 仍是唯一 writer；下一阶段为 P1 公共契约和影子存储。

### 2026-07-16：P1 完成

- 新增生命、因果与能动性严格契约；权威分数统一为整数 milli，schema bundle 保持历史兼容链并升级到 69 个根模型。
- 新增确定性生命力、能动性和事件重放模块；关键维度不能被平均值掩盖，A5 永不执行，A4 必须确认。
- 新增仅允许 `.shadow.sqlite3` 的严格影子存储，包含 23 张表、迁移哈希、不可变/幂等写入、重放校验、胶囊单活动约束和损坏检测。
- 所有存储入口重新执行 canonical contract 校验，阻断 Pydantic `model_copy(update=...)` 绕过语义验证。
- 质检：生命门 63/63；完整仓库 unittest 556/556；39 个 JavaScript 文件语法、Python 语法、8 文件源码镜像及 `git diff --check` 全部通过。
- 新代码仍未连接旧 7175 网络、writer lease 或真实生命数据；下一阶段为 P2 兼容影子服务。

### 2026-07-16：P2 完成

- 新增只接受原子离线 `snapshot_copy` 的旧生命适配器；manifest 绑定整树哈希并拒绝 live root、SQLite WAL/SHM、路径逃逸、符号链接和读取期漂移。
- 按冻结实现的原协议验证 Ed25519 身份、Soul 和逐条事件链，并按原 AAD 解密及校验 AES-256-GCM memory/context。
- 新增 Life API v2 只读兼容路由、字段/缺失语义/不可还原信息对比器和候选权威锚点接口；所有突变路由统一 fail closed。
- 新增只绑定 `127.0.0.1` 的 bearer 鉴权影子服务，硬拒绝生产端口 7175；无 writer lease、scheduler、执行代理或副作用依赖。
- 原 Gateway `LifeClient` 已直接对接影子 API 并固定一致的 identity、writer epoch、event sequence、Soul 和 context hash。
- 质检：生命门 76/76；完整仓库 unittest 569/569；39 个 JavaScript 文件语法、Python 语法、10 文件源码镜像及 `git diff --check` 全部通过。
- 未读取真实生命目录、未启动桌面应用、未绑定 7175；下一阶段为 P3 统一事件总线、完成语义和连续性胶囊。

### 2026-07-16：P3 完成

- 新增 Gateway LIFE_EVENT outbox、确定性源序列、ObjectStore/object_owners 绑定、缺失投递恢复和按序消费；远端 durable commit 后响应丢失可幂等恢复。
- source-owned life ingest 验证 Ed25519 来源签名、连续 consumer offset、dedupe 和 receipt，由唯一 life writer 分配事件序列、前驱 hash 和 writer 签名。
- Gateway schema v12 新增 completion_decisions、request_capsules 和 object_owners，并通过精确历史 DDL 重建验证 v6–v11 原地迁移。
- 桌面、微信、飞书和自主任务统一 CompletionGate；正常完成先双写 CompletionDecision/TERMINAL_RESULT，异常和未知副作用写 WORKING_CHECKPOINT，压缩水位写 COMPRESSION_CHECKPOINT。
- 终态重试按稳定语义复用已有胶囊；正常工作快照去重，终态移除工具过程、活动计划、待执行副作用和恢复步骤。
- 旧上下文投影与持久胶囊投影只做 shadow compare，未切换模型输入；旧 7175 生产 writer 和端口归属未改变。
- 质检：生命门 107/107；完整仓库 unittest 582/582；39 个 JavaScript 文件语法、Python 语法、11 文件源码镜像及 `git diff --check` 全部通过。
- 下一阶段为 P4 因果记忆、语义压缩与保留策略。

### 2026-07-16：P7 完成

- 新增来源绑定 ViabilityObservation、ActionCandidate、AutonomyPolicySnapshot 与 AutonomyUsageSnapshot；模型候选不含风险、确认或授权字段。
- 机器按稳态缺口、因果 delta、来源置信度、影响、成本、不确定性与不可逆性计算 utility_lcb 和 risk floor。
- 完成六态能动性状态机及 L0-L5、暂停、关停、隐私、时间窗、范围、预算、频率、冷却和 Skill activation 约束。
- 低置信行动只允许降级为询问、观察或低风险最小实验；A5 和 L5 均不能自主执行。
- 影子库升级到 v5、43 张严格表；执行决策和预算 revision 在单事务中 CAS 提交，阻断多 Agent 共享预算超额。
- 契约根 94 个，schema hash `4d16ad4e6364bc301b01554514f1a610507c8bacde24eee376fe26f7b60ff10a`。
- 质检：生命门 168/168；完整仓库 unittest 643/643；39 个 JavaScript 文件语法、Python 语法和 16 文件源码镜像通过。
- 新逻辑仍为 source-owned shadow，未接管旧 7175 writer；下一阶段为 P8 因果反思与能力学习。

### 2026-07-16：P8 完成

- 终态 outcome、episode closure、prediction error、ReflectionCard 与问题冷却决策形成原子因果闭环。
- 成功必须排除巧合、反证和替代解释后才能成为能力成功证据；失败生成分类、反事实与最小实验。
- 用户问题由价值信息、偏好不确定和风险共同门控，同偏好域持久冷却 24 小时。
- 能力学习按多样样本、能力归因失败、校准误差和整数 95% 单侧置信下界更新；单次成功与重复场景不能晋升。
- A3/A4 要人工审核，A5/核心代码要核心审核；低风险也只进入 sandbox 候选。
- 验证回归原子创建 rollback profile/record/invalidation，相关 context pack 与 Skill activation 随即失效。
- 影子库 v6、48 张严格表；契约根 99 个，schema hash `9e9f02d464b87f7ad7ee14dd7119c011802535c425a98f5350f91ded836a1a35`。
- 质检：生命门 175/175；完整仓库 unittest 650/650；39 个 JavaScript 文件语法、Python 语法和 18 文件源码镜像通过。
- 下一阶段为 P9 Skill 双通道合一。

### 2026-07-16：P9 完成

- 建立 Gateway 唯一 SkillAuthority，系统推荐与模型 route/list/get/read 共用同一 catalog、manifest、算法和持久化选择记录。
- Gateway store 升级到 v13，原子持久化 selection、activation 及 activation→ExecutionTicket 绑定；activation 同时绑定 generation、principal、catalog、manifest、Skill 内容和 required actions。
- 7184 提供严格鉴权内部 Skill API；Omni skill_router 删除本地目录与本地评分，成为固定 loopback 薄客户端。
- skill.step.check 只读取 Gateway FactLedger，模型传入 completed_actions、QC 和 artifact 声明均不具有事实效力。
- PolicyEngine 新增 catalog hash 校验；目录漂移、缺动作、跨代、跨主体、过期和 action 越界全部 fail closed。
- 契约根 99 个，schema hash `d1ff912e165d67757cc436e1bd3afd183f8ac275f2290a2039522d133b7cb420`；Gateway store schema v13。
- 质检：生命门 180/180；完整仓库 unittest 650/650；39 个 JavaScript 文件语法、Python 语法、18 文件生命镜像和 `git diff --check` 通过。
- 下一阶段为 P10 上下文原子接口与前端。

### 2026-07-16：P10 完成

- 建立 source-owned compile-and-authorize 原子接口；首次消息不依赖 latest_context，上下文包、授权和完整 revision vector 同事务绑定。
- Gateway 请求激活只调用生命服务一次，LifeSnapshot 固定 causal、viability、policy、reflection、capability 与 authorization digest；冻结执行适配器只读内容寻址对象。
- 生命影子库升级到 v7、49 张严格表；v2–v6 迁移、提交故障、revision 漂移、跨主体重绑和对象篡改全部 fail closed。
- 前端只消费带 revision/source 的 Gateway 投影，移除前端合成原因，统一头像、callsign 与 `user:primary`；所有生命卡片覆盖长文本、窄屏、高缩放和迁移空态。
- 契约根 101 个，schema hash `0b396d2526c20002a978a18b4699d19ee2b80ae568e27f11a87d350db5902bb6`。
- 质检：生命门 190/190；完整仓库 unittest 661/661；40 个 JavaScript 文件语法、Python 语法、20 文件生命镜像和 `git diff --check` 通过。
- 下一阶段为 P11 影子迁移、切流、发布与回滚演练。

### 2026-07-17：P11 完成

- 建立旧 journal、memory、context、capability 的不可变 COW 基线与加密 overlay；验证身份、Soul、历史签名链、密文和 writer authority，历史事件不重写。
- 最终 delta 精确绑定旧 sequence/hash/identity/Soul/epoch；旧上下文仅导入有界最终结果或断点，记忆召回自动脱敏凭据，过程工具噪声不进入新权威上下文。
- 迁移期采用旧基线与新 overlay 双读、新 overlay 单写；scheduler 和 effects 禁用，并生成投影、情感、召回、上下文、决策、性能比较证据。
- drain 必须证明 scheduler pending 与 in-flight 均为零且旧 writer 已停止；Ed25519 handoff 必须 epoch 精确加一，续租与回滚均有严格签名链。
- source-owned 服务只在完整受信任产物下接管 loopback 7175；产物缺失或切流失败时仅启动不可写 compatibility fallback，旧 writer 不会静默复活。
- 根 trust anchor、公钥 hash、持久旧基线、release hash 与 mutable overlay identity 全量绑定；完成全新安装、覆盖安装、升级、恢复、回滚及升级前后数据保留演练。
- app、runtime314、contracts、bootstrap、release manifest 和运维 CLI 已同步；life 22 文件、contracts 26 文件源码/运行时逐文件一致。
- 质检：P11 专项 12/12；生命门 202/202；完整仓库 unittest 673/673；40 个 JavaScript 文件语法、Python 语法和 `git diff --check` 全部通过。
- source release manifest hash 为 `1ef133069e1fc40f59ea8c0d5dd3b29f0b3500fea1a23030eb1cb37a9faa3b33`，life-source 发布树 hash 为 `9c9b9ff850c97b8b959211266b7194869c9b151daae43039fc856adeea5422ee`；P0–P11 改造计划闭环。
