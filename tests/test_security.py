import base64
import hashlib
import json
import unittest

from pydantic import ValidationError

from contracts import (
    EmergencyKeyRevocationManifest,
    KeyRotationManifest,
    ProtectedPrivateKeyEnvelope,
    PublicKeyDescriptor,
    RedactedLogPayload,
    RedactionPolicy,
    ServiceAuthAssertion,
    ServiceAuthClaims,
    ServiceAuthHeader,
    ServiceAuthorizationError,
    TrustBundle,
    TrustScope,
    authorize_emergency_key_revocation_contract,
    authorize_key_rotation_contract,
    authorize_service_request_contract,
    default_redaction_policy,
    redact_log_payload,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
REQUEST_ID = "req_" + "1" * 64
EFFECT_ID = "eff_" + "2" * 64
ISSUER = "tiangong-total-gateway"
AUDIENCE = "tiangong-backend"


def public_key(kid, state="ACTIVE", byte=1, **overrides):
    raw = bytes([byte]) * 32
    values = {
        "kid": kid,
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "purpose": "service_auth",
        "public_key_base64url": base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="),
        "public_key_sha256": hashlib.sha256(raw).hexdigest(),
        "state": state,
        "not_before_ms": 0,
        "not_after_ms": 100_000,
        "component_manifest_hash": HASH_A,
    }
    if state == "REVOKED":
        values.update(
            {
                "revoked_at_ms": 50_000,
                "revocation_reason": "key.rotation_grace_ended",
            }
        )
    values.update(overrides)
    return PublicKeyDescriptor(**values)


def trust_bundle(keys, *, revision=1, generated_at_ms=10_000, **overrides):
    values = {
        "bundle_id": "trust_bundle_main",
        "revision": revision,
        "gateway_epoch": 3,
        "generated_at_ms": generated_at_ms,
        "required_scopes": (
            TrustScope(issuer=ISSUER, audience=AUDIENCE, purpose="service_auth"),
        ),
        "keys": tuple(sorted(keys, key=lambda item: (item.issuer, item.audience, item.purpose, item.kid))),
        "production_ready": True,
        "bundle_sha256": HASH_C,
    }
    values.update(overrides)
    return TrustBundle(**values).with_computed_sha256()


def service_assertion(kid="key_old", **claim_overrides):
    claims = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "subject_instance_id": "gateway_instance_001",
        "issued_at_ms": 10_000,
        "not_before_ms": 10_000,
        "expires_at_ms": 40_000,
        "gateway_epoch": 3,
        "request_nonce": "nonce_001",
        "method": "POST",
        "path": "/internal/v1/effects/claim",
        "body_sha256": HASH_B,
        "component_manifest_hash": HASH_A,
        "request_id": REQUEST_ID,
        "effect_id": EFFECT_ID,
    }
    claims.update(claim_overrides)
    return ServiceAuthAssertion(
        header=ServiceAuthHeader(kid=kid),
        claims=ServiceAuthClaims(**claims),
        signature="A" * 86,
    )


def authorize(assertion, bundle, **overrides):
    arguments = {
        "signature_verified": True,
        "nonce_registered": True,
        "now_ms": 20_000,
        "expected_gateway_epoch": 3,
        "expected_issuer": ISSUER,
        "expected_audience": AUDIENCE,
        "expected_method": "POST",
        "expected_path": "/internal/v1/effects/claim",
        "expected_body_sha256": HASH_B,
        "expected_component_manifest_hash": HASH_A,
    }
    arguments.update(overrides)
    return authorize_service_request_contract(assertion, bundle, **arguments)


