# P17-M2-04 Embedded Life Lifecycle Boundary Extraction

Date: 2026-08-14 (Asia/Singapore)

## Stage

P17-M2-04 — Embedded Life Scheduler / Bootstrap Lifecycle Boundary Extraction

## Baseline

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Working branch: `agent/p17-m2-god-module-decomposition`
- M2-03 final log baseline: `a483caf9637ffde908f493145c036c5a711936db`
- `main` baseline remained `3d5f13b6816e27f9f182e65c5fd0023e63d4b5cf`
- No merge to `main` was performed.

## Objective

Begin decomposing the large authoritative Life host `src/life_service/embedded_runtime.py` without creating a second Life Runtime, second scheduler implementation, second writer, second persistence authority, or alternative startup chain.

This stage deliberately limits itself to host-level lifecycle coordination:

1. crash-left scheduler inflight projection recovery;
2. construction/start of the already-existing `EmbeddedLifeScheduler`;
3. failed-constructor cleanup ordering and diagnostics.

It does **not** move Life business algorithms, `_scheduler_tick()`, Store authority, Memory authority, identity authority, World authority, proactive decision policy, or external-action authority.

## Audit finding

Before M2-04, `EmbeddedLifeRuntime.__init__()` simultaneously owned:

- path safety checks;
- writer lease acquisition;
- `CompleteLifeSystem` construction;
- active identity selection/recovery;
- `LifeShadowStore` opening;
- cognition shadow construction;
- state loading;
- scheduler-heartbeat reconciliation;
- five crash-left inflight marker resets;
- journal/projection reconciliation;
- memory classification and memory-contract reconciliation;
- persistence of recovered projection state;
- `EmbeddedLifeScheduler` construction and start;
- partial-initialization rollback.

The first safe extraction seam was therefore lifecycle coordination rather than Store decomposition or scheduler business logic.

## Implementation

### 1. Added `src/life_service/embedded_runtime_lifecycle.py`

New authoritative source-owned module:

`src/life_service/embedded_runtime_lifecycle.py`

It is a lifecycle coordinator only and contains no `EmbeddedLifeRuntime` class, no second scheduler class, no Store opening, no `CompleteLifeSystem` construction and no writer-lease acquisition.

It imports and reuses the existing scheduler implementation:

`life_service.complete_scheduler.EmbeddedLifeScheduler`

### 2. Typed lifecycle ports

The new module introduces narrow structural ports:

- `SchedulerStopPort`
- `AuthorityStoreClosePort`
- `WriterLeaseReleasePort`

These ports exist only to express lifecycle cleanup dependencies. They do not own or replace the underlying objects.

### 3. Crash-left inflight recovery extraction

The historical constructor reset for these exact projection flags was moved into:

`recover_inflight_scheduler_flags(state)`

Preserved flags:

- `autonomy_decision_inflight`
- `learning_decision_inflight`
- `self_iteration_decision_inflight`
- `greeting_inflight`
- `proactive_decision_inflight`

The function only clears stale `True` inflight markers in loaded scheduler projection state and reports whether recovery occurred.

It does not synthesize a completed activity, append journal history, or mutate signed authority.

### 4. Scheduler startup extraction

Historical inline construction:

`EmbeddedLifeScheduler(self._scheduler_tick, interval_seconds=heartbeat_seconds)` followed by `.start()`

is now delegated to:

`start_embedded_scheduler(...)`

The existing `EmbeddedLifeScheduler` remains the single scheduler implementation. The same `_scheduler_tick` callback and the same `TIANGONG_LIFE_HEARTBEAT_SECONDS` value are used.

### 5. Partial initialization cleanup extraction

The constructor's failure cleanup is now delegated to:

`cleanup_partial_initialization(...)`

Historical cleanup order is preserved exactly:

1. scheduler stop;
2. authority store close;
3. writer lease release.

Cleanup failures are still collected and attached as a note to the original initialization exception rather than masking it.

### 6. Single Life Runtime authority preserved

`EmbeddedLifeRuntime` remains in:

`src/life_service/embedded_runtime.py`

The following authorities remain there and were not moved into the new lifecycle module:

- `LifeWriterLease.acquire(...)`
- `CompleteLifeSystem(...)`
- `LifeShadowStore.open(...)`
- authoritative state and projection ownership
- `_scheduler_tick()` and all Life activity semantics
- close/shutdown semantics outside the extracted constructor-failure seam

There is still one embedded Life Runtime and one scheduler implementation.

### 7. Generated runtime mirror synchronized

`src/life_service` is the source authority under the existing `life-service-runtime` ownership mapping.

After the source change, `scripts/sync-generated-sources.py --write` regenerated the tracked runtime314 mirror:

- `app/life-service/runtime314/life_service/embedded_runtime.py`
- `app/life-service/runtime314/life_service/embedded_runtime_lifecycle.py`
- `app/life-service/runtime314/life_service/.tiangong-generated-source.json`

The authoritative and runtime314 copies of `embedded_runtime.py` are byte-identical, and the lifecycle modules are byte-identical.

No second human-editable authority was introduced.

## Permanent regression tests

Added:

`tests/test_embedded_life_p17_m2_04.py`

Six regression cases lock the following properties:

1. lifecycle boundary is coordination only and cannot become a second Runtime/scheduler/Store authority;
2. the boundary reuses the existing `EmbeddedLifeScheduler` implementation;
3. the exact five historical inflight recovery keys remain preserved;
4. partial cleanup order remains scheduler → store → lease;
5. `embedded_runtime.py` delegates recovery/start/partial cleanup to the new seam;
6. `EmbeddedLifeRuntime`, writer lease acquisition, `CompleteLifeSystem` and `LifeShadowStore.open` remain in the existing authoritative runtime.

