"""D-11 convergence tests: live user facts commit to the contract memory store.

Covers the §7.1 same-store projection upgrade:
- `_memory_assert` writes memory_assertions + protected payload (one
  authoritative write face with the journal),
- globally monotonic ``memory_change_seq`` with transactional outbox rows,
- privacy deletion via plaintext-free tombstone + idempotent outbox receipt,
- request-side ``required_memory_seq`` capture with wait/direct/NO_ACTION
  projection gating,
- the memory/candidates queue (no more fake-accepted stub),
- startup reconciliation with the contract store as projection authority.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from contracts import canonical_json_bytes
from life_service.embedded_runtime import EmbeddedLifeRuntime


class MemoryContractConvergence(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.env = mock.patch.dict(
            os.environ, {"TIANGONG_LIFE_HEARTBEAT_SECONDS": "3600"}
        )
        self.env.start()
        self.runtime = EmbeddedLifeRuntime(
            data_root=self.root / "life-data",
            runtime_root=self.root / "runtime",
            mode="embedded",
        )

    def tearDown(self) -> None:
        try:
            self.runtime.close()
        except Exception:
            pass
        self.env.stop()
        self.temporary.cleanup()

    def _reopen(self) -> EmbeddedLifeRuntime:
        return EmbeddedLifeRuntime(
            data_root=self.root / "life-data",
            runtime_root=self.root / "runtime",
            mode="embedded",
        )

    def _life_id(self) -> str:
        return str(self.runtime.system.identities.active(required=True)["life_id"])

    def _assert_memory(self, memory_id: str, text: str, **extra):
        status, value, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/assert",
            {
                "memory_id": memory_id,
                "memory_type": "semantic",
                "content": {"text": text},
                "provenance": {"source": "convergence-test"},
                **extra,
            },
        )
        self.assertEqual(status, 200, value)
        return value

    def _store(self):
        return self.runtime.authority_store

    def test_assert_commits_contract_assertion_and_monotonic_seq(self):
        first = self._assert_memory("mem-alpha", "喜欢无糖豆浆")
        second = self._assert_memory("mem-beta", "住在杭州")
        self.assertIn("contract_memory_id", first)
        self.assertGreater(first["memory_change_seq"], 0)
        self.assertGreater(second["memory_change_seq"], first["memory_change_seq"])

        store = self._store()
        contract_id = first["contract_memory_id"]
        assertion = store.get_latest_memory_assertion(contract_id)
        self.assertIsNotNone(assertion)
        self.assertEqual(assertion.revision, 1)
        self.assertEqual(assertion.lifecycle_status, "active")
        self.assertEqual(assertion.life_id, self._life_id())
        plaintext = json.loads(
            store.read_protected_payload(assertion.protected_payload_id).decode("utf-8")
        )
        self.assertEqual(plaintext["schema"], "tiangong.life.live-memory-record.v1")
        self.assertEqual(plaintext["record"]["content"], {"text": "喜欢无糖豆浆"})
        # The contract read face now sees the live user fact (D-11 core).
        visible = store.list_latest_memory_assertions(self._life_id())
        self.assertIn(contract_id, {item.memory_id for item in visible})
        # Live ids that do not fit the contract pattern are deterministically mapped.
        self.assertNotEqual(contract_id, "mem-alpha")
        self.assertRegex(contract_id, r"^mem_[0-9a-f]{64}$")

        status, head, _ = self.runtime.request(
            "GET", "/api/v1/v3/life/memory/projection-head", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(head["memory_change_seq"], second["memory_change_seq"])
        self.assertEqual(head["outbox_pending"], 2)

    def test_duplicate_assert_is_idempotent_on_contract_store(self):
        first = self._assert_memory("mem-dup", "重复事实")
        again = self._assert_memory("mem-dup", "重复事实")
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["memory_change_seq"], first["memory_change_seq"])
        assertion = self._store().get_latest_memory_assertion(first["contract_memory_id"])
        self.assertEqual(assertion.revision, 1)

    def test_status_change_and_correct_advance_revision_and_seq(self):
        created = self._assert_memory("mem-flow", "原始断言")
        contract_id = created["contract_memory_id"]
        status, changed, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/status",
            {"memory_id": "mem-flow", "status": "recall_suppressed"},
        )
        self.assertEqual(status, 200, changed)
        self.assertGreater(changed["memory_change_seq"], created["memory_change_seq"])
        latest = self._store().get_latest_memory_assertion(contract_id)
        self.assertEqual(latest.revision, 2)
        self.assertEqual(latest.lifecycle_status, "recall_suppressed")

        status, corrected, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/correct",
            {
                "target_memory_id": "mem-flow",
                "content": {"text": "修正后的断言"},
                "provenance": {"source": "convergence-test"},
            },
        )
        self.assertEqual(status, 200, corrected)
        self.assertGreater(
            corrected["memory_change_seq"], changed["memory_change_seq"]
        )
        self.assertGreater(
            corrected["target_memory_change_seq"], changed["memory_change_seq"]
        )
        target = self._store().get_latest_memory_assertion(contract_id)
        self.assertEqual(target.revision, 3)
        self.assertEqual(target.lifecycle_status, "corrected")
        replacement = self._store().get_latest_memory_assertion(
            corrected["contract_memory_id"]
        )
        self.assertEqual(replacement.revision, 1)
        self.assertEqual(replacement.lifecycle_status, "active")

    def test_delete_tombstone_outbox_receipt_and_fail_closed_reads(self):
        created = self._assert_memory("mem-gone", "敏感 plaintext secret fact")
        contract_id = created["contract_memory_id"]
        store = self._store()
        payload_id = store.get_latest_memory_assertion(
            contract_id
        ).protected_payload_id

        status, deleted, _ = self.runtime.request(
            "POST", "/api/v1/v3/life/memory/delete", {"memory_id": "mem-gone"}
        )
        self.assertEqual(status, 200, deleted)
        self.assertGreater(
            deleted["memory_change_seq"], created["memory_change_seq"]
        )
        self.assertTrue(deleted["tombstone_id"].startswith("ptm_"))

        latest = store.get_latest_memory_assertion(contract_id)
        self.assertEqual(latest.lifecycle_status, "deleted")
        self.assertIsNone(latest.protected_payload_id)
        # Sensitive read after deletion fails closed: payload keys are gone.
        with self.assertRaises(Exception):
            store.read_protected_payload(payload_id)
        visible = store.list_latest_memory_assertions(self._life_id())
        self.assertNotIn(contract_id, {item.memory_id for item in visible})

        # The tombstone carries hashes and identifiers only — no plaintext.
        connection = store._connection  # noqa: SLF001
        row = connection.execute(
            "SELECT payload FROM privacy_deletion_tombstones WHERE tombstone_id = ?",
            (deleted["tombstone_id"],),
        ).fetchone()
        self.assertIsNotNone(row)
        tombstone_text = bytes(row["payload"]).decode("utf-8")
        self.assertNotIn("敏感", tombstone_text)
        self.assertNotIn("plaintext secret fact", tombstone_text)

        # Deletion went through the outbox with an idempotent receipt.
        status, outbox, _ = self.runtime.request(
            "GET", "/api/v1/v3/life/memory/outbox", {}
        )
        self.assertEqual(status, 200)
        kinds = {entry["change_seq"]: entry["change_kind"] for entry in outbox["entries"]}
        self.assertEqual(kinds[deleted["memory_change_seq"]], "tombstone")

        status, ack, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/outbox/ack",
            {"change_seq": deleted["memory_change_seq"], "receipt_id": "receipt-1"},
        )
        self.assertEqual(status, 200, ack)
        self.assertTrue(ack["acked"])
        status, ack_retry, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/outbox/ack",
            {"change_seq": deleted["memory_change_seq"], "receipt_id": "receipt-1"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(ack_retry["duplicate"])
        status, conflict, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/outbox/ack",
            {"change_seq": deleted["memory_change_seq"], "receipt_id": "receipt-2"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error_code"], "life.memory.outbox_ack_conflict")

        # Scope-side search never serves deleted plaintext either: the old
        # text no longer matches, and the deleted row is a bare tombstone.
        status, search, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/search",
            {"query": "plaintext secret fact", "statuses": ["deleted"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(search["results"], [])
        status, search, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/search",
            {"statuses": ["deleted"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            [row["content"] for row in search["results"]], [{"tombstone": True}]
        )

    def test_required_memory_seq_gating_semantics(self):
        created = self._assert_memory("mem-gated", "门控事实")
        seq = created["memory_change_seq"]

        status, current, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/search",
            {"query": "门控事实", "required_memory_seq": seq},
        )
        self.assertEqual(status, 200, current)
        self.assertEqual(current["memory_projection"]["status"], "current")
        self.assertEqual(len(current["results"]), 1)

        future = seq + 100
        status, no_action, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/search",
            {
                "query": "门控事实",
                "required_memory_seq": future,
                "on_projection_lag": "no_action",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(no_action["action"], "NO_ACTION")
        self.assertEqual(no_action["results"], [])
        self.assertEqual(no_action["memory_projection"]["status"], "no_action")

        status, direct, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/search",
            {
                "query": "门控事实",
                "required_memory_seq": future,
                "on_projection_lag": "direct",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(direct["memory_projection"]["status"], "direct_read")
        self.assertEqual(len(direct["results"]), 1)

        status, waited, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/search",
            {
                "query": "门控事实",
                "required_memory_seq": future,
                "on_projection_lag": "wait",
                "projection_wait_ms": 50,
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(waited["error_code"], "life.memory.projection_lag")

        status, invalid, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/search",
            {"query": "门控事实", "required_memory_seq": -1},
        )
        self.assertEqual(status, 400)
        self.assertEqual(invalid["error_code"], "life.memory.required_seq_invalid")

    def test_candidates_are_durably_queued_and_journaled(self):
        status, value, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/candidates",
            {
                "candidates": [
                    {"memory_type": "preference", "content": {"text": "候选偏好"}},
                    {"memory_type": "fact", "content": {"text": "候选事实"}},
                    {"content": "not-a-mapping"},
                ]
            },
        )
        self.assertEqual(status, 200, value)
        self.assertEqual(len(value["accepted"]), 2)
        self.assertEqual(len(value["rejected"]), 1)
        self.assertEqual(
            value["rejected"][0]["reason_code"], "life.memory.candidate_content_invalid"
        )
        for entry in value["accepted"]:
            self.assertEqual(entry["status"], "proposed")
            self.assertFalse(entry["duplicate"])
        self.assertEqual(value["queued_total"], 2)
        candidate_ids = [entry["candidate_id"] for entry in value["accepted"]]

        # The queue is real state: candidates are not silently asserted.
        scope = self.runtime._scope_state(self._life_id())  # noqa: SLF001
        self.assertEqual(set(scope["memory_candidates"]), set(candidate_ids))
        self.assertEqual(len(scope["memories"]), 0)

        status, again, _ = self.runtime.request(
            "POST",
            "/api/v1/v3/life/memory/candidates",
            {"candidates": [{"memory_type": "preference", "content": {"text": "候选偏好"}}]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(again["accepted"][0]["duplicate"])
        self.assertEqual(again["queued_total"], 2)

        self.runtime.close()
        self.runtime = self._reopen()
        scope = self.runtime._scope_state(self._life_id())  # noqa: SLF001
        self.assertEqual(set(scope["memory_candidates"]), set(candidate_ids))

    def test_startup_reconciliation_rolls_contract_store_forward(self):
        created = self._assert_memory("mem-roll", "前滚事实")
        contract_id = created["contract_memory_id"]
        self.runtime.close()

        # Simulate a pre-convergence deployment: the journal still holds the
        # fact, but the contract store lost its memory projection.
        store_path = self.root / "runtime" / "life-authority.shadow.sqlite3"
        connection = sqlite3.connect(store_path)
        try:
            connection.execute(
                "DELETE FROM memory_assertions WHERE memory_id = ?", (contract_id,)
            )
            connection.execute(
                "DELETE FROM memory_assertion_contracts WHERE memory_id = ?",
                (contract_id,),
            )
            connection.execute(
                "DELETE FROM memory_change_log WHERE memory_id = ?", (contract_id,)
            )
            connection.execute(
                "DELETE FROM memory_outbox WHERE memory_id = ?", (contract_id,)
            )
            connection.commit()
        finally:
            connection.close()

        self.runtime = self._reopen()
        store = self._store()
        assertion = store.get_latest_memory_assertion(contract_id)
        self.assertIsNotNone(assertion)
        self.assertEqual(assertion.lifecycle_status, "active")
        plaintext = json.loads(
            store.read_protected_payload(assertion.protected_payload_id).decode("utf-8")
        )
        self.assertEqual(plaintext["record"]["content"], {"text": "前滚事实"})
        seq = store.memory_change_seq_for(contract_id, assertion.revision)
        self.assertIsNotNone(seq)

    def test_startup_reconciliation_rebuilds_projection_from_contract(self):
        self.runtime.close()
        # Contract-native write (as produced by the migration path): the
        # journal has no event for this memory, so the scope projection must
        # be rebuilt from the contract store as the authority.
        store = EmbeddedLifeRuntime(
            data_root=self.root / "life-data",
            runtime_root=self.root / "runtime",
            mode="embedded",
        )
        life_id = str(store.system.identities.active(required=True)["life_id"])
        contract_id = "mem_" + "ab" * 32
        store.authority_store.put_live_memory_assertion(
            canonical_json_bytes({"legacy": "migrated fact"}),
            memory_id=contract_id,
            life_id=life_id,
            assertion_kind="legacy",
            epistemic_status="observed",
            lifecycle_status="active",
            privacy_scope="private",
            retention_class="LONG_TERM_MEMORY",
            valid_from_ms=1,
            created_at_ms=1,
        )
        store.close()

        self.runtime = self._reopen()
        scope = self.runtime._scope_state(life_id)  # noqa: SLF001
        self.assertIn(contract_id, scope["memories"])
        row = scope["memories"][contract_id]
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["content"], {"legacy": "migrated fact"})
        status, head, _ = self.runtime.request(
            "GET", "/api/v1/v3/life/memory/projection-head", {}
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(head["rebuilt_from_contract"], 1)

        # The rebuilt projection is stable across further restarts (no
        # divergence oscillation from classification enrichment).
        self.runtime.close()
        self.runtime = self._reopen()
        status, head, _ = self.runtime.request(
            "GET", "/api/v1/v3/life/memory/projection-head", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(head["reconciled_divergences"], 0)


if __name__ == "__main__":
    unittest.main()
