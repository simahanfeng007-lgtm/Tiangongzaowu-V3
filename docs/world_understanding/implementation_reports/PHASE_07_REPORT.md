# PHASE 07 REPORT — ABSORB EXISTING WORLD COGNITION AS L5

## 1. Status

P7 absorbs the already implemented off-main World Cognition contracts/core into the canonical World Understanding tree as L5.

The resulting architecture is:

`Known / Event / Entity / Relation / Hypothesis -> reference-only P7 bridge -> existing Cognition Core C0-C4 -> CognitionL5View`

P7 does **not** create a second cognition engine. The original deterministic C0-C4 implementation is physically stored once under `src/world_understanding/cognition/`; the old `v3.world_cognition.*` path contains thin re-export modules only.

The only World Understanding physical attachment remains `WorldUnderstandingFacade.accept(...)`. P7 does not attach Cognition to Runtime, Gateway, prompt construction, Self-Will, Tool execution, or an LLM.

## 2. SHA / branches

- Implementation branch: `agent/world-understanding-v0.1`
- P7 baseline / P6 final: `09bd95f139f9beac12f08f98f05906d316b48ae7`
- Main observed at P7 start and rechecked before P7 commit: `da714694074acade7539a02de94e7c3265f788bd`
- Existing Cognition contracts source commit: `c777561bc51546dafe01fd68de25a332d25420d5`
- Existing Cognition core source commit: `a79a5b46a54258798bce3529b8424cb3b3ab4d2c`
- P7 implementation SHA: `0ee758a63cc32d1f86a10eab5752056f514ff81b`
- P7 implementation tree: `8bd58a766b6b195057585c30e8c2bafa217b4b97`
- Rollback point: `09bd95f139f9beac12f08f98f05906d316b48ae7`
- Branch update: fast-forward only; `force=false`

## 3. Existing source was reconciled, not branch-merged

The P0 baseline manifest classified World Cognition as off-main source that must be reconciled into the frozen World Understanding architecture. P7 therefore did not merge or cherry-pick the old Cognition branch wholesale.

Only the verified Cognition contracts/core/test blobs were selected and placed at their canonical P7 locations. This prevents unrelated historical backend/runtime files from entering the World Understanding implementation branch.

At P7 time the accessible Cognition branches were:

- `agent/world-cognition-contracts-v0.1`
- `agent/world-cognition-core-v0.1`
- `agent/world-cognition-core-v0.1-verify`

The `core-v0.1-verify` branch points to the same core commit as `core-v0.1`. No later separate backfill/governance/migration implementation branch was present in the verified repository view, so P7 did not invent such modules.

## 4. Original contracts absorbed unchanged

The following contract blobs were copied byte-for-byte from the existing Cognition source:

- `src/contracts/cognition_evidence.py` — `339b323e12475ab3b98c3d86a9e8ca473813cd0a`
- `src/contracts/cognition_prior.py` — `1d04d26e47daa256e7b7e4b9964b47ed4570c7e4`
- `src/contracts/cognition_revision.py` — `29ae6468fc20ea1d34071424c5b680bb76137266`
- `src/contracts/cognition_statement.py` — `6ea3b45af36fdec036a7d8252e22c8d51fee5ca1`

P7 therefore preserves the original cognition slot ID, revision ID, statement hash, evidence ID, prior ID, transition vocabulary and C0-C4 lifecycle validation.

No replacement Cognition contract hierarchy was introduced.

## 5. Original core absorbed unchanged

The following source blobs were copied byte-for-byte from `agent/world-cognition-core-v0.1` into `src/world_understanding/cognition/`:

- `consolidator.py` — `bc85c7c4f7cffff05fce29d4311f333126154c43`
- `evidence.py` — `326f7ef0bcdb26dabdfe5c6068594d7d7cbdaf6b`
- `facade.py` — `27a6565b81e3058935f53e9263da7918e4f95208`
- `priors.py` — `519327d91894800616cf49eb5dacd52c59628c14`
- `retrieval.py` — `524623178207a59d1e05443e111070d4f4e60a24`
- `stability.py` — `ab5908cecb08a1ac7388246a8c04955c35e0adbe`
- `store.py` — `cac18e9b79ad457e1a70a98fd7557edb0e66c72b`

