# PHASE 08 — L4 SEMANTIC HYPOTHESIS PIPELINE PLAN

## 1. Baseline

- implementation branch: `agent/world-understanding-v0.1`
- P7 rollback point: `cc49db134260278eaec9414fea11dec0d51bec11`
- main observed at P8 start: `da714694074acade7539a02de94e7c3265f788bd`
- branch at P8 start: ahead of main, behind by zero.

P8 follows the frozen World Understanding roadmap. It is the first phase in which an LLM may participate inside World Understanding, but the model may produce only candidate `WorldHypothesis` objects. It receives no authority to establish reality, modify evidence, write Cognition, call Tool, or mutate Runtime.

## 2. Existing files reused

P8 reuses, and does not replace:

- `src/contracts/world_understanding/hypothesis.py`
  - canonical `WorldHypothesis` contract;
  - empirical evidence weight hard-zero;
  - `evidence_authority=none`;
  - non-authorizing/non-executing semantics.
- `src/contracts/world_understanding/transform_metrics.py`
  - common transform cost observation.
- `src/world_understanding/common/epistemic.py`
  - Γ validation and non-evidence-object enforcement.
- `src/world_understanding/common/rhythm.py`
  - Λ semantic queue, admission, debounce/backpressure.
- `src/world_understanding/common/budgets.py`
  - existing background/interactive budget reserve.
- `src/world_understanding/software_world/graph.py`
  - existing P6 sparse World Graph; P8 selects a bounded read-only subgraph.
- `src/world_understanding/cognition/l5.py`
  - stable Cognition L5 read view; no Cognition write path.
- existing V3 `app/backend/tiangong-backend/v3/jineng/http_kehuduan.py`
  - the already-owned real LLM transport/configuration path;
  - P8 adapter uses the public `scoped_tools(disable_tools=True)` + `llm_diaoyong(...)` seam by dependency injection;
  - P8 does not create a second HTTP client or second API-key/configuration system.

## 3. New files

Under `src/world_understanding/semantic/`:

- `__init__.py`
- `admission.py`
- `selection.py`
- `inputs.py`
- `model.py`
- `pipeline.py`
- `v3_http_adapter.py`

Tests:

- `tests/test_world_understanding_p8_semantic_pipeline.py`
- `tests/test_world_understanding_p8_semantic_guards.py`

## 4. Modules explicitly not replaced or modified

P8 does not replace or modify:

- `WorldUnderstandingFacade` / the one physical ingress;
- Total Gateway;
- Runtime;
- Tool execution/body;
- Self-Will;
- P4 Known closure;
- P5 Γ/Λ implementations;
- P6 graph materialization;
- P7 Cognition consolidation/store/revision/stability;
- the existing V3 HTTP LLM client.

No second Runtime, Gateway, LLM transport, World Graph, Cognition store, or execution entry is introduced.

## 5. Semantic admission

`SemanticFactors` covers frozen factors:

- novelty;
- prediction error;
- conflict;
- uncertainty;
- structural impact;
- life relevance.

Attention is deterministic milli arithmetic for:

`A = 1 - Π(1 - w_j x_j)`

VOI is represented as:

`Expected Gap Reduction / Expected Cost`

Initial weights/floors are configurable conservative constants, not learned parameters. If an existing P5 `RhythmPlane` is supplied, P8 submits only to queue class `SEMANTIC` and inherits existing budget reserve/backpressure behavior.

## 6. Model boundary

The provider-neutral `SemanticModel` contract receives:

- prompt version;
- schema version;
- non-authorizing system instruction;
- canonical JSON world-data payload;
- payload hash.

The model output schema contains only hypothesis proposal fields:

- subject reference index;
- predicate;
- typed value;
- hypothesis kind;
- uncertainty;
- basis/counter/prior reference indices.

There are no model-writable fields for empirical authority, truth promotion, authorization, execution, Tool calls, Runtime changes, Cognition transitions, or Evidence mutation.

All reference indices must point to supplied first-class refs. Invalid/extra fields fail closed.

## 7. Existing V3 model adapter

`V3HttpSemanticModel` is a thin adapter around an injected existing `HttpKehuduan`-compatible object.

It:

1. reuses the existing model channel;
2. enters `scoped_tools(disable_tools=True)` before every semantic model call;
3. sends the semantic source bundle as delimited DATA;
4. requires strict JSON output;
5. converts existing `[LLM错误: ...]` / empty responses to `SemanticModelUnavailable`;
6. creates no HTTP client and reads no API keys itself.

The current V3 public `llm_diaoyong()` seam returns text only and does not expose provider usage. P8 therefore records token counts from this adapter as `ESTIMATED`, never as exact provider usage. Provider-neutral implementations that have real usage may return `PROVIDER_USAGE`.

The P8 model SHA is a deterministic model-descriptor binding hash (provider/model/adapter identity), not a false claim to possess the remote model's weight hash.

## 8. WorldHypothesis materialization

Only deterministic P8 code constructs `WorldHypothesis` after strict parsing.

Hard invariants:

- `proposal_origin=llm_synthesis`;
- empirical evidence weight = 0;
- no authorization;
- no execution;
- source/basis lineage preserved;
- uncertainty preserved exactly;
- model/prompt/schema metadata retained;
- exact duplicate hypothesis objects deduplicated;
- different competing hypotheses may coexist;
- Γ validates every emitted hypothesis as a non-evidence object.

P8 never writes the hypothesis into Known/Relation/Cognition/Runtime directly.

## 9. Required telemetry

`SemanticTrace` / `TransformCostObservation` record:

- model ref and descriptor SHA;
- prompt version;
- schema version;
- prompt/completion tokens plus measurement class;
- latency;
- all source refs;
- model-output hash;
- output hypothesis refs/lineage;
- admission attention/VOI;
- failure type;
- transform cost.

## 10. Frozen Gate tests

P8 gate requires evidence that:

1. adversarial source/model output cannot create Evidence/authority/execution;
2. repeated same source does not become independent semantic evidence;
3. exact duplicate model proposals do not create duplicate semantic objects;
4. LLM unavailable leaves L0-L3 untouched and creates no fake hypothesis;
5. competing hypotheses coexist;
6. uncertainty is preserved;
7. semantic result does not directly modify Runtime;
8. Λ budget/backpressure still protects interactive reserve;
9. existing V3 adapter hard-disables tool exposure;
10. model/prompt/schema/token/latency/source/output/lineage telemetry is recorded.
