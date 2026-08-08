# 天工造物 V3：世界认知核心（World Cognition Core）V0.1

> 状态：设计冻结 / Core 已实现 / 尚未接入 Zongdiaodu 主链  
> 目标：在不改变 V3 单 Runtime、单总网关、现有执行与权限体系的前提下，为天工增加一个可关闭、可验证、低频稳定更新的结构化世界认知核心。

---

## 1. 第一性原理定义

天工需要区分三类内部信息：

- **记忆（Memory）**：我经历过什么、什么事情重要或难忘。
- **知识（Knowledge）**：一般规律、方法、技能和“通常应该怎么做”。
- **世界认知（World Cognition）**：结合长期记忆、固有认知先验和现实证据后，我对“当前世界是什么”的稳定结构化认识。

因此世界认知不是记忆数据库的另一个分区，也不是向量 RAG，更不是第二个 Agent Runtime。

V0.1 的核心定义为：

> **World Cognition Core = 证据约束、来源可追溯、低频修订、结构固定的持久认知模型。**

它只负责形成、保存、修订和投影认知；不负责工具执行、任务规划、权限裁决和任务完成判定。

---

## 2. 与“完整世界模型”的边界

当前 V0.1 更准确地说是 **Persistent Structured Epistemic Model（持久结构化认知/信念模型）**，还不是完整预测型 World Model。

它回答：

- 当前系统有哪些稳定组件和边界？
- A 与 B 的稳定关系是什么？
- 哪条认知仍被现实证据支持？
- 哪条旧认知已经受到反证，需要重新验证？

它暂时不负责：

- 给定动作 A，预测环境下一状态 S'；
- 模拟多步行动后果；
- 在内部生成完整环境动力学模型。

以后若加入“动作 → 世界状态变化预测/仿真”，才进入更完整的 predictive world model 范畴。

---

## 3. 前沿研究参考与吸收方式

本设计参考以下研究方向，但不照搬论文框架，而是按天工 V3 现有工程语言收敛：

1. **CoALA: Cognitive Architectures for Language Agents** — arXiv:2309.02427  
   吸收：区分工作/情景/语义等记忆与决策/行动；支持将“稳定世界语义”与经历记忆区分。

2. **Generative Agents: Interactive Simulacra of Human Behavior** — arXiv:2304.03442  
   吸收：Observation → Memory → Reflection/Abstraction 的认知形成路径。

3. **TRUSTMEM** — arXiv:2606.25161  
   吸收：长期 consolidation 不能无条件相信模型总结，需要显式完整性、遗漏/幻觉防御和可回溯证据。

4. **Memory Provenance Laundering / Provenance-Preserving Memory** — arXiv:2607.29167  
   吸收：来源权威不能在总结、复制、重写过程中被“洗白”或放大；派生内容必须保留 lineage。

5. **MemIR** — arXiv:2605.25869  
   吸收：不同记忆/认知类型需要不同语义和更新规则，不能把所有持久状态当同一种自然语言 chunk。

6. **Repository Intelligence Graph** — arXiv:2601.10112  
   吸收：软件认知应以结构、实体、关系和 provenance 为核心，未来代码感知层不应只依赖文本向量检索。

7. **Agentic World Modeling** — arXiv:2604.22748  
   吸收：世界模型应区分观察/建模/预测/演化层；本 V0.1 明确只落地稳定认知核心，不伪称已实现预测世界模型。

---

## 4. 与天工 V3 实际工程语言的适配

当前 V3 的源码基线使用：

- Python 3.12；
- Pydantic v2 Contract；
- canonical SHA-256；
- 明确的 immutable contract / revision chain；
- stdlib 驱动的 Runtime 组件；
- FactKernel 的 evidence-first 思路；
- 独立 `~/.tiangong/v3/` 持久化根；
- 现有 Total Gateway / Zongdiaodu / Omni Body / Grant / Execution Integrity 主链。