class ServiceAuthenticationTests(unittest.TestCase):
    def test_authorizes_exact_signed_nonce_bound_request(self) -> None:
        bundle = trust_bundle((public_key("key_old"),))
        claims = authorize(service_assertion(), bundle)
        self.assertEqual(claims.request_id, REQUEST_ID)
        self.assertEqual(claims.effect_id, EFFECT_ID)

    def test_rejects_unverified_unregistered_or_swapped_request(self) -> None:
        bundle = trust_bundle((public_key("key_old"),))
        cases = (
            ({"signature_verified": False}, "service_auth.signature.unverified"),
            ({"nonce_registered": False}, "service_auth.nonce.unregistered"),
            ({"expected_body_sha256": HASH_C}, "service_auth.body.mismatch"),
            ({"expected_audience": "tiangong-communication-service"}, "service_auth.principal.mismatch"),
        )
        for arguments, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ServiceAuthorizationError) as caught:
                    authorize(service_assertion(), bundle, **arguments)
                self.assertEqual(caught.exception.code, expected)

    def test_next_or_revoked_key_cannot_authenticate(self) -> None:
        active = public_key("key_old", "ACTIVE", 1)
        next_key = public_key("key_next", "NEXT", 2)
        bundle = trust_bundle((active, next_key))
        with self.assertRaises(ServiceAuthorizationError) as caught:
            authorize(service_assertion(kid="key_next"), bundle)
        self.assertEqual(caught.exception.code, "service_auth.key.state_rejected")

        revoked = public_key("key_revoked", "REVOKED", 3, revoked_at_ms=10_000)
        bundle = trust_bundle((active, revoked))
        with self.assertRaises(ServiceAuthorizationError) as caught:
            authorize(service_assertion(kid="key_revoked"), bundle)
        self.assertEqual(caught.exception.code, "service_auth.key.state_rejected")

    def test_path_and_lifetime_are_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            service_assertion(path="/internal/../admin")
        with self.assertRaises(ValidationError):
            service_assertion(expires_at_ms=40_001)
        bundle = trust_bundle((public_key("key_old"),))
        with self.assertRaises(ServiceAuthorizationError) as caught:
            authorize(service_assertion(), bundle, clock_skew_ms=5_001)
        self.assertEqual(caught.exception.code, "service_auth.clock_skew.invalid")


