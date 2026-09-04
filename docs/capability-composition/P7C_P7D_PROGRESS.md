# P7C / P7D Continuous Progress and Evidence Ledger

Last updated: 2026-09-04 23:14 +08:00.

This file is the continuous status ledger for P7C.0, P7C.1, P7D.1 and P7D.2.
It separates observed baseline evidence from planned or not-yet-run evidence.
`PENDING` is never a PASS claim.

## 1. Fixed baseline

| Item | Value | Evidence status |
|---|---|---|
| Worktree | `C:\Users\77571\Documents\天工造物v3-p7d2` | Independent P7D.2 worktree observed in this work session |
| Baseline commit | `f268d6ac3293ee31e6c20b7e7f706f46cfa3e040` | Exact merged P7D.1 `main` tip from which P7D.2 was branched |
| Baseline branch relationship | P7D.2 branch is based on the merged PR #71 tip | Exact P7D.2 remote/head-match evidence is still required before this stage closes |
| Baseline focused suite | P7D.1 final remote `9/9 SUCCESS` | Immutable exact-head evidence is recorded in PR #71 |
| Store baseline | schema v32 | Observed at P7D.1 baseline source (`STORE_SCHEMA_VERSION = 32`) |
| Store candidate | schema v33 | Additive P7D.2 continuation and attempt-2 authority migration in this worktree |
| Durable P7C baseline | P19 RegistrySnapshot + VerificationPlan + activation + limited registration + executable plan + immutable authorization receipt in the existing Store | Observed at merged P7C.1 baseline source |
| Production authority baseline | one existing Gateway/Store/Policy/Ticket/Grant/Runtime/P19/Completion chain | Must remain invariant through every stage |

The P7D.1 baseline is closed only by the immutable exact-head PR #71 evidence;
P7D.2 must produce its own local and remote evidence and cannot reuse that PASS.

## 2. Current status

| Stage | State | Completed in current work | Still required |
|---|---|---|---|
| P7C.0 | `MERGED / CLOSED` | Final successor `c7b1ba1d33bf12e8e66eed1940be248b7d048adc` passed all nine checks with exact local/remote/PR/check head match; immutable evidence is recorded in PR #69; PR #69 merged as `b75d0c8aec926e18bebbf92938ded423b44a8016` | None for P7C.0; preserve the immutable PR evidence while later stages extend the same authorities |
| P7C.1 | `MERGED / CLOSED` | Final successor `e6023ba100f2b8a19331e1a0b0b46e0251533a32` passed all nine checks with exact local/remote/PR/check head match; immutable evidence is recorded in PR #70; PR #70 merged as `acb39a63bd267b5db1b9c0b7076110c5391704c8` | None for P7C.1; P7D must continue to consume the same Policy/Ticket/Grant authorities rather than minting replacements |
| P7D.1 | `MERGED / CLOSED` | Final head `4ffaf51809e1b299b574fe7c61dbf76614981c6d` passed all nine checks with exact local/remote/PR/check head match; immutable evidence is recorded in PR #71; PR #71 merged as `f268d6ac3293ee31e6c20b7e7f706f46cfa3e040` | None for P7D.1; P7D.2 continues through the same worker, Runtime, Effect/Fact and authority seams |
| P7D.2 | `LOCAL GATES PASS / REMOTE VERIFICATION PENDING` | Durable fixed-point DAG projection, exact `STEP_OUTPUT`, explicit result/value schemas, inert restart continuation with one bounded pre-start successor, scoped recovery, plural P19 readiness/dispositions, readiness/decision write fencing, stable Life recovery and parent-plus-leaves Completion are implemented in the independent P7D.2 worktree. The final focused set is `353 passed`; the final full source verifier and Python regression passed; P19, P14, Node, freeze/fingerprint, official generation, refreshed source-release manifests, mirrors and Source Authority pass | Push one unchanged head for all nine GitHub checks, record immutable exact-head evidence, and merge only after all nine pass |

## 3. P7C.0 implementation checklist

- [x] Explain why v30 Plan ID/hash and registration JSON cannot reconstruct an
  invocation.
- [x] Define the companion `ExecutableCompositionPlanV1` authority boundary.
- [x] Define `LiteralValueBindingV1`, `PlanInputValueBindingV1`,
  `StepOutputValueBindingV1`, `StepExecutionBindingV1` and
  `WorkspaceBindingV1` roles.
- [x] Freeze dynamic `STEP_OUTPUT` fail-closed semantics.
- [x] Specify additive schema v31 in the existing `GatewayStateStore`.
- [x] Specify one atomic P19 + registration + executable-plan UoW.
- [x] Specify legacy v30 no-backfill/non-executable behavior.
- [x] State that P7C.0 does not authorize, issue, grant, execute, verify or
  complete.
- [x] Implement strict contracts and canonical identities; adversarial review
  found and closed the raw-plan sink bypass, post-seal companion mutation or
  deletion, Store-wide object delete/rebind, and incomplete marker-`1` P7B
  companion validation. Independent post-fix and final compliance reviews found
  no remaining P0-P2 product defect.
- [x] Implement the deterministic compiler and authoritative rebuild.
- [x] Implement v31 migration, strict companion table and one inseparable
  authoritative compile-and-insert bundle path; expose no raw-plan sink/token.
- [x] Extend health/integrity, active lookup, expiry and restart recovery.
- [x] Add exact idempotency, race, rollback and collision handling.
- [x] Add focused positive, migration, tamper and fail-closed tests.
- [x] Generated-source regeneration, working-tree check and committed mirror
  check pass on the exact staged file set.
- [x] Rerun the complete local regression evidence after the security fix.
- [x] Push reviewed code candidate SHA
  `439b018fe807c32bba625998272e4021b230111c`.
- [x] Pass the first all-nine GitHub check round on that exact code SHA.
- [x] Pass all nine required GitHub checks again on evidence-only successor
  `c7b1ba1d33bf12e8e66eed1940be248b7d048adc`.
- [x] Record final local/remote/PR/check head-match proof in the immutable PR #69
  comment and merge that exact head as
  `b75d0c8aec926e18bebbf92938ded423b44a8016`.

## 4. Mandatory P7C.0 counterexample matrix

The following rows were executed against P7C.0 code candidate
`439b018fe807c32bba625998272e4021b230111c`. Any subsequent contract or Store
change resets the affected rows to `PENDING`; this evidence-only ledger update
does not change the tested product/test tree.

