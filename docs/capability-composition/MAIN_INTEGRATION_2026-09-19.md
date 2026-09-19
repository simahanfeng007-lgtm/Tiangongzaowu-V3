# Main integration candidate — 2026-09-19

## Owner request and scope

The project owner explicitly requested that the latest engineering result be
integrated into the original repository's `main` branch. This authorizes the
normal PR integration of completed engineering work; it does not certify P11
live acceptance, P12 decommission, default-planner switching or deployment.

Use original PR #78 and its existing branch. Do not force-update main, remove
required checks, dismiss reviews or replace main with a ZIP or patch tree.
Merge only the exact reviewed PR head after its engineering checks succeed.
At this record's creation no merge has occurred.

## Two different R1C2 implementations were found

Common parent: `aa9fe3f7da7d3280cd4010cf17e6371aa575f2f5`.

Canonical repository candidate:
- Commit: `c4dd0cec433e0c7c18553922da38716b0d4e80fc`.
- Source tree: `b0f71fdb991c8b673c7cf73fd287f4e124560263`.
- 14 changed files relative to the common parent.

Separate, unpublished local delivery:
- Declared source tree: `6d4def156bd659616fe36981779b06c60d91eb79`.
- 11 changed files relative to the common parent.
- Its patch and source ZIP are not the repository candidate and must not be
  applied over this branch as though they were identical.

The common parent, remote changed-file list and production preparation code
were inspected alongside the local delivered implementation. This is a
source/API reconciliation, not proof of behavioral equivalence between all
possible executions of the two variants.

## Reconciliation decision: one existing production lineage

Retain the repository implementation as the canonical R1C2 code. Do not add the
local alternate `world_understanding/capability_composition/source_resolution.py`
or its duplicate candidate/Plan preparation types alongside the existing
`total_gateway/composition_source_preparation.py`.

| Concern | Local delivery | Retained repository implementation |
| --- | --- | --- |
| Source resolution | System-supplied generic callable returns P2/P3 snapshots and registry | Installed Gateway/World reader, operator-pinned P8 native Git/bundle inputs, P9 signed exact-state archive |
| Request lineage | Turn identity and current WorldState checks | Also checks persisted inbound text and active Gateway request/run/generation |
| P4 implementation | Original candidate builder, parser, compiler and tri-state validator | Same original P4 components; includes the existing one-repair parser |
| Model interaction | A convenience `plan_for_turn` combines the call | Explicit `prepare_composition_for_turn` and `compile_composition_for_turn`; existing HTTP client call stays outside Store locks |
| Execution permission | No registration or execution | No registration, Ticket/Grant, dispatch or source-approval waiver |
| Parent defects | Base-only output compatibility repaired | Same compatibility repaired, plus real Gateway fixture callback restoration |

The local convenience `plan_for_turn` is NOT silently claimed merged or API
compatible. The retained public planning seam is prepare / existing HTTP client /
compile. The local tests and their synthetic PROVED_VALID example are not
transferred or relabeled as evidence for this implementation. In particular,
the repository source-backed fixture returns UNKNOWN and its invalid-time
fixture returns PROVED_INVALID; neither result is upgraded.

Both approaches are staged engineering, not the default execution planner.
Keeping the installed reader avoids introducing a second source-preparation
implementation merely to include every file from an alternate local package.
The local package remains historical evidence, not a future update source.

## Engineering checks before integration

At the c4dd0ce checkpoint:
- P14 run 35378226995 and P19 run 35378226973 reached success.
- Architecture run 35378226976 passed Ubuntu full regression, both Node and
  Source Authority jobs, and seven Windows shards.
- Windows shard 7 failed the original
  `TotalGatewayShutdownTests.test_signal_drain_releases_single_instance_and_next_epoch_starts`
  at the first health wait, with `gateway did not become healthy`.
- That shard reported 674 passed, 1 failed and 6 skipped. The original log does
  not establish a root cause. The failed run and union are NOT acceptance.
- The original failed artifact is 10561941137, ZIP SHA-256
  `7d5f626f7dd11962eeda04e4bf4581afd40bd241daadc49130469fab01365885`.

This record changes documentation only. No product code, tests, CI selection,
timeout, freeze, dependency, permissions or sandbox behavior is weakened.
The new commit receives fresh normal PR checks, including all eight Windows
shards and their same-run-attempt union. Do not stitch successful shards from
different attempts or declare the earlier failure fixed without new evidence.
Final check outcomes and any merge SHA must be recorded in the PR discussion,
not predicted in this source file.

## Acceptance and operational boundaries remain

Engineering integration may put partial P12 work on main without closing P12.
The last fully closed stage remains P10. P11's accepted live-model observations,
controlled faults, single-path traces, metrics and independent review are still
required. P8/P9 obligations remain. PR #77 is separate and is not merged by #78.

The old HTTP learned-Skill injection, static catalog/startup/selector/query
consumers and the existing publication freeze remain in place. Source to P4
preparation is explicit; P7 admission and production default switching are not
performed. No desktop installer or production release is created by this merge.

After successful integration, use main as the next engineering baseline. Verify
its actual commit/tree and post-merge checks; do not keep applying the old local
R1C2 patch. Git LFS assets, including the VRM model, still require normal hydration.
