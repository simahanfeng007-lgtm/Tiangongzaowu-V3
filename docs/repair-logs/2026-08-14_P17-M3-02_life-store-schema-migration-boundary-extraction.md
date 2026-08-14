# P17-M3-02 Life Store Schema / Migration Boundary Extraction

Date: 2026-08-14 (Asia/Singapore)

## Stage

P17-M3-02 — Life Schema / Migration Boundary.

Frozen M3 plan remains exactly five stages:

1. M3-01 — Life SQLite Connection Boundary — complete
2. M3-02 — Life Schema / Migration Boundary — this stage
3. M3-03 — Life Domain Repository Boundary
4. M3-04 — Gateway Store / Unit-of-Work Boundary
5. M3-05 — Store Closeout / Architecture Regression

M2 remains closed at 5/5. No M2-06 or M3-06 was introduced.

## Baseline

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Formal branch: `agent/p17-m3-store-authority-decomposition`
- M3-01 final baseline: `e09478b55dc2737dc7969d1423d57f038e3756e8`
- M3-01 base tree: `40803e64c07814166ec427b3fb2d12e338b49b03`
- `main` remained `3d5f13b6816e27f9f182e65c5fd0023e63d4b5cf`
- No merge to `main` was performed.

## Problem found

After M3-01, `LifeShadowStore` no longer owned SQLite path/open/PRAGMA setup, but `src/life_service/store.py` still combined two very different persistence responsibilities:

1. Schema authority:
   - `SHADOW_STORE_SCHEMA_VERSION`
   - `SHADOW_STORE_APPLICATION_ID`
   - base schema SQL
   - P1-P17 migration SQL/statements
   - migration IDs and SHA-256 identities
   - current schema hash
   - expected table set
   - initialization transaction
   - version-to-version migration execution

2. Store/domain authority:
   - `LifeShadowStore` facade
   - domain reads/writes
   - domain transaction boundaries
   - health/integrity projection
   - replay/memory/life persistence behavior

Keeping both responsibilities in the same giant module made future repository decomposition unsafe because schema history and domain write logic could be modified together accidentally.

## Compatibility audit

A dedicated construction audit verified that historical migration tests and fixtures directly reference private names on `life_service.store`, including `_P1_*` through `_P17_*`, `_EXPECTED_TABLES`, schema hashes and `SHADOW_STORE_SCHEMA_VERSION`.

Therefore M3-02 deliberately does **not** break those names. The compatibility strategy is:

- schema definitions exist only once, in `store_schema.py`;
- `store.py` explicitly re-exports the historical names from `store_schema.py`;
- there is no wildcard import and no duplicated SQL/hash definition;
- old migration fixtures continue to resolve the legacy import surface while authority has moved.

This is a compatibility surface, not a second schema authority.

## Resulting authority structure

```text
LifeShadowStore.open()
    |
    +-- store_connection.py
    |     path safety / sqlite open / PRAGMAs
    |
    +-- store_schema.py
    |     schema version + application id
    |     P1..P17 SQL / IDs / hashes
    |     current schema hash / expected tables
    |     initialize_life_shadow_schema()
    |     migrate_life_shadow_schema()
    |
    +-- store.py
          LifeShadowStore facade
          LifeShadowStoreError authority
          explicit schema compatibility re-exports
          thin _initialize/_migrate adapters
          domain repositories
          domain transaction boundaries
          health/integrity behavior
```

## New schema authority

New authoritative module:

`src/life_service/store_schema.py`

It owns:

- `SHADOW_STORE_SCHEMA_VERSION = 17`;
- `SHADOW_STORE_APPLICATION_ID = 0x54474C53`;
- initial schema SQL;
- every historical migration definition through P17;
- migration IDs, statement tuples, SQL material and hashes;
- `_SCHEMA_SQL` and `_SCHEMA_SHA256`;
- `_EXPECTED_TABLES`;
- `initialize_life_shadow_schema(...)`;
- `migrate_life_shadow_schema(...)`.

It does **not** own:

- connection opening;
- Life domain repositories;
- Life domain write transactions;
- `LifeShadowStore` facade;
- health policy;
- `LifeShadowStoreError`.

Migration errors receive `error_factory` from the Store facade so the Store remains the public error authority and the new schema module does not import `store.py` back, avoiding circular authority.

## Store facade after extraction

`src/life_service/store.py` now:

- explicitly imports/re-exports historical schema names from `.store_schema`;
- contains no `CREATE TABLE schema_migrations` schema-definition block;
- no longer defines schema version/application id/migration hashes itself;
- retains `_initialize(...)` only as a thin call to `initialize_life_shadow_schema(...)`;
- retains `_migrate(...)` only as a thin call to `migrate_life_shadow_schema(...)` with `error_factory=LifeShadowStoreError`;
- keeps domain repository and transaction logic in place for M3-03;
- keeps `health()` behavior in place.