| Counterexample / invariant | Required result | Command or run | SHA | Status |
|---|---|---|---|---|
| Exact companion compile is deterministic | Same authoritative inputs yield byte-identical ID/hash/body | `test_canonical_full_arguments_and_typed_step_output_roundtrip` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| Caller changes legacy Plan/body and rehashes | Rejected by authoritative rebuild | `test_rehashed_caller_modified_legacy_plan_is_rejected_by_recompile` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| Missing/extra/reordered step binding | Rejected | `test_step_binding_cardinality_and_order_are_exact` (3 cases) | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| Action ID/version/schema/source/permission-manifest drift | Rejected | `test_public_bundle_rejects_rehashed_candidate_and_schema_drift` (6 cases) + `test_step_permission_source_manifest_must_match_plan_manifest` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| A1+, declared write/execute effect, `external_send`/`destructive` side effects, shell/python | Rejected at first-batch boundary | `test_current_registry_materialization_rejects_non_a0_read_verify` (5 cases) + `test_step_permission_is_self_contained_a0_read_verify` (2) + `test_step_permission_external_send_and_destructive_fail_closed` (2) + `test_current_primitive_external_send_and_destructive_fail_closed` (2) + `test_unknown_primitive_side_effect_is_rejected_fail_closed` + `test_public_bundle_revalidates_model_copy_bypassed_permission` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| `STEP_OUTPUT` self/future/undeclared/missing-edge/cyclic source | Rejected | `test_step_output_self_and_unknown_declaration_fail_closed` + `test_step_output_requires_an_explicit_dependency_edge` + `test_step_output_from_a_future_step_is_rejected` + `test_cyclic_proposal_is_rejected_before_executable_materialization` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| `STEP_OUTPUT` pointer/binding discriminant/destination overlap or duplicate invalid | Rejected | `test_step_output_reference_is_hash_bound_and_fails_closed` + `test_step_output_result_selector_requires_rfc6901_pointer` + `test_step_output_binding_rejects_discriminant_type_confusion` + `test_argument_destination_json_pointers_must_not_overlap` + `test_argument_destination_json_pointers_must_be_unique` + `test_array_pointer_indices_require_ascii_digits` + `test_unresolved_result_pointer_rejects_ambiguous_array_tokens` + `test_target_slot_must_resolve_statically_to_a_string` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| Failure during any P19/registration/plan/owner insert or marker update | Whole existing-Store UoW rolls back | `test_each_bundle_insert_failure_rolls_back_the_whole_uow` (5 tables) + `test_object_owner_insert_failure_rolls_back_the_whole_uow` + `test_parent_marker_failure_rolls_back_the_whole_uow` + `test_late_plan_insert_failure_rolls_back_request_owner_and_bundle` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| Exact retry / concurrent exact retry | One durable group; both readers converge | `test_exact_replay_preserves_first_plan_and_registration_rows` + `test_exact_replay_compares_canonical_json_not_python_sequence_shape` + `test_two_store_connections_converge_on_one_atomic_executable_bundle` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| Same registration identity with different companion material | Explicit collision/conflict; original row remains | `test_registration_identity_rejects_different_companion_material` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| Persisted companion is replaced, changed with fully recomputed hashes, or deleted | Schema-fingerprinted append-only guards reject all three operations; original row and healthy Store remain | `test_persisted_executable_plan_rejects_replace_rehashed_update_and_delete` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| New companion changes all five stable identities but reuses one Request/Run/Generation lineage | Plain INSERT, `INSERT OR REPLACE` and `ON CONFLICT ... DO NOTHING` all fail at the identity trigger; original row and healthy Store remain | `test_plan_lineage_identity_guard_rejects_insert_replace_and_upsert_without_replacing_original` (3 cases) | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| v30 database opens under v31 | Additive migration; old row readable but non-executable | `test_v30_audit_registration_migrates_additively_but_has_no_companion` + `test_v30_to_v31_concurrent_open_serializes_migration` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| Old v30 registration has no companion | No executable companion is synthesized; lookup/recovery omit it and backfill is rejected | `test_v30_audit_registration_migrates_additively_but_has_no_companion` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| v31-created registration loses/crosses/malforms companion | All applicable P7B/P7C historical/active/recovery/expiry/replay paths and health fail closed | `test_required_companion_loss_is_explicit_corruption` + `test_integrity_scan_detects_executable_column_and_json_tampering` + `test_crossed_companion_body_is_explicit_corruption` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| Same `object_id` is replaced/deleted/rebound to another hash or given a noncanonical REQUEST owner; one-plan metadata/scope drifts | Public owner API, cross-request bundle and direct SQL insert/replace/update/delete/UPSERT reject; mutated owner REPLACE/UPSERT preserve the original row; legacy divergence blocks open; same-hash cross-request reuse remains valid | `test_object_input_matches_inbound_attachment_and_gets_request_owner` + `test_public_owner_api_cannot_rebind_executable_object_identity` + `test_public_owner_api_rejects_invalid_owner_kind_before_transaction` + `test_two_bundles_cannot_bind_one_object_id_to_different_content` + `test_two_requests_may_share_one_object_id_only_for_the_same_content` + `test_object_inputs_can_pin_two_revisions_of_one_accepted_object` + `test_same_object_id_cannot_claim_different_revision_authority` (6 fields) + `test_v31_health_rejects_legacy_object_identity_hash_divergence` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| P7B operational companion validation scales with selected rows | Active lookup reads one registration-scoped companion; recovery/expiry verify each of two selected rows exactly once; no executable-plan full scan | `test_active_lookup_reads_one_registration_scoped_companion_without_full_scan` + `test_p7b_recovery_and_expiry_verify_each_selected_companion_once` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| Expired/released request generation | Active executable lookup rejects | `test_restart_recovers_companion_then_expiry_preserves_audit_body` + `test_active_lookup_fails_closed_after_generation_drift` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| Raw executable-plan writer/token exposure | No raw-plan sink/token exists; all writes recompile in the public bundle | `test_gateway_store_exposes_no_raw_executable_plan_write_sink` + `test_public_bundle_rejects_rehashed_candidate_and_schema_drift` + `test_public_bundle_revalidates_model_copy_bypassed_permission` + `test_rehashed_caller_modified_legacy_plan_is_rejected_by_recompile` | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |
| P7C.0 import/call surface | Compiler/codec and bundle method contain no Policy/Ticket/Grant/Runtime/Effect/Fact/P19 execution/Completion authority; qualified calls, exact SQL/helper closure and unique runtime bindings reject dynamic/rebind/decorator/nested-scope bypasses | compiler/codec structural test + `test_register_executable_bundle_has_no_policy_ticket_grant_effect_fact_or_runtime_authority` + `test_register_executable_bundle_authority_guard_rejects_mutants` (24 cases) + safe-layout positive control | `439b018fe807c32bba625998272e4021b230111c` | `PASS` |

## 5. Future-stage evidence checkpoints

### P7C.1

- [x] Only exact active v31 registration + companion enters authorization, and
  the v32 receipt is inserted only after the transaction rechecks current state.
- [x] Current Action Registry and effective A0 permission are rechecked at the
  immediate authorization/issuance boundary.
- [x] Resolved arguments, workspace, target state, request/run/generation,
  executable-plan hash, step and Action/version are bound into existing
  Policy/Ticket/Grant artifacts.
- [x] Expiry, durable-receipt replay mismatch, argument swap, target swap, plan
  swap and generation swap fail closed at issuance and restore. Nonce
  consumption remains the P7D dispatch boundary.
- [x] No alternate Policy, Ticket, grant, Runtime, Effect or Fact authority was
  introduced.
- [x] Focused, adversarial, generated-source, P14, P19, Node and full local
  evidence is recorded on code candidate
  `9e744d0b2185f0b6e4abca0981daa62dc9494a7c`.
- [x] The first nine-check remote round passed on that unchanged code candidate.
- [x] Pass all nine remote checks again on the evidence-only successor SHA that
  is intended to merge.
- [x] Record exact local/remote/PR/check head match for that successor SHA and
  merge PR #70 as `acb39a63bd267b5db1b9c0b7076110c5391704c8`.

### P7D.1

- [x] Existing `GatewayOrchestrationWorker` is the only scheduler.
- [x] Existing worker dispatch seam and canonical Gateway Effect/Fact
  authorities own durable progress; `ExecutionEngine` is not introduced as a
  production scheduler/outcome ledger.
- [x] A0 read/verify goes through existing `BackendClient` → Omni Body →
  `BodyRuntime` using exact Ticket/Grant bindings.
- [x] Pre-dispatch rejection invokes no handler and consumes no handler nonce.
- [x] Timeout/error after the execution boundary becomes `AMBIGUOUS` or
  reconcile-required and is not replayed blindly.
- [x] Single-step restart cut points prove no duplicate handler call; exact Fact
  recovers terminal Effect, while missing Fact closes unknown work
  `AMBIGUOUS`.
