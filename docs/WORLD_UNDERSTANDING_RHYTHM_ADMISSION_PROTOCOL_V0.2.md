# 天工造物 V3：世界理解节律与准入协议 V0.2

> English: World Understanding Rhythm & Admission Protocol V0.2  
> 状态：设计冻结候选 / 已完成协议级反证 / 尚未实现 / 尚未接入 Runtime  
> 基线：`agent/world-understanding-rhythm-admission-v0.1`  
> 上一版：`WORLD_UNDERSTANDING_RHYTHM_ADMISSION_PROTOCOL_V0.1.md`  
> 本版新增：事件边界、分级跳转、资源预算、强中断、再验证节律、空闲 Replay、Self-Will/Autonomy 联动、World Inquiry Interface、自我强化闭环防护。

---

# 0. 协议定义

世界理解节律与准入协议不是“世界理解器”，也不是“第二 Runtime”。

它只解决四个问题：

1. **什么时候值得看？**
2. **看到了以后，什么时候值得深入理解？**
3. **理解之后，什么时候有资格进入长期世界认知？**
4. **已经形成的认知，什么时候需要重新验证？**

永久冻结两条原则：

> **值得注意 ≠ 值得相信。**

> **节律控制认知资源，证据控制认知真实性。**

因此：

- Novelty、Prediction Error、Event Boundary、Task Relevance、Impact 等只能提高处理优先级；
- Evidence、Provenance、Authority、Coverage、Freshness、Directness、Independent Roots 才能提高稳定认知资格；
- LLM 可以解释、归纳、形成 Hypothesis，但不能因为“重要”“符合直觉”“重复出现”而绕过证据门；
- Query/Projection 不得反向强化 Cognition；
- Prediction 不得直接变成 Evidence；
- 世界理解系统不得自行获得现实执行权。

---

# 1. 第一性原理

现实输入近似连续，而深层推理、LLM 调用、图计算、认知固化、再验证都是有限资源。

如果所有现实输入都进入 LLM：

- 成本随输入频率近似线性膨胀；
- 大量重复世界被重新理解；
- 低价值噪声抢占认知预算；
- 高频更新会导致长期世界模型震荡。

如果所有被 LLM 认为“重要”的内容都直接写入长期认知：

- 新奇性会被误当成真实性；
- 重复会被误当成独立证据；
- Schema 一致性会形成确认偏误；
- LLM 自我生成内容会形成自证闭环。

因此世界理解必须采用不同时间尺度：

```text
高频感知
  ↓
低成本过滤与事件聚合
  ↓
事件驱动的语义升级
  ↓
稀疏候选形成
  ↓
严格证据固化
  ↓
自适应再验证
```

正式原则：

> **High-frequency sensing, sparse semantic escalation, rare durable consolidation, event-driven revalidation.**

---

# 2. 生物学 / 神经科学机制映射

本协议吸收机制原则，不机械仿脑。

## 2.1 Predictive Coding

Rao & Ballard (1999) 的 predictive coding 思路说明，高层预测与实际输入之间的误差比重复输入更值得继续处理。

工程映射：

- 世界符合既有预期时，降低深层认知频率；
- 世界变化、异常、违反结构预期时，提高认知处理优先级；
- 完整 L7 Prediction 未实现前，可先使用 hash、schema expectation、state invariant 计算局部 prediction error。

## 2.2 Event Segmentation

连续经验并非无边界存储，而会被切成事件。

工程映射：

- 感知可以连续；
- consolidation 不按固定秒数触发；
- Git commit、任务阶段结束、世界 scope 切换、事务闭合、用户明确纠正等是高价值事件边界。

## 2.3 Complementary Learning Systems

快速记录具体经历与缓慢提取稳定结构应采用不同节律。

工程映射：

- Observation / Working World Model 可以快；
- Persistent Cognition 必须慢；
- 一次观察不得直接改写高稳定认知；
- 新信息先进入工作层 / Candidate，再经过证据门。

## 2.4 Schema 加速但不增信

已有 Schema 可以降低新信息整合成本，但不能提高其现实真实性。

工程冻结：

> **Schema Compatibility 的 empirical evidence weight 永远为 0。**

