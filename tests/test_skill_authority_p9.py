from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from contracts import InboundEnvelope, InboundScope, derive_inbound_scope_keys
from total_gateway.active_requests import ActiveRequestActivator
from total_gateway.backend_client import BackendClient
from total_gateway.fact_ledger import FactLedger
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.skill_api import SkillApiError, SkillInternalApiRouter
from total_gateway.skill_authority import SkillAuthority, SkillAuthorityError
from total_gateway.skill_selection import SkillCatalog, SkillDefinition, SkillSelectionService
from total_gateway.store import GatewayStateStore, StoreConflictError
from tests.test_backend_client import FakeBackendTransport, backend_response, signed_ticket
from tests.test_execution_contracts import capability_manifest
from tests.test_skill_selection import manifest as routing_manifest


ROOT = Path(__file__).resolve().parents[1]
ROUTER = (
    ROOT
    / "app"
    / "backend"
    / "tiangong-backend"
    / "_internal"
    / "omni_body_skill"
    / "tools"
    / "skill_router.py"
)
HASH_A = "a" * 64


def envelope() -> InboundEnvelope:
    scope = InboundScope(
        channel="desktop",
        tenant_id="tenant_p9",
        link_account_id="account_p9",
        conversation_ref="conversation_p9",
        channel_message_ref="message_p9",
        sender_ref="sender_p9",
    )
    keys = derive_inbound_scope_keys(scope)
    return InboundEnvelope(
        inbound_id="inbound_p9",
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
        channel_metadata_hash=HASH_A,
        text="创建 DOCX 文档",
    )


def skill_catalog() -> SkillCatalog:
    content = "# Word Skill\n必须调用 docx.create 并以机器事实收口。\n"
    definition = SkillDefinition(
        skill_id="skill_word_p9",
        version="1.0.0",
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        source_ref="skill_source_p9",
        title="Word 文档",
        summary="DOCX",
        category="document",
        keywords=("docx", "文档"),
        task_intents=("word 文档",),
        required_actions=("docx.create",),
        content=content,
    )
    return SkillCatalog((definition,))


