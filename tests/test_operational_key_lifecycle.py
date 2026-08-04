from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from contracts import (
    EmergencyKeyRevocationManifest,
    KeyRotationManifest,
    PublicKeyDescriptor,
    TrustBundle,
    TrustScope,
)
from total_gateway.key_lifecycle import (
    OperationalTrustError,
    OperationalTrustStore,
    trust_manifest_signing_input,
)


HASH_A = "a" * 64
ISSUER = "tiangong-total-gateway"
AUDIENCE = "tiangong-backend"
PURPOSE = "execution_ticket"


def descriptor(private, kid, state="ACTIVE", **overrides):
    raw = private.public_key().public_bytes_raw()
    values = {
        "kid": kid,
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "purpose": PURPOSE,
        "public_key_base64url": base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
        "public_key_sha256": hashlib.sha256(raw).hexdigest(),
        "state": state,
        "not_before_ms": 0,
        "not_after_ms": 100_000,
        "component_manifest_hash": HASH_A,
    }
    if state == "REVOKED":
        values.update(
            {
                "revoked_at_ms": 20_000,
                "revocation_reason": "key.rotation_grace_ended",
            }
        )
    values.update(overrides)
    return PublicKeyDescriptor(**values)


def bundle(keys, *, revision, epoch=3, generated_at_ms=1_000):
    return TrustBundle(
        bundle_id="trust_execution_main",
        revision=revision,
        gateway_epoch=epoch,
        generated_at_ms=generated_at_ms,
        required_scopes=(
            TrustScope(issuer=ISSUER, audience=AUDIENCE, purpose=PURPOSE),
        ),
        keys=tuple(sorted(keys, key=lambda item: (item.issuer, item.audience, item.purpose, item.kid))),
        production_ready=True,
        bundle_sha256="0" * 64,
    ).with_computed_sha256()


def sign_rotation(private, phase, before, after):
    manifest = KeyRotationManifest(
        rotation_id="rotation_" + phase.casefold(),
        phase=phase,
        issuer=ISSUER,
        audience=AUDIENCE,
        purpose=PURPOSE,
        gateway_epoch=3,
        old_kid="key_old",
        new_kid="key_new",
        signer_kid="key_old",
        prepared_at_ms=1_000,
        effective_at_ms=10_000,
        grace_until_ms=20_000,
        before_bundle_sha256=before.bundle_sha256,
        after_bundle_sha256=after.bundle_sha256,
        component_manifest_hash=HASH_A,
        signature="A" * 86,
        manifest_sha256="0" * 64,
    ).with_computed_sha256()
    signature = base64.urlsafe_b64encode(
        private.sign(trust_manifest_signing_input(manifest))
    ).rstrip(b"=").decode("ascii")
    return manifest.model_copy(update={"signature": signature})


def sign_emergency(recovery_private, before, after):
    manifest = EmergencyKeyRevocationManifest(
        incident_id="incident_compromised_execution_key",
        issuer=ISSUER,
        audience=AUDIENCE,
        purpose=PURPOSE,
        compromised_kid="key_old",
        replacement_kid="key_new",
        recovery_signer_kid="offline_recovery",
        previous_gateway_epoch=3,
        new_gateway_epoch=4,
        detected_at_ms=4_000,
        effective_at_ms=5_000,
        before_bundle_sha256=before.bundle_sha256,
        after_bundle_sha256=after.bundle_sha256,
        component_manifest_hash=HASH_A,
        recovery_signature="A" * 86,
        manifest_sha256="0" * 64,
    ).with_computed_sha256()
    signature = base64.urlsafe_b64encode(
        recovery_private.sign(trust_manifest_signing_input(manifest))
    ).rstrip(b"=").decode("ascii")
    return manifest.model_copy(update={"recovery_signature": signature})


class OperationalKeyLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "authority" / "execution-trust.json"
        self.old_private = Ed25519PrivateKey.generate()
        self.new_private = Ed25519PrivateKey.generate()
        self.recovery_private = Ed25519PrivateKey.generate()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def rotation_line(self):
        old_active = descriptor(self.old_private, "key_old", "ACTIVE")
        new_next = descriptor(self.new_private, "key_new", "NEXT")
        initial = bundle((old_active,), revision=1)
        prepared = bundle((old_active, new_next), revision=2, generated_at_ms=2_000)
        activated = bundle(
            (
                descriptor(self.old_private, "key_old", "PREVIOUS"),
                descriptor(self.new_private, "key_new", "ACTIVE"),
            ),
            revision=3,
            generated_at_ms=10_000,
        )
        retired = bundle(
            (
                descriptor(
                    self.old_private,
                    "key_old",
                    "REVOKED",
                    revoked_at_ms=20_000,
                ),
                descriptor(self.new_private, "key_new", "ACTIVE"),
            ),
            revision=4,
            generated_at_ms=20_000,
        )
        return initial, prepared, activated, retired

    def test_prepare_activate_retire_is_durable_and_ordered(self) -> None:
        initial, prepared, activated, retired = self.rotation_line()
        store = OperationalTrustStore.open(
            self.path,
            initial_bundle=initial,
            now_ms=1_000,
        )
        transitions = (
            ("PREPARE", initial, prepared, 1_000),
            ("ACTIVATE", prepared, activated, 10_000),
            ("RETIRE", activated, retired, 20_000),
        )
        for phase, before, after, now_ms in transitions:
            with self.subTest(phase=phase):
                result = store.apply_rotation(
                    sign_rotation(self.old_private, phase, before, after),
                    after,
                    now_ms=now_ms,
                )
                self.assertEqual(result.bundle_sha256, after.bundle_sha256)
                reopened = OperationalTrustStore.open(
                    self.path,
                    initial_bundle=after,
                    now_ms=now_ms,
                )
                self.assertEqual(reopened.current_bundle().bundle_sha256, after.bundle_sha256)

    def test_wrong_signature_replay_and_state_tamper_are_rejected(self) -> None:
        initial, prepared, _, _ = self.rotation_line()
        store = OperationalTrustStore.open(
            self.path,
            initial_bundle=initial,
            now_ms=1_000,
        )
        manifest = sign_rotation(self.old_private, "PREPARE", initial, prepared)
        forged = manifest.model_copy(update={"signature": "A" * 86})
        with self.assertRaisesRegex(OperationalTrustError, "signature is invalid"):
            store.apply_rotation(forged, prepared, now_ms=1_000)
        store.apply_rotation(manifest, prepared, now_ms=1_000)
        with self.assertRaises(Exception):
            store.apply_rotation(manifest, prepared, now_ms=1_000)

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw["current_bundle"]["gateway_epoch"] = 99
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(OperationalTrustError, "integrity is invalid"):
            store.current_bundle()

    def test_emergency_revocation_uses_offline_key_and_bumps_epoch_once(self) -> None:
        compromised = descriptor(self.old_private, "key_old", "ACTIVE")
        replacement_next = descriptor(self.new_private, "key_new", "NEXT")
        before = bundle((compromised, replacement_next), revision=1, epoch=3, generated_at_ms=4_000)
        after = bundle(
            (
                descriptor(
                    self.old_private,
                    "key_old",
                    "REVOKED",
                    revoked_at_ms=5_000,
                    revocation_reason="key.compromised",
                ),
                descriptor(self.new_private, "key_new", "ACTIVE"),
            ),
            revision=2,
            epoch=4,
            generated_at_ms=5_000,
        )
        recovery = descriptor(
            self.recovery_private,
            "offline_recovery",
            "ACTIVE",
            issuer="tiangong-offline-recovery",
            audience="tiangong-total-gateway",
        )
        store = OperationalTrustStore.open(
            self.path,
            initial_bundle=before,
            now_ms=4_000,
            recovery_key=recovery,
        )
        result = store.apply_emergency_revocation(
            sign_emergency(self.recovery_private, before, after),
            after,
            now_ms=5_000,
        )
        self.assertEqual(result.gateway_epoch, 4)
        self.assertEqual(next(key for key in result.keys if key.kid == "key_old").state, "REVOKED")


if __name__ == "__main__":
    unittest.main()
