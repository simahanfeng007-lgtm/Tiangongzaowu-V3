# P7C / P7D Continuous Progress and Evidence Ledger

Last updated: 2026-09-04 09:18 +08:00.

This file is the continuous status ledger for P7C.0, P7C.1, P7D.1 and P7D.2.
It separates observed baseline evidence from planned or not-yet-run evidence.
`PENDING` is never a PASS claim.

## 1. Fixed baseline

| Item | Value | Evidence status |
|---|---|---|
| Worktree | `C:\Users\77571\Documents\天工造物v3-p7c-p7d` | Observed in this work session |
| Baseline commit | `14f6946a9d994e70654b9d64ecfcaae9c74baba4` | `git rev-parse HEAD` matched before document edits |
| Baseline branch relationship | Based on latest `main` fixed by the task | Exact remote/head-match evidence still required before a stage closes |
| Baseline focused suite | `75 passed` | Inherited baseline evidence supplied for this work; not rerun by this documentation change |
| Store baseline | schema v30 | Observed at baseline source (`STORE_SCHEMA_VERSION = 30`) |
| Durable P7B baseline | P19 RegistrySnapshot + VerificationPlan + activation + limited registration in one existing-Store UoW | Observed at baseline source |
| Production authority baseline | one existing Gateway/Store/Policy/Ticket/Grant/Runtime/P19/Completion chain | Must remain invariant through every stage |

The `75 passed` row records the supplied baseline only. Its original command,
platform, timestamp and run artifact must be attached before it is reused as a
release/merge claim. This document does not represent that suite as rerun.

## 2. Current status

| Stage | State | Completed in current work | Still required |
|---|---|---|---|
| P7C.0 | `LOCAL_GATES_PASS / CANDIDATE_PREP` | Restart-exact non-authorizing invocation template, v31 companion-required marker/table, atomic P19/P7B/plan/object-owner UoW, migration/recovery/integrity, bounded JSON, static targets and typed `STEP_OUTPUT` topology; closed raw-plan, post-seal mutation/deletion, Store-wide object rebind, lineage-only REPLACE/UPSERT and AST authority-surface bypasses; focused, adversarial, selected and full local gates pass | Restage the exact final file set, run committed-mirror verification, create/push the candidate, then pass nine remote checks and exact head-match |
| P7C.1 | `NOT_STARTED` | Boundary and non-goals documented | Current Action Registry/Policy/Ticket/Grant binding implementation and evidence |
| P7D.1 | `NOT_STARTED` | Unique Runtime seam and A0 first-slice boundary documented | Existing-worker/regenerative/Effect/Fact/BackendClient/Omni integration and evidence |
| P7D.2 | `NOT_STARTED` | DAG/`STEP_OUTPUT`/P19/Completion boundary documented | Durable DAG/restart/reconcile, P19 readiness, CompletionGate closeout, production A0 evidence |

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
- [ ] Push one reviewed candidate SHA.
- [ ] Pass all nine required GitHub checks on that SHA.
- [ ] Record final head-match proof.

## 4. Mandatory P7C.0 counterexample matrix

The following rows were executed against the uncommitted P7C.0 candidate. Any
subsequent contract or Store change resets the affected rows to `PENDING`.

