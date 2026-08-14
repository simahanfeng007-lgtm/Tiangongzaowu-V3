# P17-M3-04 Gateway Store Unit-of-Work Boundary Extraction

Date: 2026-08-14 (Asia/Shanghai)

## Stage

P17-M3-04 — Gateway Store / Unit-of-Work Boundary.

Frozen M3 plan remains exactly five stages:

1. M3-01 — Life SQLite Connection Boundary — complete
2. M3-02 — Life Schema / Migration Boundary — complete
3. M3-03 — Life Domain Repository Boundary — complete
4. M3-04 — Gateway Store / Unit-of-Work Boundary — complete in this stage
5. M3-05 — Store Closeout / Architecture Regression — next

M2 remains closed at 5/5. No M2-06 or M3-06 was introduced.

## Baseline

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`
- Formal branch: `agent/p17-m3-store-authority-decomposition`
- M3-03 final baseline: `40cae726515496caf4a50ceb3fe4b6bc55d24a3c`
- M3-04 implementation: `e5c04ee0f7500353fbbfd48054883617809d3973`
- `main` was not merged or intentionally modified by this stage.

## Problem found

`GatewayStateStore` in `src/total_gateway/store.py` (~8.6K lines) owns every
write transaction as an inline `@contextmanager` `_write_transaction` body:
closed-state precondition, `BEGIN IMMEDIATE`, `yield`, `COMMIT` and the
exception-bound `ROLLBACK`.  The historical control flow deliberately keeps
`COMMIT` inside the `try` block so a COMMIT failure also executes ROLLBACK.
61 production write-transaction call sites acquire `self._lock` first and then
enter `_write_transaction()`.

M3-04 extracts only the SQLite write-transaction mechanical lifecycle into a
new `store_unit_of_work.py`, while `GatewayStateStore` keeps lock ownership,
connection ownership, closed-state authority, schema, CAS, outbox, effect
ledger and generation fencing unchanged.

One targeted-test defect was found and repaired during candidate validation:
the generated health regression asserted `execute("BEGIN")`, but the real
`health_check` historically uses a writability-probe transaction
(`execute("BEGIN IMMEDIATE")` with `ROLLBACK` in `finally`).  The generated
assertion was aligned to the real historical shape; product semantics were
never changed.

## Resulting authority structure

```text
GatewayStateStore
    |
    +-- store_unit_of_work.py
    |     gateway_store_write_transaction(connection)
    |     BEGIN IMMEDIATE / yield / COMMIT / ROLLBACK mechanics only
    |
    +-- _write_transaction
          closed-state authority (if self._closed: raise StoreError)
          delegates only the transaction mechanics to the UoW seam
```

The UoW seam owns no lock, no connection creation, no schema, no health logic
and no domain SQL.  `health_check` keeps its own store-owned transaction and
is asserted never to use the UoW seam.

## Exact UoW contract

```python
connection.execute("BEGIN IMMEDIATE")
try:
    yield
    connection.execute("COMMIT")
except Exception:
    connection.execute("ROLLBACK")
    raise
```

COMMIT stays inside `try`, preserving the historical semantics that a COMMIT
failure follows the ROLLBACK path.

## Formal file set

Relative to M3-03, the clean implementation changes exactly four files:

1. `.github/workflows/architecture-gate.yml`
2. `src/total_gateway/store.py`
3. `src/total_gateway/store_unit_of_work.py`
4. `tests/test_total_gateway_store_p17_m3_04.py`

No construction script, audit workflow, candidate workflow or materialization
workflow is present in the formal implementation tree.

## Diff shape

M3-03 → M3-04 clean implementation:

- `architecture-gate.yml`: +4 / -1
- `store.py`: +3 / -6
- `store_unit_of_work.py`: +33
- M3-04 tests: +149

## M3-04 regression contract

`tests/test_total_gateway_store_p17_m3_04.py` locks:

1. success path preserves `BEGIN IMMEDIATE` then `COMMIT`
2. body failure preserves `ROLLBACK` and exception identity
3. COMMIT failure follows the historical ROLLBACK path
4. UoW is transaction mechanics only (no lock/schema/domain SQL/store construction)
5. Store keeps closed-state authority and delegates only mechanics
6. every `_write_transaction()` call site enters `self._lock` first
7. `health_check` keeps its own store-owned transaction and never uses the UoW seam
8. permanent Architecture Gate covers M3-04 and compiles the new seam

## Deep compatibility regression

Candidate and materialization validation additionally ran the pre-existing
production-oriented Gateway Store regression:

- `tests/test_gateway_store.py`: 11/11 passed

## Candidate verification

Final read-only candidate:

- Run: `31765794224`
- branch: `agent/p17-m3-04-construction`
- conclusion: success

Construction-chain repairs performed before the green candidate:

- `fix(p17-m3-04)`: correct health transaction assertion to historical shape
- `test(p17-m3-04)`: cover existing gateway store regression in candidate validation
- `ci(p17-m3-04)`: retire superseded v1 candidate workflow and fix PYTHONPATH

The superseded v1 candidate workflow was removed from the construction branch
because its deterministic anchor no longer matched the real historical
`_write_transaction` shape and it could only fail on every push.

## Verified blob materialization

Materialization Run:

- Run: `31765794413`
- conclusion: success
- behavior: rebuild candidate, rerun full validation, create only unreferenced Git blobs
- no commit or ref mutation was performed by the workflow

Verified formal blobs:

- `.github/workflows/architecture-gate.yml`: `81401c30cf0b9af8498c25914035f9b98e5ef507`
- `src/total_gateway/store.py`: `a81719ca2958b7a4cdf46fe9293e9592462ac6fc`
- `src/total_gateway/store_unit_of_work.py`: `db8ebae50fd0f5d9d7a281502d6ff7a9a82a3aa1`
- `tests/test_total_gateway_store_p17_m3_04.py`: `92f1b6f5cade7241486cdc11d29620a261043601`

All four matched the locally recomputed Git blob identities.

## Permanent Architecture Gate

The permanent Gate now adds:

- `python -m unittest discover -s tests -p "test_total_gateway_store_p17*.py" -v`
- compile coverage for:
  - `src/total_gateway/store.py`
  - `src/total_gateway/store_unit_of_work.py`

Implementation Gate:

- Run: `31765903164`
- head: `e5c04ee0f7500353fbbfd48054883617809d3973`
- Ubuntu: success
- Windows: success

## Explicit non-changes

M3-04 does not change:

- SQLite connection ownership or file/path safety contract
- Store lock ownership and lock-before-transaction ordering
- closed-state (`StoreError`) authority
- schema / migrations / CAS / outbox / effect ledger / generation fencing
- `health_check` transaction shape
- Life Store authority (M3-01..03)
- Total Gateway execution authority
- A0-A5 gate behavior
- `main`

## Construction lineage

Temporary construction/audit/materialization workflows and scripts remain
confined to `agent/p17-m3-04-construction`.  They are not part of the formal
M3 implementation tree.

## Outcome

P17-M3-04 is complete: the Gateway SQLite write-transaction lifecycle is a
mechanics-only Unit-of-Work seam behind the existing `GatewayStateStore`
facade, while the project still has one SQLite connection authority, one
writer path, one lock authority and unchanged transactional semantics,
including COMMIT-failure rollback.

Next frozen stage: **P17-M3-05 — Store Closeout / Architecture Regression**.