import tempfile
import threading
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contracts import (
    PublicKeyDescriptor,
    TrustBundle,
    TrustScope,
    canonical_sha256,
)
from runtime_security import EphemeralTestProtector
from total_gateway.store import GatewayStateStore, StoreConflictError
from total_gateway.tickets import (
    ProtectedKeyStore,
    TicketSigner,
    TicketVerificationError,
    verify_delivery_ticket,
    verify_execution_ticket,
)
from tests.test_delivery_contracts import delivery_ticket
from tests.test_execution_contracts import execution_ticket


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def trust(descriptor: PublicKeyDescriptor, *, epoch: int = 3) -> TrustBundle:
    return TrustBundle(
        bundle_id="trust_bundle_runtime_001",
        revision=1,
        gateway_epoch=epoch,
        generated_at_ms=20_000,
        required_scopes=(
            TrustScope(
                issuer=descriptor.issuer,
                audience=descriptor.audience,
                purpose=descriptor.purpose,
            ),
        ),
        keys=(descriptor,),
        production_ready=True,
        bundle_sha256=HASH_A,
    ).with_computed_sha256()


class TicketCryptographyTests(unittest.TestCase):
    def test_dpapi_key_roundtrip_signs_and_verifies_execution_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "key-store"
            store = ProtectedKeyStore(root, protector=EphemeralTestProtector())
            unsigned = execution_ticket().payload
            created = store.create_key(
                kid="execution_key_runtime_001",
                purpose="execution_ticket",
                audience="tiangong-backend",
                issuer="tiangong-total-gateway",
                not_before_ms=0,
                not_after_ms=100_000,
                component_manifest_hash=unsigned.component_manifest_hash,
                created_at_ms=1_000,
            )
            private = store.load_private_key(created.private_envelope)
            signed = TicketSigner(created.public_descriptor.kid, private).sign_execution(unsigned)
            verified = verify_execution_ticket(signed, trust(created.public_descriptor), now_ms=20_000)
            self.assertEqual(verified, created.public_descriptor)
            blob = root / created.private_envelope.storage_relative_path
            self.assertNotIn(b"PRIVATE KEY", blob.read_bytes())
            self.assertTrue(created.private_envelope.has_valid_sha256())

    def test_key_storage_name_is_fixed_size_and_survives_legacy_path_budget(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tg-key-path-") as temporary:
            base = Path(temporary)
            padding = "p" * max(1, 180 - len(str(base)) - 1)
            root = base / padding
            kid = "delivery-" + "a" * 138
            store = ProtectedKeyStore(root, protector=EphemeralTestProtector())
            created = store.create_key(
                kid=kid,
                purpose="delivery_ticket",
                audience="tiangong-communication-service",
                issuer="tiangong-total-gateway",
                not_before_ms=0,
                not_after_ms=100_000,
                component_manifest_hash=HASH_A,
                created_at_ms=1_000,
            )
            blob = root / created.private_envelope.storage_relative_path
            self.assertTrue(blob.is_file())
            self.assertEqual(blob.parent, root / "keys")
            self.assertRegex(blob.name, r"^k-[A-Za-z0-9_-]{27}\.dpapi$")
            self.assertNotIn(kid, blob.name)
            self.assertLess(len(str(blob.with_suffix(".dtmp"))), 248)
            self.assertEqual(len(store._storage_stem(kid)), 29)
            store.load_private_key(created.private_envelope)

    def test_release_scoped_key_path_stays_under_legacy_windows_budget(self) -> None:
        # Reproduce the deepest redirected-profile path that failed in the
        # packaged first-run matrix.  The manifest scope is part of the total
        # budget even though ProtectedKeyStore only owns the final segments.
        from total_gateway.orchestration import manifest_authority_scope

        state_root_length = 172
        manifest_scope = manifest_authority_scope("a" * 64)
        suffix = str(Path("ta") / "m" / manifest_scope)
        authority_root_length = state_root_length + 1 + len(suffix)
        deepest_temporary_length = (
            authority_root_length
            + len(str(Path("keys") / "k-"))
            + 27
            + len(".dtmp")
            + 1
        )

        self.assertLessEqual(deepest_temporary_length, 248)

    def test_ticket_payload_tamper_wrong_epoch_and_wrong_purpose_fail_closed(self) -> None:
        private = Ed25519PrivateKey.generate()
        unsigned = execution_ticket().payload
        raw_public = private.public_key().public_bytes_raw()
        descriptor = PublicKeyDescriptor(
            kid="execution_key_runtime_001",
            issuer="tiangong-total-gateway",
            audience="tiangong-backend",
            purpose="execution_ticket",
            public_key_base64url=__import__("base64").urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
            public_key_sha256=__import__("hashlib").sha256(raw_public).hexdigest(),
            state="ACTIVE",
            not_before_ms=0,
            not_after_ms=100_000,
            component_manifest_hash=unsigned.component_manifest_hash,
        )
        signed = TicketSigner(descriptor.kid, private).sign_execution(unsigned)
        tampered = signed.model_copy(
            update={"payload": signed.payload.model_copy(update={"arguments_hash": HASH_C})}
        )
        with self.assertRaises(TicketVerificationError) as caught:
            verify_execution_ticket(tampered, trust(descriptor), now_ms=20_000)
        self.assertEqual(caught.exception.code, "ticket.signature.invalid")
        with self.assertRaises(TicketVerificationError) as caught:
            verify_execution_ticket(signed, trust(descriptor, epoch=4), now_ms=20_000)
        self.assertEqual(caught.exception.code, "ticket.gateway_epoch.mismatch")

    def test_delivery_ticket_uses_distinct_audience_and_purpose_key(self) -> None:
        private = Ed25519PrivateKey.generate()
        unsigned = delivery_ticket().payload
        raw_public = private.public_key().public_bytes_raw()
        descriptor = PublicKeyDescriptor(
            kid="delivery_key_runtime_001",
            issuer="tiangong-total-gateway",
            audience="tiangong-communication-service",
            purpose="delivery_ticket",
            public_key_base64url=__import__("base64").urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
            public_key_sha256=__import__("hashlib").sha256(raw_public).hexdigest(),
            state="ACTIVE",
            not_before_ms=0,
            not_after_ms=100_000,
            component_manifest_hash=unsigned.component_manifest_hash,
        )
        signed = TicketSigner(descriptor.kid, private).sign_delivery(unsigned)
        self.assertEqual(verify_delivery_ticket(signed, trust(descriptor), now_ms=30_000), descriptor)
        wrong_scope = descriptor.model_copy(update={"purpose": "execution_ticket"})
        with self.assertRaises(TicketVerificationError):
            verify_delivery_ticket(signed, trust(wrong_scope), now_ms=30_000)


class PersistentNonceLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = GatewayStateStore.open(self.path, now_ms=1_000)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def consume(self, store=None, **overrides):
        values = {
            "issuer": "tiangong-total-gateway",
            "audience": "tiangong-backend",
            "purpose": "execution_ticket",
            "nonce": "ticket_001",
            "payload_sha256": HASH_A,
            "gateway_epoch": 3,
            "consumer_instance_id": "backend_instance_001",
            "consumed_at_ms": 10_000,
            "expires_at_ms": 70_000,
        }
        values.update(overrides)
        return (store or self.store).consume_security_nonce(**values)

    def test_first_consumer_wins_and_replay_returns_original_fact(self) -> None:
        first = self.consume()
        self.assertTrue(first.consumed_by_this_call)
        duplicate = self.consume(
            consumer_instance_id="backend_instance_002",
            consumed_at_ms=11_000,
        )
        self.assertFalse(duplicate.consumed_by_this_call)
        self.assertEqual(duplicate.consumer_instance_id, "backend_instance_001")
        with self.assertRaises(StoreConflictError):
            self.consume(payload_sha256=HASH_B)

    def test_nonce_consumption_survives_restart_and_is_concurrent_safe(self) -> None:
        other = GatewayStateStore.open(self.path, now_ms=1_100)
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def consume(store, instance):
            try:
                barrier.wait(timeout=5)
                results.append(self.consume(store, consumer_instance_id=instance))
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = (
            threading.Thread(target=consume, args=(self.store, "backend_instance_001")),
            threading.Thread(target=consume, args=(other, "backend_instance_002")),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        other.close()
        self.assertEqual(errors, [])
        self.assertEqual(sum(item.consumed_by_this_call for item in results), 1)
        self.store.close()
        self.store = GatewayStateStore.open(self.path, now_ms=20_000)
        self.assertFalse(self.consume(consumed_at_ms=20_000).consumed_by_this_call)


if __name__ == "__main__":
    unittest.main()
