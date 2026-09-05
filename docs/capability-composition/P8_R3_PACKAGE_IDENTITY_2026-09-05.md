# P8-R3 continued: production package path identity

This supplements P8_R3_RELEASE_STAGING_2026-09-05.md in the SAME open work order.
Main remains 9a3344de9fe468fa845d2ff501166484439b8ec4, PR #73 Draft/unmerged.
P8 is phase 9/18, remaining package 1/6; merged-stage progress stays 44.4%.

## Original failure and coverage gap

Windows run 33965890291, candidate 1b8c78e/workflow 051e0a7, passed immutable
source preflight and 264 tests, but failed two existing production-package
manifest tests. Those tests carry ci_fragile and ordinary CI skips them; this
supplemental acceptance deliberately executes them unchanged, without skips.
All new private-staging cases passed, but actual build/startup did not execute
after the failed prerequisite. Thus the new container staging branch has NOT
yet been exercised in an actual AppContainer by that run.

Original artifact 9969456431, ZIP SHA256:
5c3dcc34f7c48a05846d03b8ee10e865898cbb063c6bc6a80a8e3a57b0b9ff4d.
Original JUnit: 266 cases, 264 passed, 2 failed, 0 skipped.

## Explicit Verification Plane 1.6 follow-on correction

The existing production generator compared a resolved long archive name with
the original short spelling of both archive and runtime. Windows now observes
both canonical names through the existing no-reparse path verifier, then
checks their exact relative physical binding. Legitimate 8.3 spelling may
expand; an ancestor junction, outside-runtime archive or failed native identity
cannot pass. POSIX retains strict resolution/canonical spelling. The shared
packaged executable also uses the existing checked-file helper, rejecting an
ancestor redirect before reading component bytes. Hashes, canonical manifest
encoding, single-runtime component topology and final output verification
remain unchanged. No ACL, publication, permission, Registry or Runtime change.

Both original failing tests remain unchanged. Added tests cover native denial,
outside-runtime archives, real long/8.3 complete-manifest equality, DOS lookup
denial and real ancestor junctions for both desktop and executable paths.
Two portable native-denial tests failed before the repair. Local related tests:
63 passed, 7 Windows-only skipped; ordinary freeze guard: 6 passed. These are
not native startup evidence. The original tests and all four new actual-Windows
cases must run without skips in the supplemental workflow.

The official freeze generator changes only the existing release_manifest.py
hash. Mirrors use the official sync command; source topology and committed
mirror checks pass. No fixture canonicalization, global TEMP rewrite, broader
risk admission or ci_fragile skipping is used to hide the failures.

## Exit

Keep the real policy-only ancestor 4bfa94e and original b37204e unchanged. Pin
the actual repaired candidate/trusted parent, run preflight, expanded Windows
regressions, actual AppContainer build/startup/shutdown/source consistency,
and final required gates. Any failure remains unfinished R3. Even success here
would not close all P8 publication/evidence/version-lock/model/task obligations.
No P9 work, main merge, Source publication or A1+ admission is authorized here.
