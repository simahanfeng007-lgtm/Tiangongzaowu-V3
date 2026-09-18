# P12 R1A — recover interrupted Windows full regression

## Evidence and decision

This continues PR #78 on `codex/capability-composition-p12-retirement-preflight-v1`.
Rechecked candidate: `97b7615e86fa806e96c75bdc6076c802dc821568`, tree
`6aee05ef44f1f0f48330c4fb271e7df0782b17e3`; main remains
`bf29542b3048c8d1806add8063b0db7c72be055b`.

The earlier native CMD repair remains verified. It is NOT newly failing.
P14 run 35296115280 and P19 run 35296115119 are terminal success.
Architecture run 35296115055 has five successful jobs (Ubuntu full pytest,
Windows/Ubuntu Node and Source Authority) and one cancelled Windows full pytest
job 105448866918. Its original log ends at 2026-09-18T02:38:07Z, at the job's
60-minute boundary, with KeyboardInterrupt and 3,784 passed / 25 skipped /
761 passed subtests. It did NOT finish the collection. The available log does
not show an assertion failure, and its partial count is not full acceptance.

The existing workflow explicitly sets `timeout-minutes: 60`. Blindly repeating
that same serial run risks another incomplete observation. R1B production edits
remain deferred: this batch fixes full-gate execution and evidence completeness,
not manifest ownership, Runtime behavior or P11 acceptance.

## Bounded implementation

Modify the existing Architecture workflow only for Windows full pytest:
run eight whole-module shards, then retain the existing required-check name
`full-regression-windows-latest` on an independent union job. The Ubuntu full
regression remains the original serial `python -m pytest -q`.

`scripts/ci-pytest-shards.py` uses the installed pytest's normal collection of
both configured roots. Every shard independently records the entire collection.
Module allocation balances item counts deterministically, preserving the original
order within each selected module. Collection failures and any unexpected prior
filter/reorder are rejected. No static handpicked test list is substituted.

A shard records each selected item's setup/call/teardown outcome, start and finish,
collection errors/skips, subtest failures, pytest exit status and session completion.
It binds the Git HEAD/tree and observer bytes before and after execution, rejects
dirty tracked source and external PYTEST_ADDOPTS, and writes evidence outside the
checkout. Neither missing lifecycle outcomes nor interrupted pytest can pass.

The union job requires all eight successful jobs and exactly eight valid reports
from the current workflow run AND attempt. It recomputes the allocation, rejects
missing/duplicate shards, compares full collection and runtime identities, and
proves the finished-node union equals that collection exactly once. Reports from
another commit, platform, attempt or CI environment are rejected. Checksums detect
accidental inconsistency; they are not independent cryptographic attestation.

Ordinary passes, existing skips, xfails and non-strict xpasses remain distinct.
A skip is never converted to a passed test. Existing pytest semantics are preserved;
this script adds no skip/xfail, retry, timeout, test-argument filter or permission.
The existing Windows TIANGONG_CI_ENV=1 convention stays unchanged. The separate
P12 native-focused suite continues without using these CI skips as native proof.

## Scope and limitations

Four files only: one existing workflow edit, a CI runner/union script, contract
tests and this record. No product source, original test, pytest configuration,
dependency lock, generated mirror, Verification Plane, Golden corpus, permissions,
store schema, branch rule or production data changes. CI jobs keep 60-minute budgets;
product timeouts are untouched. The union job has a ten-minute metadata-check budget.

Sharding changes Windows cross-module process/fixture isolation: module order is
preserved within each shard, but the entire Windows suite no longer runs in one
process. This does not prove absence of Windows-specific cross-module ordering
bugs. Ubuntu retains the complete serial run. Long-term serial validation can be
kept as a separate non-shortening observation, not misrepresented by the shard gate.
Full collection also runs once per shard, so collection-time effects repeat. No
concurrent workers share a checkout, workspace, state store or fixture filesystem.

The new contract suite covers two-root real pytest collection, subtests, fixture
and assertion failures, collection errors, skips/xfails, source mutation, unexpected
filtering, external subset options, identity mismatch, partial/duplicate/out-of-order
execution, bad checksums and missing shard evidence. Synthetic fixtures validate
the CI mechanism, not native product behavior. Local and remote results are recorded
separately against their exact tree/commit; pending CI is never reported as passed.

## Next gate and rollback

Inspect the current-candidate Windows union and all existing workflows. Only after
full gate closure should R1B move shared manifest functions, data type, exception
and strict JSON helper out of static selection, preserving compatibility identities.
P11 live matrix/faults/single-path traces/independent review/merge prerequisites and
P8/P9 debts remain open; no static path is retired by this batch.

Rollback is a revert of these four CI-only files. Main is not changed. Engineering
stage-merge progress remains P0–P10, 11/18 = 61.1%; R1A is not declared complete until
its actual terminal full-gate evidence is available.

API references used for the development tooling: pytest 9 collection/report hooks
and GitHub's official artifact migration documentation. No third-party test runner
or new dependency is introduced.
