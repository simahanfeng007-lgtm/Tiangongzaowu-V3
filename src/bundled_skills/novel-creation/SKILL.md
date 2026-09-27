---
name: novel-creation
description: Create, continue, audit, revise, or complete an explicitly managed long-form fiction project through Tiangong's authoritative novel system. Use only when the user asks for a full book, autonomous multi-chapter/long-running serial work, or the target is already a managed novel project. Do not use for a one-off chapter, a few chapters, an outline, character/ clue sheets, or a collaboration/review package; route those to the lightweight webnovel deliverable Skill. All managed prose must use the novel.* tool actions rather than generic file writes.
---

# Managed Novel Creation

Use the existing novel engine for an explicitly managed book or serial project. For a one-off chapter or outline use the lightweight deliverable workflow. User instructions define the goal and scope.

## Authority

The engine records project data, byte hashes, versions and chapter transactions. Plans, event outcomes and emotional notes are caller-supplied story annotations, not independently verified facts about prose. Only the final adversarial judge decides content quality and task completion using the current user goal and freshly read chapter bytes. `committed=true`, `all_planned_chapters_recorded=true`, `ok=true` and legacy `energy` do not approve a deliverable.

## Workflow

1. Resolve the project in the authorized workspace. For an existing `.novel-system/manifest.json`, read `novel.project.status`; recover a prepared transaction before another submission. Retain the original failed attempt.
2. For a new managed project, use user-declared `planned_chapters` and `target_words`. There is no implicit words-per-chapter formula. Stage records with update/patch/upsert. Preserve existing records and respect returned versions.
3. Read `novel.blueprint.assist`. Missing identities, invalid references and record shapes require correction before compilation. Age, overlap, travel and other story observations are advisory: the model chooses whether and how they matter to this story. Do not blindly execute a suggested repair or optimize its numeric energy.
4. Compile the declared plan. Checkout the current `next_chapter` to obtain a v2 lease, state hash and relevant context. Query only the additional context needed.
5. Write prose matching the user's requirements. Submit prose and structured annotations through `novel.chapter.submit`. A transaction refusal identifies a concrete record, version or storage problem; repair that issue and obtain a fresh lease if stale. No default length, keyword match, deviation proof or emotional score blocks prose.
6. A successful transaction records bytes and advances the chapter sequence. Read the chapter back, inspect observations and ask the final adversarial judge to evaluate the actual requested deliverable. Suggestions delivered to the author do not prove any revision happened.
7. When creative scene planning helps, submit any non-empty candidate list, an explicit `selected_index` and the current `expected_state_hash` to `novel.scene.design`. The program records the caller's choice; it does not rank candidates or force a scene.
8. Use future-only `novel.plan.rebase` when changing the remaining plan. Original snapshots and recorded history retain their identities. After any revision, recovery or changed goal, acquire fresh observations and a new final decision.
9. Before delivery, audit file hashes/ledger continuity, read actual content, and obtain the sole final adversarial decision. Report the managed folder and requested chapter artifact. Do not infer business completion from a chapter count.

## Storage and recovery

Use novel transactions for managed `正文/` and `.novel-system/` records; generic file writes would bypass their version and recovery contract. Recovery accepts historical v1 prepared byte transactions after integrity checks, without reinterpreting old approval claims. A v1 checkout must be replaced by a new v2 lease. Conflicting actual bytes or state stop replay and require reconciliation.

The legacy `scripts/novel_tool.py` is a file observation/assembly helper. Its `gate`, `audit`, and `contract-check` commands never issue quality approval. It cannot replace managed transactions or the final judge. Historical passed/failed files are preserved and labeled historical.

## References

- [workflow.md](references/workflow.md): execution and recovery.
- [blueprint-schema.md](references/blueprint-schema.md): record shapes and caller annotations.
- [chapter-transaction.md](references/chapter-transaction.md): v2 transaction contract.
- [emotion-engine.md](references/emotion-engine.md): optional creative notes and explicit scene selection.
- [quality-rules.md](references/quality-rules.md): judge observations.
- [action-reference.md](references/action-reference.md): tool arguments.

To repair the latest recorded chapter, read its chapter record and actual bytes, then checkout that chapter with `revision_of` equal to its current SHA-256. Submit the fresh lease with revised prose and `actual={}`. This changes prose only; prior story annotations remain explicitly unverified. Earlier chapter or state-delta corrections are outside this version’s revision support and must not be silently rewritten. Old bytes/records remain in transaction history. Read the new content and obtain a fresh final judge decision.