它可以：

- 降低解析成本；
- 缩短等待时间；
- 提高 consolidation scheduling priority。

它不可以：

- 提高 Authority；
- 提高 Provenance；
- 增加 Independent Evidence Root；
- 直接提高 Cognition Stability。

## 2.5 Reconsolidation 与 Prediction Error

被再次读取的旧认知，不应仅因为被读取而变得更可信。

工程冻结：

- retrieval path 永久只读；
- Query 不刷新 confidence；
- Projection 不生成证据；
- 只有反证、scope shift、来源变化、依赖变化、prediction error、stale-risk 达阈值等才进入 revalidation。

## 2.6 Prioritized Replay

空闲时不平均处理所有候选，而优先处理最值得重新理解的部分。

工程映射：

```text
Replay Priority
≈ Future Need × Expected Gain × Evidence Gap × Conflict Pressure
```

---

# 3. AI 长期认知系统的工程映射

近期 agent memory / world-model 工作共同指出：长期状态写入不应由单一 LLM yes/no 决策承担。

天工采用的工程结论：

- 明确重复优先 NOOP；
- 模糊信息才升级昂贵推理；
- recurrence 只能提高“值得总结”的优先级，不能自动制造独立真实性；
- consolidation transition 必须可验证；
- retrieval / consolidate / forget / revalidate 本身属于控制问题。

因此，本协议拒绝单一 `importance_score > threshold → write` 模型。

---

# 4. 在九层世界理解架构中的位置

```text
L0 World Frame
       │
       ▼
L1 Perception
       │
       ▼
L2 Entity
       │
       ▼
L3 Relationship
       │
       ▼
L4 Semantic & Causal Understanding
       │
       ▼
L5 Cognition Core
       │
       ▼
L6 Persistent World Model
       │
       ▼
L7 Dynamics & Prediction
       │
       ▼
L8 Query & Projection

──────── 横向贯穿 ────────
Rhythm & Admission
Evidence / Provenance / Time / Scope / Conflict / Budget
```

节律与准入不是第 10 层，而是横向控制各层信息什么时候允许升级、停留、退回、重新验证。

---

# 5. 五级准入门

## G1 — Observation Admission

问题：

> 这个现实信号是否值得成为正式 Observation？

输入可能包括：

- 文件变化；
- Git diff；
- Runtime event；
- ToolResult；
- 用户输入；
- Web / document perception；
- device state；
- 自主行动结果。

G1 主要是廉价、确定性的：

- schema validation；
- deduplication；
- scope binding；
- source binding；
- timestamp validation；
- event coalescing；
- obvious noise filtering。

G1 不调用昂贵 LLM 作为默认路径。

输出：`WorldObservation`。

---

## G2 — Understanding Escalation

问题：

> 这条 Observation 是否值得继续花认知资源进行实体、关系、语义、因果理解？

定义 Attention / Escalation Score：

```text
A = ScopeGate × [1 - Π(1 - w_j x_j)]
```

候选特征：

- Novelty；
- Prediction Error；
- Event Boundary；
- Graph Centrality；
- Task Relevance；
- Independent Recurrence；
- Current Uncertainty；
- Potential Impact；
- Conflict Pressure。

`A` 只决定处理优先级，不代表真实性。

输出可能为：

- NOOP；
- LOW_COST_PARSE；
- STRUCTURAL_ANALYSIS；
- SEMANTIC_ESCALATION；
- LLM_HYPOTHESIS_REQUEST。

---

## G3 — Candidate Cognition Admission

问题：

> 当前理解是否已经足够明确，可以形成一条可验证的 Candidate Cognition？

Candidate 必须：

- 绑定 WorldFrame；
- 绑定 Entity / Relation / Subject；
- 具有明确 predicate；
- 具有明确 value；
- 具有 Evidence 引用或明确 Evidence Gap；
- 具有 hypothesis origin；
- 不能把 model inference 伪装成现实证据。

输出：`CognitionProposal / Candidate Cognition`。

---

## G4 — Stable Cognition Admission

问题：

> Candidate 是否有资格成为长期稳定世界认知？

此门完全交给现有 Cognition Core 的 deterministic evidence gate。

