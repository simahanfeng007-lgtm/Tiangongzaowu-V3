# Chapter Transaction

## Checkout

Use the lease, pre-state hash, rolling blueprint hash, chapter plan, relevant entities, due open events, selected set pieces, and recent summaries returned by `novel.chapter.checkout`.

## Submit shape

```json
{
  "lease_id": "lease_...",
  "chapter_number": 1,
  "title": "chapter title",
  "content": "final chapter prose",
  "actual": {
    "summary": "factual result",
    "events": [],
    "state_changes": [],
    "relationship_changes": [],
    "foreshadow_ops": [],
    "emotional_transactions": [],
    "theme_tags": [],
    "convergence_proof": {}
  }
}
```

## Actual event

```json
{
  "id": "evt.001",
  "status": "progressed|turned|closed",
  "start_tick": 20,
  "duration_ticks": 1,
  "participants": ["char.hero"],
  "location": "loc.city",
  "outcome_tags": ["result-tag"],
  "evidence_terms": ["term appearing in prose"],
  "result": "permanent factual outcome"
}
```

For an unplanned event, set `unplanned=true` and include causal prerequisites plus a closure deadline.

## State change

```json
{"character_id": "char.hero", "field": "location", "from": "loc.home", "to": "loc.city", "op": "set"}
```

Allowed fields are `alive`, `location`, `realm`, `injuries`, `inventory`, and `knowledge`. Use `op=add` or `op=remove` with `items` for list changes.

## Deviation

The tool computes a 0-100 weighted distance across plot events, characters, locations, outcomes, themes, and time. Low deviation is accepted, medium deviation rebases the rolling plan, and high deviation requires a convergence proof preserving every protected anchor.

## Closure

Submit `status=closed`, a factual result, and permanent state consequences for every event due in the chapter. Do not advance while a required closure remains open.

