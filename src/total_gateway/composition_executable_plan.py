"""Immutable executable composition-plan bindings for P7C.0.

This module turns a previously compiled ``CapabilityCompositionPlanV1`` into
an entirely data-only executable contract.  It deliberately owns no Store
connection, scheduler, Policy decision, Ticket, Grant, Runtime call,
verification verdict, or Completion decision.

The legacy plan remains frozen.  Dynamic data flow is expressed with typed
whole-value references and JSON Pointers; string interpolation is forbidden.
At execution time the existing Gateway must still resolve those references and
issue one ordinary Policy -> Ticket -> Grant chain for every step.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Self, TypeAlias
import unicodedata

from pydantic import Field, model_validator

from contracts import ActionRegistrySnapshot, canonical_json_bytes, canonical_sha256
from contracts.capability_composition import (
    CapabilityCompositionPlanV1,
    CompositionProposalV1,
    SourceRevisionRefV1,
)
from contracts.execution import ObjectGrant
from contracts.models import ActionId, ContractModel, OpaqueId, RequestId, RunId, Sha256
from contracts.policy import ActionPermission
from world_understanding.capability_composition.compiler import (
    compile_capability_composition_plan,
    plan_has_valid_sha256,
)
from world_understanding.capability_composition.models import (
    CompositionCandidateSnapshotV1,
    CompositionCompileContextV1,
)

from .composition_activation_store import (
    LimitedActivationStoreRecord,
    computed_limited_activation_lifecycle_sha256,
)


EXECUTABLE_COMPOSITION_PLAN_SCHEMA = "tiangong.composition-executable-plan.v1"
MAX_STORED_EXECUTABLE_PLAN_JSON_BYTES = 16_777_216
ZERO_SHA256 = "0" * 64

_INTERPOLATION_MARKERS = ("{{", "}}", "${")
_SAFE_A0_SIDE_EFFECTS = frozenset({"none", "read"})
_FORBIDDEN_RESOURCE_CLASSES = frozenset({"shell", "python"})
_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 50_000
_MAX_JSON_VALUE_BYTES = 1_048_576
_MAX_EXECUTION_BINDINGS_BYTES = MAX_STORED_EXECUTABLE_PLAN_JSON_BYTES


class ExecutableCompositionPlanError(ValueError):
    """Stable fail-closed error raised by the P7C.0 compiler."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def _error(code: str, detail: str = "") -> None:
    raise ExecutableCompositionPlanError(code, detail)


def _require_nfc(value: str, *, label: str) -> None:
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{label} must use NFC Unicode normalization")


def _require_canonical_json_value(
    value: Any,
    *,
    label: str,
    max_bytes: int = _MAX_JSON_VALUE_BYTES,
) -> None:
    """Reject pathological JSON before the recursive canonical encoder runs."""

    stack: list[tuple[Any, int]] = [(value, 0)]
    node_count = 0
    while stack:
        current, depth = stack.pop()
        node_count += 1
        if node_count > _MAX_JSON_NODES:
            raise ValueError(f"{label} exceeds the JSON node limit")
        if depth > _MAX_JSON_DEPTH:
            raise ValueError(f"{label} exceeds the JSON depth limit")
        if isinstance(current, Mapping):
            if any(not isinstance(key, str) for key in current):
                raise ValueError(f"{label} contains a non-string object key")
            if len(current) > _MAX_JSON_NODES - node_count:
                raise ValueError(f"{label} exceeds the JSON node limit")
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            if len(current) > _MAX_JSON_NODES - node_count:
                raise ValueError(f"{label} exceeds the JSON node limit")
            stack.extend((item, depth + 1) for item in current)
    try:
        encoded = canonical_json_bytes(value)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise ValueError(f"{label} is not canonical gateway JSON") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds the canonical JSON byte limit")