Consequences:

- C0/C1/C2/C3/C4 transition mathematics are not reimplemented;
- evidence independence/correlation math is not reimplemented;
- CAS head semantics are not reimplemented;
- revision/protection/challenge/reverification logic is not reimplemented;
- retrieval and prior behavior are not reimplemented.

This is an absorption of the existing Cognition, not a parallel replacement.

## 6. C0-C4 semantics retained

The existing semantics remain:

- C0 -> CANDIDATE
- C1 -> PROVISIONAL
- C2 -> STABLE
- C3 -> CORE
- C4 -> protected CORE

The existing Cognition core already treats C4 as a protection class rather than a stronger empirical evidence class. Entering C4 requires explicit system authority or migration; ordinary deterministic policy cannot silently promote C3 to C4.

P7 does not reinterpret C4 as world truth, authorization, or execution authority.

## 7. Canonical package surface

`src/world_understanding/cognition/__init__.py` exports:

- the existing consolidator/evidence/retrieval/stability/store primitives;
- the new P7 reference bridge;
- the new L5 view.

It deliberately does **not** export `WorldCognitionFacade`.

The old facade implementation remains in `src/world_understanding/cognition/facade.py` only so existing callers/tests can be kept compatible through the legacy path. It is not registered as a second World Understanding ingress or Runtime attachment.

## 8. Legacy compatibility without dual implementation

P7 adds `app/backend/tiangong-backend/v3/world_cognition/` compatibility modules.

Each business module contains only a re-export, for example:

`from world_understanding.cognition.consolidator import *`

The compatibility package owns:

- no SQLite store;
- no independent stability policy;
- no consolidator instance;
- no current Life state;
- no worker/thread;
- no Runtime/Gateway attachment.

Therefore old imports point to the same canonical Python classes rather than a copied engine.

## 9. First-class World evidence bridge

New file:

`src/world_understanding/cognition/bridge.py`

Allowed input record classes are exactly:

- `world_known`
- `world_event`
- `world_entity`
- `world_relation`
- `world_hypothesis`

For every input the bridge validates before adaptation:

1. record type is allowed;
2. runtime object class matches the declared record type;
3. record ID matches the `WorldRecordRef`;
4. record revision matches for revisioned Entity/Relation objects;
5. record hash matches the `WorldRecordRef`;
6. canonical object hash is valid where the contract exposes `has_valid_hash()`;
7. exact Life/World/Principal scope matches the expected `WorldScope`.

No side is automatically selected on mismatch. Failure is closed.

## 10. Γ integration

For Direct/Derived Known, P7 invokes the P5 Γ plane before creating cognition evidence.

A Known record that is:

- scope-incompatible;
- inadmissible;
- stale/challenged/reverifying/retired;
- UNKNOWN;
- otherwise ineligible for stable promotion

cannot become P7 Cognition support.

Entity/Relation inputs must be `TRUE/CURRENT`.

Hypothesis is accepted only as a zero-authority interpretive input and passes Γ's non-evidence-object validation.

## 11. Conservative authority mapping

P7 does not allow the L5 bridge to manufacture empirical support.

- Known: contribution is bounded by the Known empirical weight and authority ceiling.
- Event: contribution is bounded by its existing empirical weight.
- Relation: contribution is bounded by its existing empirical weight.
- Entity: empirical contribution is hard-zero because the Entity is a materialized graph object, not an independent observation.
- Hypothesis: empirical contribution is hard-zero and is mapped to legacy `model_synthesis/model_inference` semantics.

The legacy Cognition stability engine further re-applies its own authority/correlation/independence rules.

## 12. Reference-only adaptation

P7 does not serialize or embed the full Known/Event/Entity/Relation/Hypothesis object into Cognition evidence.

The adapted evidence contains only reference/provenance material:

- source object ID;
- source object revision when applicable;
- source object SHA-256;
- a reference-only observation marker;
- preserved lineage-root hashes;
- world scope hashes;
- conservative legacy evidence-class/authority fields.

`content_sha256` remains the referenced World object's hash.