因此 V0.1 **没有引入 Neo4j、Redis、向量数据库或新的 Agent 框架**。

实现采用：

- Python 3.12；
- Pydantic Contract；
- Python stdlib `sqlite3`；
- WAL + `synchronous=FULL`；
- immutable ledger + CAS head；
- lazy initialization；
- deterministic integer-only stability policy。

目的不是技术栈炫技，而是降低接入面和维护债务。

---

## 5. 总体架构

```text
Memory / Fact / Code Perception / User Assertion
                       │
                       ▼
              Cognition Evidence
                       │
             provenance / lineage
                       │
                       ▼
               Candidate Cognition
                       │
                       ▼
           Deterministic Stability Policy
                       │
          ┌────────────┼────────────┐
          │            │            │
       Promote      Challenge     Reject/Wait
          │            │
          ▼            ▼
     Stable/Core   Reverify/Supersede
          │
          ▼
        Store
          │
          ▼
   Live Evidence Re-evaluation
          │
          ▼
      Context Projection
          │
          ▼
      Future Zongdiaodu
```

公共入口只有：

```python
WorldCognitionFacade
```

Core 的 Store / Stability / Consolidator / Retrieval / Evidence Ledger 都是内部实现，不应成为第二套 Runtime API。

---

## 6. V0.1 模块

```text
app/backend/tiangong-backend/v3/world_cognition/
├── __init__.py
├── facade.py
├── store.py
├── evidence.py
├── stability.py
├── priors.py
├── consolidator.py
└── retrieval.py
```

### facade.py

唯一未来挂载面。

- `enabled=False`：NoOp；
- OFF 时不创建目录、不创建 SQLite、不读 evidence、不启动线程、不调用模型；
- projection 异常时返回空字符串，退回 legacy V3 context path；
- 公共 consolidation 永远使用 `deterministic_policy` 权限；
- C4 显式系统权限暂不暴露给公共 Facade，未来必须绑定现有 Runtime 的真实授权票据，而不是相信字符串。

### store.py

不可变认知账本。

- priors：immutable；
- evidence：immutable；
- statements：immutable revisions；
- revisions：immutable transition decisions；
- cognition_heads：唯一可更新指针。

状态提交使用：

```text
BEGIN IMMEDIATE
      ↓
检查 expected_head_sha256
      ↓
追加 Statement + Revision
      ↓
CAS 更新 Head
      ↓
COMMIT
```

禁止 last-write-wins。

### evidence.py

治理证据入口。

派生 evidence 必须：

- 引用真实存在的 parent evidence；
- 保留所有 parent lineage roots；
- 保留 parent cognition ancestor chain；
- 不跨 life/domain/world/principal scope；
- authority ceiling 不能高于 parent provenance 所允许的上限；
- provenance integrity 不能在派生过程中被放大。

### stability.py

完全 deterministic，不调用 LLM。

负责：

- authority ceiling；
- provenance integrity；
- source/class weighting；
- freshness；
- negative-search coverage；
- same-source correlation discount；
- lineage connected-component collapse；
- support/counter aggregation；
- C0-C3 eligibility；
- challenge threshold。

### priors.py

V0.1 内置 7 个软件世界认知先验：

1. continuity；
2. evidence_first；
3. reality_over_memory；
4. stability；
5. revisability；
6. provenance；
7. anti_hallucination。

所有 Prior：

```text
empirical_evidence_weight = 0
```

即潜意识可以影响“如何理解”，不能自己证明“现实是什么”。

### consolidator.py

认知状态机。

LLM 可以提出 `CognitionProposal`，但不能成为 commit authority。

### retrieval.py

只投影当前仍有活证据支持的 STABLE / CORE Cognition。

存量状态曾经是 Stable，不代表今天还应投影。Retrieval 会重新运行 Stability Evaluation；证据衰减、失效、闭包损坏时自动停止投影。

---

## 7. 数学模型

### 7.1 单条证据有效权重

设一条证据 `i` 的：