- [x] P7D.1 cannot claim production completion before P7D.2.
- [x] Focused, adversarial, generated-source, P14, P19 and Node local evidence
  is recorded on the current pre-commit candidate tree.
- [x] Full Python final rerun is recorded on the current pre-commit candidate
  tree.
- [x] The first nine-check remote round and exact head match are recorded on
  candidate `c327851a1e18deb7c602673c3f3f87c6afa785f7`.
- [x] Nine remote checks and exact head-match are recorded on final successor
  `4ffaf51809e1b299b574fe7c61dbf76614981c6d`; immutable evidence is in PR #71,
  merged as `f268d6ac3293ee31e6c20b7e7f706f46cfa3e040`.

### P7D.2

- [x] Durable topological scheduling unlocks a step only after every dependency
  has an authoritative successful Effect head and exact Gateway fact.
- [x] `STEP_OUTPUT` resolves only from the exact upstream fact and its resolved
  arguments hash is persisted before dispatch.
- [x] Crash windows before/after claim, started boundary, handler, Fact write,
  Effect completion, frontier/checkpoint and P19 are covered.
- [x] Fact/Effect disagreement, ambiguous outcomes and stale generations enter
  reconciliation; no duplicate side effect is possible.
- [x] P19 uses the exact active Plan/Registry/subjects and derives readiness
  through the existing readiness authority.
- [x] Failed/inconclusive/error verification cannot complete; A0 rollout cannot
  dispatch an A1+ repair.
- [x] Existing `CompletionGate` checks every required leaf Effect/fact and is the
  only completed-status source.
- [x] No external send/delivery and no A1+ Action is enabled in the first batch.
- [x] All required local evidence, including the final full Python gate, is recorded.
- [ ] Nine remote checks and exact head-match are recorded.

## 6. Local evidence before remote CI

Populate one row per actual run. Do not replace a missing value with an
assumption.

