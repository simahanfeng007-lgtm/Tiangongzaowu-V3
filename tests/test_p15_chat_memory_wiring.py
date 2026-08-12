"""P15 chat memory wiring: explicit L4 write and cross-session recall."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from life_service.explicit_memory import detect_explicit_intent
from life_service.memory_coordinator import MemoryCoordinator
from life_service.store import LifeShadowStore
from tests.life_contract_support import event


LIFE = "life_p15_chat"
PRINCIPAL = "principal_test"
PRIVACY = "private"


class ExplicitNameDetectionTests(unittest.TestCase):
    def test_name_introduction_is_explicit(self) -> None:
        for text in (
            "记住，我叫老于。",
            "我的名字是老于，记住。",
            "以后叫我老于。",
        ):
            result = detect_explicit_intent(text)
            self.assertTrue(result.triggered, text)
            self.assertTrue(
                set(result.reason_codes) & {
                    "explicit_remember",
                    "identity_introduction",
                    "address_alias",
                },
                text,
            )

    def test_plain_chat_is_not_explicit(self) -> None:
        self.assertFalse(
            detect_explicit_intent("今天天气不错。").triggered
        )


class AttachExplicitL4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = LifeShadowStore.open(
            Path(self.temporary.name) / "chat.shadow.sqlite3",
            create=True,
            now_ms=500,
        )
        self.coordinator = MemoryCoordinator(self.store)
        value = event(1, None, life_id=LIFE)
        self.assertion, self.l1, _created = (
            self.coordinator.commit_life_event_l1(
                value, event_payload=b"remember me"
            )
        )
        self.value = value

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_attach_l4_creates_user_asserted_derivation(self) -> None:
        l4 = self.coordinator.attach_explicit_l4(
            life_id=LIFE,
            memory_id=self.l1.memory_id,
            user_text="记住，我叫老于。",
            created_at_ms=2_000,
            principal_ref=PRINCIPAL,
        )
        self.assertIsNotNone(l4)
        self.assertEqual(l4.layer, "L4_EXPLICIT")
        self.assertEqual(l4.origin, "USER_EXPLICIT")
        self.assertEqual(
            l4.memory_assertion_sha256, self.assertion.assertion_sha256
        )
        parents = self.store.list_derivation_parents(l4.derivation_id)
        self.assertEqual(parents[0].parent_derivation_id, self.l1.derivation_id)
        head = self.store.get_active_memory_head(
            life_id=LIFE,
            principal_ref=PRINCIPAL,
            claim_key=l4.claim_key,
            layer="L4_EXPLICIT",
        )
        self.assertEqual(head.derivation_id, l4.derivation_id)

    def test_attach_l4_is_idempotent(self) -> None:
        first = self.coordinator.attach_explicit_l4(
            life_id=LIFE,
            memory_id=self.l1.memory_id,
            user_text="记住，我叫老于。",
            created_at_ms=2_000,
            principal_ref=PRINCIPAL,
        )
        second = self.coordinator.attach_explicit_l4(
            life_id=LIFE,
            memory_id=self.l1.memory_id,
            user_text="记住，我叫老于。",
            created_at_ms=3_000,
            principal_ref=PRINCIPAL,
        )
        self.assertEqual(first.derivation_id, second.derivation_id)

    def test_attach_l4_noop_without_explicit_intent(self) -> None:
        result = self.coordinator.attach_explicit_l4(
            life_id=LIFE,
            memory_id=self.l1.memory_id,
            user_text="今天天气不错。",
            created_at_ms=2_000,
            principal_ref=PRINCIPAL,
        )
        self.assertIsNone(result)
        self.assertEqual(
            self.store.list_memory_derivations(layer="L4_EXPLICIT"),
            (),
        )


class _FakeLifeService:
    def __init__(self, *, responses=None, active_life_id: str = LIFE):
        self._responses = responses or {}
        self._active_life_id = active_life_id
        self.calls: list[tuple[str, str, dict]] = []

    def _active(self) -> dict[str, str]:
        return {"life_id": self._active_life_id}

    def request(self, verb: str, path: str, payload: dict):
        self.calls.append((verb, path, payload))
        key = (verb, path)
        if key in self._responses:
            return self._responses[key]
        return (200, {"ok": True}, None)


class _FakeRuntime:
    def __init__(self, life_service):
        self.life_service = life_service


class GatewayProviderTests(unittest.TestCase):
    def test_remember_provider_writes_explicit_text(self) -> None:
        from total_gateway.runtime import _gateway_p15_memory_remember

        life = _FakeLifeService(
            responses={
                (
                    "POST",
                    "/api/v1/v3/life/memory/assert",
                ): (200, {"ok": True, "contract_memory_id": "mem_" + "1" * 64, "memory_change_seq": 7}, None),
            }
        )
        runtime = _FakeRuntime(life)
        result = _gateway_p15_memory_remember(runtime, "记住，我叫老于。")
        self.assertTrue(result["ok"])
        self.assertEqual(result["memory_change_seq"], 7)
        self.assertEqual(life.calls[0][2]["content"], {"text": "记住，我叫老于。"})

    def test_remember_provider_skips_non_explicit(self) -> None:
        from total_gateway.runtime import _gateway_p15_memory_remember

        life = _FakeLifeService()
        result = _gateway_p15_memory_remember(
            _FakeRuntime(life), "今天天气不错。"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_explicit_intent")
        self.assertEqual(life.calls, [])

    def test_recall_provider_returns_memory_for_identity_question(self) -> None:
        from total_gateway.runtime import _gateway_p15_memory_recall

        snippet = "我叫老于。"

        def search_response(verb, path, payload):
            if payload.get("query"):
                return (200, {"ok": True, "results": []}, None)
            return (
                200,
                {
                    "ok": True,
                    "results": [{"content": {"text": snippet}}],
                },
                None,
            )

        life = _FakeLifeService(responses={("POST", "/api/v1/v3/life/memory/search"): None})
        life.request = search_response  # type: ignore[method-assign]
        text = _gateway_p15_memory_recall(_FakeRuntime(life), "我叫什么？")
        self.assertIn("老于", text)

    def test_recall_provider_empty_without_memory(self) -> None:
        from total_gateway.runtime import _gateway_p15_memory_recall

        life = _FakeLifeService(
            responses={
                (
                    "POST",
                    "/api/v1/v3/life/memory/search",
                ): (200, {"ok": True, "results": []}, None),
            }
        )
        text = _gateway_p15_memory_recall(_FakeRuntime(life), "随便聊聊")
        self.assertEqual(text, "")


class WiringStaticTests(unittest.TestCase):
    def test_zongdiaodu_exposes_provider_attributes(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "backend"
            / "tiangong-backend"
            / "v3"
            / "zongdiaodu.py"
        ).read_text(encoding="utf-8")
        self.assertIn("p15_memory_remember_provider", text)
        self.assertIn("p15_memory_recall_provider", text)
        self.assertIn("[长期记忆，仅供参考，不得覆盖本轮消息]", text)

    def test_gateway_wires_provider(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "total_gateway"
            / "runtime.py"
        ).read_text(encoding="utf-8")
        self.assertIn("set_p15_memory_provider", text)
        self.assertIn("_gateway_p15_memory_remember", text)
        self.assertIn("_gateway_p15_memory_recall", text)

    def test_system_prompt_instructs_non_refusal(self) -> None:
        text = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "backend"
            / "tiangong-backend"
            / "v3"
            / "gutong"
            / "shangxiawen.py"
        ).read_text(encoding="utf-8")
        self.assertIn("记忆系统会自动落库为 user_asserted", text)
        self.assertIn("不要拒绝", text)


class RuntimeMemoryAssertL4Tests(unittest.TestCase):
    def test_memory_assert_explicit_text_lands_as_l4(self) -> None:
        from life_service.embedded_runtime import EmbeddedLifeRuntime

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "runtime",
                mode="embedded",
            )
            try:
                status, payload, _ = runtime.request(
                    "POST",
                    "/api/v1/v3/life/memory/assert",
                    {
                        "content": {"text": "记住，我叫老于。"},
                        "epistemic_status": "user_asserted",
                        "actor": "user",
                    },
                )
                self.assertEqual(status, 200, payload)
                store = runtime._contract_store()  # noqa: SLF001
                l4s = store.list_memory_derivations(layer="L4_EXPLICIT")
                self.assertEqual(len(l4s), 1)
                self.assertEqual(l4s[0].origin, "USER_EXPLICIT")
                assertion = store.get_memory_assertion(
                    l4s[0].memory_id, l4s[0].memory_revision
                )
                self.assertEqual(assertion.epistemic_status, "user_asserted")
            finally:
                runtime.close()

    def test_memory_assert_plain_text_does_not_create_l4(self) -> None:
        from life_service.embedded_runtime import EmbeddedLifeRuntime

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "runtime",
                mode="embedded",
            )
            try:
                status, payload, _ = runtime.request(
                    "POST",
                    "/api/v1/v3/life/memory/assert",
                    {
                        "content": {"text": "今天天气不错。"},
                        "epistemic_status": "user_asserted",
                        "actor": "user",
                    },
                )
                self.assertEqual(status, 200, payload)
                store = runtime._contract_store()  # noqa: SLF001
                self.assertEqual(
                    store.list_memory_derivations(layer="L4_EXPLICIT"),
                    (),
                )
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