def _contains_interpolation(value: Any) -> bool:
    if isinstance(value, str):
        return any(marker in value for marker in _INTERPOLATION_MARKERS)
    if isinstance(value, Mapping):
        return any(
            _contains_interpolation(key) or _contains_interpolation(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return any(_contains_interpolation(item) for item in value)
    return False


def _permission_is_safe_a0_binding(
    permission: ActionPermission,
    *,
    action_id: str,
    action_version: str,
) -> bool:
    """Return whether a persisted permission is self-contained A0 authority."""

    return (
        permission.has_valid_sha256()
        and permission.action_id == action_id
        and permission.action_version == action_version
        and permission.registry_risk == "A0"
        and permission.effective_risk == "A0"
        and permission.effect in {"read", "verify"}
        and not permission.allow_shell
        and not permission.allow_python
        and not permission.requires_confirmation
        and set(permission.allowed_side_effects).issubset(_SAFE_A0_SIDE_EFFECTS)
    )


def _json_pointer_tokens(pointer: str, *, label: str) -> tuple[str, ...]:
    if not isinstance(pointer, str) or len(pointer) > 2048:
        raise ValueError(f"{label} is invalid")
    _require_nfc(pointer, label=label)
    if pointer == "":
        return ()
    if not pointer.startswith("/"):
        raise ValueError(f"{label} must be an RFC 6901 JSON Pointer")
    tokens: list[str] = []
    for encoded in pointer[1:].split("/"):
        index = 0
        decoded: list[str] = []
        while index < len(encoded):
            character = encoded[index]
            if character != "~":
                decoded.append(character)
                index += 1
                continue
            if index + 1 >= len(encoded) or encoded[index + 1] not in {"0", "1"}:
                raise ValueError(f"{label} contains an invalid JSON Pointer escape")
            decoded.append("~" if encoded[index + 1] == "0" else "/")
            index += 2
        tokens.append("".join(decoded))
    return tuple(tokens)


def _json_pointer_value(document: Any, pointer: str, *, label: str) -> Any:
    current = document
    for token in _json_pointer_tokens(pointer, label=label):
        if isinstance(current, Mapping):
            if token not in current:
                raise ValueError(f"{label} does not resolve in its document")
            current = current[token]
            continue
        if isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            if token == "-" or not token or any(
                character < "0" or character > "9" for character in token
            ):
                raise ValueError(f"{label} has an invalid array index")
            if len(token) > 1 and token.startswith("0"):
                raise ValueError(f"{label} has a non-canonical array index")
            offset = int(token)
            if offset >= len(current):
                raise ValueError(f"{label} array index is out of range")
            current = current[offset]
            continue
        raise ValueError(f"{label} traverses a scalar value")
    return current


def _validate_unresolved_json_pointer(pointer: str, *, label: str) -> None:
    """Reject numeric-looking tokens with ambiguous future array semantics."""

    for token in _json_pointer_tokens(pointer, label=label):
        if token.isdigit() and (
            any(character < "0" or character > "9" for character in token)
            or (len(token) > 1 and token.startswith("0"))
        ):
            raise ValueError(f"{label} has an ambiguous array-index token")


def _validate_non_overlapping_pointers(
    pointers: tuple[str, ...], *, label: str
) -> None:
    tokenized = tuple(
        (pointer, _json_pointer_tokens(pointer, label=label)) for pointer in pointers
    )
    if len({tokens for _, tokens in tokenized}) != len(tokenized):
        raise ValueError(f"{label} contains duplicate destinations")
    terminal = object()
    trie: dict[object, Any] = {}
    for pointer, tokens in tokenized:
        node = trie
        for token in tokens:
            ancestor = node.get(terminal)
            if ancestor is not None:
                raise ValueError(
                    f"{label} contains an ancestor conflict: "
                    f"{ancestor!r} and {pointer!r}"
                )
            child = node.get(token)
            if child is None:
                child = {}
                node[token] = child
            node = child
        if node:
            descendant = node
            while terminal not in descendant:
                child_key = next(
                    key for key in descendant if key is not terminal
                )
                descendant = descendant[child_key]
            raise ValueError(
                f"{label} contains an ancestor conflict: "
                f"{pointer!r} and {descendant[terminal]!r}"
            )
        node[terminal] = pointer


class _CanonicalHashModel(ContractModel):
    """Shared canonical hash mechanics for immutable nested plan records."""

    _hash_field: ClassVar[str] = "sha256"
    sha256: Sha256

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={self._hash_field})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        try:
            return self.sha256 == self.computed_sha256()
        except (TypeError, ValueError, RecursionError, OverflowError):
            return False

    def with_computed_sha256(self) -> Self:
        return self.model_copy(update={self._hash_field: self.computed_sha256()})


class WorkspaceBindingV1(_CanonicalHashModel):
    schema_version: Literal[EXECUTABLE_COMPOSITION_PLAN_SCHEMA] = (
        EXECUTABLE_COMPOSITION_PLAN_SCHEMA
    )
    record_type: Literal["WORKSPACE"] = "WORKSPACE"
    workspace_id: OpaqueId
    workspace_root: str = Field(min_length=1, max_length=4096)
    workspace_scope_sha256: Sha256

    @model_validator(mode="after")
    def validate_workspace(self) -> Self:
        _require_nfc(self.workspace_root, label="workspace root")
        if _contains_interpolation(self.workspace_root):
            raise ValueError("workspace root must not contain string interpolation")
        return self


def computed_execution_bindings_sha256(
    *,
    workspace: WorkspaceBindingV1,
    plan_inputs: tuple[PlanInputV1, ...],
    step_bindings: tuple[StepExecutionBindingV1, ...],
    final_output_aliases: tuple[FinalOutputAliasV1, ...],
) -> str:
    """Hash the complete materialization independently from the legacy plan."""

    payload = {
        "domain": "tiangong.composition-execution-bindings.v1",
        "workspace": workspace.model_dump(mode="json"),
        "plan_inputs": [item.model_dump(mode="json") for item in plan_inputs],
        "step_bindings": [item.model_dump(mode="json") for item in step_bindings],
        "final_output_aliases": [
            item.model_dump(mode="json") for item in final_output_aliases
        ],
    }
    _require_canonical_json_value(
        payload,
        label="execution bindings",
        max_bytes=_MAX_EXECUTION_BINDINGS_BYTES,
    )
    return canonical_sha256(payload)


class PlanInputV1(_CanonicalHashModel):
    """One restart-safe plan input, either inline JSON or a pinned object grant."""

    schema_version: Literal[EXECUTABLE_COMPOSITION_PLAN_SCHEMA] = (
        EXECUTABLE_COMPOSITION_PLAN_SCHEMA
    )
    record_type: Literal["PLAN_INPUT"] = "PLAN_INPUT"
    input_id: OpaqueId
    input_kind: Literal["INLINE_JSON", "OBJECT_GRANT"]
    inline_value: Any = None
    object_grant: ObjectGrant | None = None
    value_schema_sha256: Sha256
    value_sha256: Sha256
    contains_secret: Literal[False] = False

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if self.input_kind == "INLINE_JSON":
            if self.object_grant is not None:
                raise ValueError("inline plan input must not carry an ObjectGrant")
            _require_canonical_json_value(self.inline_value, label="inline plan input")
            if _contains_interpolation(self.inline_value):
                raise ValueError(
                    "inline plan input must not contain string interpolation"
                )
            if self.value_sha256 != canonical_sha256(self.inline_value):
                raise ValueError("inline plan input value hash is invalid")
        else:
            if self.object_grant is None or self.inline_value is not None:
                raise ValueError(
                    "object plan input requires exactly one pinned ObjectGrant"
                )
            if self.value_sha256 != self.object_grant.sha256:
                raise ValueError("object plan input content hash is invalid")
        return self


class LiteralValueBindingV1(_CanonicalHashModel):
    schema_version: Literal[EXECUTABLE_COMPOSITION_PLAN_SCHEMA] = (
        EXECUTABLE_COMPOSITION_PLAN_SCHEMA
    )
    record_type: Literal["VALUE_BINDING"] = "VALUE_BINDING"
    binding_kind: Literal["LITERAL"] = "LITERAL"
    value: Any

    @model_validator(mode="after")
    def validate_literal(self) -> Self:
        _require_canonical_json_value(self.value, label="literal value binding")
        if _contains_interpolation(self.value):
            raise ValueError(
                "literal value binding must not contain string interpolation"
            )
        return self


class PlanInputValueBindingV1(_CanonicalHashModel):
    schema_version: Literal[EXECUTABLE_COMPOSITION_PLAN_SCHEMA] = (
        EXECUTABLE_COMPOSITION_PLAN_SCHEMA
    )
    record_type: Literal["VALUE_BINDING"] = "VALUE_BINDING"
    binding_kind: Literal["PLAN_INPUT"] = "PLAN_INPUT"
    input_id: OpaqueId
    input_sha256: Sha256
    json_pointer: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def validate_pointer(self) -> Self:
        _json_pointer_tokens(self.json_pointer, label="plan-input JSON Pointer")
        return self


class StepOutputValueBindingV1(_CanonicalHashModel):
    schema_version: Literal[EXECUTABLE_COMPOSITION_PLAN_SCHEMA] = (
        EXECUTABLE_COMPOSITION_PLAN_SCHEMA
    )
    record_type: Literal["VALUE_BINDING"] = "VALUE_BINDING"
    binding_kind: Literal["STEP_OUTPUT"] = "STEP_OUTPUT"
    producer_step_id: OpaqueId
    output_binding_id: OpaqueId
    output_declaration_sha256: Sha256


CompositionValueBindingV1: TypeAlias = Annotated[
    LiteralValueBindingV1
    | PlanInputValueBindingV1
    | StepOutputValueBindingV1,
    Field(discriminator="binding_kind"),
]


class ArgumentSlotV1(_CanonicalHashModel):
    """Replace one complete JSON leaf; partial string substitution is forbidden."""

    schema_version: Literal[EXECUTABLE_COMPOSITION_PLAN_SCHEMA] = (
        EXECUTABLE_COMPOSITION_PLAN_SCHEMA
    )
    record_type: Literal["ARGUMENT_SLOT"] = "ARGUMENT_SLOT"
    destination_json_pointer: str = Field(min_length=1, max_length=2048)
    value_binding: CompositionValueBindingV1

    @model_validator(mode="after")
    def validate_slot(self) -> Self:
        if not self.value_binding.has_valid_sha256():
            raise ValueError("argument slot contains an invalid value-binding hash")
        tokens = _json_pointer_tokens(
            self.destination_json_pointer,
            label="argument-slot destination JSON Pointer",
        )
        if not tokens:
            raise ValueError("argument slot cannot replace the entire args object")
        return self


class OutputDeclarationV1(_CanonicalHashModel):
    """A typed extraction point that may feed a later STEP_OUTPUT reference."""

    schema_version: Literal[EXECUTABLE_COMPOSITION_PLAN_SCHEMA] = (
        EXECUTABLE_COMPOSITION_PLAN_SCHEMA
    )
    record_type: Literal["OUTPUT_DECLARATION"] = "OUTPUT_DECLARATION"
    output_binding_id: OpaqueId
    source_kind: Literal["RESULT_PAYLOAD", "OUTPUT_OBJECT_REF", "FACT_ID"]
    json_pointer: str | None = Field(default=None, max_length=2048)
    ordinal: int | None = Field(default=None, ge=0, le=255)
    value_schema_sha256: Sha256
    requires_verified_commit: Literal[True] = True

    @model_validator(mode="after")
    def validate_selector(self) -> Self:
        if self.source_kind == "RESULT_PAYLOAD":
            if self.json_pointer is None or self.ordinal is not None:
                raise ValueError(
                    "result-payload output requires only a JSON Pointer selector"
                )
            _validate_unresolved_json_pointer(
                self.json_pointer, label="result-payload output JSON Pointer"
            )
        elif self.json_pointer is not None or self.ordinal is None:
            raise ValueError(
                "object/fact output requires only a non-negative ordinal selector"
            )
        return self


class FinalOutputAliasV1(_CanonicalHashModel):
    schema_version: Literal[EXECUTABLE_COMPOSITION_PLAN_SCHEMA] = (
        EXECUTABLE_COMPOSITION_PLAN_SCHEMA
    )
    record_type: Literal["FINAL_OUTPUT_ALIAS"] = "FINAL_OUTPUT_ALIAS"
    alias: OpaqueId
    value_binding: StepOutputValueBindingV1

    @model_validator(mode="after")
    def validate_alias(self) -> Self:
        if not self.value_binding.has_valid_sha256():
            raise ValueError("final output alias contains an invalid output reference")
        return self


class StepExecutionBindingV1(_CanonicalHashModel):
    """Complete non-authorizing invocation template for one legacy plan step."""

    schema_version: Literal[EXECUTABLE_COMPOSITION_PLAN_SCHEMA] = (
        EXECUTABLE_COMPOSITION_PLAN_SCHEMA
    )
    record_type: Literal["STEP_EXECUTION_BINDING"] = "STEP_EXECUTION_BINDING"
    step_id: OpaqueId
    candidate_id: OpaqueId
    candidate_binding_sha256: Sha256
    action_id: ActionId
    action_version: str = Field(min_length=1, max_length=80)
    source_revision: SourceRevisionRefV1
    argument_schema_sha256: Sha256
    result_schema_sha256: Sha256
    permission: ActionPermission
    permission_sha256: Sha256
    depends_on: tuple[OpaqueId, ...] = ()
    target_skeleton: str | None = Field(default=None, max_length=4096)
    target_slot: CompositionValueBindingV1 | None = None
    args_skeleton: dict[str, Any]
    argument_slots: tuple[ArgumentSlotV1, ...] = Field(default=(), max_length=512)
    output_declarations: tuple[OutputDeclarationV1, ...] = Field(
        default=(), max_length=256
    )
    authorizes: Literal[False] = False
    may_execute: Literal[False] = False

    @model_validator(mode="after")
    def validate_step_binding(self) -> Self:
        if self.depends_on != tuple(sorted(set(self.depends_on))):
            raise ValueError("step dependencies must be sorted and unique")
        if (self.target_skeleton is None) == (self.target_slot is None):
            raise ValueError(
                "step target requires exactly one literal skeleton or typed slot"
            )
        if self.target_skeleton is not None:
            _require_nfc(self.target_skeleton, label="target skeleton")
            if _contains_interpolation(self.target_skeleton):
                raise ValueError("target skeleton must not contain string interpolation")
        elif self.target_slot is not None and not self.target_slot.has_valid_sha256():
            raise ValueError("target slot contains an invalid value-binding hash")

        _require_canonical_json_value(self.args_skeleton, label="args skeleton")
        if _contains_interpolation(self.args_skeleton):
            raise ValueError("args skeleton must not contain string interpolation")

        slot_pointers = tuple(
            item.destination_json_pointer for item in self.argument_slots
        )
        if slot_pointers != tuple(sorted(slot_pointers)):
            raise ValueError("argument slots must be ordered by destination pointer")
        if any(not item.has_valid_sha256() for item in self.argument_slots):
            raise ValueError("step contains an invalid argument-slot hash")
        _validate_non_overlapping_pointers(
            slot_pointers, label="argument-slot destination"
        )
        for pointer in slot_pointers:
            if _json_pointer_value(
                self.args_skeleton,
                pointer,
                label="argument-slot destination",
            ) is not None:
                raise ValueError(
                    "argument-slot destination must identify an explicit null hole"
                )

        output_ids = tuple(
            item.output_binding_id for item in self.output_declarations
        )
        if output_ids != tuple(sorted(set(output_ids))):
            raise ValueError("output declarations must be sorted and unique")
        if any(not item.has_valid_sha256() for item in self.output_declarations):
            raise ValueError("step contains an invalid output-declaration hash")
        selectors = tuple(
            (item.source_kind, item.json_pointer, item.ordinal)
            for item in self.output_declarations
        )
        if len(selectors) != len(set(selectors)):
            raise ValueError("step output declarations duplicate one extraction point")
        if (
            self.permission_sha256 != self.permission.permission_sha256
            or not _permission_is_safe_a0_binding(
                self.permission,
                action_id=self.action_id,
                action_version=self.action_version,
            )
        ):
            raise ValueError("step permission binding is not valid A0 read/verify")
        return self


def _iter_value_bindings(
    step: StepExecutionBindingV1,
) -> tuple[CompositionValueBindingV1, ...]:
    target = () if step.target_slot is None else (step.target_slot,)
    return target + tuple(item.value_binding for item in step.argument_slots)


def _validate_dataflow(
    *,
    plan_inputs: tuple[PlanInputV1, ...],
    step_bindings: tuple[StepExecutionBindingV1, ...],
    final_output_aliases: tuple[FinalOutputAliasV1, ...],
) -> None:
    inputs = {item.input_id: item for item in plan_inputs}
    steps = {item.step_id: item for item in step_bindings}
    step_index = {item.step_id: index for index, item in enumerate(step_bindings)}
    outputs: dict[tuple[str, str], OutputDeclarationV1] = {}
    output_ids: set[str] = set()
    for step in step_bindings:
        for declaration in step.output_declarations:
            key = (step.step_id, declaration.output_binding_id)
            if key in outputs or declaration.output_binding_id in output_ids:
                raise ValueError("output binding ids must be globally unique")
            outputs[key] = declaration
            output_ids.add(declaration.output_binding_id)

    used_inputs: set[str] = set()
    used_outputs: set[tuple[str, str]] = set()

    def validate_value(
        value: CompositionValueBindingV1, *, consumer_step: StepExecutionBindingV1 | None
    ) -> None:
        if not value.has_valid_sha256():
            raise ValueError("dataflow contains an invalid value-binding hash")
        if isinstance(value, LiteralValueBindingV1):
            return
        if isinstance(value, PlanInputValueBindingV1):
            plan_input = inputs.get(value.input_id)
            if plan_input is None or plan_input.sha256 != value.input_sha256:
                raise ValueError("PLAN_INPUT reference is missing or hash-drifted")
            if plan_input.input_kind == "OBJECT_GRANT":
                if value.json_pointer != "":
                    raise ValueError("ObjectGrant plan input only supports the root value")
            else:
                _json_pointer_value(
                    plan_input.inline_value,
                    value.json_pointer,
                    label="PLAN_INPUT JSON Pointer",
                )
            used_inputs.add(value.input_id)
            return

        key = (value.producer_step_id, value.output_binding_id)
        declaration = outputs.get(key)
        if (
            declaration is None
            or declaration.sha256 != value.output_declaration_sha256
        ):
            raise ValueError("STEP_OUTPUT reference is missing or hash-drifted")
        if consumer_step is not None:
            if value.producer_step_id == consumer_step.step_id:
                raise ValueError("step cannot consume its own output")
            if value.producer_step_id not in consumer_step.depends_on:
                raise ValueError(
                    "every STEP_OUTPUT reference requires an explicit dependency edge"
                )
            if step_index[value.producer_step_id] >= step_index[consumer_step.step_id]:
                raise ValueError("STEP_OUTPUT producer must precede its consumer")
        used_outputs.add(key)

    def validate_target(step: StepExecutionBindingV1) -> None:
        """P7C.0 only seals targets whose string value is known at compile time."""

        value = step.target_slot
        if value is None:
            return
        if isinstance(value, LiteralValueBindingV1):
            resolved = value.value
        elif isinstance(value, PlanInputValueBindingV1):
            plan_input = inputs.get(value.input_id)
            if plan_input is None or plan_input.sha256 != value.input_sha256:
                raise ValueError("target PLAN_INPUT is missing or hash-drifted")
            if plan_input.input_kind != "INLINE_JSON":
                raise ValueError("ObjectGrant plan input cannot materialize a target")
            resolved = _json_pointer_value(
                plan_input.inline_value,
                value.json_pointer,
                label="target PLAN_INPUT JSON Pointer",
            )
        else:
            raise ValueError(
                "dynamic STEP_OUTPUT targets are outside the P7C.0 A0 batch"
            )
        if not isinstance(resolved, str):
            raise ValueError("step target slot must resolve statically to a string")
        _require_nfc(resolved, label="resolved target")
        if _contains_interpolation(resolved):
            raise ValueError("resolved target must not contain string interpolation")

    for step in step_bindings:
        for value in _iter_value_bindings(step):
            validate_value(value, consumer_step=step)
        validate_target(step)
    for alias in final_output_aliases:
        validate_value(alias.value_binding, consumer_step=None)

    if used_inputs != set(inputs):
        raise ValueError("plan inputs must be used exactly by typed value references")
    if used_outputs != set(outputs):
        raise ValueError(
            "every declared step output must feed a step or a final output alias"
        )


def _validate_workspace_authority(workspace: WorkspaceBindingV1) -> None:
    """Bind the data contract to the existing Omni workspace derivation."""

    raw = Path(workspace.workspace_root)
    if not raw.is_absolute() or not raw.exists() or not raw.is_dir() or raw.is_symlink():
        _error("executable_plan.workspace.not_real_directory")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        _error("executable_plan.workspace.resolve_failed", str(exc))
    resolved_text = str(resolved)
    if workspace.workspace_root != resolved_text:
        _error("executable_plan.workspace.root_not_canonical")
    expected_workspace_id = "workspace-" + canonical_sha256(resolved_text)
    normalized = os.path.normcase(unicodedata.normalize("NFC", resolved_text))
    expected_scope = canonical_sha256({"normalized_workspace": normalized})
    if (
        workspace.workspace_id != expected_workspace_id
        or workspace.workspace_scope_sha256 != expected_scope
    ):
        _error("executable_plan.workspace.authority_mismatch")


def _validate_object_grant_inputs(plan_inputs: tuple[PlanInputV1, ...]) -> None:
    grants = tuple(
        item.object_grant
        for item in plan_inputs
        if item.input_kind == "OBJECT_GRANT"
    )
    if not grants:
        return
    if any(grant is None for grant in grants):
        _error("executable_plan.object_grant.missing")
    materialized = tuple(grant for grant in grants if grant is not None)
    object_revisions = tuple(
        (grant.object_id, grant.revision) for grant in materialized
    )
    if len(object_revisions) != len(set(object_revisions)):
        _error("executable_plan.object_grant.duplicate")
    object_identity: dict[str, tuple[str, int, str, str, str, str]] = {}
    for grant in materialized:
        projection = (
            grant.sha256,
            grant.size_bytes,
            grant.mime,
            grant.tenant_id,
            grant.link_account_id,
            grant.conversation_scope_hash,
        )
        previous = object_identity.setdefault(grant.object_id, projection)
        if previous != projection:
            _error("executable_plan.object_grant.object_identity_conflict")
    scopes = {
        (
            grant.tenant_id,
            grant.link_account_id,
            grant.conversation_scope_hash,
        )
        for grant in materialized
    }
    if len(scopes) != 1:
        _error("executable_plan.object_grant.scope_mismatch")


class ExecutableCompositionPlanV1(ContractModel):
    """Content-addressed, restart-safe companion to the frozen legacy plan."""

    schema_version: Literal[EXECUTABLE_COMPOSITION_PLAN_SCHEMA] = (
        EXECUTABLE_COMPOSITION_PLAN_SCHEMA
    )
    executable_plan_id: OpaqueId
    legacy_plan: CapabilityCompositionPlanV1
    composition_plan_id: OpaqueId
    composition_plan_sha256: Sha256
    dependency_graph_sha256: Sha256
    legacy_bindings_sha256: Sha256
    execution_bindings_sha256: Sha256
    proposal_sha256: Sha256
    candidate_snapshot_sha256: Sha256
    compile_context_sha256: Sha256
    request_id: RequestId
    run_id: RunId
    generation: int = Field(ge=0)
    principal_scope_hash: Sha256
    world_state_sha256: Sha256
    source_manifest_sha256: Sha256
    capability_manifest_sha256: Sha256
    action_registry_sha256: Sha256
    registration_id: OpaqueId
    registration_sha256: Sha256
    registration_lifecycle_sha256: Sha256
    composition_activation_id: OpaqueId
    composition_activation_sha256: Sha256
    verification_plan_id: OpaqueId
    verification_plan_sha256: Sha256
    verification_plan_activation_id: OpaqueId
    verification_registry_sha256: Sha256
    workspace: WorkspaceBindingV1
    plan_inputs: tuple[PlanInputV1, ...] = Field(default=(), max_length=256)
    step_bindings: tuple[StepExecutionBindingV1, ...] = Field(min_length=1, max_length=128)
    final_output_aliases: tuple[FinalOutputAliasV1, ...] = Field(
        default=(), max_length=256
    )
    sealed_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    eligibility_only: Literal[True] = True
    schema_compatibility_proven: Literal[False] = False
    dispatch_schema_validation_required: Literal[True] = True
    path_policy_enforced: Literal[False] = False
    dispatch_path_policy_validation_required: Literal[True] = True
    authorizes: Literal[False] = False
    confirms: Literal[False] = False
    changes_risk: Literal[False] = False
    may_execute: Literal[False] = False
    issues_ticket: Literal[False] = False
    issues_grant: Literal[False] = False
    may_record_verification: Literal[False] = False
    may_complete: Literal[False] = False
    executable_plan_sha256: Sha256

    def payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"executable_plan_id", "executable_plan_sha256"},
        )

    def computed_executable_plan_sha256(self) -> str:
        payload = self.payload()
        _require_canonical_json_value(
            payload,
            label="executable composition plan",
            max_bytes=_MAX_EXECUTION_BINDINGS_BYTES,
        )
        return canonical_sha256(payload)

    def computed_executable_plan_id(self) -> str:
        return "ecp_" + self.computed_executable_plan_sha256()

    def has_valid_identity(self) -> bool:
        try:
            return (
                self.executable_plan_sha256
                == self.computed_executable_plan_sha256()
                and self.executable_plan_id == self.computed_executable_plan_id()
            )
        except (TypeError, ValueError, RecursionError, OverflowError):
            return False

    def with_computed_identity(self) -> Self:
        digest = self.computed_executable_plan_sha256()
        return self.model_copy(
            update={
                "executable_plan_id": "ecp_" + digest,
                "executable_plan_sha256": digest,
            }
        )

    @model_validator(mode="after")
    def validate_plan_structure(self) -> Self:
        _require_canonical_json_value(
            self.model_dump(mode="json"),
            label="stored executable composition plan",
            max_bytes=MAX_STORED_EXECUTABLE_PLAN_JSON_BYTES,
        )
        if not plan_has_valid_sha256(self.legacy_plan):
            raise ValueError("embedded legacy composition plan hash is invalid")
        expected_plan_projection = (
            self.legacy_plan.plan_id,
            self.legacy_plan.plan_sha256,
            self.legacy_plan.dependency_graph_sha256,
            self.legacy_plan.bindings_sha256,
            self.legacy_plan.request_id,
            self.legacy_plan.run_id,
            self.legacy_plan.generation,
            self.legacy_plan.principal_scope_hash,
            self.legacy_plan.world_state_sha256,
            self.legacy_plan.source_manifest_sha256,
            self.legacy_plan.capability_manifest_sha256,
        )
        actual_plan_projection = (
            self.composition_plan_id,
            self.composition_plan_sha256,
            self.dependency_graph_sha256,
            self.legacy_bindings_sha256,
            self.request_id,
            self.run_id,
            self.generation,
            self.principal_scope_hash,
            self.world_state_sha256,
            self.source_manifest_sha256,
            self.capability_manifest_sha256,
        )
        if actual_plan_projection != expected_plan_projection:
            raise ValueError("executable plan projection disagrees with legacy plan")
        if not (
            self.legacy_plan.created_at_ms
            <= self.sealed_at_ms
            < self.expires_at_ms
        ):
            raise ValueError("executable plan lifetime is invalid")
        expected_lifecycle = computed_limited_activation_lifecycle_sha256(
            registration_id=self.registration_id,
            registration_sha256=self.registration_sha256,
            state="ACTIVE",
            expires_at_ms=self.expires_at_ms,
            expired_at_ms=None,
        )
        if self.registration_lifecycle_sha256 != expected_lifecycle:
            raise ValueError(
                "executable plan does not bind the registration's initial ACTIVE lifecycle"
            )
        if not self.workspace.has_valid_sha256():
            raise ValueError("executable plan workspace hash is invalid")

        input_ids = tuple(item.input_id for item in self.plan_inputs)
        if input_ids != tuple(sorted(set(input_ids))):
            raise ValueError("plan inputs must be sorted and unique")
        if any(not item.has_valid_sha256() for item in self.plan_inputs):
            raise ValueError("executable plan contains an invalid plan-input hash")
        _validate_object_grant_inputs(self.plan_inputs)

        legacy_steps = self.legacy_plan.steps
        if tuple(item.step_id for item in self.step_bindings) != tuple(
            item.step_id for item in legacy_steps
        ):
            raise ValueError("executable step order disagrees with legacy plan")
        if any(not item.has_valid_sha256() for item in self.step_bindings):
            raise ValueError("executable plan contains an invalid step-binding hash")
        if any(
            item.permission.source_manifest_sha256
            != self.capability_manifest_sha256
            for item in self.step_bindings
        ):
            raise ValueError(
                "step permission source manifest disagrees with the plan"
            )
        for binding, legacy in zip(self.step_bindings, legacy_steps, strict=True):
            if (
                binding.action_id != legacy.action_id
                or binding.action_version != legacy.action_version
                or binding.depends_on != legacy.depends_on
            ):
                raise ValueError("executable step disagrees with legacy plan")

        aliases = tuple(item.alias for item in self.final_output_aliases)
        if aliases != tuple(sorted(set(aliases))):
            raise ValueError("final output aliases must be sorted and unique")
        if any(not item.has_valid_sha256() for item in self.final_output_aliases):
            raise ValueError("executable plan contains an invalid final-output hash")
        _validate_dataflow(
            plan_inputs=self.plan_inputs,
            step_bindings=self.step_bindings,
            final_output_aliases=self.final_output_aliases,
        )
        expected_execution_bindings = computed_execution_bindings_sha256(
            workspace=self.workspace,
            plan_inputs=self.plan_inputs,
            step_bindings=self.step_bindings,
            final_output_aliases=self.final_output_aliases,
        )
        if self.execution_bindings_sha256 != expected_execution_bindings:
            raise ValueError("executable-plan materialization hash is invalid")
        return self


