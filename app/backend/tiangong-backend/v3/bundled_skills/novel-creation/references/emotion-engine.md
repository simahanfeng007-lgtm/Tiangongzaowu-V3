# Emotion and Scene Notes

Emotional accounts and historical triggers are creative annotations. The engine no longer calculates emotional balances, creates mandatory payoff triggers, requires 2–3 scenes, or selects a scene using a numeric threshold.

`actual.emotional_transactions` stores caller notes with their provenance. To record a selected scene, call `novel.scene.design` with a non-empty `candidates` array, zero-based `selected_index`, and `expected_state_hash`. The selected candidate needs `title` and integer `target_chapter` for an unrecorded chapter in the plan. Optional `trigger_id` can associate a pending historical trigger. Omit it for a new scene.

A scene is recorded, not quality approved. Explain creative choices through the user's requirements and actual prose for the final judge. Historical numeric fields remain historical and cannot grant a current completion decision.
