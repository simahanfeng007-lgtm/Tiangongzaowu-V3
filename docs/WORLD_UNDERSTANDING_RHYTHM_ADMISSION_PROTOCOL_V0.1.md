# 天工造物 V3：世界理解节律与准入协议 V0.1

> English: World Understanding Rhythm & Admission Protocol V0.1  
> 状态：设计完成 / 待讨论冻结 / 尚未实现 / 尚未接入 Runtime  
> 基线：`agent/world-cognition-core-v0.1` / `a79a5b46a54258798bce3529b8424cb3b3ab4d2c`  
> 目的：为世界理解九层架构定义统一的“什么时候看、什么时候深入理解、什么时候成为候选认知、什么时候允许固化、什么时候重新验证”的横向协议。

---

## 0. 一句话定义

**节律与准入系统不负责理解世界，也不负责判断世界事实；它只负责控制认知资源在正确的时间进入正确的层。**

必须永久区分：

> **值得注意 ≠ 值得相信。**

因此：

- 新奇性、异常、预测误差、任务相关度，只能提高“处理优先级”；
- Evidence / Provenance / Authority / Independent Roots 才能提高“认知固化资格”；
- LLM 可以解释和提出假设，但不能因为“感觉重要”而绕过证据门；
- 固定时间轮询不能作为主认知节律；事件、变化、预测误差和世界状态才是主驱动。

---

# 1. 第一性原理

世界理解是一个资源受限过程。

现实输入近似连续，而可用于深度推理、LLM 调用、图分析、认知固化和再验证的计算资源有限。

因此世界理解系统必须解决两个不同问题：

1. **Resource Allocation：什么值得继续花认知资源？**
2. **Epistemic Admission：什么有资格进入稳定世界认知？**

这两个问题绝不能用同一个分数解决。

如果把“新奇、重要、重复、相关”直接等价为“真实”，系统会产生确认偏误；如果所有输入都调用 LLM 并进入 consolidation，则世界模型会快速膨胀、成本失控、错误持久化。

因此本协议采用：

```text
高频感知
  ↓
低成本过滤 / 聚合
  ↓
事件驱动的理解升级
  ↓
稀疏候选形成
  ↓
严格证据固化
  ↓
自适应再验证
```

核心原则：

> **High-frequency sensing, sparse semantic escalation, rare durable consolidation, event-driven revalidation.**

---

# 2. 生物学与神经科学依据

本协议只吸收机制原则，不机械模拟生物神经结构。

## 2.1 Predictive Coding：优先处理“预测误差”而非重复世界

Rao & Ballard (1999) 的 predictive coding 模型提出，高层表征向下提供预测，向上传递的重要信号是预测与真实输入之间的残差。

工程吸收：

- 世界状态符合预期时，不应不断触发深层认知；
- 世界变化、异常、不匹配才提高认知处理优先级；
- 未实现完整 L7 Prediction 前，也可以通过 expected state / hash / schema expectation 计算局部 prediction error。

参考：
- Rao, R. P. N. & Ballard, D. H. *Predictive coding in the visual cortex*. Nature Neuroscience 2, 79–87 (1999). DOI: 10.1038/4580

## 2.2 Event Segmentation：连续经验需要事件边界

Zheng et al. (2022) 在人类内侧颞叶记录到对抽象认知边界响应的神经元；连续经历不是无差别储存，而会被切分为事件。

工程吸收：

- 感知可以连续；
- consolidation 不按固定秒数触发；
- Git commit、task phase 结束、scope shift、长 burst 结束、明确事务闭合等事件边界，是更合理的认知固化机会。

参考：
- Zheng, J. et al. *Neurons detect cognitive boundaries to structure episodic memories in humans*. Nature Neuroscience 25, 358–368 (2022). DOI: 10.1038/s41593-022-01020-w

## 2.3 Complementary Learning Systems：快记录、慢整合

McClelland, McNaughton & O'Reilly (1995) 提出的 Complementary Learning Systems 说明：快速记录具体经历与缓慢提取跨经历稳定结构应由不同学习节律承担。

工程吸收：

- Observation / Working World Model 可以快；
- Persistent Cognition 必须慢；
- 一次观察绝不应直接改写高稳定认知；
- 新信息先进入工作层 / candidate，再经过证据和 consolidation。