def _validate_proposal_graph(proposal: CompositionProposalV1) -> None:
    step_ids = tuple(item.step_id for item in proposal.steps)
    if len(step_ids) != len(set(step_ids)):
        _error("executable_plan.proposal.step_duplicate")
    known = set(step_ids)
    derived_edges = tuple(
        sorted(
            (dependency, step.step_id)
            for step in proposal.steps
            for dependency in step.depends_on
        )
    )
    if any(
        step.step_id in step.depends_on
        or not set(step.depends_on).issubset(known)
        or step.depends_on != tuple(sorted(set(step.depends_on)))
        for step in proposal.steps
    ):
        _error("executable_plan.proposal.dependency_invalid")
    if proposal.dependency_edges != derived_edges:
        _error("executable_plan.proposal.edges_mismatch")
    incoming = {
        step.step_id: len(step.depends_on) for step in proposal.steps
    }
    outgoing: dict[str, list[str]] = {step_id: [] for step_id in step_ids}
    for dependency, dependent in derived_edges:
        outgoing[dependency].append(dependent)
    ready = sorted(step_id for step_id, count in incoming.items() if count == 0)
    visited = 0
    while ready:
        step_id = ready.pop(0)
        visited += 1
        for dependent in sorted(outgoing[step_id]):
            incoming[dependent] -= 1
            if incoming[dependent] == 0:
                ready.append(dependent)
                ready.sort()
    if visited != len(step_ids):
        _error("executable_plan.proposal.dependency_cycle")
    if proposal.control_flow == "SEQUENTIAL":
        expected = tuple(
            sorted(
                (
                    proposal.steps[index - 1].step_id,
                    proposal.steps[index].step_id,
                )
                for index in range(1, len(proposal.steps))
            )
        )
        if proposal.dependency_edges != expected:
            _error("executable_plan.proposal.sequential_mismatch")


