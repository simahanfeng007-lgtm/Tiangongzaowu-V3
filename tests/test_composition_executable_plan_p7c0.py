from __future__ import annotations

import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import threading
import unicodedata

import pytest
from pydantic import ValidationError
import total_gateway.store as store_module

from contracts import (
    AttachmentRef,
    InboundEnvelope,
    InboundScope,
    canonical_json_bytes,
    canonical_sha256,
    derive_inbound_scope_keys,
    derive_request_identity,
    derive_run_identity,
)
from contracts.execution import ObjectGrant
from contracts.verification import AcceptancePredicate
from total_gateway.composition_activation_registration import (
    LimitedCompositionActivationRegistrationV1,
    compile_limited_activation_registration,
)
from total_gateway.composition_activation_shadow import (
    build_system_verification_binding,
    propose_shadow_composition_activation,
)
from total_gateway.composition_activation_store import (
    LimitedActivationStoreRecord,
    computed_limited_activation_lifecycle_sha256,
)
from total_gateway.composition_executable_plan import (
    ArgumentSlotV1,
    ExecutableCompositionPlanError,
    ExecutableCompositionPlanV1,
    FinalOutputAliasV1,
    LiteralValueBindingV1,
    MAX_STORED_EXECUTABLE_PLAN_JSON_BYTES,
    OutputDeclarationV1,
    PlanInputV1,
    PlanInputValueBindingV1,
    StepExecutionBindingV1,
    StepOutputValueBindingV1,
    WorkspaceBindingV1,
    computed_execution_bindings_sha256,
    _validate_dataflow,
    _validate_object_grant_inputs,
    compile_executable_composition_plan,
)
from total_gateway.composition_executable_plan_store import (
    canonical_executable_plan_json,
)
from total_gateway.store import (
    STORE_SCHEMA_VERSION,
    GatewayStateStore,
    StoreConflictError,
    StoreCorruptionError,
)
from total_gateway.verification_registry import VerifierRegistry
from world_understanding.capability_composition import (
    build_candidate_snapshot,
    compile_capability_composition_plan,
    computed_plan_sha256,
    computed_proposal_sha256,
    parse_composition_proposal,
    validate_capability_composition_plan,
)

from tests.test_capability_composition_p4 import (
    H,
    _context,
    _proposal_document,
    _worlds,
)
from tests.test_composition_activation_store_p7b2 import (
    _register_request_lineage,
)


ZERO_SHA256 = "0" * 64

_CORRUPTION_TEST_TRIGGERS = frozenset(
    {
        "composition_executable_plan_immutable_delete_guard",
        "composition_executable_plan_immutable_update_guard",
        "composition_executable_plan_identity_insert_guard",
        "object_owners_immutable_delete_guard",
        "object_owners_identity_insert_guard",
    }
)


def _hashed(value):
    return value.with_computed_sha256()


def _workspace(path: Path) -> WorkspaceBindingV1:
    root = str(path.resolve(strict=True))
    normalized = os.path.normcase(unicodedata.normalize("NFC", root))
    return _hashed(
        WorkspaceBindingV1(
            workspace_id="workspace-" + canonical_sha256(root),
            workspace_root=root,
            workspace_scope_sha256=canonical_sha256(
                {"normalized_workspace": normalized}
            ),
            sha256=ZERO_SHA256,
        )
    )


def _register_request_lineage_variant(
    store: GatewayStateStore, seed: str
):
    if len(seed) != 1 or seed not in "0123456789abcdef":
        raise ValueError("test lineage seed must be one lowercase hex digit")
    scope = InboundScope(
        channel="desktop",
        tenant_id=f"tenant_p7c0_{seed}",
        link_account_id=f"desktop_p7c0_{seed}",
        conversation_ref=f"conversation_p7c0_{seed}",
        channel_message_ref=f"message_p7c0_{seed}",
        sender_ref=f"sender_p7c0_{seed}",
    )
    keys = derive_inbound_scope_keys(scope)
    idempotency_key = seed * 64
    request = derive_request_identity(idempotency_key)
    run = derive_run_identity(request.request_id, 1)
    envelope = InboundEnvelope(
        inbound_id=f"inbound_p7c0_{seed}",
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
        idempotency_key=idempotency_key,
        channel_metadata_hash=H,
        text="register a distinct P7C.0 test lineage",
    )
    registration = store.register_request(
        envelope, ingress_sha256=H, created_at_ms=1_100
    )
    assert registration.entry.request_id == request.request_id
    store.acquire_generation_lease(
        request_id=request.request_id,
        run_id=run.run_id,
        run_sequence=1,
        generation=1,
        gateway_epoch=1,
        lease_id=f"lease_p7c0_{seed}",
        owner_instance_id=f"gateway_p7c0_{seed}",
        issued_at_ms=1_200,
        lease_duration_ms=10_000,
    )
    return envelope, request, run


def _compile_material(
    store: GatewayStateStore,
    workspace_root: Path,
    *,
    risk: str = "A0",
    effect: str = "read",
    allow_shell: bool = False,
    allow_python: bool = False,
    declare_dependency: bool = True,
    primitive_side_effects: tuple[str, ...] | None = None,
    lineage_seed: str | None = None,
) -> dict:
    if lineage_seed is None:
        envelope, request, run = _register_request_lineage(store)
    else:
        envelope, request, run = _register_request_lineage_variant(
            store, lineage_seed
        )
    side_effects = (
        ("read",)
        if effect in {"read", "verify"}
        else ("local_write", "read")
    )
    specs = (
        {
            "action_id": "artifact.read",
            "risk": risk,
            "effect": effect,
            "side_effects": side_effects,
            "read_set": ("resource:artifact",),
            "allow_shell": allow_shell,
            "allow_python": allow_python,
        },
        {
            "action_id": "artifact.verify",
            "risk": risk,
            "effect": effect,
            "side_effects": side_effects,
            "read_set": ("resource:artifact",),
            "allow_shell": allow_shell,
            "allow_python": allow_python,
        },
    )
    action_registry, tool_world, method_world = _worlds(specs)
    if primitive_side_effects is not None:
        primitives = []
        for primitive in tool_world.primitives:
            draft_primitive = primitive.model_copy(
                update={
                    "side_effects": tuple(sorted(set(primitive_side_effects))),
                    "descriptor_sha256": ZERO_SHA256,
                }
            )
            primitives.append(
                draft_primitive.model_copy(
                    update={
                        "descriptor_sha256": canonical_sha256(
                            draft_primitive.model_dump(
                                mode="json", exclude={"descriptor_sha256"}
                            )
                        )
                    }
                )
            )
        draft_tool_world = replace(
            tool_world,
            primitives=tuple(primitives),
            snapshot_sha256=ZERO_SHA256,
        )
        tool_world = replace(
            draft_tool_world,
            snapshot_sha256=canonical_sha256(draft_tool_world.payload()),
        )
    candidates = build_candidate_snapshot(
        tool_world,
        method_world,
        method_ids=("generate_then_verify",),
        action_ids=("artifact.read", "artifact.verify"),
    )
    context = replace(
        _context(goal_ref="goal.p7c0-two-step"),
        request_id=request.request_id,
        run_id=run.run_id,
        generation=1,
        principal_scope_hash=envelope.principal_scope_hash,
        created_at_ms=1_250,
        context_sha256=ZERO_SHA256,
    ).with_computed_sha256()
    document = _proposal_document(
        goal_ref="goal.p7c0-two-step",
        methods=("M01",),
        actions=("A01", "A02"),
        steps=(
            ("step.01", "A01", ()),
            (
                "step.02",
                "A02",
                ("step.01",) if declare_dependency else (),
            ),
        ),
    )
    proposal = parse_composition_proposal(document, candidates)
    legacy_plan = compile_capability_composition_plan(
        proposal, candidates, context, action_registry
    )
    validation = validate_capability_composition_plan(
        legacy_plan,
        proposal,
        candidates,
        context,
        action_registry,
        available_verifiers=frozenset(legacy_plan.verification_intents),
        validated_at_ms=1_300,
    )
    assert validation.result == "PROVED_VALID"
    verification_registry = VerifierRegistry.with_defaults().snapshot(
        captured_at_ms=1_350
    )
    verification_bindings = tuple(
        build_system_verification_binding(
            intent_ref=intent_ref,
            predicate=AcceptancePredicate.create(
                predicate_type="artifact.nonempty",
                subject_kind="artifact",
                params={},
            ),
            subject_identity=f"object:p7c0-{index}",
            evaluation_phase="POST_EXECUTION",
            registry_snapshot=verification_registry,
        )
        for index, intent_ref in enumerate(legacy_plan.verification_intents)
    )
    shadow = propose_shadow_composition_activation(
        legacy_plan,
        validation,
        action_registry,
        verification_registry,
        verification_bindings,
        current_world_state_sha256=legacy_plan.world_state_sha256,
        expected_principal_scope_hash=legacy_plan.principal_scope_hash,
        issued_at_ms=1_500,
        expires_at_ms=2_500,
    )

    plan_input_value = {"artifact": {"id": "artifact-001"}}
    plan_input = _hashed(
        PlanInputV1(
            input_id="input.artifact",
            input_kind="INLINE_JSON",
            inline_value=plan_input_value,
            value_schema_sha256=H,
            value_sha256=canonical_sha256(plan_input_value),
            sha256=ZERO_SHA256,
        )
    )
    input_reference = _hashed(
        PlanInputValueBindingV1(
            input_id=plan_input.input_id,
            input_sha256=plan_input.sha256,
            json_pointer="/artifact/id",
            sha256=ZERO_SHA256,
        )
    )
    literal_reference = _hashed(
        LiteralValueBindingV1(value="metadata-only", sha256=ZERO_SHA256)
    )
    first_output = _hashed(
        OutputDeclarationV1(
            output_binding_id=proposal.steps[0].output_bindings[0],
            source_kind="RESULT_PAYLOAD",
            json_pointer="/artifact/id",
            value_schema_sha256=H,
            sha256=ZERO_SHA256,
        )
    )
    first_slots = tuple(
        sorted(
            (
                _hashed(
                    ArgumentSlotV1(
                        destination_json_pointer="/artifact_id",
                        value_binding=input_reference,
                        sha256=ZERO_SHA256,
                    )
                ),
                _hashed(
                    ArgumentSlotV1(
                        destination_json_pointer="/mode",
                        value_binding=literal_reference,
                        sha256=ZERO_SHA256,
                    )
                ),
            ),
            key=lambda item: item.destination_json_pointer,
        )
    )

    first_candidate = candidates.action_by_candidate()[proposal.steps[0].candidate_id]
    second_candidate = candidates.action_by_candidate()[proposal.steps[1].candidate_id]
    permission_by_action = {
        item.action_id: item for item in action_registry.permissions
    }
    first_step = _hashed(
        StepExecutionBindingV1(
            step_id=legacy_plan.steps[0].step_id,
            candidate_id=proposal.steps[0].candidate_id,
            candidate_binding_sha256=first_candidate.binding_sha256,
            action_id=legacy_plan.steps[0].action_id,
            action_version=legacy_plan.steps[0].action_version,
            source_revision=first_candidate.source_revision,
            argument_schema_sha256=first_candidate.primitive.argument_schema_sha256,
            result_schema_sha256=first_candidate.primitive.result_schema_sha256,
            permission=permission_by_action[legacy_plan.steps[0].action_id],
            permission_sha256=permission_by_action[
                legacy_plan.steps[0].action_id
            ].permission_sha256,
            depends_on=legacy_plan.steps[0].depends_on,
            target_skeleton=str(workspace_root / "artifact-001"),
            args_skeleton={"artifact_id": None, "mode": None},
            argument_slots=first_slots,
            output_declarations=(first_output,),
            sha256=ZERO_SHA256,
        )
    )

    upstream_reference = _hashed(
        StepOutputValueBindingV1(
            producer_step_id=first_step.step_id,
            output_binding_id=first_output.output_binding_id,
            output_declaration_sha256=first_output.sha256,
            sha256=ZERO_SHA256,
        )
    )
    second_output = _hashed(
        OutputDeclarationV1(
            output_binding_id=proposal.steps[1].output_bindings[0],
            source_kind="RESULT_PAYLOAD",
            json_pointer="/verified",
            value_schema_sha256=H,
            sha256=ZERO_SHA256,
        )
    )
    second_step = _hashed(
        StepExecutionBindingV1(
            step_id=legacy_plan.steps[1].step_id,
            candidate_id=proposal.steps[1].candidate_id,
            candidate_binding_sha256=second_candidate.binding_sha256,
            action_id=legacy_plan.steps[1].action_id,
            action_version=legacy_plan.steps[1].action_version,
            source_revision=second_candidate.source_revision,
            argument_schema_sha256=second_candidate.primitive.argument_schema_sha256,
            result_schema_sha256=second_candidate.primitive.result_schema_sha256,
            permission=permission_by_action[legacy_plan.steps[1].action_id],
            permission_sha256=permission_by_action[
                legacy_plan.steps[1].action_id
            ].permission_sha256,
            depends_on=legacy_plan.steps[1].depends_on,
            target_skeleton=str(workspace_root / "artifact-001"),
            args_skeleton={"upstream_artifact_id": None},
            argument_slots=(
                _hashed(
                    ArgumentSlotV1(
                        destination_json_pointer="/upstream_artifact_id",
                        value_binding=upstream_reference,
                        sha256=ZERO_SHA256,
                    )
                ),
            ),
            output_declarations=(second_output,),
            sha256=ZERO_SHA256,
        )
    )
    final_reference = _hashed(
        StepOutputValueBindingV1(
            producer_step_id=second_step.step_id,
            output_binding_id=second_output.output_binding_id,
            output_declaration_sha256=second_output.sha256,
            sha256=ZERO_SHA256,
        )
    )
    final_alias = _hashed(
        FinalOutputAliasV1(
            alias=proposal.output_bindings[0],
            value_binding=final_reference,
            sha256=ZERO_SHA256,
        )
    )
    return {
        "proposal": proposal,
        "candidates": candidates,
        "context": context,
        "legacy_plan": legacy_plan,
        "validation": validation,
        "action_registry": action_registry,
        "verification_registry": verification_registry,
        "verification_bindings": verification_bindings,
        "shadow": shadow,
        "plan_inputs": (plan_input,),
        "step_bindings": (first_step, second_step),
        "final_output_aliases": (final_alias,),
        "workspace": _workspace(workspace_root),
    }


def _registration_record(material: dict) -> LimitedActivationStoreRecord:
    registration = compile_limited_activation_registration(
        material["shadow"],
        plan=material["legacy_plan"],
        validation=material["validation"],
        action_registry=material["action_registry"],
        verification_registry=material["verification_registry"],
        verification_bindings=material["verification_bindings"],
        current_world_state_sha256=material["legacy_plan"].world_state_sha256,
        expected_principal_scope_hash=material[
            "legacy_plan"
        ].principal_scope_hash,
        registered_at_ms=1_600,
    )
    lifecycle_sha256 = computed_limited_activation_lifecycle_sha256(
        registration_id=registration.registration_id,
        registration_sha256=registration.registration_sha256,
        state="ACTIVE",
        expires_at_ms=registration.expires_at_ms,
        expired_at_ms=None,
    )
    return LimitedActivationStoreRecord(
        registration=registration,
        verification_plan_activation_id="vpa_p7c0_fixture",
        state="ACTIVE",
        expired_at_ms=None,
        lifecycle_sha256=lifecycle_sha256,
    )


def _synthetic_registration_record(material: dict) -> LimitedActivationStoreRecord:
    """Build a valid non-authorizing row to test P7C.0 defense in depth.

    Risky fixtures are intentionally ineligible for the real P7B compiler. The
    P7C.0 compiler must still reject them even if a caller presents a
    self-consistent registration-shaped object.
    """

    shadow = material["shadow"]
    activation = shadow.activation_contract
    verification = shadow.verification_plan
    registration = LimitedCompositionActivationRegistrationV1(
        registration_id="car_" + ZERO_SHA256,
        shadow_proposal_sha256=shadow.proposal_sha256,
        differential_trace_sha256=shadow.differential_trace.trace_sha256,
        composition_activation_id=activation.composition_activation_id,
        composition_activation_sha256=activation.activation_sha256,
        composition_plan_id=activation.composition_plan_id,
        composition_plan_sha256=activation.composition_plan_sha256,
        verification_plan_id=verification.verification_plan_id,
        verification_plan_sha256=verification.plan_sha256,
        validation_mode=shadow.validation_mode,
        validation_sha256=shadow.validation_sha256,
        request_id=activation.request_id,
        run_id=activation.run_id,
        generation=activation.generation,
        principal_scope_hash=activation.principal_scope_hash,
        world_state_sha256=activation.world_state_sha256,
        source_manifest_sha256=activation.source_manifest_sha256,
        capability_manifest_sha256=activation.capability_manifest_sha256,
        action_registry_sha256=shadow.action_registry_sha256,
        verification_registry_sha256=shadow.verification_registry_sha256,
        allowed_action_ids=activation.allowed_action_ids,
        allowed_action_versions=activation.allowed_action_versions,
        issued_at_ms=activation.issued_at_ms,
        expires_at_ms=activation.expires_at_ms,
        registered_at_ms=1_600,
        provisional_verification_required=(
            shadow.validation_mode == "PROVISIONAL_UNKNOWN"
        ),
        registration_sha256=ZERO_SHA256,
    ).with_computed_identity()
    lifecycle_sha256 = computed_limited_activation_lifecycle_sha256(
        registration_id=registration.registration_id,
        registration_sha256=registration.registration_sha256,
        state="ACTIVE",
        expires_at_ms=registration.expires_at_ms,
        expired_at_ms=None,
    )
    return LimitedActivationStoreRecord(
        registration=registration,
        verification_plan_activation_id="vpa_p7c0_synthetic",
        state="ACTIVE",
        expired_at_ms=None,
        lifecycle_sha256=lifecycle_sha256,
    )


def _compile_executable(material: dict, registration_record=None):
    return compile_executable_composition_plan(
        material["proposal"],
        material["candidates"],
        material["context"],
        material["action_registry"],
        legacy_plan=material["legacy_plan"],
        plan_inputs=material["plan_inputs"],
        step_bindings=material["step_bindings"],
        final_output_aliases=material["final_output_aliases"],
        workspace=material["workspace"],
        registration_record=(
            _registration_record(material)
            if registration_record is None
            else registration_record
        ),
    )