参考：
- McClelland, J. L., McNaughton, B. L. & O'Reilly, R. C. *Why there are complementary learning systems in the hippocampus and neocortex*. Psychological Review 102, 419–457 (1995). DOI: 10.1037/0033-295X.102.3.419

## 2.4 Schema：已有结构可以加快整合，但不能提高真实性

Tse et al. (2007) 发现，在已有 schema 建立后，与 schema 一致的新关联可以更快形成长期系统级整合。

工程吸收：

- schema-compatible 信息可以降低处理成本和等待时间；
- 可以走 Fast Consolidation Scheduling；
- **但 schema compatibility 的 empirical evidence weight 永远为 0**；
- “符合旧世界观”不能增加 Authority、Provenance 或 Independent Evidence Count。

参考：
- Tse, D. et al. *Schemas and memory consolidation*. Science 316, 76–82 (2007). DOI: 10.1126/science.1135935

## 2.5 Reconsolidation：检索不是更新理由，预测误差才是重要边界条件

Sevenster, Beckers & Kindt 的人类实验显示，仅 retrieval 本身并不必然触发 reconsolidation；prediction error 是已有记忆进入可更新状态的重要条件。

工程吸收：

- Cognition 被 Query 读取不会因此“越读越真”；
- 重复检索不刷新 confidence；
- 已有稳定认知只有在反证、来源变化、scope shift、prediction error、依赖变化或到达再验证风险边界时才进入 revalidation；
- retrieval path 必须只读。

参考：
- Sevenster, D., Beckers, T. & Kindt, M. *Prediction error governs pharmacologically induced amnesia for learned fear*. Science 339, 830–833 (2013). DOI: 10.1126/science.1231357
- Sevenster, D., Beckers, T. & Kindt, M. *Retrieval per se is not sufficient to trigger reconsolidation of human fear memory*. Neurobiology of Learning and Memory 97, 338–345 (2012). DOI: 10.1016/j.nlm.2012.01.009

## 2.6 Prioritized Replay：不是所有经历都值得同等 consolidation

Mattar & Daw (2018) 从规范模型角度提出 prioritized memory access；Huelin Gorriz et al. (2023) 进一步观察到 hippocampal replay 会受到经验显著性影响。

工程吸收：

- background replay / consolidation 必须有优先级；
- 优先处理未来更可能被使用、当前不确定性更高、可能改变世界模型更大、存在冲突或证据缺口的候选；
- 不能 FIFO 平均处理所有候选。

参考：
- Mattar, M. G. & Daw, N. D. *Prioritized memory access explains planning and hippocampal replay*. Nature Neuroscience 21, 1609–1617 (2018). DOI: 10.1038/s41593-018-0232-z
- Huelin Gorriz, M., Takigawa, M. & Bendor, D. *The role of experience in prioritizing hippocampal replay*. Nature Communications 14, 8157 (2023). DOI: 10.1038/s41467-023-43939-z

---

# 3. AI 长期记忆方向的工程启发

以下为 2026 年预印本，作为工程参考，不视为已形成学术共识。

## 3.1 A-MAC：显式 admission 比全 LLM 决策更可审计

A-MAC 将长期记忆 admission 拆为未来效用、事实置信、语义新颖度、时间新近性、内容类型先验等可解释因素。

吸收：准入必须显式、可记录、可版本化，不能只依赖一个 LLM “要不要记”的自然语言判断。

- arXiv:2603.04549 — *Adaptive Memory Admission Control for LLM Agents*

## 3.2 SAGE：明确重复应尽早 NOOP，模糊情况再调用昂贵模型

SAGE 把 write-side gate 建模为 novelty detection：明确新信息 ADD，明确重复 NOOP，只有不确定样本进入更昂贵 LLM merge。

吸收：G1/G2 应尽可能 deterministic / lightweight；LLM 是昂贵的上层解释器，而不是所有信号的默认入口。

- arXiv:2605.30711 — *SAGE: A Novelty Gate for Efficient Memory Evolution in Agentic LLMs*

## 3.3 RecMem：持续重复可以成为 consolidation 的触发，但重复本身不是真实性

RecMem 通过 recurrence 决定何时调用 LLM consolidation，而不是处理每条输入。