| Counterexample / invariant | Required result | Command or run | SHA | Status |
|---|---|---|---|---|
| Exact companion compile is deterministic | Same authoritative inputs yield byte-identical ID/hash/body | `test_canonical_full_arguments_and_typed_step_output_roundtrip` | `UNCOMMITTED` | `PASS` |
| Caller changes legacy Plan/body and rehashes | Rejected by authoritative rebuild | `test_rehashed_caller_modified_legacy_plan_is_rejected_by_recompile` | `UNCOMMITTED` | `PASS` |
| Missing/extra/reordered step binding | Rejected | `test_step_binding_cardinality_and_order_are_exact` (3 cases) | `UNCOMMITTED` | `PASS` |
| Action ID/version/schema/source/permission-manifest drift | Rejected | `test_public_bundle_rejects_rehashed_candidate_and_schema_drift` (6 cases) + `test_step_permission_source_manifest_must_match_plan_manifest` | `UNCOMMITTED` | `PASS` |
| A1+, declared write/execute effect, `external_send`/`destructive` side effects, shell/python | Rejected at first-batch boundary | `test_current_registry_materialization_rejects_non_a0_read_verify` (5 cases) + `test_step_permission_is_self_contained_a0_read_verify` (2) + `test_step_permission_external_send_and_destructive_fail_closed` (2) + `test_current_primitive_external_send_and_destructive_fail_closed` (2) + `test_unknown_primitive_side_effect_is_rejected_fail_closed` + `test_public_bundle_revalidates_model_copy_bypassed_permission` | `UNCOMMITTED` | `PASS` |
| `STEP_OUTPUT` self/future/undeclared/missing-edge/cyclic source | Rejected | `test_step_output_self_and_unknown_declaration_fail_closed` + `test_step_output_requires_an_explicit_dependency_edge` + `test_step_output_from_a_future_step_is_rejected` + `test_cyclic_proposal_is_rejected_before_executable_materialization` | `UNCOMMITTED` | `PASS` |
| `STEP_OUTPUT` pointer/binding discriminant/destination overlap or duplicate invalid | Rejected | `test_step_output_reference_is_hash_bound_and_fails_closed` + `test_step_output_result_selector_requires_rfc6901_pointer` + `test_step_output_binding_rejects_discriminant_type_confusion` + `test_argument_destination_json_pointers_must_not_overlap` + `test_argument_destination_json_pointers_must_be_unique` + `test_array_pointer_indices_require_ascii_digits` + `test_unresolved_result_pointer_rejects_ambiguous_array_tokens` + `test_target_slot_must_resolve_statically_to_a_string` | `UNCOMMITTED` | `PASS` |
| Failure during any P19/registration/plan/owner insert or marker update | Whole existing-Store UoW rolls back | `test_each_bundle_insert_failure_rolls_back_the_whole_uow` (5 tables) + `test_object_owner_insert_failure_rolls_back_the_whole_uow` + `test_parent_marker_failure_rolls_back_the_whole_uow` + `test_late_plan_insert_failure_rolls_back_request_owner_and_bundle` | `UNCOMMITTED` | `PASS` |
| Exact retry / concurrent exact retry | One durable group; both readers converge | `test_exact_replay_preserves_first_plan_and_registration_rows` + `test_exact_replay_compares_canonical_json_not_python_sequence_shape` + `test_two_store_connections_converge_on_one_atomic_executable_bundle` | `UNCOMMITTED` | `PASS` |
| Same registration identity with different companion material | Explicit collision/conflict; original row remains | `test_registration_identity_rejects_different_companion_material` | `UNCOMMITTED` | `PASS` |
| Persisted companion is replaced, changed with fully recomputed hashes, or deleted | Schema-fingerprinted append-only guards reject all three operations; original row and healthy Store remain | `test_persisted_executable_plan_rejects_replace_rehashed_update_and_delete` | `UNCOMMITTED` | `PASS` |
| New companion changes all five stable identities but reuses one Request/Run/Generation lineage | Plain INSERT, `INSERT OR REPLACE` and `ON CONFLICT ... DO NOTHING` all fail at the identity trigger; original row and healthy Store remain | `test_plan_lineage_identity_guard_rejects_insert_replace_and_upsert_without_replacing_original` (3 cases) | `UNCOMMITTED` | `PASS` |
| v30 database opens under v31 | Additive migration; old row readable but non-executable | `test_v30_audit_registration_migrates_additively_but_has_no_companion` + `test_v30_to_v31_concurrent_open_serializes_migration` | `UNCOMMITTED` | `PASS` |
| Old v30 registration has no companion | No executable companion is synthesized; lookup/recovery omit it and backfill is rejected | `test_v30_audit_registration_migrates_additively_but_has_no_companion` | `UNCOMMITTED` | `PASS` |
| v31-created registration loses/crosses/malforms companion | All applicable P7B/P7C historical/active/recovery/expiry/replay paths and health fail closed | `test_required_companion_loss_is_explicit_corruption` + `test_integrity_scan_detects_executable_column_and_json_tampering` + `test_crossed_companion_body_is_explicit_corruption` | `UNCOMMITTED` | `PASS` |
| Same `object_id` is replaced/deleted/rebound to another hash or given a noncanonical REQUEST owner; one-plan metadata/scope drifts | Public owner API, cross-request bundle and direct SQL insert/replace/update/delete/UPSERT reject; mutated owner REPLACE/UPSERT preserve the original row; legacy divergence blocks open; same-hash cross-request reuse remains valid | `test_object_input_matches_inbound_attachment_and_gets_request_owner` + `test_public_owner_api_cannot_rebind_executable_object_identity` + `test_public_owner_api_rejects_invalid_owner_kind_before_transaction` + `test_two_bundles_cannot_bind_one_object_id_to_different_content` + `test_two_requests_may_share_one_object_id_only_for_the_same_content` + `test_object_inputs_can_pin_two_revisions_of_one_accepted_object` + `test_same_object_id_cannot_claim_different_revision_authority` (6 fields) + `test_v31_health_rejects_legacy_object_identity_hash_divergence` | `UNCOMMITTED` | `PASS` |
| P7B operational companion validation scales with selected rows | Active lookup reads one registration-scoped companion; recovery/expiry verify each of two selected rows exactly once; no executable-plan full scan | `test_active_lookup_reads_one_registration_scoped_companion_without_full_scan` + `test_p7b_recovery_and_expiry_verify_each_selected_companion_once` | `UNCOMMITTED` | `PASS` |
| Expired/released request generation | Active executable lookup rejects | `test_restart_recovers_companion_then_expiry_preserves_audit_body` + `test_active_lookup_fails_closed_after_generation_drift` | `UNCOMMITTED` | `PASS` |
| Raw executable-plan writer/token exposure | No raw-plan sink/token exists; all writes recompile in the public bundle | `test_gateway_store_exposes_no_raw_executable_plan_write_sink` + `test_public_bundle_rejects_rehashed_candidate_and_schema_drift` + `test_public_bundle_revalidates_model_copy_bypassed_permission` + `test_rehashed_caller_modified_legacy_plan_is_rejected_by_recompile` | `UNCOMMITTED` | `PASS` |
| P7C.0 import/call surface | Compiler/codec and bundle method contain no Policy/Ticket/Grant/Runtime/Effect/Fact/P19 execution/Completion authority; qualified calls, exact SQL/helper closure and unique runtime bindings reject dynamic/rebind/decorator/nested-scope bypasses | compiler/codec structural test + `test_register_executable_bundle_has_no_policy_ticket_grant_effect_fact_or_runtime_authority` + `test_register_executable_bundle_authority_guard_rejects_mutants` (24 cases) + safe-layout positive control | `UNCOMMITTED` | `PASS` |