def _persist_executable(
    store: GatewayStateStore,
    material: dict,
    *,
    recorded_at_ms: int = 1_600,
):
    return store.register_executable_composition_plan_bundle(
        material["shadow"],
        plan=material["legacy_plan"],
        validation=material["validation"],
        action_registry=material["action_registry"],
        verification_registry=material["verification_registry"],
        verification_bindings=material["verification_bindings"],
        current_world_state_sha256=material["legacy_plan"].world_state_sha256,
        expected_principal_scope_hash=material[
            "legacy_plan"
        ].principal_scope_hash,
        composition_proposal=material["proposal"],
        candidates=material["candidates"],
        compile_context=material["context"],
        plan_inputs=material["plan_inputs"],
        step_bindings=material["step_bindings"],
        final_output_aliases=material["final_output_aliases"],
        workspace=material["workspace"],
        recorded_at_ms=recorded_at_ms,
    )


def _persist_registration_only(
    store: GatewayStateStore,
    material: dict,
    *,
    recorded_at_ms: int = 1_600,
):
    return store.register_limited_composition_activation_bundle(
        material["shadow"],
        plan=material["legacy_plan"],
        validation=material["validation"],
        action_registry=material["action_registry"],
        verification_registry=material["verification_registry"],
        verification_bindings=material["verification_bindings"],
        current_world_state_sha256=material["legacy_plan"].world_state_sha256,
        expected_principal_scope_hash=material[
            "legacy_plan"
        ].principal_scope_hash,
        recorded_at_ms=recorded_at_ms,
    )


@contextmanager
def _temporarily_disable_trigger_for_corruption(
    connection: sqlite3.Connection,
    trigger_name: str,
):
    """Model out-of-band row corruption while restoring the expected schema."""

    assert trigger_name in _CORRUPTION_TEST_TRIGGERS
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
        (trigger_name,),
    ).fetchone()
    assert row is not None
    trigger_sql = str(row[0])
    connection.execute(f'DROP TRIGGER "{trigger_name}"')
    try:
        yield
    finally:
        connection.execute(trigger_sql)


def _replace_inbound_attachments(
    store: GatewayStateStore,
    material: dict,
    attachments: tuple[AttachmentRef, ...],
) -> None:
    request_id = material["legacy_plan"].request_id
    row = store._connection.execute(
        "SELECT envelope_json FROM request_inbound_payload WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    envelope = InboundEnvelope.model_validate_json(row[0], strict=True).model_copy(
        update={"attachments": attachments}
    )
    payload = envelope.model_dump(mode="json")
    store._connection.execute(
        "UPDATE request_inbound_payload "
        "SET envelope_json = ?, envelope_sha256 = ? WHERE request_id = ?",
        (
            canonical_json_bytes(payload).decode("utf-8"),
            canonical_sha256(payload),
            request_id,
        ),
    )


def _add_object_input(material: dict, grant: ObjectGrant) -> None:
    object_input = _hashed(
        PlanInputV1(
            input_id="input.object",
            input_kind="OBJECT_GRANT",
            object_grant=grant,
            value_schema_sha256=H,
            value_sha256=grant.sha256,
            sha256=ZERO_SHA256,
        )
    )
    reference = _hashed(
        PlanInputValueBindingV1(
            input_id=object_input.input_id,
            input_sha256=object_input.sha256,
            json_pointer="",
            sha256=ZERO_SHA256,
        )
    )
    first = material["step_bindings"][0]
    mode_slot = next(
        item
        for item in first.argument_slots
        if item.destination_json_pointer == "/mode"
    )
    replacement = _hashed(
        mode_slot.model_copy(
            update={"value_binding": reference, "sha256": ZERO_SHA256}
        )
    )
    slots = tuple(
        replacement if item.destination_json_pointer == "/mode" else item
        for item in first.argument_slots
    )
    material["plan_inputs"] = tuple(
        sorted(material["plan_inputs"] + (object_input,), key=lambda item: item.input_id)
    )
    material["step_bindings"] = (
        _hashed(
            first.model_copy(
                update={"argument_slots": slots, "sha256": ZERO_SHA256}
            )
        ),
        material["step_bindings"][1],
    )


def _prepare_exact_object_input(
    store: GatewayStateStore,
    material: dict,
    *,
    sha256: str = "d" * 64,
) -> tuple[AttachmentRef, ObjectGrant]:
    request_id = material["legacy_plan"].request_id
    inbound_row = store._connection.execute(
        "SELECT envelope_json FROM request_inbound_payload WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    envelope = InboundEnvelope.model_validate_json(inbound_row[0], strict=True)
    attachment = AttachmentRef(
        object_id="oref_" + "c" * 64,
        revision=1,
        sha256=sha256,
        size_bytes=17,
        mime="text/plain",
        filename="evidence.txt",
        tenant_id=envelope.tenant_id,
        link_account_id=envelope.link_account_id,
        conversation_scope_hash=envelope.conversation_scope_hash,
        source_message_ref=envelope.channel_message_ref,
        created_at_ms=1_000,
    )
    _replace_inbound_attachments(store, material, (attachment,))
    grant = ObjectGrant(
        object_id=attachment.object_id,
        revision=attachment.revision,
        sha256=attachment.sha256,
        size_bytes=attachment.size_bytes,
        mime=attachment.mime,
        tenant_id=attachment.tenant_id,
        link_account_id=attachment.link_account_id,
        conversation_scope_hash=attachment.conversation_scope_hash,
    )
    _add_object_input(material, grant)
    return attachment, grant


def _downgrade_v31_to_v30(path: Path) -> None:
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 31
        connection.execute(
            "DROP TRIGGER IF EXISTS "
            "composition_executable_plan_immutable_delete_guard"
        )
        connection.execute(
            "DROP TRIGGER IF EXISTS "
            "composition_executable_plan_immutable_update_guard"
        )
        connection.execute(
            "DROP TRIGGER IF EXISTS "
            "composition_executable_plan_identity_insert_guard"
        )
        connection.execute(
            "DROP TRIGGER IF EXISTS object_owners_request_owner_insert_guard"
        )
        connection.execute(
            "DROP TRIGGER IF EXISTS object_owners_immutable_delete_guard"
        )
        connection.execute(
            "DROP TRIGGER IF EXISTS object_owners_immutable_update_guard"
        )
        connection.execute(
            "DROP TRIGGER IF EXISTS object_owners_identity_insert_guard"
        )
        connection.execute(
            "DROP TRIGGER IF EXISTS object_owners_object_sha256_insert_guard"
        )
        connection.execute(
            "DROP TRIGGER IF EXISTS "
            "composition_activation_executable_plan_required_monotonic"
        )
        connection.execute(
            "DROP INDEX IF EXISTS composition_executable_plan_expiry_idx"
        )
        connection.execute(
            "DROP INDEX IF EXISTS composition_executable_plan_lineage_idx"
        )
        connection.execute("DROP TABLE IF EXISTS composition_executable_plan")
        connection.execute(
            "ALTER TABLE composition_activation_registration "
            "DROP COLUMN executable_plan_required"
        )
        connection.execute("DELETE FROM schema_migrations WHERE version = 31")
        connection.execute("PRAGMA user_version = 30")
    finally:
        connection.close()


def test_canonical_full_arguments_and_typed_step_output_roundtrip(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        first = _compile_executable(material)
        second = _compile_executable(material)

    assert first == second
    assert first.has_valid_identity()
    assert first.executable_plan_id.startswith("ecp_")
    assert first.legacy_plan == material["legacy_plan"]
    assert first.execution_bindings_sha256 != first.legacy_bindings_sha256
    assert first.authorizes is False
    assert first.confirms is False
    assert first.changes_risk is False
    assert first.may_execute is False
    assert first.schema_compatibility_proven is False
    assert first.dispatch_schema_validation_required is True
    assert first.path_policy_enforced is False
    assert first.dispatch_path_policy_validation_required is True

    first_step, second_step = first.step_bindings
    assert first_step.args_skeleton == {"artifact_id": None, "mode": None}
    assert isinstance(
        first_step.argument_slots[0].value_binding,
        PlanInputValueBindingV1,
    )
    assert isinstance(
        first_step.argument_slots[1].value_binding,
        LiteralValueBindingV1,
    )
    dynamic = second_step.argument_slots[0].value_binding
    assert isinstance(dynamic, StepOutputValueBindingV1)
    assert dynamic.producer_step_id == first_step.step_id
    assert dynamic.output_binding_id == first_step.output_declarations[0].output_binding_id
    assert dynamic.output_declaration_sha256 == first_step.output_declarations[0].sha256

    encoded = canonical_executable_plan_json(first)
    decoded = ExecutableCompositionPlanV1.model_validate_json(encoded, strict=True)
    assert decoded == first
    assert canonical_executable_plan_json(decoded) == encoded


def test_rehashed_caller_modified_legacy_plan_is_rejected_by_recompile(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-rehashed-legacy-plan.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        registration_record = _registration_record(material)

    original = material["legacy_plan"]
    forged = original.model_copy(
        update={
            "environment_class": "forged-environment",
            "plan_sha256": ZERO_SHA256,
        }
    )
    forged = forged.model_copy(
        update={"plan_sha256": computed_plan_sha256(forged)}
    )
    assert forged.plan_sha256 == computed_plan_sha256(forged)
    material["legacy_plan"] = forged

    with pytest.raises(
        ExecutableCompositionPlanError,
        match="executable_plan.legacy_plan_mismatch",
    ):
        _compile_executable(material, registration_record=registration_record)


@pytest.mark.parametrize("shape", ("missing", "extra", "reordered"))
def test_step_binding_cardinality_and_order_are_exact(
    tmp_path: Path,
    shape: str,
) -> None:
    path = tmp_path / f"gateway-step-shape-{shape}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)

    first, second = material["step_bindings"]
    material["step_bindings"] = {
        "missing": (first,),
        "extra": (first, second, first),
        "reordered": (second, first),
    }[shape]
    with pytest.raises(
        ExecutableCompositionPlanError,
        match="executable_plan.step.order_mismatch",
    ):
        _compile_executable(material)


def test_cyclic_proposal_is_rejected_before_executable_materialization(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-cyclic-proposal.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)

    proposal = material["proposal"]
    first, second = proposal.steps
    cyclic_first = first.model_copy(update={"depends_on": (second.step_id,)})
    cyclic = proposal.model_copy(
        update={
            "steps": (cyclic_first, second),
            "dependency_edges": tuple(
                sorted(
                    (
                        (second.step_id, first.step_id),
                        (first.step_id, second.step_id),
                    )
                )
            ),
            "control_flow": "DAG",
            "proposal_sha256": ZERO_SHA256,
        }
    )
    cyclic = cyclic.model_copy(
        update={"proposal_sha256": computed_proposal_sha256(cyclic)}
    )
    material["proposal"] = cyclic

    with pytest.raises(
        ExecutableCompositionPlanError,
        match="executable_plan.proposal.dependency_cycle",
    ):
        _compile_executable(material)


@pytest.mark.parametrize(
    "drift",
    (
        "action_id",
        "action_version",
        "candidate_binding",
        "argument_schema",
        "result_schema",
        "source_revision",
    ),
)
def test_public_bundle_rejects_rehashed_candidate_and_schema_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    path = tmp_path / f"gateway-authority-drift-{drift}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        first, second = material["step_bindings"]
        updates = {
            "action_id": {"action_id": second.action_id},
            "action_version": {"action_version": "999.0"},
            "candidate_binding": {"candidate_binding_sha256": "e" * 64},
            "argument_schema": {"argument_schema_sha256": "e" * 64},
            "result_schema": {"result_schema_sha256": "e" * 64},
            "source_revision": {
                "source_revision": first.source_revision.model_copy(
                    update={"source_sha256": "e" * 64}
                )
            },
        }[drift]
        forged_first = _hashed(
            first.model_copy(update={**updates, "sha256": ZERO_SHA256})
        )
        material["step_bindings"] = (forged_first, second)

        with pytest.raises(
            ExecutableCompositionPlanError,
            match="executable_plan.step.binding_mismatch",
        ):
            _persist_executable(store, material)

        for table in (
            "verification_registry_snapshot",
            "verification_plan",
            "verification_plan_activation",
            "composition_activation_registration",
            "composition_executable_plan",
        ):
            assert store._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0] == 0
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_step_output_reference_is_hash_bound_and_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)

    second = material["step_bindings"][1]
    slot = second.argument_slots[0]
    drifted_reference = _hashed(
        slot.value_binding.model_copy(
            update={
                "output_declaration_sha256": "f" * 64,
                "sha256": ZERO_SHA256,
            }
        )
    )
    drifted_slot = _hashed(
        slot.model_copy(
            update={"value_binding": drifted_reference, "sha256": ZERO_SHA256}
        )
    )
    drifted_second = _hashed(
        second.model_copy(
            update={"argument_slots": (drifted_slot,), "sha256": ZERO_SHA256}
        )
    )
    material["step_bindings"] = (
        material["step_bindings"][0],
        drifted_second,
    )
    with pytest.raises(
        ExecutableCompositionPlanError,
        match="executable_plan.materialization_invalid",
    ):
        _compile_executable(material)


def test_p7c0_compiler_and_codec_have_no_second_execution_authority() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "total_gateway"
    paths = (
        root / "composition_executable_plan.py",
        root / "composition_executable_plan_store.py",
    )
    forbidden_names = {
        "GatewayStateStore",
        "PolicyDecision",
        "PolicyEngine",
        "ExecutionTicket",
        "ExecutionTicketAuthority",
        "OmniCapabilityGrant",
        "OmniGrantAuthority",
        "BackendClient",
        "BodyRuntime",
        "ExecutionEngine",
        "GatewayOrchestrationWorker",
        "VerificationPlanExecutor",
        "CompletionDecision",
        "CompletionGate",
    }
    forbidden_calls = {
        "authorize",
        "dispatch",
        "execute",
        "issue",
        "issue_ticket",
        "record_execution",
        "run_omni_body",
    }
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        calls = {
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        classes = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }
        assert forbidden_names.isdisjoint(imported | classes | calls)
        assert forbidden_calls.isdisjoint(calls)
        assert "sqlite3.connect" not in source

    existing_store = (root / "store.py").read_text(encoding="utf-8")
    assert existing_store.count("class GatewayStateStore:") == 1


_P7C0_FORBIDDEN_AUTHORITY_SYMBOLS = frozenset(
    {
        "PolicyDecision",
        "PolicyEngine",
        "ExecutionTicket",
        "ExecutionTicketPayload",
        "TicketSigner",
        "RuntimeTicketAuthority",
        "OmniCapabilityGrant",
        "OmniCapabilityGrantPayload",
        "OmniGrantAuthority",
        "SkillActivationGrant",
        "EffectClaim",
        "EffectResult",
        "FactRecord",
        "FactLedger",
        "BodyRuntime",
        "ExecutionEngine",
        "VerificationPlanExecutor",
        "CompletionDecision",
        "CompletionGate",
    }
)

_P7C0_FORBIDDEN_AUTHORITY_CALLS = frozenset(
    {
        "evaluate",
        "sign_execution",
        "sign_omni_capability",
        "issue_omni_capability_grant",
        "bind_skill_activation_ticket",
        "claim_effect",
        "mark_effect_started",
        "complete_effect",
        "record_effect_reconciliation",
        "continue_effect_after_pna",
        "admit_sub_effect",
        "_append_effect_fact_locked",
        "put_effect_outcome_head",
        "put_effect_reconciliation_record",
        "record_execution",
        "acquire_dispatch_permit",
        "create_dispatch_marker",
        "mark_dispatch_marker_dispatched",
        "put_verification_record",
        "put_verification_readiness",
        "put_verification_failure_evidence",
        "put_verification_disposition",
        "put_repair_directive",
        "put_repair_attempt",
        "put_verification_subject_successor",
        "reserve_repair_execution",
        "start_repair_execution",
        "complete_repair_execution",
        "record_completion_decision",
    }
)

_P7C0_FORBIDDEN_AUTHORITY_TABLES = frozenset(
    {
        "effect_ledger",
        "effect_attempts",
        "effect_facts",
        "effect_outcome_head",
        "effect_reconciliation",
        "fact_ledger",
        "execution_fact_batches",
        "skill_activation_tickets",
        "dispatch_permit_release",
        "verification_record",
        "verification_readiness",
        "verification_failure_evidence",
        "verification_disposition",
        "repair_directive",
        "repair_attempt",
        "verification_subject_successor",
        "repair_execution_binding",
        "completion_decisions",
    }
)

_P7C0_NESTED_SCOPE_TYPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
)


def _p7c0_normalize_sql(value: str) -> str:
    """Normalize SQL layout while preserving quoted token semantics."""

    punctuation = frozenset("(),.*=<>!?+-/%;")
    tokens: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character.isspace():
            index += 1
            continue
        if character in {"'", '"', "`"}:
            quote = character
            start = index
            index += 1
            while index < len(value):
                if value[index] != quote:
                    index += 1
                    continue
                if index + 1 < len(value) and value[index + 1] == quote:
                    index += 2
                    continue
                index += 1
                break
            else:
                raise AssertionError("unterminated quoted SQL token")
            tokens.append(value[start:index])
            continue
        if character == "[":
            start = index
            index += 1
            while index < len(value) and value[index] != "]":
                index += 1
            assert index < len(value), "unterminated bracketed SQL identifier"
            index += 1
            tokens.append(value[start:index])
            continue
        if character in punctuation:
            if (
                index + 1 < len(value)
                and value[index : index + 2] in {"<=", ">=", "!=", "<>"}
            ):
                tokens.append(value[index : index + 2])
                index += 2
            else:
                tokens.append(character)
                index += 1
            continue
        start = index
        while (
            index < len(value)
            and not value[index].isspace()
            and value[index] not in punctuation
            and value[index] not in {"'", '"', "`", "["}
        ):
            index += 1
        tokens.append(value[start:index].lower())
    return " ".join(tokens)


def _p7c0_qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _p7c0_qualified_name(node.value)
        if owner is not None:
            return f"{owner}.{node.attr}"
    return None


def _p7c0_call_name(node: ast.Call) -> str | None:
    direct = _p7c0_qualified_name(node.func)
    if direct is not None:
        return direct
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
        owner = _p7c0_qualified_name(node.func.value.func)
        if owner is not None:
            return f"{owner}().{node.func.attr}"
    return None