- `a_i` = `min(source_credibility, authority_ceiling, provenance_integrity)`；
- `c_i` = evidence class factor；
- `s_i` = source factor；
- `v_i` = coverage factor；
- `f_i(t)` = freshness factor。

全部采用 0–1000 milli 整数。

基础有效权重：

```text
q_i = a_i × c_i × s_i × v_i × f_i(t)
```

每一步均做 milli 定点乘法并截断至 `[0,1000]`，不使用浮点数，避免跨平台签名/决策差异。

### 7.2 时间衰减

非 structural evidence：

```text
f(t) = H / (H + age)
```

milli 形式：

```text
f_milli = 1000 × H // (H + age)
```

满足：

- `age=0 → 1000`；
- `age=H → 500`；
- 单调非增；
- 无浮点不确定性。

Structural evidence 不做墙钟衰减；它通过 world scope 变化或现实反证失效。

### 7.3 同源折扣

同一个有效独立性分组内部，按权重从高到低：

```text
w0 + γw1 + γ²w2 + ...
```

V0.1：

```text
γ = 0.25
```

大量复制同源材料不能线性放大置信度。

### 7.4 独立性不信任 caller 声明

仅依赖 `independence_group_hash` 仍可能被伪造。

因此 V0.1 重新构造 **effective independence group**：

> 若两条 evidence 的 declared group 相同，或共享任何 lineage root，则属于同一 provenance connected component。

对连通分量做 canonical hash，作为真正用于 quorum 的 group。

结果：

```text
相同 lineage root
+ 不同伪造 independence_group_hash
≠ 多份独立证据
```

### 7.5 独立组之间合并

不同有效独立组使用 noisy-OR：

```text
S_next = S + x - S×x
```

milli 整数实现，范围天然保持 `[0,1000]`。

Support 与 Counter 分别计算：

```text
Net = max(0, Support - Counter)
```

如果同一 effective provenance component 同时出现在 support 和 counter，两边同时移除并标记 conflict，避免一份来源同时“证明”和“反驳”同一认知。

---

## 8. 认知等级

`confidence_milli` 不是“90% 概率为真”，而是 deterministic evidence support margin。

### C0 — Candidate

候选认知，可无有效证据。

### C1 — Provisional

至少：

- 1 个有效独立证据组；
- Net ≥ 300。

### C2 — Stable

至少：

- 2 个有效独立证据组；
- 至少 1 个 direct observation group；
- Net ≥ 600；
- Counter ≤ 350。

### C3 — Core

至少：

- 3 个有效独立证据组；
- 至少 1 个 direct observation group；
- Net ≥ 850；
- Counter ≤ 150。

### C4 — Protected Cognition

C4 **不是更高概率等级**。

它是治理保护等级：

```text
C3 + explicit system authority → C4
```

自动 evidence policy 永远只能到 C3。

C4 仍允许被强反证 Challenge，但不能被普通 deterministic caller Supersede/Retire。

---

## 9. 状态机

```text
GENESIS
   ↓
CANDIDATE/C0
   │
   ├── REPLACE_CANDIDATE
   │
   └── PROMOTE
          ↓
    PROVISIONAL/C1
          │
          └── PROMOTE
                 ↓
             STABLE/C2
                 │
                 └── PROMOTE
                        ↓
                     CORE/C3
                        │
                        └── PROTECT (explicit authority only)
                               ↓
                            CORE/C4
```

同值新证据：

```text
REFRESH
```

反证：

```text
ACTIVE
  ↓ CHALLENGE
CHALLENGED
  ↓ BEGIN_REVERIFY
REVERIFYING
  ├── CONFIRM → 原认知恢复
  └── SUPERSEDE → 新值成为该 cognition slot 的下一 revision
```

所有 Statement / Revision 都 immutable；变化通过新 revision 表达。

---

## 10. Cognition Slot 原则

`cognition_id` 由以下内容形成：

```text
life_id
+ domain
+ world_scope
+ principal_scope
+ claim_kind
+ subject
+ predicate
+ condition
```

