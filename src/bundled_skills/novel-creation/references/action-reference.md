# Novel Action Reference

| Action | Target | Required arguments |
|---|---|---|
| `novel.project.create` | New project directory | `title`, `genre`, `planned_chapters`, `target_words` |
| `novel.project.status` | Managed project directory | none |
| `novel.project.recover` | Managed project directory | none |
| `novel.blueprint.update` | Managed project directory | `section`, `data` |
| `novel.blueprint.patch` | Managed project directory | `section`, `selector`, `changes` |
| `novel.blueprint.upsert_many` | Managed project directory | `section`, `items` (1-30 objects; chapters 1-15 only per call); optional `expected_revision`; required for every later list batch because list `replace_all` is forbidden |
| `novel.blueprint.assist` | Managed project directory | none; optional `previous_energy`, `batch_size` |
| `novel.reference.resolve` | Managed project directory | `entity_type`, `queries` |
| `novel.timeline.calculate` | Managed project directory | `operation` plus operation fields |
| `novel.timeline.shift_suffix` | Managed project directory | `event_id`, positive `delta_ticks`, `reason`; atomically shifts the chronological suffix and chapter intervals |
| `novel.timeline.normalize` | Managed project directory | `reason`; optional `max_shifts` 1-256; atomically applies strictly improving overlap/travel-gap suffix shifts |
| `novel.mobility.align_initial_many` | Managed project directory | `items` with `character_id` and first-scene `location`; atomically applies a strictly improving batch |
| `novel.blueprint.compile` | Managed project directory | none |
| `novel.plan.rebase` | Managed project directory | `expected_state_hash`, `reason`, `event_updates`, `chapter_updates`, `maintained_anchor_ids` |
| `novel.chapter.checkout` | Managed project directory | `chapter_number` |
| `novel.chapter.submit` | Managed project directory | `lease_id`, `chapter_number`, `title`, `content`, `actual` |
| `novel.scene.design` | Managed project directory | `trigger_id`, `candidates` |
| `novel.context.query` | Managed project directory | `entity_type`, `entity_ids` |
| `novel.project.audit` | Managed project directory | none |

Every target must be non-empty and inside the backend-owned workspace. Invalid calls are rejected before locks, backups, prose writes, or canonical state changes. Use `system.action_schema` when uncertain; never repeat an identical rejected payload.