| Stage | Evidence kind | Exact command | Platform/runtime | Result | Test count | Candidate SHA | Timestamp/run artifact |
|---|---|---|---|---|---|---|---|
| Baseline | Supplied focused baseline | Not attached | Not attached | `75 passed` (supplied, not rerun here) | 75 | `14f6946a9d994e70654b9d64ecfcaae9c74baba4` | Not attached |
| P7C.0 | Focused contract/compiler + migration/restart + tamper/rollback/race | `python -m pytest tests/test_composition_executable_plan_p7c0.py tests/test_composition_activation_store_p7b2.py -q` | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 141 passed | `439b018fe807c32bba625998272e4021b230111c` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | P4→P7 selected regression | `python -m pytest tests/test_capability_composition_contracts.py tests/test_capability_composition_p4.py tests/test_capability_composition_p4_hardening.py tests/test_capability_composition_p4_cross_phase.py tests/test_composition_activation_shadow_p7a.py tests/test_composition_activation_registration_p7b.py tests/test_composition_activation_store_p7b2.py tests/test_composition_executable_plan_p7c0.py -q` | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 188 passed | `439b018fe807c32bba625998272e4021b230111c` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | P19 Store/write-evidence/golden/fault selected regression | `python -m pytest tests/test_p19_m1_store.py tests/test_p19_m3_write_evidence_v2.py tests/golden/p19_r2/test_golden_trace.py tests/golden/p19_r2/test_fault_matrix.py -q` | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 64 passed | `439b018fe807c32bba625998272e4021b230111c` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | P14 focused regression | Workflow-equivalent 12-file P14 selection recorded by this ledger's work session | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 109 passed | `439b018fe807c32bba625998272e4021b230111c` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | P3→P13 boundary regression | Five-file P3/P13 selection recorded by this ledger's work session | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 151 passed | `439b018fe807c32bba625998272e4021b230111c` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | Existing architecture/store regression | Twelve-file Gateway/Memory/P15/P17/settings selection recorded by this ledger's work session | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 124 passed | `439b018fe807c32bba625998272e4021b230111c` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | Full Python regression | Set current-worktree `src`, backend and readable-source roots in `PYTHONPATH`; run original embedded `python.exe -m pytest -q --maxfail=1 tests` | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 3800 passed, 17 skipped, 824 subtests passed | `439b018fe807c32bba625998272e4021b230111c` | 586.31 s; completed before ledger refresh 2026-09-04 09:18 +08:00 |
| P7C.0 | Full Node regression | `$NodeTests = @(Get-ChildItem tests -Filter '*.test.mjs' -File \| Sort-Object FullName \| ForEach-Object FullName); node --test @NodeTests` | Windows / Node v24.14.0 | `PASS` | 224 passed, 2 skipped, 0 failed in 29 files | `439b018fe807c32bba625998272e4021b230111c` | 1976.58 ms; completed before ledger refresh 2026-09-04 09:18 +08:00 |
| P7C.0 | Generated-source mirrors | `python scripts/sync-generated-sources.py --check`; after staging: `python scripts/sync-generated-sources.py --check-committed` | Windows / embedded Python 3.12 | `PASS` | 2 passed | `439b018fe807c32bba625998272e4021b230111c` | Both checks passed on the exact 16-file staged set; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | Source Authority | `python scripts/check-source-authority.py` | Windows / embedded Python 3.12 | `PASS` | 16 independent authorities, 1 alias, 24 generated targets, 1 closed-world boundary | `439b018fe807c32bba625998272e4021b230111c` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | P19 freeze + drift fingerprint | `python -m pytest -q tests/golden/p19_r2/test_freeze_and_guards.py tests/golden/p19_r2/test_calibration_and_stability.py::DriftFingerprintTests::test_fingerprint_matches_or_declared` | Windows / embedded Python 3.12 | `PASS` | 7 passed | `439b018fe807c32bba625998272e4021b230111c` | Regenerated from final product source and verified; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | Portable paths + source syntax + forbidden artifacts | Embedded Python portable-path audit; `node --check` over app JS/MJS; `ast.parse` over src/tests Python; repository file-name scan | Windows / embedded Python 3.12 + Node v24.14.0 | `PASS` | 1953 portable files; 102 JS; 808 Python; 2421 source files artifact-scanned | `439b018fe807c32bba625998272e4021b230111c` | Exact `check.ps1` steps run with the main checkout's same embedded runtime because ignored `app/runtime` is absent from this independent worktree; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.1 | Focused contracts, Store, adapter and security counterexamples | `python -m pytest tests/test_action_schema_catalog_p7c1.py tests/test_composition_execution_binding_p7c1.py tests/test_composition_step_authorization_store_p7c1.py tests/test_composition_activation_adapter_p7c1.py tests/test_composition_grant_authority_p7c1.py -q` | Windows / Python 3.12.10, current-worktree sources | `PASS` | 92 passed, 23 subtests passed | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | 18.33 s; completed in this work session before the 2026-09-04 11:56 +08:00 candidate commit |
| P7C.1 | Generated/release/P19 selected regression | `python -m pytest -q tests/golden/p19_r2/test_calibration_and_stability.py tests/golden/p19_r2/test_freeze_and_guards.py tests/test_cross_platform_source_20.py tests/test_foundation_closeout.py tests/test_release_manifest.py tests/test_life_cutover_p11.py tests/test_single_process_application.py tests/test_source_authority_p17_m1.py` | Windows / Python 3.12.10, final official generated tree | `PASS` | 146 passed, 1 skipped, 26 subtests passed | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | 64.91 s; completed in this work session before the candidate commit |
| P7C.1 | Full Python regression | `python -m pytest -q` | Windows / Python 3.12.10, current-worktree sources | `PASS` | 3893 passed, 17 skipped, 847 subtests passed | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | 639.16 s; completed in this work session before the candidate commit |
| P7C.1 | Full Node regression | `$NodeTests = @(Get-ChildItem -LiteralPath tests -Filter '*.test.mjs' -File \| Sort-Object FullName \| ForEach-Object { $_.FullName }); node --test @NodeTests` | Windows / Node v24.14.0; locked `app/package-lock.json` dependencies | `PASS` | 224 passed, 2 skipped, 0 failed | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | 1965.55 ms; completed in this work session before the candidate commit |
| P7C.1 | P14 focused regression | `python -m pytest -q tests/test_repository_structure_p14_m2.py tests/test_repository_structure_p14_m2_wiring.py tests/test_repository_query_p14_m3.py tests/test_repository_query_p14_m3_coherence.py tests/test_repository_query_p14_m3_wiring.py tests/test_repository_context_p14_m4.py tests/test_repository_context_p14_m4_wiring.py tests/test_repository_incremental_p14_m5.py tests/test_repository_security_p14.py tests/test_life_repository_bridge_p14.py tests/test_reflection_capability_p8.py tests/test_life_capability_health_flow.py` | Windows / Python 3.12.10 | `PASS` | 109 passed | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | 15.93 s; completed in this work session before the candidate commit |
| P7C.1 | P3-P13 boundary regression | `python -m pytest -q tests/test_world_understanding_p3_sources_life_isolation.py tests/test_world_understanding_p13_counterexamples.py tests/test_world_understanding_p13_full_chain.py tests/test_world_understanding_p13_failure_recovery.py tests/test_world_understanding_p13_stress_performance.py` | Windows / Python 3.12.10 | `PASS` | 151 passed | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | 2.81 s; completed in this work session before the candidate commit |
| P7C.1 | P19 Golden Gate | `python -m pytest tests/golden/p19_r2/ -q` | Windows / Python 3.12.10 | `PASS` | 55 passed | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | 38.08 s; completed in this work session before the candidate commit |
| P7C.1 | P19/verification/repair selection | `python -m pytest tests/ -k "p19 or verification or repair" -q` | Windows / Python 3.12.10 | `PASS` | 316 passed, 3594 deselected | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | 76.66 s; completed in this work session before the candidate commit |
| P7C.1 | Official generation, mirrors and Source Authority | `python scripts/sync-generated-sources.py --check`; `python scripts/sync-generated-sources.py --check-committed`; `python scripts/sync_omni_capability_manifest.py --check`; `python scripts/check-source-authority.py`; `git diff --check` | Windows / Python 3.12.10 + Git | `PASS` | 16 independent authorities, 1 alias, 24 generated targets, 1 closed-world boundary; both mirror modes and manifest check passed | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | Final staged-tree checks completed in this work session before the candidate commit |
| P7D.1 | Runtime/Effect/Fact/restart/timeout focused regression | `python -m pytest -q tests/test_composition_step_execution_p7d1.py tests/test_composition_backend_transport_p7d1.py tests/test_composition_execution_manifest_p7d1.py tests/test_gateway_worker_composition_integration_p7d1.py tests/test_gateway_worker_composition_recovery_p7d1.py tests/test_effect_fact_chain_v14.py tests/test_execution_contract_epoch.py tests/test_p18_m4_authority_repairs.py tests/test_effect_ledger.py` | Windows / Python 3.12.10, current-worktree sources | `PASS` | 97 passed | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | 67.27 s; rerun after timeout-permit repair |
| P7D.1 | P19 Golden Gate | `python -m pytest tests/golden/p19_r2/ -q` | Windows / Python 3.12.10, refreshed 1.4 freeze/fingerprint | `PASS` | 55 passed | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | 38.48 s; rerun after final watchdog compatibility fix |
| P7D.1 | P19/verification/repair selection | `python -m pytest tests/ -k "p19 or verification or repair" -q` | Windows / Python 3.12.10 | `PASS` | 316 passed, 3670 deselected | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | 77.42 s |
| P7D.1 | P14 focused regression | `python -m pytest -q tests/test_repository_structure_p14_m2.py tests/test_repository_structure_p14_m2_wiring.py tests/test_repository_query_p14_m3.py tests/test_repository_query_p14_m3_coherence.py tests/test_repository_query_p14_m3_wiring.py tests/test_repository_context_p14_m4.py tests/test_repository_context_p14_m4_wiring.py tests/test_repository_incremental_p14_m5.py tests/test_repository_security_p14.py tests/test_life_repository_bridge_p14.py tests/test_reflection_capability_p8.py tests/test_life_capability_health_flow.py` | Windows / Python 3.12.10 | `PASS` | 109 passed | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | 13.98 s; rerun after final product changes |
| P7D.1 | Full Node regression | `$NodeTests = @(Get-ChildItem -LiteralPath tests -Filter '*.test.mjs' -File \| Sort-Object FullName \| ForEach-Object { $_.FullName }); node --test @NodeTests` | Windows / Node v24.14.0 | `PASS` | 224 passed, 2 skipped, 0 failed in 29 files | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | 2011.00 ms Node duration; worktree byte-equivalent before/after |
| P7D.1 | Official generation, manifests and Source Authority | `python scripts/sync-generated-sources.py --write`; `python scripts/sync-generated-sources.py --check`; `python scripts/sync_omni_capability_manifest.py --check`; `python scripts/check-source-authority.py`; `git diff --check` | Windows / Python 3.12.10 + Git | `PASS` | 19 managed novel Actions, 790 total, 290 executable; 16 independent authorities, 1 alias, 24 generated targets, 1 closed-world boundary | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | Final official write/check completed after product fixes |
| P7D.1 | Full Python regression | `python -m pytest -q` | Windows / Python 3.12.10, current-worktree sources | `PASS` | 3969 passed, 17 skipped, 847 subtests passed | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | 709.48 s; final clean rerun after all product/test fixes |
| P7D.1 remediation | P7D runtime + evidence concurrency focused regression | `python -m pytest -q tests/test_composition_step_execution_p7d1.py tests/test_composition_backend_transport_p7d1.py tests/test_composition_execution_manifest_p7d1.py tests/test_gateway_worker_composition_integration_p7d1.py tests/test_gateway_worker_composition_recovery_p7d1.py tests/test_effect_fact_chain_v14.py tests/test_execution_contract_epoch.py tests/test_p18_m4_authority_repairs.py tests/test_effect_ledger.py tests/test_policy_evidence_concurrency.py` | Windows / Python 3.12.10, exact product repair commit | `PASS` | 99 passed | `24937b440746ec90b4e0000723280bef0e88f49e` | 77.70 s; includes deterministic two-ledger no-clobber race and publication-failure cleanup |
| P7D.1 remediation | P7C.1 authority + evidence concurrency regression | `python -m pytest -q tests/test_policy_evidence_concurrency.py tests/test_composition_grant_authority_p7c1.py` | Windows / Python 3.12.10, repaired product/test tree | `PASS` | 30 passed | `24937b440746ec90b4e0000723280bef0e88f49e` | 13.45 s; original two-authority race also passed 20/20 isolated repetitions |
| P7D.1 remediation | Full Python regression after official mirror sync | `python -m pytest -q` | Windows / Python 3.12.10, repaired product/test tree | `PASS` | 3971 passed, 17 skipped, 847 subtests passed | `24937b440746ec90b4e0000723280bef0e88f49e` | 723.92 s; 8 existing warnings |
| P7D.1 remediation | P19 Golden Gate | `python -m pytest tests/golden/p19_r2/ -q` | Windows / Python 3.12.10 | `PASS` | 55 passed | `24937b440746ec90b4e0000723280bef0e88f49e` | 39.40 s |
| P7D.1 remediation | P19/verification/repair selection | `python -m pytest tests/ -k "p19 or verification or repair" -q` | Windows / Python 3.12.10 | `PASS` | 316 passed, 3672 deselected | `24937b440746ec90b4e0000723280bef0e88f49e` | 77.75 s |
| P7D.1 remediation | P14 focused regression | Same 12-file P14 selection recorded above | Windows / Python 3.12.10 | `PASS` | 109 passed | `24937b440746ec90b4e0000723280bef0e88f49e` | 14.53 s |
| P7D.1 remediation | Full Node regression | `$NodeTests = @(Get-ChildItem -LiteralPath tests -Filter '*.test.mjs' -File \| Sort-Object FullName \| ForEach-Object { $_.FullName }); node --test @NodeTests` | Windows / Node v24.14.0 | `PASS` | 224 passed, 2 skipped, 0 failed in 29 files | `24937b440746ec90b4e0000723280bef0e88f49e` | 1960.69 ms; repaired transport wait helper also passed 50/50 isolated stress runs |
| P7D.1 remediation | Official generation, manifests and Source Authority | `python scripts/sync-generated-sources.py --write`; both mirror checks; Omni manifest check; `python scripts/check-source-authority.py`; `git diff --check` | Windows / Python 3.12.10 + Git | `PASS` | 19 managed novel Actions, 790 total, 290 executable; 16 independent authorities, 1 alias, 24 generated targets, 1 closed-world boundary | `24937b440746ec90b4e0000723280bef0e88f49e` | Official write followed by final check/check-committed and source-authority PASS |
| P7D.2 | DAG/`STEP_OUTPUT`/reconcile/P19/Completion focused regression | `$P7D2Tests = @(Get-ChildItem tests -Filter '*p7d2.py' -File \| Sort-Object FullName \| ForEach-Object FullName); python -m pytest -q @P7D2Tests tests/test_action_schema_catalog_p7c1.py tests/test_composition_executable_plan_p7c0.py tests/test_composition_step_execution_p7d1.py tests/test_gateway_worker_composition_integration_p7d1.py tests/test_gateway_worker_composition_recovery_p7d1.py tests/test_watchdog_stale_effect_regression.py tests/test_p19_m5_repair_loop.py tests/test_active_request_activation.py tests/test_gateway_life_continuity.py tests/test_life_journal_projection_recovery.py` | Windows / Python 3.12.10, current-worktree sources | `PASS` | 353 passed | Pre-commit P7D.2 candidate tree; final SHA will be bound in immutable PR evidence | 227.50 s; final rerun completed 2026-09-04 after the readiness seal repair |
| P7D.2 | Official generation, release manifests, mirrors and Source Authority | `python scripts/sync_omni_capability_manifest.py`; `python scripts/sync-generated-sources.py --write`; `python scripts/refresh-source-release.py`; `python scripts/sync_omni_capability_manifest.py --check`; `python scripts/sync-generated-sources.py --check`; `python scripts/check-source-authority.py` | Windows / Python 3.12.10 + Git | `PASS` | 19 managed novel Actions, 790 total, 290 executable; 16 independent authorities, 1 alias, 24 generated targets, 1 closed-world boundary; capability manifest `0971fd04f760d4b491361fa3526b17d092c561ce224fb7f9b10446e0bcd5999d`; contract schema bundle `39b15ea6c5ab6403c8b135b9db7f5f6c4abc9bf919426aa1405892511d77647b`; component manifest `4406a102397336564995a3c8800963324873f4e3f80849c6700d5b2dbe4341dd`; release manifest `4e85ddbe52aedd39f1b60e4736f1008bed4bbbbdc1f64ac42dc17f3627829ebc`; all three release-manifest copies byte-identical with file SHA-256 `d25c8a14eb31efffd21926ebeeafcd6b97268a778eced41f7b7e471ba0063abd` | Pre-commit P7D.2 candidate tree; final SHA will be bound in immutable PR evidence | Official refresh/check completed after the final capability and contract-schema changes |
| P7D.2 | P19 1.5 freeze + drift fingerprint | `python -m pytest -q tests/golden/p19_r2/test_freeze_and_guards.py tests/golden/p19_r2/test_calibration_and_stability.py::DriftFingerprintTests::test_fingerprint_matches_or_declared` | Windows / Python 3.12.10 | `PASS` | 7 passed | Pre-commit P7D.2 candidate tree; final SHA will be bound in immutable PR evidence | 7.44 s compare-only run after refreshing the 38-file authority surface |
| P7D.2 | P19 Golden Gate | `python -m pytest tests/golden/p19_r2/ -q` | Windows / Python 3.12.10 | `PASS` | 55 passed | Pre-commit P7D.2 candidate tree; final SHA will be bound in immutable PR evidence | 51.44 s; final stable-tree rerun |
| P7D.2 | P19/verification/repair selection | `python -m pytest tests/ -k "p19 or verification or repair" -q` | Windows / Python 3.12.10 | `PASS` | 324 passed, 3768 deselected | Pre-commit P7D.2 candidate tree; final SHA will be bound in immutable PR evidence | 101.05 s; final stable-tree rerun |
| P7D.2 | P14 focused regression | `python -m pytest -q tests/test_repository_structure_p14_m2.py tests/test_repository_structure_p14_m2_wiring.py tests/test_repository_query_p14_m3.py tests/test_repository_query_p14_m3_coherence.py tests/test_repository_query_p14_m3_wiring.py tests/test_repository_context_p14_m4.py tests/test_repository_context_p14_m4_wiring.py tests/test_repository_incremental_p14_m5.py tests/test_repository_security_p14.py tests/test_life_repository_bridge_p14.py tests/test_reflection_capability_p8.py tests/test_life_capability_health_flow.py` | Windows / Python 3.12.10 | `PASS` | 109 passed | Pre-commit P7D.2 candidate tree; final SHA will be bound in immutable PR evidence | 15.94 s; final stable-tree rerun |
| P7D.2 | Full Node regression | `$NodeTests = @(Get-ChildItem -LiteralPath tests -Filter '*.test.mjs' -File \| Sort-Object FullName \| ForEach-Object { $_.FullName }); node --test @NodeTests` | Windows / Node v24.14.0; existing locked dependencies | `PASS` | 224 passed, 2 skipped, 0 failed in 29 files | Pre-commit P7D.2 candidate tree; final SHA will be bound in immutable PR evidence | 1980.7034 ms Node duration; final stable-tree run |
| P7D.2 | Full source verification + Python regression | `python scripts/verify_source.py` | Windows / Python 3.12.10, current-worktree sources | `PASS` | 4075 passed, 17 skipped, 847 subtests passed | Pre-commit P7D.2 candidate tree; final SHA will be bound in immutable PR evidence | 907.82 s; final unchanged-tree run completed 2026-09-04 23:14 +08:00 |