单条证据质量：

```text
q_i = Authority_i
    × ProvenanceIntegrity_i
    × Coverage_i
    × Freshness_i
    × Directness_i
```

同 lineage / 同有效独立组先折扣；跨独立组再聚合。

支持：

```text
S = 1 - Π_g(1 - G_g)
```

反证：

```text
C = 1 - Π_h(1 - H_h)
```

净支持：

```text
M = max(0, S - C)
```

`M` 是 Evidence Support Margin，不是现实概率。

硬规则继续沿用：

- C1：至少 1 个有效独立根；
- C2：至少 2 个独立根 + 至少一个直接强现实证据组；
- C3：至少 3 个独立根 + 强现实支持；
- C4：禁止自动晋升，仅受可信显式系统权威 / migration 保护。

输出：`CognitionStatement`。

---

## G5 — Revalidation Admission

问题：

> 已经形成的认知现在是否值得重新验证？

主要触发：

- strong counterevidence；
- dependency change；
- source invalidation；
- scope change；
- prediction error；
- ontology change；
- stale-risk 达阈值；
- 用户明确纠正；
- 世界核心边界发生改变。

注意：

> **进入 Revalidation 不等于旧认知已经错误。**

只代表旧认知需要重新接受现实检验。

---

# 6. 五级准入是否允许跳级

结论：**允许有限快速通道，但禁止绕过真实性门。**

## 6.1 普通信息

普通信息必须遵循：

```text
G1 → G2 → G3 → G4
```

不允许因为 LLM 认为重要就直接进入 G4。

## 6.2 强现实证据快速通道

以下信息可以跳过部分“语义理解成本”，直接形成结构化 Candidate：

- FactKernel / execution verified outcome；
- Git 确定性事实；
- 文件系统确定性事实；
- 可信系统 migration；
- 用户明确声明且该认知类型本身就是 user-asserted 类型。

允许：

```text
G1 → deterministic normalization → G3 → G4
```

禁止：

```text
G1 → Stable Cognition
```

即使是强现实证据，也必须经过 scope/provenance/evidence contract 与 Cognition Core。

### 冻结原则 A

> **允许强现实证据走快速通道，但绝不允许绕过真实性校验。**

---

# 7. 什么算事件边界

Event Boundary 用于决定何时聚合、总结、形成候选、进入 consolidation window。

事件边界分四类。

## 7.1 强边界 Strong Boundary

默认立即成立：

- WorldFrame / principal / project / repository / branch 切换；
- Git commit / merge / checkout；
- 用户明确纠正已有事实；
- 任务完成 / cancel / failed terminal；
- 事务 commit / rollback；
- Runtime authoritative state transition；
- 核心权限边界改变；
- 明确生命周期事件。

## 7.2 中边界 Semantic Boundary

需要上下文判断：

- 一个任务阶段完成；
- 一组文件修改 burst 结束；
- 一段持续工具链闭合；
- 一个问题域从 A 明确切换到 B；
- 一个会话子目标完成。

## 7.3 弱边界 Temporal Boundary

仅作为 fallback：

- 一段时间无新相关事件；
- idle window；
- burst quiet period。

弱边界不能单独证明事件语义结束，只提供 consolidation opportunity。

## 7.4 非边界

默认不视为事件边界：

- 单次文件保存；
- 单次普通工具成功；
- 普通一句对话结束；
- 单次 Query；
- 单次 Projection；
- 单个低影响 Runtime log。

这些可以被聚合进更大事件。

---

# 8. 高频到底允许多高：资源预算模型

协议不冻结“每秒 N 次”作为全局真理。

必须区分成本等级：

```text
C0 纯事件/哈希/计数/去重
C1 解析/局部图更新
C2 图查询/静态分析/局部规则推理
C3 小型 LLM 语义理解
C4 大型 LLM / 多步推理 / 大范围重建
```

调度原则：

- G1 优先使用 C0/C1；
- G2 优先使用 C1/C2，只有模糊区域升级 C3；
- G3 可以使用 C3，但不要求每个 Candidate 都调用 LLM；
- G4 尽量 deterministic；
- 大范围 ontology / architecture rebuild 才进入 C4。

