# 天工造物 V3：World Data Model V0.1

> English: World Data Model V0.1  
> 状态：**正式设计冻结 / Contract Baseline / 尚未实现 / 尚未接入 Runtime**  
> 分支：`agent/world-data-model-v0.1`  
> 基线：`75f1d40026a6517bcd3ce579fd84119925169693`（World Understanding Rhythm & Admission Protocol V0.2）  
> 适用范围：World Understanding L0-L8 的统一数据模型。  
> 不改变现有单 Runtime、总网关、Omni Body、Execution Integrity、FactKernel、自我意志系统的权限边界。

---

# 0. 冻结结论

World Data Model V0.1 只定义“世界理解中的信息如何合法存在、如何被引用、如何被追溯、如何被投影给 LLM”。

它不定义：

- Runtime 如何执行现实动作；
- LLM 如何选择工具；
- Self-Will 是否发起自主行动；
- A0-A5 如何裁决；
- 某个领域如何解析 Python、Git、Windows、网页或用户行为；
- L4 具体使用哪个模型；
- L8 具体使用哪个排序算法。

本 Contract 永久冻结七条架构原则：

1. **九层是信息转换层，不是九个服务、九个 Agent 或九套 Runtime。**
2. **World Store 保存“理解与可追溯索引”，不复制整个现实。**
3. **World Graph 与 Derivation DAG 永久分离。**
4. **稳定身份 ID 与版本内容 Hash 永久分离。**
5. **Truth 与 Epistemic State 永久分离；`STALE` 不是一种真值。**
6. **Prediction、Hypothesis、Projection 永远不能自行升级为现实 Evidence。**
7. **所有进入 LLM 的世界上下文默认 `context_only`，不能因此获得执行权或事实权威。**

最终信息方向：

```text
Reality
  ↓
L0 WorldFrame
  ↓
L1 WorldObservation
  ↓
L2 WorldEntity
  ↓
L3 WorldRelation
  ↓
L4 WorldHypothesis
  ↓
L5 CognitionStatement
  ↓
L6 WorldState
  ├────────→ L7 WorldPrediction
  │                 │
  └────────────┬────┘
               ↓
        L8 WorldContextPacket
               ↓
              LLM
```

认知缺口反馈：

```text
LLM / World Understanding
        ↓
    WorldInquiry
        ↓
 Self-Will / Autonomy
        ↓
      Runtime
        ↓
      Reality
        ↓
 WorldObservation
```

---

# 1. 第一性原理

世界理解系统面对三个互相冲突的目标：

- 现实信息量可以极大；
- LLM 上下文容量和推理成本有限；
- 高层认知必须长期稳定、可验证、可修订。

因此系统不能把“看到的一切”长期保存为认知，也不能把“认为重要的一切”直接塞给 LLM。

正确的数据形态必须满足：

```text
信息数量：L1 > L2/L3 > L4 > L5 > L8
语义密度：L1 < L2/L3 < L4 < L5 < L8
```

World Data Model 的目的不是最大化保存量，而是最大化：

> **可恢复的世界理解 / 单位上下文成本。**

---

# 2. 数据分类：World Data 与 Telemetry 永久分离

任何九层模块产生的数据先分为两大类。

## 2.1 World Data

用于描述世界本身、天工对世界的结构化理解以及这些理解的来源。

包括：

- Frame；
- Observation；
- Entity；
- Relation；
- Hypothesis；
- Cognition；
- State；
- Prediction；
- Context Packet；
- Source/Derivation/Inquiry 引用。

## 2.2 System Telemetry

用于维护世界理解系统本身，不属于“世界是什么”。

例如：

- cache hit rate；
- dedup ratio；
- transform latency；
- token usage；
- queue depth；
- retry count；
- database size；
- admission throughput。

**Telemetry 默认不得进入 WorldContextPacket。**

仅当用户明确询问系统健康、性能或世界理解自身状态时，L8 才可把相关 Telemetry 作为“系统诊断信息”单独投影。

---

# 3. Canonical Encoding 与通用数据约束

所有正式 World Contract 必须遵循：

- UTF-8；
- 文本 NFC 规范化；
- 禁止 NUL；
- Set-like 字段必须 canonical sort + unique；
- 所有 immutable payload 使用 canonical serialization 后计算 SHA-256；
- Hash 不是数据库主键替代品；
- Stable ID 与 revision payload hash 必须分离；
- 时间使用 Unix epoch milliseconds，整数范围不得超过 JavaScript safe integer；
- 大型原始内容不得直接嵌入 World Record；
- 超过领域约定阈值的内容必须通过 `WorldSourceRef` / domain index ref 引用。

推荐 schema version：

```text
tiangong.world-data.contracts.v1
```

---

# 4. 公共嵌入值对象

这些对象本身不代表九层中的独立“世界事实”，而是所有层共用的嵌入结构。

## 4.1 `WorldRecordRef`

任何跨记录引用必须绑定到一个确定版本，而不是只写字符串 ID。

```text
WorldRecordRef
- record_type
- record_id
- revision            optional for immutable one-shot records
- sha256
```

硬约束：

- 如果目标对象有 revision，则 ref 必须同时携带 revision + sha256；
- 如果目标对象天然 immutable，则 revision 可省略，但 sha256 必须存在；
- ref 解析时 ID、revision、sha256 三者必须一致；
- 禁止“ID 对得上就接受不同内容”。

目的：防止 head 已变化时旧推理偷偷绑定到新内容。

---

## 4.2 `WorldValue`

所有 Relation / Hypothesis / Packet Atom 的值使用有限类型，不允许无界任意 JSON。

V0.1 允许：

