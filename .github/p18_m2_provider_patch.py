from pathlib import Path

STORE = Path('src/total_gateway/store.py')
PROVIDER = Path('src/total_gateway/regenerative_provider.py')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected 1 anchor, found {count}')
    return text.replace(old, new, 1)


store = STORE.read_text(encoding='utf-8')
anchor = '''    def get_execution_task_contract(\n        self, request_id: str, *, run_id: str, generation: int\n    ) -> dict | None:\n'''
method = '''    def get_request_generation_binding(self, request_id: str) -> dict | None:\n        \"\"\"Return the authoritative generation row without exposing a mutable handle.\"\"\"\n        if not request_id:\n            raise ValueError(\"request_id is required\")\n        with self._lock:\n            if self._closed:\n                raise StoreError(\"gateway store is closed\")\n            row = self._connection.execute(\n                \"SELECT * FROM request_generation WHERE request_id = ?\",\n                (request_id,),\n            ).fetchone()\n            return None if row is None else dict(row)\n\n'''
store = replace_once(store, anchor, method + anchor, 'generation binding method')
STORE.write_text(store, encoding='utf-8', newline='\n')

provider = PROVIDER.read_text(encoding='utf-8')
provider = replace_once(
    provider,
    '        event_key = f"step.prepared:{logical_effect_id}:{attempt}"\n',
    '        event_key = f"step.prepared:{step_id}:{attempt}"\n',
    'prepared event key',
)
provider = replace_once(
    provider,
    '            event_key=f"step.dispatched:{logical_effect_id}:{attempt_id}",\n',
    '            event_key=f"step.dispatched:{step_id}:{attempt_id}",\n',
    'dispatch event key',
)
provider = replace_once(
    provider,
    '            event_key=f"{event_type}:{logical_effect_id}:{attempt_id}",\n',
    '            event_key=f"{event_type}:{step_id}:{attempt_id}",\n',
    'finish event key',
)
PROVIDER.write_text(provider, encoding='utf-8', newline='\n')
