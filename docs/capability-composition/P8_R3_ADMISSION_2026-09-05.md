# P8-R3 continued: explicit core-policy lineage and candidate preflight

## Position and bounded scope

Same open P8-R3 work order, not P9 and not a new completion claim. Main reread
as 9a3344de9fe468fa845d2ff501166484439b8ec4; work head as
13f09fe27574a5c7c3b08498878e48bd50c9846e. PR #73 remains Draft/unmerged.
Master-plan engineering merge progress remains 8/18 = 44.4%; P8 is phase
9/18 and remaining execution package 1/6. This is not production readiness.

R3's preceding Architecture run 33959475630 is now terminal SUCCESS for all
four required jobs, including Windows full regression 101288706619. Those
results apply to the preceding product/PR merge, not this new candidate.
The previous native admission failure remains original evidence.

## Pre-edit audit

Reviewed the v1.2 master plan, execution protocol, existing candidate classifier,
Git ancestry requirements, source ownership/topology validator, official mirror
generator, build/probe scripts and the full changed-file inventory from b37204e
to 13f09fe. Reproduced the unowned helper rejection in a test before updating
policy. Classified all 26 preceding changed paths plus the three proposed
validation/document additions against the corrected policy before submission.
The policy change is a separate core-maintenance proposal, not a candidate's
self-amendment. Generated targets and runtime code are unchanged this round.

## Core policy and immutable ancestry

The independently inspected policy-only object is
4bfa94ef918a18e33a939dc01d90b969ccf64a7f, parent
b37204ee4d94e4857aaf477b810a21f19345d584. It changes ONLY source-ownership.json
by adding the exact existing src/runtime_security/path_identity.py mapping.
Its tree a0780a47588591f1cbbab77827f9c0d6818d1480 retains every original source
blob, including the OLD helper. No editable root, generated target, Registry,
Runtime or execution permission is added. This is not production approval.

The integration commit uses current work head as first parent and that
policy-only object as second parent. Therefore the policy base is a real
ancestor, the candidate policy is byte-identical to it, and the complete old
helper -> repaired helper change remains visible in the candidate envelope.
No history rewrite, graft, force update, candidate-classifier exception or
moving repaired code into a fabricated old baseline is used.

## Read-only preflight and tests

scripts/preflight-tool-source-candidate.py calls the existing native-Git
candidate inspector; it never builds, imports candidate modules, publishes,
updates a Registry or grants authority. Additional checks reject any baseline
source edit, other mapping/root/boundary change, hidden helper change, absent
original helper, non-ancestor policy base or candidate policy amendment.
Every observed result keeps may_publish/may_authorize/may_execute false.
Reports are exclusive-write and retain exact original/policy/candidate IDs.

New tests exercise real synthetic Git branches and two-parent integration,
policy self-amendment, sibling/frozen changes, directory replacement, hidden
changes, dirty checkout and report overwrite. The unchanged original candidate
suite runs alongside them. Local Python 3.13.5 focused tests: 47 passed, zero
failures/skips. The ownership regression failed first on the old policy. Local
source topology and official committed-mirror checks passed. These runs use
the unchanged candidate-inspection implementation from an exact reconstructed
R2 source tree and the proposed policy/script/tests; they are NOT full current
product, native-Windows or release-lock acceptance. Current Windows execution
must run the actual pinned integrated candidate and expanded tests.

No runtime_security/path_identity.py edits are included in this continuation.
The previous unpushed DOS device/control-name hardening is not silently
represented as delivered; it remains a separate open review item. This round
preserves the helper bytes that passed the preceding full cross-platform CI.
The connector's previously rejected source write is not retried through an
alternate transport or hidden workflow write. All writes use normal connector
Git object/ref actions; supplemental workflow permissions stay contents:read.

## Exit and next work

Before any contained source build, the supplemental Windows job must record
successful native-object policy/candidate preflight. Candidate/workflow/trusted
parent identities are explicit. Unsupported containment, skipped native tests,
false READY, cleanup errors and source drift remain failures. Existing required
workflows, source classifier, freeze hashes and Golden expectations are unchanged.

R3 acceptance requires affected Windows regressions plus actual AppContainer
build -> Gateway READY -> clean shutdown -> both child and parent source
consistency, and final required regressions. Any failure remains unfinished
R3 work. P8 publication, full permission-delta/evidence-contract review, live
X/X+1 run locks and real-model/task evaluation are separate open obligations.
No transition to P9, Source publication or main merge follows merely from a
successful preflight, build, test count or offline Gateway probe.