## 5. Future-stage evidence checkpoints

### P7C.1

- [ ] Only exact active v31 registration + companion enters authorization.
- [ ] Current Action Registry and effective A0 permission are rechecked at the
  immediate authorization/dispatch boundary.
- [ ] Resolved arguments, workspace, target state, request/run/generation,
  executable-plan hash, step and Action/version are bound into existing
  Policy/Ticket/Grant artifacts.
- [ ] Expiry, nonce replay, argument swap, target swap, plan swap and generation
  swap fail closed.
- [ ] No alternate Policy, Ticket or grant authority exists.
- [ ] Focused/local evidence, nine remote checks and head-match are recorded.

### P7D.1

- [ ] Existing `GatewayOrchestrationWorker` is the only scheduler.
- [ ] Existing regenerative execution seam and canonical Gateway Effect/Fact
  authorities own durable progress; `ExecutionEngine` is not introduced as a
  production scheduler/outcome ledger.
- [ ] A0 read/verify goes through existing `BackendClient` → Omni Body →
  `BodyRuntime` using exact Ticket/Grant bindings.
- [ ] Pre-dispatch rejection invokes no handler.
- [ ] Timeout/error after the execution boundary becomes `AMBIGUOUS` or
  reconcile-required and is not replayed blindly.
- [ ] Single-step restart cut points prove no duplicate handler call.
- [ ] P7D.1 cannot claim production completion before P7D.2.
- [ ] Focused/local evidence, nine remote checks and head-match are recorded.