吸收：独立重复可以提高“值得总结”的优先级；但同 lineage 的复制不能制造新证据。

- arXiv:2605.16045 — *RecMem: Recurrence-based Memory Consolidation for Efficient and Effective Long-Running LLM Agents*

## 3.4 MemCon：什么时候操作长期状态本身是控制问题

MemCon 把 retrieval / consolidation / forgetting 作为 context-dependent 控制过程。

吸收：世界理解节律不能是一组静态 timer；必须受上下文、预算、任务阶段、世界变化和历史反馈影响。

- arXiv:2607.13591 — *Memory as a Controlled Process: Learned Adaptive Memory Management for LLM Agents*

## 3.5 TRUSTMEM：进入长期状态前必须验证 transition

TRUSTMEM 指出 consolidation 可能产生 omission、corruption 和 unsupported hallucination，并显式验证 memory transition。

吸收：即使 G3 已形成 Candidate，也不能直接写 Stable World Cognition；G4 必须由现有 Cognition Core 的 deterministic evidence gate 接管。

- arXiv:2606.25161 — *TRUSTMEM: Learning Trustworthy Memory Consolidation for LLM Agents with Long-Term Memory*

---

# 4. 协议在九层架构中的位置

```text
L0 World Frame
       │
       ▼
L1 Perception ──────────────┐
       │                    │
       ▼                    │
L2 Entity                   │
       │                    │
       ▼                    │
L3 Relation                 │
       │                    │
       ▼                    │
L4 Semantic/Hypothesis      │
       │                    │
       ▼                    │
L5 Cognition Core           │
       │                    │
       ▼                    │
L6 Persistent World Model   │
       │                    │
       ▼                    │
L7 Dynamics/Prediction ─────┤
       │                    │
       ▼                    │
L8 Query/Projection         │
                            │
        ┌───────────────────┘
        ▼
World Understanding Rhythm & Admission Controller

横向负责：节律、准入、预算、聚合、冷却、重验证调度
不负责：世界语义、证据真实性、工具执行、权限、安全裁决
```

它不是第 10 层，而是一个 **cross-layer control plane**。

---

# 5. 四条最高不变量

## R1. Attention is not Evidence

以下字段全部不得直接增加 Cognition Evidence Support：

- novelty
- task relevance
- event boundary
- graph centrality
- surprise
- prediction error
- recurrence frequency
- schema compatibility
- emotional/salience signal

它们只能改变：

- 是否升级处理；
- 进入哪条队列；
- 何时 revalidate；
- 给多少计算预算。

## R2. Retrieval is Read-Only

一次 Cognition 被读取 1000 次，不能比只读取 1 次更可信。

Query / Projection：

- 不产生新 evidence；
- 不刷新 statement verification time；
- 不增加 recurrence；
- 不改变 confidence。

## R3. Repetition Must Be Lineage-Aware

1000 条由同一个源复制出来的信息，最多是一个来源根的重复传播。

`independent_recurrence` 只能统计不同可信 lineage roots / independence components。

## R4. Critical Interrupt Bypasses Waiting, Not Truth

强反证、world scope 变化、Git branch 切换、直接现实变化等事件可以：

- 跳过普通等待；
- 立即触发 revalidation / challenge scheduling；

但不能：

- 绕过 Evidence Core；
- 自动把新 Hypothesis 写成 Stable；
- 自动 supersede C4。

---

# 6. 五种节律

## Rhythm A — Perception Rhythm（感知节律）

特点：高频、低成本、尽量事件驱动。

优先顺序：

1. 原生事件（Git/file/runtime/tool event）；
2. delta/hash/change detector；
3. fallback polling。

原则：

> 高频观察，不等于高频调用 LLM。

## Rhythm B — Escalation Rhythm（理解升级节律）

L1-L3 可以高频 deterministic 工作；只有以下情况才进入 L4 深语义理解：

- 新实体 / 新关系；
- 高 prediction error；
- 高 uncertainty；
- material conflict；
- 高 impact change；
- event boundary 后的候选聚合；
- 用户任务明确需要当前未知语义；
- 多个独立来源形成结构性 recurrence。

## Rhythm C — Consolidation Rhythm（认知固化节律）

低频、稀疏。

优先触发：