```text
entity_ref
record_ref
string
integer
boolean
number_milli
string_set
record_ref_set
small_object
```

其中：

- `small_object` 只能保存结构化小对象；
- 大型 AST、全文、HTML、图片、日志、向量、二进制不得进入 `WorldValue`；
- 此类数据通过 SourceRef / DomainIndexRef 访问。

---

## 4.3 `WorldClaim`

L3-L7 共用的最小声明结构：

```text
WorldClaim
- subject_ref
- predicate
- value
- condition_ref          optional
- condition_sha256       optional
```

`WorldClaim` 不包含 confidence、evidence 或 truth。

原因：

> “说了什么”与“为什么相信、相信多少”必须分离。

---

# 5. World Scope Contract

## 5.1 `WorldScope`

WorldScope 定义一条记录属于哪一个逻辑世界。

```text
WorldScope
- life_id
- world_id
- domain_id
- scope_bindings[]
- world_scope_hash
- principal_scope_hash
- privacy_scope
```

### `world_id`

稳定的世界命名空间，不等于项目、仓库或 branch。

默认可按 Life 建立一个世界命名空间；未来允许同一 Life 拥有多个隔离世界。

推荐 ID：

```text
wld_<sha256>
```

### `domain_id`

V0.1 不把 Domain 写死成 Python Literal，而采用注册表 ID。

核心推荐 Domain：

```text
software
self
user
environment
device
organization
external
```

Domain Adapter 必须注册后才能写入正式 World Data。

### `scope_bindings`

有序层级绑定，例如：

```text
domain=software
project=<entity-ref>
repository=<entity-ref>
branch=main
worktree=<opaque-id>
```

顺序表达层级，不按字母排序。

但同一 scope 中同一 binding key 不得重复。

### `world_scope_hash`

只绑定：

```text
life_id
world_id
domain_id
scope_bindings
```

不混入 principal/privacy。

### `principal_scope_hash`

独立表示用户/主体隔离边界，以兼容现有 Cognition Core。

### `privacy_scope`

冻结采用：

```text
public
relationship
private
secret
system
```

与当前 `CognitionPrivacyScope` 保持一致。

---

# 6. World Time Contract

## 6.1 `WorldTime`

世界记录至少区分三种时间：

```text
valid_from_ms
valid_until_ms      optional
observed_at_ms      optional
recorded_at_ms
```

语义：

- `valid_*`：现实中认为该状态成立的时间范围；
- `observed_at`：天工什么时候观察到；
- `recorded_at`：什么时候正式写入世界系统。

硬约束：

```text
valid_until >= valid_from
observed_at <= recorded_at
```

`valid_from` 可以早于 `observed_at`，例如今天读取一份描述昨日状态的权威记录。

`STALE` 不通过修改 valid time 表达；它属于 epistemic state。

Prediction 使用独立 forecast horizon，不把未来预测伪装成 `WorldTime.valid_*`。

---

# 7. Truth 与 Epistemic State

## 7.1 Truth State

只允许：

```text
TRUE
FALSE
UNKNOWN
CONFLICTED
```

含义：

- TRUE：当前有效证据支持；
- FALSE：当前有效证据支持其否定；
- UNKNOWN：证据不足，不能判断；
- CONFLICTED：存在不可忽略的支持/反对证据且尚未收敛。

采用开放世界语义：

> **Not observed ≠ FALSE。**

## 7.2 Epistemic State

通用认识状态：

```text
CURRENT
STALE
CHALLENGED
REVERIFYING
RETIRED
```

必须与 Truth 独立。

例：

```text
truth_state = TRUE
epistemic_state = STALE
```

含义是：

> 过去最后一次确认时为真，但现在需要重新验证。

L5 CognitionStatement 继续使用自己的更细状态机，不被本通用枚举覆盖。

---

# 8. Source 与 Provenance

## 8.1 `WorldSourceRef`

所有现实来源使用统一引用：

```text
WorldSourceRef
- source_kind
- object_id
- object_revision        optional
- sha256
- locator                optional
- span_start             optional
- span_end               optional
- authority_ceiling_milli
- provenance_integrity_milli
```

`source_kind` 由 Domain Registry 注册，例如：

```text
file
git_object
runtime_fact
tool_result
user_instruction
memory
web_resource
document
device_state
system_authority
model_output
```

### 权威单调性

任何派生信息的现实权威不得超过最弱父来源允许的 ceiling：

```text
A_child <= min(A_parent_i)
```

模型总结、重复复制、Memory 转述不得提高 authority ceiling。

### SourceRef 与 Evidence 的边界

`WorldSourceRef` 只回答：

> “材料从哪里来？”

L5 `CognitionEvidence` 回答：

> “这份材料以什么证据语义支持/反驳某条 Cognition？”

两者不得合并。

---

# 9. Derivation Contract

## 9.1 `DerivationRef`

用于构建 **Derivation DAG**。

```text
DerivationRef
- derivation_id
- source_refs[]
- target_refs[]
- transform_type
- transform_version
- model_assisted
- lineage_root_hashes[]
- authority_ceiling_milli
- created_at_ms
- derivation_sha256
```

推荐 ID：

```text
wdrv_<sha256>
```

硬约束：

- source_refs / target_refs 都必须精确绑定 hash；
- `target` 不得引用自身作为祖先；
- lineage 必须完整向上传递；
- model-assisted transformation 不能把模型输出重新标记成 direct observation；
- Derivation DAG 不允许成为 World Graph relation；
- Derivation 本身不构成现实 Evidence。

完整 Heavy Transform Record 的字段将在《World Derivation Protocol V0.1》冻结。

---

# 10. 九个核心对象总览

