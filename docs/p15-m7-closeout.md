# P15 M7 — Memory → WorldCandidate → WorldPatch 闭环收尾记录

日期：2026-08-12
分支：`agent/p15-memory-ssot-life-world-closure-v0.1`

## 契约

- 新增 [contracts/world_understanding/memory_candidate.py](../../src/contracts/world_understanding/memory_candidate.py)：
  `MemoryWorldCandidate`（candidate_id/life_id/world+principal scope hash、
  source memory+derivation ref、claim_key、semantic_payload、evidence_refs、
  lineage_root_hashes、epistemic_status、confidence_milli、volatility_class、
  有效区间、privacy_scope、candidate_sha256）+ 确定性 id/root-hash 派生。
  secret privacy 与倒置有效区间在契约层拒绝。

## Life 侧（只做 candidate 投递，永不造 WorldPatch）

- store schema 16→17：新增 `memory_world_candidate_outbox`
  （pending/delivered/failed + 幂等 receipt + 确定性 payload）。
- [memory_coordinator.py](../../src/life_service/memory_coordinator.py)
  `project_memory_world_candidates`：只投影 active + `world_candidate_eligible`
  + WORLD 域 + 非 secret + 未过期 + 非注入标记的 L3/L4/L5；
  user_asserted 置信度封顶 750；幂等入 outbox。

## WU 侧（唯一 ingress / cognition 路径）

- 新增 [cognition/memory_candidate.py](../../src/world_understanding/cognition/memory_candidate.py)
  `MemoryWorldCandidateBridge`：
  - `to_cognition_evidence`：source_kind=memory、extractor=memory_projection、
    权威上限按 epistemic（user_asserted≤750 / observed、verified≤1000）；
  - 反回声自证：roots 全部仅由 WU/GIT 输出类证据
    （code_perception/model_synthesis）覆盖 → `echo_only`，不增加独立证据组；
  - `stability_report` 复用现有 `evaluate_evidence`；
  - `materialize_world_patch`：只有 existing stability 达 C2+ 才由
    World Understanding 内部写 WorldPatch 记录到 WorldStateStore，
    Memory 本身无此能力。
- MEMORY DirectKnown authority 恒 0（p3.py 未动）；Repository 仍走 GIT_CODE
  原路径（authority 1000 不变）。

## 测试与回归

- 新增 M7 5 文件 **40 用例**：candidate 契约/投影/outbox 幂等与冲突、
  独立证据折叠、时效与 volatility、GIT/WU 反回路、outbox 跨重启恢复、
  稳定 memory 证据（+直接现实证据）→ C2+ WorldPatch。
- 适配：守卫测试允许 coordinator 引用 contracts.world_understanding 契约
  （仍禁止 WU 运行时）；schema 17 的三处降级模拟 + M1 store 测试。
- 全量回归：`pytest tests` = **2584 passed / 17 skipped / 0 failed**。

## 关键事实

1. 反回声集合只有 `code_perception`/`model_synthesis`；`fact_execution` 等
   现实证据覆盖 root 时 candidate 仍独立（记忆+现实可组合成稳定补丁）。
2. 稳定 WorldPatch 必须由 WU 内部在 `highest_eligible_level >= C2` 后创建；
   memory-only 证据永远非 DIRECT（SOURCE_FACTOR memory=600），单靠记忆
   到不了 C2，必须与直接现实证据共同作用。
