from __future__ import annotations
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
