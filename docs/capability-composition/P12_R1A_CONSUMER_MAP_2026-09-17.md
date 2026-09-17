# P12 R1A — consumer mapping and retained-capability regression

## Checkpoint and scope

Continuation of PR #78 on the SAME branch, not a parallel product implementation.
R0 baseline: `5d3ba57b135d8376f11b6b280e9f1f5939ea3803`, tree
`fbe392b37cb14f859c8da5c9b22bc54887d2178a`. Main remains `bf29542b3048c8d1806add8063b0db7c72be055b`.
P11 PR #77 remains Draft/unmerged at `3e8a6f0ffbf1488cc1e3f954773917b01767ced9`.

At entry, R0's previously pending Architecture/P14/P19 workflow conclusions were
re-read and all were success. This is old-candidate evidence, not approval of R1A.
The current batch is P12 preparatory engineering, not formal phase exit, production
cutover, compatibility removal or a statement that P11 acceptance has completed.

Three additions and one developer-workflow modification only. No product source,
Runtime/Gateway/Store, Source ownership, generated mirrors, permissions, P19
freeze/Golden baseline, release entry or live database is modified.

## Important dependency findings

`src/total_gateway/skill_selection.py` mixes the legacy catalog/selector with
`load_model_capability_manifest` and `compile_composition_execution_manifest`.
The existing orchestration imports and calls the latter. The whole file cannot
be retired as an old Planner: dynamic execution depends on its manifest/schema/
permission join. The map explicitly retains both shared functions.

`src/omni_body_skill/tools/skill_router.py` mixes static query actions with
`skill.step.check` and fact-derived `skill.progress.report`. A filename containing
Skill is not grounds for removing machine-fact progress or Gateway authentication.
The explicit-skill context helper exposes user selection without activating Skill
content; it needs individual review, not blanket removal of all skill_context names.

The static index declares 34 items at the inspected baseline. R1A reads its actual
count, pins every referenced document to the R0 blob and enumerates the union of
six required-action fields. A preserved document is not a migrated capability.
Every item remains `REQUIRES_TASK_PARITY`, with replacement behavior unproven.

## Executable map

`scripts/audit-p12-retirement-map.py` reuses the original R0 native Git reader.
The R0 observer is unchanged and verified against its original SHA-256 before
loading. No product Python is imported. The new observer's bytes must match HEAD.

Seventeen review anchors resolve to actual file/definition ranges and hashes.
Five review surfaces link legacy entry, candidate replacement, preserved shared
functionality and exit obligation. Missing/changed/duplicate required definitions,
missing/changed corpus items, unsafe paths and malformed indexes are errors.

AST analysis records import bindings and conservative call candidates, including
aliases and relative imports. It is explicitly NOT a sound call graph: shadowing,
re-exports, reflective dispatch and external consumers still need review. Dynamic
sites, parse failures, unscanned inputs and truncation are disclosed. ASTs are
processed one file at a time to bound memory. R0 lexical coverage remains attached.

The new scope check compares against R0 and permits only these three additions
and the workflow modification. The original four-file R0 scope rule is untouched:
its scope result on an R1 delta stays false and is explicitly recorded, not relabeled
as a pass. R1 has a separately named, non-authorizing development-scope check.

`mapping_gate_passed` means source inventory, anchors, preserved corpus and this
bounded developer delta passed inspection. It never means full consumer review,
behavioral equivalence, production zero use, P11 exit or P12 retirement permission.
All such approval/parity fields remain false. Replacement anchors are code-location
candidates, not newly installed runtime wiring or action authority.

## Tests and original failures

The new suite adds 30 diagnostic cases to the unchanged 24 R0 cases. The first local
combined run had 1 failure and 26 errors: the new parser incorrectly expected a
strict-JSON helper to exist in R0. That developer integration bug was corrected
inside the new script, without changing R0. The next combined run passed all 54.
The original failed log is retained in the delivery package; it is not a product
failure. Final local results are recorded with the frozen delivered bytes.

The two-platform workflow now runs the original R0 and new R1A contracts, creates
the exact-commit map, then runs five UNCHANGED product regression modules covering
dynamic execution manifests, P8 loading, composition Grant authority, step execution
and explicit-intent behavior. It uses the existing locked dependency installer.
Remote results must be read from new logs and recorded against the new commit.
No pending run or platform skip is converted to acceptance.

The local executor cannot resolve github.com for cloning. The connector can read
and publish source. No July ZIP was substituted as the current product baseline;
local synthetic diagnostics do not constitute local full-product validation.

## Reproduction and next batch

On the exact candidate with clean tracked source:

```text
python -m unittest discover -s tests -p "test_p12_retirement*.py" -v
python scripts/audit-p12-retirement-map.py --expected-head <exact-candidate-SHA> --baseline-head 5d3ba57b135d8376f11b6b280e9f1f5939ea3803
```

Exit 0: static mapping/developer scope passed. Exit 1: invalid/incomplete input.
Exit 2: forbidden delta. All three retain retirement_authorized=false.

R1A closes only for the scope actually verified by the new two-platform diagnostics
and retained product regressions. Full caller-body review and behavioral migration
remain open. R1B should first isolate the shared manifest dependency from the legacy
selection concern, with exact Source/Registry/schema/risk/error-identity preservation,
then review both context consumers and fact-derived router actions. Do not invent
another Runtime/Gateway, weaken acceptance checks or introduce implicit fallback.
P11 live matrix, controlled faults, single-path traces, independent review and merge
remain required before actual static-path retirement. P8/P9 debts remain separately
open. P13 removal and P14 default switching are not included in this batch.

Rollback is a revert of this developer-only delta. No database migration or deployed
switch is involved. Engineering-stage merge progress remains 11/18 = 61.1%; this
is not a product-readiness or effort estimate.
