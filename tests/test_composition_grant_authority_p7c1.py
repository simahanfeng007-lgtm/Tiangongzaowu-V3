"""P7C.1 integration and adversarial tests for composition authorization.

The tests deliberately enter through the existing ``OmniGrantAuthority`` and
Gateway Store.  They never call PolicyEngine, TicketSigner, or the durable
authorization receipt directly, so a passing result proves that the narrow
plan-id adapter cannot smuggle caller-selected invocation material into the
signed chain.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any, Iterator

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from runtime_security import EphemeralTestProtector

import test_capability_composition_p4 as p4
import test_composition_executable_plan_p7c0 as p7c0
from contracts import (
    AttachmentRef,
    ObjectGrant,
    PublicKeyDescriptor,
    TrustBundle,
    TrustScope,
    canonical_json_bytes,
    canonical_sha256,
    derive_effect_identity,
)
from tests.test_execution_contracts import execution_ticket
from total_gateway.action_registry import (
    ActionSchemaCatalog,
    load_action_authority,
)
from total_gateway.composition_activation_adapter import (
    CompositionActivationAdapter,
)
from total_gateway.composition_executable_plan import (
    ArgumentSlotV1,
    FinalOutputAliasV1,
    OutputDeclarationV1,
    PlanInputV1,
    PlanInputValueBindingV1,
    StepExecutionBindingV1,
    StepOutputValueBindingV1,
    computed_execution_bindings_sha256,
)
from total_gateway.composition_executable_plan_store import (
    ExecutableCompositionPlanStoreRecord,
)
from total_gateway.composition_execution_binding import derive_run_sequence
from total_gateway.effects import EffectClaim, EffectResult
from total_gateway.fact_ledger import FactLedger
from total_gateway.object_store import ContentAddressedObjectStore
from total_gateway.omni_grant_authority import (
    OmniGrantAuthority,
    OmniGrantAuthorityError,
)
from total_gateway.orchestration import GatewayOrchestrationWorker
from total_gateway.policy_evidence import PolicyEvidenceLedger
from total_gateway.store import GatewayStateStore
from total_gateway.tickets import TicketSigner


ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_MANIFEST = (
    ROOT / "src" / "omni_body_skill" / "registry" / "capability_manifest.generated.json"
)
ZERO = "0" * 64
COMPONENT_MANIFEST_SHA256 = "c" * 64
SKILL_CATALOG_SHA256 = "d" * 64


def _rehash_primitive(
    primitive,
    *,
    argument_schema_sha256: str,
    result_schema_sha256: str,
):
    draft = primitive.model_copy(
        update={
            "argument_schema_sha256": argument_schema_sha256,
            "result_schema_sha256": result_schema_sha256,
            "descriptor_sha256": ZERO,
        }
    )
    return draft.model_copy(
        update={
            "descriptor_sha256": canonical_sha256(
                draft.model_dump(mode="json", exclude={"descriptor_sha256"})
            )
        }
    )


def _production_material(
    store: GatewayStateStore,
    objects: ContentAddressedObjectStore,
    workspace_root: Path,
    loaded,
    *,
    action_id: str = "life.body.state.query",
    target: str = "",
    arguments: dict[str, Any] | None = None,
    with_object_grant: bool = False,
    multi_step: bool = False,
    plan_expires_at_ms: int = 2_500,
) -> tuple[dict[str, Any], Any, ObjectGrant | None]:
    """Build a P7C.0 bundle using the current production registry/schema.

    P7C.0's public fixture is reused for lineage, candidate compilation,
    verification bindings, hashing, and atomic persistence.  Only the action
    and its explicit argument-schema hash are selected from today's generated
    production authority.
    """

    arguments = (
        {"recent_limit": 5, "sections": ["summary"]}
        if arguments is None
        else arguments
    )
    envelope, request, run = p7c0._register_request_lineage(store)
    permission = next(
        item for item in loaded.registry.permissions if item.action_id == action_id
    )
    schema = loaded.schema_catalog.resolve(
        action_id,
        permission.action_version,
        require_explicit=True,
    )
    multi_step = multi_step or with_object_grant
    selected_action_ids = (action_id,)
    if multi_step:
        selected_action_ids = (action_id, "skill.get")
    selected_permissions = {
        selected_action_id: next(
            item
            for item in loaded.registry.permissions
            if item.action_id == selected_action_id
        )
        for selected_action_id in selected_action_ids
    }
    selected_schemas = {
        selected_action_id: loaded.schema_catalog.resolve(
            selected_action_id,
            selected_permissions[selected_action_id].action_version,
            require_explicit=True,
            require_result_explicit=multi_step,
        )
        for selected_action_id in selected_action_ids
    }
    specs = tuple(
        {
            "action_id": selected_action_id,
            "risk": selected_permissions[selected_action_id].registry_risk,
            "effect": selected_permissions[selected_action_id].effect,
            "side_effects": (
                selected_permissions[selected_action_id].allowed_side_effects
            ),
            "read_set": ("resource:life",),
            "resource_scope": (
                selected_permissions[selected_action_id].path_policy,
            ),
        }
        for selected_action_id in selected_action_ids
    )
    _fixture_registry, tool_world, method_world = p4._worlds(
        specs,
        manifest_sha256=loaded.registry.source_manifest_sha256,
    )
    primitives = tuple(
        _rehash_primitive(
            primitive,
            argument_schema_sha256=(
                selected_schemas[primitive.action_id].argument_schema_sha256
            ),
            result_schema_sha256=(
                selected_schemas[primitive.action_id].result_schema_sha256
            ),
        )
        for primitive in tool_world.primitives
    )
    draft_world = replace(
        tool_world,
        action_registry_sha256=loaded.registry.registry_sha256,
        primitives=primitives,
        snapshot_sha256=ZERO,
    )
    tool_world = replace(
        draft_world,
        snapshot_sha256=canonical_sha256(draft_world.payload()),
    )
    candidates = p7c0.build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=selected_action_ids,
    )
    context = replace(
        p4._context(
            goal_ref="goal.p7c1-authorize-a0",
            manifest_sha256=loaded.registry.source_manifest_sha256,
        ),
        request_id=request.request_id,
        run_id=run.run_id,
        generation=1,
        principal_scope_hash=envelope.principal_scope_hash,
        created_at_ms=1_250,
        context_sha256=ZERO,
    ).with_computed_sha256()

    steps = (("step.01", "A01", ()),)
    if multi_step:
        steps = (
            ("step.01", "A01", ()),
            ("step.02", "A02", ("step.01",)),
        )
    document = p4._proposal_document(
        goal_ref="goal.p7c1-authorize-a0",
        methods=("M01",),
        actions=("A01", "A02") if multi_step else ("A01",),
        steps=steps,
    )
    proposal = p7c0.parse_composition_proposal(document, candidates)
    legacy_plan = p7c0.compile_capability_composition_plan(
        proposal,
        candidates,
        context,
        loaded.registry,
    )
    validation = p7c0.validate_capability_composition_plan(
        legacy_plan,
        proposal,
        candidates,
        context,
        loaded.registry,
        available_verifiers=frozenset(legacy_plan.verification_intents),
        validated_at_ms=1_300,
    )
    assert validation.result == "PROVED_VALID"
    verification_registry = p7c0.VerifierRegistry.with_defaults().snapshot(
        captured_at_ms=1_350
    )
    verification_bindings = tuple(
        p7c0.build_system_verification_binding(
            intent_ref=intent_ref,
            predicate=p7c0.AcceptancePredicate.create(
                predicate_type="artifact.nonempty",
                subject_kind="artifact",
                params={},
            ),
            subject_identity=f"object:p7c1-{index}",
            evaluation_phase="POST_EXECUTION",
            registry_snapshot=verification_registry,
        )
        for index, intent_ref in enumerate(legacy_plan.verification_intents)
    )
    shadow = p7c0.propose_shadow_composition_activation(
        legacy_plan,
        validation,
        loaded.registry,
        verification_registry,
        verification_bindings,
        current_world_state_sha256=legacy_plan.world_state_sha256,
        expected_principal_scope_hash=legacy_plan.principal_scope_hash,
        issued_at_ms=1_500,
        expires_at_ms=plan_expires_at_ms,
    )

    candidate = candidates.action_by_candidate()[proposal.steps[0].candidate_id]
    state_sha256_schema = next(
        (
            item
            for item in schema.value_schemas
            if item.value_schema_id == "state_sha256"
        ),
        None,
    )
    first_output = p7c0._hashed(
        OutputDeclarationV1(
            output_binding_id=proposal.steps[0].output_bindings[0],
            source_kind="RESULT_PAYLOAD",
            json_pointer=(
                state_sha256_schema.json_pointer
                if state_sha256_schema is not None
                else "/result"
            ),
            value_schema_sha256=(
                state_sha256_schema.value_schema_sha256
                if state_sha256_schema is not None
                else p7c0.H
            ),
            sha256=ZERO,
        )
    )
    first_step = p7c0._hashed(
        StepExecutionBindingV1(
            step_id=legacy_plan.steps[0].step_id,
            candidate_id=proposal.steps[0].candidate_id,
            candidate_binding_sha256=candidate.binding_sha256,
            action_id=action_id,
            action_version=permission.action_version,
            source_revision=candidate.source_revision,
            argument_schema_sha256=schema.argument_schema_sha256,
            result_schema_sha256=candidate.primitive.result_schema_sha256,
            permission=permission,
            permission_sha256=permission.permission_sha256,
            depends_on=(),
            target_skeleton=target,
            args_skeleton=arguments,
            argument_slots=(),
            output_declarations=(first_output,),
            sha256=ZERO,
        )
    )

    plan_inputs: tuple[PlanInputV1, ...] = ()
    object_grant: ObjectGrant | None = None
    step_bindings = (first_step,)
    final_producer = first_step
    final_output = first_output
    attachment = None
    object_input = None
    if with_object_grant:
        body = b"sealed P7C.1 object input"
        stored = objects.put_bytes(
            body,
            kind="attachment",
            tenant_id=envelope.tenant_id,
            link_account_id=envelope.link_account_id,
            conversation_scope_hash=envelope.conversation_scope_hash,
            created_at_ms=1_000,
        ).reference
        attachment = AttachmentRef(
            object_id=stored.object_id,
            revision=1,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            mime="text/plain",
            filename="p7c1.txt",
            tenant_id=stored.tenant_id,
            link_account_id=stored.link_account_id,
            conversation_scope_hash=stored.conversation_scope_hash,
            source_message_ref=envelope.channel_message_ref,
            created_at_ms=1_000,
        )
        object_grant = ObjectGrant(
            object_id=stored.object_id,
            revision=1,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            mime="text/plain",
            tenant_id=stored.tenant_id,
            link_account_id=stored.link_account_id,
            conversation_scope_hash=stored.conversation_scope_hash,
        )
        object_input = p7c0._hashed(
            PlanInputV1(
                input_id="input.object",
                input_kind="OBJECT_GRANT",
                object_grant=object_grant,
                value_schema_sha256=p7c0.H,
                value_sha256=object_grant.sha256,
                sha256=ZERO,
            )
        )
    if multi_step:
        upstream_ref = p7c0._hashed(
            StepOutputValueBindingV1(
                producer_step_id=first_step.step_id,
                output_binding_id=first_output.output_binding_id,
                output_declaration_sha256=first_output.sha256,
                sha256=ZERO,
            )
        )
        second_candidate = candidates.action_by_candidate()[
            proposal.steps[1].candidate_id
        ]
        second_permission = selected_permissions[second_candidate.primitive.action_id]
        second_schema = selected_schemas[second_candidate.primitive.action_id]
        markdown_schema = next(
            item
            for item in second_schema.value_schemas
            if item.value_schema_id == "markdown"
        )
        second_output = p7c0._hashed(
            OutputDeclarationV1(
                output_binding_id=proposal.steps[1].output_bindings[0],
                source_kind="RESULT_PAYLOAD",
                json_pointer=markdown_schema.json_pointer,
                value_schema_sha256=markdown_schema.value_schema_sha256,
                sha256=ZERO,
            )
        )
        second_args = {"skill_id": None}
        second_slots = [
            p7c0._hashed(
                ArgumentSlotV1(
                    destination_json_pointer="/skill_id",
                    value_binding=upstream_ref,
                    sha256=ZERO,
                )
            )
        ]
        if object_input is not None:
            object_ref = p7c0._hashed(
                PlanInputValueBindingV1(
                    input_id=object_input.input_id,
                    input_sha256=object_input.sha256,
                    json_pointer="",
                    sha256=ZERO,
                )
            )
            second_args["unused_object"] = None
            second_slots.append(
                p7c0._hashed(
                    ArgumentSlotV1(
                        destination_json_pointer="/unused_object",
                        value_binding=object_ref,
                        sha256=ZERO,
                    )
                )
            )
        second_step = p7c0._hashed(
            StepExecutionBindingV1(
                step_id=legacy_plan.steps[1].step_id,
                candidate_id=proposal.steps[1].candidate_id,
                candidate_binding_sha256=second_candidate.binding_sha256,
                action_id=second_candidate.primitive.action_id,
                action_version=second_permission.action_version,
                source_revision=second_candidate.source_revision,
                argument_schema_sha256=second_schema.argument_schema_sha256,
                result_schema_sha256=second_schema.result_schema_sha256,
                permission=second_permission,
                permission_sha256=second_permission.permission_sha256,
                depends_on=(first_step.step_id,),
                target_skeleton="",
                args_skeleton=second_args,
                argument_slots=tuple(second_slots),
                output_declarations=(second_output,),
                sha256=ZERO,
            )
        )
        plan_inputs = () if object_input is None else (object_input,)
        step_bindings = (first_step, second_step)
        final_producer = second_step
        final_output = second_output
        if attachment is not None:
            p7c0._replace_inbound_attachments(
                store,
                {"legacy_plan": legacy_plan},
                (attachment,),
            )

    final_reference = p7c0._hashed(
        StepOutputValueBindingV1(
            producer_step_id=final_producer.step_id,
            output_binding_id=final_output.output_binding_id,
            output_declaration_sha256=final_output.sha256,
            sha256=ZERO,
        )
    )
    final_alias = p7c0._hashed(
        FinalOutputAliasV1(
            alias=proposal.output_bindings[0],
            value_binding=final_reference,
            sha256=ZERO,
        )
    )
    material = {
        "proposal": proposal,
        "candidates": candidates,
        "context": context,
        "legacy_plan": legacy_plan,
        "validation": validation,
        "action_registry": loaded.registry,
        "verification_registry": verification_registry,
        "verification_bindings": verification_bindings,
        "shadow": shadow,
        "plan_inputs": plan_inputs,
        "step_bindings": step_bindings,
        "final_output_aliases": (final_alias,),
        "workspace": p7c0._workspace(workspace_root),
    }
    bundle = p7c0._persist_executable(store, material)
    return material, bundle.record.executable_plan, object_grant


def _trust_bundle(private: Ed25519PrivateKey) -> TrustBundle:
    public = private.public_key().public_bytes_raw()
    descriptor = PublicKeyDescriptor(
        kid="p7c1_execution_key",
        issuer="tiangong-total-gateway",
        audience="tiangong-backend",
        purpose="execution_ticket",
        public_key_base64url=base64.urlsafe_b64encode(public)
        .rstrip(b"=")
        .decode("ascii"),
        public_key_sha256=hashlib.sha256(public).hexdigest(),
        state="ACTIVE",
        not_before_ms=0,
        not_after_ms=100_000,
        component_manifest_hash=COMPONENT_MANIFEST_SHA256,
    )
    return TrustBundle(
        bundle_id="trust_p7c1",
        revision=1,
        gateway_epoch=1,
        generated_at_ms=1_500,
        required_scopes=(
            TrustScope(
                issuer=descriptor.issuer,
                audience=descriptor.audience,
                purpose=descriptor.purpose,
            ),
        ),
        keys=(descriptor,),
        production_ready=True,
        bundle_sha256=ZERO,
    ).with_computed_sha256()


@dataclass
class _Harness:
    root: Path
    database_path: Path
    store: GatewayStateStore
    objects: ContentAddressedObjectStore
    facts: FactLedger
    loaded: Any
    capability_file_sha256: str
    private: Ed25519PrivateKey
    signer: TicketSigner
    trust: TrustBundle
    authority: OmniGrantAuthority
    material: dict[str, Any]
    plan: Any
    outer: Any
    object_grant: ObjectGrant | None
    parent_claim: EffectClaim | None

    def close(self) -> None:
        self.facts.close()
        self.objects.close()
        self.store.close()


def _authority(
    harness: _Harness,
    *,
    store: GatewayStateStore | None = None,
    objects: ContentAddressedObjectStore | None = None,
    registry=None,
    schema_catalog: ActionSchemaCatalog | None = None,
    capability_manifest_hash: str | None = None,
    trust_provider=None,
    outer=None,
    authority_expires_at_ms: int = 2_490,
) -> OmniGrantAuthority:
    authority = OmniGrantAuthority(
        registry=harness.loaded.registry if registry is None else registry,
        action_schema_catalog=(
            harness.loaded.schema_catalog
            if schema_catalog is None
            else schema_catalog
        ),
        capability_manifest_hash=(
            harness.capability_file_sha256
            if capability_manifest_hash is None
            else capability_manifest_hash
        ),
        capability_source_manifest_hash=harness.loaded.manifest_sha256,
        component_manifest_hash=COMPONENT_MANIFEST_SHA256,
        skill_catalog_hash=SKILL_CATALOG_SHA256,
        signer=harness.signer,
        gateway_epoch=1,
        workspace_root=harness.root,
        evidence=PolicyEvidenceLedger(harness.root / "policy-evidence"),
        trust_bundle_provider=(
            (lambda _now_ms: harness.trust)
            if trust_provider is None
            else trust_provider
        ),
        effect_store=harness.store if store is None else store,
        object_store=harness.objects if objects is None else objects,
        fact_ledger=harness.facts,
    )
    authority.register(
        harness.outer if outer is None else outer,
        life_id="life_p7c1",
        life_evidence_ref="lev_" + "a" * 64,
        session_id="session_p7c1",
        registered_at_ms=1_600,
        authority_expires_at_ms=authority_expires_at_ms,
    )
    return authority


def _open_harness(
    root: Path,
    *,
    action_id: str = "life.body.state.query",
    target: str = "",
    arguments: dict[str, Any] | None = None,
    with_object_grant: bool = False,
    multi_step: bool = False,
    coherent_parent_effect: bool = False,
    outer_expires_at_ms: int = 2_450,
    authority_expires_at_ms: int = 2_490,
    complete_parent_effect: bool = True,
    plan_expires_at_ms: int = 2_500,
) -> _Harness:
    root = root.resolve()
    database_path = root / "gateway.sqlite3"
    store = GatewayStateStore.open(database_path, now_ms=1_000)
    objects = ContentAddressedObjectStore.open(root / "objects", now_ms=1_000)
    facts = FactLedger.open(root / "facts.sqlite3", objects, now_ms=1_000)
    loaded = load_action_authority(CAPABILITY_MANIFEST, generated_at_ms=1_250)
    capability_file_sha256 = hashlib.sha256(
        CAPABILITY_MANIFEST.read_bytes()
    ).hexdigest()
    material, plan, object_grant = _production_material(
        store,
        objects,
        root,
        loaded,
        action_id=action_id,
        target=target,
        arguments=arguments,
        with_object_grant=with_object_grant,
        multi_step=multi_step,
        plan_expires_at_ms=plan_expires_at_ms,
    )
    private = Ed25519PrivateKey.generate()
    signer = TicketSigner("p7c1_execution_key", private)
    trust = _trust_bundle(private)
    envelope = store.get_request_envelope(plan.request_id)
    assert envelope is not None
    input_objects = tuple(
        sorted(
            (
                item.object_grant
                for item in plan.plan_inputs
                if item.object_grant is not None
            ),
            key=lambda item: (item.object_id, item.revision),
        )
    )
    outer = execution_ticket(
        ticket_id="ticket_parent_p7c1",
        nonce="nonce_parent_p7c1",
        issued_at_ms=1_500,
        not_before_ms=1_500,
        expires_at_ms=outer_expires_at_ms,
        gateway_epoch=1,
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        channel=envelope.channel,
        tenant_id=envelope.tenant_id,
        link_account_id=envelope.link_account_id,
        conversation_scope_hash=envelope.conversation_scope_hash,
        principal_scope_hash=plan.principal_scope_hash,
        capability_manifest_hash=capability_file_sha256,
        component_manifest_hash=COMPONENT_MANIFEST_SHA256,
        workspace_id=plan.workspace.workspace_id,
        input_objects=input_objects,
        max_output_bytes=1_000_000,
        max_runtime_ms=30_000,
        max_tool_calls=10,
    )
    parent_claim: EffectClaim | None = None
    if multi_step or with_object_grant or coherent_parent_effect:
        parent_intent_sha256 = canonical_sha256(
            {
                "domain": "tiangong.test.composition-parent.v1",
                "executable_plan_id": plan.executable_plan_id,
            }
        )
        run_sequence = derive_run_sequence(plan.request_id, plan.run_id)
        parent_effect_id = derive_effect_identity(
            request_id=plan.request_id,
            run_id=plan.run_id,
            run_sequence=run_sequence,
            generation=plan.generation,
            effect_kind="execution",
            ordinal=0,
            intent_sha256=parent_intent_sha256,
        ).effect_id
        parent_claim = EffectClaim(
            effect_id=parent_effect_id,
            request_id=plan.request_id,
            run_id=plan.run_id,
            run_sequence=run_sequence,
            generation=plan.generation,
            effect_kind="execution",
            ordinal=0,
            intent_sha256=parent_intent_sha256,
            pipeline_version="tiangong.test.composition-parent.v1",
            attempt=1,
            claim_revision=1,
            lease_epoch=1,
            supersedes_claim_sha256=None,
            owner_component_id="tiangong-total-gateway",
            claimed_at_ms=1_600,
            claim_sha256=ZERO,
        ).with_computed_sha256()
        outer = outer.model_copy(
            update={
                "payload": outer.payload.model_copy(
                    update={
                        "effect_id": parent_effect_id,
                        "claim_sha256": parent_claim.claim_sha256,
                        "claim_revision": parent_claim.claim_revision,
                        "claim_lease_epoch": parent_claim.lease_epoch,
                    }
                )
            }
        )
        store.claim_effect(parent_claim)
        if complete_parent_effect:
            store.mark_effect_started(parent_effect_id, started_at_ms=1_601)
            store.complete_effect(
                EffectResult(
                    result_id="parent-result-p7c1",
                    effect_id=parent_effect_id,
                    status="SUCCEEDED",
                    fact_id="parent-fact-p7c1",
                    evidence_sha256="9" * 64,
                    observed_at_ms=1_602,
                    result_sha256=ZERO,
                ).with_computed_sha256()
            )
    outer = signer.sign_execution(outer.payload)
    shell = _Harness(
        root=root,
        database_path=database_path,
        store=store,
        objects=objects,
        facts=facts,
        loaded=loaded,
        capability_file_sha256=capability_file_sha256,
        private=private,
        signer=signer,
        trust=trust,
        authority=None,  # type: ignore[arg-type]
        material=material,
        plan=plan,
        outer=outer,
        object_grant=object_grant,
        parent_claim=parent_claim,
    )
    shell.authority = _authority(
        shell,
        authority_expires_at_ms=authority_expires_at_ms,
    )
    return shell


@contextmanager
def _harness(root: Path, **kwargs) -> Iterator[_Harness]:
    harness = _open_harness(root, **kwargs)
    try:
        yield harness
    finally:
        harness.close()


def _authorize(
    harness: _Harness,
    *,
    authority: OmniGrantAuthority | None = None,
    parent_ticket_id: str | None = None,
    registration_id: str | None = None,
    step_id: str = "step.01",
    now_ms: int = 1_700,
) -> dict[str, Any]:
    return (authority or harness.authority).issue_composition_step(
        parent_ticket_id=parent_ticket_id or harness.outer.payload.ticket_id,
        registration_id=registration_id or harness.plan.registration_id,
        step_id=step_id,
        now_ms=now_ms,
    )


def _effect_fact_nonce_counts(store: GatewayStateStore) -> dict[str, int]:
    return {
        table: int(
            store._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        )
        for table in (
            "effect_ledger",
            "effect_attempts",
            "effect_facts",
            "security_nonce_ledger",
        )
    }


def _authorization_count(store: GatewayStateStore) -> int:
    return int(
        store._connection.execute(
            "SELECT count(*) FROM composition_step_authorization"
        ).fetchone()[0]
    )


def _catalog_with_entry(
    catalog: ActionSchemaCatalog,
    action_id: str,
    **updates,
) -> ActionSchemaCatalog:
    entries = tuple(
        replace(item, **updates) if item.action_id == action_id else item
        for item in catalog.entries
    )
    draft = replace(catalog, entries=entries, catalog_sha256=ZERO)
    return replace(draft, catalog_sha256=canonical_sha256(draft.payload()))


def _registry_with_permission(harness: _Harness, **updates):
    permissions = []
    for permission in harness.loaded.registry.permissions:
        if permission.action_id == harness.plan.step_bindings[0].action_id:
            draft = permission.model_copy(
                update={**updates, "permission_sha256": ZERO}
            )
            permission = draft.with_computed_sha256()
        permissions.append(permission)
    draft_registry = harness.loaded.registry.model_copy(
        update={"permissions": tuple(permissions), "registry_sha256": ZERO}
    )
    return draft_registry.with_computed_sha256()


def _tampered_plan_record(
    harness: _Harness,
    *,
    arguments: dict[str, Any] | None = None,
    target: str | None = None,
    registration_id: str | None = None,
) -> ExecutableCompositionPlanStoreRecord:
    plan = harness.plan
    original = plan.step_bindings[0]
    changed_step = original.model_copy(
        update={
            "args_skeleton": (
                original.args_skeleton if arguments is None else arguments
            ),
            "target_skeleton": (
                original.target_skeleton if target is None else target
            ),
            "sha256": ZERO,
        }
    ).with_computed_sha256()
    steps = (changed_step, *plan.step_bindings[1:])
    execution_bindings_sha256 = computed_execution_bindings_sha256(
        workspace=plan.workspace,
        plan_inputs=plan.plan_inputs,
        step_bindings=steps,
        final_output_aliases=plan.final_output_aliases,
    )
    changed_plan = plan.model_copy(
        update={
            "step_bindings": steps,
            "execution_bindings_sha256": execution_bindings_sha256,
            "registration_id": (
                plan.registration_id
                if registration_id is None
                else registration_id
            ),
            "executable_plan_id": "ecp_" + ZERO,
            "executable_plan_sha256": ZERO,
        }
    ).with_computed_identity()
    return ExecutableCompositionPlanStoreRecord(executable_plan=changed_plan)


def test_happy_a0_explicit_schema_binds_every_signed_host_and_writes_no_execution(
    tmp_path: Path,
) -> None:
    with _harness(tmp_path) as harness:
        before = _effect_fact_nonce_counts(harness.store)

        response = _authorize(harness)

        assert response["status"] == "OK"
        assert response["decision"]["risk_class"] == "A0"
        runtime = response["runtime"]
        binding = runtime["composition_execution_binding"]
        grant_payload = response["grant"]["payload"]
        assert runtime["executable_plan_id"] == harness.plan.executable_plan_id
        assert runtime["step_id"] == "step.01"
        assert runtime["effect_id"] == grant_payload["effect_id"]
        assert runtime["composition_binding_sha256"] == binding["binding_sha256"]
        assert runtime["capability_manifest_hash"] == harness.capability_file_sha256
        assert harness.capability_file_sha256 != harness.loaded.manifest_sha256
        assert (
            harness.plan.capability_manifest_sha256
            == harness.loaded.manifest_sha256
        )
        assert binding["executable_plan_sha256"] == harness.plan.executable_plan_sha256
        assert binding["step_binding_sha256"] == harness.plan.step_bindings[0].sha256
        assert binding["materialized_arguments_sha256"] == canonical_sha256(
            {"recent_limit": 5, "sections": ["summary"]}
        )
        assert grant_payload["composition_execution_binding"] == binding
        persisted = harness.store.get_composition_step_authorization(
            harness.plan.executable_plan_id,
            "step.01",
            now_ms=1_700,
        )
        assert persisted is not None
        assert (
            persisted.artifacts.intent["composition_execution_binding"]
            == binding
        )
        assert (
            persisted.artifacts.decision["composition_execution_binding"]
            == binding
        )
        assert (
            persisted.artifacts.signed_ticket["payload"][
                "composition_execution_binding"
            ]
            == binding
        )
        assert (
            persisted.artifacts.signed_grant["payload"][
                "composition_execution_binding"
            ]
            == binding
        )
        assert _effect_fact_nonce_counts(harness.store) == before
        assert _authorization_count(harness.store) == 1


def test_worker_exposes_the_same_narrow_adapter_over_the_existing_authority(
    tmp_path: Path,
) -> None:
    with _harness(tmp_path) as harness:
        worker = object.__new__(GatewayOrchestrationWorker)
        worker._composition_activation_adapter = CompositionActivationAdapter(
            harness.authority
        )

        adapter = worker.composition_activation_adapter
        response = adapter.authorize_step(
            parent_ticket_id=harness.outer.payload.ticket_id,
            registration_id=harness.plan.registration_id,
            step_id="step.01",
            now_ms=1_700,
        )

        assert response["status"] == "OK"
        assert adapter is worker.composition_activation_adapter


def test_runtime_config_wires_raw_release_hash_and_semantic_schema_hash_separately(
    tmp_path: Path,
) -> None:
    objects = ContentAddressedObjectStore.open(
        (tmp_path / "runtime-objects").resolve(),
        now_ms=1_000,
    )
    store = GatewayStateStore.open(
        (tmp_path / "runtime-gateway.sqlite3").resolve(),
        now_ms=1_000,
    )
    config = SimpleNamespace(
        release_manifest_path=None,
        release_source_root=ROOT,
        environment="development",
        state_root=(tmp_path / "runtime-state").resolve(),
        workspace_root=tmp_path.resolve(),
        backend_internal_token="b" * 48,
        life_internal_token="l" * 48,
        communication_api_token="c" * 48,
        runtime_key_protector=EphemeralTestProtector(),
    )
    worker = GatewayOrchestrationWorker.from_runtime_config(
        config=config,
        activator=SimpleNamespace(),
        store=store,
        objects=objects,
        facts=SimpleNamespace(),
        gateway_epoch=1,
        gateway_instance_id="gateway-p7c1-wiring",
        now_ms=1_500,
    )
    try:
        authority = worker.omni_grant_authority
        raw_release_hash = worker.release_manifest.capability_manifest_sha256
        semantic_hash = authority.registry.source_manifest_sha256
        assert raw_release_hash != semantic_hash
        assert authority.capability_manifest_hash == raw_release_hash
        assert authority.capability_source_manifest_hash == semantic_hash
        assert (
            authority._action_schema_catalog.source_manifest_sha256
            == semantic_hash
        )
        assert worker.composition_activation_adapter._issuer is authority
    finally:
        worker.close()
        objects.close()
        store.close()


def test_restart_replays_the_durable_winner_byte_for_byte(tmp_path: Path) -> None:
    harness = _open_harness(tmp_path)
    first = _authorize(harness)
    first_bytes = canonical_json_bytes(first)
    harness.objects.close()
    harness.store.close()

    reopened_store = GatewayStateStore.open(harness.database_path, now_ms=1_701)
    reopened_objects = ContentAddressedObjectStore.open(
        harness.root / "objects", now_ms=1_701
    )
    try:
        restarted = _authority(
            harness,
            store=reopened_store,
            objects=reopened_objects,
        )
        replay = _authorize(harness, authority=restarted, now_ms=1_701)
        assert canonical_json_bytes(replay) == first_bytes
        assert replay == first
        assert _effect_fact_nonce_counts(reopened_store) == {
            "effect_ledger": 0,
            "effect_attempts": 0,
            "effect_facts": 0,
            "security_nonce_ledger": 0,
        }
    finally:
        reopened_objects.close()
        reopened_store.close()


def test_two_authorities_and_store_connections_converge_on_one_winner(
    tmp_path: Path,
) -> None:
    with _harness(tmp_path) as harness:
        second_store = GatewayStateStore.open(harness.database_path, now_ms=1_650)
        barrier = threading.Barrier(2)

        def synchronized_trust(_now_ms: int) -> TrustBundle:
            barrier.wait(timeout=10)
            return harness.trust

        first = _authority(harness, trust_provider=synchronized_trust)
        second = _authority(
            harness,
            store=second_store,
            trust_provider=synchronized_trust,
        )
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = tuple(
                    pool.map(
                        lambda authority: _authorize(
                            harness,
                            authority=authority,
                            now_ms=1_700,
                        ),
                        (first, second),
                    )
                )
            assert canonical_json_bytes(responses[0]) == canonical_json_bytes(
                responses[1]
            )
            assert _authorization_count(harness.store) == 1
            assert _effect_fact_nonce_counts(harness.store) == {
                "effect_ledger": 0,
                "effect_attempts": 0,
                "effect_facts": 0,
                "security_nonce_ledger": 0,
            }
        finally:
            second_store.close()


def test_expired_outer_ticket_is_not_resurrected_by_a_live_plan(
    tmp_path: Path,
) -> None:
    with _harness(tmp_path, outer_expires_at_ms=1_699) as harness:
        before = _effect_fact_nonce_counts(harness.store)
        with pytest.raises(OmniGrantAuthorityError):
            _authorize(harness, now_ms=1_700)
        assert _authorization_count(harness.store) == 0
        assert _effect_fact_nonce_counts(harness.store) == before


def test_expired_registration_is_not_resurrected_by_active_outer_ticket(
    tmp_path: Path,
) -> None:
    with _harness(tmp_path, outer_expires_at_ms=3_000) as harness:
        authority = _authority(
            harness,
            authority_expires_at_ms=3_000,
        )
        before = _effect_fact_nonce_counts(harness.store)
        with pytest.raises(OmniGrantAuthorityError):
            _authorize(harness, authority=authority, now_ms=2_501)
        assert _authorization_count(harness.store) == 0
        assert _effect_fact_nonce_counts(harness.store) == before


def test_released_generation_fails_before_any_authorization_receipt(
    tmp_path: Path,
) -> None:
    with _harness(tmp_path) as harness:
        harness.store.release_generation(
            harness.plan.request_id,
            released_at_ms=1_650,
        )
        before = _effect_fact_nonce_counts(harness.store)
        with pytest.raises(OmniGrantAuthorityError):
            _authorize(harness)
        assert _authorization_count(harness.store) == 0
        assert _effect_fact_nonce_counts(harness.store) == before


def test_global_action_fence_stops_composition_authorization(
    tmp_path: Path,
) -> None:
    with _harness(tmp_path) as harness:
        harness.store.increment_action_fence(
            reason="p7c1-adversarial-stop",
            now_ms=1_650,
        )
        before = _effect_fact_nonce_counts(harness.store)
        with pytest.raises(OmniGrantAuthorityError):
            _authorize(harness)
        assert _authorization_count(harness.store) == 0
        assert _effect_fact_nonce_counts(harness.store) == before


@pytest.mark.parametrize(
    "drift",
    (
        "registry",
        "manifest",
        "permission",
        "schema_hash",
        "schema_opaque",
    ),
)
def test_current_registry_manifest_permission_and_schema_drift_fail_closed(
    tmp_path: Path,
    drift: str,
) -> None:
    with _harness(tmp_path) as harness:
        kwargs: dict[str, Any] = {}
        if drift == "registry":
            draft = harness.loaded.registry.model_copy(
                update={
                    "generated_at_ms": harness.loaded.registry.generated_at_ms + 1,
                    "registry_sha256": ZERO,
                }
            )
            kwargs["registry"] = draft.with_computed_sha256()
        elif drift == "manifest":
            kwargs["capability_manifest_hash"] = "e" * 64
        elif drift == "permission":
            kwargs["registry"] = _registry_with_permission(
                harness,
                handler="_action_life_body_state_query_drifted",
            )
        elif drift == "schema_hash":
            kwargs["schema_catalog"] = _catalog_with_entry(
                harness.loaded.schema_catalog,
                "life.body.state.query",
                argument_schema_sha256="e" * 64,
            )
        else:
            kwargs["schema_catalog"] = _catalog_with_entry(
                harness.loaded.schema_catalog,
                "life.body.state.query",
                kind="OPAQUE",
            )
        drifted = _authority(harness, **kwargs)
        before = _effect_fact_nonce_counts(harness.store)

        with pytest.raises(OmniGrantAuthorityError):
            _authorize(harness, authority=drifted)

        assert _authorization_count(harness.store) == 0
        assert _effect_fact_nonce_counts(harness.store) == before


@pytest.mark.parametrize(
    "permission_updates",
    (
        {"registry_risk": "A1", "effective_risk": "A1"},
        {
            "registry_risk": "A2",
            "effective_risk": "A2",
            "effect": "write",
            "allowed_side_effects": ("local_write", "read"),
        },
        {"allowed_side_effects": ("local_write", "read")},
        {
            "registry_risk": "A4",
            "effective_risk": "A4",
            "allow_shell": True,
        },
        {
            "registry_risk": "A4",
            "effective_risk": "A4",
            "allow_python": True,
        },
    ),
    ids=("a1", "write-effect", "write-side-effect", "shell", "python"),
)
def test_current_non_a0_or_privileged_permission_never_authorizes(
    tmp_path: Path,
    permission_updates: dict[str, Any],
) -> None:
    with _harness(tmp_path) as harness:
        unsafe = _authority(
            harness,
            registry=_registry_with_permission(harness, **permission_updates),
        )
        before = _effect_fact_nonce_counts(harness.store)
        with pytest.raises(OmniGrantAuthorityError):
            _authorize(harness, authority=unsafe)
        assert _authorization_count(harness.store) == 0
        assert _effect_fact_nonce_counts(harness.store) == before


@pytest.mark.parametrize(
    "tamper",
    ("arguments", "target", "plan", "step", "parent"),
)
def test_argument_target_plan_step_and_parent_swaps_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    with _harness(tmp_path) as harness:
        kwargs: dict[str, Any] = {}
        if tamper == "arguments":
            forged = _tampered_plan_record(
                harness,
                arguments={
                    "recent_limit": 5,
                    "sections": ["summary"],
                    "caller_authority": True,
                },
            )
            monkeypatch.setattr(
                harness.store,
                "get_active_executable_composition_plan",
                lambda _registration_id, *, now_ms: forged,
            )
        elif tamper == "target":
            forged = _tampered_plan_record(harness, target="forged-target")
            monkeypatch.setattr(
                harness.store,
                "get_active_executable_composition_plan",
                lambda _registration_id, *, now_ms: forged,
            )
        elif tamper == "plan":
            forged = _tampered_plan_record(
                harness,
                registration_id="car_" + "f" * 64,
            )
            monkeypatch.setattr(
                harness.store,
                "get_active_executable_composition_plan",
                lambda _registration_id, *, now_ms: forged,
            )
        elif tamper == "step":
            kwargs["step_id"] = "step.99"
        else:
            kwargs["parent_ticket_id"] = "ticket_other_parent"
        before = _effect_fact_nonce_counts(harness.store)

        with pytest.raises(OmniGrantAuthorityError):
            _authorize(harness, **kwargs)

        assert _authorization_count(harness.store) == 0
        assert _effect_fact_nonce_counts(harness.store) == before


def test_dependent_step_cannot_be_authorized_before_p7d_scheduler(
    tmp_path: Path,
) -> None:
    with _harness(tmp_path, with_object_grant=True) as harness:
        before = _effect_fact_nonce_counts(harness.store)
        with pytest.raises(OmniGrantAuthorityError):
            _authorize(harness, step_id="step.02")
        assert _authorization_count(harness.store) == 0
        assert _effect_fact_nonce_counts(harness.store) == before


def test_object_grant_and_bytes_are_reverified_even_on_durable_replay(
    tmp_path: Path,
) -> None:
    with _harness(tmp_path, with_object_grant=True) as harness:
        assert harness.object_grant is not None
        first = _authorize(harness)
        assert first["status"] == "OK"
        assert _authorization_count(harness.store) == 1
        before = _effect_fact_nonce_counts(harness.store)

        digest = harness.object_grant.sha256
        blob = harness.objects.root / "blobs" / "sha256" / digest[:2] / digest
        original = blob.read_bytes()
        blob.write_bytes(b"X" * len(original))

        with pytest.raises(OmniGrantAuthorityError):
            _authorize(harness, now_ms=1_701)
        assert _authorization_count(harness.store) == 1
        assert _effect_fact_nonce_counts(harness.store) == before


def test_outer_ticket_cannot_drop_a_plan_object_grant(tmp_path: Path) -> None:
    with _harness(tmp_path, with_object_grant=True) as harness:
        bad_payload = harness.outer.payload.model_copy(
            update={
                "input_objects": (),
                "object_grants_sha256": canonical_sha256([]),
            }
        )
        bad_outer = harness.outer.model_copy(update={"payload": bad_payload})
        mismatched = _authority(harness, outer=bad_outer)
        before = _effect_fact_nonce_counts(harness.store)
        with pytest.raises(OmniGrantAuthorityError):
            _authorize(harness, authority=mismatched)
        assert _authorization_count(harness.store) == 0
        assert _effect_fact_nonce_counts(harness.store) == before


def test_object_grant_only_path_policy_rejects_even_a_valid_read_target(
    tmp_path: Path,
) -> None:
    presentation = tmp_path / "input.pptx"
    presentation.write_bytes(b"not executed")
    with _harness(
        tmp_path,
        action_id="qc.ppt.delivery_check",
        target=str(presentation.resolve()),
        arguments={"min_slides": 1},
    ) as harness:
        before = _effect_fact_nonce_counts(harness.store)
        with pytest.raises(OmniGrantAuthorityError):
            _authorize(harness)
        assert _authorization_count(harness.store) == 0
        assert _effect_fact_nonce_counts(harness.store) == before
