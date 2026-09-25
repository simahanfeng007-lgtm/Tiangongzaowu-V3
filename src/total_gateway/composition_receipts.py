"""Project leaf outcomes, including a verified reuse of an earlier receipt."""


def composition_outcomes(events):
    outcomes = {}
    terminals = {e.event_id: e for e in events
                 if e.event_type in {"step.committed", "step.failed", "step.ambiguous", "step.reconciled"}}
    by_effect = {}
    for event in terminals.values():
        by_effect[event.effect_id] = event
    for event in events:
        ref = event.payload.get("composition_ref") if event.event_type == "step.prepared" else None
        if not ref:
            continue
        terminal = (terminals.get(event.payload.get("prior_event_id"))
                    if event.payload.get("disposition") == "already_committed"
                    else by_effect.get(event.effect_id))
        if terminal is not None:
            status = terminal.event_type
            if status == "step.reconciled":
                verdict = str(terminal.payload.get("verdict", "")).upper()
                status = "step.committed" if verdict == "APPLIED" else "not_executed" if verdict == "PROVEN_NOT_APPLIED" else "step.ambiguous"
            outcomes[(ref["composition_id"], ref["leaf_id"])] = {
                "status": status, "evidence_hash": terminal.event_hash,
                "result": terminal.payload.get("result_summary", terminal.payload.get("evidence", {}))}
    return outcomes