世界理解维护必须有独立 `CognitionBudget`，至少包含：

- max LLM calls / time window；
- max tokens / time window；
- max graph rebuild cost；
- max replay items / idle window；
- max concurrent revalidation；
- per-domain fairness quota。

预算不足时：

```text
Observation 不丢
↓
进入 backlog / coalesced event
↓
按优先级延迟理解
```

禁止通过丢弃高价值 Observation 来伪装性能稳定。

---

# 9. 什么情况必须立即中断正常节律

以下触发 `Urgent Revalidation` 或立即 `CHALLENGED/STALE` 标记：

1. 强直接反证；
2. WorldFrame 改变；
3. 用户明确纠正；
4. 权限 / 执行边界改变；
5. 核心代码结构 / 核心依赖改变；
6. authoritative source 被撤销 / 失效；
7. identity resolution 发生 merge/split；
8. 明显 prediction error 超过域阈值；
9. 影响面被判定为 critical；
10. 发现 provenance 伪造或 lineage 污染。

中断动作只能是：

- mark challenged；
- mark stale；
- schedule revalidation；
- isolate suspect evidence；
- emit World Inquiry。

不能直接：

- 自动接受新答案；
- 修改现实；
- 绕过权限；
- 宣告任务完成。

---

# 10. 自适应再验证节律

固定“每 10 分钟检查所有认知”不可接受。

对某类世界对象 / relation / cognition 估计变化率 `λ`。

近似 stale-risk：

```text
P_stale(Δt) = 1 - exp(-λ Δt)
```

给定允许 stale 风险 `δ`：

```text
Δt_max = -ln(1 - δ) / λ
```

结论：

- 高频变化对象 → 高频再验证；
- 长期稳定结构 → 极低频再验证；
- 事件驱动 invalidation 永远优先于 timer。

`λ` 不写死，按领域校准。

可以使用带先验的变化率估计，例如 Gamma-Poisson：

```text
λ | events ~ Gamma(α0 + k, β0 + exposure)
```

这样少样本领域不会因为一次偶然事件就产生极端频率。

协议冻结算法方向，不冻结具体秒数、分钟数、阈值。

---

# 11. 防止频率震荡：Hysteresis

所有 admission / de-escalation 决策必须避免单阈值抖动。

例如：

```text
enter_threshold = 650
hold_threshold  = 500
```

规则：

- `score >= enter_threshold`：进入；
- 已进入状态且 `score >= hold_threshold`：保持；
- `score < hold_threshold`：退出 / 降级。

不得因为：

```text
599 → 601 → 599 → 602
```

形成不停进入 / 退出的认知震荡。

---

# 12. 事件合并与重复控制

高频世界中，相邻变化默认先聚合成 `EventCluster`。

聚合 key 至少包含：

- WorldFrame；
- domain；
- entity / scope；
- source class；
- short temporal window；
- causal / transaction binding（如果存在）。

同一来源的 100 次重复信号不能变成 100 个独立证据根。

独立重复可以提高：

- attention；
- recurrence；
- replay priority。

同 lineage 重复不能提高：

- authority；
- provenance；
- independent root count。

---

# 13. Schema 快速通道

当新信息高度符合已有 Schema 时，允许：

- 降低解析成本；
- 缩短 candidate waiting period；
- 提高 consolidation scheduling priority。

但是：

```text
schema_match ≠ evidence
schema_match ≠ authority
schema_match ≠ independent confirmation
```

如果新信息与 Schema 冲突，反而应提高 G2 escalation 和 G5 revalidation priority。

---

# 14. 空闲期 Replay / Consolidation

空闲期允许世界理解系统自行执行内部认知维护：

- replay 已有 Observation；
- candidate merge / dedup；
- conflict analysis；
- consolidation；
- deterministic revalidation；
- stale assessment；
- graph consistency check；
- prediction error retrospective analysis；
- ontology candidate organization。

这些活动属于“内部认知维护”，不等于现实行动。

Replay Priority 建议：

```text
R_i = Gain_i × Need_i × EvidenceGap_i × Conflict_i × FreshnessNeed_i
```

其中：

