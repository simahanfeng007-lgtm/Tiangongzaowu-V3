# P9 R3-A - Method archive, one-ingress publication and invalidation - 2026-09-07

Status: **R3-A implemented and locally validated; R3 and P9 remain IN PROGRESS.**
This checkpoint does not close running-task/generation binding or stage acceptance.

## Baseline

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`.
- Branch: `codex/capability-composition-p9-method-source-evolution-v1`.
- Parent: `93adea30fe93a5e282e7d93123b3f45a1c8e6b87` (R2).
- Parent tree: `1bf7bd78ccc32b00f41d31eb4117d09958853410`.
- Main observed: `34668e03b9f5097adde92a32e6dc8c2d7e541753`.

R2 stopped at a prepared semantic revision. R3-A now exercises actual local
archive writes, native Git reads, the existing ingress and a persistent World
head update. Production operator provisioning and running-task integration
remain separate, explicit exit requirements below.

## Implemented path

`R2 source/evidence/review bytes + exact repository/worktree/frame/current head`
-> `separate domain-separated publication signature`
-> `immutable content-addressed archive (no current pointer)`
-> `SYSTEM_GOVERNANCE reference-only envelope`
-> `existing facade.accept / WorldUnderstandingIngress`
-> `Gateway resolver rechecks signatures, current head and native Git bytes`
-> `existing Method contribution compiler and WorldState materializer`
-> `existing single WorldStateStore index commit`.

The Gateway archive records the original source JSON, candidate, base snapshot,
legacy/native provenance, signed review and every simulation observation. It
also binds publication time, exact SoftwareWorldFrame, prior WorldState ref,
prior archive and reconstructed R2 revision. A staged archive is not approval
and cannot select itself. The existing WorldCut owns archive/snapshot selection.

Admission uses existing P8 native Git object and source-ownership readers with
an independently configured policy commit. Modified working-tree bytes are not
substituted. A valid R2 signature cannot admit a source document absent from the
pinned authoritative commit. No candidate Python import or code execution occurs.

The pure World seam replaces only the Method contribution. Unrelated graph
records, cognition/hypothesis heads, uncertainties, conflicts and dependency
bindings are preserved. ADD/UPDATE/REMOVE use the existing contribution compiler
and refresh the graph/source references rather than creating a Method registry.

The existing store gains an exact-head publication transaction using its own
RLock. Its existing snapshot-first/index-second write remains the commit point.
A stale competing publication is rejected. An injected index failure preserves
the prior head and live graph; the same source event can retry successfully.
No new database, current-pointer file, Gateway, Runtime, scheduler or listener
was added. WorldState/store wire schemas and P19 freeze remain unchanged.

## Production composition seam and historical reads

`configure_production_method_publication(resolver)` installs explicit operator
configuration on the existing V3 singleton. It neither provisions keys nor adds
a model/HTTP/tool route. It is disabled without configuration, and configuration
must be supplied again after process restart.

`production_method_world_for_state(state_ref, run_context)` checks the existing
Life/principal/workspace scope and resolves exactly that WorldState. Missing,
evicted, wrong-scope or mismatched state refs fail closed; there is no fallback
to the latest method revision. The archive is reopened and signatures/source
bytes reconstructed; its own success flags are not treated as authority.
Historical review validity is evaluated at the signed publication time, not
used to authorize a new publication after expiry.

An actual separate Python child process reopened an older retained WorldState
after a newer Method update and recovered the same old Method snapshot hash,
with no source Git repository available to the child. This is real local
process/disk evidence, NOT a real-model running-task/replan acceptance result.

## Reproduced existing invalidation defect

The original materializer marked a dependent record stale after a source change,
then silently dropped that stale ref on a later unrelated cut. A reproduction
using the preserved original materializer and original input API produced stale
counts `1 -> 0` and failed the retention assertion. The original failure log
and reproduction script are retained.

The fix carries forward unchanged stale refs. Only replacement, removal or
explicit cognition revalidation can clear the corresponding prior invalidation.
Ordinary source events also retain Method/dependent bindings instead of erasing
the information needed by the next Method revision. New integrated and isolated
regressions cover both event sequences.

## Local verification and evidence limits

The workspace was reconstructed from the original C5 source-revision archive;
all 2,911 indexed entries passed size/hash checks. Committed Action Manifest,
main's sealing checkpoint and P9 R1/R2 files were restored separately. Native
clone failed on DNS resolution. This is NOT a claimed complete exact-parent
clone. All four modified existing files matched the remote parent Git blobs:

- `production.py`: `867431ec03622f56225ff6bd3df038a6264c0839`.
- `world_state/store.py`: `f6a82bb137820e753eb7be7217549ae1d73367a0`.
- `world_state/materializer.py`: `dc63dc708faf4bedae2472ed251825d3ab03096c`.
- V3 `world_understanding_production.py`: `1dc304df133dfdead51fdfc3af260a8470dfa792`.

Remote construction retains untouched files from the actual parent Git tree.
No reconstructed whole-workspace upload or manual runtime mirror edit is used.

| Observation | Result |
|---|---|
| Restored R1/R2 baseline | 88 passed |
| Initial existing integration regression | 114 passed |
| Expanded new R3 tests before fixture correction | 26 passed, 1 failed |
| Final new R3 fault/integration group | 31 passed, no failures/skips |
| Final expanded focused regression | 271 passed, 0 failures/errors/skips |
| Exact source/test inventory during final run and subsequent readback | 1,018 Python inputs unchanged |
| Source Authority | 17 authorities, 1 alias, 24 generated targets, 1 closed-world boundary: PASS |
| Original official generator --write then --check | PASS |

These overlapping groups are NOT additive unique task counts. The final run
includes the 31 new cases, R1/R2, P3/P4/P6, production activation/World state,
semantic and integration guards, P8 publication/preparation/sealing, and the
unchanged P19 freeze/architecture guard. UPDATE_FREEZE was absent. This is not
the full P19 Golden corpus, full repository Python/Node or native Windows CI.

Environment: Linux / Python 3.13.5. Final pytest time: 46.57 seconds. Five existing
Pydantic field-shadow warnings remain. Tests use real disposable Git repositories,
real filesystem/index writes and a real reader subprocess. Their review keys
and simulation observations are synthetic TEST fixtures, not human approvals,
Windows AppContainer results, real model evaluations or production deployment.

The one new-test failure used an entity refreshed by an ordinary source event,
then incorrectly expected its previous ref to be stale. The test now seeds a
separate dependent entity and reopens the canonical store before exercising the
sequence. No existing assertion was weakened. The separate original-materializer
reproduction still demonstrates the actual stale-retention defect.

During blob serialization, the first V3 upload contained a mistyped existing
query method name. Its hash differed from the tested input, so it was rejected
before tree creation. The corrected blob matches the tested file exactly. The
mismatching object was not attached to a branch or used as test evidence.

Final JUnit SHA-256: `48f5ffc6b005a1118567c17f43fe7a44327f6ab2f474b25f7341fefa8b3640be`.
Final log SHA-256: `52e14b966f8a988506a25d8a22fa8c065d22969c15fcfb9fd5ebd2b6a9113d4b`.
Input inventory SHA-256: `77098e5bc19882bc6c43624d52a24408eae64c40976c57c81b7398cca28897e8`.
Original stale failure log SHA-256: `d4545765b628b0f1ac690af9df9e0911e883cf9906b0415a248941e575bec262`.

## R3 remaining exit requirements

The exact historical reader is NOT yet automatically wired to every active
composition request/run/generation or replan boundary. That integration must
reuse the existing plan/source bindings, not create a parallel run authority.

The existing store retains a bounded history (default 64 states per frame).
An old state may be evicted while a task is still running. This checkpoint
rejects an unavailable pin rather than switching versions; it does NOT yet
provide active-run retention. Retention/release and restart cleanup must be
integrated with the existing lifecycle before claiming full running-task locks.

The transaction is the existing single-process writer with an in-process lock,
not a multi-process writer protocol or a new power-loss durability guarantee.
Archive read-only mode checks are not Windows DACL or same-user mutation proof.
Archive retention/cleanup and operator key provisioning remain operational
requirements; no production keys were accessed or generated in this work.

Next: complete request/run/generation selection and active-pin retention, then
exercise real task continuation/restart/replan around Method updates. Only after
R3 closes should R4 run the final fault matrix and exact-final-candidate full
Python/Node, Ubuntu/Windows, P14/P19 gates and normal PR review/merge.

This is a focused intermediate checkpoint using [skip ci]. Absent/skipped CI
is not PASS. No main merge, permission expansion, P10 work or P8 debt approval
occurs. P8's unapplied packaging patch and outstanding Tool Source/risk/real-task
acceptance remain separate. Merged engineering checkpoint metric stays 9/18
(50%); P9 is stage 10/18, with R3-A complete and R3 still open.
