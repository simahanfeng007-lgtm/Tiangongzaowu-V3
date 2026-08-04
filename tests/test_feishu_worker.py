import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path

from communication_service.adapters import AdapterRegistry
from communication_service.channel_authority import ChannelAuthorityGate
from communication_service.feishu_route import FeishuRouteLedger
from communication_service.feishu_worker import FeishuProductionAdapter
from communication_service.inbox import CommunicationInbox
from communication_service.raw_inbound_store import RawInboundStore
from contracts import (
    activate_candidate_owner,
    apply_channel_drain,
    begin_channel_cutover,
    build_channel_drain_evidence,
    canonical_json_bytes,
)
from tests.test_feishu_inbound import event


class _Protector:
    def protect(self, plaintext, entropy):
        key = hashlib.sha256(entropy).digest()
        return b"TEST" + bytes(
            value ^ key[index % len(key)] for index, value in enumerate(plaintext)
        )

    def unprotect(self, ciphertext, entropy):
        key = hashlib.sha256(entropy).digest()
        return bytearray(
            value ^ key[index % len(key)]
            for index, value in enumerate(ciphertext[4:])
        )


class _Credentials:
    values = {
        "app_id": "app-a",
        "app_secret": "secret-a",
        "bot_open_id": "bot-open-a",
        "encrypt_key": "",
        "platform_tenant_key": "tenant-key-a",
        "verification_token": "",
    }

    def get(self, channel, tenant_id, link_account_id):
        if (channel, tenant_id, link_account_id) == ("feishu", "tenant-a", "account-a"):
            return dict(self.values)
        return None


class _UnusedAttachmentIngestor:
    def ingest(self, *_args, **_kwargs):
        raise AssertionError("text event must not download an attachment")


class _LongConnection:
    def __init__(self):
        self.calls = 0

    def run_once(self, *, on_event, should_continue, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            on_event(canonical_json_bytes(event()))
        while should_continue():
            time.sleep(0.01)


class FeishuProductionWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.inbox = CommunicationInbox.open(root / "inbox.sqlite3", now_ms=1_000)
        self.routes = FeishuRouteLedger.open(
            root / "routes.sqlite3",
            now_ms=1_000,
            protector=_Protector(),
        )
        self.raw = RawInboundStore(root / "raw")
        self.forwarded = []
        self.forwarded_event = threading.Event()
        self.now = 2_000

        candidate = "candidate-7176"
        manifest_sha = "d" * 64
        snapshot = begin_channel_cutover(
            channel="feishu",
            tenant_id="tenant-a",
            link_account_id="account-a",
            gateway_epoch=17,
            legacy_owner_component_id="legacy-communication",
            legacy_owner_instance_id="legacy-instance",
            candidate_owner_instance_id=candidate,
            started_at_ms=1_000,
        )
        evidence = build_channel_drain_evidence(
            channel="feishu",
            tenant_id="tenant-a",
            link_account_id="account-a",
            gateway_epoch=17,
            legacy_owner_component_id="legacy-communication",
            legacy_owner_instance_id="legacy-instance",
            inbox_ledger_sha256="a" * 64,
            delivery_ledger_sha256="b" * 64,
            last_cursor_sha256=None,
            observed_at_ms=1_100,
        )
        drained = apply_channel_drain(snapshot, evidence)
        _, lease = activate_candidate_owner(
            drained,
            evidence,
            component_manifest_sha256=manifest_sha,
            issued_at_ms=1_200,
            lease_ttl_ms=30_000,
        )
        gate = ChannelAuthorityGate(
            owner_instance_id=candidate,
            expected_gateway_epoch=17,
            expected_component_manifest_sha256=manifest_sha,
        )
        gate.install_lease(lease, now_ms=self.now)
        self.registry = AdapterRegistry(gate)

    def tearDown(self):
        self.routes.close()
        self.inbox.close()
        self.temporary.cleanup()

    def clock(self):
        self.now += 1
        return self.now

    def forward(self, envelope, permit, *, now_ms):
        self.forwarded.append((envelope, permit, now_ms))
        self.forwarded_event.set()
        return {"accepted": True, "request_id": "synthetic"}

    def test_authenticated_long_connection_persists_forwards_and_acks_once(self):
        transport = _LongConnection()
        adapter = FeishuProductionAdapter(
            self.registry,
            self.inbox,
            self.routes,
            _Credentials(),
            self.raw,
            _UnusedAttachmentIngestor(),
            tenant_id="tenant-a",
            link_account_id="account-a",
            forward=self.forward,
            transport=transport,
            clock_ms=self.clock,
        )
        self.registry.register(adapter, now_ms=self.clock())
        adapter.start()
        self.assertTrue(self.forwarded_event.wait(3.0))
        adapter.close()

        self.assertEqual(len(self.forwarded), 1)
        self.assertEqual(self.forwarded[0][0].text, "你好，飞书")
        self.assertEqual(self.inbox.count_records(), 1)
        self.assertEqual(
            self.inbox.channel_drain_facts(
                channel="feishu",
                tenant_id="tenant-a",
                link_account_id="account-a",
            ).unacknowledged_count,
            0,
        )
        self.assertEqual(transport.calls, 1)


if __name__ == "__main__":
    unittest.main()
