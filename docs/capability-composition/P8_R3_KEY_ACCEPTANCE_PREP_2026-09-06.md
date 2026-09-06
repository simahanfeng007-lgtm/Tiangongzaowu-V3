# P8-R3: local key-acceptance preparation; remote source write blocked

## Identity and scope

Main was reread at `9a3344de9fe468fa845d2ff501166484439b8ec4`.
P8 branch/PR #73 remains at `50feb1684012370c15366716f010ac57a7c979f4`,
tree `4a1802ad93c0fb7cdfe2fdbdc2ea479b58886763`. PR is Draft/unmerged.
The preceding local source archive was hash-checked and reconstructed as
Git tree `33f92b52d52e1f8ecebbb40d6c735d2eb0805100` before any editing.

This continuation reviewed the existing proposed private-file helper, ticket
integration, release-staging integration, native worker and supplemental
workflow. The product implementation is UNCHANGED from that prior local
candidate. No source-policy, runtime, DPAPI, permission, Registry, P19 or
Completion change was added in this continuation. It remains the same open
P8-R3, not a completed phase and not P9.

The normal UTF-8 create_blob request for the reviewed helper again returned
"OpenAI could not determine the safety status" and was blocked. No product
blob/commit/ref was created in this continuation. No alternative upload,
encoding or transport was attempted. The new tests/workflow below are LOCAL
ONLY, not changes to an online workflow or proof of a native run.

## New local validation coverage

1. Nineteen binary-ACL tests exercise the actual `_observed_dacl` parser with
   controlled API buffers, rather than replacing its output with SDDL text.
   They cover exact numeric SIDs, absent/NULL/defaulted/unprotected DACLs,
   unexpected ACE counts/types/flags/rights, invalid SID lengths, wrong/broad
   principals and descriptor/ACE query failure. Allocated observations are
   released on rejection. These are portable fixtures, NOT OS access checks.
2. Seventeen lifecycle-report tests require matching environment, every
   lifecycle outcome, exact booleans and non-authorizing flags. A nominal
   success with an error/cleanup/failure field is rejected, even if empty.
3. Host and actual-AppContainer lifecycle cases are independent parameterized
   tests. A failed host control no longer prevents collecting a contained
   outcome in the same pytest invocation. Original process and observation
   reports can be retained before PASS assertions in a CI-owned evidence
   directory; only reports are copied, never test keys or ciphertext.
4. The local supplemental workflow retains all 21 prior test-file entries and
   adds three relevant files. An explicit native-key step precedes whole
   Gateway build/startup. Native skips or any failed prerequisite prevent the
   whole-boot step. Read-only workflow permissions are unchanged.
5. The identity step hashes the exact helper, ticket implementation and new
   tests from the checked-out source. Old pins lacking the helper/tests fail
   before dependency installation instead of accidentally validating old code.

IMPORTANT: the local workflow still contains the existing prior product pins
`7b73217a560b8bad7ae52d00fa12fe3a0b31efbc`. They are NOT a new candidate.
No accepted source commit exists to substitute. The explicit old-pin failure
is intentional until normal source submission is possible and both observer
and candidate pins are updated to the reviewed new product commit. Do not run
this workflow under stale pins and report new-candidate acceptance.

## Evidence observed in this continuation

Linux / Python 3.13.5; pytest 9.0.2, pydantic 2.13.4, cryptography 46.0.4.
This environment does not match the Windows release lock.

Final affected suite: **178 ordinary passed, 6 subtests passed, 3 skipped,
0 failed/errors**. The JUnit has 181 testcase elements and a tests=187 total
including six subtests. It is not 187 independent ordinary passes.

The three skipped cases require native Windows: independent host and
AppContainer key lifecycle, and the existing private release-stage contract.
No Windows execution of this new candidate occurred. The existing remote
Architecture/P19/P14 successes belong to the unchanged ONLINE product only.

The separate key-focused suite returned 64 passed, 2 native skips; it overlaps
with the final affected suite and must not be added to its unique count.
The binary-ACL-only run returned 19 passed and also overlaps.

Source-authority topology and official `--write` / `--check-committed` mirrors
passed. The existing normal P19 freeze guard was included in the affected
suite and passed without UPDATE_FREEZE. No new freeze refresh was needed:
this continuation does not change the already-declared product surface.

All supplemental Python step bodies compile. The checked workflow has 24 test
file references, including all prior 21. These static checks do not establish
GitHub Actions or native Windows acceptance. Two local command-invocation
attempts failed before pytest (unsupported streaming session, then absent
log directory); neither was counted as test evidence. The actual test runs
completed and retain their original logs/JUnit.

## Required next gate

Source publication is blocked on the platform's normal source-write path, not
on another newly discovered production traceback. Repeated permission prompts,
old-CI reruns or renamed work orders do not supply missing source acceptance.
Do not repeatedly resubmit the same blocked payload or use a different route.

Once an allowed normal source-write path is restored: reread current HEAD and
any intervening changes; review and submit the exact candidate; bind the
existing observer and runtime-candidate pins to it; preserve original b37204e
and policy-only 4bfa94e ancestry; run immutable candidate preflight and the
actual ProtectedKeyStore lifecycle, then full native Gateway startup and final
required cross-platform gates. A complete R3 exit remains:

AppContainer build -> Gateway READY -> clean shutdown -> matching child and
parent source evidence -> exact-candidate required regressions.

No native READY, clean Gateway shutdown or child post-shutdown source proof
is claimed. P8 still also requires Manifest risk/alias/schema review, evidence
contracts/publication, live X/X+1 run locks and real model/task evaluation.

Progress stays **8/18 = 44.4% engineering stages merged**, not workload or
production readiness. Current location: **P8, stage 9/18, remaining package
1/6, P8-R3 OPEN/BLOCKED**.
