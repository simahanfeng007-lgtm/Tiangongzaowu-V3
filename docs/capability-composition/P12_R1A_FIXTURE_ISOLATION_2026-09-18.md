# P12 R1A — deterministic fault injection and test-fixture isolation

## Exact baseline and bounded scope

Continue PR #78 on its existing branch from `66327179f897efcbbf542dc3756e29db687b2e55`,
tree `d3b5c631d0cd10a33ed1dafdf98388a11d90bf40`. Main is not merged or modified.
R1B shared-manifest separation has not started; this batch closes two concrete
R1A regression-fixture defects before further product refactoring.

Four files change: two existing test modules, the existing read-only P12 focused
workflow, and this record. Product code, dependency locks, pytest configuration,
Windows shard allocation/union rules, original epoch/native tests, Source Authority,
generated mirrors, Verification Plane 1.16, Store v33 and Golden corpus are unchanged.
No new skip, xfail, retry, permission, timeout, runtime or production fallback is added.

## Original upstream failures, not an incomplete-run guess

At the baseline, P14 run `35301340111` and P19 run `35301340051` succeeded.
Architecture run `35301340082` failed: seven Windows shards succeeded, shard 6
failed, and the union correctly rejected it. Ubuntu serial regression also failed.

- Windows job `105464428618`, artifact `10529927822`: 665 passed, 1 failed,
  2 skipped, 64 subtests. Its complete collection contained 5,347 items, of which
  668 belonged to this shard. Failure was the original production-checkpoint
  rollover test in `tests/test_p18_m1_execution_epoch.py`.
- Ubuntu job `105464428288`: 5,276 passed, 1 failed, 70 skipped, 938 subtests.
  The failing shadow-context corruption test expected HTTP 409 but received 200.

The Windows artifact ZIP was downloaded and SHA-256 verified:
`f2348b19720db561f9725891700da3338f81d812f440884e3a949c83f934065a`.
Architecture checked the PR merge `5f4af42cd355e6f4a35bfd4ac0f2eb4cfc8f3316`,
whose recorded source tree equals the baseline tree. This is not relabeled as a
branch-HEAD checkout. Original red evidence is retained in the delivery package.

## Root causes and corrections

The shadow test replaced the final random ciphertext byte with literal `x`.
When that byte was already `x`, it did not corrupt anything. A controlled valid
AES-GCM fixture ending with `x` reproduced HTTP 200 with unchanged ciphertext.
The test now flips one bit and rejects empty fault fixtures. All original HTTP 409,
error, snapshot-preservation and mutation-rejection assertions remain. A new test
covers every possible final byte (256 subtests), unchanged prefix/length, and empty
input. No decryption or integrity-check implementation changes are needed.

The real Gateway-start integration test installed process-global continuity and
regenerative providers, then closed its runtime without restoring the surrounding
test's providers. The writeback module also calls that same test helper. In the
failing shard order the later epoch test therefore reached a closed runtime, and
the production provider path correctly refused continuation rather than falling back.

Before starting the real Gateway, its test fixture now registers the previous two
provider identities with pytest's existing monkeypatch cleanup. Real startup still
installs its actual callbacks and all authority checks run unchanged; on fixture
exit the exact prior objects are restored, including when startup raises. A new
nested-scope regression verifies identity restoration and that surrounding providers
were not called. The original epoch test and canonical fail-closed tests are untouched.
This is test-lifecycle hygiene, not a claim of a production restart/lifecycle redesign.

## Reproduction and local evidence

Local Linux: Python 3.13.5, pytest 9.0.2, Pydantic 2.13.4. These differ from CI locks.

- Original epoch module alone: 10 passed. The 415-item shard predecessor sequence
  reproduced the defect: 413 passed, 1 failed, 1 skipped, 62 subtests.
- The new provider identity regression failed against the original fixture, then
  passed after its cleanup was added. The unchanged 415-item predecessor sequence
  then passed: 414 passed, 1 platform-only skip, 62 subtests in 121.32 seconds.
- Controlled valid trailing-`x` ciphertext reproduced the old no-op mutation;
  the same controlled fixture passes the repaired original corruption test.
- New focused same-process selection (Gateway predecessor, restoration, epoch,
  canonical continuity, delivery boundaries and shadow): 37 passed, no skips,
  256 subtests in 13.97 seconds.
- Existing CI-sharder contracts: 29 passed, 33 subtests. Original R0/R1A diagnostic
  suite: 54 passed, no skips. Normal Source Authority and committed mirrors passed.

Groups overlap and must not be summed as unique tests. The 415-item sequence is
not a full repository or native Windows run. Warnings remain in original logs.
The existing P12 workflow now runs the same predecessor/epoch/shadow selection on
both platforms in addition to every previous focused group. The full Architecture
workflow still runs all collected tests, with the original eight-shard union gate.

## Exit boundary

New candidate CI must be read separately. No old successful shard, P14, P19 or
native result is inherited as approval of the new commit. R1A closes only after
current-candidate complete gates; R1B resumes thereafter. P11 live observations,
controlled faults, single-path traces, independent review and merge remain required
before static-path retirement. P8/P9 obligations remain. PR stays Draft/unmerged;
engineering-stage merge progress remains 11/18 = 61.1%.