def _validate_registration_binding(
    *,
    record: LimitedActivationStoreRecord,
    plan: CapabilityCompositionPlanV1,
    registry: ActionRegistrySnapshot,
) -> None:
    if not isinstance(record, LimitedActivationStoreRecord):
        _error("executable_plan.registration_record.invalid")
    if not record.has_valid_lifecycle() or record.state != "ACTIVE":
        _error("executable_plan.registration.inactive_or_invalid")
    registration = record.registration
    if not record.active_at(registration.registered_at_ms):
        _error("executable_plan.registration.not_active_at_first_write")
    if (
        not registration.has_valid_identity()
        or not registration.eligibility_only
        or registration.authorizes
        or registration.confirms
        or registration.changes_risk
        or registration.may_execute
    ):
        _error("executable_plan.registration.authority_invalid")
    expected = (
        plan.plan_id,
        plan.plan_sha256,
        plan.request_id,
        plan.run_id,
        plan.generation,
        plan.principal_scope_hash,
        plan.world_state_sha256,
        plan.source_manifest_sha256,
        plan.capability_manifest_sha256,
        registry.registry_sha256,
    )
    actual = (
        registration.composition_plan_id,
        registration.composition_plan_sha256,
        registration.request_id,
        registration.run_id,
        registration.generation,
        registration.principal_scope_hash,
        registration.world_state_sha256,
        registration.source_manifest_sha256,
        registration.capability_manifest_sha256,
        registration.action_registry_sha256,
    )
    if actual != expected:
        _error("executable_plan.registration.binding_mismatch")
    if not record.verification_plan_activation_id:
        _error("executable_plan.p19.activation_missing")