- Gain：重新理解可能改变多少现有世界模型；
- Need：未来多大概率会被使用；
- EvidenceGap：证据缺口；
- Conflict：冲突压力；
- FreshnessNeed：陈旧风险。

不采用纯 FIFO。

---

# 15. 与自我意志 / Autonomy 的正式连接

这是 V0.2 最重要新增边界。

天工现有 Life / Autonomy 系统允许在无用户即时指令时产生自主任务，因此世界理解不能永久禁止“主动探索现实”。

但探索权不能属于世界理解系统本身。

正式冻结：

> **世界理解可以产生“我还需要知道什么”，但不能自己决定“我要去做什么”。**

因此建立唯一桥：

# World Inquiry Interface

世界理解在以下情况下可以产生 `WorldInquiry`：

- Evidence Gap；
- unresolved conflict；
- unknown high-impact entity/relation；
- stale high-value cognition；
- prediction error 无法通过已有证据解释；
- ontology ambiguity；
- cross-domain identity ambiguity；
- revalidation 需要新的现实观察。

`WorldInquiry` 只是一条“求知需求”，不是任务指令。

建议 Contract：

```text
WorldInquiry
├─ inquiry_id
├─ world_frame_id
├─ domain
├─ target_subject
├─ question
├─ epistemic_gap
├─ expected_information_gain
├─ urgency
├─ impact_if_unknown
├─ current_evidence_refs
├─ forbidden_self_evidence_refs
├─ suggested_observation_types
├─ created_at
└─ expires_at
```

注意：

- `suggested_observation_types` 只能描述需要哪类信息；
- 不得包含“必须调用哪个工具”的强制执行命令；
- 不得携带权限票据；
- 不得自动变成 Runtime action。

---

# 16. World Inquiry → Self-Will / Autonomy → Reality 闭环

正式链路：

```text
World Model
   │
   ▼
发现未知 / 冲突 / 陈旧 / prediction error
   │
   ▼
World Inquiry
   │
   ▼
Self-Will / Autonomy
   │
   ├─ 忽略
   ├─ 延后
   ├─ 合并进已有自主目标
   └─ 决定发起行动
          │
          ▼
现有 Runtime / Gateway / Risk / Permission / Tool
          │
          ▼
Reality
          │
          ▼
ToolResult / Fact / Observation
          │
          ▼
World Understanding G1
          │
          ▼
新的 Evidence / Cognition
```

Self-Will / Autonomy 保留完整决策权：

- 要不要调查；
- 什么时候调查；
- 是否值得消耗资源；
- 如何拆任务；
- 使用哪些工具；
- 是否放弃。

世界理解不能越权。

### 冻结原则 B

> **空闲期世界理解可以自行整理已有世界信息；需要新的现实探索时，只能产生 World Inquiry，并交由现有自我意志系统决定是否行动。**

---

# 17. 自我强化闭环防护

必须禁止：

```text
我怀疑 A
↓
我产生 Inquiry 调查 A
↓
自我意志接受了 Inquiry
↓
因此 A 更可信
```

这属于伪证据闭环。

正式规则：

1. `WorldInquiry` 的 evidence weight = 0；
2. “Autonomy 接受 Inquiry” evidence weight = 0；
3. “生成了自主任务” evidence weight = 0；
4. “调用了某工具”本身不证明目标命题；
5. 只有工具产生的真实 Observation / verified Fact / authoritative result 可以作为新 Evidence；
6. 新 Evidence 必须保留 Inquiry / task lineage，但 Inquiry 祖先不得被计为独立现实根；
7. 如果 Observation 内容源自系统自己的旧 Cognition，必须保留 `ancestor_cognition_ids`，防止自我确认。

因此：

> **求知行为可以增加观察机会，但不能增加命题真实性。**

---

# 18. Query / Projection 绝不强化认知

以下操作 evidence weight 永久为 0：

- cognition 被检索；
- cognition 被投影进 Prompt；
- LLM 读取后再次复述；
- LLM 根据旧 cognition 生成同义句；
- replay 只是重复读取；
- 自我意志使用 cognition 作为决策上下文。

只有新的外部现实观察或可验证内部执行事实，才能成为新 Evidence。

---