def _p7c0_direct_scope_nodes(
    scope: ast.FunctionDef,
    *,
    reject_nested: bool,
) -> tuple[ast.AST, ...]:
    nodes: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        if isinstance(node, _P7C0_NESTED_SCOPE_TYPES):
            if reject_nested:
                raise AssertionError(
                    f"{scope.name} contains nested executable scope"
                )
            continue
        nodes.append(node)
        pending.extend(ast.iter_child_nodes(node))
    return tuple(nodes)


def _p7c0_first_nested_scopes(
    scope: ast.FunctionDef,
) -> tuple[ast.AST, ...]:
    nested: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        if isinstance(node, _P7C0_NESTED_SCOPE_TYPES):
            nested.append(node)
            continue
        pending.extend(ast.iter_child_nodes(node))
    return tuple(nested)


def _p7c0_call_counter(nodes: tuple[ast.AST, ...]) -> Counter[str]:
    calls: Counter[str] = Counter()
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        name = _p7c0_call_name(node)
        assert name is not None, "dynamic callee is forbidden in P7C.0 persistence"
        calls[name] += 1
    return calls


def _p7c0_import_counter(
    nodes: tuple[ast.AST, ...],
) -> Counter[tuple[str, int, str, str, str | None]]:
    imports: Counter[tuple[str, int, str, str, str | None]] = Counter()
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[("import", 0, "", alias.name, alias.asname)] += 1
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports[
                    (
                        "from",
                        node.level,
                        node.module or "",
                        alias.name,
                        alias.asname,
                    )
                ] += 1
    return imports


def _p7c0_assert_plain_function(scope: ast.FunctionDef) -> None:
    assert not scope.decorator_list
    assert not getattr(scope, "type_params", ())


def _p7c0_assert_plain_class(scope: ast.ClassDef) -> None:
    assert not scope.decorator_list
    assert not scope.bases
    assert not scope.keywords
    assert not getattr(scope, "type_params", ())


def _p7c0_runtime_binding_inventory(
    statements: list[ast.stmt],
    names: frozenset[str],
) -> dict[str, Counter[tuple[object, ...]]]:
    bindings = {name: Counter() for name in names}

    def record(name: str | None, descriptor: tuple[object, ...]) -> None:
        if name in bindings:
            bindings[name][descriptor] += 1

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            record(node.name, (type(node).__name__,))
            return
        if isinstance(node, ast.Import):
            for alias in node.names:
                record(
                    alias.asname or alias.name.split(".", 1)[0],
                    ("Import", alias.name, alias.asname),
                )
            return
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                record(
                    alias.asname or alias.name,
                    (
                        "ImportFrom",
                        node.level,
                        node.module or "",
                        alias.name,
                        alias.asname,
                    ),
                )
            return
        if isinstance(node, ast.Name) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            record(node.id, (type(node.ctx).__name__,))
        elif isinstance(node, ast.ExceptHandler):
            record(node.name, ("ExceptHandler",))
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            record(node.name, (type(node).__name__,))
        elif isinstance(node, ast.MatchMapping):
            record(node.rest, ("MatchMapping",))
        if isinstance(node, ast.Lambda):
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in statements:
        visit(statement)
    return bindings


def _p7c0_sql_terminal(
    execute_call: ast.Call,
    nodes: tuple[ast.AST, ...],
) -> str:
    consumers = [
        candidate
        for candidate in nodes
        if isinstance(candidate, ast.Call)
        and isinstance(candidate.func, ast.Attribute)
        and candidate.func.value is execute_call
    ]
    assert len(consumers) <= 1
    if not consumers:
        return "execute"
    consumer = consumers[0]
    assert consumer.func.attr in {"fetchone", "fetchall"}
    assert not consumer.args and not consumer.keywords
    return consumer.func.attr


def _p7c0_assert_sql_surface(
    nodes: tuple[ast.AST, ...],
    *,
    receiver: str,
    expected: Counter[tuple[str, str]],
) -> None:
    sql_surface: Counter[tuple[str, str]] = Counter()
    database_calls = [
        node
        for node in nodes
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
    ]
    for call in database_calls:
        assert _p7c0_qualified_name(call.func) == receiver
        assert call.func.attr == "execute"
        assert len(call.args) == 2 and not call.keywords
        statement, parameters = call.args
        assert isinstance(statement, ast.Constant)
        assert isinstance(statement.value, str)
        assert isinstance(parameters, ast.Tuple)
        assert all(not isinstance(item, ast.Starred) for item in parameters.elts)
        assert statement.value.count("?") == len(parameters.elts)
        sql_surface[
            (
                _p7c0_normalize_sql(statement.value),
                _p7c0_sql_terminal(call, nodes),
            )
        ] += 1
    assert sql_surface == expected


def _p7c0_assert_scope(
    scope: ast.FunctionDef,
    *,
    expected_calls: Counter[str],
    expected_imports: Counter[tuple[str, int, str, str, str | None]],
    sql_receiver: str,
    expected_sql: Counter[tuple[str, str]],
) -> tuple[ast.AST, ...]:
    _p7c0_assert_plain_function(scope)
    nodes = _p7c0_direct_scope_nodes(scope, reject_nested=True)
    calls = _p7c0_call_counter(nodes)
    assert calls == expected_calls
    assert _p7c0_import_counter(nodes) == expected_imports

    referenced = {
        node.id for node in nodes if isinstance(node, ast.Name)
    } | {node.attr for node in nodes if isinstance(node, ast.Attribute)}
    assert _P7C0_FORBIDDEN_AUTHORITY_SYMBOLS.isdisjoint(referenced)
    assert _P7C0_FORBIDDEN_AUTHORITY_CALLS.isdisjoint(
        name.rsplit(".", 1)[-1] for name in calls
    )
    assert all(
        not node.attr.startswith("__")
        for node in nodes
        if isinstance(node, ast.Attribute)
    )
    string_literals = {
        node.value.lower()
        for node in nodes
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert all(
        table not in literal
        for table in _P7C0_FORBIDDEN_AUTHORITY_TABLES
        for literal in string_literals
    )
    _p7c0_assert_sql_surface(
        nodes,
        receiver=sql_receiver,
        expected=expected_sql,
    )
    return nodes


_P7C0_ROOT_CALLS = Counter(
    {
        "ExecutableCompositionBundleRegistration": 1,
        "ExecutableCompositionPlanStoreRecord": 1,
        "ExecutableCompositionPlanV1.model_validate_json": 1,
        "StoreConflictError": 6,
        "StoreCorruptionError": 8,
        "ValueError": 4,
        "_verify_executable_composition_plan_authorities": 3,
        "_verify_executable_composition_plan_input_authorities": 1,
        "canonical_executable_plan_json": 2,
        "compile_executable_composition_plan": 1,
        "executable.has_valid_identity": 1,
        "executable_plan_record_from_row": 2,
        "existing_registration.active_at": 1,
        "len": 2,
        "new_registration.active_at": 1,
        "registration_record.active_at": 1,
        "self._assert_request_binding_locked": 1,
        "self._connection.execute": 6,
        "self._connection.execute().fetchall": 1,
        "self._connection.execute().fetchone": 3,
        "self._write_transaction": 1,
        "self.register_limited_composition_activation_bundle": 1,
    }
)

_P7C0_ROOT_IMPORTS = Counter(
    {
        (
            "from",
            1,
            "composition_executable_plan",
            "ExecutableCompositionPlanV1",
            None,
        ): 1,
        (
            "from",
            1,
            "composition_executable_plan",
            "compile_executable_composition_plan",
            None,
        ): 1,
    }
)

_P7C0_ROOT_SQL = Counter(
    {
        (
            _p7c0_normalize_sql(
                "SELECT * FROM composition_executable_plan "
                "WHERE registration_id = ?"
            ),
            "fetchone",
        ): 1,
        (
            _p7c0_normalize_sql(
                "SELECT executable_plan_required "
                "FROM composition_activation_registration "
                "WHERE registration_id = ?"
            ),
            "fetchone",
        ): 1,
        (
            _p7c0_normalize_sql(
                "SELECT * FROM composition_executable_plan "
                "WHERE executable_plan_id = ? OR registration_id = ? "
                "OR composition_activation_id = ? OR composition_plan_id = ? "
                "OR executable_plan_sha256 = ?"
            ),
            "fetchall",
        ): 1,
        (
            _p7c0_normalize_sql(
                "UPDATE composition_activation_registration "
                "SET executable_plan_required = 1 "
                "WHERE registration_id = ? AND executable_plan_required = 0"
            ),
            "execute",
        ): 1,
        (
            _p7c0_normalize_sql(
                """
                INSERT INTO composition_executable_plan(
                    executable_plan_id, registration_id, registration_sha256,
                    composition_activation_id, composition_activation_sha256,
                    composition_plan_id, composition_plan_sha256,
                    execution_bindings_sha256, action_registry_sha256,
                    verification_registry_sha256, verification_plan_id,
                    verification_plan_sha256, request_id, run_id, generation,
                    principal_scope_hash, world_state_sha256,
                    source_manifest_sha256, capability_manifest_sha256,
                    workspace_id, workspace_scope_hash, sealed_at_ms,
                    expires_at_ms, step_count, executable_plan_json,
                    executable_plan_sha256
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """
            ),
            "execute",
        ): 1,
        (
            _p7c0_normalize_sql(
                "SELECT * FROM composition_executable_plan "
                "WHERE executable_plan_id = ?"
            ),
            "fetchone",
        ): 1,
    }
)

_P7C0_HELPER_CALLS = {
    "_verify_executable_composition_plan_authorities": Counter(
        {
            "RegistrySnapshot.model_validate_json": 1,
            "StoreCorruptionError": 12,
            "VerificationPlan.model_validate_json": 1,
            "_verify_executable_composition_plan_input_authorities": 1,
            "canonical_sha256": 2,
            "connection.execute": 4,
            "connection.execute().fetchone": 4,
            "limited_activation_record_from_row": 1,
            "registry.has_valid_identity": 1,
            "sorted": 1,
            "tuple": 2,
            "verification_plan.has_valid_identity": 1,
            "zip": 1,
        }
    ),
    "_verify_executable_composition_plan_input_authorities": Counter(
        {
            "StoreCorruptionError": 3,
            "_assert_object_content_identity_locked": 1,
            "_object_owner_from_row": 2,
            "_object_ownership_sha256": 1,
            "_parse_inbound_envelope": 1,
            "any": 3,
            "attachments.get": 1,
            "connection.execute": 4,
            "connection.execute().fetchall": 1,
            "connection.execute().fetchone": 2,
            "failure": 7,
            "len": 3,
            "tuple": 2,
        }
    ),
    "_assert_object_content_identity_locked": Counter(
        {
            "StoreCorruptionError": 1,
            "connection.execute": 1,
            "connection.execute().fetchall": 1,
            "len": 1,
            "mismatch_error": 1,
        }
    ),
    "_object_owner_from_row": Counter(
        {
            "ObjectOwnerRecord": 1,
            "StoreCorruptionError": 1,
            "_object_ownership_sha256": 1,
        }
    ),
    "_object_ownership_sha256": Counter({"canonical_sha256": 1}),
    "_parse_inbound_envelope": Counter(
        {
            "InboundEnvelope.model_validate_json": 1,
            "StoreCorruptionError": 2,
            "_inbound_envelope_payload": 1,
        }
    ),
    "_inbound_envelope_payload": Counter(
        {
            "canonical_json_bytes": 1,
            "canonical_json_bytes().decode": 1,
            "canonical_sha256": 1,
            "envelope.model_dump": 1,
        }
    ),
}

_P7C0_HELPER_EDGES = {
    "_verify_executable_composition_plan_authorities": Counter(
        {"_verify_executable_composition_plan_input_authorities": 1}
    ),
    "_verify_executable_composition_plan_input_authorities": Counter(
        {
            "_assert_object_content_identity_locked": 1,
            "_object_owner_from_row": 2,
            "_object_ownership_sha256": 1,
            "_parse_inbound_envelope": 1,
        }
    ),
    "_assert_object_content_identity_locked": Counter(),
    "_object_owner_from_row": Counter({"_object_ownership_sha256": 1}),
    "_object_ownership_sha256": Counter(),
    "_parse_inbound_envelope": Counter({"_inbound_envelope_payload": 1}),
    "_inbound_envelope_payload": Counter(),
}

_P7C0_HELPER_IMPORTS = {
    name: Counter() for name in _P7C0_HELPER_CALLS
}
_P7C0_HELPER_IMPORTS["_verify_executable_composition_plan_authorities"] = Counter(
    {("from", 0, "contracts.verification", "VerificationPlan", None): 1}
)

_P7C0_HELPER_SQL = {
    name: Counter() for name in _P7C0_HELPER_CALLS
}
_P7C0_HELPER_SQL["_verify_executable_composition_plan_authorities"] = Counter(
    {
        (
            _p7c0_normalize_sql(
                "SELECT * FROM composition_activation_registration "
                "WHERE registration_id = ?"
            ),
            "fetchone",
        ): 1,
        (
            _p7c0_normalize_sql(
                "SELECT * FROM verification_registry_snapshot "
                "WHERE snapshot_sha256 = ?"
            ),
            "fetchone",
        ): 1,
        (
            _p7c0_normalize_sql(
                "SELECT * FROM verification_plan WHERE verification_plan_id = ?"
            ),
            "fetchone",
        ): 1,
        (
            _p7c0_normalize_sql(
                "SELECT * FROM verification_plan_activation "
                "WHERE activation_id = ?"
            ),
            "fetchone",
        ): 1,
    }
)
_P7C0_HELPER_SQL[
    "_verify_executable_composition_plan_input_authorities"
] = Counter(
    {
        (
            _p7c0_normalize_sql(
                "SELECT * FROM request_inbound_payload WHERE request_id = ?"
            ),
            "fetchone",
        ): 1,
        (
            _p7c0_normalize_sql(
                "SELECT * FROM object_owners "
                "WHERE object_id = ? AND owner_kind = 'REQUEST' "
                "AND request_id = ? ORDER BY owner_id"
            ),
            "fetchall",
        ): 1,
        (
            _p7c0_normalize_sql(
                """
                INSERT INTO object_owners(
                    object_id, object_sha256, owner_kind, owner_id,
                    request_id, run_id, generation, created_at_ms,
                    ownership_sha256
                ) VALUES (?, ?, 'REQUEST', ?, ?, ?, ?, ?, ?)
                """
            ),
            "execute",
        ): 1,
        (
            _p7c0_normalize_sql(
                "SELECT * FROM object_owners "
                "WHERE object_id = ? AND owner_kind = 'REQUEST' AND owner_id = ?"
            ),
            "fetchone",
        ): 1,
    }
)
_P7C0_HELPER_SQL["_assert_object_content_identity_locked"] = Counter(
    {
        (
            _p7c0_normalize_sql(
                "SELECT DISTINCT object_sha256 FROM object_owners "
                "WHERE object_id = ? ORDER BY object_sha256"
            ),
            "fetchall",
        ): 1
    }
)

_P7C0_P7B_BOUNDARY_CALLS = Counter(
    {
        "LimitedActivationBundleRegistration": 1,
        "LimitedCompositionActivationRegistrar": 1,
        "LimitedCompositionActivationRegistrar().register": 1,
        "StoreCorruptionError": 3,
        "_BundleRegistrationPort": 1,
        "compile_limited_activation_registration": 1,
        "limited_activation_record_from_row": 1,
        "self._connection.execute": 1,
        "self._connection.execute().fetchone": 1,
        "self._write_transaction": 1,
        "self.activate_verification_plan": 1,
        "self.put_registry_snapshot": 1,
        "self.put_verification_plan": 1,
    }
)

_P7C0_P7B_SELF_CALLS = Counter(
    {
        "self._connection.execute": 1,
        "self._write_transaction": 1,
        "self.activate_verification_plan": 1,
        "self.put_registry_snapshot": 1,
        "self.put_verification_plan": 1,
    }
)

_P7C0_P7B_BOUNDARY_IMPORTS = Counter(
    {
        (
            "from",
            1,
            "composition_activation_registration",
            "EXISTING_GATEWAY_STATE_STORE_AUTHORITY",
            None,
        ): 1,
        (
            "from",
            1,
            "composition_activation_registration",
            "LimitedCompositionActivationRegistrar",
            None,
        ): 1,
        (
            "from",
            1,
            "composition_activation_registration",
            "compile_limited_activation_registration",
            None,
        ): 1,
    }
)

_P7C0_P7B_BOUNDARY_SQL = Counter(
    {
        (
            _p7c0_normalize_sql(
                "SELECT * FROM composition_activation_registration "
                "WHERE registration_id = ?"
            ),
            "fetchone",
        ): 1
    }
)

_P7C0_P7B_PORT_CALLS = {
    "__init__": Counter(),
    "get_limited_activation_registration": Counter(
        {
            "StoreConflictError": 1,
            "self._owner.get_limited_activation_registration": 1,
        }
    ),
    "put_limited_activation_registration": Counter(
        {
            "StoreConflictError": 1,
            "self._owner._put_limited_activation_registration_from_bundle": 1,
        }
    ),
}

_P7C0_MODULE_BINDINGS: dict[str, Counter[tuple[object, ...]]] = {
    "GatewayStateStore": Counter({("ClassDef",): 1}),
    "_verify_executable_composition_plan_authorities": Counter(
        {("FunctionDef",): 1}
    ),
    "_verify_executable_composition_plan_input_authorities": Counter(
        {("FunctionDef",): 1}
    ),
    "_assert_object_content_identity_locked": Counter({("FunctionDef",): 1}),
    "_object_owner_from_row": Counter({("FunctionDef",): 1}),
    "_object_ownership_sha256": Counter({("FunctionDef",): 1}),
    "_parse_inbound_envelope": Counter({("FunctionDef",): 1}),
    "_inbound_envelope_payload": Counter({("FunctionDef",): 1}),
    "ExecutableCompositionBundleRegistration": Counter(
        {
            (
                "ImportFrom",
                1,
                "composition_executable_plan_store",
                "ExecutableCompositionBundleRegistration",
                None,
            ): 1
        }
    ),
    "ExecutableCompositionPlanStoreRecord": Counter(
        {
            (
                "ImportFrom",
                1,
                "composition_executable_plan_store",
                "ExecutableCompositionPlanStoreRecord",
                None,
            ): 1
        }
    ),
    "canonical_executable_plan_json": Counter(
        {
            (
                "ImportFrom",
                1,
                "composition_executable_plan_store",
                "canonical_executable_plan_json",
                None,
            ): 1
        }
    ),
    "executable_plan_record_from_row": Counter(
        {
            (
                "ImportFrom",
                1,
                "composition_executable_plan_store",
                "executable_plan_record_from_row",
                None,
            ): 1
        }
    ),
    "InboundEnvelope": Counter(
        {("ImportFrom", 0, "contracts", "InboundEnvelope", None): 1}
    ),
    "canonical_json_bytes": Counter(
        {("ImportFrom", 0, "contracts", "canonical_json_bytes", None): 1}
    ),
    "canonical_sha256": Counter(
        {("ImportFrom", 0, "contracts", "canonical_sha256", None): 1}
    ),
    "RegistrySnapshot": Counter(
        {
            (
                "ImportFrom",
                0,
                "contracts.verification",
                "RegistrySnapshot",
                None,
            ): 1
        }
    ),
    "LimitedActivationBundleRegistration": Counter(
        {
            (
                "ImportFrom",
                1,
                "composition_activation_store",
                "LimitedActivationBundleRegistration",
                None,
            ): 1
        }
    ),
    "limited_activation_record_from_row": Counter(
        {
            (
                "ImportFrom",
                1,
                "composition_activation_store",
                "limited_activation_record_from_row",
                None,
            ): 1
        }
    ),
    "StoreCorruptionError": Counter({("ClassDef",): 1}),
    "StoreConflictError": Counter({("ClassDef",): 1}),
    "ObjectOwnerRecord": Counter({("ClassDef",): 1}),
    "ValueError": Counter(),
    "any": Counter(),
    "len": Counter(),
    "sorted": Counter(),
    "tuple": Counter(),
    "zip": Counter(),
}


def _p7c0_store_tree() -> ast.Module:
    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "total_gateway"
        / "store.py"
    )
    return ast.parse(path.read_text(encoding="utf-8"))


