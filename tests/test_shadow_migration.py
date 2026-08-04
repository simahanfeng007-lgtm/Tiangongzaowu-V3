from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from pydantic import ValidationError

from communication_service.inbox import (
    CommunicationInbox,
    InboxIngress,
    cursor_token_sha256,
    derive_cursor_stream_key,
)
from communication_service.shadow_mirror import (
    CommunicationShadowMirror,
    LoopbackShadowMirrorTransport,
)
from contracts import (
    InboundEnvelope,
    InboundScope,
    ShadowObservationBatch,
    build_shadow_decision_observation,
    build_shadow_ingress_copy,
    canonical_json_bytes,
    canonical_sha256,
    compare_shadow_observations,
    derive_inbound_scope_keys,
)
from total_gateway.bootstrap import GatewayConfig
from total_gateway.runtime import GatewayRuntime
from total_gateway.server import GatewayHttpServer
from total_gateway.store import GatewayStateStore, StoreConflictError
from tests.gateway_store_migration_support import downgrade_v12_to_v11


TOKEN = "shadow-token-" + "a" * 40


def envelope() -> InboundEnvelope:
    scope = InboundScope(
        channel="wechat",
        tenant_id="tenant-shadow",
        link_account_id="account-shadow",
        conversation_ref="conversation-shadow",
        channel_message_ref="message-shadow",
        sender_ref="sender-shadow",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id="inbound-shadow",
        channel=scope.channel,
        tenant_id=scope.tenant_id,
        link_account_id=scope.link_account_id,
        conversation_ref=scope.conversation_ref,
        conversation_scope_hash=keys.conversation_scope_hash,
        principal_scope_hash=keys.principal_scope_hash,
        message_scope_hash=keys.message_scope_hash,
        channel_message_ref=scope.channel_message_ref,
        sender_ref=scope.sender_ref,
        received_at_ms=1_000,
        idempotency_key=keys.idempotency_key,
        channel_metadata_hash="a" * 64,
        text="shadow only",
    )


def durable_permit(root: Path):
    incoming = envelope()
    record = InboxIngress(
        ingress_id=incoming.inbound_id,
        envelope=incoming,
        raw_payload_object_id="raw-shadow",
        raw_payload_sha256="b" * 64,
        raw_payload_size_bytes=32,
        cursor_stream_key=derive_cursor_stream_key(
            incoming.channel,
            incoming.tenant_id,
            incoming.link_account_id,
        ),
        previous_cursor_sha256=None,
        next_cursor_token="cursor-shadow",
        next_cursor_sha256=cursor_token_sha256("cursor-shadow"),
        captured_at_ms=1_050,
        ingress_sha256="0" * 64,
    ).with_computed_sha256()
    inbox = CommunicationInbox.open(root / "communication.sqlite3", now_ms=1_000)
    try:
        permit = inbox.persist_and_advance_cursor(record, persisted_at_ms=1_100).permit
    finally:
        inbox.close()
    return incoming, permit


def shadow_batch(root: Path):
    incoming, permit = durable_permit(root)
    copy = build_shadow_ingress_copy(
        incoming,
        source_ingress_sha256=permit.inbox_record_sha256,
        source_ack_permit_sha256=permit.permit_sha256,
        copied_at_ms=1_200,
    )
    candidate = build_shadow_decision_observation(
        copy,
        side="candidate",
        source_component_id="tiangong-communication-service",
        source_instance_id="communication-shadow-instance",
        source_decision_sha256=canonical_sha256({"candidate": "ACCEPTED"}),
        classification="ACCEPTED",
        should_forward=True,
        observed_at_ms=1_200,
    )
    batch = ShadowObservationBatch(
        ingress_copy=copy,
        observations=(candidate,),
        batch_sha256="0" * 64,
    ).with_computed_sha256()
    return incoming, permit, copy, candidate, batch


