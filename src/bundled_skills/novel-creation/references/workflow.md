# Novel System Workflow

## New project

1. Check the intended workspace folder. If it already contains `.novel-system/manifest.json`, switch to **Resume**; otherwise create the managed project. Set `planned_chapters` for the complete book, never the current prose checkpoint; require `planned_chapters >= ceil(target_words / 5000)`.
2. Stage story, characters, world, calendar, locations, routes, schedules, progression rules, plot events, chapters, relationships, foreshadows, emotional accounts, and settings. Generate and submit contiguous batches of at most 30 plot events and at most 15 chapter plans. Wait for each accepted batch before generating the next missing range; never build the full long-book list in one model response.
3. After all declared plot-event and chapter ranges are present, call `novel.blueprint.assist` and repair its first dependency batch with local patches. Use `novel.blueprint.upsert_many` whenever a list section is uploaded in multiple batches; whole-list `replace_all` is forbidden.
4. Resolve names with `novel.reference.resolve`; calculate ages and travel with `novel.timeline.calculate`. Apply dense overlap or travel-gap repairs through the returned `novel.timeline.normalize` transaction; keep `novel.timeline.shift_suffix` for an explicitly reviewed single pivot.
5. Compile the blueprint.
6. After a failed compile, call assist once for the baseline. After each local patch batch, call assist again with the prior `energy` as `previous_energy`. Continue only while energy strictly decreases.
7. Repair every blocking issue before prose generation.

The story blueprint must exist in structured data. A Markdown outline may be exported for people, but it is not authoritative.

## Chapter loop

1. Read project status.
2. Resolve mandatory emotional scene design if due.
3. Check out exactly the next chapter.
4. Query only missing relevant context.
5. Draft the complete chapter in model working memory.
6. Construct the actual event and state delta from the prose.
7. Submit the chapter through the lease.
8. Repair blocking issues without advancing.
9. After acceptance, use the returned next action.

Do not ask the user to say “continue” during an authorized full-book run. Persist at every accepted chapter and stop only on configured terminal conditions, repeated deterministic failure, authority requirements, or completion.

## Resume

1. Call `novel.project.status`.
2. If `workspace.planning_complete=false`, fill only the returned missing planning sections, assist, and compile before prose. If it is true, trust canonical `next_chapter` and do not regenerate the plan.
3. Ignore unaccepted loose prose.
4. If a lease is stale, check out the reported next chapter again.
5. If prepared transactions are reported, call `novel.project.recover`.
6. Continue from canonical `next_chapter`, state hash, open event debt, and pending emotional triggers.

At every stopping checkpoint, deliver the portable project folder plus the latest accepted file under `正文/`.

## Completion

Call `novel.project.audit`. A book is complete only when:

- every planned chapter required by the project has an accepted transaction;
- no overdue event remains open;
- required foreshadows are resolved;
- protected anchors remain satisfied;
- the canonical state hash and chapter ledger agree;
- the final event consequences are recorded.
