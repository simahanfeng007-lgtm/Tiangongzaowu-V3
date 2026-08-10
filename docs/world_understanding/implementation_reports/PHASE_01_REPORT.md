# World Understanding Phase 01 Report

## 1. Phase

- Phase: **P1 — Contracts First**
- Implementation branch: `agent/world-understanding-v0.1`
- Parent at phase start: `8c139c9a04d662213fb98db0820dd00e6d221f31`
- P1 contract code commit: `f6cf400351f95fe420704f1afe1ca344676cb663`
- Baseline main inherited from P0: `a918b3606e18e8e9eec4395e0dbd9dce4ae79120`

This phase intentionally adds **contracts and contract tests only**. It does not attach World Understanding to Runtime, Gateway, zongdiaodu, Self-Will, persistence, background loops, tools, LLM calls, or network access.

## 2. Scope implemented

Added one repository-native contract package:

`src/contracts/world_understanding/`

Modules:

- `__init__.py`
- `_base.py`
- `authority.py`
- `cognition_compat.py`
- `context_packet.py`
- `curiosity.py`
- `derivation.py`
- `entity.py`
- `event.py`
- `hypothesis.py`
- `ingress.py`
- `inquiry.py`
- `known.py`
- `observability.py`
- `outputs.py`
- `prediction.py`
- `query.py`
- `relation.py`
- `scope.py`
- `source.py`
- `state.py`
- `time.py`
- `transform_metrics.py`
- `world_cut.py`

Added contract regression test:

`tests/test_world_understanding_contracts.py`

## 3. Roadmap contract coverage

P1 now defines the frozen contract surface for:

1. `WorldIngressEnvelope` with `SOURCE_RECORD` / `CONTEXT_REQUEST`
2. `SourceKind` / `AuthorityDomain`
3. `DirectKnownRecord`
4. `DerivedKnownRecord`
5. `WorldScope`
6. `WorldTime`
7. `WorldCut`
8. `WorldEvent`
9. `ObservabilityState`
10. `WorldEntity`
11. `EntityResolutionCandidate`
12. `WorldRelation`
13. `RelationMaterializationClass`
14. `WorldHypothesis`
15. `WorldState`
16. `WorldPrediction` / `PredictionOutcome`
17. `WorldQuery`
18. `WorldContextPacket`
19. `WorldContextItem` / `ExpansionHandle`
20. `WorldCuriosity`
21. `KnowledgeGap`
22. `WorldInquiry`
23. `InquiryOutcome`
24. `DerivationRef` / `DerivationEdge`
25. `TransformCostObservation`
26. `TransformQualityProfile`
27. `WorldContextOutputPort`
28. `WorldInquiryOutputPort`

The test surface counts `WorldContextItem` and `ExpansionHandle` separately, therefore the import coverage list contains 29 concrete symbols for the Roadmap's 28 contract groups.

## 4. Frozen invariants

### 4.1 Stable identity, revision and content hash

- Stable logical IDs use deterministic canonical-hash derivation.
- Mutable-head records separate stable slot identity from revision/content hash.
- Revision lineage rejects invalid genesis/supersede combinations where defined.
- `WorldRecordRef` binds exact record id + revision + SHA-256.

### 4.2 Time semantics

`WorldTime` separates:

- `valid_from_ms` / `valid_until_ms`
- `observed_at_ms`
- `recorded_at_ms`

The contract rejects inverted validity intervals and observations recorded before they occurred.

### 4.3 Truth vs epistemic state

Truth vocabulary remains:

- `TRUE`
- `FALSE`
- `UNKNOWN`
- `CONFLICTED`

Epistemic vocabulary remains independently:

- `CURRENT`
- `STALE`
- `CHALLENGED`
- `REVERIFYING`
- `RETIRED`

`STALE` is not encoded as a truth value.

### 4.4 ContextRequest cannot become reality evidence

`WorldIngressEnvelope(envelope_kind=CONTEXT_REQUEST)`:

- requires `source_kind=CONTEXT_REQUEST`
- cannot carry native reality authority
- `may_authorize=false`
- `may_execute=false`
- `empirical_evidence_weight_milli=0`

`DirectKnownRecord` explicitly rejects both `CONTEXT_REQUEST` and `UNCLASSIFIED_SOURCE` source kinds.

This is a contract-level fail-closed rule rather than a later routing convention.

### 4.5 WorldContextPacket cannot authorize

The packet contract freezes:

- `projection_authority=context_only`
- `context_only=true`
- `authorizes=false`
- `confirms=false`
- `changes_risk=false`
- `may_execute=false`
- `empirical_evidence_weight_milli=0`

Expansion handles remain scope/principal/privacy bound and context-only.

### 4.6 WorldInquiry cannot execute

The inquiry contract freezes:

- `authorization=NONE`
- `may_execute=false`
- `may_call_tools=false`
- `may_authorize=false`
- `empirical_evidence_weight_milli=0`

An accepted/resolved inquiry outcome cannot become self-evidence: it requires independent resulting source/observation/evidence references before a resolved ACCEPT outcome can close.

### 4.7 Prediction cannot become Evidence

`WorldPrediction` and `PredictionOutcome` freeze:

- `evidence_authority=none`
- `empirical_evidence_weight_milli=0`
- `may_authorize=false`
- `may_execute=false`

A prediction marked `RESOLVED` must reference real outcome observations.

### 4.8 Authority is domain/scope/time specific

`AuthorityBinding` binds:

- authority domain
- proposition type
- world scope hash
- validity interval
- authority ceiling

Empirical evidence weight cannot exceed the authority ceiling. Only an explicitly native authorization-source binding may set `may_authorize=true`.

### 4.9 Model output remains non-empirical by default

- Hypotheses are `hypothesis_only`, evidence authority `none`, empirical weight `0`.
- Model-assisted relations cannot carry positive empirical evidence weight.
- Curiosity / KnowledgeGap / Derivation / query / projection telemetry cannot bootstrap reality authority.

### 4.10 World Graph and Derivation DAG remain separate

`WorldRelation` defines world structure.

`DerivationRef` / `DerivationEdge` define lineage and transformation provenance.

Derivation records have zero empirical evidence weight and cannot target the exact same record revision they consume.

### 4.11 Existing Cognition contracts are not copied

P1 does not import the old World Cognition branch implementation into this branch.

`cognition_compat.py` only defines `CognitionStatementRef`, which requires exact cognition id + revision + statement SHA-256 agreement with its `WorldRecordRef`.

The existing Cognition Core remains a future internal L5 ownership target and is not exposed as a second public World Understanding facade.

## 5. Verification actually executed

### 5.1 Isolated repository-faithful contract harness

Environment:

- Python: `3.13.5`
- Pydantic: `2.13.4` (same pin as current repository `pyproject.toml`)
- Repository canonical serializer and `ContractModel` semantics reused.

Executed checks:

1. `python -m compileall` on the P1 contract package — **PASS / rc=0**.
2. Full isolated P1 contract suite — **25/25 PASS**.
3. Compact committed regression test equivalent — **12/12 PASS in 0.33s**.
4. JSON Schema generation across the public Pydantic model set — **36 models generated successfully** in the larger isolated harness.
5. Serialization round-trip — **PASS**.
6. Canonical hash determinism — **PASS**.
7. Invalid enum rejection — **PASS**.
8. ContextRequest → DirectKnown laundering attempt — **REJECTED as required**.
9. Unclassified source authority laundering attempt — **REJECTED as required**.
10. Context packet authorization attempt — structurally impossible through frozen `Literal[False]` fields.
11. Inquiry tool/execution authority — structurally disabled.
12. Prediction evidence authority — structurally disabled.

### 5.2 GitHub post-commit verification

After moving the implementation branch to the P1 code commit, compare against the P0 head showed:

- `ahead_by=1`
- `behind_by=0`
- exactly 24 new `src/contracts/world_understanding/*.py` files
- exactly 1 new `tests/test_world_understanding_contracts.py`
- **no Runtime files changed**
- **no Gateway files changed**
- **no zongdiaodu files changed**
- **no persistence/runtime attachment added**

Critical committed files were read back from GitHub after commit, including:

- public `__init__.py`
- `context_packet.py`
- `inquiry.py`
- committed contract test

Their final GitHub content preserves the intended non-authorizing boundary.

## 6. Verification not executed

The following are **NOT RUN** and are intentionally not reported as PASS:

1. Full repository pytest/runtime regression at commit `f6cf400...`.
   - This execution environment does not have an authenticated local repository checkout.
   - `gh` CLI is not installed in the container.
   - GitHub Actions query for this exact commit returned `total_count=0`; there is no CI result to reuse.

2. mypy/typecheck.
   - Current repository `pyproject.toml` contains no mypy configuration.
   - Repository code search found no configured mypy contract.
   - Therefore this gate is recorded as **not configured / not run**, not PASS.

3. Windows-specific runtime regression.
   - P1 contains pure contracts only and does not alter runtime code, but Windows end-to-end execution was not performed in this phase.

## 7. Gate status

### P1 contract gate

**PASS on the isolated contract harness and GitHub structural verification.**

Confirmed:

- Contract package surface exists.
- Frozen authority/evidence constraints are enforced by type validation.
- ContextRequest cannot become Direct Known.
- WorldInquiry defaults to zero authorization/execution authority.
- WorldContextPacket is non-authorizing context-only data.
- Prediction is not Evidence.
- Canonical serialization/hash semantics are deterministic in the executed harness.
- Cognition is referenced rather than duplicated.
- P1 does not alter the existing execution architecture.

### Full repository regression gate

**NOT RUN / PENDING environment with an authenticated checkout or CI run.**

This limitation does not justify bypassing the missing regression result; later phases must continue to report it explicitly until a full-repository execution is available.

## 8. P2 readiness

P1 provides enough contract surface to begin P2 — `WorldUnderstandingFacade` + one physical Ingress — while preserving these constraints:

- no Tool call from Ingress
- no LLM call from Ingress
- no network from Ingress
- no reality mutation from Ingress
- no second Runtime
- no second Total Gateway
- `CONTEXT_REQUEST` never enters Known/Evidence
- OFF mode remains no-op/lazy

P2 must consume these P1 contracts rather than redefining parallel schemas.
