# PHASE 08 REPORT — L4 SEMANTIC HYPOTHESIS PIPELINE

## 1. Status

P8 implements the frozen L4 Semantic Pipeline on the existing World Understanding branch.

The resulting semantic path is:

`K* / Entity / Relation / selected subgraph / stable Cognition / cognitive prior / uncertainty-conflict -> Semantic Admission (attention + VOI + optional existing Λ) -> provider-neutral SemanticModel -> strict JSON parser -> deterministic WorldHypothesis`

P8 is the first World Understanding phase in which an LLM may participate. The LLM can only propose hypothesis-shaped data. Deterministic P8 code constructs the canonical `WorldHypothesis` contract afterward.

P8 does **not** attach a new public World Understanding entry, does not create a second Runtime/Gateway/model transport, does not call tools, does not mutate Reality, does not write Cognition, and does not promote any semantic result into Evidence/Known/Relation truth.

## 2. SHA / branches

- Implementation branch: `agent/world-understanding-v0.1`
- P8 rollback point / P7 final: `cc49db134260278eaec9414fea11dec0d51bec11`
- Main observed at P8 start and rechecked immediately before P8 commit: `da714694074acade7539a02de94e7c3265f788bd`
- P8 implementation commit: `5b2e9844c4f0e39bc068517b4bc4a7a7e420bf69`
- P8 implementation tree: `5b3f7e16cc376c1adf3ab9644a8cd07849426817`
- Branch update: fast-forward only; `force=false`

The P8 core compare from P7 final is exactly one commit, ten files, ahead by one, behind by zero.

## 3. Existing modules reused

P8 reuses without replacement:

- `src/contracts/world_understanding/hypothesis.py`
  - canonical `WorldHypothesis`;
  - empirical evidence weight hard-zero;
  - evidence authority `none`;
  - projection authority `hypothesis_only`;
  - no authorization / no execution.
- `src/contracts/world_understanding/transform_metrics.py`
  - common transform cost observation.
- `src/world_understanding/common/epistemic.py`
  - Γ validation and non-evidence-object validation.
- `src/world_understanding/common/rhythm.py`
  - existing Λ semantic queue/admission/backpressure.
- `src/world_understanding/common/budgets.py`
  - existing background/interactive budget reserve.
- `src/world_understanding/software_world/graph.py`
  - existing P6 sparse graph; P8 only reads a bounded selected subgraph.
- `src/world_understanding/cognition/l5.py`
  - existing stable Cognition read view; no Cognition write path.
- `app/backend/tiangong-backend/v3/jineng/http_kehuduan.py`
  - existing V3 LLM transport/configuration/API-key path;
  - P8 adapter depends on its public `scoped_tools(disable_tools=True)` and `llm_diaoyong(...)` seam.

## 4. New P8 files

Core implementation:

- `src/world_understanding/semantic/__init__.py`
- `src/world_understanding/semantic/admission.py`
- `src/world_understanding/semantic/selection.py`
- `src/world_understanding/semantic/inputs.py`
- `src/world_understanding/semantic/model.py`
- `src/world_understanding/semantic/pipeline.py`
- `src/world_understanding/semantic/v3_http_adapter.py`

Tests:

- `tests/test_world_understanding_p8_semantic_pipeline.py`
- `tests/test_world_understanding_p8_semantic_guards.py`

Plan:

- `docs/world_understanding/PHASE_08_SEMANTIC_PIPELINE_PLAN.md`

The two focused test files are a publishing split of one original local P8 test module. The split was required because the connector truncated a single ~22 KiB blob payload. Gate coverage was preserved and the exact split files were rerun before commit.

## 5. Modules explicitly not replaced or modified

P8 does not replace or modify:

- `WorldUnderstandingFacade` / the one physical ingress;
- Total Gateway;
- Runtime;
- Tool execution / Omni Body;
- Self-Will;
- P4 Known closure;
- P5 Γ/Λ implementations;
- P6 graph materialization;
- P7 Cognition consolidation/store/revision/stability;
- the existing V3 HTTP LLM client.

No second Runtime, Gateway, LLM HTTP client, API-key/configuration system, World Graph, Cognition store, or execution entry is introduced.

## 6. Semantic Admission

P8 implements fixed-point deterministic attention:

`A = 1 - Π(1 - w_j x_j)`

Initial configurable factors:

- novelty;
- prediction error;
- conflict;
- uncertainty;
- structural impact;
- life relevance.

P8 also implements VOI as:

`Expected Gap Reduction / Expected Cost`

Initial weights and floors are configurable conservative constants, not learned parameters.

When an existing P5 `RhythmPlane` is supplied, P8 submits only to queue class `SEMANTIC` and inherits existing Λ budget reserve/backpressure behavior. It does not create a parallel rhythm/budget mechanism.

## 7. Read-only semantic input

P8 constructs a bounded `SemanticInputBundle` from first-class refs.

Supported categories:

- KNOWN;
- ENTITY;
- RELATION;
- COGNITION;
- PRIOR;
- UNCERTAINTY;
- CONFLICT.

Hard behavior:

- Known passes existing Γ admissibility and canonical hash validation;
- selected graph is exact-scope, read-only and bounded by hop/entity/relation limits;
- Cognition input is only stable/core C2/C3/C4 read-view material and contributes zero new empirical authority;
- repeated identical source refs are deduplicated;
- conflicting duplicate ref payload/category fails closed;
- source text is serialized into the model payload as DATA, never treated as an instruction channel.

## 8. Strict model boundary

`SemanticModel` is provider-neutral. The model request contains:

- prompt version;
- schema version;
- non-authorizing system instruction;
- canonical world-data JSON;
- canonical payload hash.

The model output root is exactly:

`{"hypotheses": [...]}`

Each hypothesis may provide only:

- subject ref index;
- predicate;
- typed value;
- hypothesis kind;
- uncertainty;
- basis ref indices;
- counter ref indices;
- prior ref indices.

There are no model-writable fields for:

- empirical evidence weight;
- truth promotion;
- authority;
- execution;
- Tool invocation;
- Runtime mutation;
- Cognition transition;
- Evidence mutation.

Unknown/extra fields, invalid reference indices, invalid prior references, invalid identifiers, basis/counter overlap, malformed JSON, and oversized output fail closed.

## 9. WorldHypothesis materialization

Only deterministic P8 code constructs `WorldHypothesis` after strict parsing.

Every emitted object is fixed to:

- `proposal_origin=llm_synthesis`;
- `empirical_evidence_weight_milli=0`;
- `evidence_authority=none`;
- `projection_authority=hypothesis_only`;
- `may_authorize=false`;
- `may_execute=false`.

The model's uncertainty is preserved exactly. Basis/counter/prior refs are preserved as lineage. Different competing hypotheses coexist. Exact duplicate hypothesis objects are deduplicated by canonical hypothesis hash.

Every emitted hypothesis is rechecked through Γ's non-evidence-object validation.

P8 never directly writes a hypothesis into Known, World Relation, Cognition, Runtime, Gateway or Tool state.

## 10. Existing V3 LLM adapter

`V3HttpSemanticModel` is a thin adapter over an injected existing V3 `HttpKehuduan`-compatible client.

It:

1. creates no HTTP client;
2. reads no API key;
3. owns no provider configuration;
4. enters `client.scoped_tools(disable_tools=True)` before every semantic call;
5. calls the existing `client.llm_diaoyong(...)` seam;
6. converts existing `[LLM错误: ...]` / empty responses into `SemanticModelUnavailable`;
7. sends the semantic source bundle inside explicit DATA delimiters;
8. requires strict JSON output.

Therefore the first LLM participation in WU reuses the existing V3 transport rather than introducing a parallel model channel.

### Token measurement limitation

The current public `HttpKehuduan.llm_diaoyong()` seam returns text only. Provider usage is consumed internally and is not exposed as a caller return value.

For this adapter, prompt/completion token counts are therefore deterministic estimates and `token_measurement=ESTIMATED` is recorded explicitly. They are **not** represented as provider-exact usage.

### Model hash limitation

P8 records a deterministic model-descriptor binding hash over provider/model/adapter identity. This is provenance for the configured model route; it is **not** a claim to possess or hash inaccessible remote model weights.

## 11. Telemetry

`SemanticTrace` and `TransformCostObservation` record:

- attention;
- VOI;
- admission disposition/reason;
- model ref;
- model descriptor SHA;
- prompt version;
- schema version;
- prompt tokens;
- completion tokens;
- token measurement class;
- latency;
- all source refs;
- model output hash;
- resulting hypothesis refs / lineage;
- failure type;
- transform cost.