- 一个事件结束；
- 一个任务阶段结束；
- Git commit / checkpoint；
- 一组候选达到 evidence readiness；
- active workload 降低且 backlog 中存在高 replay priority 候选；
- 用户明确要求形成长期稳定理解，但仍不能跳过证据规则。

禁止：

- 每条消息 consolidation；
- 每个 Observation consolidation；
- 固定每 N 秒无差别 consolidation。

## Rhythm D — Interrupt Rhythm（异常中断节律）

立即提高处理优先级：

- `WorldFrame` hard shift；
- authoritative source 直接反证；
- execution-verified change；
- 当前世界核心依赖发生变化；
- 高影响对象突然消失 / 替换；
- 已有 Stable/Core cognition 的关键 source invalidated。

中断只触发“重新检查”，不直接决定新事实。

## Rhythm E — Revalidation Rhythm（长期再验证节律）

不存在全局固定周期。

每条认知 / entity / relation 根据：

- volatility；
- source change rate；
- impact；
- dependency invalidation；
- recent prediction error；
- last verification；
- future need；

动态计算下一次 revalidation 风险窗口。

---

# 7. 五级 Admission Gate

## G1 — Observation Admission

回答：

> “这条信号是否值得成为一个正式 Observation？”

主要处理：

- scope 是否有效；
- source identity 是否有效；
- payload/hash 是否变化；
- 是否明显 duplicate；
- 是否属于已 coalesced 的同一事件；
- 是否超过 observation buffer 限额。

输出：

```text
DROP_INVALID
NOOP_DUPLICATE
COALESCE
BUFFER_OBSERVATION
HARD_INTERRUPT
```

G1 不调用 LLM。

## G2 — Understanding Escalation Admission

回答：

> “这条 Observation / Delta 值不值得继续进入深层理解？”

使用 Attention / Escalation Score。

可能输出：

```text
KEEP_SHALLOW
ESCALATE_RULE_GRAPH
ESCALATE_LLM
DEFER
INTERRUPT_REVALIDATION
```

LLM 只在 `ESCALATE_LLM` 后进入。

## G3 — Candidate Cognition Admission

回答：

> “L4 形成的 Hypothesis 是否值得成为长期认知候选？”

硬条件：

- 有明确 `WorldFrame`；
- 有 canonical subject / predicate / value；
- 有 provenance link；
- 能声明 unknown / conflicted，而不是强迫二值化；
- LLM 输出不得携带 promotion authority；
- 已有完全等价 candidate 时 merge/noop，不复制。

输出：

```text
REJECT_MALFORMED
MERGE_CANDIDATE
WAIT_MORE_EVIDENCE
CREATE_CANDIDATE
```

## G4 — Stable Cognition Admission

回答：

> “Candidate 是否有资格进入 Persistent Cognition？”

**G4 不重新发明数学模型。直接交给当前 World Cognition Core。**

沿用：

- authority ceiling；
- provenance integrity；
- lineage roots；
- independent evidence groups；
- support / counterevidence；
- freshness；
- direct real-world evidence；
- C0/C1/C2/C3/C4 状态规则。

即：

> Rhythm Controller 决定“什么时候送去审理”，Cognition Core 决定“有没有资格成为稳定认知”。

## G5 — Revalidation Admission

回答：

> “已经存在的 Cognition 现在是否值得重新打开检查？”

触发源：

- hazard risk 到期；
- source object revision 改变；
- dependent entity / relation 改变；
- material counterevidence；
- prediction error；
- world remap；
- on-demand high-impact query；
- explicit correction；
- integrity/provenance invalidation。

G5 输出：

```text
NO_REVALIDATION
SCHEDULE_REVALIDATION
IMMEDIATE_REVALIDATION
CHALLENGE_PENDING
WORLD_REMAP_REQUIRED
```

---

# 8. Attention / Escalation 数学模型

不能使用单一线性加权，因为 novelty、prediction error、event boundary 高度相关，直接相加会重复计数。

V0.1 采用“特征族 → 族内去相关 → 族间 bounded aggregation”。

## 8.1 四个特征族

### Change / Surprise Family

```text
X_change = max(
  Novelty,
  PredictionError,
  EventBoundaryStrength
)
```

### Epistemic Family