An earlier discovered Node full-suite run had one timeout before the `cancel`
assertions in the pre-existing `test_avatar_p2b_transport.test.mjs` readiness
helper. The same unchanged test failed on baseline `main`; repeated isolation
was then 20/20 green, and a subsequent discovered full suite passed. During the
P7D.1 remediation gate, the canonical suite hit the same helper in `cancel`,
then an isolated rerun hit the adjacent backpressure test at the same initial
wait. The helper comment prohibited fixed-turn timing assumptions, but its code
still exhausted 200 `setImmediate` turns before real file I/O was guaranteed to
complete. Repair `24937b440746ec90b4e0000723280bef0e88f49e` replaces that
non-contract turn count with the file's existing two-second wall-clock pattern;
all protocol assertions remain unchanged. The file passed 28/28, isolated
stress passed 50/50, and the canonical suite passed 224/2/0 afterward. The two
failed discovery runs are not counted as PASS.

The first P7C.1 Node discovery run was executed before this independent
worktree had installed the locked `app/package-lock.json` dependencies. Six
avatar files failed to import `three`, and one pre-existing readiness wait timed
out while four Python jobs were running concurrently. That run is not counted
as PASS. After `npm ci --ignore-scripts --no-audit --no-fund`, the unchanged
Node suite was rerun without concurrent Python load and completed with 224
passed, 2 skipped and 0 failed.

