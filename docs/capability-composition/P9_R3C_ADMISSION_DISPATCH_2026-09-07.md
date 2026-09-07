# P9 R3-C — Mandatory Method retention at admission and dispatch — 2026-09-07

Status: **R3-C implementation checkpoint; P9 is NOT phase-accepted or merged.**
No production deployment, real-model certification, Method approval, P8 debt approval or P10 work.

## Exact source baseline

- Repository: `simahanfeng007-lgtm/Tiangongzaowu-V3`.
- Branch: `codex/capability-composition-p9-method-source-evolution-v1`.
- Product parent: `5217b700cf02ae9a24f61e05824c42c54b8a97ab` (R3-B).
- Exact local checkout: `e1774c7c9a964a2a86975d7994928fa6d4cbc31c`.
  That child adds only a read-only source-transfer workflow, no product change.
- Main observed: `34668e03b9f5097adde92a32e6dc8c2d7e541753`.

Direct Git transport failed on local DNS. Instead of reconstructing another
workspace from old C5 artifacts, the read-only Actions transfer exported native
Git objects at the exact branch commit. Run `34081844791`, artifact `10003929343`,
outer ZIP SHA-256 `ccb752d5952a0f82fd31c3d46d9efcfb5ea36df061c72de9c1782d7acc4896d5`.
The native bundle hash, HEAD/tree and full `git fsck` passed, and all subsequent
work/tests use that exact native checkout. This transport job is NOT a test.

## Implemented production boundary

The existing `GatewayStateStore.register_executable_composition_plan_bundle`
validates the normal sealed plan and parent registration, independently verifies
its exact Method sources, and persists a reference in the existing World index
BEFORE the existing SQLite transaction can expose the executable registration.
A failed or missing pin rolls back the parent, executable companion and P19
registration rows together. Duplicate registration keeps the same owner/ref.
No parallel admission table, task state machine or permission authority exists.

Concrete `wst_` World references and P9 native JSON Method sources require the
lifecycle even on an unconfigured Store connection. Once operator-configured,
ALL Method plans require it. Historical non-World P7 Markdown migration/legacy
compatibility is not retired here; P12's later cutover remains a distinct task.
A durable Gateway cannot be paired with an in-memory-only World store.

`claim_effect`, `mark_effect_started` and `acquire_dispatch_permit` check the
registered exact plan's already-persisted reference and reverify source bytes
before a NEW execution boundary. They never recreate a missing pin. A reopened
second connection cannot bypass the requirement by omitting configuration.
Existing Policy/Ticket/Grant, fence, Effect/Fact and Completion checks still
apply independently; retaining source bytes grants no execution permission.

The existing V3 configuration hook installs these checks on the SAME Gateway
and World singletons. Operator trust/key provisioning remains explicit; no
keys, user databases, startup settings or production task resources were used.
Configured task reads likewise reject a missing admission pin, rather than
silently repairing the lifecycle outside admission/startup recovery.

## Commit, recovery and cleanup semantics

Cancellation, release, generation advance and Effect completion mark the outer
Gateway UoW for post-COMMIT reconciliation. A nested rollback does not release
an active reference. Side-effect-started or ambiguous/reconcile-required attempts
retain their source even when the request is cancelled; GC waits for durable
resolution. A grant/lease expiry alone is still not evidence of completion.

Known local SQL rollback may release ONLY a reference newly made by that failed
transaction, after checking no committed plan owns it. A lost COMMIT acknowledgement
cannot delete a pin for a plan whose commit actually succeeded. Post-COMMIT GC
failure is reported as `METHOD_RETENTION_CLEANUP:<exception-type>` and retains
references; it does not turn a successful terminal commit into a false rollback.

The explicit recovery hook verifies active registered plans and their exact
sources before enabling a reopened connection. A process-death orphan (pin exists,
plan absent) is retained and reported, not guessed to mean completion. This is
safe conservative recovery, NOT an atomic transaction across SQLite and JSON,
and NOT automatic garbage collection of every ambiguous crash remnant.
No new power-loss durability or multi-process World writer protocol is claimed.

An adversarial two-connection regression exposed a race in the initial R3-C
cleanup: another connection could register the same owner after the cleanup's
absence read but before its deletion. The failure was retained. Cleanup and
reconciliation now use the existing SQLite writer exclusion through the check
and release, preventing this interleaving. There is no new lock service.

Lock order for admission/dispatch is Gateway/SQLite -> WorldStore. The exact
archived Method reader does not access live Runtime graphs, so it no longer
acquires the World Runtime lock. This avoids inversion with World publication's
Runtime -> Gateway observer callback. A concurrent test holds the Runtime lock
while registration completes; another test excludes cancellation by a separate
SQLite connection during exact-source verification and reading.

## Explicit Verification Plane 1.7

The intentional Gateway authority surface change first failed the ordinary
freeze guard. Plane 1.7 is explicitly declared in the single version source and
an appended AUTHORITY_MAP entry; the official UPDATE_FREEZE generator refreshed
the manifest. The final ordinary checks run with UPDATE_FREEZE absent.

