# P18-M4 Adversarial Long-Horizon Certification — Closeout Through M4.6

Date: 2026-08-15
Branch: `agent/p18-m4-adversarial-certification`
M3 inherited baseline: `b532288b796f757e10a3263cc3ed110bb79258a2`
Status: **M4.3-M4.6 CLOSED — M4.7 PENDING — OVERALL M4 NOT CLOSED**

## Authority / candidate lineage

- M4 production source freeze: `eec7bf0750b384bea7784198e0e1e328d9027a94`
- M4 certification-harness freeze: `e1649eec367b66c0a5b7537fdb4a0b9a1488a085`
- M4.3-M4.6 final verified candidate: `5a1978a5ad68b5f921f2d58dbc47fc84c4cf1edc`
- final closeout workflow: `.github/workflows/p18-m4-3-6-closeout-validation.yml`
- final closeout Run: `31874103846`
- final closeout result: **4/4 SUCCESS**

The final candidate preserves the required production topology:

`Single Gateway -> Single Runtime -> Single GatewayStateStore -> existing Effect/Continuity Authority -> Zongdiaodu -> Tool/Reality`

No second Runtime, Scheduler, Store, Continuity Capsule, Total Gateway, startup entry, or parallel production tool dispatcher was introduced.

## M4.1 Deterministic 1000-Step Harness — PASS

Earlier cross-platform certification Run `31871703276`, candidate `50a3702ccdff583f845359122122e43a0e7c87a3`:

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

## M4.2 Hard Metrics — PASS

All mandatory hard metrics remained zero/false:

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

## Production defects discovered and closed during M4.3-M4.6

Certification exposed real authority defects; tests were not weakened.

### P0-1 — concurrent PREPARE canonical event instability

`step.prepared` included transient `claimed_now`, allowing two identical concurrent prepares to construct different payloads for one canonical event key. The transient field was removed from the canonical execution event.

### P0-2 — regenerative DISPATCH bypassed existing action fence

`RegenerativeExecutionAuthority._start_effect()` previously used the lower-level started-state mutation instead of the existing Gateway dispatch-permit CAS. The production seam now uses the existing action-fence permit. For the same immutable physical attempt, exactly one concurrent actor may receive dispatch permission.

### P0-3 — APPLIED reconciliation projection

The immutable first transport result remains `AMBIGUOUS`; it is not rewritten. After independently verified `APPLIED` reconciliation, Frontier, Checkpoint, Recovery and Completion now consult the reconciliation verdict and project the logical effect as committed. This preserves first-result immutability while preventing false ambiguity, blind retry and false incomplete state.

Targeted repair gate before production freeze: `20 passed`, Source Authority PASS, generated-source mirror PASS.

Final status for the M4.3-M4.6 scope:

- production P0 known/open: `0`
- M4.3-M4.6 blocking P1 known/open: `0`
- regression failures on final candidate: `0`

## M4.3 Corruption Certification — CLOSED / PASS

The complete 20/20 corruption matrix is bound to canonical production authority boundaries and passed Ubuntu + Windows:

1. model claims modification without reality change -> Completion Proof rejects
2. model claims tests passed without running tests -> Completion Proof rejects
3. ToolResult false success -> untrusted data cannot become Verified Reality
4. timeout while effect actually applied -> reconciliation required; `APPLIED` blocks replay
5. bad compact summary -> Semantic Drift forces audit/replan
6. bad checkpoint candidate -> Checkpoint Reality Audit rejects
7. stale World State -> revalidation required
8. prompt injection -> ToolResult boundary blocks authority escalation
9. fake admin/system instruction in tool output -> remains data
10. two agents target same file -> existing action-fence CAS allows one dispatch
11. provider switch attempts old side-effect replay -> logical-effect registry returns already committed
12. false fact memory promotion -> Learning Promotion Guard rejects
13. premature completion -> Completion Proof rejects
14. Ledger torn tail -> detected; only post-anchor tail can be truncated
15. checkpoint corruption -> fail closed when no known-good copy survives
16. schema upgrade -> migration + revalidation required
17. crash after PREPARED before DISPATCH -> proven not applied before a new physical attempt
18. response lost after DISPATCH -> ambiguous; blind retry blocked pending reconciliation
19. current checkpoint corrupt, previous known-good -> safe fallback
20. concurrent Ledger sequence race -> monotonic unique `ledger_seq`

