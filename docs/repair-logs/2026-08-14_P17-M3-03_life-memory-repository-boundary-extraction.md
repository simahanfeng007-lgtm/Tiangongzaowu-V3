# P17-M3-03 Life Memory Repository Boundary Extraction

Date: 2026-08-14 (Asia/Singapore)

## Stage

P17-M3-03 — Life Domain Repository Boundary / Memory SSoT transaction closure.

Frozen M3 plan remains exactly five stages:

1. M3-01 — Life SQLite Connection Boundary — complete
2. M3-02 — Life Schema / Migration Boundary — complete
3. M3-03 — Life Domain Repository Boundary — complete in this stage
4. M3-04 — Gateway Store / Unit-of-Work Boundary
5. M3-05 — Store Closeout / Architecture Regression

M2 remains closed at 5/5. No M2-06 or M3-06 was introduced.

## Baseline

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Formal branch: `agent/p17-m3-store-authority-decomposition`
- M3-02 final baseline: `836d69aec1f53c0e289c805232110bc3b17e067a`
- M3-02 base tree: `df2be90c9bea25e355ae8414380520f639d0a88a`
- M3-03 implementation: `e7b072b348db27d6a49e254957d03262f4a3e628`
- M3-03 implementation tree: `fe54bf817471675a9144b4e6bbc8d4d71a13bb13`
- `main` was not merged or intentionally modified by this stage.

## Problem found

After M3-01 and M3-02, `LifeShadowStore` no longer owned SQLite connection lifecycle or schema/migration authority, but `src/life_service/store.py` still contained about 120 methods and mixed multiple domain persistence responsibilities.

A method/table/transaction audit showed that mechanically splitting CRUD methods would be unsafe because Memory SSoT writes are transaction closures spanning several tables and private helpers. In particular, `delete_memory()` spans protected-payload key destruction, recall-index deletion, privacy tombstones/suppressions and memory change recording and therefore must remain one atomic transaction closure.

M3-03 therefore extracts one coherent domain repository only: Memory SSoT persistence.

## Resulting authority structure

```text
LifeShadowStore
    |
    +-- store_connection.py
    |     SQLite path/open/PRAGMA lifecycle
    |
    +-- store_schema.py
    |     schema v17 / migration authority
    |
    +-- store_contract_support.py
    |     shared Store/Repository error and contract result identity
    |
    +-- LifeMemoryRepository
          same sqlite3.Connection object
          Memory assertion / protected payload
          memory change log + outbox
          derivation DAG + active heads
          invalidation / temperament receipt
          world-candidate outbox
          memory search / relation
          privacy deletion transaction
```

`LifeShadowStore` remains the compatibility facade and keeps the historical public/private method signatures. It constructs exactly one `LifeMemoryRepository` with the same connection object:

```python
self._memory_repository = LifeMemoryRepository(connection)
```

There is no `sqlite3.connect()` call in the repository and no second database writer, schema authority, Runtime or startup path.

## Exact Memory repository cluster

The extraction moves 51 existing methods as one transaction-aware domain cluster:

- protected payload record/read/write and index-key helpers
- memory assertion and live assertion writes
- memory change head/sequence/outbox
- derivation parse/write/read/query/parent-child graph
- active memory heads and consumer offsets
- invalidation and active-head clearing
- temperament adaptation receipts
- World Understanding memory-candidate outbox
- memory outbox acknowledgement
- latest/get/list/search assertion reads
- memory relation writes/reads
- `delete_memory`

No causal, affect, autonomy, reflection, capability, ingress, life-authority or health domain methods were moved.

## Shared contract support

`store_contract_support.py` owns shared identity that must be identical between the facade and Memory repository:

- `LifeShadowStoreError`
- `ProtectedPayloadRecord`
- `MemoryDeletionResult`
- `_revalidate_contract`
- `_parse_stored_contract`

`store.py` explicitly imports/re-exports these identities for compatibility. This avoids circular imports and avoids defining a second exception or result contract.

The two result records preserve their exact historical Python descriptors:

```python
@dataclass(frozen=True, slots=True)
```

## Python descriptor defects found and repaired during candidate validation

Two real extraction defects were found by regression testing before formal materialization.

### 1. Static helper descriptor loss

AST source extraction originally copied method text without the decorator source span, causing `_protected_payload_record_from_row` to lose `@staticmethod` after moving into `LifeMemoryRepository`.

Observed failure shape:

```text
TypeError: ... takes 1 positional argument but 2 were given
```

Affected protected-memory tests failed through the same binding defect.

Repair:

- preserve `@staticmethod` for `_protected_payload_record_from_row`
- preserve `@staticmethod` for `_term_digests`
- lock the Store facade to call these as static repository helpers

### 2. Dataclass descriptor loss

The same AST source-segment behavior omitted class decorators when moving `ProtectedPayloadRecord` and `MemoryDeletionResult` into `store_contract_support.py`.

Observed failure shape:

```text
TypeError: ProtectedPayloadRecord() takes no arguments
```

Repair:

- restore exact `@dataclass(frozen=True, slots=True)` semantics
- add regression checks for dataclass identity, slots, construction and frozen assignment behavior

These were candidate-stage defects only. They were fixed before the verified artifact and before the formal implementation commit.

## Formal file set

Relative to M3-02, the clean implementation changes exactly nine files:

1. `.github/workflows/architecture-gate.yml`
2. `src/life_service/store.py`
3. `src/life_service/store_contract_support.py`
4. `src/life_service/store_memory_repository.py`
5. `app/life-service/runtime314/life_service/store.py`
6. `app/life-service/runtime314/life_service/store_contract_support.py`
7. `app/life-service/runtime314/life_service/store_memory_repository.py`
8. `app/life-service/runtime314/life_service/.tiangong-generated-source.json`
9. `tests/test_life_store_p17_m3_03.py`

