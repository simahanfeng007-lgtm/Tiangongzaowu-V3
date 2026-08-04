---
name: novel-creation
description: Create, continue, audit, revise, or complete an explicitly managed long-form fiction project through Tiangong's authoritative novel system. Use only when the user asks for a full book, autonomous multi-chapter/long-running serial work, or the target is already a managed novel project. Do not use for a one-off chapter, a few chapters, an outline, character/ clue sheets, or a collaboration/review package; route those to the lightweight webnovel deliverable Skill. All managed prose must use the novel.* tool actions rather than generic file writes.
---

# Novel Creation

## Entry gate

Enter this workflow only when the request explicitly authorizes a managed full-book or long-running multi-chapter project, or when `novel.project.status` identifies the target as an existing managed project. Never infer `target_words` or `planned_chapters` from a single-chapter request. For a one-off chapter, a few chapters, an outline, character/ clue sheets, collaboration notes, or continuity review documents, use the lightweight deliverable Skill and `file.write`/`docx.create` plus QC instead of `novel.project.create`.

Treat the model as the prose author, never as the source of truth. Treat accepted chapter facts as canon, the immutable original blueprint as intent, the rolling blueprint as the current route, and deterministic tools as the gatekeeper.

## Project discovery and portable workspace

Before choosing create or continue, resolve the intended project folder inside the active workspace.

- If the folder contains `.novel-system/manifest.json`, call `novel.project.status` first. When `workspace.planning_complete=true`, continue exactly from canonical `next_chapter`; do not recreate the project or regenerate the whole plan.
- If a managed folder exists but `workspace.planning_complete=false`, repair only `workspace.missing_planning_sections`, then assist and compile. Do not start prose against a partial plan.
- If the intended folder does not exist, call `novel.project.create`, then build and compile the complete book blueprint before drafting the requested checkpoint chapter.
- Every managed action synchronizes a portable project view containing `project.json`, `pipeline_state.json`, `创作宪法.md`, `设定/`, `大纲/`, `追踪数据/`, and `正文/`. The `.novel-system/` directory remains authoritative.
- At the final checkpoint, report both `delivery.project_folder` and `delivery.latest_chapter`. The whole project folder and the accepted prose are the deliverables; do not report only an isolated chapter file.

## Mandatory workflow

1. Call `novel.project.create` for a new project. `planned_chapters` describes the full book, not the requested stopping chapter, and must be at least `ceil(target_words / 5000)`.
2. Stage every required blueprint section with `novel.blueprint.update` only while that section is empty. Bound batches by payload: at most 30 `plot_events`, but at most 15 `chapters`, in one response or tool call. Send every later contiguous range through `novel.blueprint.upsert_many`, waiting for the accepted result before generating the next range. Never use `replace_all` on a list section.
3. Call `novel.blueprint.assist` before compilation. Let the system calculate age, reference, travel, and dependency debt.
4. Use `novel.reference.resolve` and `novel.timeline.calculate` for deterministic questions. The model chooses the creative resolution.
5. Repair only the returned next dependency batch with its exact `repair_sequence`; use `novel.blueprint.upsert_many` for multi-item batches and never replace a correct whole section.
   If `novel.blueprint.assist` returns `repair_batch`, execute that exact batch first. It combines independent initial-location alignments or route declarations into one checked transaction.
   While chapter coverage is incomplete, convergence is `building`, not regression, and `novel.blueprint.assist` will reject the call. For mobility, first-scene mismatches repair `characters.initial.location`; later off-screen movement requires a route plus sufficient calculated time. Dense timing conflicts use `novel.timeline.normalize` to apply all currently deterministic minimal suffix shifts in one strictly improving transaction; never guess and patch individual downstream ticks.
   Participant overlaps use the same normalization transaction. Recalculate after it finishes because newly declared routes may reveal additional travel-time debt.
   Every patch returns `energy_before`, `energy_after`, and `convergence`; prefer improving patches and reconsider regressing ones.
   After full chapter coverage exists, do not insert unreferenced plot events. Update existing canonical ids only; the backend rejects any repair upsert that increases total error energy.
6. Call `novel.blueprint.compile`. Do not draft until it succeeds.
7. Call `novel.chapter.checkout` for exactly `next_chapter`.
8. Draft from the returned chapter card and relevant queried context.
9. Call `novel.chapter.submit` with final prose and a structured factual delta.
10. If rejected, change the failed prose or delta and resubmit the same lease unless the tool reports it stale.
11. If `novel.scene.design` is required, submit 2-3 causal candidates before the indicated chapter.
12. If accepted facts make future plans inconsistent, call `novel.plan.rebase` for future-only changes while preserving every protected anchor.
13. Advance only after `accepted=true`.
14. Call `novel.project.audit` before declaring a volume or book complete.

For a long book, finish every declared `plot_events` batch and every declared `chapters` batch before calling assist or compile. A checkpoint such as chapter 15 limits prose generation, not full-book blueprint coverage.

Never write, append, patch, rename, move, or delete files under a managed project's `正文/` with generic file tools. Never claim a draft is canonical before `novel.chapter.submit` succeeds.

## Authority order

Use this precedence when plans disagree:

1. Protected story anchors and hard world rules.
2. Accepted factual state and event outcomes.
3. Current rolling blueprint.
4. Original blueprint as the divergence baseline.
5. Unaccepted prose or model memory.

Do not overwrite the original blueprint. Let chapter submission rebase the rolling plan onto accepted facts.

## Required gates

- Reject impossible time, age, travel, progression, knowledge, life/death, or causal prerequisites.
- Use hour or finer ticks for multiple same-day scenes and never overlap one character's event intervals unless simultaneous participation is explicit and physically possible.
- For mobility debt, execute `novel.blueprint.assist` calculations in order: create the returned missing route, wait for success, then patch the returned existing event id into a valid travel transition. Do not add an unreferenced event.
- Close every event whose deadline or chapter contract requires closure.
- Preserve permanent consequences; do not silently restore the previous state.
- Use deviation scores to distinguish natural growth from loss of story direction.
- Require convergence proof for high deviation while preserving all protected anchors.
- Deposit or withdraw emotional energy only with evidence present in the prose.
- Design a payoff scene when an emotional account reaches its threshold; do not force death or arbitrary reversal.

## Context discipline

Use the checkout card as the minimum writing context. Call `novel.context.query` only for relevant characters, events, relationships, foreshadows, chapters, or emotional accounts. Do not load the whole novel when targeted facts suffice.

When automatic context compilation archives old blocks at the configured budget, continue from `novel.project.status`, the checkout card, and targeted `novel.context.query` results. Never reconstruct continuity from discarded chat text or reload the whole project into the prompt.

## References

- Read [workflow.md](references/workflow.md) before starting or resuming a managed project.
- Read [blueprint-schema.md](references/blueprint-schema.md) when creating or revising story plans.
- Read [chapter-transaction.md](references/chapter-transaction.md) before submitting chapters or diagnosing rejection.
- Read [emotion-engine.md](references/emotion-engine.md) when emotional thresholds or set-piece design are active.
- Read [quality-rules.md](references/quality-rules.md) while drafting or revising prose.
- Read [action-reference.md](references/action-reference.md) when an action or argument shape is uncertain.
