# P17-M2-05 Embedded Life Gateway Wiring Boundary Extraction

Date: 2026-08-14 (Asia/Singapore)

## Stage

P17-M2-05 — Embedded Life Callback / Gateway Wiring Boundary Extraction

This is the fifth and final planned stage of P17-M2 God Module Decomposition.

## Baseline

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Working branch: `agent/p17-m2-god-module-decomposition`
- M2-04 final repair-log baseline: `241f4cdd6b208896fe83573d17b5adc81b22d14b`
- `main` baseline remained `3d5f13b6816e27f9f182e65c5fd0023e63d4b5cf`
- No merge to `main` was performed.

## Objective

Close the remaining high-coupling Gateway-to-Life composition seam without changing the single-process architecture or moving any Life authority.

Before M2-05, `src/total_gateway/runtime.py` directly knew and invoked the concrete setter surface of `EmbeddedLifeRuntime`, including autonomy, learning, proactive, self-iteration, artifact, World identity, workspace and materialization bindings.

The target architecture for this stage was:

```text
Total Gateway assembly
        |
        v
Typed Life Gateway Wiring Contract
        |
        v
Existing EmbeddedLifeRuntime public setters
        |
        v
Existing Life callback fields / policies / scheduler
```

The new boundary is dependency-installation coordination only. It is not a second Runtime, scheduler, policy engine, state store, execution loop or callback implementation.

## Pre-change audit findings

### 1. Production assembly point

The actual production callback installation chain is in:

`src/total_gateway/runtime.py`

It directly invoked the following Life setter surface:

- `set_autonomy_decider`
- `set_learning_decider`
- `set_capability_patch_decider`
- `set_learning_share_writer`
- `set_greeting_writer`
- `set_proactive_decider`
- `set_proactive_expression_writer`
- `set_self_iteration_decider`
- `set_upgrade_executor`
- `set_artifact_action_catalog_provider`
- `set_artifact_publisher`
- `set_world_identity_provider`
- `set_proactive_world_provider`
- `set_capability_workspace_mapper`
- `set_capability_workspace_remover`
- `set_capability_workspace_marker`
- `set_artifact_invoker`
- `set_learning_materializers`

### 2. Setter timing is behaviorally relevant

`EmbeddedLifeRuntime` starts its existing scheduler during construction. Moving every callback install to one later batch would widen the interval in which the scheduler can run before a callback is installed.

M2-05 therefore preserves the original callback definition and installation positions. Only the expression used to install each dependency changes.

### 3. Raw callback fields are compatibility-sensitive

Production `app/backend/tiangong-backend/v3/hotfix_20260727.py` directly reads:

`getattr(self, "_autonomy_decider", None)`

Therefore M2-05 deliberately does not delete, rename or replace the existing raw callback fields in `EmbeddedLifeRuntime`.

### 4. Existing setters own real semantics

The Life setters are not all trivial aliases. For example, `set_cognition_decider` also updates an already-created cognition shadow when present.

M2-05 therefore delegates to the existing public setters instead of writing callback fields directly.

## Implementation

### 1. Added Life-owned typed wiring boundary

New authoritative file:

`src/life_service/embedded_runtime_wiring.py`

The module defines:

- `GatewayCallback`
- `OptionalGatewayCallback`
- `EmbeddedLifeGatewayBinding`
- `EmbeddedLifeGatewayWiringPort`
- `bind_embedded_life_gateway_callback(...)`
- `bind_embedded_life_learning_materializers(...)`

The module contains no `Any`, no `EmbeddedLifeRuntime` implementation, no scheduler, no Store open, no `CompleteLifeSystem`, no writer lease and no Total Gateway import.

### 2. Explicit typed binding enum

`EmbeddedLifeGatewayBinding` names the supported dependency slots, including:

- cognition
- autonomy
- learning
- learning-share expression
- proactive decision/expression/World read
- self-iteration
- upgrade execution
- greeting expression
- artifact catalog/publisher/invoker
- World identity projection
- capability workspace mapper/remover/marker
- capability patch drafting

This gives the assembly layer a stable typed vocabulary without making Total Gateway depend on concrete Life setter names.

### 3. Existing setter semantics remain authoritative

`bind_embedded_life_gateway_callback(...)` dispatches every binding to the existing `EmbeddedLifeRuntime` public setter.

It does not:

- assign `_autonomy_decider` or other raw fields directly;
- duplicate validation;
- duplicate Life locking;
- duplicate cognition-shadow update behavior;
- own fallback behavior;
- own decision policy.