class DpapiEnvelopeTests(unittest.TestCase):
    def envelope(self, **overrides):
        values = {
            "envelope_id": "private_key_envelope_001",
            "kid": "key_old",
            "purpose": "service_auth",
            "audience": AUDIENCE,
            "additional_entropy_sha256": HASH_A,
            "encrypted_blob_sha256": HASH_A,
            "encrypted_blob_bytes": 256,
            "storage_relative_path": "keys/service_auth/key_old.dpapi",
            "owner_sid_sha256": HASH_B,
            "acl_sha256": HASH_C,
            "created_at_ms": 10_000,
            "envelope_sha256": HASH_A,
        }
        values.update(overrides)
        return ProtectedPrivateKeyEnvelope(**values).with_computed_sha256()

    def test_metadata_requires_current_user_dpapi_and_no_plaintext(self) -> None:
        envelope = self.envelope()
        self.assertTrue(envelope.has_valid_sha256())
        self.assertEqual(envelope.protection_scope, "CurrentUser")
        self.assertFalse(envelope.plaintext_present)
        invalid = (
            {"storage_relative_path": "C:/keys/key.dpapi"},
            {"storage_relative_path": "keys\\key.dpapi"},
            {"storage_relative_path": "keys/../key.dpapi"},
            {"plaintext_present": True},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    self.envelope(**values)


class KeyRotationTests(unittest.TestCase):
    def manifest(self, phase, before, after, **overrides):
        values = {
            "rotation_id": f"rotation_{phase.lower()}_001",
            "phase": phase,
            "issuer": ISSUER,
            "audience": AUDIENCE,
            "purpose": "service_auth",
            "gateway_epoch": 3,
            "old_kid": "key_old",
            "new_kid": "key_new",
            "signer_kid": "key_old",
            "prepared_at_ms": 1_000,
            "effective_at_ms": 10_000,
            "grace_until_ms": 20_000,
            "before_bundle_sha256": before.bundle_sha256,
            "after_bundle_sha256": after.bundle_sha256,
            "component_manifest_hash": HASH_A,
            "signature": "B" * 86,
            "manifest_sha256": HASH_C,
        }
        values.update(overrides)
        return KeyRotationManifest(**values).with_computed_sha256()

    def rotation_line(self):
        old_active = public_key("key_old", "ACTIVE", 1)
        new_next = public_key("key_new", "NEXT", 2)
        before = trust_bundle((old_active,), revision=1, generated_at_ms=1_000)
        prepared = trust_bundle((old_active, new_next), revision=2, generated_at_ms=2_000)
        old_previous = public_key("key_old", "PREVIOUS", 1)
        new_active = public_key("key_new", "ACTIVE", 2)
        activated = trust_bundle((old_previous, new_active), revision=3, generated_at_ms=10_000)
        old_revoked = public_key("key_old", "REVOKED", 1, revoked_at_ms=20_000)
        retired = trust_bundle((old_revoked, new_active), revision=4, generated_at_ms=20_000)
        return before, prepared, activated, retired

    def test_next_active_previous_revoked_rotation_line(self) -> None:
        before, prepared, activated, retired = self.rotation_line()
        phases = (
            ("PREPARE", before, prepared, 1_000),
            ("ACTIVATE", prepared, activated, 10_000),
            ("RETIRE", activated, retired, 20_000),
        )
        for phase, source, target, now in phases:
            with self.subTest(phase=phase):
                manifest = self.manifest(phase, source, target)
                self.assertIs(
                    authorize_key_rotation_contract(
                        manifest,
                        source,
                        target,
                        signature_verified=True,
                        now_ms=now,
                    ),
                    target,
                )

    def test_rotation_rejects_key_swap_or_unverified_signature(self) -> None:
        _, prepared, activated, _ = self.rotation_line()
        manifest = self.manifest("ACTIVATE", prepared, activated)
        with self.assertRaises(ServiceAuthorizationError) as caught:
            authorize_key_rotation_contract(
                manifest,
                prepared,
                activated,
                signature_verified=False,
                now_ms=10_000,
            )
        self.assertEqual(caught.exception.code, "key_rotation.signature.unverified")

        swapped_new = public_key("key_new", "ACTIVE", 9)
        swapped = trust_bundle(
            (public_key("key_old", "PREVIOUS", 1), swapped_new),
            revision=3,
            generated_at_ms=10_000,
        )
        forged_manifest = self.manifest("ACTIVATE", prepared, swapped)
        with self.assertRaises(ServiceAuthorizationError) as caught:
            authorize_key_rotation_contract(
                forged_manifest,
                prepared,
                swapped,
                signature_verified=True,
                now_ms=10_000,
            )
        self.assertEqual(caught.exception.code, "key_rotation.key_identity.changed")


class EmergencyKeyRevocationTests(unittest.TestCase):
    def transition(self):
        compromised = public_key("key_old", "ACTIVE", 1)
        replacement_next = public_key("key_new", "NEXT", 2)
        before = trust_bundle(
            (compromised, replacement_next),
            revision=1,
            generated_at_ms=4_000,
            gateway_epoch=3,
        )
        compromised_revoked = public_key(
            "key_old",
            "REVOKED",
            1,
            revoked_at_ms=5_000,
            revocation_reason="key.compromised",
        )
        replacement_active = public_key("key_new", "ACTIVE", 2)
        after = trust_bundle(
            (compromised_revoked, replacement_active),
            revision=2,
            generated_at_ms=5_000,
            gateway_epoch=4,
        )
        return before, after

    def manifest(self, before, after, **overrides):
        values = {
            "incident_id": "incident_001",
            "issuer": ISSUER,
            "audience": AUDIENCE,
            "purpose": "service_auth",
            "compromised_kid": "key_old",
            "replacement_kid": "key_new",
            "recovery_signer_kid": "offline_recovery_001",
            "previous_gateway_epoch": 3,
            "new_gateway_epoch": 4,
            "detected_at_ms": 4_000,
            "effective_at_ms": 5_000,
            "before_bundle_sha256": before.bundle_sha256,
            "after_bundle_sha256": after.bundle_sha256,
            "component_manifest_hash": HASH_A,
            "recovery_signature": "C" * 86,
            "manifest_sha256": HASH_C,
        }
        values.update(overrides)
        return EmergencyKeyRevocationManifest(**values).with_computed_sha256()

    def authorize(self, manifest, before, after, **overrides):
        arguments = {
            "recovery_signature_verified": True,
            "expected_recovery_signer_kid": "offline_recovery_001",
            "now_ms": 5_000,
        }
        arguments.update(overrides)
        return authorize_emergency_key_revocation_contract(
            manifest,
            before,
            after,
            **arguments,
        )

    def test_offline_recovery_revokes_key_activates_replacement_and_bumps_epoch(self) -> None:
        before, after = self.transition()
        manifest = self.manifest(before, after)
        self.assertIs(self.authorize(manifest, before, after), after)

    def test_rejects_wrong_recovery_signer_or_unverified_signature(self) -> None:
        before, after = self.transition()
        manifest = self.manifest(before, after)
        cases = (
            (
                {"recovery_signature_verified": False},
                "emergency_revocation.recovery_signature.unverified",
            ),
            (
                {"expected_recovery_signer_kid": "offline_recovery_999"},
                "emergency_revocation.recovery_signer.mismatch",
            ),
        )
        for arguments, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ServiceAuthorizationError) as caught:
                    self.authorize(manifest, before, after, **arguments)
                self.assertEqual(caught.exception.code, expected)

    def test_rejects_inexact_revocation_time(self) -> None:
        before, _ = self.transition()
        compromised_revoked = public_key(
            "key_old",
            "REVOKED",
            1,
            revoked_at_ms=4_500,
            revocation_reason="key.compromised",
        )
        after = trust_bundle(
            (compromised_revoked, public_key("key_new", "ACTIVE", 2)),
            revision=2,
            generated_at_ms=5_000,
            gateway_epoch=4,
        )
        manifest = self.manifest(before, after)
        with self.assertRaises(ServiceAuthorizationError) as caught:
            self.authorize(manifest, before, after)
        self.assertEqual(
            caught.exception.code,
            "emergency_revocation.revocation_time.mismatch",
        )

    def test_rejects_unrelated_key_change_hidden_in_bundle(self) -> None:
        before, after = self.transition()
        retired_before = public_key(
            "key_retired",
            "REVOKED",
            3,
            revoked_at_ms=1_000,
        )
        retired_after = public_key(
            "key_retired",
            "REVOKED",
            9,
            revoked_at_ms=1_000,
        )
        before = trust_bundle(
            (*before.keys, retired_before),
            revision=1,
            generated_at_ms=4_000,
            gateway_epoch=3,
        )
        after = trust_bundle(
            (*after.keys, retired_after),
            revision=2,
            generated_at_ms=5_000,
            gateway_epoch=4,
        )
        manifest = self.manifest(before, after)
        with self.assertRaises(ServiceAuthorizationError) as caught:
            self.authorize(manifest, before, after)
        self.assertEqual(
            caught.exception.code,
            "emergency_revocation.unrelated_key.changed",
        )