### P7D.2

- [ ] Durable topological scheduling unlocks a step only after every dependency
  has an authoritative successful Effect head and exact Gateway fact.
- [ ] `STEP_OUTPUT` resolves only from the exact upstream fact and its resolved
  arguments hash is persisted before dispatch.
- [ ] Crash windows before/after claim, started boundary, handler, Fact write,
  Effect completion, frontier/checkpoint and P19 are covered.
- [ ] Fact/Effect disagreement, ambiguous outcomes and stale generations enter
  reconciliation; no duplicate side effect is possible.
- [ ] P19 uses the exact active Plan/Registry/subjects and derives readiness
  through the existing readiness authority.
- [ ] Failed/inconclusive/error verification cannot complete; A0 rollout cannot
  dispatch an A1+ repair.
- [ ] Existing `CompletionGate` checks every required leaf Effect/fact and is the
  only completed-status source.
- [ ] No external send/delivery and no A1+ Action is enabled in the first batch.
- [ ] Focused/local evidence, nine remote checks and head-match are recorded.

## 6. Local evidence before remote CI

Populate one row per actual run. Do not replace a missing value with an
assumption.

| Stage | Evidence kind | Exact command | Platform/runtime | Result | Test count | Candidate SHA | Timestamp/run artifact |
|---|---|---|---|---|---|---|---|
| Baseline | Supplied focused baseline | Not attached | Not attached | `75 passed` (supplied, not rerun here) | 75 | `14f6946a9d994e70654b9d64ecfcaae9c74baba4` | Not attached |
| P7C.0 | Focused contract/compiler + migration/restart + tamper/rollback/race | `python -m pytest tests/test_composition_executable_plan_p7c0.py tests/test_composition_activation_store_p7b2.py -q` | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 141 passed | `UNCOMMITTED` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | P4→P7 selected regression | `python -m pytest tests/test_capability_composition_contracts.py tests/test_capability_composition_p4.py tests/test_capability_composition_p4_hardening.py tests/test_capability_composition_p4_cross_phase.py tests/test_composition_activation_shadow_p7a.py tests/test_composition_activation_registration_p7b.py tests/test_composition_activation_store_p7b2.py tests/test_composition_executable_plan_p7c0.py -q` | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 188 passed | `UNCOMMITTED` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | P19 Store/write-evidence/golden/fault selected regression | `python -m pytest tests/test_p19_m1_store.py tests/test_p19_m3_write_evidence_v2.py tests/golden/p19_r2/test_golden_trace.py tests/golden/p19_r2/test_fault_matrix.py -q` | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 64 passed | `UNCOMMITTED` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | P14 focused regression | Workflow-equivalent 12-file P14 selection recorded by this ledger's work session | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 109 passed | `UNCOMMITTED` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | P3→P13 boundary regression | Five-file P3/P13 selection recorded by this ledger's work session | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 151 passed | `UNCOMMITTED` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | Existing architecture/store regression | Twelve-file Gateway/Memory/P15/P17/settings selection recorded by this ledger's work session | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 124 passed | `UNCOMMITTED` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | Full Python regression | Set current-worktree `src`, backend and readable-source roots in `PYTHONPATH`; run original embedded `python.exe -m pytest -q --maxfail=1 tests` | Windows / embedded Python 3.12, current-worktree sources only | `PASS` | 3800 passed, 17 skipped, 824 subtests passed | `UNCOMMITTED` | 586.31 s; completed before ledger refresh 2026-09-04 09:18 +08:00 |
| P7C.0 | Full Node regression | `$NodeTests = @(Get-ChildItem tests -Filter '*.test.mjs' -File | Sort-Object FullName | ForEach-Object FullName); node --test @NodeTests` | Windows / Node v24.14.0 | `PASS` | 224 passed, 2 skipped, 0 failed in 29 files | `UNCOMMITTED` | 1976.58 ms; completed before ledger refresh 2026-09-04 09:18 +08:00 |
| P7C.0 | Generated-source mirrors | `python scripts/sync-generated-sources.py --check`; after staging: `python scripts/sync-generated-sources.py --check-committed` | Windows / embedded Python 3.12 | `PASS` | 2 passed | `UNCOMMITTED` | Both checks passed on the exact 16-file staged set; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | Source Authority | `python scripts/check-source-authority.py` | Windows / embedded Python 3.12 | `PASS` | 16 independent authorities, 1 alias, 24 generated targets, 1 closed-world boundary | `UNCOMMITTED` | Captured in this work session; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | P19 freeze + drift fingerprint | `python -m pytest -q tests/golden/p19_r2/test_freeze_and_guards.py tests/golden/p19_r2/test_calibration_and_stability.py::DriftFingerprintTests::test_fingerprint_matches_or_declared` | Windows / embedded Python 3.12 | `PASS` | 7 passed | `UNCOMMITTED` | Regenerated from final product source and verified; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.0 | Portable paths + source syntax + forbidden artifacts | Embedded Python portable-path audit; `node --check` over app JS/MJS; `ast.parse` over src/tests Python; repository file-name scan | Windows / embedded Python 3.12 + Node v24.14.0 | `PASS` | 1953 portable files; 102 JS; 808 Python; 2421 source files artifact-scanned | `UNCOMMITTED` | Exact `check.ps1` steps run with the main checkout's same embedded runtime because ignored `app/runtime` is absent from this independent worktree; ledger refreshed 2026-09-04 09:18 +08:00 |
| P7C.1 | focused + security counterexamples | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` | `TBD` |
| P7C.1 | generated-source sync/check + mirrors | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` | `TBD` |
| P7C.1 | selected/full local regression | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` | `TBD` |
| P7D.1 | runtime/effect/fact/restart/timeout | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` | `TBD` |
| P7D.1 | generated-source sync/check + mirrors | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` | `TBD` |
| P7D.1 | selected/full local regression | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` | `TBD` |
| P7D.2 | DAG/`STEP_OUTPUT`/reconcile/P19/Completion | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` | `TBD` |
| P7D.2 | generated-source sync/check + mirrors | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` | `TBD` |
| P7D.2 | selected/full local regression | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` | `TBD` |

The first discovered Node full-suite run had one timeout before the `cancel`
assertions in the pre-existing `test_avatar_p2b_transport.test.mjs` readiness
helper. The same unchanged test failed on baseline `main`; repeated isolation
was 20/20 green, and a subsequent discovered full suite was 224 passed / 2
skipped / 0 failed. This is recorded as a baseline timing flake, not hidden as
a product-code pass.

## 7. Nine required GitHub checks per stage

Focused/migration/tamper tests and local sync/check evidence must be green
before a candidate enters remote CI. Then each stage must pass these exact nine
checks against one unchanged candidate SHA.

| Required GitHub check | P7C.0 | P7C.1 | P7D.1 | P7D.2 |
|---|---|---|---|---|
| `source-authority-ubuntu-latest` | `PENDING` | `PENDING` | `PENDING` | `PENDING` |
| `source-authority-windows-latest` | `PENDING` | `PENDING` | `PENDING` | `PENDING` |
| `Architecture full-regression-ubuntu-latest` | `PENDING` | `PENDING` | `PENDING` | `PENDING` |
| `Architecture full-regression-windows-latest` | `PENDING` | `PENDING` | `PENDING` | `PENDING` |
| `p14-focused-ubuntu-latest` | `PENDING` | `PENDING` | `PENDING` | `PENDING` |
| `p14-focused-windows-latest` | `PENDING` | `PENDING` | `PENDING` | `PENDING` |
| `P14 full-regression-ubuntu` | `PENDING` | `PENDING` | `PENDING` | `PENDING` |
| `p19-r2-golden-ubuntu-latest` | `PENDING` | `PENDING` | `PENDING` | `PENDING` |
| `p19-r2-golden-windows-latest` | `PENDING` | `PENDING` | `PENDING` | `PENDING` |

For every non-pending cell, attach the GitHub run URL, conclusion, checked SHA
and completion time below. A green check on another SHA does not count.

## 8. Remote CI evidence records

| Stage | Candidate SHA | Check name | Run URL/ID | Conclusion | Checked head SHA | Completed at |
|---|---|---|---|---|---|---|
| P7C.0 | `TBD` | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` |
| P7C.1 | `TBD` | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` |
| P7D.1 | `TBD` | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` |
| P7D.2 | `TBD` | `TBD` | `TBD` | `PENDING` | `TBD` | `TBD` |

