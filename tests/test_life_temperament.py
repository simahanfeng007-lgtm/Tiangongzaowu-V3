from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from life_service.complete_core import CompleteLifeSystem, LifeCoreError
from life_service.embedded_runtime import EmbeddedLifeRuntime
from life_service.temperament import (
    DISPOSITION_KEYS,
    TRAIT_KEYS,
    adapt_from_completed_turn,
    generate_innate_temperament,
    initial_temperament_state,
    public_temperament_projection,
)


class LifeTemperamentTests(unittest.TestCase):
    def test_birth_generation_is_bounded_varied_and_explicitly_soul_independent(self) -> None:
        documents = [
            generate_innate_temperament("org_test", seed=index)
            for index in range(32)
        ]
        self.assertEqual(len({json.dumps(row["traits_milli"], sort_keys=True) for row in documents}), 32)
        for document in documents:
            self.assertEqual(document["soul_influence"], "forbidden")
            self.assertEqual(set(document["traits_milli"]), set(TRAIT_KEYS))
            self.assertEqual(
                set(document["affective_disposition_milli"]),
                set(DISPOSITION_KEYS),
            )
            self.assertTrue(
                all(120 <= value <= 880 for value in document["traits_milli"].values())
            )
            self.assertGreaterEqual(
                document["adaptation_policy"]["trait_learning_denominator"],
                1024,
            )

    def test_signed_birth_temperament_survives_soul_update_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            system = CompleteLifeSystem(Path(temporary))
            identity = system.create_identity("first")
            root = system.identities.root_for(identity["life_id"])
            before = system.identities.verify_temperament(root)

            system.update_soul(
                {
                    "prompt": "A completely different Soul.",
                    "values": ["changed"],
                },
                actor="test",
            )

            after = system.identities.verify_temperament(root)
            self.assertEqual(before, after)
            self.assertEqual(after["soul_influence"], "forbidden")

    def test_invalid_existing_temperament_is_never_silently_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            system = CompleteLifeSystem(Path(temporary))
            identity = system.create_identity("tamper-test")
            root = system.identities.root_for(identity["life_id"])
            document_path = root / "identity" / "temperament.json"
            document = json.loads(document_path.read_text(encoding="utf-8"))
            document["traits_milli"]["openness"] = 999
            document_path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaises(LifeCoreError) as raised:
                system.identities.ensure_temperament(root)

            self.assertEqual(raised.exception.code, "temperament_signature_invalid")
            self.assertEqual(
                json.loads(document_path.read_text(encoding="utf-8"))["traits_milli"]["openness"],
                999,
            )

    def test_one_turn_is_idempotent_and_cannot_overwrite_personality(self) -> None:
        innate = generate_innate_temperament("org_slow", seed=42)
        initial = initial_temperament_state(innate)
        adapted, changed = adapt_from_completed_turn(
            innate,
            initial,
            evidence_id="turn_1",
            user_text="我们继续讨论这个问题吗？",
            assistant_text="可以，我们逐步核对证据。",
            affect={"valence": 0.8, "arousal": 0.7, "dominance": 0.4},
        )
        self.assertTrue(changed)
        self.assertEqual(adapted["completed_turn_evidence"], 1)
        self.assertLessEqual(
            max(
                abs(adapted["traits_micro"][key] - initial["traits_micro"][key])
                for key in TRAIT_KEYS
            ),
            100,
        )
        duplicate, duplicate_changed = adapt_from_completed_turn(
            innate,
            adapted,
            evidence_id="turn_1",
            user_text="ignored",
            assistant_text="ignored",
            affect={"valence": -1, "arousal": -1, "dominance": -1},
        )
        self.assertFalse(duplicate_changed)
        self.assertEqual(duplicate, adapted)
        self.assertNotIn("user_text", json.dumps(adapted))

    def test_completed_runtime_turn_adapts_only_its_identity_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "runtime",
                mode="embedded",
            )
            try:
                first_id = str(runtime._active()["life_id"])
                status, created, _ = runtime.request(
                    "POST",
                    "/api/v1/v3/life/identity/create",
                    {"name": "second"},
                )
                self.assertEqual(status, 200, created)
                second_id = str(created["identity"]["life_id"])
                first_before = deepcopy(runtime._scope_state(first_id)["temperament"])
                second_before = deepcopy(runtime._scope_state(second_id)["temperament"])

                status, turn, _ = runtime.request(
                    "POST",
                    "/api/v1/v3/life/memory/turn",
                    {
                        "life_id": second_id,
                        "conversation_id": "test",
                        "turn_id": "turn_slow_1",
                        "user_text": "请解释一下这个设计？",
                        "assistant_text": "我会从边界和证据开始解释。",
                    },
                )
                self.assertEqual(status, 200, turn)
                self.assertEqual(turn["temperament"]["completed_turn_evidence"], 1)
                self.assertEqual(runtime._scope_state(first_id)["temperament"], first_before)
                self.assertNotEqual(runtime._scope_state(second_id)["temperament"], second_before)

                status, duplicate, _ = runtime.request(
                    "POST",
                    "/api/v1/v3/life/memory/turn",
                    {
                        "life_id": second_id,
                        "conversation_id": "test",
                        "turn_id": "turn_slow_1",
                        "user_text": "请解释一下这个设计？",
                        "assistant_text": "我会从边界和证据开始解释。",
                    },
                )
                self.assertEqual(status, 200, duplicate)
                self.assertTrue(duplicate["duplicate"])
                self.assertEqual(duplicate["temperament"]["completed_turn_evidence"], 1)

                status, payload, _ = runtime.request(
                    "GET",
                    "/api/v1/v3/life/temperament",
                    None,
                )
                self.assertEqual(status, 200, payload)
                self.assertEqual(payload["temperament"]["life_id"], second_id)
                self.assertEqual(payload["temperament"]["soul_influence"], "none")
            finally:
                runtime.close()

    def test_temperament_is_a_separate_context_constraint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = EmbeddedLifeRuntime(
                data_root=root / "life-data",
                runtime_root=root / "runtime",
                mode="embedded",
            )
            try:
                items = runtime._external_memory_items()
                temperament_items = [
                    item for item in items if item.item_ref.startswith("temperament_")
                ]
                self.assertEqual(len(temperament_items), 1)
                summary = json.loads(temperament_items[0].summary)
                self.assertIn("traits", summary)
                self.assertIn("affective_disposition", summary)
                self.assertIn("independent from Soul", summary["instruction"])
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
