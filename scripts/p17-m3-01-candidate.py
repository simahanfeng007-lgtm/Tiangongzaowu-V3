from pathlib import Path

root = Path(__file__).resolve().parents[1]
store_path = root / "src/life_service/store.py"
gate_path = root / ".github/workflows/architecture-gate.yml"

store = store_path.read_text(encoding="utf-8")
import_anchor = "from .replay import LifeReplaySummary, advance_replay_sha256, replay_life_events\n"
if store.count(import_anchor) != 1:
    raise SystemExit("M3-01 store import anchor mismatch")
store = store.replace(import_anchor, import_anchor + "from .store_connection import open_life_shadow_sqlite\n", 1)

start = store.index("    @classmethod\n    def open(")
end = store.index("    @staticmethod\n    def _initialize", start)
replacement = '''    @classmethod
    def open(
        cls,
        path: Path,
        *,
        create: bool,
        now_ms: int,
    ) -> "LifeShadowStore":
        opened = open_life_shadow_sqlite(
            path,
            create=create,
            now_ms=now_ms,
            error_factory=LifeShadowStoreError,
            initialize=cls._initialize,
            migrate=cls._migrate,
        )
        try:
            store = cls(opened.path, opened.connection)
            store.health()
            return store
        except Exception:
            opened.connection.close()
            raise

'''
store = store[:start] + replacement + store[end:]
compile(store, str(store_path), "exec")
store_path.write_text(store, encoding="utf-8", newline="\n")

gate = gate_path.read_text(encoding="utf-8")
old = '''      - name: Compile P17 M2 seams
        run: python -m py_compile app/backend/tiangong-backend/v3/zongdiaodu.py app/backend/tiangong-backend/v3/runtime_bootstrap.py app/backend/tiangong-backend/v3/runtime_composition.py app/backend/tiangong-backend/v3/runtime_lifecycle.py app/backend/tiangong-backend/v3/runtime_turn_orchestration.py app/backend/tiangong-backend/v3/runtime_tool_result_boundary.py src/life_service/embedded_runtime.py src/life_service/embedded_runtime_lifecycle.py src/life_service/embedded_runtime_wiring.py src/total_gateway/runtime.py
'''
new = '''      - name: Run P17 M3-01 life store connection regression
        run: python tests/test_life_store_p17_m3_01.py -v

      - name: Compile P17 M2 and M3 seams
        run: python -m py_compile app/backend/tiangong-backend/v3/zongdiaodu.py app/backend/tiangong-backend/v3/runtime_bootstrap.py app/backend/tiangong-backend/v3/runtime_composition.py app/backend/tiangong-backend/v3/runtime_lifecycle.py app/backend/tiangong-backend/v3/runtime_turn_orchestration.py app/backend/tiangong-backend/v3/runtime_tool_result_boundary.py src/life_service/embedded_runtime.py src/life_service/embedded_runtime_lifecycle.py src/life_service/embedded_runtime_wiring.py src/life_service/store.py src/life_service/store_connection.py src/total_gateway/runtime.py
'''
if gate.count(old) != 1:
    raise SystemExit("M3-01 architecture gate anchor mismatch")
gate_path.write_text(gate.replace(old, new, 1), encoding="utf-8", newline="\n")
print("P17-M3-01 candidate patched")