Add nine rows for a stage when its candidate is pushed; do not summarize mixed
SHAs into one PASS.

## 9. Head-match closure

| Stage | Local reviewed HEAD | Remote branch tip | PR head SHA | Nine-check SHA | Worktree clean | Status |
|---|---|---|---|---|---|---|
| P7C.0 | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` | `PENDING` |
| P7C.1 | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` | `PENDING` |
| P7D.1 | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` | `PENDING` |
| P7D.2 | `TBD` | `TBD` | `TBD` | `TBD` | `TBD` | `PENDING` |

Closure requires all four SHA columns to be identical, all nine checks green on
that SHA, and no unreviewed worktree change. Any later commit resets the stage's
remote-check and head-match cells to `PENDING`.

## 10. Known risks carried forward

| Risk | Required control | Owning stage | Status |
|---|---|---|---|
| v30 stores only a hash, not recoverable execution material | v31 companion; never synthesize/backfill | P7C.0 | `CONTROL IMPLEMENTED / FULL LOCAL PASS / REMOTE PENDING` |
| Self-consistent forged companion | authoritative deterministic rebuild + exact equality; no raw-plan Store sink | P7C.0 | `CONTROL REVISED / FULL LOCAL PASS / REMOTE PENDING` |
| Partial P19/registration/plan visibility | one existing-Store atomic UoW + per-insert/marker/owner rollback injection | P7C.0 | `CONTROL IMPLEMENTED / FULL LOCAL PASS / REMOTE PENDING` |
| Same object identity replaced/deleted/rebound across owners or requests | Store-wide pre-insert check + canonical REQUEST owner + append-only schema guards + legacy integrity scan | P7C.0 | `CONTROL IMPLEMENTED / FULL LOCAL PASS / REMOTE PENDING` |
| Sealed executable companion replaced, changed or deleted after commit | identity INSERT/REPLACE/UPSERT guard + unconditional UPDATE/DELETE guards + schema fingerprint + integrity scan | P7C.0 | `CONTROL IMPLEMENTED / FULL LOCAL PASS / REMOTE PENDING` |
| Marker-1 companion corruption bypasses P7B paths | registration-scoped full companion parse/cross-authority validation on each P7B read/recovery/expiry/replay target; global open/health scan | P7C.0 | `CONTROL IMPLEMENTED / FULL LOCAL PASS / REMOTE PENDING` |
| Dynamic result drift/substitution | exact upstream Effect+Fact+lineage and schema-bound `STEP_OUTPUT` | P7D.2 | `OPEN` |
| Plan A0 label used as permission | current compiled permission + Policy at immediate boundary | P7C.1 | `OPEN` |
| Second scheduler/outcome authority | only `GatewayOrchestrationWorker` + existing regenerative/Effect/Fact seams | P7D.1 | `OPEN` |
| Cross-boundary timeout replay | `AMBIGUOUS`/reconcile-required; no blind replay | P7D.1/P7D.2 | `OPEN` |
| Grant-admission evidence mistaken for action success | require canonical action Effect head + Gateway execution fact | P7D.1/P7D.2 | `OPEN` |
| Verification or repair escapes A0 | exact P19 plan; no A1+ repair dispatch in first batch | P7D.2 | `OPEN` |
| Partial leaf success declared complete | CompletionGate requires every required leaf Effect/fact | P7D.2 | `OPEN` |
| Read-only composition triggers channel delivery | initial P7D batch excludes external delivery/send | P7D.2 | `OPEN` |
