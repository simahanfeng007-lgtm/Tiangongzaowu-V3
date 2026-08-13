# Blueprint Schema

## Story

Required fields:

```json
{
  "soul": "story purpose",
  "themes": ["theme"],
  "core_conflict": "central conflict",
  "ending": "intended ending",
  "protected_anchors": ["stable-anchor-id"]
}
```

## Character

```json
{
  "id": "char.hero",
  "name": "name",
  "birth_tick": -5400,
  "initial": {
    "alive": true,
    "location": "loc.home",
    "realm": "mortal",
    "realm_since_tick": -5400,
    "injuries": [],
    "inventory": [],
    "knowledge": []
  }
}
```

Derive age from `birth_tick`, event tick, and `ticks_per_year`. Never store a manually advanced age as authority.

## Calendar and space

```json
{
  "calendar": {"epoch": "era", "tick_unit": "day", "ticks_per_year": 360, "start_tick": 0},
  "locations": [{"id": "loc.home", "name": "home"}],
  "routes": [{"from": "loc.home", "to": "loc.city", "mode": "walk", "min_duration_ticks": 2, "bidirectional": true}],
  "schedules": [{"id": "sect.exam", "phase_tick": 20, "period_ticks": 1080}]
}
```

## Event

```json
{
  "id": "evt.001",
  "chapter": 1,
  "phase": "setup|develop|turn|close",
  "kind": "scene|travel|progression|death|resurrection|memory",
  "start_tick": 20,
  "duration_ticks": 1,
  "participants": ["char.hero"],
  "location": "loc.city",
  "requires_events": [],
  "deadline_chapter": 3,
  "closure_required": false,
  "evidence_terms": ["concrete term"]
}
```

Travel events also require `from`, `to`, and `mode`. Scheduled events require `schedule_id`. Age-sensitive events may supply `expected_ages`. Knowledge-sensitive events may supply `knowledge_requires`.

## Chapter plan

```json
{
  "number": 1,
  "title": "working title",
  "event_ids": ["evt.001"],
  "participants": ["char.hero"],
  "locations": ["loc.city"],
  "start_tick": 20,
  "duration_ticks": 1,
  "required_outcomes": ["outcome-tag"],
  "theme_tags": ["theme"],
  "protected_anchor_ids": []
}
```

## Emotional account

```json
{
  "id": "emotion.family.hero-father",
  "category": "family",
  "subject_ids": ["char.hero", "char.father"],
  "initial_balance": 0
}
```