Trace empirical evidence weight is zero and trace itself cannot authorize or execute.

## 12. Defects found during P8

### 12.1 Failure metric identifier mismatch

During implementation, P8 initially attempted to propagate internal underscore-style reason codes directly into `TransformCostObservation.failure_type`, whose existing contract is an OpaqueId. P8 was corrected to map known failure classes to valid dotted identifiers and hash unknown reason text into a deterministic OpaqueId-safe suffix.

### 12.2 Parser validation hardening

Model-returned predicate/hypothesis-kind/model metadata needed parser-side validation so invalid output failed as a semantic rejection rather than leaking generic Pydantic validation exceptions. Parser validation was hardened before Gate execution.

### 12.3 Duplicate hypothesis defect — actual test failure

First focused P8 test run:

- 15 PASS;
- 1 FAIL.

Failure: two byte-equivalent model hypothesis proposals produced two identical `WorldHypothesis` objects. Although both retained empirical weight zero, duplicate semantic objects could later be misused as synthetic multiplicity.

Fix: canonical `hypothesis_sha256` deduplication inside the P8 pipeline.

After the fix the duplicate-proposal test passes and distinct competing hypotheses remain separate.

## 13. Tests actually executed

The final committed split P8 tests were rerun after implementation:

`python -m pytest -q tests/test_world_understanding_p8_semantic_pipeline.py tests/test_world_understanding_p8_semantic_guards.py`

Result in the local reconstructed WU focused harness:

- `18 passed in 0.14s`

P2 -> P8 focused combination was rerun with the exact committed P8 test versions:

- `163 passed in 4.65s`

P8 package compilation:

`python -m compileall -q src/world_understanding/semantic`

Result:

- PASS / exit code 0.

The focused P8 Gate covers:

- deterministic attention formula;
- bounded subgraph selection;
- repeated source-ref deduplication;
- stable Cognition/prior read-only input;
- adversarial output cannot set evidence/authority/execution;
- hard-zero `WorldHypothesis` authority;
- uncertainty/lineage preservation;
- competing hypotheses coexist;
- exact duplicates deduplicate;
- LLM unavailable creates no fake hypothesis and does not mutate L0-L3 graph;
- late model unavailability fails closed;
- low attention does not call model;
- existing Λ backpressure preserves interactive reserve;
- model/prompt/schema/token/latency/output/source/hypothesis telemetry;
- prior-index restrictions;
- invalid semantic identifier rejection;
- static semantic-package guard against Runtime/Gateway/Tool/HTTP/Cognition-write imports;
- prompt-injection source text remains serialized data;
- existing V3 adapter hard-disables tool exposure;
- existing V3 adapter error text maps to unavailable.

## 14. Tests not run / limitations

NOT RUN:

- full authoritative repository `pytest`;
- exact authenticated current-repository checkout regression;
- full legacy Cognition tests in an exact authoritative checkout;
- Windows runtime smoke;
- production Linux runtime smoke;
- real external provider/network/API-key semantic call;
- Runtime/Gateway integration (intentionally not attached in P8);
- Tool execution integration (forbidden in P8);
- prompt-context insertion into production conversation path;
- GitHub Actions — the P8 implementation commit currently has no combined status entries.

The local environment used for executable results is a reconstructed World Understanding focused harness, not a complete authoritative repository checkout. These limitations are not converted into PASS claims.

## 15. Frozen Gate result

Gate checks represented by actual focused tests are satisfied:

- adversarial prompt/output cannot become Evidence/authority/execution: PASS;
- repeated same source cannot become independent semantic evidence: PASS;
- LLM unavailable leaves L0-L3 untouched and creates no fake hypothesis: PASS;
- competing hypotheses coexist: PASS;
- uncertainty is preserved: PASS;
- semantic result does not directly modify Runtime: PASS;
- existing Λ backpressure protects interactive reserve: PASS;
- existing V3 semantic adapter disables tool exposure: PASS;
- required telemetry is captured, with token measurement truthfully classified: PASS.

**P8 gate: PASS WITH FULL-REPOSITORY / LIVE-PROVIDER TEST-EXECUTION LIMITATIONS RECORDED.**

Rollback point: `cc49db134260278eaec9414fea11dec0d51bec11`.

P9 has not been started.
