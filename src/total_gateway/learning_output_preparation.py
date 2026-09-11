"""P10 learning-output preparation using the existing Knowledge/P8/P9/P5 seams.

This is a Gateway-side adapter, not a publisher, observer, Memory writer or new
learning state machine. Expected pins and scopes are trusted caller inputs;
never obtain them from a model's proposal. A prepared object's own hash is NOT
approval. Production callers must resolve the pins from the existing Life,
World/Git and machine-evidence authorities before calling these functions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from contracts import canonical_json_bytes, canonical_sha256
from contracts.capability_composition import SourceRevisionRefV1
from life_service.artifact_executor import compile_artifact
from life_service.learning_workflow import LEARNING_WORKFLOW_SCHEMA, legacy_publication_blocked
from world_understanding.capability_composition.capability_experience_api import (
    apply_capability_experience_observation, build_capability_experience_memory_intent,
    evaluate_capability_experience_admission, exact_source_hashes,
)
from world_understanding.capability_composition.capability_experience_attribution import evaluate_attribution_integrity
from world_understanding.capability_composition.capability_experience_policy import (
    CapabilityExperienceAggregateStateV1, CapabilityExperienceObservationV1,
)
from world_understanding.skill_method_world import (
    LegacySkillMethodCorpusV1, MethodSourceCandidateV1, SkillMethodWorldSnapshotV1,
    compile_method_source_lifecycle, compile_native_method_source, compile_skill_method_world,
)
from .composition_executable_plan import _require_canonical_json_value
from .method_source_review import MethodSimulationEvidenceV1, _verify_simulation
from .tool_source_candidate import inspect_tool_source_candidate

_OUTPUT_SCHEMA = "tiangong.learning-output-preparation.v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}\Z")
_ROUTES = {
    "KNOWLEDGE": "EXISTING_KNOWLEDGE_PUBLICATION",
    "TOOL_SOURCE": "P8_ISOLATED_BUILD_AND_INDEPENDENT_REVIEW",
    "METHOD_SOURCE": "P9_INDEPENDENT_REVIEW_AND_WORLD_PUBLICATION",
    "COMPOSITION_EXPERIENCE": "EXISTING_MEMORY_COORDINATOR",
}


class LearningOutputPreparationError(ValueError):
    pass


def _sha(value: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise LearningOutputPreparationError("learning_output.invalid_digest")
    return value


def _json_bytes(value: Any, *, limit: int = 1_048_576) -> bytes:
    _require_canonical_json_value(value, label="learning_output", max_bytes=limit)
    return canonical_json_bytes(value)


def learning_record_sha256(record: Mapping[str, Any]) -> str:
    """Content pin of the CURRENT record, not its possibly older draft hash."""
    if not isinstance(record, Mapping):
        raise LearningOutputPreparationError("learning_output.record_required")
    detached = json.loads(_json_bytes(dict(record)))
    return canonical_sha256({"domain": "tiangong.learning-output-record.v1", "record": detached})


@dataclass(frozen=True, slots=True)
class LearningOutputBasisV1:
    """Externally resolved identity pins; possession alone grants no authority."""
    life_id: str
    learning_id: str
    learning_scope_sha256: str
    learning_record_sha256: str
    principal_ref: str
    principal_scope_hash: str
    privacy_scope: str
    privacy_scope_hash: str

    def __post_init__(self) -> None:
        for value in (self.life_id, self.learning_id, self.principal_ref, self.privacy_scope):
            if type(value) is not str or _ID.fullmatch(value) is None:
                raise LearningOutputPreparationError("learning_output.invalid_identity")
        for value in (self.learning_scope_sha256, self.learning_record_sha256,
                      self.principal_scope_hash, self.privacy_scope_hash):
            _sha(value)


@dataclass(frozen=True, slots=True)
class PreparedLearningOutputV1:
    """Canonical immutable preparation bytes, never a self-attested receipt."""
    canonical_bytes: bytes

    def __post_init__(self) -> None:
        if type(self.canonical_bytes) is not bytes or not 0 < len(self.canonical_bytes) <= 16_777_216:
            raise LearningOutputPreparationError("learning_output.invalid_preparation_bytes")
        value = json.loads(self.canonical_bytes)
        if _json_bytes(value, limit=16_777_216) != self.canonical_bytes:
            raise LearningOutputPreparationError("learning_output.noncanonical_preparation")
        if (type(value) is not dict or set(value) != {
                "schema", "status", "output_kind", "next_boundary", "basis", "body",
                "context_section", "may_publish", "may_authorize", "may_execute", "may_write_store"}
                or value["schema"] != _OUTPUT_SCHEMA or value["status"] != "LEARNING_OUTPUT_PREPARED"
                or type(value["output_kind"]) is not str or value["output_kind"] not in _ROUTES
                or value["next_boundary"] != _ROUTES[value["output_kind"]]
                or value["context_section"] != "DATA"
                or any(value[f] is not False for f in ("may_publish", "may_authorize", "may_execute", "may_write_store"))):
            raise LearningOutputPreparationError("learning_output.preparation_is_not_authority")
        if type(value["basis"]) is not dict or type(value["body"]) is not dict:
            raise LearningOutputPreparationError("learning_output.preparation_shape")
        LearningOutputBasisV1(**value["basis"])

    @property
    def preparation_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    def payload(self) -> dict[str, Any]:
        return json.loads(self.canonical_bytes)  # A detached view cannot mutate the pin.


def _record(record, basis, *, target: str | None):
    if type(basis) is not LearningOutputBasisV1:
        raise LearningOutputPreparationError("learning_output.external_basis_required")
    basis.__post_init__()
    value = json.loads(_json_bytes(dict(record)))
    if (learning_record_sha256(value) != basis.learning_record_sha256
            or value.get("schema") != LEARNING_WORKFLOW_SCHEMA
            or value.get("life_id") != basis.life_id or value.get("learning_id") != basis.learning_id
            or value.get("scope_sha256") != basis.learning_scope_sha256):
        raise LearningOutputPreparationError("learning_output.record_pin_or_scope_mismatch")
    if value.get("status") not in {"approved", "awaiting_user", "migration_required"} or value.get("registered") is not False:
        raise LearningOutputPreparationError("learning_output.record_not_preparable")
    if target is not None:
        aliases = {"kb": "knowledge", "knowledge-base": "knowledge", "knowledgebase": "knowledge", "capability": "skill"}
        for key in ("target", "kind"):
            raw = value.get(key)
            if type(raw) is not str:
                raise LearningOutputPreparationError("learning_output.explicit_target_required")
            normalized = raw.strip().casefold().replace("_", "-").removeprefix("learning-")
            if aliases.get(normalized, normalized) != target:
                raise LearningOutputPreparationError("learning_output.target_mismatch")
    return value


def _output(kind, basis, body):
    return PreparedLearningOutputV1(_json_bytes({
        "schema": _OUTPUT_SCHEMA, "status": "LEARNING_OUTPUT_PREPARED", "output_kind": kind,
        "next_boundary": _ROUTES[kind], "basis": asdict(basis), "body": body,
        "context_section": "DATA", "may_publish": False, "may_authorize": False,
        "may_execute": False, "may_write_store": False,
    }, limit=16_777_216))


def prepare_knowledge_learning_output(record, *, basis: LearningOutputBasisV1) -> PreparedLearningOutputV1:
    """Compile a Knowledge preview; existing consent/risk/import still owns publication."""
    value = _record(record, basis, target="knowledge")
    if legacy_publication_blocked(value):
        raise LearningOutputPreparationError("learning_output.knowledge_contains_capability")
    artifact = compile_artifact(value, action_catalog=())
    return _output("KNOWLEDGE", basis, {"artifact": artifact, "publication_performed": False})


def prepare_tool_source_learning_output(record, *, basis: LearningOutputBasisV1,
                                        repository: Path, base_commit: str, candidate_commit: str,
                                        requested_action_ids: tuple[str, ...]) -> PreparedLearningOutputV1:
    """Use P8's pinned native Git reader, never import candidate source or run a build."""
    _record(record, basis, target="tool")
    candidate = inspect_tool_source_candidate(repository, base_commit=base_commit,
        candidate_commit=candidate_commit, requested_action_ids=requested_action_ids)
    return _output("TOOL_SOURCE", basis, {"candidate": asdict(candidate),
        "build_verified": False, "review_verified": False, "publication_performed": False})