| Layer | Core Object | 角色 |
|---|---|---|
| L0 | `WorldFrame` | 当前世界边界与情境 |
| L1 | `WorldObservation` | 对现实的可追溯观察 |
| L2 | `WorldEntity` | 世界中的稳定身份对象 |
| L3 | `WorldRelation` | 对象之间的结构关系 |
| L4 | `WorldHypothesis` | 对结构意义/因果的候选理解 |
| L5 | `CognitionStatement` | 经过证据门的长期认知 |
| L6 | `WorldState` | 当前世界的一致物化视图 |
| L7 | `WorldPrediction` | 对局部未来状态转移的预测 |
| L8 | `WorldContextPacket` | 提供给 LLM 的任务相关世界投影 |

---

# 11. L0 — `WorldFrame`

## 11.1 定义

WorldFrame 是一个 immutable 世界情境快照。

它回答：

> “当前这次世界理解发生在哪个世界、哪个 scope、哪个环境状态中？”

## 11.2 字段

```text
WorldFrame
- schema_version
- frame_id
- scope: WorldScope
- environment_refs[]
- parent_frame_ref         optional
- remap_reason             optional
- time: WorldTime
- frame_sha256
```

推荐 ID：

```text
wfr_<sha256>
```

Frame ID 绑定 immutable payload。

## 11.3 规则

- Frame 不允许原地修改；
- branch/worktree/environment 等上下文发生结构性变化时建立新 Frame；
- 新 Frame 可引用 parent_frame；
- remapping 不覆盖历史 Frame；
- Frame 自身不是 Cognition；
- Frame 不能因为“当前选中了某项目”而证明项目内任何事实。

## 11.4 LLM 暴露

L8 默认必须投影当前 Frame 的最小必要信息。

---

# 12. L1 — `WorldObservation`

## 12.1 定义

WorldObservation 是“系统实际观察到什么”的 immutable 记录。

**Observation ≠ Interpretation。**

## 12.2 字段

```text
WorldObservation
- schema_version
- observation_id
- scope: WorldScope
- frame_ref
- observation_kind
- source_ref: WorldSourceRef
- normalized_subject_hint   optional
- normalized_predicate_hint optional
- normalized_value          optional WorldValue
- content_object_id
- content_sha256
- search_scope_hash         optional
- coverage_milli
- volatility_class
- time: WorldTime
- lineage_root_hashes[]
- observation_sha256
```

推荐 ID：

```text
wob_<sha256>
```

## 12.3 `observation_kind`

V0.1 至少支持注册类型：

```text
presence
absence
measurement
event
change
aggregate
tool_error
state_snapshot
```

## 12.4 负向观察规则

`absence` / `aggregate` 必须具备：

```text
search_scope_hash != null
coverage_milli > 0
```

禁止：

> 没找到 → 不存在。

## 12.5 Raw Data 边界

Observation 默认只保存：

- source ref；
- normalized 小型结构；
- hash；
-必要 span/locator。

禁止把整个：

- source file；
- HTML；
- PDF；
- 图片；
- 视频；
- Runtime 全日志；
- AST 全树

复制进 World Store。

## 12.6 LLM 暴露

默认不直接投影大量 Observation。

只有：

- recent delta；
- critical direct evidence；
- conflict；
- 用户要求展开证据

时由 L8 暴露摘要或 expand handle。

---

# 13. L2 — `WorldEntity`

## 13.1 定义

WorldEntity 是世界中具有持续身份的对象。

核心原则：

```text
identity ≠ name
identity ≠ path
identity ≠ location
identity ≠ current attributes
```

## 13.2 ID 模型

Entity 采用：

```text
stable entity_id
+
immutable revision payload
```

推荐：

```text
went_<sha256(life_id, domain_id, genesis_identity_anchor)>
```

### Identity Anchor

允许两类：

```text
authoritative_key
allocated_anchor
```

- authoritative_key：Git object identity、系统对象 ID、稳定数据库主键等；
- allocated_anchor：现实没有可靠稳定 key 时，由 Entity Resolver 分配 opaque anchor。

Identity Anchor 一旦建立不得因名称/路径变化而改变。

## 13.3 字段

```text
WorldEntity
- schema_version
- entity_id
- scope: WorldScope
- entity_type
- identity_anchor_hash
- canonical_name
- aliases[]
- attributes
- location_refs[]
- source_observation_refs[]
- truth_state
- epistemic_state
- lifecycle
- replacement_refs[]
- revision
- supersedes_entity_sha256
- time: WorldTime
- entity_sha256
```

## 13.4 Lifecycle

```text
ACTIVE
MERGED
SPLIT
RETIRED
```

`STALE/CHALLENGED` 属于 epistemic state，不塞进 lifecycle。

## 13.5 Merge / Split

### Merge

如果两个 Entity 后来确认是同一对象：

- 原 Entity 历史不删除；
- 标为 MERGED；
- replacement_refs 指向保留的 canonical Entity；
- Derivation DAG 保留旧引用解析链。

### Split

如果一个 Entity 后来证明实际包含多个对象：

- 原 Entity 标为 SPLIT；
- replacement_refs 指向新 Entity 集合；
- 禁止静默改 ID。

## 13.6 LLM 暴露

L8 只投影任务相关实体及必要属性，不投影完整实体表。

---

# 14. L3 — `WorldRelation`

## 14.1 定义

WorldRelation 表达 World Graph 中两个对象或对象与值之间的关系。

## 14.2 Stable Slot Identity

Relation ID 不包含：

- truth state；
- evidence；
- freshness；
- revision；
-当前解释置信。

它只绑定稳定关系槽：