The clean diff reduces `store.py` by approximately 1,407 net responsibility lines while adding the dedicated schema authority module.

## Formal implementation

Clean implementation commit:

`9fa1a60052b1c858431200b68d4d9e28bbc1e482`

Parent:

`e09478b55dc2737dc7969d1423d57f038e3756e8`

Clean tree:

`f8ebd27646137b8f184a96290a82288d452c4bab`

Formal implementation changed exactly seven files:

1. `.github/workflows/architecture-gate.yml`
2. `src/life_service/store.py`
3. `src/life_service/store_schema.py`
4. `app/life-service/runtime314/life_service/store.py`
5. `app/life-service/runtime314/life_service/store_schema.py`
6. `app/life-service/runtime314/life_service/.tiangong-generated-source.json`
7. `tests/test_life_store_p17_m3_02.py`

No construction script, audit workflow, candidate workflow or materialization workflow was admitted into the formal tree.

The absent/git-ignored `app/runtime/python312/...` build-runtime mirror was not committed.

## Regression tests

`tests/test_life_store_p17_m3_02.py` contains six focused checks:

1. `store_schema.py` is the single schema-definition authority and `store.py` contains explicit compatibility imports only.
2. `LifeShadowStore._initialize/_migrate` are thin facades and Store-owned exception semantics remain injected.
3. Fresh initialization preserves application id, schema version 17, migrations 1..17, metadata hash and full expected table set.
4. A real SQLite database constructed at schema version 1 is migrated through the historical chain to version 17 in order.
5. Migration errors preserve the caller-supplied Store exception type.
6. Permanent Architecture Gate covers the new M3-02 test and compiles `store_schema.py`.

The real v1 -> v17 migration regression is important: M3-02 is not validated only by textual/AST checks.

## Candidate validation

Construction branch:

`agent/p17-m3-02-construction`

Schema-reference audit Run:

`31757223062` — success.

First candidate Run:

`31757368798` — success.

The first artifact omitted hidden paths because `actions/upload-artifact` excludes hidden files by default. Product validation was green; artifact collection policy was then corrected with `include-hidden-files: true`.

Final complete candidate Run:

`31757431640` — success.

Complete artifact:

- Artifact ID: `9203257627`
- Name: `p17-m3-02-candidate-readonly`
- SHA-256: `5bf21147210c7b74fec2e17fc2dbd9f3cfdbf616a1acf16a90682e86906531d0`

The downloaded ZIP was recomputed locally and matched the GitHub artifact digest exactly.

## Verified blob materialization

Materialization Run:

`31757516262` — success.

It rebuilt the candidate, synchronized mirrors, reran the permanent M1/M2/M3-01/M3-02 validation and created only unreferenced Git blobs. It did not move the formal branch.

Verified formal blob identities:

- Architecture Gate: `6c4bee7097957a32a960c93930b77b35cb8a93b8`
- runtime314 generated marker: `632f3b16268133cd73144855f6489c670d604156`
- source/runtime314 `store.py`: `da6066189d0b06474bb453523acfb552e31726b1`
- source/runtime314 `store_schema.py`: `5cc3abd149a0d3575f04aeb8912c9daee4459783`
- M3-02 regression test: `a6ebf2d7d993baebff2b7f2ab2b6a24071b14fce`

Materialized Git blob identities matched the complete candidate Artifact **7/7**.

## Additional compatibility-test note

A separate dependency-aware workflow for running a broader set of old schema fixtures was attempted during construction, but GitHub connector safety blocked creation/update of that temporary workflow. This was a tooling/control-plane block, not a source or runtime failure.

The formal M3-02 regression still directly verifies the compatibility re-export surface and performs a real v1-to-v17 SQLite migration. The permanent Architecture Gate also preserves all prior M1/M2/M3-01 regressions.

## Permanent Architecture Gate

Formal implementation Run:

`31757643239`

Head:

`9fa1a60052b1c858431200b68d4d9e28bbc1e482`

Result:

- Ubuntu: success
- Windows: success
- source-authority topology: success
- generated mirror check: success
- all retained M1/M2 regressions: success
- M3-01 regression: success
- M3-02 regression: success
- M2/M3 seam compilation: success

## Scope explicitly unchanged

M3-02 did not change:

- `LifeShadowStore` public entry/facade;
- SQLite connection/path/PRAGMA semantics established in M3-01;
- schema version 17;
- migration sequence or migration identities;
- domain Repository behavior;
- domain transaction boundaries;
- Memory/World/Life fact authority;
- Total Gateway store or Unit-of-Work behavior;
- Runtime, Action Gate or Tool Executor;
- startup chain;
- `main`.

## Closeout

M3-02 successfully separated immutable schema/migration authority from the Life domain Store giant module without creating a second Store, changing database history, or breaking the historical import surface.

Next stage is M3-03 — Life Domain Repository Boundary. It will decompose domain persistence responsibilities while preserving transaction atomicity and Single Writer semantics; it must not turn repositories into independent state authorities.