```text
X_epistemic = max(
  Uncertainty,
  ConflictStrength,
  EvidenceGap
)
```

### Utility Family

```text
X_utility = max(
  TaskRelevance,
  GraphImpact,
  FutureNeed
)
```

### Recurrence Family

```text
X_recurrence = IndependentRecurrence
```

其中 `IndependentRecurrence` 必须 lineage-aware。

## 8.2 Attention Score

对有效 scope：

```text
A = 1 - Π_f (1 - w_f * X_f)
```

所有输入范围 `[0,1]`。

如果 scope 无效：

```text
A = 0
```

注意：

- `A` 不是 probability；
- `A` 不是 confidence；
- `A` 不能写入 CognitionStatement.confidence；
- `A` 只控制 scheduling / escalation budget。

## 8.3 为什么不用简单求和

若 Novelty、PredictionError、Boundary 都来自同一次代码变更，线性求和会把同一事件算三次。

先取族内最大值，可以显著降低重复解释同一现象导致的 attention amplification。

---

# 9. Event Boundary 协议

事件边界分为 Hard Boundary 和 Soft Boundary。

## 9.1 Hard Boundary

无需模型判断：

- `world_scope_hash` 改变；
- repository / branch / principal 切换；
- runtime incarnation / process generation 改变；
-明确事务 commit / rollback；
- source authority revision 改变。

Hard Boundary：

- 立即关闭当前 event window；
- 不把两个世界的 Observation 合并；
- 触发 pending candidate flush / defer；
- 必要时创建新 WorldFrame。

## 9.2 Soft Boundary

由多个低成本信号形成：

- burst → idle；
- task phase change；
- semantic topic discontinuity；
- relation graph topology material change；
- explicit “阶段完成”；
- sustained change cluster 结束。

Soft Boundary 只代表：

> “现在可能是一次合理的总结/固化机会。”

它不意味着 candidate 必须被固化。

---

# 10. Debounce、Coalescing 与 Refractory

现实系统会产生 notification storm。

例如一次保存文件可能产生：

```text
modify
rename-temp
write
metadata-change
watcher-event
hash-change
```

如果全部进入 L4，会造成 LLM storm。

因此 G1 必须支持：

## Debounce

短窗口内相同 `event_key` 的高频信号延迟决策，等待稳定 delta。

## Coalescing

同一 causal event 合并，但保留：

- first_seen；
- last_seen；
- unique lineage roots；
- source revisions；
- min/max observations；
- event count。

## Refractory

同一 entity / relation 刚刚完成一次昂贵 L4 理解后，进入短暂认知冷却期。

冷却期间：

- 普通重复不再次调用 LLM；
- 新 lineage root、hard interrupt、scope shift、material counterevidence 可以打破 refractory。

**具体毫秒数不在 V0.1 规范中冻结，必须通过领域 benchmark 校准。**

---

# 11. Schema Fast Path

如果新 Observation 与已有稳定 Schema 高度一致，可以：

- 跳过部分昂贵 semantic reconstruction；
- 更快生成结构化 candidate；
- 更早进入 G4 readiness check。

但以下全部禁止：

```text
SchemaMatch → +EvidenceWeight
SchemaMatch → +Authority
SchemaMatch → +IndependentRoot
SchemaMatch → 自动Stable
```

Schema compatibility 仅影响：

```text
processing cost
scheduling delay
candidate construction path
```

它对 truth support 的贡献严格为 0。

这是防确认偏误的硬约束。

---

# 12. 自适应 Revalidation 频率

## 12.1 核心思想

稳定对象不应高频检查，快速变化对象不应低频检查。

设某类事实的变化事件率为：

```text
lambda
```

在近似 Poisson hazard 下，经过时间 `Δt` 后发生至少一次变化的风险：

```text
P_stale = 1 - exp(-lambda * Δt)
```

若策略允许最大 stale risk 为 `delta`：

```text
Δt_max = -ln(1-delta) / lambda
```

因此：

- lambda 高 → revalidation interval 短；
- lambda 低 → interval 长；
- lambda≈0 的结构认知无需周期性高频扫描。

## 12.2 lambda 不应只靠 EMA

对于低频事件，单纯 EMA 容易因短期安静误判为“永远稳定”。

