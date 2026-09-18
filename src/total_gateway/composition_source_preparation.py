"""Request-bound Source -> P4 preparation on the existing Gateway/World readers.

No model field supplies a path, Source hash, registry, risk or permission. The
operator pins Tool build/Git inputs; P9 resolves the state's signed Method archive.
Preparation and compilation are read-only, not publication or execution approval.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import io
import json
from pathlib import Path
import re
import zipfile

from contracts import ActionRegistrySnapshot, canonical_json_bytes, canonical_sha256
from contracts.capability_composition import CapabilityCompositionPlanV1, CompositionValidationResultV1
from contracts.world_understanding.query import WorldQuery
from world_understanding.capability_composition import (
    build_candidate_snapshot, compile_capability_composition_plan,
    parse_with_single_repair, validate_capability_composition_plan,
)
from world_understanding.capability_composition.parser import ProposalParseOutcomeV1
from world_understanding.capability_composition.models import (
    CompositionCandidateSnapshotV1, CompositionCompileContextV1,
    derive_action_source_revision,
)
from world_understanding.context_output.capability_context import (
    CapabilityContextPacketV1, ProtectedContextIdentityV1, build_capability_context_packet,
)
from world_understanding.context_output.world_reference_context import _address, _validate_snapshot
from world_understanding.domain_contribution import FrameBindingV1

from .action_registry import compile_action_authority
from .tool_source_bundle import _read_verified_bundle, _stage_root
from .tool_source_candidate import _repository_path, _strict_pairs, _invalid_constant
from .tool_source_inputs import ToolSourceInputFileV1, ToolSourceInputsV1
from .tool_source_publication import prepare_tool_source_publication
from .tool_source_world import compile_source_bound_tool_world


@dataclass(frozen=True, slots=True)
class PlanningToolSource:
    """Operator-selected P8 coordinates, never accepted from a Proposal or prompt."""
    repository: Path
    bundle_path: Path
    bundle_sha256: str
    base_commit: str
    candidate_commit: str
    requested_action_ids: tuple[str, ...]
    action_entry_path: str
    repository_id: str
    worktree_id: str

    def identity(self) -> str:
        for commit in (self.base_commit, self.candidate_commit):
            if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
                raise ValueError("COMPOSITION_SOURCE_COMMIT_INVALID")
        if re.fullmatch(r"[0-9a-f]{64}", self.bundle_sha256) is None:
            raise ValueError("COMPOSITION_SOURCE_BUNDLE_HASH_INVALID")
        _stage_root(self.repository)
        _stage_root(self.bundle_path)
        _repository_path(self.action_entry_path)
        value = asdict(self)
        value.update(repository=str(self.repository), bundle_path=str(self.bundle_path))
        return canonical_sha256(value)

    def load(self, registry: ActionRegistrySnapshot):
        """Reconstruct exact existing P2 data, without importing candidate code.

        P8's preparation verifies packaged inputs against native Git. Its remaining
        approval blockers are NOT cleared here. All Action authority must still
        agree with the separately supplied system registry. Installed release admission is still
        a separate P7 check; this object is not a registry-registration receipt.
        """
        self.identity()
        proposal = prepare_tool_source_publication(
            self.repository, bundle_path=self.bundle_path, expected_sha256=self.bundle_sha256,
            base_commit=self.base_commit, candidate_commit=self.candidate_commit,
            requested_action_ids=self.requested_action_ids,
        )
        raw, _index = _read_verified_bundle(self.bundle_path, expected_sha256=self.bundle_sha256)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            report = json.loads(archive.read("build-report.json"), object_pairs_hook=_strict_pairs,
                                parse_constant=_invalid_constant)
        artifact = report["build_artifact"]
        inputs = artifact["source_inputs"]
        measured = ToolSourceInputsV1(**{
            **inputs, "files": tuple(ToolSourceInputFileV1(**item) for item in inputs["files"]),
        })
        entry = next((item for item in measured.files if item.path == self.action_entry_path), None)
        if entry is None:
            raise ValueError("COMPOSITION_SOURCE_ENTRY_NOT_MEASURED")
        manifest = artifact["gateway_manifest"]
        if (type(registry) is not ActionRegistrySnapshot or not registry.has_valid_sha256()
                or compile_action_authority(manifest, generated_at_ms=registry.generated_at_ms).registry != registry):
            raise ValueError("COMPOSITION_SOURCE_SYSTEM_REGISTRY_MISMATCH")
        tool_world = compile_source_bound_tool_world(
            manifest, measured, action_source_binding={"path": entry.path, "sha256": entry.content_sha256})
        return tool_world, proposal["proposal_sha256"]


@dataclass(frozen=True, slots=True)
class PreparedSourceComposition:
    """Immutable pre-Plan data. A checksum is integrity, never a Grant or approval."""
    query: WorldQuery
    reference_context: CapabilityContextPacketV1
    candidates: CompositionCandidateSnapshotV1
    context: CompositionCompileContextV1
    registry: ActionRegistrySnapshot
    workspace_id: str
    tool_source_identity: str
    tool_review_proposal_sha256: str
    display_to_candidate: tuple[tuple[str, str, str], ...]
    preparation_sha256: str

    def payload(self) -> dict:
        return {
            "schema": "tiangong.source-composition-preparation.v1",
            "query": self.query.model_dump(mode="json"),
            "reference_context": self.reference_context.payload(),
            "reference_packet_sha256": self.reference_context.packet_sha256,
            "candidates": self.candidates.payload(),
            "candidate_snapshot_sha256": self.candidates.candidate_snapshot_sha256,
            "context": self.context.payload(), "context_sha256": self.context.context_sha256,
            "registry": self.registry.model_dump(mode="json"), "workspace_id": self.workspace_id,
            "tool_source_identity": self.tool_source_identity,
            "tool_review_proposal_sha256": self.tool_review_proposal_sha256,
            "display_to_candidate": self.display_to_candidate,
            "may_authorize": False, "may_execute": False,
        }

    def has_valid_sha256(self) -> bool:
        return (self.query.has_valid_hash() and self.reference_context.has_valid_sha256()
                and self.candidates.has_valid_sha256() and self.context.has_valid_sha256()
                and self.registry.has_valid_sha256()
                and self.preparation_sha256 == canonical_sha256(self.payload()))

    def capability_context(self) -> CapabilityContextPacketV1:
        """The prompt now uses REAL P4 candidate IDs and hash, not display IDs."""
        if not self.has_valid_sha256():
            raise ValueError("COMPOSITION_SOURCE_PREPARATION_HASH_INVALID")
        packet = build_capability_context_packet(
            world_state_ref=self.query.basis_world_state_ref,
            frame_binding_sha256=self.reference_context.frame_binding_sha256,
            candidates=self.candidates,
            protected_identities=self.reference_context.protected_identities,
        )
        # Goal and small Proposal grammar come from the system context, not the
        # display-only packet or model. This is still DATA, not execution advice.
        packet = replace(packet, composition_abi=packet.composition_abi +
            ";goal_ref=" + self.context.goal_ref + ";compile_context=" + self.context.context_sha256 +
            ";proposal_fields=proposal_schema,goal_ref,selected_method_candidate_ids,selected_action_candidate_ids,"
            "steps,dependency_edges,output_bindings,control_flow,rationale_tags;"
            "step_fields=step_id,candidate_id,depends_on,output_bindings;source_approval_proven=false")
        return replace(packet, packet_sha256=packet.computed_sha256())


@dataclass(frozen=True, slots=True)
class SourceCompositionResult:
    preparation: PreparedSourceComposition
    parse_outcome: ProposalParseOutcomeV1
    plan: CapabilityCompositionPlanV1
    validation: CompositionValidationResultV1
    # Results are not automatically registered, dispatched or treated as success.
    may_authorize: bool = False
    may_execute: bool = False

    def __post_init__(self) -> None:
        if self.may_authorize is not False or self.may_execute is not False:
            raise ValueError("COMPOSITION_SOURCE_RESULT_CANNOT_AUTHORIZE")


def _planning_snapshot(owner, query, *, request_id, run_id, generation, workspace_id):
    if (type(query) is not WorldQuery or not query.has_valid_hash()
            or not isinstance(request_id, str) or re.fullmatch(r"req_[0-9a-f]{64}", request_id) is None
            or not isinstance(run_id, str) or re.fullmatch(r"run_[0-9a-f]{64}", run_id) is None
            or type(generation) is not int or generation < 0
            or not isinstance(workspace_id, str) or not workspace_id):
        raise ValueError("COMPOSITION_SOURCE_REQUEST_IDENTITY_INVALID")
    correlation = "wctx." + canonical_sha256({
        "request_id": request_id, "run_id": run_id, "generation": generation,
        "task_sha256": query.task_sha256, "created_at_ms": query.created_at_ms,
    })[:32]
    if (query.correlation_id != correlation or query.task_ref != "task." + query.task_sha256[:32]):
        raise ValueError("COMPOSITION_SOURCE_REQUEST_QUERY_MISMATCH")
    with owner.gateway._lock, owner.gateway._write_transaction(), owner.world.store.retention_transaction():
        binding = owner.gateway.get_request_generation_binding(request_id)
        envelope = owner.gateway.get_request_envelope(request_id)
        if (binding is None or binding["status"] != "ACTIVE" or binding["run_id"] != run_id
                or binding["current_generation"] != generation or envelope is None):
            raise ValueError("COMPOSITION_SOURCE_GENERATION_NOT_ACTIVE")
        if (envelope.principal_scope_hash != query.scope.principal_scope_hash
                or query.task_sha256 != canonical_sha256({"current_user_text": envelope.text.strip()})
                or query.focus != envelope.text.strip()[:20_000]):
            raise ValueError("COMPOSITION_SOURCE_REQUEST_TASK_MISMATCH")
        scoped = {item.key: item.value for item in query.scope.scope_bindings}.get("workspace_id")
        if scoped is not None and scoped != workspace_id:
            raise ValueError("COMPOSITION_SOURCE_WORKSPACE_MISMATCH")
        ref = query.basis_world_state_ref
        if ref is None or ref.record_type != "world_state":
            raise ValueError("COMPOSITION_SOURCE_WORLD_REQUIRED")
        snapshot = owner.world.store.get(ref.record_id)
        if snapshot is None or snapshot.state_ref != ref or snapshot.state.scope != query.scope:
            raise ValueError("COMPOSITION_SOURCE_WORLD_UNAVAILABLE")
        current = owner.world.store.current(life_id=query.scope.life_id, principal_scope_hash=query.scope.principal_scope_hash,
                                           world_scope_hash=query.scope.world_scope_hash, frame_id=snapshot.frame_id)
        if current is None or current.state_ref != ref:
            # Pre-Plan work cannot silently move to a new head. Registered plans
            # keep their separate, existing pinned historical-source lifecycle.
            raise ValueError("COMPOSITION_SOURCE_WORLD_NO_LONGER_CURRENT")
        _validate_snapshot(snapshot, query)
        return snapshot


def prepare_source_composition(owner, *, query, reference_context, tool_source, registry,
                               request_id, run_id, generation, workspace_id, prepared_at_ms):
    """Resolve exact reference addresses and call the ORIGINAL P4 candidate builder."""
    from .method_source_run_binding import MethodRunSourceResolver
    if (type(owner) is not MethodRunSourceResolver or type(tool_source) is not PlanningToolSource
            or type(reference_context) is not CapabilityContextPacketV1):
        raise TypeError("COMPOSITION_SOURCE_SYSTEM_INPUTS_REQUIRED")
    snapshot = _planning_snapshot(owner, query, request_id=request_id, run_id=run_id,
                                  generation=generation, workspace_id=workspace_id)
    if (not reference_context.has_valid_sha256() or reference_context.world_state_ref != snapshot.state_ref
            or ProtectedContextIdentityV1("query_ref", f"{query.query_id}@{query.query_sha256}") not in reference_context.protected_identities
            or ProtectedContextIdentityV1("workspace_id", workspace_id) not in reference_context.protected_identities
            or "candidate_ids=DISPLAY_ONLY" not in reference_context.composition_abi):
        raise ValueError("COMPOSITION_SOURCE_REFERENCE_CONTEXT_MISMATCH")
    if type(prepared_at_ms) is not int or prepared_at_ms < query.created_at_ms:
        raise ValueError("COMPOSITION_SOURCE_PREPARATION_TIME_INVALID")
    rows = {}
    for entity in snapshot.entities:
        if entity.entity_type in {"ToolCapability", "SkillMethod"}:
            row = _address(entity, snapshot)
            key = (row[1].source_kind, row[1].semantic_id)
            if key in rows:
                raise ValueError("COMPOSITION_SOURCE_ENTITY_DUPLICATE")
            rows[key] = row
    if not reference_context.action_candidates:
        raise ValueError("COMPOSITION_SOURCE_ACTION_CANDIDATES_REQUIRED")
    selected = []
    for method, entries in ((True, reference_context.method_candidates), (False, reference_context.action_candidates)):
        for entry in entries:
            semantic = (entry.method_ref if method else entry.action_ref).partition(":")[2]
            row = rows.get(("SKILL_METHOD" if method else "TOOL_ACTION", semantic))
            if row is None:
                raise ValueError("COMPOSITION_SOURCE_REFERENCE_NOT_IN_STATE")
            entity, source, ref, attrs = row
            stale = (*snapshot.state.stale_refs, *snapshot.state.unresolved_conflict_refs)
            if (entity.lifecycle != "ACTIVE" or entity.truth_state != "TRUE" or entity.epistemic_state != "CURRENT"
                    or any(r.record_type == "world_entity" and r.record_id == entity.entity_id for r in stale)):
                raise ValueError("COMPOSITION_SOURCE_REFERENCE_NOT_CURRENT")
            if (source.version != entry.version or source.descriptor_sha256 != entry.descriptor_sha256
                    or canonical_sha256(source.model_dump(mode="json")) != entry.source_revision
                    or attrs["context_frame_binding"] != reference_context.frame_binding_sha256
                    or attrs["context_workspace"] != workspace_id):
                raise ValueError("COMPOSITION_SOURCE_REFERENCE_BINDING_MISMATCH")
            if method:
                if (entry.method_ref != "method:" + source.semantic_id or entry.title != entity.canonical_name
                        or entry.summary != attrs.get("semantic_summary", "")):
                    raise ValueError("COMPOSITION_SOURCE_DISPLAY_SEMANTICS_MISMATCH")
            elif (entry.action_ref != "action:" + source.semantic_id or entry.effect_class != attrs["effect_class"]
                    or entry.risk_floor != attrs["risk_floor"] or entry.availability != attrs["availability"]):
                raise ValueError("COMPOSITION_SOURCE_DISPLAY_SEMANTICS_MISMATCH")
            frame = FrameBindingV1.model_validate_json(attrs["context_frame_ref"])
            if (frame.repository != tool_source.repository_id or frame.worktree != tool_source.worktree_id
                    or frame.commit != tool_source.candidate_commit):
                raise ValueError("COMPOSITION_SOURCE_TOOL_FRAME_MISMATCH")
            selected.append((entry, method, row, frame))
    digest = canonical_sha256({"domain": "tiangong.world-source-reference-projection.v1",
        "query_sha256": query.query_sha256, "state": snapshot.state_ref.model_dump(mode="json"),
        "selected": [{"entity_ref": r[2][2].model_dump(mode="json"), "source_ref": r[2][1].model_dump(mode="json")} for r in selected]})
    if digest != reference_context.candidate_snapshot_sha256:
        raise ValueError("COMPOSITION_SOURCE_DISPLAY_DIGEST_INVALID")
    tools, review_digest = tool_source.load(registry)
    # This is the existing pre-Plan archived-state reader, NOT MethodRunSourceResolver.read.
    methods = owner.world.method_world_for_state(snapshot.state_ref, scope=query.scope)
    tool_by_id = {p.action_id: p for p in tools.primitives}
    method_by_id = {p.method_id: p for p in methods.primitives}
    method_ids, action_ids = [], []
    for _entry, method, (_entity, source, ref, _attrs), _frame in selected:
        primitive = (method_by_id if method else tool_by_id).get(source.semantic_id)
        actual_source = None if primitive is None else (primitive.source_ref if method else derive_action_source_revision(primitive))
        required_world = ("method-world:" + methods.snapshot_sha256) if method else ("tool-world:" + tools.snapshot_sha256)
        if (actual_source != source or required_world not in snapshot.dependencies.source_keys_for(ref)):
            raise ValueError("COMPOSITION_SOURCE_RESOLVED_REVISION_MISMATCH")
        (method_ids if method else action_ids).append(source.semantic_id)
    candidates = build_candidate_snapshot(tools, methods, method_ids=method_ids, action_ids=action_ids)
    context = CompositionCompileContextV1(
        schema="tiangong.composition-compile-context.v1", request_id=request_id, run_id=run_id, generation=generation,
        principal_scope_hash=query.scope.principal_scope_hash, world_state_ref=snapshot.state_ref.record_id,
        world_state_sha256=snapshot.state_ref.sha256, goal_ref=query.task_ref, goal_fingerprint=query.task_sha256,
        environment_class=selected[0][3].environment,
        context_fingerprint_sha256=canonical_sha256({"query": query.query_sha256, "reference": reference_context.packet_sha256,
                                                   "tool_source": tool_source.identity(), "candidates": candidates.candidate_snapshot_sha256}),
        capability_manifest_sha256=registry.source_manifest_sha256, created_at_ms=prepared_at_ms, context_sha256="0"*64,
    ).with_computed_sha256()
    mapping = tuple((entry.candidate_id, ("method:" if method else "action:") + row[1].semantic_id,
                     next(item.candidate_id for item in (candidates.method_candidates if method else candidates.action_candidates)
                          if (item.primitive.method_id if method else item.primitive.action_id) == row[1].semantic_id))
                    for entry, method, row, _frame in selected)
    result = PreparedSourceComposition(query, reference_context, candidates, context, registry, workspace_id,
                                       tool_source.identity(), review_digest, mapping, "0"*64)
    _planning_snapshot(owner, query, request_id=request_id, run_id=run_id, generation=generation, workspace_id=workspace_id)
    return replace(result, preparation_sha256=canonical_sha256(result.payload()))


def compile_source_composition(owner, prepared, primary_text, *, tool_source, repair_text=None,
                               available_verifiers=frozenset(), validated_at_ms):
    """Revalidate retained sources, parse once/one repair, call ORIGINAL P4 compiler/validator."""
    if type(prepared) is not PreparedSourceComposition or not prepared.has_valid_sha256():
        raise ValueError("COMPOSITION_SOURCE_PREPARATION_HASH_INVALID")
    if (type(validated_at_ms) is not int or validated_at_ms < 0
            or type(available_verifiers) is not frozenset
            or any(type(value) is not str or not value for value in available_verifiers)):
        raise ValueError("COMPOSITION_SOURCE_VALIDATION_INPUT_INVALID")
    ctx = prepared.context
    rebuilt = prepare_source_composition(owner, query=prepared.query, reference_context=prepared.reference_context,
        tool_source=tool_source, registry=prepared.registry, request_id=ctx.request_id, run_id=ctx.run_id,
        generation=ctx.generation, workspace_id=prepared.workspace_id, prepared_at_ms=ctx.created_at_ms)
    if rebuilt != prepared:
        raise ValueError("COMPOSITION_SOURCE_PREPARATION_DRIFT")
    parsed = parse_with_single_repair(primary_text, prepared.candidates, repair_text=repair_text)
    plan = compile_capability_composition_plan(parsed.proposal, prepared.candidates, ctx, prepared.registry)
    validation = validate_capability_composition_plan(plan, parsed.proposal, prepared.candidates, ctx, prepared.registry,
        available_verifiers=available_verifiers, validated_at_ms=validated_at_ms)
    _planning_snapshot(owner, prepared.query, request_id=ctx.request_id, run_id=ctx.run_id,
                       generation=ctx.generation, workspace_id=prepared.workspace_id)
    return SourceCompositionResult(prepared, parsed, plan, validation)