## Permanent Architecture Gate

`.github/workflows/architecture-gate.yml` now permanently runs:

`python tests/test_embedded_life_p17_m2_04.py -v`

and compiles both:

- `src/life_service/embedded_runtime.py`
- `src/life_service/embedded_runtime_lifecycle.py`

in addition to all previous M2 V3 seams.

## Candidate safety process

`embedded_runtime.py` is a large production file, so the change was generated with exact anchors rather than manually replacing the full file through chat.

The candidate builder required every target block to occur exactly once, then compiled the resulting authoritative file before validation.

The first temporary candidate workflow placed on the official M2 construction lineage was rejected by GitHub at workflow-parse level and produced no job. No source patch or candidate artifact executed in that failed run.

Construction was then isolated onto temporary branches:

- `agent/p17-m2-04-construction`
- `agent/p17-m2-04-materialize`

The official final Tree contains neither temporary workflow nor candidate script.

## Read-only candidate verification

Read-only candidate workflow:

- Run: `31730799490`
- Branch: `agent/p17-m2-04-construction`
- Head: `dcc477153f4fb6f5e3b96a8bb16a63778ef15820`
- Conclusion: `success`

Candidate artifact:

- Artifact ID: `9193139965`
- Digest: `sha256:80f309c95f311dea7092f87e4d825da2a9289b29544ef60a17af76ca31e86c2e`

The downloaded artifact digest matched GitHub's digest exactly.

## Verified blob identity

A separate materialization workflow rebuilt and revalidated the same candidate before creating only unreferenced Git blobs.

Materialization run:

- Run: `31731243802`
- Head: `04cf1b2f13535988009d479a8af6baf14e641264`
- Conclusion: `success`

Final verified blob identities:

- `.github/workflows/architecture-gate.yml` — `bf2e64e56c20f22b797cd45db7e19446393629d8`
- `src/life_service/embedded_runtime.py` — `63c5643e71b91525198ad6f433f0e3b7d339bf59`
- `src/life_service/embedded_runtime_lifecycle.py` — `18122fa0e18ba29b9aa820a315ffe113cda24ada`
- `app/life-service/runtime314/life_service/embedded_runtime.py` — `63c5643e71b91525198ad6f433f0e3b7d339bf59`
- `app/life-service/runtime314/life_service/embedded_runtime_lifecycle.py` — `18122fa0e18ba29b9aa820a315ffe113cda24ada`
- `app/life-service/runtime314/life_service/.tiangong-generated-source.json` — `c94205aad001bc62f5f86489bf223f60e2c5797f`
- `tests/test_embedded_life_p17_m2_04.py` — `de5984c74ba75f58a16329ae3c108c2bb971695b`

The CI-produced blob identities matched the locally recomputed Git blob hashes from the read-only artifact 7/7.

## Clean implementation

Clean implementation commit:

`afd00beb18fd2dd59f39fd724f0ed0ce118cee8d`

Construction-lineage closure commit:

`dd1b92fdfe0a073441f49b9680f9fc2302d651f6`

The closure commit uses:

- first parent: clean M2-04 implementation;
- second parent: the official construction lineage that had already been created;
- Tree: exactly the clean implementation Tree.

This permits a normal fast-forward without retaining temporary construction files in the final Tree.

## Final net diff from M2-03

Relative to `a483caf9637ffde908f493145c036c5a711936db`, the final M2-04 Tree changes exactly seven repository files:

1. `.github/workflows/architecture-gate.yml`
2. `src/life_service/embedded_runtime.py`
3. `src/life_service/embedded_runtime_lifecycle.py`
4. `app/life-service/runtime314/life_service/embedded_runtime.py`
5. `app/life-service/runtime314/life_service/embedded_runtime_lifecycle.py`
6. `app/life-service/runtime314/life_service/.tiangong-generated-source.json`
7. `tests/test_embedded_life_p17_m2_04.py`

For each authoritative/generated `embedded_runtime.py` copy, the stage diff is `+13 / -45`.

No temporary workflow or candidate script exists in the final Tree.

## Final Architecture Gate

Implementation/closure verification:

- Run: `31731406102`
- Head: `dd1b92fdfe0a073441f49b9680f9fc2302d651f6`
- Conclusion: `success`
- Ubuntu: success
- Windows: success

Both OS jobs passed:

- Source Authority topology;
- generated-source mirror verification;
- P17 M1 regression (13 tests);
- P17 M1-03 regression (5 tests);
- P17 M2-01 regression (6 tests);
- P17 M2-02 regression (6 tests);
- P17 M2-03 regression (7 tests);
- P17 M2-04 regression (6 tests);
- M2 seam compilation including authoritative Life runtime lifecycle files.

## Explicit non-changes

This stage did not modify:

- `EmbeddedLifeScheduler` implementation or its stop/quiescence rules;
- `_scheduler_tick()` business logic;
- Life heartbeat semantics or interval configuration;
- Life writer lease authority;
- identity authority or migration rules;
- `LifeShadowStore` authority/schema;
- Memory SSoT or memory lifecycle rules;
- World Understanding authority;
- proactive/autonomy/learning decision semantics;
- Total Gateway or A0-A5 gate semantics;
- `main`.

## Result

P17-M2-04 is complete.

The Life God Module now has an explicit typed lifecycle coordination seam while retaining one `EmbeddedLifeRuntime`, one writer authority and one existing `EmbeddedLifeScheduler` implementation.

Recommended next stage: **P17-M2-05 — Embedded Life Callback / Gateway Wiring Boundary Extraction**, focusing on the large family of gateway-owned callback setters and composition wiring without moving Life state or policy authority.