协议推荐使用带 domain prior 的 Gamma-Poisson rate estimator：

```text
lambda ~ Gamma(alpha0, beta0)

observed n changes over exposure T

posterior:
alpha = alpha0 + n
beta  = beta0 + T

E[lambda] = alpha / beta
```

不同 cognition / entity type 具有不同 `alpha0/beta0`，即 volatility prior。

实现阶段可以使用整数近似或预计算策略表，保证跨平台可复现。

## 12.3 Event-driven invalidation 优先于 Timer

如果 source revision、Git diff、dependency、runtime fact 已明确发生变化：

```text
立即触发 G5
```

不能等 timer 到期。

Timer 只是没有原生事件时的 stale-risk fallback。

---

# 13. Revalidation Lease

每条 Stable/Core cognition 未来应拥有一个 `RevalidationLease` 概念：

```text
cognition_id
world_scope_hash
source_dependency_hashes
volatility_policy_ref
last_verified_at
next_risk_boundary
revalidate_after
revalidate_before
reason_codes
policy_sha256
```

Lease 到期不意味着 cognition 为假。

它只意味着：

```text
verified → stale-risk-increased → eligible-for-revalidation
```

只有真实反证才能进入 Challenge。

---

# 14. Background Replay / Consolidation 优先级

离线候选不能简单 FIFO。

协议使用三个核心概念：

- **Need**：未来可能再次用到的程度；
- **Expected Information Gain**：重新处理后预计能减少多少模型不确定性；
- **Model Impact**：这条认知影响多少关键实体/关系/决策路径。

基础 replay priority：

```text
P_replay = Need × InformationGain × ModelImpact
```

另外必须加入 `AgingFloor`，避免长期低优先级候选永久饥饿。

冲突候选具有最小优先级保护：

> 已经存在强 counterevidence 的 cognition，不能因为当前任务“不相关”而永久不处理。

---

# 15. Cognitive Budget

节律系统必须显式管理认知成本，否则“理解一切”会吞噬 Runtime。

每个调度窗口未来至少有：

```text
CognitiveBudget
├── llm_calls
├── llm_input_tokens
├── wall_time_ms
├── cpu_budget
├── io_reads
└── background_share
```

队列至少分三条 lane：

```text
Critical / Interrupt
Interactive / Task
Background / Replay
```

规则：

1. Critical 可抢占 Background；
2. Background 不能使当前交互延迟失控；
3. Interactive 不能永久饿死 Background；
4. 多个 world/domain 使用 weighted fair scheduling + aging；
5. cost estimate 只影响调度，不影响 truth；
6. budget exhaustion 时优先 DEFER，不允许为了省成本降低 evidence standards。

---

# 16. LLM 在节律协议中的权限

LLM 可以：

- 评估语义 novelty；
- 评估 task relevance；
- 提出 future need；
- 解释 event cluster；
- 产生 Hypothesis；
- 在模糊样本中建议 merge/split。

LLM 不可以：

- 修改 hard scope gate；
- 给自己提高 budget；
- 伪造 independent recurrence；
- 决定 Evidence Authority；
- 把 Attention Score 当成 Confidence；
- 自己将 Candidate 晋升 Stable/Core；
- 仅因“我已经多次看到”就刷新 cognition；
- 绕过 cooldown / audit / policy version。

硬门应 deterministic；LLM 只能提供可审计 feature/proposal。

---

# 17. 协议对象（未来 Contract 草案）

本阶段只冻结语义，不写 Contract 文件。

## RhythmSignal

```text
signal_id
world_frame_id / world_scope_hash
principal_scope_hash
domain
source_ref
source_revision
entity_refs
relation_refs
lineage_root_hashes
signal_kind
observed_at_ms
monotonic_sequence
payload_sha256
```

## EventEnvelope

```text
event_id
event_key
boundary_kind
first_seen_at
last_seen_at
scope
unique_lineage_roots
coalesced_signal_ids
event_state
```

## AdmissionContext

```text
current_layer
target_layer
attention_features
current_uncertainty
existing_cognition_refs
active_task_ref
budget_snapshot
cooldown_state
policy_ref
```

## AdmissionDecision

```text
decision_id
signal/event/candidate ref
from_layer
to_layer
decision
reason_codes
attention_score_milli
eligible_after
cooldown_until
budget_lease_ref
policy_sha256
created_at_ms
```