```text
scope
subject
predicate
object/value
condition
```

推荐：

```text
wrel_<sha256>
```

## 14.3 字段

```text
WorldRelation
- schema_version
- relation_id
- scope: WorldScope
- subject_ref
- predicate
- value: WorldValue
- condition_ref          optional
- condition_sha256       optional
- extraction_mode
- source_observation_refs[]
- derivation_refs[]
- truth_state
- epistemic_state
- revision
- supersedes_relation_sha256
- time: WorldTime
- relation_sha256
```

`extraction_mode` 至少区分：

```text
deterministic
observed
inferred
model_assisted
migration
```

## 14.4 硬规则

- LLM 推出的 Relation 必须保留 `model_assisted` 来源；
- model-assisted relation 不可冒充 deterministic；
- Relation 的 FALSE / UNKNOWN / CONFLICTED 不等于删除该 relation slot；
- relation 退役保留 revision lineage。

## 14.5 LLM 暴露

L8 优先投影：

- 关键路径；
- 当前任务依赖关系；
- architecture boundary；
- high-centrality task-relevant relation；
- 当前变化关系。

不得把整张图无差别塞入上下文。

---

# 15. L4 — `WorldHypothesis`

## 15.1 定义

WorldHypothesis 是系统或 LLM 对世界结构含义提出的**候选理解**。

它回答：

> “这些 Observation / Entity / Relation 可能意味着什么？”

它不是事实。

## 15.2 字段

```text
WorldHypothesis
- schema_version
- hypothesis_id
- scope: WorldScope
- claim: WorldClaim
- hypothesis_kind
- proposal_origin
- basis_refs[]
- counter_refs[]
- derivation_refs[]
- interpretive_prior_refs[]
- uncertainty_milli
- proposal_model_ref      optional
- proposal_model_sha256   optional
- created_at_ms
- valid_until_ms          optional
- projection_authority = hypothesis_only
- evidence_authority = none
- hypothesis_sha256
```

推荐 ID：

```text
whyp_<sha256(immutable proposal payload)>
```

## 15.3 `proposal_origin`

```text
deterministic_pattern
rule_inference
graph_inference
llm_synthesis
memory_consolidation
migration
```

## 15.4 硬规则

- `uncertainty_milli` 只是候选解释的不确定度，不是现实概率；
- LLM 自己觉得“很确定”不能提高 evidence authority；
- Hypothesis 不允许成为自身后续验证的独立证据；
- Hypothesis 可被 L5 转换为 Cognition Candidate，但必须经过 CognitionEvidence；
- Prior 只能帮助解释，empirical weight 为 0。

## 15.5 LLM 暴露

仅在：

- 对当前任务重要；
- 尚未进入稳定 Cognition；
- 或存在竞争 Hypothesis

时投影，并必须明确标注 `hypothesis`。

---

# 16. L5 — `CognitionStatement`

## 16.1 权威来源

World Data Model V0.1 **不重新定义** CognitionStatement。

L5 继续以现有：

```text
src/contracts/cognition_prior.py
src/contracts/cognition_evidence.py
src/contracts/cognition_statement.py
src/contracts/cognition_revision.py
```

为正式认知真实性内核。

现有语义继续冻结：

- stable cognition slot ID 不包含 value；
- C0-C4；
- Candidate/Provisional/Stable/Core/Challenged/Reverifying/Retired；
- independent evidence groups；
- provenance lineage；
- prior empirical weight = 0；
- LLM 不拥有 revision authority；
- projection authority = context_only。

## 16.2 与 World Data 的映射

### Scope

WorldScope 必须产生与 L5 完全一致的：

```text
life_id
world_scope_hash
principal_scope_hash
privacy_scope
```

### Subject

`CognitionStatement.subject_ref` 优先引用 `WorldEntity.entity_id`。

### Predicate / Value

L4 `WorldClaim` 在 promotion 时转换为 L5 claim slot。

### Evidence

L1/L3 的直接现实材料必须先转换为 `CognitionEvidence`，不能把 Hypothesis 当 direct evidence。

### Domain Compatibility

当前 Cognition V1 的 Domain Literal 比 World Domain Registry 更窄。

V0.1 冻结规则：

> **禁止静默降级或错误映射 Domain。**

某 World Domain 暂不被 Cognition V1 支持时：

- 可以完成 L0-L4；
- 可以进入 Working/Semantic World；
- 不得假装成另一个 Domain 晋升 L5；
- 等 Cognition Contract 正式扩展后再晋升。

---

# 17. L6 — `WorldState`

## 17.1 定义

WorldState 不是“世界所有数据的第二份复制”。

它是一个：

> **consistent materialized world-state head。**

## 17.2 字段

```text
WorldState
- schema_version
- world_state_id
- scope: WorldScope
- frame_ref
- world_sequence
- observation_cutoff_ref
- entity_head_manifest_ref
- relation_head_manifest_ref
- cognition_head_manifest_ref
- active_hypothesis_manifest_ref
- delta_manifest_ref
- unresolved_conflict_refs[]
- stale_refs[]
- materialized_at_ms
- source_transaction_id
- state_sha256
```

推荐 ID：

```text
wst_<sha256>
```

## 17.3 Manifest 设计

为避免 Snapshot 自身无限膨胀，WorldState 不直接嵌入数十万个 Entity/Relation ID。

采用内容寻址 manifest：

```text
entity_head_manifest_ref
relation_head_manifest_ref
...
```

Manifest 本身可分页/分片，但必须有 root hash。

## 17.4 一致性

一个 WorldState 必须对应一个原子 transaction cut。

禁止：