# 19. Prediction 与现实严格分离

`WorldPrediction` 永远不是 `WorldEvidence`。

```text
Prediction(t)
   ↓
等待未来 Observation(t+1)
   ↓
比较
   ↓
Prediction Error
```

禁止：

```text
Prediction → Evidence
```

允许：

```text
Prediction + later Observation
→ prediction accuracy record
→ dynamics model calibration
```

---

# 20. “不知道”和“不是”严格分离

世界理解采用开放世界语义：

```text
TRUE
FALSE
UNKNOWN
CONFLICTED
```

节律系统不得因为长时间没有看到某物，自动把 `UNKNOWN` 推成 `FALSE`。

负证据必须具有：

- explicit search scope；
- adequate coverage；
- source authority；
- time validity。

因此：

> **Not observed ≠ Not existent。**

---

# 21. 高影响未知不能被预算永久饿死

Budget 不允许产生“永远没空理解核心风险”的 starvation。

采用 aging + priority floor：

```text
EffectivePriority
= BasePriority
+ AgingBonus
+ CriticalityFloor
```

对以下项目设置最低服务保证：

- critical conflict；
- security boundary；
- world frame mismatch；
- high-impact stale cognition；
- unresolved authoritative contradiction。

即使低优先级领域长期堆积，也不能挤死关键 revalidation。

---

# 22. 多领域公平调度

世界理解未来包含 Software / User / Device / Self / Environment 等领域。

不能因为 Software World 高频事件最多，就吞掉全部认知预算。

因此 CognitionBudget 至少要有：

- global budget；
- per-domain minimum share；
- per-domain burst ceiling；
- critical preemption；
- backlog aging。

默认调度可以采用 weighted fair queue，而不是单纯最高分优先。

---

# 23. LLM 冷却与语义抖动控制

同一 EventCluster 不应在短时间内被多个 LLM 重复解释。

必须建立：

- semantic cooldown；
- hypothesis dedup；
- equivalent proposal merge；
- repeated prompt suppression。

只有以下情况允许突破 cooldown：

- new independent evidence；
- strong counterevidence；
- world frame change；
- user correction；
- critical prediction error。

---

# 24. Failure Semantics

节律系统故障时必须区分读路径与写路径。

## 24.1 感知 / 调度故障

- 不伪造“已理解”；
- Observation 能保留则进入 backlog；
- 无法保留则明确记录 dropped reason / telemetry；
- 不因内部调度失败改变 Reality。

## 24.2 Cognition Core 故障

- 不写稳定认知；
- 不降级为“LLM 直接写”；
- 保留 Candidate / Evidence 等待恢复。

## 24.3 World Inquiry 交付失败

- Inquiry 可以 pending / expired；
- 不自动转为工具调用；
- 不因交付失败改变认知真实性。

---

# 25. 权限与执行边界

Rhythm & Admission 永久无权：

- 直接调用现实工具完成任务；
- 创建第二 Runtime；
- 绕过 Gateway；
- 绕过 A0-A5；
- 生成有效执行凭证；
- 修改用户现实文件作为“认知维护”；
- 宣告现实任务完成；
- 将 World Inquiry 当成授权。

它可以：

- 读取合法提供的 Observation / Evidence；
- 控制认知资源调度；
- 标记 stale/challenged；
- 形成 Candidate；
- 触发内部 replay；
- 产生 World Inquiry。

---

# 26. 与现有 V3 自主系统的未来接入原则

未来实现时必须绑定现有 Life / Autonomy 主链，而不是另起 autonomy loop。

世界理解只新增一个“认知需求来源”，类似：

```text
autonomy input source:
  world_inquiry
```

Autonomy 仍然使用自身既有：

- heartbeat；
- model decider；
- pending tasks；
- risk level；
- existing action / capability path。

禁止：

```text
World Understanding
→ 新后台线程
→ 自己调用工具
```

必须：

```text
World Understanding
→ WorldInquiry
→ Existing Autonomy
→ Existing Runtime
```

---

# 27. Protocol State Machine

建议统一节律状态：