class RedactionTests(unittest.TestCase):
    def policy(self):
        return default_redaction_policy()

    def test_redacts_sensitive_keys_bearer_jwt_and_truncates_before_logging(self) -> None:
        payload = {
            "request_id": REQUEST_ID,
            "Authorization": "Bearer top-secret-token-value",
            "nested": {
                "provider_api_key": "sk-very-secret",
                "note": "received Bearer another-secret-token",
            },
            "long_text": "x" * 5_000,
        }
        record = redact_log_payload(
            payload,
            self.policy(),
            event_id="log_event_001",
            source_component_id="tiangong-total-gateway",
            observed_at_ms=10_000,
        )
        self.assertNotIn("top-secret", record.redacted_payload_json)
        self.assertNotIn("another-secret", record.redacted_payload_json)
        self.assertNotIn("sk-very-secret", record.redacted_payload_json)
        decoded = json.loads(record.redacted_payload_json)
        self.assertEqual(decoded["request_id"], REQUEST_ID)
        self.assertTrue(decoded["long_text"].endswith("...[TRUNCATED]"))
        self.assertEqual(
            record.redacted_paths,
            ("/Authorization", "/nested/note", "/nested/provider_api_key"),
        )

    def test_rejects_floats_and_tampered_redacted_payload(self) -> None:
        with self.assertRaises(TypeError):
            redact_log_payload(
                {"latency": 1.25},
                self.policy(),
                event_id="log_event_001",
                source_component_id="tiangong-total-gateway",
                observed_at_ms=10_000,
            )
        record = redact_log_payload(
            {"ok": True},
            self.policy(),
            event_id="log_event_002",
            source_component_id="tiangong-total-gateway",
            observed_at_ms=10_000,
        )
        with self.assertRaises(ValidationError):
            RedactedLogPayload(
                **{
                    **record.model_dump(),
                    "redacted_payload_json": '{"ok":false}',
                }
            )


if __name__ == "__main__":
    unittest.main()