```text
Entity heads = transaction N
Relation heads = transaction N+1
Cognition heads = transaction N-2
```

然后把它称为同一个 State。

## 17.5 LLM 暴露

L8 的主要查询来源是 WorldState，但只从 manifest/index 取当前任务所需部分。

---

# 18. L7 — `WorldPrediction`

## 18.1 定义

WorldPrediction 表达：

> 基于某个已知 WorldState，在明确条件与 horizon 下，对局部世界状态转移的预测。

V0.1 禁止“大而无边界的未来幻想”。

## 18.2 Stable Prediction Identity

预测 ID 必须绑定：

- basis state；
- subject / target；
- condition；
- expected transition；
- horizon。

不同 basis state 产生不同预测身份。

推荐：

```text
wprd_<sha256>
```

## 18.3 字段

```text
WorldPrediction
- schema_version
- prediction_id
- scope: WorldScope
- basis_world_state_ref
- condition_claim         optional
- predicted_claim
- prediction_kind
- horizon_start_ms
- horizon_end_ms
- prediction_score_milli
- model_ref               optional
- model_sha256            optional
- basis_refs[]
- status
- outcome_observation_refs[]
- resolution_score_milli  optional
- revision
- supersedes_prediction_sha256
- created_at_ms
- projection_authority = context_only
- evidence_authority = none
- prediction_sha256
```

## 18.4 Status

```text
PENDING
RESOLVED
EXPIRED
CANCELLED
```

## 18.5 硬规则

- `prediction_score_milli` 不得解释为 Cognition confidence；
- Prediction 不得进入 Evidence 支持组；
- Self-Will 因 Prediction 发起行动也不能让 Prediction 自我变真；
- 只有后续 Reality → Observation 才能解析 outcome；
- Prediction resolution 必须引用真实 Observation；
- Prediction 可帮助 L1/L4 调整 attention，但不得提升 truth authority。

## 18.6 LLM 暴露

默认不投影。

只有涉及：

- change impact；
- risk；
- next-state planning；
- 未来条件判断

时才由 L8 放入 Packet。

---

# 19. L8 — `WorldContextPacket`

## 19.1 定义

WorldContextPacket 是 World Understanding 对 LLM 的正式输出产品。

它不是数据库 Dump，也不是新的 System Prompt。

它是：

> **当前任务下，对当前世界最小充分、可追溯、带不确定性标注的结构化投影。**

## 19.2 字段骨架

```text
WorldContextPacket
- schema_version
- packet_id
- scope: WorldScope
- frame_ref
- basis_world_state_ref
- task_ref
- task_sha256
- generated_at_ms
- token_budget
- mandatory_atoms[]
- ranked_atoms[]
- uncertainty_atoms[]
- prediction_atoms[]
- evidence_digest[]
- expansion_handles[]
- overflow_state
- projection_policy_ref
- projection_policy_sha256
- projection_authority = context_only
- packet_sha256
```

推荐 ID：

```text
wcp_<sha256>
```

## 19.3 `ContextAtom`

Packet 中每条信息使用：

```text
ContextAtom
- atom_id
- atom_kind
- summary
- referenced_world_records[]
- truth_state              optional
- epistemic_label          optional
- cognition_stability      optional
- task_relevance_milli
- impact_milli
- freshness_need_milli
- mandatory
- expansion_handle         optional
```

注意：

`task_relevance_milli` 只控制投影，不是 truth confidence。

## 19.4 Mandatory 区

以下信息一旦存在，不得单纯因为排名低而被普通 ranked atom 挤掉：

- current WorldFrame；
- hard world constraints；
- active contradiction；
- critical uncertainty；
- current high-impact delta；
- 与任务直接相关的 protected/core architecture invariant；
- 当前执行边界相关认知。

如果 Mandatory 信息本身超过 token budget：

```text
overflow_state = MANDATORY_OVERFLOW
```

系统必须显式报告压缩/展开需求，禁止静默删除关键约束。

## 19.5 Progressive Disclosure

Packet 至少支持三级：

```text
L0 Summary
L1 Supporting Context
L2 Raw/Direct Evidence Reference
```

默认 Packet 主要包含 L0；

必要时带 L1；

L2 通过 `expansion_handle` 按需读取。

## 19.6 Expansion Handle

```text
ExpansionHandle
- handle_id
- target_refs[]
- allowed_depth
- scope_hash
- principal_scope_hash
- privacy_scope
- expires_at_ms
- handle_sha256
```

硬约束：

- expansion 不得跨 scope；
- 不得提升 privacy authority；
- 返回内容仍为 context_only；
- expand 操作不得自动写 Cognition；
- expand 次数不得作为 evidence strength。

## 19.7 Packet 生命周期

WorldContextPacket 默认是 ephemeral。

长期存储只需要：

- packet hash；
- basis state；
- projection policy；
-必要审计摘要。

除非调试/审计模式，不建议永久保存完整 Packet 文本。

---

# 20. World Inquiry Contract

WorldInquiry 是世界理解与 Self-Will 之间唯一正式“求知桥”。

## 20.1 字段

```text
WorldInquiry
- schema_version
- inquiry_id
- scope: WorldScope
- subject_refs[]
- question
- inquiry_kind
- reason_codes[]
- evidence_gap
- expected_information_gain_milli
- impact_if_unresolved_milli
- urgency_milli
- suggested_observation_classes[]
- source_world_state_ref
- source_cognition_refs[]
- status
- self_will_decision_ref     optional
- resulting_observation_refs[]
- revision
- supersedes_inquiry_sha256
- created_at_ms
- inquiry_sha256
```

推荐：

```text
winq_<sha256 stable inquiry slot>
```

## 20.2 Status