## RevalidationLease

见第 13 节。

## AdmissionAuditRecord

所有改变处理深度的决策必须能回答：

```text
为什么处理？
为什么没处理？
使用了哪些 feature？
使用哪个 policy 版本？
是否调用过 LLM assessor？
消耗多少 budget？
是否由 hard interrupt 触发？
```

---

# 18. L0-L8 节律矩阵

| Transition | 默认节律 | LLM | 主要准入依据 |
|---|---|---:|---|
| L0 → L1 | event-driven / continuous | 否 | WorldFrame / scope |
| L1 → L2 | 高频 deterministic | 否 | valid observation / delta |
| L2 → L3 | delta / event batch | 通常否 | entity/relationship change |
| L3 → L4 | sparse escalation | 是，可选 | Attention Score / unknown / conflict |
| L4 → L5 | candidate admission | 可生成 proposal | structure + provenance + scope |
| L5 → L6 | rare consolidation | 否（最终裁定） | Cognition Core evidence policy |
| L6 → L7 | on-demand / material change | 可用 | need + model change |
| L6/L7 → L8 | request-driven | 是 | current task/query |
| L6 → G5 | adaptive | 否/可辅助 | hazard / invalidation / prediction error |

---

# 19. Open-World Logic 与准入

节律控制器必须认识：

```text
TRUE
FALSE
UNKNOWN
CONFLICTED
```

没有观察到，不代表 FALSE。

因此：

- absence signal 只有在 closed search scope + adequate coverage 下才可以形成强 negative evidence；
- UNKNOWN 可以有很高 Attention，因为它值得调查；
- CONFLICTED 可以有很高 Replay/Revalidation Priority；
- 但 UNKNOWN/CONFLICTED 都不能被 admission controller 强行压成 TRUE/FALSE。

---

# 20. Hysteresis：防止认知节律震荡

如果某 admission threshold 为单值，分数在阈值附近抖动会导致：

```text
ESCALATE → DROP → ESCALATE → DROP
```

因此所有会改变昂贵处理状态的 gate 必须支持 hysteresis：

```text
enter_threshold > maintain_threshold
```

例如：

```text
只有达到高阈值才进入 ESCALATED；
进入后只有跌破较低阈值才退出；
中间区域保持当前状态。
```

具体阈值不在 V0.1 固定，由 benchmark 校准。

---

# 21. Restart / Crash / Failure Semantics

节律系统失败不能破坏世界事实或主 Runtime。

## Controller Failure

- 不影响工具执行主链；
- 不修改 permissions；
- 不修改 Execution Integrity；
- 不允许不完整 admission decision 被当成 Stable Cognition；
- 未完成昂贵处理进入 DEFER / retry queue；
- Persistent Cognition 保持最后已验证状态。

## LLM Failure

- Observation 不丢失；
- Hypothesis 不生成；
- 不降级为“直接相信原始文本”；
- 可在未来 budget 可用时重新处理。

## Clock Failure

- cadence/cooldown 优先使用 monotonic duration；
- world fact 时间保留 wall-clock valid/observed time；
- 两者不得混用。

## Queue Overflow

优先：

1. coalesce；
2. drop exact duplicate；
3. compact low-impact raw observations；
4. 保留 hard interrupts / conflict / high-impact events；
5. 不以“自动固化”作为释放队列的手段。

---

# 22. 反证 / 攻击推演

## Case 1：同一错误消息复制 10,000 次

风险：recurrence 变成“多数即真”。

结果：同 lineage root 合并；可提高 event intensity，但不能增加 independent evidence roots。

**通过。**

## Case 2：恶意输入极度新奇

风险：novelty 直接变长期认知。

结果：Novelty 只提高 A，最多触发 L4；G4 仍需 evidence。

**通过。**

## Case 3：LLM 非常确信一个错误架构判断

风险：语言 confidence 变系统 confidence。

结果：LLM 只产生 Hypothesis；model inference promotion contribution 仍为 0。

**通过。**

## Case 4：稳定认知被重复 Query 数千次

风险：retrieval self-reinforcement。

结果：retrieval read-only，不产生 evidence，不刷新 verification。

**通过。**