def _validate_step_materialization(
    *,
    proposal: CompositionProposalV1,
    candidates: CompositionCandidateSnapshotV1,
    registry: ActionRegistrySnapshot,
    legacy_plan: CapabilityCompositionPlanV1,
    step_bindings: tuple[StepExecutionBindingV1, ...],
) -> None:
    proposed_steps = {item.step_id: item for item in proposal.steps}
    candidate_by_id = candidates.action_by_candidate()
    permission_by_action = {item.action_id: item for item in registry.permissions}
    if tuple(item.step_id for item in step_bindings) != tuple(
        item.step_id for item in legacy_plan.steps
    ):
        _error("executable_plan.step.order_mismatch")
    if any(not item.has_valid_sha256() for item in step_bindings):
        _error("executable_plan.step.hash_invalid")

    selected_actions: set[tuple[str, str]] = set()
    for binding, legacy_step in zip(
        step_bindings, legacy_plan.steps, strict=True
    ):
        proposed = proposed_steps.get(binding.step_id)
        candidate = candidate_by_id.get(binding.candidate_id)
        if proposed is None or candidate is None:
            _error("executable_plan.step.source_missing", binding.step_id)
        primitive = candidate.primitive
        permission = permission_by_action.get(primitive.action_id)
        if proposed.candidate_id != binding.candidate_id:
            _error("executable_plan.step.candidate_mismatch", binding.step_id)
        if (
            binding.candidate_binding_sha256 != candidate.binding_sha256
            or binding.action_id != primitive.action_id
            or binding.action_version != primitive.action_version
            or binding.source_revision != candidate.source_revision
            or binding.argument_schema_sha256 != primitive.argument_schema_sha256
            or binding.result_schema_sha256 != primitive.result_schema_sha256
            or permission is None
            or binding.permission != permission
            or binding.permission_sha256 != permission.permission_sha256
            or not _permission_is_safe_a0_binding(
                permission,
                action_id=binding.action_id,
                action_version=binding.action_version,
            )
            or binding.action_id != legacy_step.action_id
            or binding.action_version != legacy_step.action_version
            or binding.depends_on != proposed.depends_on
            or binding.depends_on != legacy_step.depends_on
        ):
            _error("executable_plan.step.binding_mismatch", binding.step_id)
        if tuple(
            item.output_binding_id for item in binding.output_declarations
        ) != proposed.output_bindings:
            _error("executable_plan.step.output_mismatch", binding.step_id)

        primitive_effect = primitive.effect_class.removeprefix("effect:").casefold()
        primitive_side_effects = {
            item.casefold() for item in primitive.side_effects
        }
        primitive_resources = {
            item.casefold() for item in primitive.resource_scope
        }
        if (
            primitive.risk_floor != "A0"
            or primitive_effect not in {"read", "verify"}
            or not primitive_side_effects.issubset(_SAFE_A0_SIDE_EFFECTS)
            or primitive_resources & _FORBIDDEN_RESOURCE_CLASSES
        ):
            _error("executable_plan.step.not_a0_read_verify", binding.step_id)
        selected_actions.add((binding.action_id, binding.action_version))

    if not selected_actions:
        _error("executable_plan.step.empty")