Permanent M4.3 Ubuntu and Windows jobs both passed before final closeout. The final focused closeout reran this matrix together with M1-M3 inherited authority regressions.

## M4.4 Real Process Burn-in — CLOSED / PASS

Independent permanent Run `31874103829`: **Ubuntu SUCCESS + Windows SUCCESS**.

Real OS processes use the same canonical GatewayStateStore and RegenerativeExecutionAuthority; the parent process kills workers after durable checkpoints and a new process reopens the same SQLite / Request / Run / Generation.

Certified cases:

- Case A: 500-step file engineering; durable checkpoint -> process kill -> same-run recovery
- Case B: 1000-step code/edit/test/fix; intentional syntax failure -> repair -> process kill/restart
- Case C: 1000-step read-only investigation; corrupt current checkpoint -> previous-known-good fallback
- Case D: 500-step high-fault run with socket disconnect, API timeout, tool timeout, OS file lock, provider 5xx/reconnect, Ledger torn-tail corruption and process restart
- Case E: Windows-specific 1000-step kill/restart long-run
- Case F: two independent subprocesses race the same logical artifact effect; exactly one dispatch/write is permitted

Exact independent gate evidence:

- Ubuntu: `5 passed, 1 skipped in 11.82s` — Windows-only Case E skipped
- Windows: `6 passed in 15.54s`
- Source Authority PASS on both platforms
- generated-source mirror PASS on both platforms
- real-process architecture boundary PASS on both platforms

A platform-dependent test-only disconnect probe was corrected to use peer EOF (`recv() == b""`) instead of relying on post-close `send()` failure. No production source changed for that harness correction.

## M4.5 Provider L4 Certification — CLOSED / PASS

The production provider registry under certification contained five registered profiles:

- `deepseek_v4` -> `deepseek-v4-pro`
- `mimo` -> `mimo-v2.5-pro`
- `glm_5_2` -> `glm-5.2`
- `minimax_m3` -> `MiniMax-M3`
- `gpt_5_6` -> `gpt-5.6`

Each provider profile independently passed:

- L0 identity/descriptor consistency
- L1 response + tool-call normalization
- L2 stream/tool/error semantics
- L3 durable Gateway execution authority
- >=200 normalized model rounds
- >=1000 deterministic simulated tool steps
- parse coverage `1.0` (>99%)
- stream disconnect/reconnect semantics
- checkpoint rehydration
- ambiguous-effect reconciliation
- false-completion prevention
- ToolResult poisoning defense
- private reasoning leakage count = `0`
- long-horizon hard metrics clean

Aggregate certification evidence:

- provider profiles certified: `5/5`
- normalized model rounds: `>=1000`
- deterministic simulated tool steps: `>=5000`
- private reasoning leaks: `0`
- static `LONG_HORIZON_PRODUCTION_READY=true` flag: absent; no static flag substituted for certification evidence

Important scope statement: this is production adapter/runtime L4 certification against deterministic high-fidelity protocol and durable-authority harnesses. It is **not** a claim that live vendor networks, live vendor credentials, billing paths, or external service availability were exercised.

M4.5 permanent Ubuntu and Windows jobs both passed.

## M4.6 Cross-Provider Resume Final — CLOSED / PASS

Source provider family: `deepseek_v4`.

Target production provider profiles:

- `mimo`
- `glm_5_2`
- `minimax_m3`
- `gpt_5_6`

For each target, the source execution persisted:

- 300 durable structured source steps
- Verified Fact head
- Artifact Revision head
- one irreversible committed logical Effect
- structured Frontier / checkpoint / semantic handoff

