from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "app"
    / "backend"
    / "tiangong-backend"
    / "_internal"
    / "backend_life_context_authority.py"
)


def _load_authority_module():
    spec = importlib.util.spec_from_file_location("backend_life_context_authority", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._clean_text = lambda value: str(value or "").strip()
    module._normalize_attachments = lambda payload: list(payload.get("attachments") or [])
    module._interaction_mode = lambda _text, attachments: "work" if attachments else "chat"
    module._related_skills = lambda _text, _attachments, mode: [{"id": mode}]
    module._load_backend_history = lambda _store, session, request: [
        {"role": "system", "content": f"{session}:{request}"}
    ]
    module._attachment_manifest = lambda attachments: [{"count": len(attachments)}]
    return module


def _payload() -> dict[str, object]:
    envelope = {
        "life_id": "org_test",
        "cycle_id": "cycle_test",
        "context_hash": "a" * 64,
        "trigger": {"kind": "timer", "ref": "life_action_test"},
    }
    life_context = {
        "life_id": "org_test",
        "writer_epoch": 7,
        "cycle_id": "cycle_test",
        "context_hash": "a" * 64,
        "context_envelope": envelope,
        "lifecycle_state": "authorized",
    }
    return {
        "request_id": "life_action_test",
        "session_id": "life:org_test:autonomy",
        "text": "执行计划",
        "attachments": [],
        "life_context": life_context,
        "life_context_envelope": envelope,
        "life_context_hash": "a" * 64,
        "cycle_id": "cycle_test",
        "conversation_context": {
            "life_context": life_context,
            "life_context_hash": "a" * 64,
            "cycle_id": "cycle_test",
        },
    }


class BackendLifeContextAuthorityTests(unittest.TestCase):
    def test_7174_consumes_authorized_context_without_compiling(self) -> None:
        module = _load_authority_module()
        result = module._compile_life_context(object(), _payload())
        self.assertEqual(result["context_hash"], "a" * 64)
        self.assertEqual(result["cycle_id"], "cycle_test")
        self.assertEqual(result["lifecycle_state"], "authorized")
        self.assertNotIn("_life_post", module._compile_life_context.__code__.co_names)

    def test_7174_fails_closed_without_authoritative_context(self) -> None:
        module = _load_authority_module()
        payload = _payload()
        payload.pop("life_context")
        payload["conversation_context"] = {}
        with self.assertRaisesRegex(RuntimeError, "authoritative life_context is required"):
            module._compile_life_context(object(), payload)

    def test_7174_rejects_request_binding_mismatch(self) -> None:
        module = _load_authority_module()
        payload = _payload()
        payload["request_id"] = "life_action_other"
        with self.assertRaisesRegex(RuntimeError, "request binding mismatch"):
            module._compile_life_context(object(), payload)

    def test_7174_rejects_redundant_hash_mismatch(self) -> None:
        module = _load_authority_module()
        payload = _payload()
        payload["life_context_hash"] = "b" * 64
        with self.assertRaisesRegex(RuntimeError, "hash copies disagree"):
            module._compile_life_context(object(), payload)


if __name__ == "__main__":
    unittest.main()