```text
PENDING
DEFERRED
ACCEPTED
DECLINED
SATISFIED
EXPIRED
CANCELLED
```

## 20.3 权力边界

World Inquiry 只能表达：

> “这里还有什么值得知道。”

它不能表达：

> “现在必须执行某个工具。”

最终行动仍然必须：

```text
WorldInquiry
→ Self-Will
→ existing Runtime/Gateway/Grant/Omni Body
```

## 20.4 防自证

永久禁止：

```text
Hypothesis
→ Inquiry
→ Self-Will accepts
→ 因为 accepted
→ Hypothesis becomes stronger
```

Self-Will decision 本身不是现实 evidence。

只有自主行动以后产生的真实 Observation / Fact / ToolResult 才能进入 Evidence 链。

---

# 21. World Graph 与 Derivation DAG

必须永久分成两种数据结构。

## 21.1 World Graph

回答：

> “世界里的东西如何连接？”

节点主要是 Entity；边主要是 Relation。

例如：

```text
Gateway --CALLS--> Zongdiaodu
Zongdiaodu --USES--> ActionRegistry
ExecutionPath --GUARDED_BY--> Grant
```

## 21.2 Derivation DAG

回答：

> “为什么系统形成了这条理解？”

例如：

```text
source.py
   ↓
Observation
   ↓
Entity
   ↓
Relation
   ↓
Hypothesis
   ↓
Cognition
```

## 21.3 永久禁止混图

以下不是 WorldRelation：

```text
Cognition-X DERIVED_FROM Observation-Y
```

这是 Derivation edge。

以下也不是 Derivation：

```text
Function-A CALLS Function-B
```

这是 World Graph edge。

混图会导致：

- 世界关系与知识来源语义污染；
- 图查询无法区分 reality structure 与 cognition lineage；
- invalidation 错误扩散；
- LLM 可能把“来源关系”理解成现实关系。

---

# 22. 数据温层与存储边界

World Data 按生命周期分成五个温层。

## T0 — Raw Reality / Domain Index

例如：

- 完整源代码；
- AST；
- Git objects；
- HTML；
- PDF；
- 图片/视频；
- 桌面截图；
-全量日志。

默认**不进入 World Store**。

由各 Domain Adapter / Perception Index 管理。

## T1 — Working World

主要：

- Observation；
- Event cluster；
- Entity candidate；
- Delta。

高频、可淘汰/归档。

## T2 — Semantic World

主要：

- Entity revisions；
- Relation revisions；
- Hypothesis；
- Schema/role 等结构理解。

中等频率。

## T3 — Persistent Cognition

主要：

- CognitionStatement；
- CognitionEvidence；
- CognitionRevision；
- 长期结构性认知。

低频、高稳定。

## T4 — Projection

主要：

- WorldContextPacket；
- expansion response。

短生命周期、按任务生成。

冻结原则：

> **World Store 存理解，Domain Index 存可重新观察的细节。**

---

# 23. Stable ID、Revision 与 Hash

V0.1 统一采用三层身份模型。

## 23.1 Stable Slot ID

用于“这是同一个逻辑对象”。

例如：

```text
entity_id
relation_id
cognition_id
prediction_id
inquiry_id
```

## 23.2 Revision

用于“同一个对象的第几个正式版本”。

从 1 开始单调递增。

禁止跳号提交。

## 23.3 Content Hash

用于“这个版本的具体 payload 是什么”。

例如：

```text
entity_sha256
relation_sha256
statement_sha256
```

Revision chain：

```text
rev 1 hash A
   ↓ superseded by
rev 2 hash B
   ↓
rev 3 hash C
```

## 23.4 CAS

所有 mutable-head 类对象更新必须支持 expected-head CAS。

禁止 last-write-wins。

---

# 24. Invalidation 数据要求

完整 invalidation 算法由下一份 Derivation Protocol 定义。

本模型先冻结它所需的数据条件。

每个可派生对象必须能回答：

- 我直接来源于哪些 records/source refs？
- 我的 lineage roots 是什么？
- 我属于哪个 Frame/Scope？
- 我的当前 revision/hash 是什么？
- 我的下游 derivation targets 是什么？

因此当：

```text
source hash changes
```

可以沿：

```text
Source
→ Observation
→ Entity/Relation
→ Hypothesis
→ Cognition
→ State
```

精确找到受影响区域。

禁止默认采用：

> 一个文件改变 → 整个世界模型全部失效。

---

# 25. Schema / Ontology 边界

World Data Model 只定义通用元模型。

它不定义：

```text
PythonFunction
GitBranch
WindowsProcess
PersonPreference
CompanyDepartment
```

这些属于 Domain Ontology。

统一原则：

```text
Core Meta Model
      ↓
Domain Ontology
      ↓
WorldEntity / WorldRelation 实例
```

Emergent Concept 可以由 L4 提议，但：

- 未进入 Domain Registry 前不得成为正式 entity_type/predicate；
- LLM 不得无限创造近义类型；
- ontology promotion 属于独立治理流程。

---

# 26. 最小数据库映射建议

V0.1 不绑定 SQLite/Neo4j/PostgreSQL 产品。

第一版推荐 SQLite 即可。

逻辑表至少：

```text
world_frames
world_observations
world_entities
world_entity_heads
world_relations
world_relation_heads
world_hypotheses

# Existing L5
cognition_priors
cognition_evidence
cognition_statements
cognition_revisions
cognition_heads

world_states
world_predictions
world_prediction_heads
world_derivations
world_inquiries
world_inquiry_heads

world_manifests
world_source_catalog
world_domain_registry
```

可选审计表：

