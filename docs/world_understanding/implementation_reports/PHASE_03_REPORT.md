# PHASE 03 REPORT — SOURCE COMPILER + LIFE ISOLATION HARDENING

## 1. Status

P3 is implemented as one integrated phase:

`P3 = Source Compiler + Life Isolation Hardening`

The requested P3.5 is not a side system and is not a second engine. Its invariants were merged into P3 before Known Closure.

## 2. SHA / branch

- Implementation branch: `agent/world-understanding-v0.1`
- Start SHA: `757d9a018540b8559181f6e2a461bf5c3cacca61`
- Main SHA re-verified after implementation: `ae3404b3300c09a0de0f7782e7d739fe24c93c05`
- P3 implementation SHA: `f0483aa749babcf6394064f7fbbe9f82d6f9c567`
- P3 implementation tree: `fe9fe6d8895a4b9c26bc384dabcde22f1a736c02`
- Rollback point: `757d9a018540b8559181f6e2a461bf5c3cacca61`
- Branch update: fast-forward only; `force=false`
- After P3: implementation branch `behind main = 0`

## 3. Changed files

Contracts:

- `src/contracts/world_understanding/ingress.py`
- `src/contracts/world_understanding/curiosity.py`
- `src/contracts/world_understanding/inquiry.py`
- `src/contracts/world_understanding/prediction.py`
- `src/contracts/world_understanding/derivation.py`

Shared engine / boundary:

- `src/world_understanding/facade.py`
- `src/world_understanding/ingress/__init__.py`
- `src/world_understanding/ingress/compiler_registry.py`
- `src/world_understanding/ingress/compiler_boundary.py`
- `src/world_understanding/ingress/router.py`
- `src/world_understanding/ingress/validation.py`
- `src/world_understanding/scope_guard.py`
- `src/world_understanding/source_adapters.py`

Source compilers:

- `src/world_understanding/source_compilers/__init__.py`
- `src/world_understanding/source_compilers/base.py`
- `src/world_understanding/source_compilers/p3.py`

Tests / plan:

- `tests/test_world_understanding_contracts.py`
- `tests/test_world_understanding_ingress.py`
- `tests/test_world_understanding_p3_sources_life_isolation.py`
- `docs/world_understanding/PHASE_03_SOURCE_COMPILER_LIFE_ISOLATION_PLAN.md`

GitHub compare of start -> implementation SHA shows no `app/backend/...` Runtime/Gateway/FactKernel/ToolResult native implementation file changed.

## 4. Life isolation hardening actually landed

### Ingress

- `WorldIngressEnvelope.life_id` is required, not optional.
- `envelope.life_id == envelope.scope_hint.life_id` is enforced.
- if top-level `principal_scope_hash` exists, it must equal `scope_hint.principal_scope_hash`.
- mismatch is fail-closed; no side is auto-selected.

### Shared engine

- one `WorldUnderstandingFacade` remains the public physical surface.
- facade holds only `_enabled` and `_ingress`; it has no current-life/current-world mutable slot.
- one shared `CompilerRegistry` serves all lives.
- registry stores only `_lock` and `_compilers`; no `current_life`, `current_world`, or `last_life_state`.

### Compiler boundary

All compiler results pass a uniform post-compiler validation boundary.

A `DirectKnownRecord` must preserve:

- input `life_id`
- input `world_scope_hash`
- input `principal_scope_hash`
- input `source_envelope_id`
- input `source_kind`
- input `source_native_id`
- input `source_payload_hash`

A compiler that returns another life's scope is rejected with `SCOPE_MISMATCH`.

### Stable identity

`world_id` and `world_scope_hash` already include `life_id`; `DirectKnownRecord.known_id` includes `world_scope_hash`. Therefore the same source payload/native id/proposition under Life A and Life B produces different scope hash and different Known identity.

### State contracts tightened

- DirectKnown / DerivedKnown: already `WorldScope` bound.
- Event / Entity / Relation / Hypothesis / WorldState / WorldPrediction: already scope-bound.
- WorldCuriosity: migrated from standalone `life_id` to `scope: WorldScope` as single life identity source.
- InquiryOutcome: now carries `scope: WorldScope` and scope participates in stable id derivation.
- PredictionOutcome: now carries `scope: WorldScope` and scope participates in stable id derivation.
- DerivationRef / DerivationEdge: now carry `scope: WorldScope` and scope participates in stable id derivation.
- ContextPacket / WorldInquiry: remain `scope: WorldScope` with no duplicate top-level life id.
- existing Cognition compatibility reference already carries explicit life/scope/principal identity; P7 ownership is unchanged.

## 5. P3 Source Compiler set

Configured deterministic compilers:

`RUN_CONTEXT`, `USER_CONVERSATION`, `SYSTEM_GOVERNANCE`, `RUNTIME_ENVIRONMENT`, `AUTHORIZATION`, `FACT_EXECUTION`, `TOOL_RESULT`, `FILESYSTEM`, `GIT_CODE`, `WEB_EXTERNAL`, `DESKTOP_UI`, `MEMORY`, `KNOWLEDGE`, `CONTEXT_CONTINUITY`, `AUTONOMY`, `CHAIN_EVENT`, `EXECUTION_INTEGRITY`, `METRICS`, `MIGRATION_AUDIT`, `MODEL_OUTPUT`.

Compiler instances contain configuration only; life/world state is explicit input from the envelope/scope.

Semantic safeguards include:

