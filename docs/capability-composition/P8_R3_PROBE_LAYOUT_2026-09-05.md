# P8-R3 continued — bounded source-probe layout

Same open R3 work order. P8 is phase 9/18, remaining execution package 1/6;
merged-stage progress is 8/18 = 44.4%, not production-readiness percentage.
Main reread at 9a3344de9fe468fa845d2ff501166484439b8ec4; current work head
871306b064a29dedfe47d50bb15764b722417eae. PR #73 is Draft and unmerged.

## Current source and original evidence

Resume from the actual latest source, not the earlier 13f09fe checkpoint.
The core-policy integration, candidate preflight, contained release staging
and package-identity corrections are already committed. Their original
failure records remain; none is retroactively reported as a successful boot.

Run 33966811766 tested product/trusted parent 75a6cd8f02f2721672581fc34dbabb07866d5539,
source tree f1415fa90dfa7e9a42161a6edbfb8564557a9e1a, workflow 871306b.
Immutable candidate preflight passed, Windows regressions: 273 passed, zero
skips/failures. Actual AppContainer build passed. The child verified 1,965
source/mirror entries and generated the release. GatewayConfig then rejected
the selected deep _internal/omni_body_skill mirror as longer than 240 chars.
No READY or successful Gateway shutdown was observed. The parent independently
verified all 2,887 staged files after the attempt; this is not a boot proof.
Artifact 9969804337 ZIP SHA256:
a1b9d79f475a43cb5ab9fce896a73e99a4d2f8dc1cb6ca19debc469bce1a4578.
Original reports, JUnit and all SHA256 entries were downloaded and checked.

## Pre-edit audit and explicit plane 1.6 addendum

Reviewed complete child/parent probe scripts, GatewayConfig path validators,
source preflight's owned-skill roots, source policy and current integration
workflow. The native source root is 187 chars long; the selected generated
mirror adds 54 chars and crosses the unchanged 240-char configuration bound.
The authoritative src/omni_body_skill belongs to the SAME staged installation,
is already explicitly accepted by the existing source preflight, and is much
shorter. There is no reason for this source probe to select the deepest mirror.

The only runtime-facing edit is the trusted offline worker's explicit skill
root: select source/src/omni_body_skill. There is no fallback, copying, path
alias, permission widening, GatewayConfig limit change, Registry change,
source-policy amendment, Runtime change or host ACL/registry mutation. All
mirrors remain checked by source consistency regardless of the selected root.
The selected release and source inputs remain pinned. This is not a production
publication or live-task source-lock claim.

Two new red-first deep-path tests reproduced the rejection using the REAL
GatewayConfig validators with simulated service seams. The repaired layout
passes at both depths; an overlong workspace still rejects before Runtime
assembly. Release failure still prevents assembly. These fixtures are NOT
native AppContainer acceptance. Local Python 3.13.5 related tests: 69 passed;
normal existing freeze/architecture guard: 6 passed. Source topology and
committed generated mirrors pass. The official UPDATE_FREEZE generator changes
only the existing child-probe hash; no Golden or authority entries are removed.
All four proposed changed paths classify as validation/documentation inputs
under the existing policy. No new core-policy revision or classifier exception.

## Required exit and next work

Pin the exact repaired parent/candidate in the supplemental workflow; retain
the original b37204e and separate policy-only 4bfa94e ancestry. Add the new
layout tests without removing any existing native cases. Review actual
AppContainer build -> Gateway READY -> clean shutdown -> child and parent
source consistency, plus final exact-source required gates. Any failure remains
unfinished R3. Even a successful integration does not close P8 permission-delta,
evidence-contract, publication, live X/X+1 or real-model/task obligations.
Do not begin P9 or merge main based only on this offline probe.