All validation, locking, state mutation and side effects remain in the original setters.

### 4. Learning materializers remain paired

The historical paired call:

`set_learning_materializers(researcher=..., synthesizer=...)`

is preserved as one paired wiring operation through:

`bind_embedded_life_learning_materializers(...)`

The Life runtime still owns the public paired setter.

### 5. Total Gateway no longer knows Life setter names

`src/total_gateway/runtime.py` now imports the Life-owned wiring contract at the existing production assembly location and installs dependencies through:

`bind_embedded_life_gateway_callback(...)`

and:

`bind_embedded_life_learning_materializers(...)`

The final source contains no remaining direct:

`runtime.life_service.set_*(`

calls.

### 6. Installation order preserved

The original installation sequence remains:

1. autonomy decider
2. learning decider
3. capability patch decider
4. learning-share writer
5. greeting writer
6. proactive decider
7. proactive expression writer
8. self-iteration decider
9. upgrade executor
10. artifact action catalog provider
11. artifact publisher
12. World identity provider
13. proactive World provider
14. capability workspace mapper
15. capability workspace remover
16. capability workspace marker
17. artifact invoker
18. paired learning materializers

Callback definitions were not moved to a new end-of-constructor batch.

### 7. Life Runtime storage compatibility preserved

M2-05 does not modify `src/life_service/embedded_runtime.py`.

Existing raw fields remain, including `_autonomy_decider`, and existing public setters remain available for compatibility callers.

The production hotfix path therefore continues to observe the same field.

## Source authority and generated mirrors

Existing source ownership remains unchanged:

- `src/life_service` is the Life service authority;
- `src/total_gateway` is the Total Gateway authority.

No `source-ownership.json` change was needed.

The tracked Life runtime314 mirror was regenerated:

- `app/life-service/runtime314/life_service/embedded_runtime_wiring.py`
- `app/life-service/runtime314/life_service/.tiangong-generated-source.json`

The authoritative and runtime314 wiring modules are byte-identical.

Build-only python312 mirrors were also generated and compiled during candidate verification, but remain governed by the repository's existing build-only generated-target policy rather than becoming new tracked authorities.

## Permanent regression tests

Added:

`tests/test_embedded_life_p17_m2_05.py`

Six tests lock:

1. the wiring boundary is typed coordination only and cannot become a second Runtime/Store/Scheduler authority;
2. every supported binding routes through the existing public Life setter surface;
3. Total Gateway no longer contains direct `runtime.life_service.set_*` calls;
4. historical callback installation order is preserved;
5. raw Life callback storage and `hotfix_20260727.py` compatibility remain intact;
6. learning materializers remain one paired public setter operation.

## Permanent Architecture Gate

`.github/workflows/architecture-gate.yml` now permanently runs:

`python tests/test_embedded_life_p17_m2_05.py -v`

The compile gate now includes:

- `src/life_service/embedded_runtime_wiring.py`
- `src/total_gateway/runtime.py`

in addition to all prior M2 seams.

## Candidate validation history

### Candidate Run 1

- Run: `31754830025`
- Head: `7fcd3091dee649c0ed5a653c556b22cda7d9b1b1`
- Result: failed only at `git diff --check`

All architecture tests including all six M2-05 tests passed. The only defect was seven trailing-whitespace lines introduced by the deterministic call-prefix rewriter.

No semantic correction was needed.

### Candidate Run 2

- Run: `31754910641`
- Head: `4f8bfab0e0cdcca4df2241f6f51abb61445cac14`
- Result: success

Whitespace was normalized before writing the candidate runtime. All tests, compilation and `git diff --check` passed.

This run exposed a construction-only artifact collection issue: dynamic `git diff --name-only` omitted newly created untracked formal files. The code candidate itself was valid, but that artifact was not used as final byte evidence.

### Complete candidate Run 3

- Run: `31755037745`
- Head: `a5afe7a754452d137a981fdb9a8825d3c9ef33b1`
- Result: success

The collector was changed to an explicit formal-file whitelist.

Final complete candidate artifact:

- Artifact ID: `9202396168`
- Digest: `sha256:8f8c2bc46a832e17ef150fe28610617d0d91e908271e5c3c2adee14edca13a6a`

The downloaded ZIP SHA256 matched GitHub's artifact digest exactly.

## Verified blob materialization

Materialization Run:

- Run: `31755120826`
- Construction head: `0b075fc535e363f177db699f428d086aae7fb24c`
- Result: success

The workflow rebuilt the candidate, synchronized mirrors, repeated all M1/M2 regression tests, compiled authoritative/build mirrors, passed `git diff --check`, then created only unreferenced Git blobs.

