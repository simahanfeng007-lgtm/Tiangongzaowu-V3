# P17-M3-01 Life Store Connection Boundary Extraction

Date: 2026-08-14 (Asia/Singapore)

## Stage

P17-M3-01 — Life SQLite Connection Boundary

This is the first stage of the frozen five-stage P17-M3 Store Authority / Repository / UoW decomposition.

Frozen M3 plan:

1. M3-01 — Life SQLite Connection Boundary
2. M3-02 — Life Schema / Migration Boundary
3. M3-03 — Life Domain Repository Boundary
4. M3-04 — Gateway Store / Unit-of-Work Boundary
5. M3-05 — Store Closeout / Architecture Regression

M2 remains closed at 5/5. No M2-06 was introduced.

## Baseline

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Formal M3 branch: `agent/p17-m3-store-authority-decomposition`
- M2 final repair-log baseline: `4101187e83000b9c33b7d841e091210acbb48456`
- `main` remained: `3d5f13b6816e27f9f182e65c5fd0023e63d4b5cf`
- No merge to `main` was performed.

## Pre-change audit

`src/life_service/store.py` was not merely a large repository module. `LifeShadowStore` owned several distinct infrastructure and domain responsibilities in one file:

- SQLite path validation and safety checks;
- connection creation;
- connection-level PRAGMA configuration;
- schema initialization;
- schema migration through the current schema line;
- health/integrity validation;
- Life-domain persistence and query methods;
- explicit transaction boundaries used by domain operations.

`src/total_gateway/store.py` also remains a large persistence authority, but it is intentionally outside M3-01.

The first extraction therefore had to avoid touching schema, migration, transaction, or domain repository semantics.

## Objective

Extract only the physical SQLite connection lifecycle from `LifeShadowStore.open()` while preserving `LifeShadowStore` as the single authoritative Life store facade.

Target structure:

```text
Embedded Life / callers
        |
        v
LifeShadowStore.open()
        |
        v
store_connection.open_life_shadow_sqlite()
        |
        +-- path safety
        +-- sqlite3.connect
        +-- connection PRAGMAs
        +-- initialize/migrate callback dispatch
        |
        v
LifeShadowStore._initialize / _migrate / health / repositories
```

The extracted module is infrastructure coordination only. It does not become a second Store authority.

## Formal implementation

Clean implementation commit:

`33b25c07ff02012059f3d92ee570eec97ed8fb4e`

Parent:

`4101187e83000b9c33b7d841e091210acbb48456`

Clean implementation tree:

`b0d78bb5f352578ee97604305073ec255e10196b`

### New authoritative module

`src/life_service/store_connection.py`

It owns only:

- `LifeStoreSchemaLifecycle` typed callback protocol;
- `LifeStoreErrorFactory` typed error factory;
- `OpenedLifeShadowSqlite` immutable result;
- `open_life_shadow_sqlite(...)`.

The function preserves the historical open sequence:

1. validate `now_ms`;
2. validate `.shadow.sqlite3` path shape;
3. resolve the existing parent;
4. reject unsafe existing target shapes;
5. enforce `create=False` existence semantics;
6. open SQLite with the historical timeout/isolation/thread configuration;
7. install `sqlite3.Row`;
8. enable foreign keys;
9. disable trusted schema;
10. set busy timeout;
11. require WAL mode;
12. set synchronous FULL;
13. call the existing store-owned `_initialize` or `_migrate` callback;
14. close the connection on any failure.

### `LifeShadowStore.open()`

`src/life_service/store.py` now delegates physical connection setup to `open_life_shadow_sqlite(...)` and still owns:

- the public `LifeShadowStore.open()` API;
- `LifeShadowStoreError` exception semantics;
- `_initialize(...)`;
- `_migrate(...)`;
- `health()`;
- every domain repository/query/write method;
- all existing transaction boundaries outside the physical-open seam.

The store passes `LifeShadowStoreError` as the error factory, so callers continue to observe the existing store-owned exception type.

## Explicit non-goals / authorities not moved

M3-01 does **not** move or duplicate:

- `_SCHEMA_SQL`;
- schema version authority;
- migration SQL or migration ordering;
- `BEGIN IMMEDIATE` / COMMIT / ROLLBACK domain transaction logic;
- Life writer lease;
- Memory SSoT;
- World Understanding state;
- scheduler state;
- Life domain repositories;
- Total Gateway store;
- any Runtime, startup entry, HTTP listener, or execution loop.

No second SQLite authority or second Life store was created.

## Source-mirror handling

`src/life_service` remains the sole human-editable authority under `source-ownership.json`.

The already-committed runtime314 target was synchronized:

- `app/life-service/runtime314/life_service/store.py`
- `app/life-service/runtime314/life_service/store_connection.py`
- generated-source marker