def _p7c0_store_class(tree: ast.Module) -> ast.ClassDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "GatewayStateStore"
    ]
    assert len(matches) == 1
    return matches[0]


def _p7c0_store_method(tree: ast.Module, name: str) -> ast.FunctionDef:
    store_class = _p7c0_store_class(tree)
    matches = [
        node
        for node in store_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(matches) == 1
    assert isinstance(matches[0], ast.FunctionDef)
    return matches[0]


def _p7c0_module_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(matches) == 1
    assert isinstance(matches[0], ast.FunctionDef)
    return matches[0]


def _assert_p7c0_register_bundle_is_persistence_only(tree: ast.Module) -> None:
    assert _p7c0_runtime_binding_inventory(
        tree.body, frozenset(_P7C0_MODULE_BINDINGS)
    ) == _P7C0_MODULE_BINDINGS
    store_class = _p7c0_store_class(tree)
    _p7c0_assert_plain_class(store_class)
    assert all(isinstance(node, ast.FunctionDef) for node in store_class.body)
    store_method_names = [node.name for node in store_class.body]
    assert len(store_method_names) == len(set(store_method_names))

    method = _p7c0_store_method(
        tree, "register_executable_composition_plan_bundle"
    )
    assert not method.args.posonlyargs
    assert [item.arg for item in method.args.args] == ["self", "proposal"]
    assert method.args.vararg is None and method.args.kwarg is None
    assert not method.args.defaults
    assert all(item is None for item in method.args.kw_defaults)
    assert [item.arg for item in method.args.kwonlyargs] == [
        "plan",
        "validation",
        "action_registry",
        "verification_registry",
        "verification_bindings",
        "current_world_state_sha256",
        "expected_principal_scope_hash",
        "composition_proposal",
        "candidates",
        "compile_context",
        "plan_inputs",
        "step_bindings",
        "final_output_aliases",
        "workspace",
        "recorded_at_ms",
    ]
    _p7c0_assert_scope(
        method,
        expected_calls=_P7C0_ROOT_CALLS,
        expected_imports=_P7C0_ROOT_IMPORTS,
        sql_receiver="self._connection.execute",
        expected_sql=_P7C0_ROOT_SQL,
    )

    module_functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    pending = [
        "_verify_executable_composition_plan_authorities",
        "_verify_executable_composition_plan_input_authorities",
    ]
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        assert name in _P7C0_HELPER_CALLS
        helper = module_functions[name]
        helper_nodes = _p7c0_assert_scope(
            helper,
            expected_calls=_P7C0_HELPER_CALLS[name],
            expected_imports=_P7C0_HELPER_IMPORTS[name],
            sql_receiver="connection.execute",
            expected_sql=_P7C0_HELPER_SQL[name],
        )
        local_calls = Counter(
            call_name
            for node in helper_nodes
            if isinstance(node, ast.Call)
            for call_name in [_p7c0_call_name(node)]
            if call_name in module_functions
        )
        assert local_calls == _P7C0_HELPER_EDGES[name]
        pending.extend(local_calls)
        visited.add(name)
    assert visited == set(_P7C0_HELPER_CALLS)

    input_helper = module_functions[
        "_verify_executable_composition_plan_input_authorities"
    ]
    input_nodes = _p7c0_direct_scope_nodes(
        input_helper, reject_nested=True
    )
    failure_assignments = [
        node
        for node in input_nodes
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "failure"
    ]
    assert len(failure_assignments) == 1
    failure_value = failure_assignments[0].value
    assert isinstance(failure_value, ast.IfExp)
    assert _p7c0_qualified_name(failure_value.test) == (
        "create_missing_request_owners"
    )
    assert _p7c0_qualified_name(failure_value.body) == "StoreConflictError"
    assert _p7c0_qualified_name(failure_value.orelse) == "StoreCorruptionError"
    object_identity_calls = [
        node
        for node in input_nodes
        if isinstance(node, ast.Call)
        and _p7c0_call_name(node) == "_assert_object_content_identity_locked"
    ]
    assert len(object_identity_calls) == 1
    mismatch_keywords = [
        item
        for item in object_identity_calls[0].keywords
        if item.arg == "mismatch_error"
    ]
    assert len(mismatch_keywords) == 1
    assert _p7c0_qualified_name(mismatch_keywords[0].value) == "failure"

    p7b_boundary = _p7c0_store_method(
        tree, "register_limited_composition_activation_bundle"
    )
    _p7c0_assert_plain_function(p7b_boundary)
    first_nested = _p7c0_first_nested_scopes(p7b_boundary)
    assert len(first_nested) == 1
    registration_port = first_nested[0]
    assert isinstance(registration_port, ast.ClassDef)
    assert registration_port.name == "_BundleRegistrationPort"
    _p7c0_assert_plain_class(registration_port)
    boundary_nodes = _p7c0_direct_scope_nodes(
        p7b_boundary, reject_nested=False
    )
    boundary_calls = _p7c0_call_counter(boundary_nodes)
    assert boundary_calls == _P7C0_P7B_BOUNDARY_CALLS
    assert Counter(
        {
            name: count
            for name, count in boundary_calls.items()
            if name.startswith("self.") and "()." not in name
        }
    ) == _P7C0_P7B_SELF_CALLS
    assert _p7c0_import_counter(boundary_nodes) == _P7C0_P7B_BOUNDARY_IMPORTS
    _p7c0_assert_sql_surface(
        boundary_nodes,
        receiver="self._connection.execute",
        expected=_P7C0_P7B_BOUNDARY_SQL,
    )

    port_assignments = [
        node for node in registration_port.body if isinstance(node, ast.Assign)
    ]
    assert len(port_assignments) == 1
    authority_assignment = port_assignments[0]
    assert len(authority_assignment.targets) == 1
    assert _p7c0_qualified_name(authority_assignment.targets[0]) == "authority_kind"
    assert _p7c0_qualified_name(authority_assignment.value) == (
        "EXISTING_GATEWAY_STATE_STORE_AUTHORITY"
    )
    assert all(
        isinstance(node, (ast.Assign, ast.FunctionDef))
        for node in registration_port.body
    )
    port_method_nodes = [
        node for node in registration_port.body if isinstance(node, ast.FunctionDef)
    ]
    assert len(port_method_nodes) == len(
        {node.name for node in port_method_nodes}
    )
    port_methods = {node.name: node for node in port_method_nodes}
    assert set(port_methods) == set(_P7C0_P7B_PORT_CALLS)
    for name, expected_calls in _P7C0_P7B_PORT_CALLS.items():
        _p7c0_assert_scope(
            port_methods[name],
            expected_calls=expected_calls,
            expected_imports=Counter(),
            sql_receiver="connection.execute",
            expected_sql=Counter(),
        )


def test_register_executable_bundle_has_no_policy_ticket_grant_effect_fact_or_runtime_authority() -> None:
    _assert_p7c0_register_bundle_is_persistence_only(_p7c0_store_tree())


def _p7c0_mutant_dynamic_callback(tree: ast.Module) -> None:
    method = _p7c0_store_method(
        tree, "register_executable_composition_plan_bundle"
    )
    method.args.kwonlyargs.append(ast.arg(arg="active_at"))
    method.args.kw_defaults.append(None)
    method.body.insert(2, ast.parse("active_at()").body[0])


def _p7c0_mutant_dynamic_getattr(tree: ast.Module) -> None:
    method = _p7c0_store_method(
        tree, "register_executable_composition_plan_bundle"
    )
    method.body.insert(
        2,
        ast.parse(
            'getattr(self, "record_" + "completion_decision")(None)'
        ).body[0],
    )


def _p7c0_mutant_dynamic_dunder_lookup(tree: ast.Module) -> None:
    method = _p7c0_store_method(
        tree, "register_executable_composition_plan_bundle"
    )
    statements = ast.parse(
        'active_at = self.__dict__["record_" + "completion_decision"]\n'
        "active_at(None)"
    ).body
    method.body[2:2] = statements


def _p7c0_root_update_call(tree: ast.Module) -> ast.Call:
    method = _p7c0_store_method(
        tree, "register_executable_composition_plan_bundle"
    )
    return next(
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and _p7c0_qualified_name(node.func) == "self._connection.execute"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and "UPDATE composition_activation_registration" in node.args[0].value
    )


def _p7c0_mutant_same_table_wrong_sql(tree: ast.Module) -> None:
    call = _p7c0_root_update_call(tree)
    call.args[0] = ast.Constant(
        value=(
            "UPDATE composition_activation_registration SET state = ? "
            "WHERE registration_id = 'victim'"
        )
    )


def _p7c0_mutant_concatenated_sql(tree: ast.Module) -> None:
    call = _p7c0_root_update_call(tree)
    call.args[0] = ast.BinOp(
        left=ast.Constant(
            value="UPDATE composition_activation_registration SET state = "
        ),
        op=ast.Add(),
        right=ast.Constant(value="? WHERE registration_id = 'victim'"),
    )


def _p7c0_mutant_fstring_sql(tree: ast.Module) -> None:
    call = _p7c0_root_update_call(tree)
    call.args[0] = ast.parse(
        'f"UPDATE composition_activation_registration SET {column} = ?"',
        mode="eval",
    ).body


def _p7c0_mutant_duplicate_p7b_call(tree: ast.Module) -> None:
    method = _p7c0_store_method(
        tree, "register_executable_composition_plan_bundle"
    )
    call = next(
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and _p7c0_call_name(node)
        == "self.register_limited_composition_activation_bundle"
    )
    duplicate = ast.parse(ast.unparse(call), mode="eval").body
    method.body.insert(2, ast.Expr(value=duplicate))


def _p7c0_mutant_helper_indirection(tree: ast.Module) -> None:
    helper = _p7c0_module_function(
        tree, "_verify_executable_composition_plan_authorities"
    )
    helper.body.insert(
        1, ast.parse("_p7c0_hidden_completion_helper(connection)").body[0]
    )
    hidden = ast.parse(
        """
def _p7c0_hidden_completion_helper(connection):
    getattr(connection, "record_" + "completion_decision")(None)
"""
    ).body[0]
    tree.body.append(hidden)


def _p7c0_mutant_helper_dynamic_sql(tree: ast.Module) -> None:
    helper = _p7c0_module_function(
        tree, "_verify_executable_composition_plan_authorities"
    )
    helper.body.insert(
        1,
        ast.parse(
            'connection.execute("INSERT INTO completion_" + "decisions "'
            ' "DEFAULT VALUES", ())'
        ).body[0],
    )


def _p7c0_mutant_p7b_completion_call(tree: ast.Module) -> None:
    boundary = _p7c0_store_method(
        tree, "register_limited_composition_activation_bundle"
    )
    boundary.body.insert(
        2, ast.parse("self.record_completion_decision(None)").body[0]
    )


def _p7c0_mutant_nested_definition(tree: ast.Module) -> None:
    method = _p7c0_store_method(
        tree, "register_executable_composition_plan_bundle"
    )
    method.body.insert(2, ast.parse("def hidden():\n    pass").body[0])


def _p7c0_mutant_duplicate_root_method(tree: ast.Module) -> None:
    store_class = _p7c0_store_class(tree)
    duplicate = ast.parse(
        """
def register_executable_composition_plan_bundle(self, *args, **kwargs):
    return getattr(self, "record_" + "completion_decision")(*args, **kwargs)
"""
    ).body[0]
    store_class.body.append(duplicate)


def _p7c0_mutant_duplicate_store_class(tree: ast.Module) -> None:
    duplicate = ast.parse(
        """
class GatewayStateStore:
    def register_executable_composition_plan_bundle(self, *args, **kwargs):
        return getattr(self, "record_" + "completion_decision")(*args, **kwargs)
"""
    ).body[0]
    tree.body.append(duplicate)


def _p7c0_mutant_class_rebind_root_method(tree: ast.Module) -> None:
    store_class = _p7c0_store_class(tree)
    store_class.body.append(
        ast.parse(
            "register_executable_composition_plan_bundle = "
            "completion_authority_wrapper"
        ).body[0]
    )


def _p7c0_mutant_module_rebind_helper(tree: ast.Module) -> None:
    tree.body.append(
        ast.parse(
            "_verify_executable_composition_plan_authorities = "
            "completion_authority_wrapper"
        ).body[0]
    )


def _p7c0_mutant_local_shadow_codec(tree: ast.Module) -> None:
    shadow = ast.parse(
        """
def canonical_executable_plan_json(value):
    return getattr(value, "record_" + "completion_decision")()
"""
    ).body[0]
    tree.body.append(shadow)


def _p7c0_mutant_async_helper_override(tree: ast.Module) -> None:
    override = ast.parse(
        """
async def _verify_executable_composition_plan_authorities(connection, record):
    return getattr(connection, "record_" + "completion_decision")(record)
"""
    ).body[0]
    tree.body.append(override)


def _p7c0_mutant_root_decorator(tree: ast.Module) -> None:
    method = _p7c0_store_method(
        tree, "register_executable_composition_plan_bundle"
    )
    method.decorator_list.append(
        ast.Name(id="completion_authority_wrapper", ctx=ast.Load())
    )


def _p7c0_mutant_p7b_decorator(tree: ast.Module) -> None:
    method = _p7c0_store_method(
        tree, "register_limited_composition_activation_bundle"
    )
    method.decorator_list.append(
        ast.Name(id="completion_authority_wrapper", ctx=ast.Load())
    )


def _p7c0_mutant_helper_decorator(tree: ast.Module) -> None:
    helper = _p7c0_module_function(
        tree, "_verify_executable_composition_plan_authorities"
    )
    helper.decorator_list.append(
        ast.Name(id="completion_authority_wrapper", ctx=ast.Load())
    )


def _p7c0_registration_port(tree: ast.Module) -> ast.ClassDef:
    boundary = _p7c0_store_method(
        tree, "register_limited_composition_activation_bundle"
    )
    matches = [
        node
        for node in ast.walk(boundary)
        if isinstance(node, ast.ClassDef) and node.name == "_BundleRegistrationPort"
    ]
    assert len(matches) == 1
    return matches[0]


def _p7c0_mutant_registration_port_decorator(tree: ast.Module) -> None:
    registration_port = _p7c0_registration_port(tree)
    registration_port.decorator_list.append(
        ast.Name(id="completion_authority_wrapper", ctx=ast.Load())
    )


def _p7c0_mutant_registration_port_base(tree: ast.Module) -> None:
    registration_port = _p7c0_registration_port(tree)
    registration_port.bases.append(ast.Name(id="CompletionGate", ctx=ast.Load()))


def _p7c0_mutant_registration_port_method_decorator(tree: ast.Module) -> None:
    registration_port = _p7c0_registration_port(tree)
    method = next(
        node
        for node in registration_port.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "put_limited_activation_registration"
    )
    method.decorator_list.append(
        ast.Name(id="completion_authority_wrapper", ctx=ast.Load())
    )


def _p7c0_mutant_quoted_request_literal_case(tree: ast.Module) -> None:
    helper = _p7c0_module_function(
        tree, "_verify_executable_composition_plan_input_authorities"
    )
    changed = False
    for node in ast.walk(helper):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "'REQUEST'" in node.value
        ):
            node.value = node.value.replace("'REQUEST'", "'request'")
            changed = True
    assert changed


def _p7c0_mutant_safe_sql_layout(tree: ast.Module) -> None:
    method = _p7c0_store_method(
        tree, "register_executable_composition_plan_bundle"
    )
    for node in ast.walk(method):
        if (
            isinstance(node, ast.Call)
            and _p7c0_qualified_name(node.func) == "self._connection.execute"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and "INSERT INTO composition_executable_plan(" in node.args[0].value
        ):
            node.args[0].value = node.args[0].value.replace(
                "INSERT INTO composition_executable_plan(",
                "insert   into composition_executable_plan (",
            )
            return
    raise AssertionError("root INSERT SQL was not found")