```text
OBSERVED
  ↓
COALESCED
  ↓
QUEUED_FOR_UNDERSTANDING
  ↓
UNDERSTANDING
  ↓
HYPOTHESIS_READY
  ↓
CANDIDATE
  ↓
CONSOLIDATION_PENDING
  ↓
STABLE / PROVISIONAL
```

旁路：

```text
OBSERVED → NOOP_DUPLICATE
OBSERVED → ARCHIVED_LOW_VALUE
CANDIDATE → EVIDENCE_GAP
EVIDENCE_GAP → WORLD_INQUIRY_PENDING
STABLE → CHALLENGED
CHALLENGED → REVERIFYING
REVERIFYING → CONFIRMED / UPDATED / RETIRED
```

所有状态转换必须有 `reason_code`。

---

# 28. 最小公共 Contract 集

未来实现前建议冻结以下 Contract：

```text
WorldRhythmSignal
EventBoundary
EventCluster
AdmissionDecision
CognitionBudget
RevalidationRequest
WorldInquiry
ReplayCandidate
RhythmPolicy
RhythmDecisionTrace
```

每个 Decision Trace 至少记录：

- input refs；
- world frame；
- gate；
- policy version/hash；
- score components；
- budget state；
- outcome；
- reason codes；
- timestamp。

用于可审计与未来参数校准。

---

# 29. 数学口径冻结

## 29.1 Attention Score 与 Truth Support 分离

Attention：

```text
A = ScopeGate × [1 - Π(1 - w_j x_j)]
```

Truth Support：

```text
q_i = authority × provenance × coverage × freshness × directness
```

二者不得混用。

## 29.2 独立性必须基于 lineage

同一根证据复制 N 次，不得近似 N 倍增强。

继续使用 lineage connected component / effective independence group。

## 29.3 同源重复折扣

同源 recurrence 可采用几何折扣：

```text
1 + γ + γ² + ...
```

其中 `0 < γ < 1`。

γ 是政策参数，不写死为真实性概率。

## 29.4 重验证风险

```text
P_stale(Δt) = 1 - exp(-λ Δt)
```

仅用于调度，不用于直接改变 cognition truth state。

---

# 30. 反证测试：协议必须抵抗的攻击

## A. 高频噪声洪泛

攻击：大量低价值文件事件挤爆 LLM。

处理：G1 coalescing + budget + event cluster + G2 escalation。

## B. 复制证据制造共识

攻击：同一来源复制 100 份。

处理：lineage collapse + independence recompute。

## C. 新奇即真实

攻击：异常内容因为 Novelty 高直接进入稳定认知。

处理：Attention 与 Evidence 完全分离。

## D. Schema 确认偏误

攻击：符合已有世界观就更可信。

处理：schema compatibility evidence weight = 0。

## E. Query 自我强化

攻击：认知被读取很多次后 confidence 上升。

处理：retrieval/projection/read evidence weight = 0。

## F. 自主探索自我证明

攻击：系统产生 Inquiry，自我意志接受 Inquiry，于是原假设被增强。

处理：Inquiry/acceptance/task/tool-call 本身 evidence weight = 0；只接受新 reality observation。

## G. Prediction 自证

攻击：预测内容被回写成“观察”。

处理：Prediction 与 Observation 类型硬分离。

## H. 固定频率浪费

攻击：稳定世界持续高频重扫。

处理：adaptive hazard + event invalidation。

## I. 低频对象永不复查

攻击：λ 很低导致核心认知永久不验证。

处理：max stale-risk / authority-source change / critical event 强触发。

## J. 单阈值震荡

攻击：score 在阈值附近反复进出。

处理：hysteresis。

## K. Software World 吞噬全部预算

攻击：代码事件高频导致 User/Device 永远无资源。

处理：weighted fair budget + minimum domain share。

## L. 世界理解变成第二 Agent

攻击：发现未知后直接调用工具。

处理：World Inquiry 只到 Existing Autonomy；世界理解不拥有 tool authority。

## M. Autonomy 绕过世界真实性

攻击：自主任务“完成”被自动视为 cognition true。

处理：任务完成不是命题证据；必须解析实际 ToolResult / Fact / Observation。

## N. World Inquiry 注入执行命令

攻击：Inquiry 偷带具体高风险 action，形成旁路。

