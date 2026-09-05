# Execution and reporting protocol — v1.2 master plan

Requested by the project owner on 2026-09-05. Applies to each subsequent batch.
The v1.2 master plan remains authoritative; this protocol does not change its
architecture, acceptance obligations, phase order or publication authority.

## Before editing

1. Reread current main HEAD, work-branch HEAD, PR state and relevant terminal
   CI results. Compare changes since the last accepted candidate. Never use
   July snapshots or a remembered SHA as the production baseline.
2. Locate the batch in P0–P17 and in the remaining execution-package plan.
   Enumerate prerequisites, unresolved earlier obligations, scope, affected
   authority files, all direct consumers, mirrors and recovery/rollback points.
3. Read changed authority files in full. Inspect identity, lifetime, imports,
   failure cleanup and cross-platform behavior of their callers. Do not repair
   one stack frame and assume the other consumers are compatible.
4. Define the positive, negative, integration and regression exit matrix before
   writing production code. For uncertain platform behavior, first observe it
   in the actual restricted environment; simulated fixtures are not evidence
   of native compatibility. Keep failed observations and pre-fix regressions.
5. Confirm the design preserves one Gateway/Runtime/WorldState/Memory/Registry/
   Completion authority. Do not relax risk, permissions, tests, ACLs or source
   identity checks to obtain a passing result.

## Implementation and acceptance

Edit authoritative source only. Generate mirrors and freeze declarations using
the existing official procedures, with an explicit explanation of any changed
verification surface. Run related tests during implementation; group commits
and reserve full regressions for the agreed batch boundary. Reread the remote
branch immediately before a non-forced update; never overwrite another writer.

Bind results to exact product source, trusted observer and workflow identities.
Record actual terminal outcomes, original logs, artifacts and their digests.
Distinguish code implemented, targeted tests passed, real integration passed,
full regression passed and merged. A pending test, platform skip, fixture pass
or successful build must not be reported as completed production acceptance.
Errors found while validating a batch remain that batch's unfinished work;
do not declare completion and transfer the same exit criterion to a new phase.
A new blocker may be reported honestly as partial progress, without advancing.

## Required report format

1. 完成内容 — implemented functionality, precise commit and acceptance level.
2. 错误 — repaired defects, remaining failures/unverified items, their impact.
3. 下一步计划 — a bounded work order derived from the master plan and current
   code, with prerequisites and an observable exit gate.
4. 完成总计划百分之多少 — use the explicit metric below; never invent precision.
5. 现在处于总计划列表的哪一步 — P-number, batch, and overall package position.

## Stable progress metric

Engineering-stage merge progress = (master-plan stages whose engineering
implementation is merged into main) / 18 * 100. P0 through P17 are eighteen
stages, not seventeen. At the start of P8-R3, P0–P7 are merged: 8/18 = 44.4%.
P8 remains open and unmerged; partial R1/R2/R3 repairs do not increase that
stage-completion numerator. P8 is stage 9 of 18 and is in remaining execution
package 1 of 6. P9–P17 have not started.

This deliberately simple metric is NOT an estimate of labor, elapsed time or
production acceptance. Stage sizes are unequal. P4/P6 real-model evaluation
and P7/P8 real-task evidence debt must be separately disclosed and cannot be
converted into acceptance by merged code or aggregate unit-test counts.
If the stage list or metric is changed, declare and version the change rather
than silently comparing incompatible percentages.
