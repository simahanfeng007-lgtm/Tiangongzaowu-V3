# P8-R3 continued — explicit private profile and constructor cleanup

Same unfinished R3. P8 phase 9/18, remaining package 1/6, merged-stage metric
8/18 = 44.4%. PR #73 stays Draft, main 9a3344de unchanged, no P9 or publication.
Starting workflow head ba8a3f230d7e5d24087a7117b8a161ebcecc38d0;
product/trusted observer f6c38783be0c7499f0d771843e45d26e50e3b492.

## Native observation and pre-edit scope

Run 33968240885 / job 101312054320 passed immutable preflight and all
277 Windows cases with zero skips/failures. Actual AppContainer build passed.
The contained startup child verified 1,965 source/mirror entries, generated
the release, passed GatewayConfig and opened the actual Gateway Stores/lease.
It then failed in SoulBackupManager.default_sources: Path.home() cannot resolve
because the sandbox correctly strips ambient HOME/USERPROFILE. Gateway did
NOT reach READY. Parent reverified all 2,889 staged files; no source drift.
Artifact 9970239816, SHA256:
8cdfa5302fc49361e67dce521bb9fcca590a4b18ab842874108f83a7c104ede0.
Every artifact checksum and the original JUnit/report were independently read.

Reviewed the child environment setup, shared bootstrap/constructor cleanup,
SoulBackup default sources and all literal HOME/profile/cache uses in the
startup consumers, including legacy body settings and communication migration.
The intended fix is a PRIVATE profile, never restoring the actual host profile.
The failure also exposed that cls(...) was just outside the existing Store
initialization cleanup guard. A real Store/epoch regression confirms the leak.

## Explicit Verification Plane 1.6 addendum

Before any candidate import, the existing trusted probe exclusively creates
fresh home, appdata, localdata, tmp, documents, life-data and life-runtime
folders under its one parent-owned sandbox workspace. HOME/USERPROFILE,
APPDATA/LOCALAPPDATA, TEMP/TMP and explicit Life/document roots use those paths.
Alternate ambient HOMEDRIVE/HOMEPATH are discarded. Existing paths are not
reused or followed. Initialization errors retain a private_environment failure
report; all approval flags remain false. No host profile, configuration or
credentials are copied. No ambient permission or existing ACL is changed.

The only product-source edit moves the existing Gateway constructor call
inside its existing resource initialization try/except. Constructor failures
now follow the same facts -> objects -> Store -> epoch cleanup as earlier
Store-open failures. There is no new Runtime, lifecycle entry, permission,
release selection or completion rule. This does not alter the existing handling
of a cleanup operation itself failing. Normal dependency-aware runtime.close
and service assembly remain unchanged.

Five red-first regressions: missing/foreign ambient home, two preexisting-home
rejections, and constructor Store/epoch cleanup. Actual host pathlib and the
standard Windows ntpath expansion must both point to the private home. A host
sentinel is unchanged; failures occur before candidate imports and service
assembly. The constructor test uses real SQLite Stores and a real epoch lease,
and verifies that a retry can acquire it after cleanup. Service seams in probe
tests are simulated, not native Gateway proof.

Local related suites: 78 passed; separate bootstrap/authority suite: 19 passed
plus six passing subtests (suites overlap). Final native evidence is still
required. Official --write/--check-committed mirror generation is used. Refresh
only the already-covered child-probe and runtime.py hashes with UPDATE_FREEZE,
then test with that flag absent. No authority entry or Golden corpus is removed.

## Exit and next work

Pin the actual revised candidate and trusted observer, preserve original
b37204e and policy-only 4bfa94e ancestry, and retain all native tests plus these
regressions. Observe actual AppContainer build -> Gateway READY -> clean close
-> child and parent source identity, then terminal exact-head required gates.
Failures remain this same R3 work order. If all R3 gates pass, the next bounded
work order is P8 semantic Manifest risk/alias/schema review, not P9. Source
publication/evidence contracts, live X/X+1 locks and real-model/task debt remain
separate unfulfilled P8 obligations; startup success never approves them.
