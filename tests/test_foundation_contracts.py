import unittest

from pydantic import ValidationError

from contracts import (
    AttachmentRef,
    InboundEnvelope,
    LifeSnapshot,
    SkillCandidate,
    SkillSelectionRecord,
    contract_schema_bundle,
    contract_schema_bundle_sha256,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
REQUEST_ID = "req_" + "1" * 64
RUN_ID = "run_" + "2" * 64


def accepted_attachment(**overrides):
    values = {
        "object_id": "attachment_001",
        "revision": 1,
        "sha256": HASH_A,
        "size_bytes": 1024,
        "mime": "application/pdf",
        "filename": "report.pdf",
        "tenant_id": "tenant_001",
        "link_account_id": "wechat_001",
        "conversation_scope_hash": HASH_B,
        "source_message_ref": "message_001",
        "created_at_ms": 1_784_010_685_000,
    }
    values.update(overrides)
    return AttachmentRef(**values)


class AttachmentRefTests(unittest.TestCase):
    def test_accepts_content_addressed_file(self) -> None:
        attachment = accepted_attachment()
        self.assertEqual(attachment.acceptance, "accepted")
        self.assertTrue(attachment.magic_verified)

    def test_rejects_path_or_reserved_windows_name(self) -> None:
        for filename in ("../report.pdf", "folder\\report.pdf", "CON.txt", "bad?.txt"):
            with self.subTest(filename=filename), self.assertRaises(ValidationError):
                accepted_attachment(filename=filename)

    def test_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            accepted_attachment(host_path="C:\\secret.txt")


class InboundEnvelopeTests(unittest.TestCase):
    def test_requires_text_or_attachment(self) -> None:
        with self.assertRaises(ValidationError):
            InboundEnvelope(
                inbound_id="inbound_001",
                channel="wechat",
                tenant_id="tenant_001",
                link_account_id="wechat_001",
                conversation_ref="conversation_001",
                conversation_scope_hash=HASH_B,
                principal_scope_hash=HASH_A,
                message_scope_hash=HASH_C,
                channel_message_ref="message_001",
                sender_ref="sender_001",
                received_at_ms=1_784_010_685_000,
                idempotency_key=HASH_A,
                channel_metadata_hash=HASH_C,
            )

    def test_binds_attachment_to_same_scope(self) -> None:
        with self.assertRaises(ValidationError):
            InboundEnvelope(
                inbound_id="inbound_001",
                channel="wechat",
                tenant_id="tenant_001",
                link_account_id="wechat_001",
                conversation_ref="conversation_001",
                conversation_scope_hash=HASH_B,
                principal_scope_hash=HASH_A,
                message_scope_hash=HASH_C,
                channel_message_ref="message_001",
                sender_ref="sender_001",
                received_at_ms=1_784_010_685_000,
                idempotency_key=HASH_A,
                channel_metadata_hash=HASH_C,
                attachments=(accepted_attachment(tenant_id="tenant_002"),),
            )

    def test_accepts_mixed_message(self) -> None:
        envelope = InboundEnvelope(
            inbound_id="inbound_001",
            channel="wechat",
            tenant_id="tenant_001",
            link_account_id="wechat_001",
            conversation_ref="conversation_001",
            conversation_scope_hash=HASH_B,
            principal_scope_hash=HASH_A,
            message_scope_hash=HASH_C,
            channel_message_ref="message_001",
            sender_ref="sender_001",
            received_at_ms=1_784_010_685_000,
            idempotency_key=HASH_A,
            channel_metadata_hash=HASH_C,
            text="请审查这个文件",
            attachments=(accepted_attachment(),),
        )
        self.assertEqual(len(envelope.attachments), 1)


class LifeSnapshotTests(unittest.TestCase):
    def test_preserves_persona_and_user_identity_separately(self) -> None:
        snapshot = LifeSnapshot(
            snapshot_id="life_snapshot_001",
            revision=3,
            sha256=HASH_A,
            created_at_ms=1_784_010_685_000,
            identity_ref="life_ip_001",
            identity_revision=2,
            persona_name="起源",
            persona_avatar_ref="avatar_persona_001",
            persona_voice_ref="voice_persona_001",
            user_callsign="老板",
            user_avatar_ref="avatar_user_001",
            user_occupation="产品负责人",
            compiled_context_object_id="life_context_001",
            compiled_context_sha256=HASH_B,
            soul_sha256=HASH_C,
            memory_revision=7,
            affect_revision=4,
            capability_profile_hash=HASH_A,
        )
        self.assertEqual(snapshot.persona_name, "起源")
        self.assertEqual(snapshot.user_callsign, "老板")


class SkillSelectionTests(unittest.TestCase):
    def candidate(self, **overrides):
        values = {
            "skill_id": "word_delivery",
            "version": "3.0.0",
            "sha256": HASH_A,
            "source_ref": "skill_source_001",
            "score_millis": 920,
            "required_actions": ("docx.create", "qc.docx.delivery_check"),
            "missing_actions": (),
            "incompatible_reasons": (),
            "compatible": True,
        }
        values.update(overrides)
        return SkillCandidate(**values)

    def test_model_can_activate_only_resolved_compatible_skill(self) -> None:
        record = SkillSelectionRecord(
            selection_id="selection_001",
            request_id=REQUEST_ID,
            run_id=RUN_ID,
            generation=1,
            origin="model_request",
            operation="skill.read",
            query_hash=HASH_B,
            skill_catalog_hash=HASH_A,
            capability_manifest_hash=HASH_C,
            candidates=(self.candidate(),),
            decision="activate",
            selected_skill_id="word_delivery",
            selected_skill_version="3.0.0",
            selected_skill_sha256=HASH_A,
            activation_state="active",
            resolved_via="skill.read",
            reason_code="model.selected.compatible",
            decided_at_ms=1_784_010_685_000,
        )
        self.assertEqual(record.origin, "model_request")

    def test_rejects_activation_with_missing_action(self) -> None:
        candidate = self.candidate(
            missing_actions=("qc.docx.delivery_check",),
            incompatible_reasons=("action.missing",),
            compatible=False,
        )
        with self.assertRaises(ValidationError):
            SkillSelectionRecord(
                selection_id="selection_001",
                request_id=REQUEST_ID,
                run_id=RUN_ID,
                generation=1,
                origin="model_request",
                operation="skill.read",
                skill_catalog_hash=HASH_A,
                capability_manifest_hash=HASH_C,
                candidates=(candidate,),
                decision="activate",
                selected_skill_id="word_delivery",
                selected_skill_version="3.0.0",
                selected_skill_sha256=HASH_A,
                activation_state="active",
                resolved_via="skill.read",
                reason_code="model.selected.incompatible",
                decided_at_ms=1_784_010_685_000,
            )

    def test_rejects_claimed_read_when_only_route_was_called(self) -> None:
        with self.assertRaises(ValidationError):
            SkillSelectionRecord(
                selection_id="selection_001",
                request_id=REQUEST_ID,
                run_id=RUN_ID,
                generation=1,
                origin="model_request",
                operation="skill.route",
                skill_catalog_hash=HASH_A,
                capability_manifest_hash=HASH_C,
                candidates=(self.candidate(),),
                decision="activate",
                selected_skill_id="word_delivery",
                selected_skill_version="3.0.0",
                selected_skill_sha256=HASH_A,
                activation_state="active",
                resolved_via="skill.read",
                reason_code="model.claimed.unverified",
                decided_at_ms=1_784_010_685_000,
            )


class SchemaBundleTests(unittest.TestCase):
    def test_bundle_contains_only_root_contracts_with_ids(self) -> None:
        bundle = contract_schema_bundle()
        self.assertEqual(
            list(bundle),
            [
                "AcceptancePredicate",
                "ActionCandidate",
                "ActionImpact",
                "ActionIntent",
                "ActionPermission",
                "ActionRegistrySnapshot",
                "AffectExpressionCase",
                "AffectExpressionSelection",
                "AffectIntakeReceipt",
                "AffectSignal",
                "AffectSourcePolicySnapshot",
                "AffectiveStateV3",
                "AgencyDecision",
                "AggregateStatus",
                "AppraisalVectorV3",
                "ArtifactManifest",
                "ArtifactRevisionIdentity",
                "AttachmentRef",
                "AutonomyPolicySnapshot",
                "AutonomyUsageSnapshot",
                "CapabilityEvidence",
                "CapabilityLearningDecision",
                "CapabilityManifest",
                "CapabilityProfile",
                "CapabilityRollbackRecord",
                "CausalContextPack",
                "CausalEpisode",
                "CausalHypothesis",
                "CausalNodeV3",
                "ChannelAckPermit",
                "ChannelCutoverSnapshot",
                "ChannelDrainEvidence",
                "ChannelOwnershipLease",
                "CircuitBreakerPolicy",
                "CircuitBreakerSnapshot",
                "CircuitPermission",
                "CircuitUpdate",
                "ComponentManifest",
                "ComponentReadinessEvidence",
                "CompositionExecutionBindingV1",
                "ContextTokenBudget",
                "DeliveryIdentity",
                "DeliveryReceipt",
                "DeliveryTicket",
                "DynamicTimeoutPolicy",
                "EffectIdentity",
                "EmergencyKeyRevocationManifest",
                "EpisodeOutcomeEvidence",
                "ErrorDescriptor",
                "ExecutionResult",
                "ExecutionTicket",
                "FactRecord",
                "FenceDecision",
                "GenerationFence",
                "InboundEnvelope",
                "InboundScope",
                "InboundScopeKeys",
                "KeyRotationManifest",
                "LifeContextAuthorization",
                "LifeEventEnvelope",
                "LifeEventIngress",
                "LifeEventIngressReceipt",
                "LifeRevisionVector",
                "LifeSnapshot",
                "MemoryAssertionV3",
                "MemoryRelationV3",
                "OmniCapabilityGrant",
                "OutboundPlan",
                "OutboundScope",
                "OutboundScopeKeys",
                "PolicyDecision",
                "PrivacyDeletionTombstone",
                "ProductionInboundAcceptance",
                "ProductionInboundSubmission",
                "ProtectedPrivateKeyEnvelope",
                "PublicKeyDescriptor",
                "ReadinessDecision",
                "ReadinessExpectation",
                "RedactedLogPayload",
                "RedactionPolicy",
                "ReflectionCard",
                "ReflectionQuestionDecision",
                "RegistrySnapshot",
                "ReleaseManifest",
                "RequestIdentity",
                "RetryDecision",
                "RetryPolicy",
                "RunIdentity",
                "RuntimeCloseoutEvidence",
                "ServiceAuthAssertion",
                "ShadowComparison",
                "ShadowDecisionObservation",
                "ShadowIngressCopy",
                "ShadowObservationBatch",
                "SkillActivationGrant",
                "SkillSelectionRecord",
                "StateSnapshot",
                "TaskContinuityCapsule",
                "TimeoutDecision",
                "TransitionDecision",
                "TransitionEvent",
                "TrustBundle",
                "UserConfirmationGrant",
                "VerificationPlan",
                "VerificationPlanEntryV2",
                "VerificationReadiness",
                "VerificationRecord",
                "VerifierDescriptor",
                "ViabilityObservation",
                "ViabilityState",
                "WriteEvidenceV2",
            ],
        )
        for name, schema in bundle.items():
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertTrue(schema["$id"].endswith(name))
            self.assertFalse(schema["additionalProperties"])

    def test_bundle_digest_is_lowercase_sha256(self) -> None:
        digest = contract_schema_bundle_sha256()
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, digest.lower())


if __name__ == "__main__":
    unittest.main()