The first P7D.1 full Python run is not counted as PASS: it exposed one missing
reviewed-lineage successor for the deliberately changed authorization contract
and two legacy watchdog-test doubles without a production `claim` field. The
minimal lineage and compatibility fixes passed a 14-test targeted rerun. The
subsequent complete unchanged-tree run is the 3969-pass result recorded above.

The first remediation full Python run is also not counted as PASS: it was run
before the mandatory generated-source write and correctly reported only the
authoritative `policy_evidence.py` mirror and marker as stale (3970 passed, two
source-authority failures). After the official write/check, both exact failures
passed and the complete unchanged-tree rerun passed 3971 tests.

The evidence-only successor `331e675a7e159493d78fc4242612a1bdb8aed41f`
is not a final PASS. Eight checks succeeded, but Windows full regression failed
when two independent policy-evidence ledgers concurrently published the same
digest path and Windows rejected the second `os.replace` with `WinError 5`.
Repair `24937b440746ec90b4e0000723280bef0e88f49e` uses atomic no-clobber
publication, validates the winning bytes and cleans temporary files. Independent
review found no P0/P1 defect in that repair or in the bounded Node wait repair.

## 7. Nine required GitHub checks per stage

Focused/migration/tamper tests and local sync/check evidence must be green
before a candidate enters remote CI. Then each stage must pass these exact nine
checks against one unchanged candidate SHA.

| Required GitHub check | P7C.0 | P7C.1 | P7D.1 | P7D.2 |
|---|---|---|---|---|
| `source-authority-ubuntu-latest` | `FINAL PASS @ c7b1ba1…` | `FINAL PASS @ e6023ba…` | `FINAL PASS @ 4ffaf518…` | `PENDING` |
| `source-authority-windows-latest` | `FINAL PASS @ c7b1ba1…` | `FINAL PASS @ e6023ba…` | `FINAL PASS @ 4ffaf518…` | `PENDING` |
| `full-regression-ubuntu-latest` | `FINAL PASS @ c7b1ba1…` | `FINAL PASS @ e6023ba…` | `FINAL PASS @ 4ffaf518…` | `PENDING` |
| `full-regression-windows-latest` | `FINAL PASS @ c7b1ba1…` | `FINAL PASS @ e6023ba…` | `FINAL PASS @ 4ffaf518…` | `PENDING` |
| `p14-focused-ubuntu-latest` | `FINAL PASS @ c7b1ba1…` | `FINAL PASS @ e6023ba…` | `FINAL PASS @ 4ffaf518…` | `PENDING` |
| `p14-focused-windows-latest` | `FINAL PASS @ c7b1ba1…` | `FINAL PASS @ e6023ba…` | `FINAL PASS @ 4ffaf518…` | `PENDING` |
| `full-regression-ubuntu` | `FINAL PASS @ c7b1ba1…` | `FINAL PASS @ e6023ba…` | `FINAL PASS @ 4ffaf518…` | `PENDING` |
| `p19-r2-golden-ubuntu-latest` | `FINAL PASS @ c7b1ba1…` | `FINAL PASS @ e6023ba…` | `FINAL PASS @ 4ffaf518…` | `PENDING` |
| `p19-r2-golden-windows-latest` | `FINAL PASS @ c7b1ba1…` | `FINAL PASS @ e6023ba…` | `FINAL PASS @ 4ffaf518…` | `PENDING` |

For every non-pending cell, attach the GitHub run URL, conclusion, checked SHA
and completion time below. A green check on another SHA does not count.

## 8. Remote CI evidence records