No construction script, audit workflow, candidate workflow or materialization workflow is present in the formal implementation tree.

The source/runtime314 pairs are byte-identical and materialized to the same Git blob IDs.

## Diff shape

M3-02 → M3-03 clean implementation:

- each `store.py`: +149 / -2276
- each `store_contract_support.py`: +48
- each `store_memory_repository.py`: +2333
- Architecture Gate: +7 / -1
- M3-03 tests: +127
- generated-source marker updated by the authoritative source synchronizer

The large removal from `store.py` is responsibility movement to one repository, not duplicate persistence logic.

## M3-03 regression contract

`tests/test_life_store_p17_m3_03.py` locks:

1. one supplied SQLite connection, no repository-owned connection creation
2. no schema/open/`CREATE TABLE` authority in the repository
3. Store facade and repository method signatures remain identical
4. Store Memory methods are thin delegates and no longer own `BEGIN IMMEDIATE`
5. the extracted method set remains the intended Memory transaction cluster
6. repository-internal private calls do not escape back into Store business logic
7. `LifeShadowStoreError` identity is shared, not duplicated
8. protected-payload and deletion result records remain frozen/slotted dataclasses
9. real `LifeShadowStore.open()` wires repository and Store to the same connection and schema v17 remains healthy
10. permanent Architecture Gate covers M3-03 and its authoritative modules

## Deep compatibility regression

Candidate/materialization validation additionally ran existing production-oriented persistence regressions:

- `tests/test_life_shadow_store.py`: 7/7 passed
- `tests/test_causal_memory_store.py`: 7/7 passed
- `tests/test_memory_derivation_store_p15.py`: 17/17 passed

These validate, among other behavior:

- concurrent revision writers converge to one winner
- plaintext memory is not exposed directly in the database
- ciphertext/index tampering is detected
- legal hold remains fail-closed
- privacy deletion destroys recall paths while keeping minimal proof
- protected memory/relation/node/context round trips
- historical migration behavior
- active-head replacement and derivation history
- consumer offset monotonicity
- derivation DAG parent ordering
- invalid-parent rollback
- principal/privacy scope enforcement
- world-candidate outbox schema migration

## Candidate verification

Final read-only candidate:

- Run: `31759522336`
- branch: `agent/p17-m3-03-construction`
- head: `70e139164d4d925cf650a4fd49efa3c861380856`
- conclusion: success
- artifact ID: `9204050024`
- artifact SHA256: `5e84652406105314d139956f767bfd44bd32044b7a9622e10847400f7e373453`
- artifact contained exactly the nine formal files
- local SHA256 recomputation matched GitHub's artifact digest exactly

## Verified blob materialization

Materialization Run:

- Run: `31759610335`
- conclusion: success
- behavior: rebuild candidate, sync mirrors, rerun full validation, create only unreferenced Git blobs
- no commit or ref mutation was performed by the workflow

Verified formal blobs:

- `.github/workflows/architecture-gate.yml`: `e545ce2f0cb30c1ddcd3ca6b6a55fafc01e616bd`
- `src/life_service/store.py`: `d011ff76fcd9e255a63632274ff45fa97dea795e`
- `src/life_service/store_contract_support.py`: `71700f22aefcd9278addc61520f617e0e61ea06e`
- `src/life_service/store_memory_repository.py`: `7a3bce0963bd476e48f0c26086cb74381ec74b77`
- runtime314 `store.py`: `d011ff76fcd9e255a63632274ff45fa97dea795e`
- runtime314 `store_contract_support.py`: `71700f22aefcd9278addc61520f617e0e61ea06e`
- runtime314 `store_memory_repository.py`: `7a3bce0963bd476e48f0c26086cb74381ec74b77`
- runtime314 generated-source marker: `8e084729ae7b52592b9454a84ea48242dfdd559d`
- `tests/test_life_store_p17_m3_03.py`: `79414ca4979275bf106e7106b016d53d5047030e`

All nine matched the locally recomputed Git blob identities from the frozen Artifact.

## Permanent Architecture Gate

The permanent Gate now adds:

- repository-pinned runtime test dependencies:
  - `cryptography==48.0.1`
  - `pydantic==2.13.4`
- `python tests/test_life_store_p17_m3_03.py -v`
- compile coverage for:
  - `src/life_service/store_contract_support.py`
  - `src/life_service/store_memory_repository.py`

Implementation Gate:

- Run: `31759680180`
- head: `e7b072b348db27d6a49e254957d03262f4a3e628`
- Ubuntu: success
- Windows: success

## Explicit non-changes

M3-03 does not change:

- SQLite file/path safety contract
- SQLite connection lifecycle/PRAGMA authority
- schema version 17 or migration SQL/order
- Memory contract semantics
- Memory promotion/invalidation policy
- privacy deletion semantics
- World Understanding authority
- Life Runtime / scheduler / identity authority
- Total Gateway execution authority
- A0-A5 gate behavior
- Gateway Store / Unit-of-Work design (reserved for M3-04)
- `main`

## Construction lineage

Temporary construction/audit workflows and scripts remain confined to `agent/p17-m3-03-construction`. They are not part of the formal M3 implementation tree. The connector does not provide a branch-delete operation in this workflow, so this temporary branch is retained only as construction/audit evidence.

## Outcome

P17-M3-03 is complete: Memory SSoT persistence is now a coherent repository behind the existing `LifeShadowStore` facade, while the project still has one SQLite connection authority, one writer path, one schema authority and unchanged transactional semantics.

Next frozen stage: **P17-M3-04 — Gateway Store / Unit-of-Work Boundary**.