```text
world_packet_audit
world_admission_audit
world_transaction_audit
```

不要求为每个 Layer 建独立数据库。

---

# 27. 逻辑存储原则

## 27.1 Immutable Ledger + Mutable Head

优先模式：

```text
immutable revision rows
+
mutable current-head pointer
```

已有 Cognition Core 已采用此方向，World Entity / Relation / Prediction / Inquiry 应保持一致。

## 27.2 Lazy Creation

World Understanding OFF 时：

- 不创建目录；
- 不创建 DB；
- 不启动后台任务；
- 不消费 Token。

只读空查询也不应该为了“查询不存在的数据”创建持久目录。

## 27.3 Scope Isolation

所有查询首先 scope filter，再进行 relevance 排序。

禁止先全局检索再依赖 LLM 自己过滤隐私边界。

---

# 28. 九层的数据统计字段：不进入核心 Record

九层需要统计，但这些 Metrics 属于 Telemetry，不应污染 World Record schema。

推荐：

## L0

```text
frame_count
scope_switch_count
ambiguous_scope_count
remap_count
```

## L1

```text
observation_count
dedup_ratio
novelty_rate
change_rate
coverage
source_error_rate
```

## L2

```text
entity_count
unresolved_identity_count
merge_count
split_count
entity_churn
```

## L3

```text
relation_count
orphan_relation_count
relation_churn
centrality_change_count
```

## L4

```text
hypothesis_count
promotion_candidate_rate
rejection_rate
conflict_rate
model_cost
```

## L5

```text
C0/C1/C2/C3/C4 distribution
challenge_rate
stale_rate
revalidation_rate
```

## L6

```text
coverage
freshness
consistency
materialization_latency
```

## L7

```text
prediction_count
resolution_rate
prediction_error
calibration
false_positive/false_negative by prediction class
```

## L8

```text
packet_token_cost
mandatory_overflow_rate
expansion_rate
irrelevant_atom_rate
missing_context_rate
```

顶层重点关注：

```text
Coverage
Freshness
Consistency
Provenance Integrity
Compression Ratio
Cognitive Density
Revalidation Load
Expansion Rate
```

---

# 29. LLM 数据边界

LLM 默认应该看到“世界”，而不是“系统内部怎么处理世界”。

WorldContextPacket 优先表达：

```text
WHERE AM I
WHAT EXISTS
HOW THEY CONNECT
WHAT IS TRUE NOW
WHAT DO I ALREADY KNOW
WHAT CHANGED
WHAT IS UNCERTAIN
WHAT MATTERS FOR THIS TASK
```

默认不表达：

```text
DB row count
cache hit
queue size
scheduler retry
internal SQL key
internal transaction implementation
```

除非用户任务本身就是诊断世界理解系统。

---

# 30. 权威与投影规则

各核心对象的默认权威：

| Object | Reality Evidence Authority | LLM Projection Authority |
|---|---:|---|
| WorldFrame | none | context_only |
| WorldObservation | source-dependent | context_only |
| WorldEntity | derived | context_only |
| WorldRelation | derived | context_only |
| WorldHypothesis | none | hypothesis_only |
| CognitionStatement | evidence-backed | context_only |
| WorldState | materialized, not independent evidence | context_only |
| WorldPrediction | none | context_only |
| WorldContextPacket | none | context_only |
| WorldInquiry | none | inquiry_only |

最关键规则：

> **Projection never creates authority.**

某条信息被投影给 LLM 一千次，也不会因此更真实。

---

# 31. 自我强化闭环禁止项

以下闭环全部永久禁止：

## 31.1 Projection Loop

```text
Cognition
→ Packet
→ LLM sees it
→ because LLM repeats it
→ new independent Evidence
```

禁止。

## 31.2 Prediction Loop

```text
Prediction A
→ LLM plans around A
→ because plan used A
→ A becomes evidence
```

禁止。

## 31.3 Inquiry Loop

```text
Hypothesis A
→ Inquiry A
→ Self-Will accepts
→ acceptance becomes support for A
```

禁止。

## 31.4 Memory Laundering

```text
Observation A
→ Memory copy
→ Summary copy
→ LLM summary
→ 被当成四个独立证据
```

禁止。

Lineage roots 必须使其重新收敛到同一来源族。

---

# 32. 示例：Software World

假设当前现实：

```text
Gateway 调用 Zongdiaodu
Zongdiaodu 使用 ActionRegistry
执行进入 Grant
Grant 放行后进入 OmniBody
```

## L1

产生源码/运行时 Observation refs。

## L2

形成：

```text
Entity Gateway
Entity Zongdiaodu
Entity ActionRegistry
Entity Grant
Entity OmniBody
```

## L3

形成：

```text
Gateway CALLS Zongdiaodu
Zongdiaodu USES ActionRegistry
ExecutionPath GUARDED_BY Grant
Grant PRECEDES OmniBody
```

## L4

提出：

```text
Hypothesis:
Grant may be the execution authorization boundary
```

## L5

证据门确认后形成：

```text
CognitionStatement:
Grant ROLE execution_authorization_boundary
```

## L6

Current WorldState materializes this architecture state。

## L8

当任务是“修改认证但不要破坏执行链”，Packet 只需要投影：

```text
Frame: current repo/branch
Entities: Gateway, Zongdiaodu, Grant, OmniBody
Relations: critical execution path
Stable Cognition: Grant = authorization boundary
Current Delta: auth file changed
Hard Constraint: no bypass of Grant
Uncertainty: capability-X impact not yet verified
Expansion: refs to source/runtime evidence
```

LLM 不需要重新读取整个仓库才能恢复核心世界结构。

---