def prepare_method_source_learning_output(
    record, *, basis: LearningOutputBasisV1, base_snapshot: SkillMethodWorldSnapshotV1,
    expected_base_snapshot_sha256: str, corpus: LegacySkillMethodCorpusV1,
    candidates: tuple[MethodSourceCandidateV1, ...], source_documents: dict[str, tuple[str, bytes]],
    simulation_evidence: dict[str, MethodSimulationEvidenceV1], trusted_observer_public_key: bytes,
    now_ms: int,
) -> PreparedLearningOutputV1:
    """Verify independent simulations and exact semantic bytes; P9 review is still due.

    The observer key is installation input, never part of the learning record.
    P9 publication must additionally bind native Git/WorldFrame/current head.
    """
    _record(record, basis, target="skill")
    _sha(expected_base_snapshot_sha256)
    if (type(now_ms) is not int or now_ms < 0 or type(base_snapshot) is not SkillMethodWorldSnapshotV1
            or base_snapshot.snapshot_sha256 != expected_base_snapshot_sha256
            or not base_snapshot.has_valid_sha256()):
        raise LearningOutputPreparationError("learning_output.method_base_or_clock_mismatch")
    if compile_skill_method_world(base_snapshot.primitives, corpus=corpus,
            migration_bindings=base_snapshot.migration_bindings,
            reviewed_source_bindings=base_snapshot.reviewed_source_bindings) != base_snapshot:
        raise LearningOutputPreparationError("learning_output.method_base_graph_mismatch")
    if (type(candidates) is not tuple or not 1 <= len(candidates) <= 128
            or any(type(c) is not MethodSourceCandidateV1 or c.may_authorize is not False
                   or c.may_execute is not False for c in candidates)
            or type(source_documents) is not dict or type(simulation_evidence) is not dict):
        raise LearningOutputPreparationError("learning_output.method_input_shape")
    candidates = tuple(replace(c) for c in candidates)
    source_documents, simulation_evidence = dict(source_documents), dict(simulation_evidence)
    plan = compile_method_source_lifecycle(base_snapshot, candidates)
    if len({c.candidate_id for c in candidates}) != len(candidates):
        raise LearningOutputPreparationError("learning_output.duplicate_candidate_id")
    if (set(source_documents) != {c.method_id for c in candidates if c.operation != "REMOVE"}
            or set(simulation_evidence) != {c.method_id for c in candidates}):
        raise LearningOutputPreparationError("learning_output.method_evidence_scope")
    old = {p.method_id: p for p in base_snapshot.primitives}
    documents, evidence_digests = {}, {}
    for c in candidates:
        evidence_digests[c.method_id] = _verify_simulation(c, simulation_evidence[c.method_id],
                                                         trusted_observer_public_key, now_ms)
        if c.operation == "REMOVE":
            continue
        document = source_documents[c.method_id]
        if type(document) is not tuple or len(document) != 2:
            raise LearningOutputPreparationError("learning_output.method_document_shape")
        path, raw = document
        primitive = compile_native_method_source(path, raw, expected_source_sha256=c.primitive.source_sha256)
        if primitive != c.primitive:
            raise LearningOutputPreparationError("learning_output.method_source_drift")
        if c.operation == "UPDATE":
            previous = old[c.method_id].version
            if re.fullmatch(r"v[1-9][0-9]{0,8}", previous) is None or int(primitive.version[1:]) <= int(previous[1:]):
                raise LearningOutputPreparationError("learning_output.method_version_not_advanced")
        documents[c.method_id] = {"path": path, "source_sha256": primitive.source_sha256}
    steps = [s for p in plan.next_primitives for s in p.method_steps]
    paths = [path.casefold() for p in plan.next_primitives for path in p.source_ref.source_files if path.endswith(".json")]
    if len(steps) != len(set(steps)) or len(paths) != len(set(paths)):
        raise LearningOutputPreparationError("learning_output.method_identity_collision")
    return _output("METHOD_SOURCE", basis, {"candidates": [
        {**c.payload(), "candidate_sha256": c.candidate_sha256} for c in sorted(candidates, key=lambda c: c.method_id)],
        "plan": {**plan.payload(), "plan_sha256": plan.plan_sha256}, "source_documents": documents,
        "simulation_evidence_sha256": evidence_digests, "simulation_verified": True,
        "review_verified": False, "native_git_verified": False, "publication_performed": False})


