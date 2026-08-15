# P18-M4 Adversarial Long-Horizon Certification — Progress Record

Date: 2026-08-15
Branch: `agent/p18-m4-adversarial-certification`
M3 inherited baseline: `b532288b796f757e10a3263cc3ed110bb79258a2`
Status: **M4 ACTIVE — NOT CLOSED**

## Phase objective

P18-M4 certifies that the existing Single Gateway / Single Runtime / Single GatewayStateStore execution authority continues correctly under long-horizon faults, corruption, process restart, provider drift and concurrency. M4 is a certification phase; it must not introduce a second Runtime, Scheduler, persistence authority, continuity capsule, total gateway, startup entry, or parallel tool dispatcher.

## Verified milestones

### M4.1 Deterministic 1000-Step Harness — PASS

Cross-platform certification Run `31871703276`, verified candidate `50a3702ccdff583f845359122122e43a0e7c87a3`:

- Ubuntu: SUCCESS
- Windows: SUCCESS
- `tool_steps = 1000`
- `model_decision_rounds = 250`
- `checkpoint_count = 20`
- `epoch_count = 21`
- all 21 deterministic fault points observed:
  `49, 83, 121, 173, 241, 307, 377, 421, 489, 533, 577, 641, 702, 777, 850, 884, 921, 953, 965, 972, 981`
- max model working set: `64`
- max bounded snapshot items: `96`
- no second LongChainRuntime / Store / Scheduler introduced by the harness.

### M4.2 Hard Metrics — PASS

The same Ubuntu and Windows run recorded zero/false for every mandatory hard metric:

- missing_required_steps = 0
- duplicate_committed_irreversible_effects = 0
- authority_changes = 0
- request_id_changes = 0
- run_id_changes = 0
- illegal_generation_changes = 0
- unreconciled_ambiguous_effects = 0
- completed_obligation_loss = 0
- root_goal_hash_changes = 0
- task_contract_hash_illegal_changes = 0
- false_verified_facts = 0
- false_completion_accepts = 0
- tool_prompt_injection_authority_escalation = 0
- invalid_learning_promotions = 0
- silent_concurrency_overwrites = 0
- model_working_set_linear_growth = false
- run_snapshot_unbounded_growth = false
- ledger_replay_mismatch = 0
- ledger_seq_conflict = 0
- torn_tail_undetected = 0
- checkpoint_corruption_silent_accept = 0
- prepared_before_dispatch_violations = 0
- logical_effect_duplicate_commit = 0

## M4.3-M4.6 active certification candidate

Production repair freeze: `eec7bf0750b384bea7784198e0e1e328d9027a94`

A targeted P0 repair gate passed `20/20` before this production freeze was pushed. The repair closed two authority defects exposed by M4 certification:

1. Regenerative `start_effect` now uses the existing Gateway action-fence dispatch permit CAS instead of directly marking an effect started. Concurrent agents targeting the same immutable physical attempt can no longer both receive dispatch permission.
2. An immutable first transport result remains `AMBIGUOUS`; an independently verified `APPLIED` reconciliation is projected as logically committed by Frontier, Checkpoint, Recovery and Completion without rewriting that first result.

Temporary repair/applicator workflows were removed before the production freeze. This document-only commit exists to trigger the permanent Ubuntu/Windows certification workflows against the unchanged production tree above.

### M4.3 Corruption Certification — FINAL GATE RUNNING

The complete 20-case corruption matrix now maps every planned corruption to an existing canonical authority boundary:

- Completion Proof for hallucinated modification/test success/early completion;
- ToolResult poisoning and instruction-priority boundaries;
- transactional Effect + reconciliation for timeout-after-applied;
- Semantic Drift for bad compact summary;
- Checkpoint Reality Audit for bad checkpoint candidates;
- World State Freshness for stale facts;
- action-fence dispatch CAS for same-artifact concurrency;
- logical-effect registry for provider-switch replay;
- Learning promotion guard for false-memory promotion;
- Ledger hash chain / known-good checkpoint recovery;
- checkpoint checksum fail-closed / previous-known-good fallback;
- Version Compatibility Guard for schema upgrade;
- PREPARED-before-DISPATCH and DISPATCH-without-response crash recovery;
- concurrent Ledger sequence integrity.

