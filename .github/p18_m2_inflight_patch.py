from pathlib import Path

PROVIDER = Path('src/total_gateway/regenerative_provider.py')
ZONG = Path('app/backend/tiangong-backend/v3/zongdiaodu.py')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one anchor, found {count}')
    return text.replace(old, new, 1)


def replace_block(text: str, start: str, end: str, replacement: str, label: str) -> str:
    i = text.find(start)
    j = text.find(end, i + len(start))
    if i < 0 or j < 0:
        raise SystemExit(f'{label}: block anchors missing')
    return text[:i] + replacement + text[j:]


provider = PROVIDER.read_text(encoding='utf-8')
logical = '''    def _logical_effect_disposition(\n        self, identity: _Identity, logical_effect_id: str\n    ) -> tuple[str | None, Any | None]:\n        unresolved_started: dict[str, Any] = {}\n        unresolved_ambiguous: dict[str, Any] = {}\n        committed = None\n        for event in self._store.list_execution_events(\n            identity.request_id, run_id=identity.run_id, generation=identity.generation\n        ):\n            if event.logical_effect_id != logical_effect_id:\n                continue\n            effect_id = str(event.effect_id or \"\")\n            if event.event_type == \"step.dispatched\" and effect_id:\n                unresolved_started[effect_id] = event\n            elif event.event_type == \"step.committed\":\n                committed = event\n                if effect_id:\n                    unresolved_started.pop(effect_id, None)\n                    unresolved_ambiguous.pop(effect_id, None)\n            elif event.event_type == \"step.failed\" and effect_id:\n                unresolved_started.pop(effect_id, None)\n                unresolved_ambiguous.pop(effect_id, None)\n            elif event.event_type == \"step.ambiguous\" and effect_id:\n                unresolved_started.pop(effect_id, None)\n                unresolved_ambiguous[effect_id] = event\n            elif event.event_type == \"step.reconciled\" and effect_id:\n                verdict = str(event.payload.get(\"verdict\") or \"\").upper()\n                if verdict == \"APPLIED\":\n                    committed = event\n                    unresolved_started.pop(effect_id, None)\n                    unresolved_ambiguous.pop(effect_id, None)\n                elif verdict == \"PROVEN_NOT_APPLIED\":\n                    unresolved_started.pop(effect_id, None)\n                    unresolved_ambiguous.pop(effect_id, None)\n        if committed is not None:\n            return \"already_committed\", committed\n        if unresolved_ambiguous:\n            return \"reconcile_required\", list(unresolved_ambiguous.values())[-1]\n        if unresolved_started:\n            return \"in_flight\", list(unresolved_started.values())[-1]\n        return None, None\n\n'''
provider = replace_block(
    provider,
    '    def _logical_effect_disposition(\n',
    '    def _prepare_effect(self, payload: Mapping[str, Any]) -> dict[str, Any]:\n',
    logical,
    'logical effect disposition',
)
provider = provider.replace(
    '"effect_state": "LOGICAL_COMMITTED" if prior_disposition == "already_committed" else "AMBIGUOUS"',
    '"effect_state": ("LOGICAL_COMMITTED" if prior_disposition == "already_committed" else "SIDE_EFFECT_STARTED" if prior_disposition == "in_flight" else "AMBIGUOUS")',
)
if provider.count('SIDE_EFFECT_STARTED" if prior_disposition == "in_flight"') != 2:
    raise SystemExit('expected two prior effect-state projections')
PROVIDER.write_text(provider, encoding='utf-8', newline='\n')

zong = ZONG.read_text(encoding='utf-8')
anchor = '''    if disposition == "reconcile_required":\n        _simple_chain_regenerative_effect_state(run_state, effect_id, state="ambiguous")\n'''
replacement = '''    if disposition == "in_flight":\n        _simple_chain_regenerative_effect_state(run_state, effect_id, state="prepared")\n        if update_frontier:\n            _simple_chain_regenerative_update_frontier(\n                run_state, turn_loop, global_step=global_step,\n                latest_safe_step=f"logical effect {logical_effect_id} remains in flight",\n                next_action_hint="wait for the in-flight effect to resolve; do not dispatch a duplicate",\n            )\n        return {\n            "ok": False,\n            "status": "in_flight",\n            "ambiguous_effect": False,\n            "error": "[EFFECT_IN_FLIGHT] logical action already dispatched; duplicate retry blocked",\n            "effect_id": effect_id,\n            "logical_effect_id": logical_effect_id,\n        }\n    if disposition == "reconcile_required":\n        _simple_chain_regenerative_effect_state(run_state, effect_id, state="ambiguous")\n'''
zong = replace_once(zong, anchor, replacement, 'runtime in-flight dispatch block')
ZONG.write_text(zong, encoding='utf-8', newline='\n')