def load_router():
    spec = importlib.util.spec_from_file_location("p9_thin_skill_router", ROUTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillAuthorityP9Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = GatewayStateStore.open(root / "gateway.sqlite3", now_ms=900)
        self.objects = ContentAddressedObjectStore.open(root / "objects", now_ms=900)
        self.facts = FactLedger.open(root / "facts.sqlite3", self.objects, now_ms=900)
        self.inbound = envelope()
        self.registration = self.store.register_request(
            self.inbound,
            ingress_sha256=HASH_A,
            created_at_ms=1_100,
        )
        activator = ActiveRequestActivator(
            self.store,
            gateway_epoch=3,
            owner_instance_id="gateway-p9",
            lease_duration_ms=60_000,
        )
        self.active = activator.claim_next(now_ms=1_200)
        assert self.active is not None
        self.manifest = capability_manifest()
        self.authority = SkillAuthority(
            SkillSelectionService(skill_catalog()),
            self.manifest,
            self.store,
            self.facts,
        )

    def tearDown(self) -> None:
        self.facts.close()
        self.objects.close()
        self.store.close()
        self.temporary.cleanup()

    @property
    def scope(self) -> dict[str, object]:
        return {
            "request_id": self.active.entry.request_id,
            "run_id": self.active.generation.run_id,
            "generation": self.active.generation.generation,
        }

    def test_system_and_model_channels_share_exact_catalog_candidates_and_persist(self) -> None:
        system = self.authority.system_recommend(
            "创建 DOCX 文档",
            **self.scope,
            decided_at_ms=1_300,
        )
        model = self.authority.model_request(
            "skill.route",
            **self.scope,
            principal_scope_hash=self.inbound.principal_scope_hash,
            decided_at_ms=1_301,
            query="创建 DOCX 文档",
        )
        self.assertEqual(system.skill_catalog_hash, self.authority.catalog_sha256)
        self.assertEqual(model.resolution.record.skill_catalog_hash, self.authority.catalog_sha256)
        self.assertEqual(system.candidates, model.resolution.record.candidates)
        records = self.store.list_skill_selections(**self.scope)
        self.assertEqual(len(records), 2)
        self.assertEqual({item.record.origin for item in records}, {"system_recommendation", "model_request"})

    def test_activation_is_selection_bound_and_fact_ledger_alone_controls_completion(self) -> None:
        resolved = self.authority.model_request(
            "skill.get",
            **self.scope,
            principal_scope_hash=self.inbound.principal_scope_hash,
            decided_at_ms=1_400,
            skill_id="skill_word_p9",
        )
        grant = resolved.activation
        assert grant is not None
        empty = self.authority.step_check(
            **self.scope,
            principal_scope_hash=self.inbound.principal_scope_hash,
            skill_id=grant.skill_id,
            activation_sha256=grant.activation_sha256,
            checked_at_ms=1_500,
        )
        self.assertFalse(empty.complete)
        self.assertEqual(empty.pending_actions, ("docx.create",))

        arguments = {"content": "verified"}
        ticket, manifest, trust = signed_ticket(
            arguments,
            request_id=grant.request_id,
            run_id=grant.run_id,
            generation=grant.generation,
            principal_scope_hash=grant.principal_scope_hash,
            skill_id=grant.skill_id,
            skill_version=grant.skill_version,
            skill_sha256=grant.skill_sha256,
            skill_activation_id=grant.activation_id,
            skill_activation_sha256=grant.activation_sha256,
        )
        binding = self.store.bind_skill_activation_ticket(
            grant.activation_id,
            ticket,
            bound_at_ms=20_000,
        )
        self.assertTrue(binding.created_by_this_call)
        transport = FakeBackendTransport()
        transport.response = backend_response(ticket, {"created": True})
        response = BackendClient(
            transport,
            self.store,
            ticket_consumer_instance_id="gateway-p9-fact",
        ).execute(
            ticket,
            arguments,
            capability_manifest=manifest,
            trust_bundle=trust,
            now_ms=20_000,
            expected_gateway_epoch=3,
            minimum_generation=grant.generation,
        )
        self.facts.record_execution(response, observed_at_ms=20_100)
        complete = self.authority.step_check(
            **self.scope,
            principal_scope_hash=self.inbound.principal_scope_hash,
            skill_id=grant.skill_id,
            activation_sha256=grant.activation_sha256,
            checked_at_ms=20_200,
        )
        self.assertTrue(complete.complete)
        self.assertEqual(complete.completed_actions, ("docx.create",))
        self.assertEqual(complete.current_stage, "complete")

        crossed = ticket.model_copy(
            update={"payload": ticket.payload.model_copy(update={"generation": grant.generation + 1})}
        )
        with self.assertRaises(StoreConflictError):
            self.store.bind_skill_activation_ticket(grant.activation_id, crossed, bound_at_ms=20_000)

    def test_missing_action_and_cross_generation_fail_closed(self) -> None:
        unavailable = SkillAuthority(
            SkillSelectionService(skill_catalog()),
            routing_manifest("file.read"),
            self.store,
            self.facts,
        )
        rejected = unavailable.model_request(
            "skill.get",
            **self.scope,
            principal_scope_hash=self.inbound.principal_scope_hash,
            decided_at_ms=1_500,
            skill_id="skill_word_p9",
        )
        self.assertEqual(rejected.resolution.record.decision, "reject")
        self.assertIsNone(rejected.activation)
        with self.assertRaises(StoreConflictError):
            self.authority.model_request(
                "skill.get",
                request_id=self.active.entry.request_id,
                run_id=self.active.generation.run_id,
                generation=self.active.generation.generation + 1,
                principal_scope_hash=self.inbound.principal_scope_hash,
                decided_at_ms=1_600,
                skill_id="skill_word_p9",
            )

    def test_internal_api_is_strict_and_uses_same_authority(self) -> None:
        router = SkillInternalApiRouter(self.authority, "t" * 32)
        body = {
            **self.scope,
            "principal_scope_hash": self.inbound.principal_scope_hash,
            "query": "创建 DOCX 文档",
        }
        import json

        response = router.dispatch(
            "POST",
            "/api/v1/internal/skills/route",
            "application/json",
            json.dumps(body).encode("utf-8"),
            now_ms=1_700,
        )
        self.assertEqual(response.payload["catalog_sha256"], self.authority.catalog_sha256)
        self.assertEqual(response.payload["selection"]["origin"], "model_request")
        with self.assertRaises(SkillApiError):
            router.dispatch(
                "POST",
                "/api/v1/internal/skills/get",
                "application/json",
                json.dumps({**body, "skill_id": "skill_word_p9"}).encode("utf-8"),
                now_ms=1_800,
            )

    def test_omni_router_is_thin_and_ignores_forged_progress_claims(self) -> None:
        router = load_router()
        self.assertFalse(hasattr(router, "SKILL_CATALOG"))
        runtime = types.SimpleNamespace(
            config=types.SimpleNamespace(
                request_id=self.active.entry.request_id,
                run_id=self.active.generation.run_id,
                generation=self.active.generation.generation,
                principal_scope_hash=self.inbound.principal_scope_hash,
                skill_activation_sha256="b" * 64,
            )
        )
        observed: dict[str, object] = {}

        def fake_request(_runtime, operation, payload):
            observed.update({"operation": operation, "payload": dict(payload)})
            return {
                "status": "OK",
                "catalog_sha256": "c" * 64,
                "step": {
                    "current_stage": "execute",
                    "complete": False,
                    "completed_actions": [],
                    "pending_actions": ["docx.create"],
                },
            }

        with patch.object(router, "_request_gateway", side_effect=fake_request):
            result = router._skill_step_check(  # noqa: SLF001
                runtime,
                "skill_word_p9",
                {
                    "completed_actions": ["docx.create"],
                    "last_qc": {"passed": True},
                    "artifacts": [{"path": "forged.docx", "exists": True}],
                },
            )
        self.assertTrue(result["success"])
        self.assertFalse(result["result"]["complete"])
        self.assertEqual(
            observed["payload"],
            {"skill_id": "skill_word_p9", "skill_activation_sha256": "b" * 64},
        )


if __name__ == "__main__":
    unittest.main()