- user input -> `USER_SAID`, never direct reality predicates;
- web -> `WEB_SOURCE_CLAIMS`, empirical reality weight 0;
- model -> `MODEL_PROPOSED`, empirical reality weight 0;
- memory -> `MEMORY_RECORDED`, no authority upgrade;
- autonomy -> decision record, empirical reality weight 0;
- authorization -> authorization decision record, not execution completion;
- chain lifecycle -> chain event, not proof of real-world goal completion;
- ToolResult declared write -> `TOOL_WRITE_DECLARED`, empirical reality weight 0;
- ToolResult filesystem write/delete facts only when authoritative observed write evidence exists;
- filesystem existence/hash only from explicit filesystem observation payload fields.

## 6. P4 invariant frozen now, but P4 not implemented

P3 adds only a precondition guard:

`K*_life = Closure(K0_life)`

Mixed-life parents -> `SCOPE_MISMATCH` -> no derivation.

No deterministic closure engine, fixed-point iteration, rule graph, or DerivedKnown production was implemented in P3.

## 7. Tests actually executed

### 7.1 Current source/life core reproducible run

A reconstructed current P3 core overlay was created from the P2/P3 implementation files. High-level output-contract tests requiring the complete authoritative P1 package were excluded from this isolated core run; they are listed separately below.

Exact command executed:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q /mnt/data/wu_p3_exact_core/tests/test_world_understanding_ingress.py /mnt/data/wu_p3_exact_core/tests/test_world_understanding_p3_core.py
```

Actual result:

```text
51 passed in 0.96s
```

This run covers P2 ingress behavior plus P3 source/life core behavior, including:

- deterministic compiler behavior across configured source kinds;
- Life A -> Life A DirectKnown;
- Life B -> Life B DirectKnown;
- same payload/native id across A/B -> different scope/known identity;
- missing life rejected;
- envelope/scope life mismatch rejected;
- principal-scope mismatch rejected;
- malicious compiler cross-life output rejected;
- shared Registry no life-private state;
- 1000 Life A + 1000 Life B concurrent events through one Facade/Registry -> zero observed cross-life contamination;
- source semantic non-laundering checks;
- ToolResult observed/declaration separation;
- filesystem observation semantics;
- P4 mixed-life parent guard rejection.

### 7.2 Current core syntax compilation

Exact command:

```text
/opt/pyvenv/bin/python -m compileall -q /mnt/data/wu_p3_exact_core/src
```

Actual exit code: `0`.

### 7.3 Full P3 output-contract collection attempt

Exact command executed against the reconstructed incomplete fixture:

```text
PYTHONPATH=/mnt/data/wu_p3_commit/src:/mnt/data/wu_p3_integrated/src /opt/pyvenv/bin/python -m pytest -q /mnt/data/wu_p3_commit/tests/test_world_understanding_p3_sources_life_isolation.py
```

Actual result: collection stopped because the reconstructed P1 package does not contain the complete exported ContextPacket/Inquiry contract surface. This is a fixture-completeness limitation and is **not** counted as PASS.

A targeted `PredictionOutcome` import check was also attempted; it stopped because the reconstructed `_base.py` lacked the authoritative P1 `PredictionId` export. The final GitHub contract file itself was syntax-checked during construction, but no full authoritative-package import regression is claimed.

### 7.4 GitHub Actions

Query for exact P3 implementation SHA returned:

```json
{"total_count":0,"workflow_runs":[]}
```

Therefore exact-commit CI is `NOT RUN / NOT AVAILABLE`, not PASS.

## 8. Tests not executed

- full repository pytest from an authenticated local checkout: NOT RUN;
- complete P1 contract regression after the final scope additions: NOT RUN in authoritative checkout;
- production Runtime E2E automatic producer -> WU ingestion: NOT RUN / not wired;
- Windows production smoke: NOT RUN;
- Linux production smoke: NOT RUN;
- Self-Will / Total Gateway integration: intentionally not implemented;
- P4 closure: intentionally not implemented.

## 9. Contract compatibility impact

This phase intentionally introduces a pre-Runtime World Understanding contract migration:

1. `WorldIngressEnvelope.life_id`: optional -> required.
2. P1/P2 test constructors updated to supply life/principal consistent with `scope_hint`.
3. `WorldCuriosity.life_id` replaced by `WorldCuriosity.scope` to avoid duplicate life identity sources.
4. `InquiryOutcome` gains `scope` and its stable-id derivation becomes scope-sensitive.
5. `PredictionOutcome` gains `scope` and its stable-id derivation becomes scope-sensitive.
6. `DerivationRef` / `DerivationEdge` gain `scope` and their stable-id derivation becomes scope-sensitive.

No existing V3 Runtime/Gateway contract was modified.

## 10. Deliberate limitation / source attachment status

P3 implements deterministic source compilers and `build_post_commit_source_envelope(...)` as the thin post-commit adapter seam.

It does **not** patch native Runtime/Gateway producer callsites in this commit. This preserves native source ownership and the explicit life-hardening gate that Runtime/Gateway remain unchanged.

Therefore:

- Source -> DirectKnown compilation boundary: implemented.
- Life-scoped compiler safety: implemented.
- Native automatic post-commit forwarding from every live V3 producer: **deferred and not claimed complete**.

The future attachment must occur only at verified native commit/finalization points and must inherit `life_id` from RunContext/execution lineage, never infer it from workspace/path.

## 11. OFF behavior

Unchanged in intent and structure: when WU is disabled no ingress subsystem is instantiated, and no world DB, directory, worker/thread, Tool, Runtime or LLM is started by WU.

## 12. Gate

### Life isolation / compiler gate

**PASS WITH AUTHORITATIVE-REPOSITORY TEST EXECUTION LIMITATIONS RECORDED.**

### Native automatic source-producer wiring

**DEFERRED / NOT CLAIMED COMPLETE.**

P4 must start from the per-life closure invariant and must never implement global closure followed by life filtering.