All 74 inherited source-freeze entries remain; 11 Method review/publication/
compiler/retention integration entries are added, for 85 total. The baseline,
SQLite schema 33, contract schemas, registry fingerprint, Golden baseline bytes,
Completion authority and RepairPolicy are unchanged. No required check or test
selection rule is disabled to accept this change.

Two old P7C structural guards correctly rejected the newly added registration
call in the first expanded run. Their exact call inventory is extended for ONLY
the declared retention persistence seam. New transitive helper call/import/SQL
checks and four mutation cases reject hidden Effects, dynamic dispatch, missing
pin calls and substituted plan arguments. Existing authorization bans, SQL
surfaces and all original mutation tests remain. R3-B tests now assert the
stronger registration-time pin and automatic post-terminal cleanup, rather than
asserting the previous optional-reader behavior.

## Local verification

- Exact unmodified R3-B/native-transfer baseline: **35 passed**.
- Initial expanded R3-C regression: **589 passed, 2 expected structural-guard failures**; original log retained.
- Final exact-input focused regression: **595 passed, 0 failures/errors/skips**, including **34 new R3-C cases** (not additive).
- Five existing Pydantic field-shadow warnings; no new warning or skipped crash case.
- Completed supervisor exit 0 in **270.52 seconds**; final JUnit includes every test.
- **1,337 tracked/new source-test Python inputs** unchanged during the run and rechecked before patch construction.
- Source Authority: **17 authorities, 1 alias, 24 generated targets, 1 closed-world boundary: PASS**.
- Official generated-source write/check and ordinary P19 freeze/architecture guards: PASS; UPDATE_FREEZE absent during final testing.
- This is expanded FOCUSED coverage, not full repository Python/Node, full P19 Golden or remote Windows CI.

Final JUnit SHA-256: `0c2cea3c01cdfa7b7a59865eff62b89f6b51152a6d40810e484d3c6c616228a8`.
Final log SHA-256: `9a020f824a27f35d95990ca36d93b571552166d884ea6e7d1c3c77fb366915ba`.
Final input inventory SHA-256: `0c1f4e83e267360497ee3baf946528f96c606c8be9189a3fb67b97e28faef25a`.
Initial expanded failure JUnit SHA-256: `2f65bdea54c41e03e8287e42a4ea32cb9217802bb92854960c27fb8938aeaf2e`.
Cleanup-race reproduction JUnit SHA-256: `2f6125e234ee80807639ab5684f30e920a2b57fd844ab50a6d6007b1c1a9afaf`.


The test source is a genuine exact Git checkout, not an artifact-overlay claim.
Tests exercise real disposable Git repositories, real SQLite/World index files,
70 later World events and actual process exits at three two-store boundaries:

| Injected process exit | Reopened durable result |
|---|---|
| Before pin persistence | No executable plan and no pin; no Method plan admitted |
| Pin durable, before SQL COMMIT | No executable plan; orphan pin retained and reported |
| SQL COMMIT succeeded | Plan and pin retained; same Method identity read after restart |

Additional cases verify pin-capacity exhaustion, index/COMMIT errors, duplicate
registration rollback, COMMIT acknowledgement loss, source-byte drift before
start, every Store dispatch gate, terminal rollback, in-flight cancellation,
new generations and two-connection races. Signature material is TEST-ONLY and
simulation observations are synthetic; none is a human production approval or
real-model task certification. Linux/Python 3.13 is not locked native Windows 3.12.

Preserved development errors include the first local test-edit NameError, a
new test's incorrect plural P19 table names (tightened to assert all four actual
tables exist), the reproduced cleanup race and the expected freeze/P7C authority
guard rejections. Only the test telemetry property causing an xunit2 warning
was removed; no crash-case assertion or source-identity check was removed.

## Submission and remaining gates

Because direct push remains unavailable, the locally checked unified patch is
transferred using a temporary hash-pinned Actions job. It checks the exact base,
all preimage and resulting Git blobs, rejects unrelated branch changes, and
pushes a normal non-force checkpoint on this branch only. Transport workflows
are removed after readback; they are not product runtimes or acceptance gates.
The resulting code is compared to the completed local test inputs, rather than
claiming that the transport job reruns product tests. No administrator merge or
main update occurs.

Next is the P9 final review/fault/cross-platform acceptance package, including
actual configured worker/model continuation, restart and replan evidence and
exact-final-candidate full Python/Node, Ubuntu/Windows and P14/P19 gates. This
checkpoint does NOT close those product/operational obligations. Unknown crash
orphans, archive retention, key provisioning and rollback from World index v2
remain explicit operational considerations, not silently discarded state.

P8's unapplied packaging patch and unapproved Tool Source/risk/real-task evidence
remain separate. Merged engineering checkpoint metric stays 9/18 (50%); P9 is
stage 10/18 and has not been merged. That metric is not product readiness.