def current_learning_sources_sha256(sources: tuple[SourceRevisionRefV1, ...]) -> str:
    if type(sources) is not tuple or not 1 <= len(sources) <= 512:
        raise LearningOutputPreparationError("learning_output.current_sources_required")
    checked = []
    for source in sources:
        if type(source) is not SourceRevisionRefV1:
            raise LearningOutputPreparationError("learning_output.source_contract_required")
        checked.append(SourceRevisionRefV1.model_validate_json(source.model_dump_json()))
    hashes = exact_source_hashes(tuple(checked))
    if len(hashes) != len(checked):
        raise LearningOutputPreparationError("learning_output.duplicate_source")
    return canonical_sha256({"domain": "tiangong.learning-output-current-sources.v1", "exact_source_hashes": hashes})


def prepare_composition_experience_learning_output(
    record, *, basis: LearningOutputBasisV1, observation: CapabilityExperienceObservationV1,
    expected_observation_sha256: str, prior: CapabilityExperienceAggregateStateV1 | None,
    expected_prior_state_sha256: str | None, current_sources: tuple[SourceRevisionRefV1, ...],
    expected_current_sources_sha256: str, parent_derivation_ids: tuple[str, ...], now_ms: int,
) -> PreparedLearningOutputV1:
    """Re-run P5 attribution/admission and prepare its non-writing Memory intent.

    The observation pin MUST come from the existing machine-evidence collector,
    prior pin (or confirmed absence) from Memory, and current source pin from
    World. Hash equality authenticates none of those producers by itself. This
    adapter is deliberately not exposed to model/HTTP input. It does not collect
    Runtime evidence, persist counters, or claim a new positive memory exists.
    """
    _record(record, basis, target=None)
    if type(observation) is not CapabilityExperienceObservationV1:
        raise LearningOutputPreparationError("learning_output.machine_observation_required")
    observation = CapabilityExperienceObservationV1.model_validate_json(observation.model_dump_json())
    if not observation.has_valid_sha256() or observation.observation_sha256 != _sha(expected_observation_sha256):
        raise LearningOutputPreparationError("learning_output.observation_pin_mismatch")
    if (observation.life_id, observation.principal_ref, observation.principal_scope_hash,
        observation.privacy_scope, observation.privacy_scope_hash) != (
        basis.life_id, basis.principal_ref, basis.principal_scope_hash, basis.privacy_scope, basis.privacy_scope_hash):
        raise LearningOutputPreparationError("learning_output.experience_scope_mismatch")
    if (type(now_ms) is not int or now_ms < observation.observed_at_ms
            or type(parent_derivation_ids) is not tuple or not 1 <= len(parent_derivation_ids) <= 64
            or any(type(p) is not str or _ID.fullmatch(p) is None for p in parent_derivation_ids)
            or parent_derivation_ids != tuple(sorted(set(parent_derivation_ids)))):
        raise LearningOutputPreparationError("learning_output.experience_clock_or_parents")
    if current_learning_sources_sha256(current_sources) != _sha(expected_current_sources_sha256):
        raise LearningOutputPreparationError("learning_output.current_sources_pin_mismatch")
    if exact_source_hashes(current_sources) != exact_source_hashes((*observation.plan.method_source_refs, *observation.plan.action_source_refs)):
        raise LearningOutputPreparationError("learning_output.source_revalidation_required")
    if prior is None:
        if expected_prior_state_sha256 is not None:
            raise LearningOutputPreparationError("learning_output.prior_state_missing")
    else:
        if type(prior) is not CapabilityExperienceAggregateStateV1:
            raise LearningOutputPreparationError("learning_output.prior_state_contract")
        prior = CapabilityExperienceAggregateStateV1.model_validate_json(prior.model_dump_json())
        if not prior.has_valid_sha256() or prior.state_sha256 != _sha(expected_prior_state_sha256):
            raise LearningOutputPreparationError("learning_output.prior_state_pin_mismatch")
    if prior is not None and (now_ms < prior.last_observed_at_ms or (
            observation.observation_id not in prior.observation_ids
            and observation.observed_at_ms < prior.last_observed_at_ms)):
        raise LearningOutputPreparationError("learning_output.experience_time_regression")
    rebuilt = evaluate_attribution_integrity(observation.plan, observation.trace,
        expected_principal_scope_hash=basis.principal_scope_hash, expected_privacy_scope_hash=basis.privacy_scope_hash,
        checked_at_ms=observation.attribution.checked_at_ms)
    if rebuilt != observation.attribution:
        raise LearningOutputPreparationError("learning_output.attribution_mismatch")
    admission = evaluate_capability_experience_admission(observation,
        expected_principal_scope_hash=basis.principal_scope_hash,
        expected_privacy_scope_hash=basis.privacy_scope_hash, decided_at_ms=now_ms)
    body = {"observation_sha256": observation.observation_sha256,
        "prior_state_sha256": expected_prior_state_sha256, "current_sources_sha256": expected_current_sources_sha256,
        "admission": admission.model_dump(mode="json"), "aggregate": None, "memory_intent": None,
        "memory_plaintext": None, "memory_write_performed": False, "already_accounted": False,
        "requires_machine_origin_and_parent_lineage": True}
    if admission.positive_allowed or admission.negative_allowed:
        state, negative = apply_capability_experience_observation(prior, observation, admission)
        body["aggregate"] = state.model_dump(mode="json")
        if prior is not None and observation.observation_id in prior.observation_ids:
            # P5 has already consumed this ID. Do not turn a new timestamp or
            # parent set into a second Memory intent for the same observation.
            # Exact original observation bytes are still the collector's duty.
            body["already_accounted"] = True
        else:
            intent, plaintext = build_capability_experience_memory_intent(state,
                parent_derivation_ids=parent_derivation_ids, negative_evidence=negative, created_at_ms=now_ms)
            body.update(memory_intent=intent.model_dump(mode="json"), memory_plaintext=plaintext.decode("utf-8"))
    return _output("COMPOSITION_EXPERIENCE", basis, body)
