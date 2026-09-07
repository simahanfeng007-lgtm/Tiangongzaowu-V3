# P9 R3-B - Task-bound Method reader and retained WorldState - 2026-09-07

Status: **R3-B implemented and locally validated; R3 and P9 remain IN PROGRESS.**
This checkpoint does not claim mandatory admission/dispatch wiring or real-model acceptance.

## Baseline and changed scope

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`.
- Branch: `codex/capability-composition-p9-method-source-evolution-v1`.
- Parent: `b6f9b977b153edf256ad34bc08581e4dd9131069` (R3-A).
- Parent tree: `ea261dae95b9fc9bc209b8e3b4569c2f75bcf212`.
- Main observed: `34668e03b9f5097adde92a32e6dc8c2d7e541753`.

R3-A could reopen exact historical Method revisions but did not retain states
beyond the ordinary history budget. R3-B adds persistent reference retention in
the SAME WorldStateStore index and a reader using the existing sealed Gateway
executable plan. It does not introduce a second task/generation authority.

## Implemented path

`existing ContextVar request/run/generation + exact Life/principal/workspace`
-> `existing GatewayStateStore sealed executable plan and active generation`
-> `plan-bound WorldState ID/hash and selected Method Source refs`
-> `existing archive verifier under runtime -> World store locks`
-> `verified exact source comparison + persistent reference retention`
-> `old Method revision returned without any latest-version fallback`.

`MethodRunSourceResolver` reads the existing Gateway APIs and checks generation
before and after archive verification. A supplied current-state ref cannot
replace the sealed plan's old ref. Unknown plans, wrong generations/principals,
missing WorldState and mismatched Method revisions fail closed. This reader is
not execution authorization: Policy/Ticket/Grant and ordinary dispatch checks
remain independent and unchanged.

`RetainedWorldState` contains an owner ID, exact state ref and scope, with a
canonical integrity checksum. It stores no task status, generation transition,
permission or execution decision. Repeated retention is idempotent; rebinding
an owner to another state is rejected. Multiple owners need separate releases.
Retained states survive ordinary history pruning without extending that history
list. The reference budget is bounded; exhaustion rejects new pins instead of
evicting live owners. Index-write failure rolls back the in-memory change.

The reader verifies sources before retaining and releases the pruning lock only
after pin persistence. The single process writer and its existing RLock remain
the concurrency model; this is not a cross-process/cross-database transaction.

## Cleanup and index compatibility

The resolver's explicit reconciliation reads durable Gateway state before
releasing references. It accepts only CANCELLED, RELEASED or a higher generation
within the same run. Missing/corrupt data, an unknown status or a changed run ID
is not interpreted as task completion. Activation/grant expiry alone does not
release a pin. A terminal read is rejected; reconciliation can subsequently
release that task's old state. These checks do not grant permission to execute.

The existing index remains `tiangong.world-state-store.index.v1` until the first
successful pin. It then uses explicit v2 with `retained_states`, remaining v2
even after all references are released. Historical snapshot schema and Gateway
SQLite schema are unchanged. A v1-only binary rejects v2 rather than silently
ignoring live references. Back up the World store before deploying this change;
rollback needs a compatible reader or a deliberately reviewed migration, not
blindly changing the schema string. No production store was migrated here.

On reopening, bounded unique retention rows and the referenced snapshot's exact
identity/scope are verified. A missing pinned file is corruption, not permission
to select the newest state. Released snapshots are deleted only when no owner,
current pointer or normal-history entry still references them. Failed deletions
may leave unreferenced files; this checkpoint does not add an archive collector.

## V3 seam and remaining first-pin gap

The existing V3 composition root exposes explicit operator installation of the
existing Gateway Store, a request/run/generation reader and reconciliation. It
reuses the same World singleton; configuration replacement is rejected. Once
configured, the earlier state-ref reader also validates against the sealed plan.
Installation/restart reconciliation is explicit; no daemon or new endpoint is
created, and no production keys or user task databases were accessed.

**Mandatory Gateway registration/dispatch integration is NOT complete.** The
pin is currently acquired by the task-bound reader. A state can still be pruned
between plan registration and its first reader call. A new test demonstrates
that this case rejects with METHOD_RUN_WORLD_UNAVAILABLE, without substitution.
This fail-closed behavior is not proof of uninterrupted task continuity.

The next R3 step must pin before a registered plan becomes dispatchable, retain
that pin across every actual execution/resumption/replan boundary and invoke
release reconciliation from the existing terminal lifecycle. It must preserve
single Gateway authority, avoid a second run table and define crash recovery
for the two-store boundary. Do not report the optional seam as universal wiring.

## Local verification

The test workspace was reconstructed from the original C5 source-revision ZIP
with all 2,911 indexed entries verified, then overlaid with the P8 sealing and
P9 R1/R2/R3-A changes. R1 files and the actual committed Action Manifest were
restored with exact Git blob checks. Native clone/raw downloads failed on DNS;
this is an artifact-derived workspace, not a native exact-parent checkout.
Untouched remote files are inherited from the actual parent tree, not uploaded
from the reconstructed workspace.

All three modified pre-existing files matched their remote parent blobs:

- `src/world_understanding/production.py`: `788e6c570aae683150b0b33a3e71201e421a82d5`.
- `src/world_understanding/world_state/store.py`: `e6592bafb7c60ecd6e96513e8cc96a5434fddee3`.
- V3 `world_understanding_production.py`: `b2583a3220bdab969bc79ad0db6b2863c0bb1d6b`.

| Observation | Result |
|---|---|
| Reconstructed R1/R2/R3-A baseline | 119 passed |
| First new retention group | 18 passed, 9 fixture setup errors |
| Expanded group before lease fixture correction | 34 passed, 1 failed |
| Final new retention/Gateway group | 35 passed, 0 failures/errors/skips |
| Final expanded focused regression | 436 passed, 0 failures/errors/skips |
| Input inventory during final run | 1,021 Python source/test files unchanged |
| Source Authority | 17 authorities, 1 alias, 24 generated targets, 1 closed-world boundary: PASS |
| Original official generator --write then --check | PASS |

The 35 tests are included in the 436, not additional independent task counts.
The completed group covers the existing executable-plan tests, R1/R2/R3-A,
P3/P4/P6, World production/state/architecture guards, P8 publication and the
unchanged P19 freeze/architecture guard. UPDATE_FREEZE was absent. No old test,
Action Manifest, permission contract, Gateway Store, P19 freeze or workflow was
edited. This is not full Python/Node, the whole P19 Golden corpus or remote CI.

Tests exercised real local Git, World index files, Gateway SQLite and a separate
Python child. After a Method update and 70 further World transactions, the child
reopened the old Gateway plan and retained WorldState and recovered the same
old Method hash. Capacity, multiple owners, wrong refs, cancellation/release,
superseded generation, corruption, write failures and no-latest-fallback were
also tested. Signing and simulation evidence are synthetic test fixtures, not
independent production approvals or real-model task evaluations.

Environment: Linux / Python 3.13.5. Final pytest time 96.65 seconds; supervisor
exit 0 in 98.50 seconds. Five existing Pydantic field-shadow warnings remain.
Locked native Windows/Python 3.12 final acceptance is outstanding.

Final JUnit SHA-256: `712bbced3c84dca77fda237c1372f98ff42921f69b98d8ceb8df9537738eb2eb`.
Final log SHA-256: `9ab256d18dd481bf233fbbcb34c77bc886b7f304319c62047c891ddf99f30b73`.
Input inventory SHA-256: `bf7fe269687030a42677170515637dead36992f1e82d9b0823ab18ac6a5cf702`.
First failed setup JUnit SHA-256: `740ce7aabe4eda1956b1d77ed6c7ece33fd7e181e6ff73280ecc30ebe22791d3`.
Expanded failed JUnit SHA-256: `4943f340d38fbcaefaf7c54862fd428f77d680d09dcd6195bf788f53abd6c1cf`.

## Preserved errors and boundaries

The first new integration fixture used a synthetic workspace ID incompatible
with the existing Gateway identity contract. It was corrected to use the real
workspace-derived identity and existing production scope builder. A later new
replan test supplied the wrong existing generation-lease API arguments; the
fixture was corrected to the actual API. No product authority or old assertion
was weakened to make these pass. Original failure logs/XML are retained.

One serialized V3 blob had an extra blank line and did not match the tested
input hash. It was not attached to any tree; the corrected upload matched.
All source/test blobs are verified against the completed local run before commit.

This is an intermediate [skip ci] checkpoint. Absent or skipped checks are not
PASS. No main merge, real Method deployment, A0/A1+ expansion, P10 work or P8
acceptance-debt approval occurs. P8's unapplied packaging patch and outstanding
Tool Source/risk/real-task evidence remain separate. The merged engineering
checkpoint metric stays 9/18 (50%); P9 is stage 10/18, and R3 remains open.