Verified blob identities:

- `.github/workflows/architecture-gate.yml` — `535d8a28865cdb8ae8116881f7461cde459a7e3b`
- `src/life_service/embedded_runtime_wiring.py` — `36257dcbda967d9a91894c23a42c70ee0fddbf0c`
- `app/life-service/runtime314/life_service/embedded_runtime_wiring.py` — `36257dcbda967d9a91894c23a42c70ee0fddbf0c`
- `app/life-service/runtime314/life_service/.tiangong-generated-source.json` — `53bc54eb200a2a8f17cabf2ac816a823b4e6bdf4`
- `src/total_gateway/runtime.py` — `4f22fbd2cfc9b4660ddd54d53d3b69febd48d6cc`
- `tests/test_embedded_life_p17_m2_05.py` — `7b2e421d93037b7a8d0018d14d4e702bdc1dbdb5`

These six CI-produced Git blob SHAs matched the locally recomputed Git blob identities from the complete read-only candidate artifact 6/6.

## Clean implementation commit

Clean implementation:

`d94e2e78372f528b7a5f3948e3b9a49ac1c95267`

Parent:

`241f4cdd6b208896fe83573d17b5adc81b22d14b`

Because M2-05 construction was kept on the isolated branch `agent/p17-m2-05-construction`, the formal M2 branch could fast-forward directly to the clean implementation. No second-parent construction closure was required for this stage.

## Final implementation diff from M2-04

Exactly six formal repository files changed:

1. `.github/workflows/architecture-gate.yml`
2. `src/life_service/embedded_runtime_wiring.py`
3. `src/total_gateway/runtime.py`
4. `app/life-service/runtime314/life_service/embedded_runtime_wiring.py`
5. `app/life-service/runtime314/life_service/.tiangong-generated-source.json`
6. `tests/test_embedded_life_p17_m2_05.py`

Diff statistics:

- architecture gate: `+4 / -1`
- authoritative wiring module: `+136`
- runtime314 wiring mirror: `+136`
- Total Gateway runtime: `+77 / -18`
- M2-05 tests: `+99`
- generated marker: metadata-only update

No construction workflow or candidate script exists in the formal M2 Tree.

## Permanent implementation Architecture Gate

Run:

- Run ID: `31755185406`
- Head: `d94e2e78372f528b7a5f3948e3b9a49ac1c95267`
- Conclusion: `success`
- Ubuntu: success
- Windows: success

Both operating systems passed:

- Source Authority topology;
- generated-source mirror verification;
- P17 M1 regression — 13 tests;
- P17 M1-03 regression — 5 tests;
- P17 M2-01 regression — 6 tests;
- P17 M2-02 regression — 6 tests;
- P17 M2-03 regression — 7 tests;
- P17 M2-04 regression — 6 tests;
- P17 M2-05 regression — 6 tests;
- compilation of all permanent M2 seams.

## Explicit non-changes

M2-05 did not modify:

- `src/life_service/embedded_runtime.py`;
- `EmbeddedLifeRuntime` construction or lifecycle;
- existing raw callback field names;
- existing public Life setter behavior;
- `EmbeddedLifeScheduler`;
- `_scheduler_tick()`;
- Life Store or schema;
- Memory SSoT;
- World Understanding authority;
- autonomy/proactive/learning/self-iteration policy semantics;
- model callback implementations;
- Tool Executor;
- A0-A5 Authority Gate semantics;
- public startup entrypoints;
- `main`.

## M2 closeout

P17-M2 planned decomposition is now complete 5/5:

1. M2-01 — Zongdiaodu Composition / Lifecycle Extraction
2. M2-02 — Turn Orchestration / Tool Loop Boundary Extraction
3. M2-03 — Tool Result / Continuation Boundary Extraction
4. M2-04 — Embedded Life Scheduler / Bootstrap Lifecycle Boundary Extraction
5. M2-05 — Embedded Life Callback / Gateway Wiring Boundary Extraction

Across M2, God-module responsibilities were peeled into explicit typed seams while preserving the single-process modular-monolith architecture and existing authority ownership.

M2 intentionally did not attempt a broad rewrite of the remaining large Store or scheduler business modules. Those require separate authority/schema/caller analysis rather than being folded into a God-module refactor.

## Result

P17-M2-05 implementation is complete, and P17-M2 reaches its planned 5/5 architecture decomposition target.

The final repair-log commit must still pass the permanent Ubuntu + Windows Architecture Gate before this stage is considered repository-closed.