**不包含 value。**

因此：

```text
RuntimeTopology = single
```

变成：

```text
RuntimeTopology = distributed
```

是同一 cognition slot 的 revision，而不是两条互不相干的“事实”。

---

## 11. 认知自证与循环污染防御

禁止：

```text
Cognition A
  ↓ 注入 LLM
LLM 输出
  ↓ 写入 Memory
Memory summary
  ↓ 再支持 Cognition A
```

Evidence 保留：

```text
ancestor_cognition_ids
```

如果待评估 Cognition 出现在 evidence 的 ancestor chain 中，该 evidence 对其自身支持权重为 0。

派生 evidence 还必须单调保留 parent ancestor chain，不能通过再次总结洗掉。

---

## 12. “没找到 ≠ 不存在”

Negative / Aggregate observation 必须携带：

```text
search_scope_hash
coverage_milli
```

Coverage 会直接进入有效权重。

因此扫描 10% 的目录没找到第二个 Runtime，不可以等价于：

```text
single_runtime = confirmed
```

只有高 coverage、明确搜索空间和独立证据组合才允许提升稳定度。

---

## 13. Scope 隔离

每条 Evidence / Cognition 绑定：

```text
life_id
world_scope_hash
principal_scope_hash
domain
```

目的：

- repo A 不能证明 repo B；
- user A 的认知不能污染 user B；
- 不同 worktree / branch / snapshot 的事实不能自动互认；
- 未来 Software Cognition 应把 Git/Worktree snapshot 编码进 `world_scope_hash`。

---

## 14. 为什么选择 SQLite，而不是第一版上图数据库

V0.1 当前主要任务是：

- 定义认知语义；
- 保证 immutable history；
- 做 evidence closure；
- 做稳定性计算；
- 保证最小接入和可关闭性。

SQLite 的优势：

- Python stdlib 原生；
- Windows 桌面运行环境成熟；
- 无独立服务；
- transaction/CAS 易验证；
- 安装体积和部署复杂度低；
- 与当前 V3 本地桌面生命体架构匹配。

未来 Code Perception 进入百万级 symbol/edge 后，可增加专用 index 或 graph projection，但不应改变 Cognition Contract。

---

## 15. OFF 公理

未来接入主链时，Master Valve 必须满足：

```text
World Cognition OFF ≈ 当前 legacy V3
```

严格要求：

- 不创建 `~/.tiangong/v3/world_cognition/`；
- 不读 cognition DB；
- 不读取 Memory/FactKernel 为 Cognition 服务；
- 不启动线程/Timer；
- 不调用 LLM；
- 不增加 prompt token；
- 不改变 ToolResult；
- 不改变 ActionRegistry；
- 不改变 A0–A5；
- 不改变 Execution Integrity；
- 不改变 run_state / completion；
- Cognition 读取失败时仅退化为“无 Cognition Context”。

当前 Facade 已实现零副作用 OFF 和 read-path fail-open-to-legacy；真正 `peizhi.py + Zongdiaodu` 接线放在下一阶段。

---

## 16. 本轮逻辑/数学推演中实际发现并修掉的问题

### 16.1 缺少 REFRESH

问题：同一稳定认知获得新证据时，没有合法的“值不变但证据更新”状态迁移。

修复：新增 `REFRESH`。

### 16.2 C0 候选无法换值

问题：未稳定 Candidate 若发现更合理值，原状态机只能错误使用 SUPERSEDE。

修复：新增 `REPLACE_CANDIDATE`，只允许 `CANDIDATE/C0 → CANDIDATE/C0`。

### 16.3 Privacy Scope 可被普通 revision 改写

问题：privacy 不属于 cognition_id；若不额外冻结，同一 slot 可被 private → public 改写。

修复：已有 slot 的 privacy_scope 在 V0.1 中不可通过普通 consolidation 修改。

### 16.4 C4 被错误当成“比 C3 更高的证据等级”