Certification proved:

- unrevalidated provider/model drift cannot append `run.resumed`
- compatible + explicitly revalidated drift resumes the same Request ID
- same Run ID
- same Generation
- same Root Goal Hash
- same Task Contract Hash
- same Authority Hash
- same structured Frontier identity
- Verified Fact head retained
- Artifact Revision head retained
- completed/pending obligations retained
- provider-private reasoning is not transferred
- a pre-switch committed immutable effect remains exactly-once after target-provider resume
- repeated prepare of that effect resolves `already_committed`
- no second Effect entry
- no second `step.committed` event

M4.6 permanent Ubuntu and Windows jobs both passed.

## Final M4.3-M4.6 closeout — 4/4 SUCCESS

Final verified candidate: `5a1978a5ad68b5f921f2d58dbc47fc84c4cf1edc`
Final Run: `31874103846`

### Focused Ubuntu

- result: SUCCESS
- `158 passed`
- `1 skipped`
- `0 failed`
- `4 warnings`
- Source Authority PASS
- generated-source mirror PASS
- compile PASS
- `P18-M4.3-M4.6 FINAL architecture invariants: PASS`

### Focused Windows

- result: SUCCESS
- `159 passed`
- `0 failed`
- `4 warnings`
- Source Authority PASS
- generated-source mirror PASS
- compile PASS
- `P18-M4.3-M4.6 FINAL architecture invariants: PASS`

### Full repository Ubuntu

- result: SUCCESS
- `3064 passed`
- `36 skipped`
- `0 failed`
- `4 warnings`
- `807 subtests passed`
- Source Authority PASS
- generated-source mirror PASS

### Full repository Windows

- result: SUCCESS
- `3070 passed`
- `30 skipped`
- `0 failed`
- `4 warnings`
- `804 subtests passed`
- elapsed test time: `921.08s`
- Source Authority PASS
- generated-source mirror PASS

The four warnings are inherited Pydantic schema-shadow warnings in:

- `src/contracts/world_understanding/repository_tree.py:120`
- `src/contracts/world_understanding/repository.py:251`
- `src/contracts/world_understanding/repository_structure.py:266`
- `src/contracts/world_understanding/repository_structure.py:346`

They are inherited warnings and did not produce an M4 failure.

## Final architecture closure through M4.6

Verified on the final candidate:

- Runtime still owns `RegenerativeExecutionAuthority(runtime.store)`
- regenerative provider does not open a second GatewayStateStore
- regenerative dispatch uses existing `acquire_dispatch_permit()` action-fence CAS
- regenerative provider no longer directly calls the bypassed `mark_effect_started(effect_id...)` seam
- reconciliation projection uses canonical `latest_effect_verdict`
- exact 20-case corruption matrix exists
- Real Process harness is test-only and owns no production Runtime/Scheduler/tool dispatcher
- Provider L4 thresholds remain >=200 model rounds and >=1000 simulated tool steps per registered profile
- no static production readiness flag substitutes for evidence
- Cross-provider Final explicitly requires provider/model revalidation
- temporary repair/applicator workflows are absent from the final verified tree

## Closure decision

**P18-M4.3: CLOSED — PASS**

**P18-M4.4: CLOSED — PASS**

**P18-M4.5: CLOSED — PASS**

**P18-M4.6: CLOSED — PASS**

Therefore the requested engineering scope through **M4.6 is complete and formally sealed**.

## M4.7 remains intentionally pending

M4 overall is **NOT CLOSED**.

The remaining blocker is M4.7: a genuine `>=24h` continuous Soak with scheduled corruption injection and elapsed-time evidence. A short CI run, synthetic clock advance, repeated fast loop, or previous M4.3-M4.6 evidence cannot substitute for this requirement.

M4.7 has not been claimed, simulated, compressed, or silently marked complete in this closeout.

Current decision: **M4.3-M4.6 CLOSED. M4.7 PENDING. Overall M4 ACTIVE / NOT CLOSED.**