@pytest.mark.parametrize(
    "mutator",
    (
        _p7c0_mutant_dynamic_callback,
        _p7c0_mutant_dynamic_getattr,
        _p7c0_mutant_dynamic_dunder_lookup,
        _p7c0_mutant_same_table_wrong_sql,
        _p7c0_mutant_concatenated_sql,
        _p7c0_mutant_fstring_sql,
        _p7c0_mutant_duplicate_p7b_call,
        _p7c0_mutant_helper_indirection,
        _p7c0_mutant_helper_dynamic_sql,
        _p7c0_mutant_p7b_completion_call,
        _p7c0_mutant_nested_definition,
        _p7c0_mutant_duplicate_root_method,
        _p7c0_mutant_duplicate_store_class,
        _p7c0_mutant_class_rebind_root_method,
        _p7c0_mutant_module_rebind_helper,
        _p7c0_mutant_local_shadow_codec,
        _p7c0_mutant_async_helper_override,
        _p7c0_mutant_root_decorator,
        _p7c0_mutant_p7b_decorator,
        _p7c0_mutant_helper_decorator,
        _p7c0_mutant_registration_port_decorator,
        _p7c0_mutant_registration_port_base,
        _p7c0_mutant_registration_port_method_decorator,
        _p7c0_mutant_quoted_request_literal_case,
    ),
    ids=(
        "dynamic-callback",
        "dynamic-getattr",
        "dynamic-dunder-lookup",
        "same-table-wrong-sql",
        "concatenated-sql",
        "fstring-sql",
        "duplicate-p7b-call",
        "helper-indirection",
        "helper-dynamic-sql",
        "p7b-completion-call",
        "nested-definition",
        "duplicate-root-method",
        "duplicate-store-class",
        "class-rebind-root-method",
        "module-rebind-helper",
        "local-shadow-codec",
        "async-helper-override",
        "root-decorator",
        "p7b-decorator",
        "helper-decorator",
        "registration-port-decorator",
        "registration-port-base",
        "registration-port-method-decorator",
        "quoted-request-literal-case",
    ),
)
def test_register_executable_bundle_authority_guard_rejects_mutants(
    mutator,
) -> None:
    tree = _p7c0_store_tree()
    mutator(tree)
    with pytest.raises(AssertionError):
        _assert_p7c0_register_bundle_is_persistence_only(tree)


def test_register_executable_bundle_authority_guard_accepts_safe_sql_layout() -> None:
    tree = _p7c0_store_tree()
    _p7c0_mutant_safe_sql_layout(tree)
    _assert_p7c0_register_bundle_is_persistence_only(tree)


def test_atomic_bundle_roundtrips_full_plan_and_all_existing_authorities(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        result = _persist_executable(store, material)

        assert result.created_by_this_call is True
        assert result.duplicate is False
        assert result.activation_bundle.created_by_this_call is True
        assert result.record.created_by_this_call is True
        executable = result.record.executable_plan
        registration = result.activation_bundle.record.registration
        assert executable.registration_id == registration.registration_id
        assert executable.registration_sha256 == registration.registration_sha256
        assert executable.verification_plan_activation_id == (
            result.activation_bundle.verification_plan_activation_id
        )
        assert executable.step_bindings == material["step_bindings"]
        assert executable.plan_inputs == material["plan_inputs"]
        assert executable.final_output_aliases == material["final_output_aliases"]
        assert store.get_executable_composition_plan_record(
            executable.executable_plan_id
        ).executable_plan == executable
        assert store.get_executable_composition_plan_for_registration(
            registration.registration_id
        ).executable_plan == executable
        assert store.get_active_executable_composition_plan(
            registration.registration_id, now_ms=1_700
        ).executable_plan == executable
        assert store.get_registry_snapshot(
            material["verification_registry"].registry_snapshot_id
        ) == material["verification_registry"]
        assert store.get_verification_plan(
            material["shadow"].verification_plan.verification_plan_id
        ) == material["shadow"].verification_plan
        assert store.health_check(now_ms=1_700, full=True).healthy

        counts = {
            table: store._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0]
            for table in (
                "verification_registry_snapshot",
                "verification_plan",
                "verification_plan_activation",
                "composition_activation_registration",
                "composition_executable_plan",
            )
        }
        assert counts == {table: 1 for table in counts}
        assert store._connection.execute(
            "SELECT executable_plan_required "
            "FROM composition_activation_registration"
        ).fetchone()[0] == 1


def test_object_input_matches_inbound_attachment_and_gets_request_owner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-object-input.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        inbound_row = store._connection.execute(
            "SELECT envelope_json FROM request_inbound_payload "
            "WHERE request_id = ?",
            (material["legacy_plan"].request_id,),
        ).fetchone()
        envelope = InboundEnvelope.model_validate_json(inbound_row[0], strict=True)
        attachment = AttachmentRef(
            object_id="oref_" + "c" * 64,
            revision=1,
            sha256="d" * 64,
            size_bytes=17,
            mime="text/plain",
            filename="evidence.txt",
            tenant_id=envelope.tenant_id,
            link_account_id=envelope.link_account_id,
            conversation_scope_hash=envelope.conversation_scope_hash,
            source_message_ref=envelope.channel_message_ref,
            created_at_ms=1_000,
        )
        _replace_inbound_attachments(store, material, (attachment,))
        grant = ObjectGrant(
            object_id=attachment.object_id,
            revision=attachment.revision,
            sha256=attachment.sha256,
            size_bytes=attachment.size_bytes,
            mime=attachment.mime,
            tenant_id=attachment.tenant_id,
            link_account_id=attachment.link_account_id,
            conversation_scope_hash=attachment.conversation_scope_hash,
        )
        _add_object_input(material, grant)

        result = _persist_executable(store, material)

        owner = store._connection.execute(
            "SELECT * FROM object_owners "
            "WHERE object_id = ? AND owner_kind = 'REQUEST' AND owner_id = ?",
            (attachment.object_id, material["legacy_plan"].request_id),
        ).fetchone()
        assert owner is not None
        assert owner["object_sha256"] == attachment.sha256
        assert owner["run_id"] == material["legacy_plan"].run_id
        assert owner["generation"] == material["legacy_plan"].generation
        assert result.record.executable_plan.plan_inputs[-1].object_grant == grant
        replay = _persist_executable(store, material, recorded_at_ms=1_700)
        assert replay.duplicate is True
        assert store._connection.execute(
            "SELECT count(*) FROM object_owners "
            "WHERE object_id = ? AND owner_kind = 'REQUEST' AND owner_id = ?",
            (attachment.object_id, material["legacy_plan"].request_id),
        ).fetchone()[0] == 1
        assert store.health_check(now_ms=1_700, full=True).healthy
        with _temporarily_disable_trigger_for_corruption(
            store._connection,
            "object_owners_immutable_delete_guard",
        ):
            store._connection.execute(
                "DELETE FROM object_owners "
                "WHERE object_id = ? AND owner_kind = 'REQUEST' AND owner_id = ?",
                (attachment.object_id, material["legacy_plan"].request_id),
            )
        with pytest.raises(StoreCorruptionError, match="missing its request owner"):
            store.get_executable_composition_plan_record(
                result.record.executable_plan.executable_plan_id
            )
        assert store.health_check(now_ms=1_700, full=True).healthy is False


