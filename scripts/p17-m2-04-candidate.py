from pathlib import Path
p=Path('src/life_service/embedded_runtime.py')
t=p.read_text(encoding='utf-8')
def r(old,new,label):
    global t
    c=t.count(old)
    if c!=1: raise SystemExit(f'{label}: expected 1 anchor, found {c}')
    t=t.replace(old,new,1)
a='from .complete_scheduler import EmbeddedLifeScheduler\n'
r(a,a+'from .embedded_runtime_lifecycle import (\n    cleanup_partial_initialization,\n    recover_inflight_scheduler_flags,\n    start_embedded_scheduler,\n)\n','import')
old='''            recovered_inflight = False
            identity_states = self._state.get("identity_states")
            if isinstance(identity_states, Mapping):
                for identity_scope in identity_states.values():
                    if not isinstance(identity_scope, dict):
                        continue
                    scheduler_state = identity_scope.get("scheduler")
                    if not isinstance(scheduler_state, dict):
                        continue
                    for key in (
                        "autonomy_decision_inflight",
                        "learning_decision_inflight",
                        "self_iteration_decision_inflight",
                        "greeting_inflight",
                        "proactive_decision_inflight",
                    ):
                        if scheduler_state.get(key) is True:
                            scheduler_state[key] = False
                            recovered_inflight = True
'''
r(old,'            recovered_inflight = recover_inflight_scheduler_flags(self._state)\n','recovery')
old='''            heartbeat_seconds = float(os.environ.get("TIANGONG_LIFE_HEARTBEAT_SECONDS") or 30.0)
            self.scheduler = EmbeddedLifeScheduler(
                self._scheduler_tick,
                interval_seconds=heartbeat_seconds,
            )
            self.scheduler.start()
'''
new='''            heartbeat_seconds = float(os.environ.get("TIANGONG_LIFE_HEARTBEAT_SECONDS") or 30.0)
            self.scheduler = start_embedded_scheduler(
                self._scheduler_tick,
                interval_seconds=heartbeat_seconds,
            )
'''
r(old,new,'scheduler')
old='''        except Exception as init_error:
            cleanup_errors: list[Exception] = []
            scheduler = self.scheduler
            if scheduler is not None:
                try:
                    scheduler.stop()
                except Exception as exc:
                    cleanup_errors.append(exc)
            store = self.authority_store
            if store is not None:
                try:
                    store.close()
                except Exception as exc:
                    cleanup_errors.append(exc)
            lease = self._lease
            if lease is not None:
                try:
                    lease.release()
                except Exception as exc:
                    cleanup_errors.append(exc)
            if cleanup_errors:
                init_error.add_note(
                    "life kernel partial-initialization cleanup failed: "
                    + ",".join(type(exc).__name__ for exc in cleanup_errors)
                )
            raise
'''
new='''        except Exception as init_error:
            cleanup_partial_initialization(
                scheduler=self.scheduler,
                authority_store=self.authority_store,
                lease=self._lease,
                init_error=init_error,
            )
            raise
'''
r(old,new,'cleanup')
if 'self.scheduler = EmbeddedLifeScheduler(' in t: raise SystemExit('direct scheduler remains')
if 'cleanup_errors: list[Exception] = []' in t: raise SystemExit('inline cleanup remains')
compile(t,str(p),'exec')
p.write_text(t,encoding='utf-8',newline='\n')

