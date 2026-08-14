# P17-M3-05 Store Closeout / Architecture Regression

Date: 2026-08-14 (Asia/Shanghai)

## Stage

P17-M3-05 — Store Closeout / Architecture Regression — completes the frozen
five-stage M3 plan.

1. M3-01 — Life SQLite Connection Boundary — complete
2. M3-02 — Life Schema / Migration Boundary — complete
3. M3-03 — Life Domain Repository Boundary — complete
4. M3-04 — Gateway Store / Unit-of-Work Boundary — complete
5. M3-05 — Store Closeout / Architecture Regression — complete in this stage

M3 closes at 5/5. No M3-06 was introduced. M2 remains closed at 5/5.

## Baseline

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Formal branch: `agent/p17-m3-store-authority-decomposition`
- M3-04 final baseline: `f1003b50bed7323b394985510c0a0bf861ff1e92`
- `main` was not merged or intentionally modified by this stage.

## Problem found

A full local pytest run over the M3-04 tree surfaced five failures that the
permanent Architecture Gate did not cover because its scope stopped at the
P17 regression files. Three classes of drift were found:

1. P15 static single-writer guards were stale relative to the M3-01..03
   authority structure:
   - `test_memory_single_writer_p15` and `p15_cutover.py` Phase F whitelists
     did not include the new Memory repository write owner
     `store_memory_repository.py`;
   - `test_memory_p15_architecture_guards` used a substring match for
     `class LifeShadowStore` that also matched
     `class LifeShadowStoreError` in `store_contract_support.py`;
   - `p15_cutover.py` Phase E still looked for the world-candidate outbox
     table in `store.py` although M3-02/M3-03 moved the DDL to
     `store_schema.py` and the operational SQL to
     `store_memory_repository.py`.

2. M2-era static guards were stale relative to M2-03/M2-05 seams:
   - `test_execution_integrity` asserted `decide_task_contract_completion(`
     inside `zongdiaodu.py` although M2-03 moved it behind
     `decide_simple_chain_completion` in `runtime_tool_result_boundary.py`;
   - `test_p16_native_proactive_runtime` asserted
     `set_proactive_world_provider` inside `total_gateway/runtime.py` although
     M2-05 replaced direct setter calls with
     `EmbeddedLifeGatewayBinding.PROACTIVE_WORLD_PROVIDER` binding.

3. One behavioral regression test had a stale mock anchor:
   - `test_memory_crash_atomicity_p15` patched
     `LifeShadowStore._put_memory_derivation_locked`, which is now a thin
     facade delegate; the crash must be injected into
     `LifeMemoryRepository._put_memory_derivation_locked` to exercise the
     real transaction closure. The atomic rollback guarantee itself is
     unchanged and the test passes again with the corrected anchor.

None of these failures indicated a product defect; all were guard/test
anchors that lagged the M2/M3 authority moves. The failure class itself
demonstrated that the permanent Gate had a coverage gap, which this stage
closes.

## Repairs

- extend Phase F `allowed_writers` with `store_memory_repository.py`
- re-target Phase E to the schema + repository authorities
- regex-based class scan (`class LifeShadowStore\s*[:(]`) to remove the
  `LifeShadowStoreError` false positive
- extend `test_memory_single_writer_p15` whitelist with the repository owner
- re-anchor `test_memory_crash_atomicity_p15` to the repository write method
- re-target the M2-03/M2-05 static guards to the current seams
- synchronize the `app/life-service/runtime314` generated mirror for
  `p15_cutover.py` via `sync-generated-sources.py --write`

## Permanent Architecture Gate

The permanent Gate now additionally locks, on both Ubuntu and Windows:

- store closeout regression:
  - `tests/test_gateway_store.py`
  - `tests/test_life_shadow_store.py`
  - `tests/test_causal_memory_store.py`
  - `tests/test_memory_derivation_store_p15.py`
- single-writer and cutover guards:
  - `tests/test_memory_single_writer_p15.py`
  - `tests/test_memory_p15_architecture_guards.py`
  - `tests/test_p15_cutover_phases.py`
  - `tests/test_p15_acceptance_gate.py`

These steps run through `python -m pytest` (repository pytest.ini provides the
cross-platform pythonpath) and `pytest` is added to the pinned test
dependency install. A first dual-platform run exposed that a
`PYTHONPATH=.:src` env form is not portable to Windows; the pytest-based form
replaced it and both platforms pass.

## Regression evidence

- targeted guard suites: 40 passed
- store closeout + P17 suites: 119 passed + 23 subtests
- full local pytest: 2869 passed, 812 subtests passed, 17 skipped

The only remaining full-suite failures are environment artifacts, not M3
regressions:

  - `test_release_after_pack_binding` requires the local `app/node_modules`
    Electron toolchain and fails identically on the `main` baseline worktree;
  - the two P17-M1 closed-world tests fail only when the full suite has
    already imported the V3 backend and created `__pycache__` under
    `app/backend/tiangong-backend/v3`; they pass on a clean tree and in both
    Architecture Gate platform runs.

## Explicit non-changes

M3-05 does not change:

- SQLite file/path safety contract or connection lifecycle authority
- schema v17 or migration SQL/order
- Memory contract semantics or privacy deletion semantics
- Gateway UoW seam semantics (COMMIT-failure rollback preserved)
- Life Runtime / scheduler / identity authority
- Total Gateway execution authority
- A0-A5 gate behavior
- `main`

## Construction lineage

No new construction workflow was needed for M3-05: the changes are guard/test
anchor repairs plus permanent Gate coverage, committed directly to the formal
M3 branch and validated by the dual-platform Architecture Gate.

## Outcome

P17-M3 is complete at 5/5: the Life store is decomposed into connection,
schema and Memory repository boundaries; the Gateway store owns a
mechanics-only Unit-of-Work seam; and the permanent Architecture Gate now
locks the single-writer, cutover and store regression contract on both
platforms. The three-store authority structure is:

- Life store: one connection authority, one schema authority, one Memory
  repository, one facade
- Gateway store: one connection, one lock, one UoW transaction seam
- World Understanding: unchanged single-ingress authority

Next frozen stage: **P17-M4 — Architecture Gate + full-chain regression**
(per the P17 master plan; M3 itself is closed).