class ShadowContractTests(unittest.TestCase):
    def test_match_mismatch_and_effect_authority_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, _, copy, candidate, _ = shadow_batch(Path(temporary))
            legacy = build_shadow_decision_observation(
                copy,
                side="legacy",
                source_component_id="tiangong-legacy-communication",
                source_instance_id="legacy-shadow-instance",
                source_decision_sha256=canonical_sha256({"legacy": "ACCEPTED"}),
                classification="ACCEPTED",
                should_forward=True,
                observed_at_ms=1_210,
            )
            comparison = compare_shadow_observations(
                copy, legacy, candidate, compared_at_ms=1_220
            )
            self.assertEqual(comparison.status, "MATCH")
            self.assertFalse(comparison.effects_permitted)
            self.assertFalse(comparison.request_creation_permitted)
            self.assertTrue(comparison.has_valid_sha256())

            rejected = legacy.model_copy(
                update={
                    "classification": "GROUP_DISABLED",
                    "should_forward": False,
                    "observation_sha256": "0" * 64,
                }
            ).with_computed_sha256()
            comparison = compare_shadow_observations(
                copy, rejected, candidate, compared_at_ms=1_220
            )
            self.assertEqual(comparison.status, "MISMATCH")
            self.assertEqual(
                comparison.mismatch_fields,
                ("classification", "should_forward"),
            )

            payload = copy.model_dump(mode="json")
            payload["effects_permitted"] = True
            with self.assertRaises(ValidationError):
                type(copy).model_validate_json(json.dumps(payload), strict=True)


class ShadowStoreTests(unittest.TestCase):
    def test_persistence_is_idempotent_and_never_creates_business_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, copy, candidate, batch = shadow_batch(root)
            store_path = (root / "gateway.sqlite3").resolve()
            store = GatewayStateStore.open(store_path, now_ms=1_000)
            first = store.record_shadow_batch(batch, compared_at_ms=1_300)
            self.assertEqual(first.comparison.status, "WAITING_FOR_LEGACY")
            self.assertTrue(first.copy_created)
            self.assertEqual(first.observations_created, 1)
            repeated = store.record_shadow_batch(batch, compared_at_ms=1_301)
            self.assertTrue(repeated.duplicate)

            legacy = build_shadow_decision_observation(
                copy,
                side="legacy",
                source_component_id="tiangong-legacy-communication",
                source_instance_id="legacy-shadow-instance",
                source_decision_sha256=canonical_sha256({"legacy": "ACCEPTED"}),
                classification="ACCEPTED",
                should_forward=True,
                observed_at_ms=1_250,
            )
            legacy_batch = ShadowObservationBatch(
                ingress_copy=copy,
                observations=(legacy,),
                batch_sha256="0" * 64,
            ).with_computed_sha256()
            matched = store.record_shadow_batch(legacy_batch, compared_at_ms=1_302)
            self.assertEqual(matched.comparison.status, "MATCH")
            self.assertEqual(store.count_shadow_records(), (1, 2))
            self.assertEqual(store.count_journal_entries(), 0)
            self.assertEqual(
                store._connection.execute("SELECT count(*) FROM outbox").fetchone()[0],  # noqa: SLF001
                0,
            )
            self.assertEqual(
                store._connection.execute("SELECT count(*) FROM effect_ledger").fetchone()[0],  # noqa: SLF001
                0,
            )

            changed = legacy.model_copy(
                update={
                    "source_decision_sha256": "c" * 64,
                    "observation_sha256": "0" * 64,
                }
            )
            changed = changed.model_copy(
                update={
                    "observation_id": "shobs_"
                    + canonical_sha256(
                        {
                            "domain": "tiangong.migration.shadow-decision.v1",
                            "shadow_id": changed.shadow_id,
                            "side": changed.side,
                            "source_component_id": changed.source_component_id,
                            "source_instance_id": changed.source_instance_id,
                            "source_decision_sha256": changed.source_decision_sha256,
                        }
                    )
                }
            ).with_computed_sha256()
            conflict = ShadowObservationBatch(
                ingress_copy=copy,
                observations=(changed,),
                batch_sha256="0" * 64,
            ).with_computed_sha256()
            with self.assertRaises(StoreConflictError):
                store.record_shadow_batch(conflict, compared_at_ms=1_303)
            self.assertTrue(store.health_check(now_ms=1_304, full=True).healthy)
            store.close()

            reopened = GatewayStateStore.open(store_path, now_ms=1_400)
            try:
                comparison = reopened.get_shadow_comparison(
                    copy.shadow_id, compared_at_ms=1_401
                )
                self.assertIsNotNone(comparison)
                self.assertEqual(comparison.status, "MATCH")
            finally:
                reopened.close()

    def test_semantic_tamper_makes_gateway_store_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, _, _, batch = shadow_batch(root)
            store = GatewayStateStore.open((root / "gateway.sqlite3").resolve(), now_ms=1_000)
            try:
                store.record_shadow_batch(batch, compared_at_ms=1_300)
                store._connection.execute(  # noqa: SLF001
                    "UPDATE shadow_decision SET classification = 'TAMPERED'"
                )
                self.assertFalse(store.health_check(now_ms=1_301, full=True).healthy)
            finally:
                store.close()

    def test_batch_is_atomic_and_concurrent_replay_has_one_first_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, _, _, batch = shadow_batch(root)
            path = (root / "gateway.sqlite3").resolve()
            first = GatewayStateStore.open(path, now_ms=1_000)
            first._connection.execute(  # noqa: SLF001
                """
                CREATE TRIGGER abort_shadow_decision
                BEFORE INSERT ON shadow_decision
                BEGIN SELECT RAISE(ABORT, 'fault injection'); END
                """
            )
            try:
                with self.assertRaises(sqlite3.DatabaseError):
                    first.record_shadow_batch(batch, compared_at_ms=1_300)
                self.assertEqual(first.count_shadow_records(), (0, 0))
            finally:
                first._connection.execute("DROP TRIGGER abort_shadow_decision")  # noqa: SLF001

            second = GatewayStateStore.open(path, now_ms=1_100)
            barrier = threading.Barrier(2)
            results: list[tuple[bool, bool]] = []
            errors: list[Exception] = []

            def record(store: GatewayStateStore, now_ms: int) -> None:
                barrier.wait()
                try:
                    result = store.record_shadow_batch(batch, compared_at_ms=now_ms)
                    results.append((result.copy_created, result.duplicate))
                except Exception as exc:  # pragma: no cover - asserted empty
                    errors.append(exc)

            threads = (
                threading.Thread(target=record, args=(first, 1_301)),
                threading.Thread(target=record, args=(second, 1_302)),
            )
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            self.assertFalse(errors)
            self.assertEqual(sorted(results), [(False, True), (True, False)])
            self.assertEqual(first.count_shadow_records(), (1, 1))
            second.close()
            first.close()

    def test_version_seven_store_migrates_to_shadow_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = (Path(temporary) / "gateway.sqlite3").resolve()
            store = GatewayStateStore.open(path, now_ms=1_000)
            store.close()
            connection = sqlite3.connect(path)
            try:
                downgrade_v12_to_v11(connection)
                connection.execute("DROP INDEX outbox_dispatch_boundary_started")
                connection.execute("DROP TABLE outbox_dispatch_boundary")
                connection.execute("DROP TABLE request_inbound_payload")
                connection.execute("DROP INDEX channel_one_active_lease")
                connection.execute("DROP INDEX channel_cutover_scope_epoch")
                connection.execute("DROP TABLE channel_ownership_lease")
                connection.execute("DROP TABLE channel_drain_evidence")
                connection.execute("DROP TABLE channel_cutover")
                connection.execute("DROP INDEX shadow_decision_compare")
                connection.execute("DROP TABLE shadow_decision")
                connection.execute("DROP TABLE shadow_ingress")
                connection.execute("DELETE FROM schema_migrations WHERE version > 7")
                connection.execute("PRAGMA user_version = 7")
                connection.commit()
            finally:
                connection.close()
            migrated = GatewayStateStore.open(path, now_ms=2_000)
            try:
                self.assertEqual(migrated.count_shadow_records(), (0, 0))
                self.assertTrue(migrated.health_check(now_ms=2_001, full=True).healthy)
            finally:
                migrated.close()


class ShadowHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        config = GatewayConfig(
            environment="test",
            port=0,
            state_root=(self.root / "gateway-state").resolve(),
            min_free_bytes=1_048_576,
            shadow_api_token=TOKEN,
        )
        self.runtime = GatewayRuntime.start(config, now_ms=1_000)
        self.server = GatewayHttpServer(self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.runtime.close()
        self.temporary.cleanup()

    def test_7176_client_reaches_private_route_without_creating_request_or_effect(self) -> None:
        incoming, permit, copy, _, _ = shadow_batch(self.root)
        transport = LoopbackShadowMirrorTransport(
            f"http://127.0.0.1:{self.server.server_address[1]}",
            TOKEN,
        )
        mirror = CommunicationShadowMirror(
            transport,
            source_instance_id="communication-shadow-instance",
        )
        comparison = mirror.mirror_candidate(
            incoming,
            permit,
            classification="ACCEPTED",
            should_forward=True,
            source_decision_sha256=canonical_sha256({"candidate": "ACCEPTED"}),
            observed_at_ms=1_300,
        )
        self.assertEqual(comparison.status, "WAITING_FOR_LEGACY")
        self.assertFalse(comparison.effects_permitted)
        self.assertEqual(self.runtime.store.count_shadow_records(), (1, 1))
        self.assertEqual(self.runtime.store.count_journal_entries(), 0)

        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=5
        )
        try:
            connection.request(
                "GET",
                f"/api/v1/migration/shadow/comparison?shadow_id={comparison.shadow_id}",
                headers={"X-Tiangong-Shadow-Token": TOKEN},
            )
            response = connection.getresponse()
            status_payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(
                status_payload["comparison"]["status"], "WAITING_FOR_LEGACY"
            )
            self.assertFalse(status_payload["request_created"])
            self.assertFalse(status_payload["effects_permitted"])
        finally:
            connection.close()

        legacy = build_shadow_decision_observation(
            copy,
            side="legacy",
            source_component_id="tiangong-legacy-communication",
            source_instance_id="legacy-shadow-instance",
            source_decision_sha256=canonical_sha256({"legacy": "ACCEPTED"}),
            classification="ACCEPTED",
            should_forward=True,
            observed_at_ms=1_250,
        )
        batch = mirror.build_candidate_batch(
            incoming,
            permit,
            classification="ACCEPTED",
            should_forward=True,
            source_decision_sha256=canonical_sha256({"candidate": "ACCEPTED"}),
            observed_at_ms=1_300,
            legacy_observation=legacy,
        )
        self.assertEqual(transport.submit(batch).status, "MATCH")
        self.assertEqual(self.runtime.store.count_shadow_records(), (1, 2))
        self.assertEqual(
            self.runtime.store._connection.execute("SELECT count(*) FROM outbox").fetchone()[0],  # noqa: SLF001
            0,
        )
        self.assertEqual(
            self.runtime.store._connection.execute("SELECT count(*) FROM effect_ledger").fetchone()[0],  # noqa: SLF001
            0,
        )

    def test_browser_origin_and_wrong_token_are_rejected_without_cors(self) -> None:
        _, _, _, _, batch = shadow_batch(self.root)
        body = canonical_json_bytes(batch.model_dump(mode="json"))
        for token, origin, expected in (
            ("wrong-token-" + "b" * 40, None, 401),
            (TOKEN, "null", 403),
        ):
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.server.server_address[1], timeout=5
            )
            try:
                headers = {
                    "Content-Type": "application/json",
                    "X-Tiangong-Shadow-Token": token,
                }
                if origin is not None:
                    headers["Origin"] = origin
                connection.request(
                    "POST",
                    "/api/v1/migration/shadow/observations",
                    body=body,
                    headers=headers,
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, expected)
                self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
                self.assertFalse(payload["effects_permitted"])
            finally:
                connection.close()
        self.assertEqual(self.runtime.store.count_shadow_records(), (0, 0))

    def test_noncanonical_duplicate_or_wrong_method_never_persists(self) -> None:
        _, _, _, _, batch = shadow_batch(self.root)
        canonical = canonical_json_bytes(batch.model_dump(mode="json"))
        noncanonical = json.dumps(
            batch.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        duplicate = b'{"batch_sha256":"' + b"a" * 64 + b'","batch_sha256":"' + b"b" * 64 + b'"}'
        for method, body, expected in (
            ("POST", noncanonical, 400),
            ("POST", duplicate, 400),
            ("GET", b"", 405),
        ):
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.server.server_address[1], timeout=5
            )
            try:
                connection.request(
                    method,
                    "/api/v1/migration/shadow/observations",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Tiangong-Shadow-Token": TOKEN,
                    },
                )
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, expected)
            finally:
                connection.close()
        self.assertNotEqual(canonical, noncanonical)
        self.assertEqual(self.runtime.store.count_shadow_records(), (0, 0))

    def test_desktop_wires_one_private_token_to_7176_and_7184_only(self) -> None:
        root = Path(__file__).resolve().parents[1]
        main = (root / "app/main.js").read_text(encoding="utf-8")
        preload = (root / "app/preload.js").read_text(encoding="utf-8")
        self.assertIn("const SHADOW_API_TOKEN = crypto.randomBytes(48)", main)
        self.assertIn("TIANGONG_COMMUNICATION_SHADOW_TOKEN: SHADOW_API_TOKEN", main)
        self.assertIn("TIANGONG_GATEWAY_SHADOW_TOKEN: SHADOW_API_TOKEN", main)
        self.assertNotIn("SHADOW_API_TOKEN", preload)
        self.assertNotIn("TIANGONG_GATEWAY_SHADOW_TOKEN", preload)


if __name__ == "__main__":
    unittest.main()
