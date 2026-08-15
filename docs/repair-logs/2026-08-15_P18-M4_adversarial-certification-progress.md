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

### M4.3 Corruption Certification — IN PROGRESS

Destructive persistence core is already cross-platform PASS on Run `31871703276`:

- corrupted Ledger tail is detected;
- tail is truncatable only after a known-good checkpoint anchor;
- corruption at/before checkpoint anchor fails closed;
- corrupted current checkpoint falls back to previous known-good checkpoint;
- corruption of current + previous checkpoint fails closed;
- concurrent Ledger writers preserve unique monotonic `ledger_seq`.

The full required 20-case corruption matrix is not yet marked complete. Existing M1-M3 authority tests are being reused as evidence where they already cover a case; missing cases are being added against canonical production boundaries rather than simulated as new mechanisms.

### M4.6 Cross-Provider Resume — FOUNDATION IN PROGRESS

Structured resume certification now exercises a source DeepSeek-family provider profile to a target GPT-family provider profile through the existing checkpoint/recovery authority:

- provider/model version drift cannot resume without explicit revalidation;
- blocked drift does not append `run.resumed`;
- explicitly compatible + revalidated provider/model drift must preserve Request ID, Run ID, Generation, Root Goal Hash, Task Contract Hash, Authority Hash and Frontier;
- provider-private reasoning is not transferred; only structured durable state is rehydrated;
- a logical effect committed before the provider switch is registered in the canonical logical-effect history; after target-provider resume, a repeated prepare for the same immutable intent must resolve as `already_committed` and must not create a second physical effect or second `step.committed` event.

Latest validation candidate containing the committed-effect replay proof: `60b9fa31bef4ad9d0c1e444260cdd5c8fba46782` / Run `31871964040`.

## Still required before M4 closeout

- [ ] M4.3 full 20/20 Corruption Certification
- [ ] M4.4 Real Process Burn-in: 500/1000-step real-process scenarios, kill/restart, file lock, network/SSE/provider interruptions, multi-agent same-artifact concurrency
- [ ] M4.5 Provider L4: actual provider evidence, >=200 model rounds and >=1000 simulated tool steps per certified provider; no static `LONG_HORIZON_PRODUCTION_READY=true` without evidence
- [ ] M4.6 final multi-provider / cross-provider certification closure
- [ ] Windows real-process long-run certification
- [ ] Existing full repository gate on final M4 candidate
- [ ] M4.7 >=24h continuous Soak evidence with scheduled corruption injection
- [ ] P0 = 0 and no M4-blocking P1

## Non-negotiable closure rule

`M4 CLOSED` may be written only after the final certification gates above have actual evidence. A short CI run cannot substitute for the required >=24h Soak. Provider configuration presence cannot substitute for Provider L4 certification.

Current decision: **M4 ACTIVE. Final P18 closeout remains BLOCKED pending the remaining M4 gates.**