## Case 5：配置在一分钟内来回切换 100 次

风险：LLM 调用风暴 / cognition oscillation。

结果：debounce + coalescing + refractory + hysteresis；最终保留事件序列和稳定状态。

**通过。**

## Case 6：一个低频但极高影响的核心模块发生一次变化

风险：recurrence 不够导致忽略。

结果：GraphImpact / source invalidation / hard interrupt 可直接触发 G5，不依赖重复次数。

**通过。**

## Case 7：schema 与新信息高度一致，但 schema 本身错了

风险：确认偏误。

结果：SchemaMatch 仅减少处理成本，truth contribution=0；新事实仍需现实 evidence。

**通过。**

## Case 8：两个强来源互相冲突

风险：last-write-wins。

结果：进入 CONFLICTED / CHALLENGED，Replay Priority 提高；不自动选边。

**通过。**

## Case 9：切换 Git branch 后旧认知继续被使用

风险：世界污染。

结果：Hard WorldFrame boundary，旧 scope cognition 不进入新 scope 当前投影。

**通过。**

## Case 10：Controller 挂掉

风险：主 Agent 无法工作。

结果：controller 为认知控制面，不是执行 Runtime；旧 Persistent World Model 保持，主链继续，新的长期固化暂停。

**通过。**

## Case 11：高优先级交互长期占满预算

风险：background 永久饥饿，世界模型无法巩固。

结果：weighted fair scheduling + aging floor + background minimum share；Critical 可抢占，但不能永久删除 background entitlement。

**通过。**

## Case 12：所有 cognition 到期时间相同导致 revalidation storm

风险：周期雪崩。

结果：每条 cognition 有独立 hazard / lease；scheduler 受 budget 和 priority 控制；timer 是 eligibility window，不是必须同刻执行。

**通过。**

## Case 13：Prediction 自己变成 Evidence

风险：世界模型自我实现 / 自证。

结果：Prediction 只生成 expected state；只有后续真实 Observation 能形成 evidence。

**通过。**

## Case 14：没有搜到某对象，因此系统判断不存在

风险：closed-world hallucination。

结果：无 closed search scope + coverage，不允许形成强 negative evidence。

**通过。**

## Case 15：为了降低成本，系统放宽稳定认知门槛

风险：预算影响真值标准。

结果：budget exhaustion 只能 DEFER，不能改变 G4 evidence policy。

**通过。**

---

# 23. V0.1 最终冻结候选

建议将以下原则作为后续 Contract 的不可破坏语义：

1. **高频感知、低频语义理解、稀疏持久固化。**
2. **事件驱动优先，timer 仅做 fallback。**
3. **Attention 与 Truth 完全分离。**
4. **Admission 是多级门，不是一个总分数。**
5. **独立 recurrence 必须 lineage-aware。**
6. **Query/Projection 永远只读。**
7. **Schema 可加速，不可增信。**
8. **Prediction Error 触发重新检查，不直接改写事实。**
9. **Revalidation 基于 volatility/hazard 自适应。**
10. **Hard Interrupt 可绕过等待，不可绕过证据。**
11. **预算只能影响何时处理，不能降低真实性标准。**
12. **LLM 有解释权，没有 admission 最终裁定权和现实裁定权。**
13. **所有 admission decision 必须可审计、可版本化、可复现。**
14. **节律控制器失败时，优先暂停新认知固化，而不是污染 Persistent World Model。**
15. **本协议永远不拥有工具执行、A0-A5 权限、任务完成判定或第二 Runtime。**

---

# 24. 下一步（本文件不执行）

如果本协议讨论后冻结，下一步不是接 Runtime，而是把语义正式化为 Contracts：

```text
src/contracts/world_rhythm_signal.py
src/contracts/world_event.py
src/contracts/world_admission.py
src/contracts/world_revalidation.py
src/contracts/world_budget.py
```

然后先写纯 deterministic policy / property tests，验证：

- lineage non-amplification；
- no retrieval reinforcement；
- attention/truth isolation；
- hysteresis；
- event coalescing；
- adaptive revalidation monotonicity；
- budget non-interference with truth；
- scope isolation；
- fairness / no starvation；
- crash-safe defer semantics。

在这些 Contracts 与性质测试冻结之前，不接九层 Runtime 实现。