问题：C4 实际是 protection class，不是 empirical probability class。

修复：C4 live evidence 要求等价 C3，但进入/替换 C4 需要显式系统权限。

### 16.5 Randomized Property Test 生成非法 Contract

问题：测试随机生成 `credibility > authority_ceiling`，Contract 正确拒绝。

修复：测试生成器改为先生成 ceiling，再在 ceiling 内生成 credibility；没有放宽 Contract。

### 16.6 Unicode Path Test 混淆 Path 与 OpaqueId

问题：测试把中文同时放入文件路径和 OpaqueId，而 V3 OpaqueId 正确要求 ASCII 稳定标识符。

修复：保留中文文件路径，只让 OpaqueId 使用规范 ASCII。

### 16.7 假独立证据组

问题：只相信 `independence_group_hash`，攻击者/错误 bridge 可以把同一 lineage root 伪造成多个 group。

修复：Stability Engine 通过 shared lineage root + declared group 重新计算 provenance connected components。

### 16.8 派生证据 provenance laundering

问题：派生记忆/总结可能丢掉 parent lineage 或把低权限证据重新标成高权威。

修复：新增 Evidence Ledger，强制 lineage root / ancestor chain 单调保留，authority/provenance 不允许派生放大。

### 16.9 公共 Facade 的显式权限伪造

问题：如果公共 Facade 接受 `decision_authority="explicit_system_authority"` 字符串，调用者可伪装系统权限。

修复：V0.1 公共 Facade 只运行 deterministic consolidation；C4 权限未来必须绑定 V3 现有 Runtime 的真实授权票据。

---

## 17. 验证策略

测试分四组：

1. **Contract invariants**：身份、hash、revision、C4、evidence shape；
2. **Core adversarial tests**：CAS、privacy、challenge/reverify、C4、stale retrieval、OFF side effects；
3. **Mathematical properties**：随机 600 组 evidence、单调性、有界性、排列不变性、同源复制、model-only 禁晋级；
4. **Provenance / deployment edges**：中文路径、lazy storage、派生 lineage、authority non-amplification、假独立 group。

当前正式 CI 验证（临时 workflow 阶段）：

```text
Python compile: PASS
World Cognition tests: 65 PASS
```

后续源码继续变化时必须重新跑完整套件，不能把这一结果当成未来版本的永久证明。

---

## 18. V0.1 明确不做什么

本轮不修改：

- Zongdiaodu；
- Total Gateway；
- Omni Body；
- FactKernel；
- Execution Integrity；
- Memory L1–L5；
- Life Service；
- ActionRegistry；
- A0–A5 权限系统。

本轮也不做：

- AST/Tree-sitter Code Perception；
- 向量数据库；
- 多域 User/Self/Organization Cognition；
- 后台自主 consolidation；
- predictive world model；
- 自动 C4。

这些属于后续阶段。

---

## 19. 下一阶段最小接入计划

完成 Core 后，正式接入仍坚持四步：

```text
1. peizhi.py
   只增加 Master Valve + root/limits

2. WorldCognitionFacade
   作为唯一挂载对象

3. Zongdiaodu dynamic context
   只增加 retrieve/project_context 薄调用

4. Fact / Memory Bridge
   通过受控 evidence adapter 输入，不反向修改原系统
```

Master Valve 默认：

```python
QIYONG_SHIJIE_RENZHI = False
```

OFF 必须行为等价 legacy V3。

---

## 20. 最终设计原则

> **记忆可以多，感知可以快，认知必须少、稳、可证、可修订。**

> **LLM 可以提出认知，但不能因为自己说过就把它固化成现实。**

> **先验影响解释，不创造事实。**

> **证据可以被总结，但 provenance 和 authority 不能在总结中被洗白。**

> **稳定认知可以被现实挑战；保护认知可以被挑战，但不能被普通调用者偷偷改写。**

> **世界认知是 LLM 的高可信上下文，不是权限系统，也不是第二个 Runtime。**