def test_public_owner_api_cannot_rebind_executable_object_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-object-owner-rebind.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        attachment, _ = _prepare_exact_object_input(store, material)
        created = _persist_executable(store, material)
        legacy = material["legacy_plan"]

        with pytest.raises(
            StoreConflictError,
            match="object identity is already bound to different content",
        ):
            store.record_object_owner(
                object_id=attachment.object_id,
                object_sha256="e" * 64,
                owner_kind="ARTIFACT",
                owner_id="artifact-conflicting-content",
                request_id=legacy.request_id,
                run_id=legacy.run_id,
                generation=legacy.generation,
                created_at_ms=1_700,
            )
        with pytest.raises(ValueError, match="ownership fact is invalid"):
            store.record_object_owner(
                object_id=attachment.object_id,
                object_sha256=attachment.sha256,
                owner_kind="REQUEST",
                owner_id="not-the-request-id",
                request_id=legacy.request_id,
                run_id=legacy.run_id,
                generation=legacy.generation,
                created_at_ms=1_700,
            )

        noncanonical_request_owner_sha256 = store_module._object_ownership_sha256(
            object_id=attachment.object_id,
            object_sha256=attachment.sha256,
            owner_kind="REQUEST",
            owner_id="not-the-request-id",
            request_id=legacy.request_id,
            run_id=legacy.run_id,
            generation=legacy.generation,
            created_at_ms=1_700,
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="must use the request identity",
        ):
            store._connection.execute(
                """
                INSERT INTO object_owners(
                    object_id, object_sha256, owner_kind, owner_id,
                    request_id, run_id, generation, created_at_ms,
                    ownership_sha256
                ) VALUES (?, ?, 'REQUEST', ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment.object_id,
                    attachment.sha256,
                    "not-the-request-id",
                    legacy.request_id,
                    legacy.run_id,
                    legacy.generation,
                    1_700,
                    noncanonical_request_owner_sha256,
                ),
            )

        conflicting_ownership_sha256 = store_module._object_ownership_sha256(
            object_id=attachment.object_id,
            object_sha256="e" * 64,
            owner_kind="ARTIFACT",
            owner_id="direct-sql-conflicting-content",
            request_id=legacy.request_id,
            run_id=legacy.run_id,
            generation=legacy.generation,
            created_at_ms=1_700,
        )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be rebound"):
            store._connection.execute(
                """
                INSERT INTO object_owners(
                    object_id, object_sha256, owner_kind, owner_id,
                    request_id, run_id, generation, created_at_ms,
                    ownership_sha256
                ) VALUES (?, ?, 'ARTIFACT', ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment.object_id,
                    "e" * 64,
                    "direct-sql-conflicting-content",
                    legacy.request_id,
                    legacy.run_id,
                    legacy.generation,
                    1_700,
                    conflicting_ownership_sha256,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            store._connection.execute(
                "UPDATE object_owners SET owner_id = ? WHERE object_id = ?",
                ("mutated-request-owner", attachment.object_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            store._connection.execute(
                "DELETE FROM object_owners WHERE object_id = ?",
                (attachment.object_id,),
            )

        owner_row = store._connection.execute(
            "SELECT * FROM object_owners "
            "WHERE object_id = ? AND owner_kind = 'REQUEST' AND owner_id = ?",
            (attachment.object_id, legacy.request_id),
        ).fetchone()
        assert owner_row is not None
        original_owner = dict(owner_row)
        replacement_created_at_ms = int(original_owner["created_at_ms"]) + 1
        replacement_ownership_sha256 = store_module._object_ownership_sha256(
            object_id=attachment.object_id,
            object_sha256=attachment.sha256,
            owner_kind="REQUEST",
            owner_id=legacy.request_id,
            request_id=legacy.request_id,
            run_id=legacy.run_id,
            generation=legacy.generation,
            created_at_ms=replacement_created_at_ms,
        )
        replacement_values = (
            attachment.object_id,
            attachment.sha256,
            "REQUEST",
            legacy.request_id,
            legacy.request_id,
            legacy.run_id,
            legacy.generation,
            replacement_created_at_ms,
            replacement_ownership_sha256,
        )
        owner_write_statements = (
            """
            INSERT OR REPLACE INTO object_owners(
                object_id, object_sha256, owner_kind, owner_id,
                request_id, run_id, generation, created_at_ms,
                ownership_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            """
            INSERT INTO object_owners(
                object_id, object_sha256, owner_kind, owner_id,
                request_id, run_id, generation, created_at_ms,
                ownership_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(object_id, owner_kind, owner_id) DO UPDATE SET
                created_at_ms = excluded.created_at_ms,
                ownership_sha256 = excluded.ownership_sha256
            """,
        )
        for statement in owner_write_statements:
            with pytest.raises(
                sqlite3.IntegrityError, match="identity is immutable"
            ):
                store._connection.execute(statement, replacement_values)
            current_owner = store._connection.execute(
                "SELECT * FROM object_owners "
                "WHERE object_id = ? AND owner_kind = 'REQUEST' AND owner_id = ?",
                (attachment.object_id, legacy.request_id),
            ).fetchone()
            assert current_owner is not None
            assert dict(current_owner) == original_owner

        assert store._connection.execute(
            "SELECT count(*) FROM object_owners WHERE object_id = ?",
            (attachment.object_id,),
        ).fetchone()[0] == 1
        reread = store.get_executable_composition_plan_record(
            created.record.executable_plan.executable_plan_id
        )
        assert reread is not None
        assert reread.executable_plan == created.record.executable_plan
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_public_owner_api_rejects_invalid_owner_kind_before_transaction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-invalid-owner-kind.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        legacy = material["legacy_plan"]
        statements: list[str] = []
        store._connection.set_trace_callback(statements.append)
        try:
            with pytest.raises(ValueError, match="ownership fact is invalid"):
                store.record_object_owner(
                    object_id="oref_" + "c" * 64,
                    object_sha256="d" * 64,
                    owner_kind="BOGUS",  # type: ignore[arg-type]
                    owner_id="invalid-owner",
                    request_id=legacy.request_id,
                    run_id=legacy.run_id,
                    generation=legacy.generation,
                    created_at_ms=1_700,
                )
        finally:
            store._connection.set_trace_callback(None)

        normalized = tuple(
            " ".join(statement.split()).upper() for statement in statements
        )
        assert not any(
            statement.startswith(
                ("BEGIN", "SAVEPOINT", "COMMIT", "ROLLBACK", "RELEASE")
            )
            for statement in normalized
        )
        assert not any(
            "INSERT INTO OBJECT_OWNERS" in statement for statement in normalized
        )
        assert store._connection.in_transaction is False
        assert store._connection.execute(
            "SELECT count(*) FROM object_owners"
        ).fetchone()[0] == 0
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_two_bundles_cannot_bind_one_object_id_to_different_content(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-cross-request-object-rebind.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        first_material = _compile_material(store, tmp_path)
        second_material = _compile_material(store, tmp_path, lineage_seed="8")
        first_attachment, _ = _prepare_exact_object_input(
            store, first_material, sha256="d" * 64
        )
        second_attachment, _ = _prepare_exact_object_input(
            store, second_material, sha256="e" * 64
        )
        assert first_attachment.object_id == second_attachment.object_id
        first = _persist_executable(store, first_material)

        with pytest.raises(
            StoreConflictError,
            match="object identity is already bound to different content",
        ):
            _persist_executable(store, second_material)

        second_request_id = second_material["legacy_plan"].request_id
        assert store._connection.execute(
            "SELECT count(*) FROM composition_activation_registration "
            "WHERE request_id = ?",
            (second_request_id,),
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT count(*) FROM composition_executable_plan WHERE request_id = ?",
            (second_request_id,),
        ).fetchone()[0] == 0
        reread = store.get_executable_composition_plan_record(
            first.record.executable_plan.executable_plan_id
        )
        assert reread is not None
        assert reread.executable_plan == first.record.executable_plan
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_two_requests_may_share_one_object_id_only_for_the_same_content(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-cross-request-same-object-content.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        first_material = _compile_material(store, tmp_path)
        second_material = _compile_material(store, tmp_path, lineage_seed="8")
        first_attachment, _ = _prepare_exact_object_input(
            store, first_material, sha256="d" * 64
        )
        second_attachment, _ = _prepare_exact_object_input(
            store, second_material, sha256="d" * 64
        )
        assert first_attachment.object_id == second_attachment.object_id
        assert first_attachment.sha256 == second_attachment.sha256
        assert (
            first_attachment.tenant_id,
            first_attachment.link_account_id,
            first_attachment.conversation_scope_hash,
        ) != (
            second_attachment.tenant_id,
            second_attachment.link_account_id,
            second_attachment.conversation_scope_hash,
        )

        first = _persist_executable(store, first_material)
        second = _persist_executable(store, second_material)

        assert first.created_by_this_call is True
        assert second.created_by_this_call is True
        owners = store._connection.execute(
            "SELECT owner_id, object_sha256 FROM object_owners "
            "WHERE object_id = ? AND owner_kind = 'REQUEST' ORDER BY owner_id",
            (first_attachment.object_id,),
        ).fetchall()
        assert len(owners) == 2
        assert {row["owner_id"] for row in owners} == {
            first_material["legacy_plan"].request_id,
            second_material["legacy_plan"].request_id,
        }
        assert {row["object_sha256"] for row in owners} == {"d" * 64}
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_object_inputs_can_pin_two_revisions_of_one_accepted_object(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-object-input-revisions.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        inbound_row = store._connection.execute(
            "SELECT envelope_json FROM request_inbound_payload "
            "WHERE request_id = ?",
            (material["legacy_plan"].request_id,),
        ).fetchone()
        envelope = InboundEnvelope.model_validate_json(inbound_row[0], strict=True)
        attachments = tuple(
            AttachmentRef(
                object_id="oref_" + "c" * 64,
                revision=revision,
                sha256="d" * 64,
                size_bytes=17,
                mime="text/plain",
                filename=f"evidence-r{revision}.txt",
                tenant_id=envelope.tenant_id,
                link_account_id=envelope.link_account_id,
                conversation_scope_hash=envelope.conversation_scope_hash,
                source_message_ref=envelope.channel_message_ref,
                created_at_ms=1_000,
            )
            for revision in (1, 2)
        )
        _replace_inbound_attachments(store, material, attachments)
        object_inputs = []
        references = []
        for attachment in attachments:
            grant = ObjectGrant(
                object_id=attachment.object_id,
                revision=attachment.revision,
                sha256=attachment.sha256,
                size_bytes=attachment.size_bytes,
                mime=attachment.mime,
                tenant_id=attachment.tenant_id,
                link_account_id=attachment.link_account_id,
                conversation_scope_hash=attachment.conversation_scope_hash,
            )
            plan_input = _hashed(
                PlanInputV1(
                    input_id=f"input.object-r{attachment.revision}",
                    input_kind="OBJECT_GRANT",
                    object_grant=grant,
                    value_schema_sha256=H,
                    value_sha256=grant.sha256,
                    sha256=ZERO_SHA256,
                )
            )
            object_inputs.append(plan_input)
            references.append(
                _hashed(
                    PlanInputValueBindingV1(
                        input_id=plan_input.input_id,
                        input_sha256=plan_input.sha256,
                        json_pointer="",
                        sha256=ZERO_SHA256,
                    )
                )
            )
        first = material["step_bindings"][0]
        slots = tuple(
            sorted(
                (
                    _hashed(
                        ArgumentSlotV1(
                            destination_json_pointer="/artifact_id",
                            value_binding=references[0],
                            sha256=ZERO_SHA256,
                        )
                    ),
                    _hashed(
                        ArgumentSlotV1(
                            destination_json_pointer="/mode",
                            value_binding=references[1],
                            sha256=ZERO_SHA256,
                        )
                    ),
                ),
                key=lambda item: item.destination_json_pointer,
            )
        )
        material["plan_inputs"] = tuple(
            sorted(object_inputs, key=lambda item: item.input_id)
        )
        material["step_bindings"] = (
            _hashed(
                first.model_copy(
                    update={"argument_slots": slots, "sha256": ZERO_SHA256}
                )
            ),
            material["step_bindings"][1],
        )

        result = _persist_executable(store, material)

        owners = store._connection.execute(
            "SELECT * FROM object_owners WHERE object_id = ? "
            "AND owner_kind = 'REQUEST' AND request_id = ? ORDER BY owner_id",
            (attachments[0].object_id, material["legacy_plan"].request_id),
        ).fetchall()
        assert len(owners) == 1
        assert owners[0]["object_sha256"] == attachments[0].sha256
        assert {
            item.object_grant.revision
            for item in result.record.executable_plan.plan_inputs
        } == {1, 2}
        assert store.health_check(now_ms=1_700, full=True).healthy


@pytest.mark.parametrize(
    ("field", "drifted_value"),
    (
        ("sha256", "e" * 64),
        ("size_bytes", 18),
        ("mime", "application/json"),
        ("tenant_id", "tenant-crossed"),
        ("link_account_id", "account-crossed"),
        ("conversation_scope_hash", "f" * 64),
    ),
)
def test_same_object_id_cannot_claim_different_revision_authority(
    field: str,
    drifted_value: object,
) -> None:
    common = {
        "object_id": "oref_" + "c" * 64,
        "sha256": "d" * 64,
        "size_bytes": 17,
        "mime": "text/plain",
        "tenant_id": "tenant-p7c0",
        "link_account_id": "account-p7c0",
        "conversation_scope_hash": H,
    }
    grants = (
        ObjectGrant(revision=1, **common),
        ObjectGrant(revision=2, **(common | {field: drifted_value})),
    )
    inputs = tuple(
        _hashed(
            PlanInputV1(
                input_id=f"input.object-r{grant.revision}",
                input_kind="OBJECT_GRANT",
                object_grant=grant,
                value_schema_sha256=H,
                value_sha256=grant.sha256,
                sha256=ZERO_SHA256,
            )
        )
        for grant in grants
    )
    with pytest.raises(
        ExecutableCompositionPlanError,
        match="executable_plan.object_grant.object_identity_conflict",
    ):
        _validate_object_grant_inputs(inputs)


def test_forged_object_input_rolls_back_the_whole_bundle(tmp_path: Path) -> None:
    path = tmp_path / "gateway-forged-object-input.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        inbound_row = store._connection.execute(
            "SELECT envelope_json FROM request_inbound_payload "
            "WHERE request_id = ?",
            (material["legacy_plan"].request_id,),
        ).fetchone()
        envelope = InboundEnvelope.model_validate_json(inbound_row[0], strict=True)
        attachment = AttachmentRef(
            object_id="oref_" + "c" * 64,
            revision=1,
            sha256="d" * 64,
            size_bytes=17,
            mime="text/plain",
            filename="evidence.txt",
            tenant_id=envelope.tenant_id,
            link_account_id=envelope.link_account_id,
            conversation_scope_hash=envelope.conversation_scope_hash,
            source_message_ref=envelope.channel_message_ref,
            created_at_ms=1_000,
        )
        _replace_inbound_attachments(store, material, (attachment,))
        forged = ObjectGrant(
            object_id=attachment.object_id,
            revision=999,
            sha256="f" * 64,
            size_bytes=attachment.size_bytes,
            mime=attachment.mime,
            tenant_id="cross-tenant",
            link_account_id=attachment.link_account_id,
            conversation_scope_hash=attachment.conversation_scope_hash,
        )
        _add_object_input(material, forged)

        with pytest.raises(
            StoreConflictError,
            match="not an exact accepted attachment",
        ):
            _persist_executable(store, material)

        assert store._connection.execute(
            "SELECT count(*) FROM composition_activation_registration"
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT count(*) FROM composition_executable_plan"
        ).fetchone()[0] == 0
        assert store._connection.execute(
            "SELECT count(*) FROM object_owners"
        ).fetchone()[0] == 0
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_late_plan_insert_failure_rolls_back_request_owner_and_bundle(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-late-plan-insert-failure.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        inbound_row = store._connection.execute(
            "SELECT envelope_json FROM request_inbound_payload "
            "WHERE request_id = ?",
            (material["legacy_plan"].request_id,),
        ).fetchone()
        envelope = InboundEnvelope.model_validate_json(inbound_row[0], strict=True)
        attachment = AttachmentRef(
            object_id="oref_" + "c" * 64,
            revision=1,
            sha256="d" * 64,
            size_bytes=17,
            mime="text/plain",
            filename="evidence.txt",
            tenant_id=envelope.tenant_id,
            link_account_id=envelope.link_account_id,
            conversation_scope_hash=envelope.conversation_scope_hash,
            source_message_ref=envelope.channel_message_ref,
            created_at_ms=1_000,
        )
        _replace_inbound_attachments(store, material, (attachment,))
        _add_object_input(
            material,
            ObjectGrant(
                object_id=attachment.object_id,
                revision=attachment.revision,
                sha256=attachment.sha256,
                size_bytes=attachment.size_bytes,
                mime=attachment.mime,
                tenant_id=attachment.tenant_id,
                link_account_id=attachment.link_account_id,
                conversation_scope_hash=attachment.conversation_scope_hash,
            ),
        )
        store._connection.execute(
            """
            CREATE TEMP TRIGGER force_late_executable_plan_insert_failure
            BEFORE INSERT ON composition_executable_plan
            BEGIN
                SELECT RAISE(ABORT, 'forced late executable-plan insert failure');
            END
            """
        )

        with pytest.raises(sqlite3.IntegrityError, match="forced late executable-plan"):
            _persist_executable(store, material)

        assert store._connection.in_transaction is False
        for table in (
            "verification_registry_snapshot",
            "verification_plan",
            "verification_plan_activation",
            "composition_activation_registration",
            "composition_executable_plan",
            "object_owners",
        ):
            assert store._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0] == 0
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_required_companion_loss_is_explicit_corruption(tmp_path: Path) -> None:
    path = tmp_path / "gateway-missing-required-plan.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        created = _persist_executable(store, material)
        executable_id = created.record.executable_plan.executable_plan_id
        registration_id = created.record.executable_plan.registration_id
        with pytest.raises(sqlite3.IntegrityError, match="cannot be cleared"):
            store._connection.execute(
                "UPDATE composition_activation_registration "
                "SET executable_plan_required = 0 WHERE registration_id = ?",
                (registration_id,),
            )
        with _temporarily_disable_trigger_for_corruption(
            store._connection,
            "composition_executable_plan_immutable_delete_guard",
        ):
            store._connection.execute(
                "DELETE FROM composition_executable_plan WHERE registration_id = ?",
                (registration_id,),
            )

        with pytest.raises(StoreCorruptionError, match="companion is missing"):
            store.get_executable_composition_plan_record(executable_id)
        with pytest.raises(StoreCorruptionError, match="companion is missing"):
            store.get_limited_activation_registration_record(registration_id)
        with pytest.raises(StoreCorruptionError, match="companion is missing"):
            store.get_limited_activation_registration(registration_id)
        with pytest.raises(StoreCorruptionError, match="companion is missing"):
            store.get_executable_composition_plan_for_registration(registration_id)
        with pytest.raises(StoreCorruptionError, match="companion is missing"):
            store.get_active_executable_composition_plan(
                registration_id, now_ms=1_700
            )
        with pytest.raises(StoreCorruptionError, match="companion is missing"):
            store.recover_active_executable_composition_plans(now_ms=1_700)
        with pytest.raises(StoreCorruptionError, match="companion is missing"):
            store.get_active_limited_activation_registration(
                registration_id, now_ms=1_700
            )
        with pytest.raises(StoreCorruptionError, match="companion is missing"):
            store.recover_limited_activation_registrations(now_ms=1_700)
        with pytest.raises(StoreCorruptionError, match="companion is missing"):
            _persist_executable(store, material, recorded_at_ms=1_700)
        with pytest.raises(StoreCorruptionError, match="companion is missing"):
            _persist_registration_only(store, material, recorded_at_ms=1_700)
        health = store.health_check(now_ms=1_700, full=True)
        assert health.healthy is False
        assert health.reason_code == "store.check.failed"


def test_v30_audit_registration_migrates_additively_but_has_no_companion(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-v30.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        legacy = _persist_registration_only(store, material)
        registration_id = legacy.record.registration.registration_id
        registration_json = store._connection.execute(
            "SELECT registration_json FROM composition_activation_registration "
            "WHERE registration_id = ?",
            (registration_id,),
        ).fetchone()[0]
        assert store.get_executable_composition_plan_for_registration(
            registration_id
        ) is None

    _downgrade_v31_to_v30(path)

    with GatewayStateStore.open(path, now_ms=1_700) as migrated:
        assert STORE_SCHEMA_VERSION == 31
        assert migrated._connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0] == 31
        assert migrated._connection.execute(
            "SELECT registration_json FROM composition_activation_registration "
            "WHERE registration_id = ?",
            (registration_id,),
        ).fetchone()[0] == registration_json
        assert migrated._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='composition_executable_plan'"
        ).fetchone() is not None
        trigger_names = {
            row[0]
            for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
        assert _CORRUPTION_TEST_TRIGGERS <= trigger_names
        assert migrated._connection.execute(
            "SELECT count(*) FROM composition_executable_plan"
        ).fetchone()[0] == 0
        assert migrated._connection.execute(
            "SELECT executable_plan_required "
            "FROM composition_activation_registration "
            "WHERE registration_id = ?",
            (registration_id,),
        ).fetchone()[0] == 0
        assert migrated.get_limited_activation_registration_record(
            registration_id
        ) is not None
        assert migrated.get_executable_composition_plan_for_registration(
            registration_id
        ) is None
        assert migrated.get_active_executable_composition_plan(
            registration_id, now_ms=1_700
        ) is None
        assert migrated.recover_active_executable_composition_plans(
            now_ms=1_700
        ) == ()
        with pytest.raises(
            StoreConflictError,
            match="audit-only registration cannot be backfilled",
        ):
            _persist_executable(migrated, material, recorded_at_ms=1_700)
        assert migrated._connection.execute(
            "SELECT count(*) FROM composition_executable_plan"
        ).fetchone()[0] == 0
        assert migrated.health_check(now_ms=1_700, full=True).healthy


@pytest.mark.parametrize("trigger_name", sorted(_CORRUPTION_TEST_TRIGGERS))
def test_health_rejects_missing_v31_append_only_guard(
    tmp_path: Path,
    trigger_name: str,
) -> None:
    path = tmp_path / f"gateway-missing-{trigger_name}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        store._connection.execute(f'DROP TRIGGER "{trigger_name}"')
        health = store.health_check(now_ms=1_000, full=True)
        assert health.healthy is False
        assert health.reason_code == "store.check.failed"


def test_v31_health_rejects_legacy_object_identity_hash_divergence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-v30-object-identity-divergence.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        legacy = material["legacy_plan"]

    _downgrade_v31_to_v30(path)
    object_id = "oref_" + "c" * 64
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        for owner_id, object_sha256, created_at_ms in (
            ("artifact-one", "d" * 64, 1_500),
            ("artifact-two", "e" * 64, 1_600),
        ):
            ownership_sha256 = store_module._object_ownership_sha256(
                object_id=object_id,
                object_sha256=object_sha256,
                owner_kind="ARTIFACT",
                owner_id=owner_id,
                request_id=legacy.request_id,
                run_id=legacy.run_id,
                generation=legacy.generation,
                created_at_ms=created_at_ms,
            )
            connection.execute(
                """
                INSERT INTO object_owners(
                    object_id, object_sha256, owner_kind, owner_id,
                    request_id, run_id, generation, created_at_ms,
                    ownership_sha256
                ) VALUES (?, ?, 'ARTIFACT', ?, ?, ?, ?, ?, ?)
                """,
                (
                    object_id,
                    object_sha256,
                    owner_id,
                    legacy.request_id,
                    legacy.run_id,
                    legacy.generation,
                    created_at_ms,
                    ownership_sha256,
                ),
            )
    finally:
        connection.close()

    with pytest.raises(StoreCorruptionError, match="store.check.failed"):
        GatewayStateStore.open(path, now_ms=1_700)


def test_v30_to_v31_concurrent_open_serializes_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "gateway-concurrent-v30-migration.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000):
        pass
    _downgrade_v31_to_v30(path)

    original_fingerprint = store_module._schema_fingerprint
    second_connection_reached_prior_check = threading.Event()
    seen_connections: set[int] = set()
    seen_lock = threading.Lock()

    def synchronized_prior_fingerprint(connection: sqlite3.Connection) -> str:
        database_path = connection.execute("PRAGMA database_list").fetchone()[2]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        wait_for_peer = False
        if database_path and user_version == 30:
            with seen_lock:
                connection_key = id(connection)
                if connection_key not in seen_connections:
                    seen_connections.add(connection_key)
                    wait_for_peer = len(seen_connections) == 1
                    if len(seen_connections) == 2:
                        second_connection_reached_prior_check.set()
        if wait_for_peer:
            # Before migration inspection was serialized, both connections
            # reached this point with a stale v30 decision and replayed v31.
            # With the lock held first, the peer cannot reach this v30 seam.
            second_connection_reached_prior_check.wait(timeout=1.0)
        return original_fingerprint(connection)

    monkeypatch.setattr(
        store_module, "_schema_fingerprint", synchronized_prior_fingerprint
    )
    start = threading.Barrier(2)

    def reopen(now_ms: int) -> int:
        start.wait(timeout=5)
        with GatewayStateStore.open(path, now_ms=now_ms) as reopened:
            return reopened._connection.execute("PRAGMA user_version").fetchone()[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(reopen, 1_700), executor.submit(reopen, 1_701))
        assert tuple(future.result(timeout=10) for future in futures) == (31, 31)


@pytest.mark.parametrize(
    "failed_table",
    (
        "verification_registry_snapshot",
        "verification_plan",
        "verification_plan_activation",
        "composition_activation_registration",
        "composition_executable_plan",
    ),
)
def test_each_bundle_insert_failure_rolls_back_the_whole_uow(
    tmp_path: Path,
    failed_table: str,
) -> None:
    path = tmp_path / f"gateway-fail-{failed_table}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        store._connection.execute(
            f"""
            CREATE TEMP TRIGGER force_{failed_table}_insert_failure
            BEFORE INSERT ON {failed_table}
            BEGIN
                SELECT RAISE(ABORT, 'forced bundle insert failure');
            END
            """
        )

        with pytest.raises(sqlite3.IntegrityError, match="forced bundle insert"):
            _persist_executable(store, material)

        assert store._connection.in_transaction is False
        for table in (
            "verification_registry_snapshot",
            "verification_plan",
            "verification_plan_activation",
            "composition_activation_registration",
            "composition_executable_plan",
            "object_owners",
        ):
            assert store._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0] == 0
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_object_owner_insert_failure_rolls_back_the_whole_uow(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-fail-object-owner.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        _prepare_exact_object_input(store, material)
        store._connection.execute(
            """
            CREATE TEMP TRIGGER force_object_owner_insert_failure
            BEFORE INSERT ON object_owners
            BEGIN
                SELECT RAISE(ABORT, 'forced object owner insert failure');
            END
            """
        )

        with pytest.raises(sqlite3.IntegrityError, match="forced object owner insert"):
            _persist_executable(store, material)

        assert store._connection.in_transaction is False
        for table in (
            "verification_registry_snapshot",
            "verification_plan",
            "verification_plan_activation",
            "composition_activation_registration",
            "composition_executable_plan",
            "object_owners",
        ):
            assert store._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0] == 0
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_parent_marker_failure_rolls_back_the_whole_uow(tmp_path: Path) -> None:
    path = tmp_path / "gateway-fail-parent-marker.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        store._connection.execute(
            """
            CREATE TEMP TRIGGER force_parent_marker_failure
            BEFORE UPDATE OF executable_plan_required
            ON composition_activation_registration
            BEGIN
                SELECT RAISE(ABORT, 'forced parent marker failure');
            END
            """
        )

        with pytest.raises(sqlite3.IntegrityError, match="forced parent marker"):
            _persist_executable(store, material)

        assert store._connection.in_transaction is False
        for table in (
            "verification_registry_snapshot",
            "verification_plan",
            "verification_plan_activation",
            "composition_activation_registration",
            "composition_executable_plan",
            "object_owners",
        ):
            assert store._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0] == 0
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_restart_recovers_companion_then_expiry_preserves_audit_body(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        created = _persist_executable(store, material)
        executable_id = created.record.executable_plan.executable_plan_id
        registration_id = created.record.executable_plan.registration_id

    with GatewayStateStore.open(path, now_ms=2_000) as reopened:
        recovered = reopened.recover_active_executable_composition_plans(
            now_ms=2_000
        )
        assert len(recovered) == 1
        assert recovered[0].recovered_after_restart is True
        assert recovered[0].executable_plan.executable_plan_id == executable_id
        assert reopened.get_active_executable_composition_plan(
            registration_id, now_ms=2_000
        ).executable_plan.executable_plan_id == executable_id

    with GatewayStateStore.open(path, now_ms=2_600) as expired:
        assert expired.get_active_executable_composition_plan(
            registration_id, now_ms=2_600
        ) is None
        assert expired.recover_active_executable_composition_plans(
            now_ms=2_600
        ) == ()
        historical = expired.get_executable_composition_plan_record(executable_id)
        assert historical is not None
        assert historical.executable_plan.registration_id == registration_id
        registration = expired.get_limited_activation_registration_record(
            registration_id
        )
        assert registration is not None
        assert registration.state == "EXPIRED"
        assert expired._connection.execute(
            "SELECT count(*) FROM composition_executable_plan"
        ).fetchone()[0] == 1
        assert expired.health_check(now_ms=2_600, full=True).healthy


def test_exact_replay_preserves_first_plan_and_registration_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        first = _persist_executable(store, material, recorded_at_ms=1_600)
        replay = _persist_executable(store, material, recorded_at_ms=1_700)

        assert first.created_by_this_call is True
        assert first.duplicate is False
        assert replay.created_by_this_call is False
        assert replay.duplicate is True
        assert replay.activation_bundle.duplicate is True
        assert replay.record.executable_plan == first.record.executable_plan
        assert replay.record.executable_plan.sealed_at_ms == 1_600
        assert store._connection.execute(
            "SELECT count(*) FROM composition_activation_registration"
        ).fetchone()[0] == 1
        assert store._connection.execute(
            "SELECT count(*) FROM composition_executable_plan"
        ).fetchone()[0] == 1


def test_registration_identity_rejects_different_companion_material(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-companion-conflict.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        original = _persist_executable(store, material, recorded_at_ms=1_600)
        original_plan = original.record.executable_plan

        first, second = material["step_bindings"]
        replacement_literal = _hashed(
            LiteralValueBindingV1(value="alternate-mode", sha256=ZERO_SHA256)
        )
        replacement_slots = tuple(
            _hashed(
                slot.model_copy(
                    update={
                        "value_binding": replacement_literal,
                        "sha256": ZERO_SHA256,
                    }
                )
            )
            if slot.destination_json_pointer == "/mode"
            else slot
            for slot in first.argument_slots
        )
        material["step_bindings"] = (
            _hashed(
                first.model_copy(
                    update={
                        "argument_slots": replacement_slots,
                        "sha256": ZERO_SHA256,
                    }
                )
            ),
            second,
        )

        with pytest.raises(StoreConflictError, match="identity was reused"):
            _persist_executable(store, material, recorded_at_ms=1_700)

        assert store._connection.execute(
            "SELECT count(*) FROM composition_executable_plan"
        ).fetchone()[0] == 1
        recovered = store.get_executable_composition_plan_for_registration(
            original_plan.registration_id
        )
        assert recovered is not None
        assert recovered.executable_plan == original_plan
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_exact_replay_compares_canonical_json_not_python_sequence_shape(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-canonical-sequence.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        first_step = material["step_bindings"][0]
        sequence_literal = _hashed(
            LiteralValueBindingV1(
                value=("metadata", "only"),
                sha256=ZERO_SHA256,
            )
        )
        replacement_slots = tuple(
            _hashed(
                slot.model_copy(
                    update={
                        "value_binding": sequence_literal,
                        "sha256": ZERO_SHA256,
                    }
                )
            )
            if slot.destination_json_pointer == "/mode"
            else slot
            for slot in first_step.argument_slots
        )
        material["step_bindings"] = (
            _hashed(
                first_step.model_copy(
                    update={
                        "argument_slots": replacement_slots,
                        "sha256": ZERO_SHA256,
                    }
                )
            ),
            material["step_bindings"][1],
        )

        first = _persist_executable(store, material, recorded_at_ms=1_600)
        replay = _persist_executable(store, material, recorded_at_ms=1_700)

        assert first.created_by_this_call is True
        assert replay.duplicate is True
        assert replay.record.executable_plan == first.record.executable_plan


def test_two_store_connections_converge_on_one_atomic_executable_bundle(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.sqlite3"
    bootstrap = GatewayStateStore.open(path, now_ms=1_000)
    material = _compile_material(bootstrap, tmp_path)
    bootstrap.close()
    first = GatewayStateStore.open(path, now_ms=1_400)
    second = GatewayStateStore.open(path, now_ms=1_400)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = tuple(
                pool.map(
                    lambda store: _persist_executable(
                        store, material, recorded_at_ms=1_600
                    ),
                    (first, second),
                )
            )
        assert sum(result.created_by_this_call for result in outcomes) == 1
        assert sum(result.duplicate for result in outcomes) == 1
        assert len(
            {
                result.record.executable_plan.executable_plan_id
                for result in outcomes
            }
        ) == 1
        assert first._connection.execute(
            "SELECT count(*) FROM composition_activation_registration"
        ).fetchone()[0] == 1
        assert first._connection.execute(
            "SELECT count(*) FROM composition_executable_plan"
        ).fetchone()[0] == 1
        assert first.health_check(now_ms=1_700, full=True).healthy
    finally:
        first.close()
        second.close()


def test_persisted_executable_plan_rejects_replace_rehashed_update_and_delete(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-append-only-plan.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        created = _persist_executable(store, material)
        original = created.record.executable_plan
        first = original.step_bindings[0]
        forged_args = dict(first.args_skeleton)
        forged_args["post_seal_injected"] = "ATTACK"
        forged_first = first.model_copy(
            update={"args_skeleton": forged_args, "sha256": ZERO_SHA256}
        ).with_computed_sha256()
        forged_steps = (forged_first,) + original.step_bindings[1:]
        forged_bindings_sha256 = computed_execution_bindings_sha256(
            workspace=original.workspace,
            plan_inputs=original.plan_inputs,
            step_bindings=forged_steps,
            final_output_aliases=original.final_output_aliases,
        )
        forged = original.model_copy(
            update={
                "step_bindings": forged_steps,
                "execution_bindings_sha256": forged_bindings_sha256,
                "executable_plan_id": "ecp_" + ZERO_SHA256,
                "executable_plan_sha256": ZERO_SHA256,
            }
        ).with_computed_identity()
        forged_json = canonical_executable_plan_json(forged)
        assert (
            ExecutableCompositionPlanV1.model_validate_json(
                forged_json, strict=True
            )
            == forged
        )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="composition executable plan is immutable",
        ):
            store._connection.execute(
                "UPDATE composition_executable_plan "
                "SET executable_plan_id = ?, execution_bindings_sha256 = ?, "
                "executable_plan_json = ?, executable_plan_sha256 = ? "
                "WHERE registration_id = ?",
                (
                    forged.executable_plan_id,
                    forged.execution_bindings_sha256,
                    forged_json,
                    forged.executable_plan_sha256,
                    forged.registration_id,
                ),
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="composition executable plan is immutable",
        ):
            store._connection.execute(
                "DELETE FROM composition_executable_plan WHERE registration_id = ?",
                (original.registration_id,),
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="composition executable plan is immutable",
        ):
            store._connection.execute(
                "INSERT OR REPLACE INTO composition_executable_plan "
                "SELECT * FROM composition_executable_plan WHERE registration_id = ?",
                (original.registration_id,),
            )

        reread = store.get_executable_composition_plan_record(
            original.executable_plan_id
        )
        assert reread.executable_plan == original
        assert store.health_check(now_ms=1_700, full=True).healthy


@pytest.mark.parametrize(
    ("write_form", "insert_clause", "conflict_clause"),
    (
        ("insert", "INSERT INTO", ""),
        ("replace", "INSERT OR REPLACE INTO", ""),
        (
            "upsert-do-nothing",
            "INSERT INTO",
            "ON CONFLICT(request_id, run_id, generation) DO NOTHING",
        ),
    ),
)
def test_plan_lineage_identity_guard_rejects_insert_replace_and_upsert_without_replacing_original(
    tmp_path: Path,
    write_form: str,
    insert_clause: str,
    conflict_clause: str,
) -> None:
    path = tmp_path / f"gateway-plan-lineage-{write_form}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        original_material = _compile_material(store, tmp_path)
        created = _persist_executable(store, original_material)
        original = created.record.executable_plan

        candidate_material = _compile_material(store, tmp_path, lineage_seed="8")
        candidate_registration = _persist_registration_only(
            store, candidate_material
        )
        candidate = _compile_executable(
            candidate_material,
            registration_record=candidate_registration.record,
        )
        original_identities = (
            original.executable_plan_id,
            original.registration_id,
            original.composition_activation_id,
            original.composition_plan_id,
            original.executable_plan_sha256,
        )
        candidate_identities = (
            candidate.executable_plan_id,
            candidate.registration_id,
            candidate.composition_activation_id,
            candidate.composition_plan_id,
            candidate.executable_plan_sha256,
        )
        assert all(
            candidate_identity != original_identity
            for candidate_identity, original_identity in zip(
                candidate_identities, original_identities, strict=True
            )
        )
        assert (
            candidate.request_id,
            candidate.run_id,
            candidate.generation,
        ) != (original.request_id, original.run_id, original.generation)

        original_row = store._connection.execute(
            "SELECT * FROM composition_executable_plan "
            "WHERE executable_plan_id = ?",
            (original.executable_plan_id,),
        ).fetchone()
        assert original_row is not None
        original_row_values = dict(original_row)
        candidate_json = canonical_executable_plan_json(candidate)
        insert_values = (
            candidate.executable_plan_id,
            candidate.registration_id,
            candidate.registration_sha256,
            candidate.composition_activation_id,
            candidate.composition_activation_sha256,
            candidate.composition_plan_id,
            candidate.composition_plan_sha256,
            candidate.execution_bindings_sha256,
            candidate.action_registry_sha256,
            candidate.verification_registry_sha256,
            candidate.verification_plan_id,
            candidate.verification_plan_sha256,
            original.request_id,
            original.run_id,
            original.generation,
            candidate.principal_scope_hash,
            candidate.world_state_sha256,
            candidate.source_manifest_sha256,
            candidate.capability_manifest_sha256,
            candidate.workspace.workspace_id,
            candidate.workspace.workspace_scope_sha256,
            candidate.sealed_at_ms,
            candidate.expires_at_ms,
            len(candidate.step_bindings),
            candidate_json,
            candidate.executable_plan_sha256,
        )
        statement = f"""
            {insert_clause} composition_executable_plan(
                executable_plan_id, registration_id, registration_sha256,
                composition_activation_id, composition_activation_sha256,
                composition_plan_id, composition_plan_sha256,
                execution_bindings_sha256, action_registry_sha256,
                verification_registry_sha256, verification_plan_id,
                verification_plan_sha256, request_id, run_id, generation,
                principal_scope_hash, world_state_sha256,
                source_manifest_sha256, capability_manifest_sha256,
                workspace_id, workspace_scope_hash, sealed_at_ms,
                expires_at_ms, step_count, executable_plan_json,
                executable_plan_sha256
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            {conflict_clause}
        """

        with pytest.raises(
            sqlite3.IntegrityError,
            match="composition executable plan is immutable",
        ):
            store._connection.execute(statement, insert_values)

        assert store._connection.execute(
            "SELECT count(*) FROM composition_executable_plan"
        ).fetchone()[0] == 1
        assert store._connection.execute(
            "SELECT 1 FROM composition_executable_plan "
            "WHERE executable_plan_id = ?",
            (candidate.executable_plan_id,),
        ).fetchone() is None
        lineage_row = store._connection.execute(
            "SELECT executable_plan_id, executable_plan_sha256 "
            "FROM composition_executable_plan "
            "WHERE request_id = ? AND run_id = ? AND generation = ?",
            (original.request_id, original.run_id, original.generation),
        ).fetchone()
        assert lineage_row is not None
        assert tuple(lineage_row) == (
            original.executable_plan_id,
            original.executable_plan_sha256,
        )
        preserved_row = store._connection.execute(
            "SELECT * FROM composition_executable_plan "
            "WHERE executable_plan_id = ?",
            (original.executable_plan_id,),
        ).fetchone()
        assert preserved_row is not None
        assert dict(preserved_row) == original_row_values
        reread = store.get_executable_composition_plan_record(
            original.executable_plan_id
        )
        assert reread is not None
        assert reread.executable_plan == original
        assert store.health_check(now_ms=1_700, full=True).healthy


@pytest.mark.parametrize("tamper_kind", ("column", "json"))
def test_integrity_scan_detects_executable_column_and_json_tampering(
    tmp_path: Path,
    tamper_kind: str,
) -> None:
    path = tmp_path / f"gateway-{tamper_kind}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        created = _persist_executable(store, material)
        executable_id = created.record.executable_plan.executable_plan_id
        with _temporarily_disable_trigger_for_corruption(
            store._connection,
            "composition_executable_plan_immutable_update_guard",
        ):
            if tamper_kind == "column":
                store._connection.execute(
                    "UPDATE composition_executable_plan "
                    "SET workspace_scope_hash = ? WHERE executable_plan_id = ?",
                    ("f" * 64, executable_id),
                )
            else:
                encoded = store._connection.execute(
                    "SELECT executable_plan_json FROM composition_executable_plan "
                    "WHERE executable_plan_id = ?",
                    (executable_id,),
                ).fetchone()[0]
                payload = json.loads(encoded)
                payload["workspace"]["workspace_id"] = "workspace-tampered"
                store._connection.execute(
                    "UPDATE composition_executable_plan "
                    "SET executable_plan_json = ? WHERE executable_plan_id = ?",
                    (
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        executable_id,
                    ),
                )
        registration_id = created.record.executable_plan.registration_id
        with pytest.raises(StoreCorruptionError):
            store.get_limited_activation_registration_record(registration_id)
        with pytest.raises(StoreCorruptionError):
            store.get_limited_activation_registration(registration_id)
        with pytest.raises(StoreCorruptionError):
            store.get_active_limited_activation_registration(
                registration_id, now_ms=1_700
            )
        with pytest.raises(StoreCorruptionError):
            store.recover_limited_activation_registrations(now_ms=1_700)
        with pytest.raises(StoreCorruptionError):
            store.expire_limited_activation_registrations(now_ms=2_500)
        with pytest.raises(StoreCorruptionError):
            _persist_registration_only(store, material, recorded_at_ms=1_700)
        health = store.health_check(now_ms=1_700, full=True)
        assert health.healthy is False
        assert health.reason_code == "store.check.failed"


def test_crossed_companion_body_is_explicit_corruption(tmp_path: Path) -> None:
    path = tmp_path / "gateway-crossed-companion.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        first_material = _compile_material(store, tmp_path)
        first = _persist_executable(store, first_material)
        second_material = _compile_material(
            store, tmp_path, lineage_seed="8"
        )
        second = _persist_executable(store, second_material)
        first_id = first.record.executable_plan.executable_plan_id
        crossed_json = canonical_executable_plan_json(
            second.record.executable_plan
        )
        with _temporarily_disable_trigger_for_corruption(
            store._connection,
            "composition_executable_plan_immutable_update_guard",
        ):
            store._connection.execute(
                "UPDATE composition_executable_plan SET executable_plan_json = ? "
                "WHERE executable_plan_id = ?",
                (crossed_json, first_id),
            )

        with pytest.raises(
            StoreCorruptionError,
            match="stored executable composition plan is invalid",
        ):
            store.get_executable_composition_plan_record(first_id)
        health = store.health_check(now_ms=1_700, full=True)
        assert health.healthy is False
        assert health.reason_code == "store.check.failed"


def test_store_schema_rejects_oversized_executable_plan_json(tmp_path: Path) -> None:
    path = tmp_path / "gateway-oversized-plan.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        created = _persist_executable(store, material)
        with _temporarily_disable_trigger_for_corruption(
            store._connection,
            "composition_executable_plan_immutable_update_guard",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                store._connection.execute(
                    "UPDATE composition_executable_plan "
                    "SET executable_plan_json = ? WHERE executable_plan_id = ?",
                    (
                        json.dumps(
                            "x" * MAX_STORED_EXECUTABLE_PLAN_JSON_BYTES,
                            separators=(",", ":"),
                        ),
                        created.record.executable_plan.executable_plan_id,
                    ),
                )
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_active_lookup_fails_closed_after_generation_drift(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        created = _persist_executable(store, material)
        registration_id = created.record.executable_plan.registration_id
        store.release_generation(
            material["legacy_plan"].request_id,
            released_at_ms=1_700,
        )
        with pytest.raises(StoreConflictError, match="current generation"):
            store.get_active_executable_composition_plan(
                registration_id, now_ms=1_800
            )
        assert store.recover_active_executable_composition_plans(
            now_ms=1_800
        ) == ()


@pytest.mark.parametrize(
    "lookup_name",
    (
        "get_active_limited_activation_registration",
        "get_active_executable_composition_plan",
    ),
)
def test_active_lookup_reads_one_registration_scoped_companion_without_full_scan(
    tmp_path: Path,
    lookup_name: str,
) -> None:
    path = tmp_path / f"gateway-{lookup_name}-query-count.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        created = _persist_executable(store, material)
        registration_id = created.record.executable_plan.registration_id

        traced: list[str] = []
        store._connection.set_trace_callback(traced.append)
        try:
            record = getattr(store, lookup_name)(registration_id, now_ms=1_700)
        finally:
            store._connection.set_trace_callback(None)

        assert record is not None
        statements = tuple(
            " ".join(statement.split()).upper() for statement in traced
        )
        companion_reads = tuple(
            statement
            for statement in statements
            if statement.startswith("SELECT")
            and "FROM COMPOSITION_EXECUTABLE_PLAN" in statement
        )
        assert len(companion_reads) == 1
        assert "WHERE REGISTRATION_ID =" in companion_reads[0]
        assert not any(
            "FROM COMPOSITION_EXECUTABLE_PLAN ORDER BY EXECUTABLE_PLAN_ID"
            in statement
            for statement in statements
        )


@pytest.mark.parametrize(
    ("operation_name", "now_ms"),
    (
        ("recover_limited_activation_registrations", 1_700),
        ("expire_limited_activation_registrations", 2_500),
    ),
)
def test_p7b_recovery_and_expiry_verify_each_selected_companion_once(
    tmp_path: Path,
    operation_name: str,
    now_ms: int,
) -> None:
    path = tmp_path / f"gateway-{operation_name}-query-count.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        first_material = _compile_material(store, tmp_path)
        second_material = _compile_material(store, tmp_path, lineage_seed="8")
        first = _persist_executable(store, first_material)
        second = _persist_executable(store, second_material)
        registration_ids = (
            first.record.executable_plan.registration_id,
            second.record.executable_plan.registration_id,
        )

        traced: list[str] = []
        store._connection.set_trace_callback(traced.append)
        try:
            result = getattr(store, operation_name)(now_ms=now_ms)
        finally:
            store._connection.set_trace_callback(None)

        assert len(result) == 2
        statements = tuple(
            " ".join(statement.split()).upper() for statement in traced
        )
        companion_reads = tuple(
            statement
            for statement in statements
            if statement.startswith("SELECT")
            and "FROM COMPOSITION_EXECUTABLE_PLAN" in statement
        )
        assert len(companion_reads) == 2
        assert all(
            "WHERE REGISTRATION_ID =" in statement
            for statement in companion_reads
        )
        assert all(
            sum(registration_id.upper() in statement for statement in companion_reads)
            == 1
            for registration_id in registration_ids
        )
        assert not any(
            "FROM COMPOSITION_EXECUTABLE_PLAN ORDER BY EXECUTABLE_PLAN_ID"
            in statement
            for statement in statements
        )


def test_gateway_store_exposes_no_raw_executable_plan_write_sink() -> None:
    assert (
        "_put_executable_composition_plan_from_bundle"
        not in GatewayStateStore.__dict__
    )
    assert not hasattr(
        store_module, "_EXECUTABLE_COMPOSITION_BUNDLE_WRITE_TOKEN"
    )


@pytest.mark.parametrize(
    ("risk", "effect", "allow_shell", "allow_python"),
    (
        ("A1", "read", False, False),
        ("A2", "write", False, False),
        ("A3", "execute", False, False),
        ("A4", "read", True, False),
        ("A4", "read", False, True),
    ),
)
def test_current_registry_materialization_rejects_non_a0_read_verify(
    tmp_path: Path,
    risk: str,
    effect: str,
    allow_shell: bool,
    allow_python: bool,
) -> None:
    path = tmp_path / (
        f"gateway-{risk}-{effect}-{int(allow_shell)}-{int(allow_python)}.sqlite3"
    )
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        with pytest.raises(ValueError, match="not valid A0 read/verify"):
            _compile_material(
                store,
                tmp_path,
                risk=risk,
                effect=effect,
                allow_shell=allow_shell,
                allow_python=allow_python,
            )


@pytest.mark.parametrize("drift", ("identity", "risk"))
def test_step_permission_is_self_contained_a0_read_verify(
    tmp_path: Path,
    drift: str,
) -> None:
    path = tmp_path / f"gateway-permission-{drift}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
    first, second = material["step_bindings"]
    if drift == "identity":
        permission = second.permission
    else:
        permission = first.permission.model_copy(
            update={
                "registry_risk": "A1",
                "effective_risk": "A1",
                "permission_sha256": ZERO_SHA256,
            }
        ).with_computed_sha256()
    payload = first.model_dump(mode="python")
    payload.update(
        {
            "permission": permission,
            "permission_sha256": permission.permission_sha256,
            "sha256": ZERO_SHA256,
        }
    )
    with pytest.raises(ValueError, match="not valid A0 read/verify"):
        StepExecutionBindingV1.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    ("side_effect", "effective_risk"),
    (("external_send", "A0"), ("destructive", "A4")),
)
def test_step_permission_external_send_and_destructive_fail_closed(
    tmp_path: Path,
    side_effect: str,
    effective_risk: str,
) -> None:
    path = tmp_path / f"gateway-permission-{side_effect}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
    first = material["step_bindings"][0]
    permission = first.permission.model_copy(
        update={
            "effective_risk": effective_risk,
            "allowed_side_effects": (side_effect,),
            "permission_sha256": ZERO_SHA256,
        }
    ).with_computed_sha256()
    payload = first.model_dump(mode="python")
    payload.update(
        {
            "permission": permission,
            "permission_sha256": permission.permission_sha256,
            "sha256": ZERO_SHA256,
        }
    )

    with pytest.raises(ValueError, match="not valid A0 read/verify"):
        StepExecutionBindingV1.model_validate(payload, strict=True)


def test_step_permission_source_manifest_must_match_plan_manifest(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-permission-manifest.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        executable = _compile_executable(material)
    first, second = executable.step_bindings
    drifted_permission = first.permission.model_copy(
        update={
            "source_manifest_sha256": "f" * 64,
            "permission_sha256": ZERO_SHA256,
        }
    ).with_computed_sha256()
    drifted_first = _hashed(
        first.model_copy(
            update={
                "permission": drifted_permission,
                "permission_sha256": drifted_permission.permission_sha256,
                "sha256": ZERO_SHA256,
            }
        )
    )
    drifted = executable.model_copy(
        update={"step_bindings": (drifted_first, second)}
    ).with_computed_identity()

    with pytest.raises(
        ValueError,
        match="step permission source manifest disagrees with the plan",
    ):
        ExecutableCompositionPlanV1.model_validate(
            drifted.model_dump(mode="python"), strict=True
        )


def test_public_bundle_revalidates_model_copy_bypassed_permission(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-public-bundle-revalidation.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
        first, second = material["step_bindings"]
        unsafe_permission = first.permission.model_copy(
            update={
                "registry_risk": "A1",
                "effective_risk": "A1",
                "permission_sha256": ZERO_SHA256,
            }
        ).with_computed_sha256()
        unsafe_first = first.model_copy(
            update={
                "permission": unsafe_permission,
                "permission_sha256": unsafe_permission.permission_sha256,
                "sha256": ZERO_SHA256,
            }
        ).with_computed_sha256()
        material["step_bindings"] = (unsafe_first, second)

        with pytest.raises(
            ExecutableCompositionPlanError,
            match="executable_plan.step.binding_mismatch",
        ):
            _persist_executable(store, material)

        for table in (
            "verification_registry_snapshot",
            "verification_plan",
            "verification_plan_activation",
            "composition_activation_registration",
            "composition_executable_plan",
        ):
            assert store._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0] == 0
        assert store.health_check(now_ms=1_700, full=True).healthy


def test_unknown_primitive_side_effect_is_rejected_fail_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-unknown-primitive-side-effect.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(
            store,
            tmp_path,
            primitive_side_effects=("network_mutate",),
        )
    with pytest.raises(
        ExecutableCompositionPlanError,
        match="executable_plan.step.not_a0_read_verify",
    ):
        _compile_executable(material)


@pytest.mark.parametrize("side_effect", ("external_send", "destructive"))
def test_current_primitive_external_send_and_destructive_fail_closed(
    tmp_path: Path,
    side_effect: str,
) -> None:
    path = tmp_path / f"gateway-{side_effect}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(
            store,
            tmp_path,
            primitive_side_effects=(side_effect,),
        )
    with pytest.raises(
        ExecutableCompositionPlanError,
        match="executable_plan.step.not_a0_read_verify",
    ):
        _compile_executable(material)


def test_argument_string_interpolation_is_rejected_at_contract_boundary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
    first = material["step_bindings"][0]
    payload = first.model_dump(mode="python")
    payload.update(
        {
            "args_skeleton": {
                "artifact_id": None,
                "mode": "${model_supplied_value}",
            },
            "sha256": ZERO_SHA256,
        }
    )
    with pytest.raises(ValueError, match="string interpolation"):
        StepExecutionBindingV1.model_validate(payload, strict=True)


@pytest.mark.parametrize("binding_kind", ("literal", "plan_input", "step_output"))
def test_target_slot_must_resolve_statically_to_a_string(
    tmp_path: Path,
    binding_kind: str,
) -> None:
    path = tmp_path / f"gateway-target-{binding_kind}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
    first, second = material["step_bindings"]
    if binding_kind == "literal":
        target = _hashed(
            LiteralValueBindingV1(value={"not": "a string"}, sha256=ZERO_SHA256)
        )
        replacement_index = 0
    elif binding_kind == "plan_input":
        plan_input = material["plan_inputs"][0]
        target = _hashed(
            PlanInputValueBindingV1(
                input_id=plan_input.input_id,
                input_sha256=plan_input.sha256,
                json_pointer="/artifact",
                sha256=ZERO_SHA256,
            )
        )
        replacement_index = 0
    else:
        declaration = first.output_declarations[0]
        target = _hashed(
            StepOutputValueBindingV1(
                producer_step_id=first.step_id,
                output_binding_id=declaration.output_binding_id,
                output_declaration_sha256=declaration.sha256,
                sha256=ZERO_SHA256,
            )
        )
        replacement_index = 1
    selected = material["step_bindings"][replacement_index]
    replacement = _hashed(
        selected.model_copy(
            update={
                "target_skeleton": None,
                "target_slot": target,
                "sha256": ZERO_SHA256,
            }
        )
    )
    bindings = list(material["step_bindings"])
    bindings[replacement_index] = replacement
    material["step_bindings"] = tuple(bindings)

    with pytest.raises(
        ExecutableCompositionPlanError,
        match=(
            "outside the P7C.0 A0 batch"
            if binding_kind == "step_output"
            else "must resolve statically to a string"
        ),
    ):
        _compile_executable(material)


@pytest.mark.parametrize("value_kind", ("literal", "inline"))
def test_interpolation_is_rejected_in_bound_values(value_kind: str) -> None:
    value = {"nested": ["${untrusted}"]}
    with pytest.raises(ValueError, match="string interpolation"):
        if value_kind == "literal":
            LiteralValueBindingV1(value=value, sha256=ZERO_SHA256)
        else:
            PlanInputV1(
                input_id="input.interpolation",
                input_kind="INLINE_JSON",
                inline_value=value,
                value_schema_sha256=H,
                value_sha256=canonical_sha256(value),
                sha256=ZERO_SHA256,
            )


def test_deep_inline_json_fails_with_a_bounded_contract_error() -> None:
    value: object = "leaf"
    for _ in range(70):
        value = [value]
    with pytest.raises(ValueError, match="JSON depth limit"):
        PlanInputV1(
            input_id="input.too-deep",
            input_kind="INLINE_JSON",
            inline_value=value,
            value_schema_sha256=H,
            value_sha256=H,
            sha256=ZERO_SHA256,
        )


def test_each_dynamic_json_field_has_a_one_mib_canonical_bound(
    tmp_path: Path,
) -> None:
    oversized = "x" * 1_048_577
    with pytest.raises(ValueError, match="canonical JSON byte limit"):
        LiteralValueBindingV1(value=oversized, sha256=ZERO_SHA256)
    with pytest.raises(ValueError, match="canonical JSON byte limit"):
        PlanInputV1(
            input_id="input.too-large",
            input_kind="INLINE_JSON",
            inline_value=oversized,
            value_schema_sha256=H,
            value_sha256=H,
            sha256=ZERO_SHA256,
        )

    path = tmp_path / "gateway-large-args.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
    payload = material["step_bindings"][0].model_dump(mode="python")
    payload.update(
        {
            "args_skeleton": {"oversized": oversized},
            "argument_slots": (),
            "sha256": ZERO_SHA256,
        }
    )
    with pytest.raises(ValueError, match="canonical JSON byte limit"):
        StepExecutionBindingV1.model_validate(payload, strict=True)


def test_wide_inline_json_fails_with_a_bounded_node_error() -> None:
    with pytest.raises(ValueError, match="JSON node limit"):
        PlanInputV1(
            input_id="input.too-wide",
            input_kind="INLINE_JSON",
            inline_value=[None] * 50_000,
            value_schema_sha256=H,
            value_sha256=H,
            sha256=ZERO_SHA256,
        )


@pytest.mark.parametrize(
    ("reference_kind", "detail"),
    (
        ("self", "step cannot consume its own output"),
        ("unknown", "STEP_OUTPUT reference is missing or hash-drifted"),
    ),
)
def test_step_output_self_and_unknown_declaration_fail_closed(
    tmp_path: Path,
    reference_kind: str,
    detail: str,
) -> None:
    path = tmp_path / f"gateway-{reference_kind}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
    second = material["step_bindings"][1]
    if reference_kind == "self":
        declaration = second.output_declarations[0]
        producer_step_id = second.step_id
        output_binding_id = declaration.output_binding_id
        declaration_sha256 = declaration.sha256
    else:
        producer_step_id = material["step_bindings"][0].step_id
        output_binding_id = "out.unknown"
        declaration_sha256 = "f" * 64
    reference = _hashed(
        StepOutputValueBindingV1(
            producer_step_id=producer_step_id,
            output_binding_id=output_binding_id,
            output_declaration_sha256=declaration_sha256,
            sha256=ZERO_SHA256,
        )
    )
    slot = _hashed(
        second.argument_slots[0].model_copy(
            update={"value_binding": reference, "sha256": ZERO_SHA256}
        )
    )
    material["step_bindings"] = (
        material["step_bindings"][0],
        _hashed(
            second.model_copy(
                update={"argument_slots": (slot,), "sha256": ZERO_SHA256}
            )
        ),
    )
    with pytest.raises(
        ExecutableCompositionPlanError,
        match=detail,
    ):
        _compile_executable(material)


def test_step_output_requires_an_explicit_dependency_edge(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(
            store,
            tmp_path,
            declare_dependency=False,
        )
    with pytest.raises(
        ExecutableCompositionPlanError,
        match="every STEP_OUTPUT reference requires an explicit dependency edge",
    ):
        _compile_executable(material)


def test_step_output_from_a_future_step_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
    first, second = material["step_bindings"]
    future_declaration = second.output_declarations[0]
    future_reference = _hashed(
        StepOutputValueBindingV1(
            producer_step_id=second.step_id,
            output_binding_id=future_declaration.output_binding_id,
            output_declaration_sha256=future_declaration.sha256,
            sha256=ZERO_SHA256,
        )
    )
    replacement_slot = _hashed(
        first.argument_slots[0].model_copy(
            update={"value_binding": future_reference, "sha256": ZERO_SHA256}
        )
    )
    future_consumer = _hashed(
        first.model_copy(
            update={
                "depends_on": (second.step_id,),
                "argument_slots": (replacement_slot, first.argument_slots[1]),
                "sha256": ZERO_SHA256,
            }
        )
    )
    with pytest.raises(
        ValueError,
        match="STEP_OUTPUT producer must precede its consumer",
    ):
        _validate_dataflow(
            plan_inputs=material["plan_inputs"],
            step_bindings=(future_consumer, second),
            final_output_aliases=material["final_output_aliases"],
        )


def test_argument_destination_json_pointers_must_not_overlap(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
    first = material["step_bindings"][0]
    parent_slot = _hashed(
        ArgumentSlotV1(
            destination_json_pointer="/artifact",
            value_binding=first.argument_slots[0].value_binding,
            sha256=ZERO_SHA256,
        )
    )
    child_slot = _hashed(
        ArgumentSlotV1(
            destination_json_pointer="/artifact/id",
            value_binding=first.argument_slots[1].value_binding,
            sha256=ZERO_SHA256,
        )
    )
    payload = first.model_dump(mode="python")
    payload.update(
        {
            "args_skeleton": {"artifact": {"id": None}},
            "argument_slots": (parent_slot, child_slot),
            "sha256": ZERO_SHA256,
        }
    )
    with pytest.raises(ValueError, match="ancestor conflict"):
        StepExecutionBindingV1.model_validate(payload, strict=True)


def test_argument_destination_json_pointers_must_be_unique(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway-duplicate-argument-destination.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
    first = material["step_bindings"][0]
    duplicate = first.argument_slots[0]
    payload = first.model_dump(mode="python")
    payload.update(
        {
            "argument_slots": (duplicate, duplicate),
            "sha256": ZERO_SHA256,
        }
    )

    with pytest.raises(ValueError, match="duplicate destinations"):
        StepExecutionBindingV1.model_validate(payload, strict=True)


def test_array_pointer_indices_require_ascii_digits(tmp_path: Path) -> None:
    path = tmp_path / "gateway-unicode-array-index.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
    first = material["step_bindings"][0]
    ascii_slot = _hashed(
        ArgumentSlotV1(
            destination_json_pointer="/items/1",
            value_binding=first.argument_slots[0].value_binding,
            sha256=ZERO_SHA256,
        )
    )
    unicode_alias = _hashed(
        ArgumentSlotV1(
            destination_json_pointer="/items/\u0661",
            value_binding=first.argument_slots[1].value_binding,
            sha256=ZERO_SHA256,
        )
    )
    payload = first.model_dump(mode="python")
    payload.update(
        {
            "args_skeleton": {"items": ["occupied", None]},
            "argument_slots": (ascii_slot, unicode_alias),
            "sha256": ZERO_SHA256,
        }
    )
    with pytest.raises(ValueError, match="invalid array index"):
        StepExecutionBindingV1.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    ("pointer", "detail"),
    (
        ("artifact/id", "must be an RFC 6901 JSON Pointer"),
        ("/artifact/~2id", "contains an invalid JSON Pointer escape"),
    ),
)
def test_step_output_result_selector_requires_rfc6901_pointer(
    pointer: str,
    detail: str,
) -> None:
    with pytest.raises(ValueError, match=detail):
        OutputDeclarationV1(
            output_binding_id="out.invalid-pointer",
            source_kind="RESULT_PAYLOAD",
            json_pointer=pointer,
            value_schema_sha256=H,
            sha256=ZERO_SHA256,
        )


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    (
        ("binding_kind", "STEP_RESULT", "union_tag_invalid"),
        ("binding_kind", "PLAN_INPUT", "missing"),
        ("record_type", "PLAN_INPUT", "literal_error"),
    ),
)
def test_step_output_binding_rejects_discriminant_type_confusion(
    field: str,
    value: str,
    error_type: str,
) -> None:
    reference = _hashed(
        StepOutputValueBindingV1(
            producer_step_id="step.01",
            output_binding_id="out.01",
            output_declaration_sha256=H,
            sha256=ZERO_SHA256,
        )
    )
    payload = ArgumentSlotV1(
        destination_json_pointer="/result",
        value_binding=reference,
        sha256=ZERO_SHA256,
    ).model_dump(mode="python")
    payload["value_binding"][field] = value

    with pytest.raises(ValidationError) as exc_info:
        ArgumentSlotV1.model_validate(payload, strict=True)

    assert error_type in {item["type"] for item in exc_info.value.errors()}


@pytest.mark.parametrize("token", ("\u0661", "01"))
def test_unresolved_result_pointer_rejects_ambiguous_array_tokens(
    token: str,
) -> None:
    with pytest.raises(ValueError, match="ambiguous array-index token"):
        OutputDeclarationV1(
            output_binding_id="out.ambiguous",
            source_kind="RESULT_PAYLOAD",
            json_pointer=f"/items/{token}",
            value_schema_sha256=H,
            sha256=ZERO_SHA256,
        )


@pytest.mark.parametrize("drift_field", ("workspace_id", "workspace_scope_sha256"))
def test_workspace_identity_and_scope_drift_fail_closed(
    tmp_path: Path,
    drift_field: str,
) -> None:
    path = tmp_path / f"gateway-{drift_field}.sqlite3"
    with GatewayStateStore.open(path, now_ms=1_000) as store:
        material = _compile_material(store, tmp_path)
    drift_value = "workspace-drifted" if drift_field == "workspace_id" else "f" * 64
    material["workspace"] = _hashed(
        material["workspace"].model_copy(
            update={drift_field: drift_value, "sha256": ZERO_SHA256}
        )
    )
    with pytest.raises(
        ExecutableCompositionPlanError,
        match="executable_plan.workspace.authority_mismatch",
    ):
        _compile_executable(material)