# 33. Contract 级硬禁止项

V0.1 直接冻结以下 forbidden patterns：

1. 禁止用文件路径作为 Entity 永久身份。
2. 禁止把 `STALE` 当 Truth State。
3. 禁止把 Query/Projection 当 Evidence。
4. 禁止把 Prediction 当 Observation。
5. 禁止把 Hypothesis 当 direct evidence。
6. 禁止 LLM 修改 evidence authority ceiling。
7. 禁止派生来源提升 lineage 权威。
8. 禁止 negative observation 无 coverage。
9. 禁止跨 principal scope 投影。
10. 禁止 Packet expansion 绕过 privacy scope。
11. 禁止 WorldState 混合不同 transaction cut。
12. 禁止 Entity merge/split 静默重写历史 ID。
13. 禁止 Relation truth change 生成无 lineage 的新对象来逃避 revision。
14. 禁止 World Store 保存无界 Raw Reality 副本。
15. 禁止 Domain Adapter 绕过统一 Scope/Time/Source 规范。
16. 禁止 World Understanding 通过 Inquiry 自己直接调用现实工具。
17. 禁止 World Data Model 建立第二 Runtime 或第二执行入口。
18. 禁止世界模型把自身结论反过来定义 FactKernel 事实。

---

# 34. 与现有 V3 的边界

World Data Model 是 cognition/read-model infrastructure。

允许：

```text
FactKernel / ToolResult / UserInstruction / Source Index
               ↓
          WorldObservation
```

禁止：

```text
World Cognition
      ↓
 overwrite FactKernel
```

允许：

```text
WorldInquiry
   ↓
Self-Will
```

禁止：

```text
WorldInquiry
   ↓
Tool directly
```

允许：

```text
WorldContextPacket
   ↓
LLM reasoning
```

禁止：

```text
WorldContextPacket
   ↓
Execution authority
```

---

# 35. V0.1 最小实现边界

未来实现 World Data Model V0.1 时，最小可运行版本只需要支持：

```text
WorldScope
WorldTime
WorldRecordRef
WorldSourceRef
DerivationRef

WorldFrame
WorldObservation
WorldEntity
WorldRelation
WorldHypothesis
existing CognitionStatement
WorldState
WorldPrediction
WorldContextPacket
WorldInquiry
```

不要求 V0.1 同时实现：

- 向量数据库；
- Neo4j；
- RDF/OWL；
- 全量多模态 embedding；
- 自动 ontology 演化；
- 大规模 predictive simulator；
- 多节点分布式 World DB。

先证明数据语义和转换链正确，再升级存储后端。

---

# 36. 数据模型验收条件

后续代码实现只有同时满足以下条件，才可宣称实现 World Data Model V0.1：

1. 同一个 Entity 改名/换路径不必改变 entity_id。
2. Entity merge/split 可追溯。
3. WorldFrame remap 不覆盖历史 Frame。
4. Observation 能追到真实 source hash。
5. Negative Observation 没有 scope/coverage 时被拒绝。
6. Relation Stable ID 不随 truth/evidence 变化。
7. LLM Hypothesis 明确标记 model-assisted 且 evidence authority 为 0。
8. Cognition promotion 必须进入现有 Evidence/Revision 规则。
9. Prediction 不可直接进入 Cognition Evidence。
10. WorldState 来自一个一致 transaction cut。
11. Packet mandatory constraints 不能被普通 ranking 淘汰。
12. Packet expansion 不能跨 Scope/Privacy。
13. 任一 Cognition 能沿 Derivation DAG 反向追到可验证来源。
14. Source change 能定位下游受影响 records，而不是只能全量失效。
15. World Store 不复制大型 raw source。
16. World Understanding OFF 时不创建持久资源。
17. WorldInquiry 只能进入 Self-Will，不可直接执行工具。
18. Query/Projection/Replay 不会给 Cognition 增加独立 Evidence。

---

# 37. 本 Contract 明确延后到下一份协议的问题

以下内容不是遗漏，而是有意延后到《World Derivation Protocol V0.1》：

- L0→L1→...→L8 每层准确输入输出；
- Light Derivation 与 Heavy Transform Record 的选择规则；
- invalidation propagation 算法；
- event batching；
- layer-skipping 快速通道；
- transform transaction；
- LLM-assisted transform audit；
- replay/consolidation 如何产生 derivation edges；
- Domain Adapter 的 transform registration。

以下内容延后到《WorldContextPacket V0.1》：

- mandatory atom 完整分类；
- ranked selection 函数；
- token budget 分配；
- ContextAtom 压缩策略；
- expansion protocol；
- missing-context / irrelevant-context 反馈学习；
- Packet rendering 到 system/developer/user context 的具体位置。

---

# 38. 最终冻结定义

World Data Model V0.1 的核心不是“设计一个巨大世界数据库”。

它冻结的是一条信息生命链：

```text
Reality
↓
可追溯 Observation
↓
稳定身份 Entity
↓
结构 Relation
↓
候选意义 Hypothesis
↓
证据约束 Cognition
↓
一致 WorldState
↓
局部 Prediction
↓
任务化 WorldContextPacket
↓
LLM
```

同时保证任意高层理解都可以反向追溯：

```text
WorldContextPacket
↑
WorldState
↑
Cognition / Hypothesis / Relation
↑
Entity
↑
Observation
↑
WorldSourceRef
↑
Reality authority
```

最终原则：

> **系统负责把世界变成可靠、连续、可追溯的数据结构；LLM 负责理解这些结构。**

> **世界模型是现实的内部模型，不是现实本身。**

> **任何理解都可以被修订，但任何修订都不能抹掉它从哪里来。**
