# P9 checkpoint merge decision - 2026-09-07

Status at creation: MERGE REQUESTED, NOT MERGED OR PHASE-ACCEPTED.

The user explicitly requested: merge the current P9 work first, then begin P10.
This authorizes an engineering checkpoint transition, not fabrication of missing
product evidence, Source approvals or disabling repository protection rules.

Observed P9 product/checkpoint head: b2589b530077e9aa386f1fce13ee7099e579acf8.
Observed main: 34668e03b9f5097adde92a32e6dc8c2d7e541753.
The earlier R1/R2/R3-A/R3-B/R3-C documents remain historical evidence. Their
unfinished obligations are not erased by this decision or by CI success.

## Merge procedure

Create a normal PR to main. Generate and inspect exact-head required checks
and existing stage workflows. Never admin-merge, force-update main, weaken
checks, invent a successful status or count a skipped/absent check as PASS.
After merge, record the merge SHA, both-parent ancestry and new main head in
an append-only PR comment. Start the P10 branch only from that verified main.

## Acceptance obligations remain open

- P9 configured worker/real-model continuation, restart and replan evidence.
- Production operator key provisioning, archive retention and ambiguous crash
  orphan handling; no production deployment or signing approval is performed.
- World index v2 deployment/backup/rollback compatibility considerations.
- Full product/native certification not demonstrated by unit/integration CI.
- P8 unapproved Tool Source risk changes, actual reviewed publication, live
  X/X+1 and remaining real-task evidence; packaging patch remains unapplied.
- Other prior-stage model matrices and objective-level evidence in
  P8_P17_PROGRESS.md remain unchanged until independently verified.

## P10 entry scope

P10 concerns the Knowledge / Source Evolution / Composition Experience
learning cutover across old publication entries. First inventory the actual
learning/publishing callers and freeze unsafe old publication before removal.
Preserve a single Gateway, Runtime, WorldState, Memory, Registry and existing
Policy/Ticket/Grant, Effect/Fact, P19 and Completion authorities. Learning output
is a proposal or attributed memory evidence, never an execution permission.
Do not retire the Static Skill Planner before its separately specified phase.

This file changes documentation only. The next PR CI belongs to its actual
head; older local results and transfer-job success are not substituted for it.
