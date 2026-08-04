import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from contracts import (
    ComponentReadinessEvidence,
    ExpectedServiceComponent,
    ReadinessExpectation,
)
from total_gateway.bootstrap import GatewayConfig
from total_gateway.runtime import GatewayRuntime
from total_gateway.server import GatewayHttpServer


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64

COMPONENTS = (
    ("tiangong-backend", "execution", "1" * 64),
    ("tiangong-communication-service", "communication", "2" * 64),
    ("tiangong-life-service", "life", "3" * 64),
    ("tiangong-total-gateway", "orchestrator", "4" * 64),
)


def readiness_inputs(epoch):
    components = tuple(
        ExpectedServiceComponent(
            component_id=component_id,
            role=role,
            version="3.0.0",
            build_id=f"build_{component_id}",
            executable_sha256=binary_hash,
            schema_bundle_sha256=HASH_A,
        )
        for component_id, role, binary_hash in COMPONENTS
    )
    expectation = ReadinessExpectation(
        expectation_id="http_readiness_expectation_001",
        gateway_epoch=epoch,
        component_manifest_sha256=HASH_E,
        schema_bundle_sha256=HASH_A,
        capability_manifest_sha256=HASH_B,
        skill_index_sha256=HASH_C,
        release_policy_sha256=HASH_D,
        contract_artifact_manifest_sha256=HASH_E,
        components=components,
        expectation_sha256=HASH_E,
    ).with_computed_sha256()
    evidence = tuple(
        ComponentReadinessEvidence(
            evidence_id=f"http_evidence_{item.component_id}",
            component_id=item.component_id,
            component_role=item.role,
            instance_id=f"instance_{item.component_id}",
            version=item.version,
            build_id=item.build_id,
            executable_sha256=item.executable_sha256,
            gateway_epoch=epoch,
            component_manifest_sha256=HASH_E,
            schema_bundle_sha256=HASH_A,
            capability_manifest_sha256=HASH_B,
            skill_index_sha256=HASH_C,
            release_policy_sha256=HASH_D,
            contract_artifact_manifest_sha256=HASH_E,
            health_check_passed=True,
            observed_at_ms=int(time.time() * 1_000),
            evidence_sha256=HASH_E,
        ).with_computed_sha256()
        for item in components
    )
    return expectation, evidence


class GatewayHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        config = GatewayConfig(
            environment="test",
            port=0,
            state_root=Path(self.temporary.name) / "state",
            min_free_bytes=1_048_576,
            disk_probe_interval_ms=100,
            max_evidence_age_ms=5_000,
        )
        self.runtime = GatewayRuntime.start(config, now_ms=1_000)
        self.server = GatewayHttpServer(self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_address[1],
            timeout=5,
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.runtime.close()
        self.temporary.cleanup()

    def request(self, method, path):
        self.connection.request(method, path)
        response = self.connection.getresponse()
        payload = json.loads(response.read())
        return response, payload

    def test_server_can_restart_on_the_same_port_after_clean_close(self) -> None:
        port = int(self.server.server_address[1])
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.runtime.close()

        config = GatewayConfig(
            environment="test",
            port=port,
            state_root=Path(self.temporary.name) / "restart-state",
            min_free_bytes=1_048_576,
            disk_probe_interval_ms=100,
            max_evidence_age_ms=5_000,
        )
        self.runtime = GatewayRuntime.start(config, now_ms=2_000)
        self.server = GatewayHttpServer(self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        response, payload = self.request("GET", "/health")
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "ALIVE")

    def test_artifact_egress_rejects_a_canonical_but_truncated_body(self) -> None:
        token = "communication-test-token-" + "x" * 40
        self.runtime.config = self.runtime.config.model_copy(
            update={"communication_api_token": token}
        )
        wire = b'{"grant":{},"timeout_seconds":1}'
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_address[1],
            timeout=5,
        )
        try:
            connection.putrequest("POST", "/api/v1/internal/channel/artifacts/fetch")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(wire) + 17))
            connection.putheader("X-Tiangong-Communication-Token", token)
            connection.endheaders(wire)
            connection.sock.shutdown(socket.SHUT_WR)
            response = connection.getresponse()
            payload = json.loads(response.read())
        finally:
            connection.close()
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["reason_code"], "artifact_egress.request.invalid")

    def test_artifact_egress_rejects_duplicate_content_length_headers(self) -> None:
        token = "communication-test-token-" + "y" * 40
        self.runtime.config = self.runtime.config.model_copy(
            update={"communication_api_token": token}
        )
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_address[1],
            timeout=5,
        )
        try:
            connection.putrequest("POST", "/api/v1/internal/channel/artifacts/fetch")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", "0")
            connection.putheader("Content-Length", "0")
            connection.putheader("X-Tiangong-Communication-Token", token)
            connection.endheaders()
            response = connection.getresponse()
            payload = json.loads(response.read())
        finally:
            connection.close()
        self.assertEqual(response.status, 400)
        self.assertEqual(
            payload["reason_code"],
            "artifact_egress.content_length.invalid",
        )

    def test_health_is_alive_ready_is_503_until_all_evidence_matches(self) -> None:
        response, health = self.request("GET", "/health")
        self.assertEqual(response.status, 200)
        self.assertEqual(health["status"], "ALIVE")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")

        response, ready = self.request("GET", "/ready")
        self.assertEqual(response.status, 503)
        self.assertIn("readiness.evidence.not_configured", ready["reason_codes"])

        expectation, evidence = readiness_inputs(self.runtime.lease.gateway_epoch)
        component_ids = tuple(item.component_id for item in expectation.components)
        self.runtime.readiness.update(
            expectation,
            evidence,
            authenticated_component_ids=component_ids,
            binary_verified_component_ids=component_ids,
        )
        response, ready = self.request("GET", "/ready")
        self.assertEqual(response.status, 200)
        self.assertEqual(ready["status"], "READY")
        self.assertEqual(ready["decision"]["status"], "READY")

        self.runtime.store._connection.execute(  # noqa: SLF001 - readiness fault injection
            "CREATE TABLE test_schema_tamper(value TEXT) STRICT"
        )
        self.runtime.readiness._last_store_health_ns = 0  # noqa: SLF001
        try:
            response, ready = self.request("GET", "/ready")
            self.assertEqual(response.status, 503)
            self.assertIn("store.check.failed", ready["reason_codes"])
        finally:
            self.runtime.store._connection.execute("DROP TABLE test_schema_tamper")  # noqa: SLF001

        self.runtime.objects._connection.execute(  # noqa: SLF001 - readiness fault injection
            "CREATE TABLE test_object_schema_tamper(value TEXT) STRICT"
        )
        self.runtime.readiness._last_object_health_ns = 0  # noqa: SLF001
        try:
            response, ready = self.request("GET", "/ready")
            self.assertEqual(response.status, 503)
            self.assertIn("object_store.check.failed", ready["reason_codes"])
        finally:
            self.runtime.objects._connection.execute(  # noqa: SLF001
                "DROP TABLE test_object_schema_tamper"
            )

        self.runtime.facts._connection.execute(  # noqa: SLF001 - readiness fault injection
            "CREATE TABLE test_fact_schema_tamper(value TEXT) STRICT"
        )
        self.runtime.readiness._last_fact_health_ns = 0  # noqa: SLF001
        try:
            response, ready = self.request("GET", "/ready")
            self.assertEqual(response.status, 503)
            self.assertIn("fact_ledger.check.failed", ready["reason_codes"])
        finally:
            self.runtime.facts._connection.execute(  # noqa: SLF001
                "DROP TABLE test_fact_schema_tamper"
            )

        files = {path.name for path in self.runtime.config.state_root.iterdir()}
        self.assertIn("gateway.epoch.json", files)
        self.assertIn("gateway.instance.lock", files)
        self.assertIn("gateway.sqlite3", files)
        self.assertIn("facts.sqlite3", files)
        self.assertIn("objects", files)
        self.assertFalse(any(name.startswith(".disk-probe-") for name in files))

    def test_readiness_collector_failure_is_explicit_fail_closed_and_recovers(self) -> None:
        expectation, evidence = readiness_inputs(self.runtime.lease.gateway_epoch)
        component_ids = tuple(item.component_id for item in expectation.components)

        class ToggleCollector:
            fail = False

            def collect(self, *, now_ms):
                if self.fail:
                    raise RuntimeError("sensitive collector detail must not escape")
                return expectation, evidence, component_ids, component_ids

        collector = ToggleCollector()
        self.runtime.readiness_collector = collector

        response, ready = self.request("GET", "/ready")
        self.assertEqual(response.status, 200)
        self.assertEqual(ready["status"], "READY")
        self.assertEqual(ready["evidence_collection"]["status"], "COLLECTED")

        collector.fail = True
        response, failed = self.request("GET", "/ready")
        self.assertEqual(response.status, 503)
        self.assertEqual(failed["status"], "NOT_READY")
        self.assertIn(
            "readiness.evidence.collection_failed",
            failed["reason_codes"],
        )
        self.assertNotIn(
            "readiness.evidence.not_configured",
            failed["reason_codes"],
        )
        self.assertIsNone(failed["decision"])
        self.assertEqual(failed["evidence_collection"]["status"], "FAILED")
        self.assertEqual(
            failed["evidence_collection"]["failure"]["error_type"],
            "RuntimeError",
        )
        self.assertNotIn("sensitive collector detail", json.dumps(failed))

        collector.fail = False
        response, recovered = self.request("GET", "/ready")
        self.assertEqual(response.status, 200)
        self.assertEqual(recovered["status"], "READY")
        self.assertEqual(recovered["evidence_collection"]["status"], "COLLECTED")
        self.assertIsNone(recovered["evidence_collection"]["failure"])

    def test_no_business_routes_or_mutating_methods_exist(self) -> None:
        response, payload = self.request("GET", "/api/v1/chat")
        self.assertEqual(response.status, 404)
        self.assertEqual(payload["reason_code"], "http.route.not_found")
        response, payload = self.request("POST", "/ready")
        self.assertEqual(response.status, 405)
        self.assertEqual(payload["reason_code"], "http.method.not_allowed")


if __name__ == "__main__":
    unittest.main()