| Stage | Candidate SHA | Check name | Run URL/ID | Conclusion | Checked head SHA | Completed at |
|---|---|---|---|---|---|---|
| P7C.0 | `439b018fe807c32bba625998272e4021b230111c` | `source-authority-ubuntu-latest` | [job 100878450510 / run 33825924970](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33825924970/job/100878450510) | `SUCCESS` | `439b018fe807c32bba625998272e4021b230111c` | `2026-09-04T01:29:37Z` |
| P7C.0 | `439b018fe807c32bba625998272e4021b230111c` | `source-authority-windows-latest` | [job 100878450316 / run 33825924970](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33825924970/job/100878450316) | `SUCCESS` | `439b018fe807c32bba625998272e4021b230111c` | `2026-09-04T01:30:39Z` |
| P7C.0 | `439b018fe807c32bba625998272e4021b230111c` | `full-regression-ubuntu-latest` | [job 100878450455 / run 33825924970](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33825924970/job/100878450455) | `SUCCESS` | `439b018fe807c32bba625998272e4021b230111c` | `2026-09-04T01:34:44Z` |
| P7C.0 | `439b018fe807c32bba625998272e4021b230111c` | `full-regression-windows-latest` | [job 100878450486 / run 33825924970](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33825924970/job/100878450486) | `SUCCESS` | `439b018fe807c32bba625998272e4021b230111c` | `2026-09-04T01:46:00Z` |
| P7C.0 | `439b018fe807c32bba625998272e4021b230111c` | `p14-focused-ubuntu-latest` | [job 100878450464 / run 33825925001](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33825925001/job/100878450464) | `SUCCESS` | `439b018fe807c32bba625998272e4021b230111c` | `2026-09-04T01:30:08Z` |
| P7C.0 | `439b018fe807c32bba625998272e4021b230111c` | `p14-focused-windows-latest` | [job 100878450494 / run 33825925001](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33825925001/job/100878450494) | `SUCCESS` | `439b018fe807c32bba625998272e4021b230111c` | `2026-09-04T01:32:23Z` |
| P7C.0 | `439b018fe807c32bba625998272e4021b230111c` | `full-regression-ubuntu` | [job 100878450298 / run 33825925001](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33825925001/job/100878450298) | `SUCCESS` | `439b018fe807c32bba625998272e4021b230111c` | `2026-09-04T01:33:52Z` |
| P7C.0 | `439b018fe807c32bba625998272e4021b230111c` | `p19-r2-golden-ubuntu-latest` | [job 100878450255 / run 33825924964](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33825924964/job/100878450255) | `SUCCESS` | `439b018fe807c32bba625998272e4021b230111c` | `2026-09-04T01:31:10Z` |
| P7C.0 | `439b018fe807c32bba625998272e4021b230111c` | `p19-r2-golden-windows-latest` | [job 100878450351 / run 33825924964](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33825924964/job/100878450351) | `SUCCESS` | `439b018fe807c32bba625998272e4021b230111c` | `2026-09-04T01:33:31Z` |
| P7C.1 | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `source-authority-ubuntu-latest` | [job 100907262114 / run 33835568527](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33835568527/job/100907262114) | `SUCCESS` | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `2026-09-04T04:07:10Z` |
| P7C.1 | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `source-authority-windows-latest` | [job 100907262167 / run 33835568527](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33835568527/job/100907262167) | `SUCCESS` | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `2026-09-04T04:07:54Z` |
| P7C.1 | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `full-regression-ubuntu-latest` | [job 100907262020 / run 33835568527](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33835568527/job/100907262020) | `SUCCESS` | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `2026-09-04T04:13:27Z` |
| P7C.1 | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `full-regression-windows-latest` | [job 100907262144 / run 33835568527](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33835568527/job/100907262144) | `SUCCESS` | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `2026-09-04T04:30:58Z` |
| P7C.1 | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `p14-focused-ubuntu-latest` | [job 100907262046 / run 33835568522](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33835568522/job/100907262046) | `SUCCESS` | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `2026-09-04T04:07:11Z` |
| P7C.1 | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `p14-focused-windows-latest` | [job 100907262027 / run 33835568522](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33835568522/job/100907262027) | `SUCCESS` | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `2026-09-04T04:08:46Z` |
| P7C.1 | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `full-regression-ubuntu` | [job 100907261791 / run 33835568522](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33835568522/job/100907261791) | `SUCCESS` | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `2026-09-04T04:13:55Z` |
| P7C.1 | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `p19-r2-golden-ubuntu-latest` | [job 100907262023 / run 33835568541](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33835568541/job/100907262023) | `SUCCESS` | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `2026-09-04T04:08:34Z` |
| P7C.1 | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `p19-r2-golden-windows-latest` | [job 100907261769 / run 33835568541](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33835568541/job/100907261769) | `SUCCESS` | `9e744d0b2185f0b6e4abca0981daa62dc9494a7c` | `2026-09-04T04:11:05Z` |
| P7C.1 | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `source-authority-ubuntu-latest` | [job 100914435934 / run 33838030691](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33838030691/job/100914435934) | `SUCCESS` | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `2026-09-04T04:46:59Z` |
| P7C.1 | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `source-authority-windows-latest` | [job 100914435969 / run 33838030691](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33838030691/job/100914435969) | `SUCCESS` | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `2026-09-04T04:48:01Z` |
| P7C.1 | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `full-regression-ubuntu-latest` | [job 100914435711 / run 33838030691](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33838030691/job/100914435711) | `SUCCESS` | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `2026-09-04T04:53:18Z` |
| P7C.1 | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `full-regression-windows-latest` | [job 100914435873 / run 33838030691](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33838030691/job/100914435873) | `SUCCESS` | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `2026-09-04T05:08:45Z` |
| P7C.1 | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `p14-focused-ubuntu-latest` | [job 100914435922 / run 33838030697](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33838030697/job/100914435922) | `SUCCESS` | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `2026-09-04T04:46:54Z` |
| P7C.1 | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `p14-focused-windows-latest` | [job 100914435661 / run 33838030697](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33838030697/job/100914435661) | `SUCCESS` | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `2026-09-04T04:48:11Z` |
| P7C.1 | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `full-regression-ubuntu` | [job 100914435835 / run 33838030697](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33838030697/job/100914435835) | `SUCCESS` | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `2026-09-04T04:53:19Z` |
| P7C.1 | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `p19-r2-golden-ubuntu-latest` | [job 100914435755 / run 33838030724](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33838030724/job/100914435755) | `SUCCESS` | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `2026-09-04T04:48:18Z` |
| P7C.1 | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `p19-r2-golden-windows-latest` | [job 100914435931 / run 33838030724](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33838030724/job/100914435931) | `SUCCESS` | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `2026-09-04T04:51:21Z` |
| P7D.1 | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `source-authority-ubuntu-latest` | [job 100951168554 / run 33850249911](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33850249911/job/100951168554) | `SUCCESS` | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `2026-09-04T07:47:03Z` |
| P7D.1 | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `source-authority-windows-latest` | [job 100951168479 / run 33850249911](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33850249911/job/100951168479) | `SUCCESS` | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `2026-09-04T07:48:01Z` |
| P7D.1 | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `full-regression-ubuntu-latest` | [job 100951168374 / run 33850249911](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33850249911/job/100951168374) | `SUCCESS` | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `2026-09-04T07:55:48Z` |
| P7D.1 | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `full-regression-windows-latest` | [job 100951168645 / run 33850249911](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33850249911/job/100951168645) | `SUCCESS` | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `2026-09-04T08:16:29Z` |
| P7D.1 | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `p14-focused-ubuntu-latest` | [job 100951168818 / run 33850249966](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33850249966/job/100951168818) | `SUCCESS` | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `2026-09-04T07:47:21Z` |
| P7D.1 | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `p14-focused-windows-latest` | [job 100951168990 / run 33850249966](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33850249966/job/100951168990) | `SUCCESS` | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `2026-09-04T07:49:51Z` |
| P7D.1 | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `full-regression-ubuntu` | [job 100951168672 / run 33850249966](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33850249966/job/100951168672) | `SUCCESS` | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `2026-09-04T07:55:14Z` |
| P7D.1 | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `p19-r2-golden-ubuntu-latest` | [job 100951168883 / run 33850250071](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33850250071/job/100951168883) | `SUCCESS` | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `2026-09-04T07:48:34Z` |
| P7D.1 | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `p19-r2-golden-windows-latest` | [job 100951169066 / run 33850250071](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33850250071/job/100951169066) | `SUCCESS` | `c327851a1e18deb7c602673c3f3f87c6afa785f7` | `2026-09-04T07:52:56Z` |
| P7D.1 | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `source-authority-ubuntu-latest` | [job 100959704470 / run 33852963416](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33852963416/job/100959704470) | `SUCCESS` | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `2026-09-04T08:21:22Z` |
| P7D.1 | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `source-authority-windows-latest` | [job 100959704672 / run 33852963416](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33852963416/job/100959704672) | `SUCCESS` | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `2026-09-04T08:22:27Z` |
| P7D.1 | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `full-regression-ubuntu-latest` | [job 100959704749 / run 33852963416](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33852963416/job/100959704749) | `SUCCESS` | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `2026-09-04T08:29:24Z` |
| P7D.1 | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `full-regression-windows-latest` | [job 100959704783 / run 33852963416](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33852963416/job/100959704783) | `FAILURE` | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `2026-09-04T08:43:54Z` |
| P7D.1 | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `p14-focused-ubuntu-latest` | [job 100959705011 / run 33852963463](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33852963463/job/100959705011) | `SUCCESS` | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `2026-09-04T08:21:10Z` |
| P7D.1 | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `p14-focused-windows-latest` | [job 100959705060 / run 33852963463](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33852963463/job/100959705060) | `SUCCESS` | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `2026-09-04T08:23:23Z` |
| P7D.1 | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `full-regression-ubuntu` | [job 100959704863 / run 33852963463](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33852963463/job/100959704863) | `SUCCESS` | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `2026-09-04T08:30:09Z` |
| P7D.1 | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `p19-r2-golden-ubuntu-latest` | [job 100959704524 / run 33852963431](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33852963431/job/100959704524) | `SUCCESS` | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `2026-09-04T08:22:52Z` |
| P7D.1 | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `p19-r2-golden-windows-latest` | [job 100959704620 / run 33852963431](https://github.com/simahanfeng007-lgtm/Tiangongzaowu-V3/actions/runs/33852963431/job/100959704620) | `SUCCESS` | `331e675a7e159493d78fc4242612a1bdb8aed41f` | `2026-09-04T08:26:08Z` |
| P7D.2 | `TBD` | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` |

Add nine rows for a stage when its candidate is pushed; do not summarize mixed
SHAs into one PASS.

The P7C.0 first-round rows above are supplemented by the exact successor-head
rerun matrix in the immutable PR #69 comment. The P7C.1 rows above are its first
remote round; the exact final `e6023ba100f2b8a19331e1a0b0b46e0251533a32`
successor-head matrix is preserved in the immutable PR #70 evidence comment.
Both exact successor heads were merged. Keeping final rerun matrices in
immutable PR comments avoids creating an endlessly self-invalidating evidence
commit. The P7D.1 rows include its successful first round and the rejected
eight-of-nine evidence-only predecessor. Final successor
`4ffaf51809e1b299b574fe7c61dbf76614981c6d` passed all nine checks; its exact
run links and four-way head match are preserved in the immutable PR #71
evidence comment, and that head merged as `f268d6ac3293ee31e6c20b7e7f706f46cfa3e040`.

## 9. Head-match closure

| Stage | Local reviewed HEAD | Remote branch tip | PR head SHA | Nine-check SHA | Worktree clean | Status |
|---|---|---|---|---|---|---|
| P7C.0 | `c7b1ba1d33bf12e8e66eed1940be248b7d048adc` | `c7b1ba1d33bf12e8e66eed1940be248b7d048adc` | `c7b1ba1d33bf12e8e66eed1940be248b7d048adc` | `c7b1ba1d33bf12e8e66eed1940be248b7d048adc` | `YES` | `CLOSED / PR #69 MERGED AS b75d0c8…` |
| P7C.1 | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `e6023ba100f2b8a19331e1a0b0b46e0251533a32` | `YES` | `CLOSED / PR #70 MERGED AS acb39a63…` |
| P7D.1 | `4ffaf51809e1b299b574fe7c61dbf76614981c6d` | `4ffaf51809e1b299b574fe7c61dbf76614981c6d` | `4ffaf51809e1b299b574fe7c61dbf76614981c6d` | `4ffaf51809e1b299b574fe7c61dbf76614981c6d` | `YES` | `CLOSED / PR #71 MERGED AS f268d6ac…` |
| P7D.2 | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` | `PENDING` |

Closure requires all four SHA columns to be identical, all nine checks green on
that SHA, and no unreviewed worktree change. Any later commit resets the stage's
remote-check and head-match cells to `PENDING`.

## 10. Known risks carried forward

| Risk | Required control | Owning stage | Status |
|---|---|---|---|
| v30 stores only a hash, not recoverable execution material | v31 companion; never synthesize/backfill | P7C.0 | `CLOSED @ c7b1ba1… / MERGED IN b75d0c8…` |
| Self-consistent forged companion | authoritative deterministic rebuild + exact equality; no raw-plan Store sink | P7C.0 | `CLOSED @ c7b1ba1… / MERGED IN b75d0c8…` |
| Partial P19/registration/plan visibility | one existing-Store atomic UoW + per-insert/marker/owner rollback injection | P7C.0 | `CLOSED @ c7b1ba1… / MERGED IN b75d0c8…` |
| Same object identity replaced/deleted/rebound across owners or requests | Store-wide pre-insert check + canonical REQUEST owner + append-only schema guards + legacy integrity scan | P7C.0 | `CLOSED @ c7b1ba1… / MERGED IN b75d0c8…` |
| Sealed executable companion replaced, changed or deleted after commit | identity INSERT/REPLACE/UPSERT guard + unconditional UPDATE/DELETE guards + schema fingerprint + integrity scan | P7C.0 | `CLOSED @ c7b1ba1… / MERGED IN b75d0c8…` |
| Marker-1 companion corruption bypasses P7B paths | registration-scoped full companion parse/cross-authority validation on each P7B read/recovery/expiry/replay target; global open/health scan | P7C.0 | `CLOSED @ c7b1ba1… / MERGED IN b75d0c8…` |
| Dynamic result drift/substitution | exact upstream Effect+Fact+lineage and schema-bound `STEP_OUTPUT` backed by an explicit result/value schema body in the existing Omni catalog | P7D.2 | `CONTROL IMPLEMENTED / LOCAL GATES PASS / REMOTE PENDING` |
| Parent authorization disappears after unregister or gateway epoch restart | insert-only, non-executable continuation delegation may only re-enter the current Policy/Ticket/Grant chain; it can never authorize Runtime directly | P7D.2 | `CONTROL IMPLEMENTED / LOCAL GATES PASS / REMOTE PENDING` |
| Pre-start receipt cannot be safely resumed in a new epoch | Store proves handler count zero and nonces unconsumed before bounded attempt+1/new Effect ID supersession | P7D.2 | `CONTROL IMPLEMENTED; ATTEMPT 2 BOUNDED / STARTED NO-REPLAY / LOCAL GATES PASS / REMOTE PENDING` |
| Existing CompletionGate treats a legal multi-Fact batch as a conflict | compare the complete exact FactBatch ID tuple and bind every required leaf Effect/Fact | P7D.2 | `CONTROL IMPLEMENTED / LOCAL GATES PASS / REMOTE PENDING` |
| Composition writes the regenerative `execution_frontier` | derive a read-only frontier from sealed plan + receipts + Effect heads + Fact batches; preserve the existing single writer | P7D.2 | `PROHIBITED / READ-ONLY PROJECTION IMPLEMENTED / LOCAL GATES PASS / REMOTE PENDING` |
| Plan A0 label used as permission | current compiled permission + Policy at immediate boundary | P7C.1 | `CLOSED @ e6023ba… / MERGED IN acb39a63…` |
| Capability manifest changes between model loading and authorization authority compilation | compile Registry and schema catalog from the same single verified file read, then reuse that authority in orchestration | P7C.1 | `CLOSED @ e6023ba… / MERGED IN acb39a63…` |
| Request/plan/step/arguments/target/generation crossing under a valid signature | exact `CompositionExecutionBindingV1` on Intent, Decision, Ticket and Grant plus independent expected binding | P7C.1 | `CLOSED @ e6023ba… / MERGED IN acb39a63…` |
| Durable receipt treated as signature authority after restart | Store validates canonical structure and hashes; the P7D consumer revalidates the immutable receipt, current trust, ticket and grant before dispatch | P7C.1/P7D.1 | `P7D.2 CURRENT-EPOCH CONTINUATION CONTROL IMPLEMENTED / LOCAL GATES PASS / REMOTE PENDING` |
| Same-size path content changes after snapshot | strengthen path snapshot before any path-based composition execution | P7D.1 | `CLOSED BY SCOPE: RAW HOST PATHS REMAIN FORBIDDEN; OBJECT BYTES/REVISION AND OPAQUE TARGET SNAPSHOT ARE REVALIDATED` |
| Second scheduler/outcome authority | only `GatewayOrchestrationWorker` + canonical Effect/Fact seams | P7D.1 | `P7D.2 READ-ONLY DAG PROJECTION IMPLEMENTED / LOCAL GATES PASS / REMOTE PENDING` |
| Cross-boundary timeout replay | `AMBIGUOUS`/reconcile-required; no blind replay | P7D.1/P7D.2 | `CONTROL IMPLEMENTED ACROSS DAG CUT-POINT MATRIX / LOCAL GATES PASS / REMOTE PENDING` |
| Grant-admission evidence mistaken for action success | require canonical action Effect head + Gateway execution fact | P7D.1/P7D.2 | `PARENT-PLUS-LEAF EFFECT/FACT BARRIER IMPLEMENTED / LOCAL GATES PASS / REMOTE PENDING` |
| Verification or repair escapes A0 | exact P19 plan; no A1+ repair dispatch in first batch | P7D.2 | `CONTROL IMPLEMENTED / LOCAL GATES PASS / REMOTE PENDING` |
| Partial leaf success declared complete | CompletionGate requires every required leaf Effect/fact | P7D.2 | `CONTROL IMPLEMENTED / LOCAL GATES PASS / REMOTE PENDING` |
| Read-only composition triggers channel delivery | initial P7D batch excludes external delivery/send | P7D.2 | `CONTROL IMPLEMENTED / LOCAL GATES PASS / REMOTE PENDING` |