处理：Inquiry 只描述 epistemic need / desired observation type，不携带执行票据和强制 action。

---

# 31. 协议级闭合性检查

本版检查以下边界是否闭合：

## 时间边界

已闭合：高频感知、事件聚合、低频理解、稀疏固化、自适应再验证、idle replay。

## 真实性边界

已闭合：Attention 与 Evidence 分离；G4 由 Cognition Core 接管。

## 自主性边界

已闭合：World Inquiry → Existing Autonomy；世界理解无现实行动权。

## 自证边界

已闭合：Inquiry / Query / Prediction / LLM inference 均不能自行成为现实证据。

## 资源边界

已闭合：budget、fairness、aging、critical preemption、cooldown。

## 作用域边界

已闭合：所有 signal / event / candidate / inquiry 必须绑定 WorldFrame。

## 更新边界

已闭合：strong interrupt、challenge、revalidation、hysteresis、adaptive stale risk。

## Runtime 边界

已闭合：协议不新增 Runtime、不新增 tool loop、不绕过既有权限与执行体系。

结论：

> 在 V0.2 定义范围内，当前未发现仍需新增一个独立“节律层级”才能解决的结构性问题。

剩余问题属于后续 Contract 字段、参数校准、领域策略和实现测试，不属于顶层协议缺口。

---

# 32. 实现前的冻结要求

在写代码之前必须先完成：

1. `WorldFrame` Contract；
2. `WorldObservation` Contract；
3. `WorldEntity` / `WorldRelation` Contract；
4. `WorldHypothesis` Contract；
5. 现有 Cognition Contract 对齐；
6. `WorldState` / model snapshot Contract；
7. `WorldPrediction` Contract；
8. `WorldProjection` Contract；
9. 本协议的 10 个公共 Rhythm Contract；
10. 九层之间的输入输出和禁止反向依赖规则。

在上述契约冻结前，不接 `zongdiaodu.py`，不接正式 Runtime，不建立第二后台执行链。

---

# 33. 参考研究

生物学 / 神经科学：

- Rao, R. P. N. & Ballard, D. H. *Predictive coding in the visual cortex*. Nature Neuroscience 2, 79–87 (1999). DOI: 10.1038/4580
- McClelland, J. L., McNaughton, B. L. & O'Reilly, R. C. *Why there are complementary learning systems in the hippocampus and neocortex*. Psychological Review 102, 419–457 (1995). DOI: 10.1037/0033-295X.102.3.419
- Tse, D. et al. *Schemas and memory consolidation*. Science 316, 76–82 (2007). DOI: 10.1126/science.1135935
- Zheng, J. et al. *Neurons detect cognitive boundaries to structure episodic memories in humans*. Nature Neuroscience 25, 358–368 (2022). DOI: 10.1038/s41593-022-01020-w
- Sevenster, D., Beckers, T. & Kindt, M. *Prediction error governs pharmacologically induced amnesia for learned fear*. Science 339, 830–833 (2013). DOI: 10.1126/science.1231357
- Mattar, M. G. & Daw, N. D. *Prioritized memory access explains planning and hippocampal replay*. Nature Neuroscience 21, 1609–1617 (2018). DOI: 10.1038/s41593-018-0232-z

AI / Agent memory：

- CoALA: arXiv:2309.02427
- A-MAC: arXiv:2603.04549
- SAGE: arXiv:2605.30711
- RecMem: arXiv:2605.16045
- MemCon: arXiv:2607.13591
- TRUSTMEM: arXiv:2606.25161

---

# 34. 最终冻结语句

**世界理解节律不是一个计时器，而是一套认知资源调度协议。**

**高频的是感知，不是深度思考。**

**新奇性决定“值不值得研究”，证据决定“值不值得相信”。**

**强现实证据可以走快速通道，但不能绕过真实性校验。**

**空闲期可以整理已有世界；需要新现实观察时，世界理解只提出 World Inquiry，自我意志决定是否行动。**

**认知产生求知需求，意志决定行动，身体探索现实，感知产生证据，证据再修正认知。**

这条闭环是本协议与天工造物“工程生命体”架构之间的正式连接点。