During candidate construction, `scripts/sync-generated-sources.py --write` also materialized the absent build-runtime target under:

`app/runtime/python312/Lib/site-packages/life_service`

This target is intentionally git-ignored/build-only when absent. The source synchronizer's `--check-committed` mode explicitly skips absent build-only targets. Committing that temporary materialization would have added an unrelated ~8.6k-line mirror to M3-01, so it was intentionally excluded from the formal clean tree.

This exclusion is scope control, not a source-authority exception: the authoritative source and the committed runtime314 mirror remain synchronized, while the build-only target continues to be generated when a packaged runtime is assembled.

## Formal file delta

M2 final baseline → M3-01 clean implementation contains exactly seven formal files:

1. `.github/workflows/architecture-gate.yml`
2. `src/life_service/store.py`
3. `src/life_service/store_connection.py`
4. `app/life-service/runtime314/life_service/store.py`
5. `app/life-service/runtime314/life_service/store_connection.py`
6. `app/life-service/runtime314/life_service/.tiangong-generated-source.json`
7. `tests/test_life_store_p17_m3_01.py`

`src/life_service/store.py` net structural change is limited to the connection seam: +10 / -33 in the clean compare. The new boundary is 84 lines.

## Regression coverage

New permanent regression:

`tests/test_life_store_p17_m3_01.py`

Five checks cover:

1. connection module is SQLite-lifecycle-only and owns no schema/domain authority;
2. `LifeShadowStore.open()` delegates while `_initialize`, `_migrate`, health and transaction authority stay in `store.py`;
3. create/reopen callback ordering and critical PRAGMAs are preserved;
4. the injected error factory preserves store-owned exception semantics;
5. permanent Architecture Gate includes M3-01.

The permanent Gate now runs M1, M1-03, all five M2 regressions, M3-01, and compilation of the extracted M2/M3 seams on Ubuntu and Windows.

## Candidate verification

### First candidate run

Run `31756527329` failed only because the new test imported `life_service` as a package, which executed `life_service/__init__.py` and reached an optional `cryptography` dependency not installed on the isolated candidate runner.

No product assertion had failed. The test was corrected to load `store_connection.py` directly with `importlib.util`, avoiding unrelated package initialization without adding a test dependency.

### Successful read-only candidate

Run:

`31756613187`

Result: success.

Passed:

- Source Authority topology;
- committed generated-mirror validation;
- all M1/M2 permanent regressions;
- five M3-01 tests;
- authoritative and generated-mirror compilation;
- `git diff --check`.

Candidate Artifact:

`p17-m3-01-candidate-readonly`

Artifact ID:

`9202939934`

Artifact SHA256:

`fb476b96e663d15e383b84ddcb4b31e16f25548dca24b4b01fb64fc26346aae4`

The downloaded ZIP was independently rehashed and matched the GitHub artifact digest exactly.

## Verified blob materialization

The first materialize run `31756750621` rebuilt and revalidated the candidate successfully, then failed only because the temporary workflow's inline Python had an indentation error before any Git Data request executed.

The workflow indentation was corrected without changing candidate product bytes.

Successful materialize run:

`31756830765`

It rebuilt the same candidate, repeated all validation, and created unreferenced Git blobs only.

Verified formal blob identities:

- Architecture Gate: `83e6c6a4b1aad13bddb2181989a2e8966e05cbe9`
- `store.py` authoritative/runtime314: `cfafefa2bb5303b0a3ab8e25c4fccde1cbc826eb`
- `store_connection.py` authoritative/runtime314: `de1708e46830ebc9ff3b32a0959d6c2a94c9a095`
- runtime314 generated marker: `1bdd42f00d450036ea0e622fb397fb19d8016b0d`
- M3-01 regression test: `7908e03289660539783e3f29a3fcd7f70a1f9c89`

The build-only python312 blobs were also generated during verification but intentionally omitted from the clean formal tree for the scope reason documented above.

For all ten candidate artifact files, independently calculated Git blob identity (`SHA1("blob <len>\\0" + bytes)`) matched the materialize-run Git SHA 10/10.

## Permanent Architecture Gate

Implementation Gate Run:

`31756948033`

Head:

`33b25c07ff02012059f3d92ee570eec97ed8fb4e`

Result:

- Ubuntu latest: success
- Windows latest: success

Both platforms passed source authority, generated mirrors, all M1/M2 regressions, M3-01 regression, and compilation.

## Result

P17-M3 has started and M3-01 is complete at the implementation level.

Architecture after this stage:

```text
LifeShadowStore = single Store facade / schema / migration / health / domain authority
        |
        +-- store_connection = physical SQLite connection lifecycle only
```

M3 progress after M3-01:

`1 / 5`

Next planned stage:

**P17-M3-02 — Life Schema / Migration Boundary**