This preserves the frozen principle that new World Understanding objects are first-class evidence and Cognition references them rather than cloning them.

## 13. Evidence independence and provenance

The bridge derives lineage roots from existing first-class provenance fields where present:

- Known `provenance_refs`;
- Entity/Relation `source_observation_refs`;
- Event `source_refs`;
- Hypothesis `basis_refs`.

If no upstream provenance reference exists, the object's own canonical SHA is the fallback root.

The legacy stability engine subsequently collapses evidence sharing declared groups or lineage roots, so the P7 adapter cannot create an independent support quorum by merely generating new evidence IDs.

## 14. L5 output wrapper

New file:

`src/world_understanding/cognition/l5.py`

Every existing `CognitionStatement` can be projected as a `CognitionL5View` only after:

- life ID match;
- world scope hash match;
- principal scope hash match;
- privacy scope match;
- statement hash validation.

The view hard-locks:

- `empirical_evidence_weight_milli = 0`
- `context_only = true`
- `may_authorize = false`
- `may_execute = false`
- `confirms = false`
- `changes_risk = false`
- `c4_is_empirical_fact = false`

The accompanying `CognitionStatementRef` is reference-only and uses the existing P1 compatibility seam.

Thus C4 may be cognitively protected/global while remaining non-empirical and non-authorizing.

## 15. Existing store semantics retained

The absorbed `WorldCognitionStore` remains the existing SQLite CAS ledger:

- construction/read-only access does not create a DB;
- evidence, priors, statements and revisions are immutable rows;
- only the cognition head pointer is mutable;
- head mutation uses compare-and-swap inside `BEGIN IMMEDIATE`;
- canonical hashes are checked before persistence;
- no last-write-wins semantics are introduced.

P7 does not add a second store.

## 16. Existing tests retained unchanged

The five test files from the original verified Cognition source were added byte-for-byte to the current implementation branch:

- `tests/test_world_cognition_contracts.py`
- `tests/test_world_cognition_core.py`
- `tests/test_world_cognition_edges.py`
- `tests/test_world_cognition_math.py`
- `tests/test_world_cognition_provenance.py`

The original tests continue to import `v3.world_cognition.*`; those paths now resolve through thin re-export wrappers to the canonical L5 implementation.

These tests were **not executed in the available local environment after absorption** because that environment is a reduced reconstructed World Understanding harness rather than a full authenticated checkout containing the exact absorbed 2,000+ lines of Cognition source. They are therefore recorded as NOT RUN, not PASS.

The byte identity of the absorbed core source and original tests is verifiable from their Git blob SHAs.

## 17. Real tests executed

Execution environment: reconstructed local World Understanding harness under `/mnt/data/wu_p3_exact_core`.

### 17.1 Newly authored P7 bridge/L5 compile

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m compileall -q /mnt/data/wu_p3_exact_core/src/world_understanding/cognition
```

Result: PASS, exit code 0 for the locally materialized P7 bridge/L5 test package.

The local package does not contain the exact absorbed legacy core blobs, so this is not reported as a compile of the complete GitHub P7 canonical Cognition directory.

### 17.2 P7 bridge/L5 focused harness

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q /mnt/data/wu_p3_exact_core/tests/test_p7_cognition_bridge.py
```

Final result:

```text
9 passed in 0.10s
```

Covered:

- Known first-class reference;
- Event first-class reference;
- Entity first-class reference;
- Relation first-class reference;
- Hypothesis first-class reference;
- cross-Life rejection;
- tampered reference rejection;
- stale Known rejection;
- C4 L5 output zero empirical/non-authorizing.

The first collection attempt was blocked because the reduced harness lacked P1 `WorldClaim`, WorldEvent and WorldHypothesis classes. Those missing test-fixture contracts were reconstructed from the authoritative P1 contract definitions. A subsequent run reached 8/9 because the reduced local CognitionStatement stub lacked its original `has_valid_statement_sha256()` helper. The stub was corrected to match the verified legacy contract and the final run passed 9/9. These were harness-completeness issues; neither is reported as a production-code failure or hidden.

### 17.3 P2-P7 available focused regression

Command:

```text
PYTHONPATH=/mnt/data/wu_p3_exact_core/src /opt/pyvenv/bin/python -m pytest -q \
  /mnt/data/wu_p3_exact_core/tests/test_world_understanding_ingress.py \
  /mnt/data/wu_p3_exact_core/tests/test_p3_core_after_p4.py \
  /mnt/data/wu_p3_exact_core/tests/test_p4_known_closure.py \
  /mnt/data/wu_p3_exact_core/tests/test_p5_common_kernel.py \
  /mnt/data/wu_p3_exact_core/tests/test_p6_software_world.py \
  /mnt/data/wu_p3_exact_core/tests/test_p7_cognition_bridge.py
```

Result:

```text
145 passed in 4.43s
```

## 18. GitHub verification

Post-commit compare from P6 final to P7 implementation shows one P7 implementation commit and no unrelated Runtime/Gateway execution files.

The canonical `src/world_understanding/cognition/consolidator.py` blob SHA is exactly:

`bc85c7c4f7cffff05fce29d4311f333126154c43`

which is the same blob SHA as the verified existing Cognition core source.

The legacy `v3.world_cognition.consolidator` module is only a two-line re-export.

Combined GitHub status query for the P7 implementation commit returned no statuses. There is therefore no CI result to claim.

## 19. Tests not executed / limitations

NOT RUN:

- five unchanged original Cognition contract/core test files on the absorbed current branch;
- full authoritative-repository `pytest`;
- exact authenticated P0-P7 checkout regression;
- Windows runtime smoke;
- production Linux runtime smoke;
- native producer -> WU ingress -> Known/Graph -> Cognition E2E;
- Runtime/Gateway integration;
- prompt/context integration;
- P7 GitHub CI.

No unexecuted item is reported as PASS.

## 20. Backfill / governance / migration source reality

The verified legacy contracts/core include `migration` as an explicit revision decision authority and migration-related evidence/prior semantics.

However, no separate verified backfill/governance/migration implementation modules were present in the accessible Cognition branches at P7 time. P7 therefore preserves the verified migration authority semantics but does not invent unverified standalone subsystems.

If such historical modules become available later, they must be reconciled against this canonical L5 package rather than added as a second Cognition engine.

## 21. Contract compatibility impact

Expected compatibility impact is intentionally additive:

- four existing Cognition contract modules become available on the current WU branch with their original bytes;
- original Cognition core behavior is retained byte-for-byte;
- old `v3.world_cognition.*` import paths remain available through aliases/re-exports;
- P1 `CognitionStatementRef` remains the World Understanding reference seam;
- no P1 World Understanding contract was rewritten.

The new bridge is an adapter around existing objects; it does not alter their stored identity/hash.

## 22. Runtime / Gateway / OFF behavior

P7 does not modify:

- `WorldUnderstandingFacade`;
- `WorldUnderstandingIngress`;
- Runtime;
- Total Gateway;
- Tool execution;
- FactKernel;
- ToolResult producers;
- `zongdiaodu.py`;
- `duihua_qiaojie.py`;
- Self-Will;
- prompt assembly.

The absorbed legacy facade remains lazy/disabled by default and is not attached to the WU runtime path. Merely importing the canonical P7 package does not construct a store.

Therefore current WU OFF behavior remains unchanged.

## 23. Rollback

Rollback target:

`09bd95f139f9beac12f08f98f05906d316b48ae7`

Rolling back to this SHA removes P7 while preserving P0-P6 and the latest synchronized main lineage.

## 24. Gate conclusion

P7 L5 Cognition absorption functional gate:

`PASS WITH LEGACY/FULL-REPOSITORY TEST-EXECUTION LIMITATIONS RECORDED`

What is proven in the available environment:

- no second canonical Cognition implementation;
- exact source-blob absorption of verified legacy contracts/core;
- thin legacy import compatibility;
- deterministic Life-scoped first-class World evidence adaptation;
- Γ gating;
- C4 remains context-only/non-empirical/non-authorizing;
- P2-P7 focused regression 145/145.

What is not proven yet:

- execution of the five unchanged original Cognition tests on the exact absorbed full checkout;
- full repository integration/regression.

P8 is not started by this report.