test=Path('tests/test_embedded_life_p17_m2_04.py')
test.write_text('''from __future__ import annotations
import ast, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LIFE=ROOT/"src"/"life_service"; RUNTIME=LIFE/"embedded_runtime.py"; BOUNDARY=LIFE/"embedded_runtime_lifecycle.py"
class EmbeddedLifeM204Tests(unittest.TestCase):
    def test_boundary(self):
        x=BOUNDARY.read_text(encoding="utf-8"); n={z.name for z in ast.parse(x).body if isinstance(z,(ast.ClassDef,ast.FunctionDef))}
        self.assertIn("recover_inflight_scheduler_flags",n); self.assertIn("start_embedded_scheduler",n); self.assertIn("cleanup_partial_initialization",n)
        self.assertNotIn("EmbeddedLifeRuntime",n); self.assertNotIn("EmbeddedLifeScheduler",n); self.assertNotIn("LifeShadowStore.open",x); self.assertNotIn("CompleteLifeSystem(",x)
    def test_scheduler_reuse(self):
        x=BOUNDARY.read_text(encoding="utf-8"); self.assertIn("from .complete_scheduler import EmbeddedLifeScheduler",x); self.assertIn("scheduler = EmbeddedLifeScheduler(",x); self.assertIn("scheduler.start()",x)
    def test_recovery_keys(self):
        x=BOUNDARY.read_text(encoding="utf-8")
        for k in ("autonomy_decision_inflight","learning_decision_inflight","self_iteration_decision_inflight","greeting_inflight","proactive_decision_inflight"): self.assertIn(k,x)
        self.assertIn("scheduler_state[key] = False",x)
    def test_cleanup_order(self):
        x=BOUNDARY.read_text(encoding="utf-8"); self.assertLess(x.index("scheduler.stop()"),x.index("authority_store.close()")); self.assertLess(x.index("authority_store.close()"),x.index("lease.release()")); self.assertIn("init_error.add_note",x)
    def test_runtime_delegation(self):
        x=RUNTIME.read_text(encoding="utf-8"); self.assertIn("recover_inflight_scheduler_flags(self._state)",x); self.assertIn("start_embedded_scheduler(",x); self.assertIn("cleanup_partial_initialization(",x); self.assertNotIn("self.scheduler = EmbeddedLifeScheduler(",x); self.assertNotIn("cleanup_errors: list[Exception] = []",x)
    def test_single_runtime_authority(self):
        b=BOUNDARY.read_text(encoding="utf-8"); r=RUNTIME.read_text(encoding="utf-8"); self.assertIn("class EmbeddedLifeRuntime",r); self.assertIn("LifeWriterLease.acquire",r); self.assertIn("CompleteLifeSystem(data_root",r); self.assertIn("LifeShadowStore.open",r); self.assertNotIn("LifeWriterLease.acquire",b); self.assertNotIn("LifeShadowStore.open",b)
if __name__=="__main__": unittest.main()
''',encoding='utf-8',newline='\n')

g=Path('.github/workflows/architecture-gate.yml')
s=g.read_text(encoding='utf-8')
old='''      - name: Compile P17 M2 V3 seams
        run: python -m py_compile app/backend/tiangong-backend/v3/zongdiaodu.py app/backend/tiangong-backend/v3/runtime_bootstrap.py app/backend/tiangong-backend/v3/runtime_composition.py app/backend/tiangong-backend/v3/runtime_lifecycle.py app/backend/tiangong-backend/v3/runtime_turn_orchestration.py app/backend/tiangong-backend/v3/runtime_tool_result_boundary.py
'''
new='''      - name: Run P17 M2-04 embedded life lifecycle regression
        run: python tests/test_embedded_life_p17_m2_04.py -v

      - name: Compile P17 M2 seams
        run: python -m py_compile app/backend/tiangong-backend/v3/zongdiaodu.py app/backend/tiangong-backend/v3/runtime_bootstrap.py app/backend/tiangong-backend/v3/runtime_composition.py app/backend/tiangong-backend/v3/runtime_lifecycle.py app/backend/tiangong-backend/v3/runtime_turn_orchestration.py app/backend/tiangong-backend/v3/runtime_tool_result_boundary.py src/life_service/embedded_runtime.py src/life_service/embedded_runtime_lifecycle.py
'''
if s.count(old)!=1: raise SystemExit('gate anchor')
g.write_text(s.replace(old,new,1),encoding='utf-8',newline='\n')
print('P17-M2-04 candidate patched')