M4.3 will be marked PASS only after the permanent Ubuntu and Windows jobs both succeed on the repaired production candidate.

### M4.4 Real Process Burn-in — FINAL GATE RUNNING

The permanent real-process workflow launches separate OS Python processes against the same canonical GatewayStateStore and RegenerativeExecutionAuthority, then performs actual process kill/restart and durable recovery:

- Case A: 500-step file engineering, kill/restart after durable checkpoint;
- Case B: 1000-step code-edit/test/fix with an intentional failing code revision followed by repair, plus kill/restart;
- Case C: 1000-step read-only investigation with current-checkpoint corruption and previous-known-good fallback;
- Case D: 500-step high-fault scenario with socket disconnect, API timeout, tool timeout, OS file lock, provider 5xx/reconnect, Ledger torn-tail corruption and process restart;
- Case E: dedicated Windows 1000-step kill/restart long-run;
- Case F: two separate subprocesses race for the same logical artifact effect; exactly one dispatch/write is permitted.

M4.4 will be marked PASS only after Ubuntu and Windows permanent jobs succeed.

### M4.5 Provider L4 — FINAL GATE RUNNING

The production provider registry under certification is:

- `deepseek_v4`
- `mimo`
- `glm_5_2`
- `minimax_m3`
- `gpt_5_6`

Each provider adapter must independently complete >=200 normalized model rounds and >=1000 deterministic simulated tool steps, with >99% parse coverage, stream reconnect semantics, checkpoint rehydration, ambiguous-effect reconciliation, false-completion prevention, ToolResult poisoning defense and zero provider-private reasoning leakage. Aggregate minimum evidence is >=1000 model rounds and >=5000 simulated tool steps.

This is high-fidelity production adapter/runtime L4 certification; it is not a claim that live vendor networks or credentials were exercised. No static `LONG_HORIZON_PRODUCTION_READY=true` flag can substitute for evidence.

### M4.6 Cross-Provider Resume Final — FINAL GATE RUNNING

DeepSeek-family source execution persists 300 durable structured steps, Verified Fact head, Artifact Revision head and an irreversible committed logical Effect before checkpoint. The same Request / Run / Generation / Task Contract / Authority then resumes under each target production provider profile:

- `mimo`
- `glm_5_2`
- `minimax_m3`
- `gpt_5_6`

Unrevalidated provider/model drift is blocked. Explicitly compatible + revalidated drift must preserve the structured Frontier and committed-effect identity. Provider-private reasoning is not transferred. Re-preparing the already committed immutable effect after provider switch must resolve as `already_committed`, with no second Effect and no second `step.committed` event.

M4.6 will be marked PASS only after Ubuntu and Windows permanent jobs succeed.

## Still required before M4 overall closeout

- [ ] M4.3 final Ubuntu + Windows permanent gate
- [ ] M4.4 final Ubuntu + Windows real-process gate
- [ ] M4.5 final Ubuntu + Windows Provider L4 gate
- [ ] M4.6 final Ubuntu + Windows cross-provider gate
- [ ] Existing full repository Ubuntu + Windows regression gate on the final M4.3-M4.6 candidate
- [ ] M4.7 >=24h continuous Soak evidence with scheduled corruption injection
- [ ] P0 = 0 and no M4-blocking P1

## Non-negotiable closure rule

`M4 CLOSED` may be written only after M4.7 has actual >=24h elapsed-time evidence. A short CI run cannot substitute for the required Soak. Provider configuration presence cannot substitute for Provider L4 certification.

Current decision: **M4 ACTIVE. M4.3-M4.6 are in final certification; final P18 closeout remains blocked by the remaining gates and M4.7.**