def compile_executable_composition_plan(
    proposal: CompositionProposalV1,
    candidates: CompositionCandidateSnapshotV1,
    context: CompositionCompileContextV1,
    registry: ActionRegistrySnapshot,
    *,
    legacy_plan: CapabilityCompositionPlanV1,
    plan_inputs: tuple[PlanInputV1, ...],
    step_bindings: tuple[StepExecutionBindingV1, ...],
    final_output_aliases: tuple[FinalOutputAliasV1, ...],
    workspace: WorkspaceBindingV1,
    registration_record: LimitedActivationStoreRecord,
) -> ExecutableCompositionPlanV1:
    """Compile and seal one complete, non-authorizing executable-plan bundle.

    The legacy plan is reconstructed from the original proposal, candidate
    snapshot, compile context and Action Registry.  Every caller-supplied
    invocation binding is then matched back to those authoritative inputs.
    """

    _validate_proposal_graph(proposal)
    try:
        expected_legacy_plan = compile_capability_composition_plan(
            proposal, candidates, context, registry
        )
    except (ValueError, TypeError) as exc:
        code = getattr(exc, "code", "compiler_failed")
        _error("executable_plan.legacy_recompile_failed", str(code))
    if (
        legacy_plan.model_dump(mode="json")
        != expected_legacy_plan.model_dump(mode="json")
    ):
        _error("executable_plan.legacy_plan_mismatch")

    if not workspace.has_valid_sha256():
        _error("executable_plan.workspace.hash_invalid")
    _validate_workspace_authority(workspace)
    if tuple(item.input_id for item in plan_inputs) != tuple(
        sorted({item.input_id for item in plan_inputs})
    ):
        _error("executable_plan.plan_inputs.order_or_identity_invalid")
    if any(not item.has_valid_sha256() for item in plan_inputs):
        _error("executable_plan.plan_input.hash_invalid")
    _validate_object_grant_inputs(plan_inputs)
    if tuple(item.alias for item in final_output_aliases) != tuple(
        sorted({item.alias for item in final_output_aliases})
    ):
        _error("executable_plan.final_output.order_or_identity_invalid")
    if any(not item.has_valid_sha256() for item in final_output_aliases):
        _error("executable_plan.final_output.hash_invalid")
    if tuple(item.alias for item in final_output_aliases) != proposal.output_bindings:
        _error("executable_plan.final_output.proposal_mismatch")

    _validate_registration_binding(
        record=registration_record,
        plan=legacy_plan,
        registry=registry,
    )
    _validate_step_materialization(
        proposal=proposal,
        candidates=candidates,
        registry=registry,
        legacy_plan=legacy_plan,
        step_bindings=step_bindings,
    )

    registration = registration_record.registration
    expected_actions = tuple(
        sorted({(item.action_id, item.action_version) for item in step_bindings})
    )
    registered_actions = tuple(
        zip(
            registration.allowed_action_ids,
            registration.allowed_action_versions,
            strict=True,
        )
    )
    if registered_actions != expected_actions:
        _error("executable_plan.registration.action_set_mismatch")

    execution_bindings_sha256 = computed_execution_bindings_sha256(
        workspace=workspace,
        plan_inputs=plan_inputs,
        step_bindings=step_bindings,
        final_output_aliases=final_output_aliases,
    )

    try:
        executable = ExecutableCompositionPlanV1(
            executable_plan_id="ecp_" + ZERO_SHA256,
            legacy_plan=legacy_plan,
            composition_plan_id=legacy_plan.plan_id,
            composition_plan_sha256=legacy_plan.plan_sha256,
            dependency_graph_sha256=legacy_plan.dependency_graph_sha256,
            legacy_bindings_sha256=legacy_plan.bindings_sha256,
            execution_bindings_sha256=execution_bindings_sha256,
            proposal_sha256=proposal.proposal_sha256,
            candidate_snapshot_sha256=candidates.candidate_snapshot_sha256,
            compile_context_sha256=context.context_sha256,
            request_id=legacy_plan.request_id,
            run_id=legacy_plan.run_id,
            generation=legacy_plan.generation,
            principal_scope_hash=legacy_plan.principal_scope_hash,
            world_state_sha256=legacy_plan.world_state_sha256,
            source_manifest_sha256=legacy_plan.source_manifest_sha256,
            capability_manifest_sha256=legacy_plan.capability_manifest_sha256,
            action_registry_sha256=registry.registry_sha256,
            registration_id=registration.registration_id,
            registration_sha256=registration.registration_sha256,
            registration_lifecycle_sha256=(
                registration_record.lifecycle_sha256
            ),
            composition_activation_id=registration.composition_activation_id,
            composition_activation_sha256=(
                registration.composition_activation_sha256
            ),
            verification_plan_id=registration.verification_plan_id,
            verification_plan_sha256=registration.verification_plan_sha256,
            verification_plan_activation_id=(
                registration_record.verification_plan_activation_id
            ),
            verification_registry_sha256=(
                registration.verification_registry_sha256
            ),
            workspace=workspace,
            plan_inputs=plan_inputs,
            step_bindings=step_bindings,
            final_output_aliases=final_output_aliases,
            sealed_at_ms=registration.registered_at_ms,
            expires_at_ms=registration.expires_at_ms,
            executable_plan_sha256=ZERO_SHA256,
        ).with_computed_identity()
    except ValueError as exc:
        _error("executable_plan.materialization_invalid", str(exc))
    if not executable.has_valid_identity():
        _error("executable_plan.identity_invalid")
    return executable


__all__ = [
    "EXECUTABLE_COMPOSITION_PLAN_SCHEMA",
    "MAX_STORED_EXECUTABLE_PLAN_JSON_BYTES",
    "ArgumentSlotV1",
    "CompositionValueBindingV1",
    "ExecutableCompositionPlanError",
    "ExecutableCompositionPlanV1",
    "FinalOutputAliasV1",
    "LiteralValueBindingV1",
    "OutputDeclarationV1",
    "PlanInputV1",
    "PlanInputValueBindingV1",
    "StepExecutionBindingV1",
    "StepOutputValueBindingV1",
    "WorkspaceBindingV1",
    "compile_executable_composition_plan",
    "computed_execution_bindings_sha256",
]
