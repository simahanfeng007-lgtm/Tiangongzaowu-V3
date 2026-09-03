"""P6 exact-frame Software/Tool/Method contributions for one WorldState.

A contribution is a non-authorizing set of ordinary WorldEntity/WorldRelation
records bound to one explicit SoftwareWorldFrame and WorldCut.  It is not a
second graph or WorldState store.  The existing WorldStateMaterializer remains
the only publication authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from contracts.canonical import canonical_sha256
from contracts.capability_composition import SourceRevisionRefV1
from contracts.models import ContractModel, OpaqueId, Sha256
from contracts.world_understanding._base import WorldRecordRef, WorldValue
from contracts.world_understanding.entity import (
    WorldAttribute,
    WorldEntity,
    derive_entity_id,
)
from contracts.world_understanding.relation import (
    WorldRelation,
    derive_relation_id,
)
from contracts.world_understanding.world_cut import WorldCut
from world_understanding.capability_composition.models import (
    derive_action_source_revision,
)
from world_understanding.skill_method_world import SkillMethodWorldSnapshotV1
from world_understanding.software_world.frame import SoftwareWorldFrame
from world_understanding.software_world.graph import SparseWorldGraph
from world_understanding.tool_capability_world import (
    ToolCapabilityWorldSnapshotV1,
)


DOMAIN_CONTRIBUTION_SCHEMA = "tiangong.world-domain-contribution.v1"
FRAME_BINDING_SCHEMA = "tiangong.world-frame-binding.v1"
DomainContributionKind = Literal[
    "SOFTWARE", "TOOL_CAPABILITY", "SKILL_METHOD"
]

_GENERIC_FRAME_VALUES = frozenset(
    {"", "current", "default", "generic", "latest", "runtime", "unknown"}
)


def _source_sort_key(
    value: SourceRevisionRefV1,
) -> tuple[str, str, str, str, str, str]:
    return (
        value.source_kind,
        value.semantic_id,
        value.version,
        value.source_sha256,
        value.descriptor_sha256,
        value.manifest_sha256 or "",
    )


def _record_ref_for_cut(cut: WorldCut) -> WorldRecordRef:
    return WorldRecordRef(
        record_type="world_cut",
        record_id=cut.cut_id,
        revision=None,
        sha256=cut.cut_sha256,
    )


class FrameBindingV1(ContractModel):
    schema_version: Literal[FRAME_BINDING_SCHEMA] = FRAME_BINDING_SCHEMA
    life_id: OpaqueId
    principal_scope_hash: Sha256
    workspace_id: OpaqueId
    world_scope_hash: Sha256
    frame_id: OpaqueId
    frame_revision_hash: Sha256
    repository: str = Field(min_length=1, max_length=4096)
    worktree: str = Field(min_length=1, max_length=4096)
    branch: str = Field(min_length=1, max_length=1024)
    commit: str = Field(min_length=1, max_length=1024)
    environment: str = Field(min_length=1, max_length=4096)
    world_cut_ref: WorldRecordRef
    binding_sha256: Sha256
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if self.world_cut_ref.record_type != "world_cut":
            raise ValueError("frame binding requires a WorldCut reference")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"binding_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.binding_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "FrameBindingV1":
        return self.model_copy(update={"binding_sha256": self.computed_sha256()})

    @classmethod
    def from_frame(
        cls,
        frame: SoftwareWorldFrame,
        cut: WorldCut,
    ) -> "FrameBindingV1":
        if frame.scope != cut.scope:
            raise ValueError("frame and WorldCut scopes differ")
        if frame.world_cut is not None and frame.world_cut.cut_sha256 != cut.cut_sha256:
            raise ValueError("frame is bound to a different WorldCut")
        value = cls(
            life_id=frame.scope.life_id,
            principal_scope_hash=frame.scope.principal_scope_hash,
            workspace_id="workspace_"
            + canonical_sha256(
                {
                    "domain": "tiangong.workspace-identity.v1",
                    "workspace": frame.workspace,
                    "world_scope_hash": frame.scope.world_scope_hash,
                }
            ),
            world_scope_hash=frame.scope.world_scope_hash,
            frame_id=frame.frame_id,
            frame_revision_hash=frame.frame_revision_hash,
            repository=frame.repository,
            worktree=frame.worktree,
            branch=frame.branch,
            commit=frame.commit,
            environment=frame.environment,
            world_cut_ref=_record_ref_for_cut(cut),
            binding_sha256="0" * 64,
        )
        return value.with_computed_sha256()

    def require_exact_frame(
        self,
        frame: SoftwareWorldFrame,
        cut: WorldCut,
        *,
        repository_bound: bool,
    ) -> None:
        if not self.has_valid_sha256():
            raise ValueError("WORLD_DOMAIN_FRAME_BINDING_HASH_INVALID")
        expected = FrameBindingV1.from_frame(frame, cut)
        if self != expected:
            raise ValueError("WORLD_DOMAIN_FRAME_BINDING_MISMATCH")
        if repository_bound:
            values = (
                self.repository,
                self.worktree,
                self.branch,
                self.commit,
                self.environment,
            )
            if any(value.strip().casefold() in _GENERIC_FRAME_VALUES for value in values):
                raise ValueError("REPOSITORY_BOUND_DESCRIPTOR_GENERIC_FRAME")


class WorldDomainContributionV1(ContractModel):
    schema_version: Literal[DOMAIN_CONTRIBUTION_SCHEMA] = (
        DOMAIN_CONTRIBUTION_SCHEMA
    )
    contribution_id: OpaqueId
    contribution_kind: DomainContributionKind
    frame_binding: FrameBindingV1
    source_revision_refs: tuple[SourceRevisionRefV1, ...] = ()
    entities: tuple[WorldEntity, ...] = ()
    relations: tuple[WorldRelation, ...] = ()
    dependency_source_keys: tuple[str, ...] = Field(min_length=1, max_length=4096)
    contribution_sha256: Sha256
    may_authorize: Literal[False] = False
    may_execute: Literal[False] = False

    @field_validator("source_revision_refs")
    @classmethod
    def validate_source_revisions(
        cls, value: tuple[SourceRevisionRefV1, ...]
    ) -> tuple[SourceRevisionRefV1, ...]:
        keys = tuple(_source_sort_key(item) for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("domain contribution source revisions are not canonical")
        return value

    @field_validator("entities")
    @classmethod
    def validate_entities(
        cls, value: tuple[WorldEntity, ...]
    ) -> tuple[WorldEntity, ...]:
        keys = tuple(item.entity_id for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("domain contribution entities are not canonical")
        if any(not item.has_valid_hash() for item in value):
            raise ValueError("domain contribution contains invalid entity")
        return value

    @field_validator("relations")
    @classmethod
    def validate_relations(
        cls, value: tuple[WorldRelation, ...]
    ) -> tuple[WorldRelation, ...]:
        keys = tuple(item.relation_id for item in value)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("domain contribution relations are not canonical")
        if any(not item.has_valid_hash() for item in value):
            raise ValueError("domain contribution contains invalid relation")
        return value

    @field_validator("dependency_source_keys")
    @classmethod
    def validate_dependency_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))) or any(
            not item or len(item) > 180 for item in value
        ):
            raise ValueError("domain contribution dependency keys are invalid")
        return value

    @model_validator(mode="after")
    def validate_contribution(self) -> Self:
        if not self.frame_binding.has_valid_sha256():
            raise ValueError("domain contribution frame binding hash is invalid")
        if self.contribution_kind != "SOFTWARE" and (
            not self.source_revision_refs or not self.entities
        ):
            raise ValueError("capability contribution is empty")
        expected_source_kind = {
            "TOOL_CAPABILITY": "TOOL_ACTION",
            "SKILL_METHOD": "SKILL_METHOD",
        }.get(self.contribution_kind)
        if expected_source_kind is not None and any(
            item.source_kind != expected_source_kind
            for item in self.source_revision_refs
        ):
            raise ValueError("domain contribution source kind is invalid")
        for record in (*self.entities, *self.relations):
            scope = record.scope
            if (
                scope.life_id != self.frame_binding.life_id
                or scope.world_scope_hash
                != self.frame_binding.world_scope_hash
                or scope.principal_scope_hash
                != self.frame_binding.principal_scope_hash
            ):
                raise ValueError("domain contribution record crosses frame scope")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"contribution_sha256"})

    def computed_sha256(self) -> str:
        return canonical_sha256(self.payload())

    def has_valid_sha256(self) -> bool:
        return self.contribution_sha256 == self.computed_sha256()

    def with_computed_sha256(self) -> "WorldDomainContributionV1":
        return self.model_copy(
            update={"contribution_sha256": self.computed_sha256()}
        )

    def require_exact_frame(
        self,
        frame: SoftwareWorldFrame,
        cut: WorldCut,
    ) -> None:
        if not self.has_valid_sha256():
            raise ValueError("WORLD_DOMAIN_CONTRIBUTION_HASH_INVALID")
        self.frame_binding.require_exact_frame(
            frame,
            cut,
            repository_bound=self.contribution_kind != "SOFTWARE",
        )


def _attribute(key: str, value: str | int | bool) -> WorldAttribute:
    world_value: WorldValue
    if isinstance(value, bool):
        world_value = WorldValue(kind="boolean", boolean_value=value)
    elif isinstance(value, int):
        world_value = WorldValue(kind="integer", integer_value=value)
    else:
        world_value = WorldValue(kind="string", string_value=value)
    return WorldAttribute(
        key=key,
        value=world_value,
        attribute_sha256="0" * 64,
    ).with_computed_hash()


def _entity_ref(entity: WorldEntity) -> WorldRecordRef:
    return WorldRecordRef(
        record_type="world_entity",
        record_id=entity.entity_id,
        revision=entity.revision,
        sha256=entity.entity_sha256,
    )


def _descriptor_ref(
    *, record_type: str, record_id: str, sha256: str
) -> WorldRecordRef:
    return WorldRecordRef(
        record_type=record_type,
        record_id=record_id,
        revision=None,
        sha256=sha256,
    )


def _build_entity(
    *,
    frame: SoftwareWorldFrame,
    entity_type: str,
    semantic_kind: str,
    semantic_id: str,
    canonical_name: str,
    aliases: tuple[str, ...],
    attributes: Mapping[str, str | int | bool],
    source_ref: WorldRecordRef,
    previous_entities: Mapping[str, WorldEntity],
) -> WorldEntity:
    identity_anchor_hash = canonical_sha256(
        {
            "domain": "tiangong.world.capability-entity-identity.v1",
            "semantic_kind": semantic_kind,
            "semantic_id": semantic_id,
        }
    )
    entity_id = derive_entity_id(
        life_id=frame.scope.life_id,
        domain_id=frame.scope.domain_id,
        identity_anchor_hash=identity_anchor_hash,
    )
    previous = previous_entities.get(entity_id)
    if previous is not None and (
        previous.scope != frame.scope or not previous.has_valid_hash()
    ):
        raise ValueError("WORLD_DOMAIN_PREVIOUS_ENTITY_INVALID")
    revision = 1 if previous is None else previous.revision + 1
    value = WorldEntity(
        entity_id=entity_id,
        scope=frame.scope,
        entity_type=entity_type,
        identity_anchor_hash=identity_anchor_hash,
        canonical_name=canonical_name,
        aliases=tuple(sorted(set(aliases))),
        attributes=tuple(
            _attribute(key, attributes[key]) for key in sorted(attributes)
        ),
        location_refs=(),
        source_observation_refs=(source_ref,),
        truth_state="TRUE",
        epistemic_state="CURRENT",
        lifecycle="ACTIVE",
        replacement_refs=(),
        revision=revision,
        supersedes_entity_sha256=(
            None if previous is None else previous.entity_sha256
        ),
        time=frame.time,
        entity_sha256="0" * 64,
    )
    return value.with_computed_hash()


def _build_relation(
    *,
    frame: SoftwareWorldFrame,
    subject: WorldEntity,
    predicate: str,
    target: str,
    source_ref: WorldRecordRef,
    previous_relations: Mapping[str, WorldRelation],
) -> WorldRelation:
    value = WorldValue(kind="string", string_value=target)
    relation_id = derive_relation_id(
        world_scope_hash=frame.scope.world_scope_hash,
        subject_ref=_entity_ref(subject),
        predicate=predicate,
        value=value,
        condition_sha256=None,
    )
    previous = previous_relations.get(relation_id)
    if previous is not None and (
        previous.scope != frame.scope or not previous.has_valid_hash()
    ):
        raise ValueError("WORLD_DOMAIN_PREVIOUS_RELATION_INVALID")
    revision = 1 if previous is None else previous.revision + 1
    relation = WorldRelation(
        relation_id=relation_id,
        scope=frame.scope,
        subject_ref=_entity_ref(subject),
        predicate=predicate,
        value=value,
        condition_ref=None,
        condition_sha256=None,
        extraction_mode="deterministic",
        materialization_class="STRUCTURAL",
        source_observation_refs=(source_ref,),
        derivation_refs=(),
        truth_state="TRUE",
        epistemic_state="CURRENT",
        empirical_evidence_weight_milli=1000,
        revision=revision,
        supersedes_relation_sha256=(
            None if previous is None else previous.relation_sha256
        ),
        time=frame.time,
        relation_sha256="0" * 64,
    )
    return relation.with_computed_hash()


def _contribution(
    *,
    kind: DomainContributionKind,
    binding: FrameBindingV1,
    sources: tuple[SourceRevisionRefV1, ...],
    entities: tuple[WorldEntity, ...],
    relations: tuple[WorldRelation, ...],
    dependency_source_keys: tuple[str, ...],
) -> WorldDomainContributionV1:
    identity = canonical_sha256(
        {
            "domain": "tiangong.world-domain-contribution-id.v1",
            "kind": kind,
            "frame_binding_sha256": binding.binding_sha256,
            "source_revisions": [
                item.model_dump(mode="json") for item in sources
            ],
            "entity_refs": [
                _entity_ref(item).model_dump(mode="json") for item in entities
            ],
            "relation_refs": [
                WorldRecordRef(
                    record_type="world_relation",
                    record_id=item.relation_id,
                    revision=item.revision,
                    sha256=item.relation_sha256,
                ).model_dump(mode="json")
                for item in relations
            ],
        }
    )
    value = WorldDomainContributionV1(
        contribution_id="wdc_" + identity,
        contribution_kind=kind,
        frame_binding=binding,
        source_revision_refs=sources,
        entities=entities,
        relations=relations,
        dependency_source_keys=dependency_source_keys,
        contribution_sha256="0" * 64,
    )
    return value.with_computed_sha256()


def compile_software_domain_contribution(
    frame: SoftwareWorldFrame,
    cut: WorldCut,
    graph: SparseWorldGraph,
) -> WorldDomainContributionV1:
    graph.require_frame(frame)
    if graph.frame_revision_hash != frame.frame_revision_hash:
        raise ValueError("SOFTWARE_WORLD_GRAPH_FRAME_REVISION_MISMATCH")
    entities = graph.entities()
    relations = graph.relations()
    graph_sha256 = canonical_sha256(
        {
            "entities": [
                _entity_ref(item).model_dump(mode="json") for item in entities
            ],
            "relations": [item.relation_sha256 for item in relations],
        }
    )
    return _contribution(
        kind="SOFTWARE",
        binding=FrameBindingV1.from_frame(frame, cut),
        sources=(),
        entities=entities,
        relations=relations,
        dependency_source_keys=tuple(
            sorted(
                {
                    "frame:" + frame.frame_revision_hash,
                    "software-graph:" + graph_sha256,
                    "world-cut:" + cut.cut_sha256,
                }
            )
        ),
    )


def compile_tool_capability_contribution(
    frame: SoftwareWorldFrame,
    cut: WorldCut,
    tool_world: ToolCapabilityWorldSnapshotV1,
    *,
    previous_entities: Mapping[str, WorldEntity] | None = None,
    previous_relations: Mapping[str, WorldRelation] | None = None,
) -> WorldDomainContributionV1:
    if (
        not tool_world.has_valid_sha256()
        or tool_world.may_authorize
        or tool_world.may_execute
    ):
        raise ValueError("TOOL_CAPABILITY_WORLD_INVALID")
    binding = FrameBindingV1.from_frame(frame, cut)
    binding.require_exact_frame(frame, cut, repository_bound=True)
    old_entities = previous_entities or {}
    old_relations = previous_relations or {}
    sources = tuple(
        sorted(
            (
                derive_action_source_revision(primitive)
                for primitive in tool_world.primitives
            ),
            key=_source_sort_key,
        )
    )
    entities: list[WorldEntity] = []
    entity_by_action: dict[str, WorldEntity] = {}
    entity_by_source_primitive: dict[str, WorldEntity] = {}
    descriptor_by_action: dict[str, WorldRecordRef] = {}
    for primitive in tool_world.primitives:
        descriptor_ref = _descriptor_ref(
            record_type="tool_capability_descriptor",
            record_id=primitive.source_primitive_id,
            sha256=primitive.descriptor_sha256,
        )
        entity = _build_entity(
            frame=frame,
            entity_type="ToolCapability",
            semantic_kind="TOOL_ACTION",
            semantic_id=primitive.action_id,
            canonical_name=primitive.action_id,
            aliases=(),
            attributes={
                "action_id": primitive.action_id,
                "action_version": primitive.action_version,
                "availability": primitive.availability,
                "descriptor_sha256": primitive.descriptor_sha256,
                "effect_class": primitive.effect_class,
                "provider_component_id": primitive.provider_component_id,
                "risk_floor": primitive.risk_floor,
                "source_manifest_sha256": (
                    primitive.action_manifest_sha256
                ),
            },
            source_ref=descriptor_ref,
            previous_entities=old_entities,
        )
        entities.append(entity)
        entity_by_action[primitive.action_id] = entity
        entity_by_source_primitive[primitive.source_primitive_id] = entity
        descriptor_by_action[primitive.action_id] = descriptor_ref

    relations: list[WorldRelation] = []
    for semantic in tool_world.relations:
        subject: WorldEntity | None = None
        if semantic.source_ref.startswith("tool-source:"):
            subject = entity_by_source_primitive.get(semantic.source_ref)
        elif semantic.source_ref.startswith("action:"):
            subject = entity_by_action.get(
                semantic.source_ref.removeprefix("action:")
            )
        if subject is None:
            raise ValueError("TOOL_CAPABILITY_RELATION_SUBJECT_UNRESOLVED")
        action_id = next(
            key for key, value in entity_by_action.items() if value == subject
        )
        relations.append(
            _build_relation(
                frame=frame,
                subject=subject,
                predicate="tool." + semantic.relation_type.casefold(),
                target=semantic.target_ref,
                source_ref=descriptor_by_action[action_id],
                previous_relations=old_relations,
            )
        )
    dependency_keys = {
        "action-registry:" + tool_world.action_registry_sha256,
        "capability-manifest:" + tool_world.source_manifest_sha256,
        "frame:" + frame.frame_revision_hash,
        "tool-world:" + tool_world.snapshot_sha256,
        "world-cut:" + cut.cut_sha256,
    }
    dependency_keys.update("source:" + item.source_sha256 for item in sources)
    return _contribution(
        kind="TOOL_CAPABILITY",
        binding=binding,
        sources=sources,
        entities=tuple(sorted(entities, key=lambda item: item.entity_id)),
        relations=tuple(sorted(relations, key=lambda item: item.relation_id)),
        dependency_source_keys=tuple(sorted(dependency_keys)),
    )


def compile_skill_method_contribution(
    frame: SoftwareWorldFrame,
    cut: WorldCut,
    method_world: SkillMethodWorldSnapshotV1,
    *,
    previous_entities: Mapping[str, WorldEntity] | None = None,
    previous_relations: Mapping[str, WorldRelation] | None = None,
) -> WorldDomainContributionV1:
    if (
        not method_world.has_valid_sha256()
        or method_world.may_authorize
        or method_world.may_execute
    ):
        raise ValueError("SKILL_METHOD_WORLD_INVALID")
    binding = FrameBindingV1.from_frame(frame, cut)
    binding.require_exact_frame(frame, cut, repository_bound=True)
    old_entities = previous_entities or {}
    old_relations = previous_relations or {}
    sources = tuple(
        sorted(
            (primitive.source_ref for primitive in method_world.primitives),
            key=_source_sort_key,
        )
    )
    entities: list[WorldEntity] = []
    entity_by_method: dict[str, WorldEntity] = {}
    descriptor_by_method: dict[str, WorldRecordRef] = {}
    step_owner: dict[str, str] = {}
    for primitive in method_world.primitives:
        descriptor_ref = _descriptor_ref(
            record_type="skill_method_descriptor",
            record_id="skill-method:" + primitive.method_id,
            sha256=primitive.descriptor_sha256,
        )
        entity = _build_entity(
            frame=frame,
            entity_type="SkillMethod",
            semantic_kind="SKILL_METHOD",
            semantic_id=primitive.method_id,
            canonical_name=primitive.title,
            aliases=(primitive.method_id,),
            attributes={
                "descriptor_sha256": primitive.descriptor_sha256,
                "method_id": primitive.method_id,
                "semantic_summary": primitive.semantic_summary,
                "source_sha256": primitive.source_sha256,
                "version": primitive.version,
            },
            source_ref=descriptor_ref,
            previous_entities=old_entities,
        )
        entities.append(entity)
        entity_by_method[primitive.method_id] = entity
        descriptor_by_method[primitive.method_id] = descriptor_ref
        for step in primitive.method_steps:
            if step in step_owner:
                raise ValueError("SKILL_METHOD_STEP_IDENTITY_COLLISION")
            step_owner[step] = primitive.method_id

    relations: list[WorldRelation] = []
    for semantic in method_world.relations:
        method_id: str | None = None
        if semantic.source_ref.startswith("method:"):
            method_id = semantic.source_ref.removeprefix("method:")
        elif semantic.target_ref.startswith("method:"):
            method_id = semantic.target_ref.removeprefix("method:")
        elif semantic.source_ref in step_owner:
            method_id = step_owner[semantic.source_ref]
        elif semantic.target_ref in step_owner:
            method_id = step_owner[semantic.target_ref]
        if method_id is None or method_id not in entity_by_method:
            raise ValueError("SKILL_METHOD_RELATION_SUBJECT_UNRESOLVED")
        target = semantic.target_ref
        if semantic.relation_type == "SOURCE_REVISION_OF":
            target = semantic.source_ref
        elif semantic.relation_type == "PRECEDES":
            target = semantic.source_ref + ">" + semantic.target_ref
        relations.append(
            _build_relation(
                frame=frame,
                subject=entity_by_method[method_id],
                predicate="method." + semantic.relation_type.casefold(),
                target=target,
                source_ref=descriptor_by_method[method_id],
                previous_relations=old_relations,
            )
        )
    dependency_keys = {
        "frame:" + frame.frame_revision_hash,
        "legacy-skill-corpus:" + method_world.legacy_corpus_sha256,
        "method-sources:" + method_world.method_sources_sha256,
        "method-world:" + method_world.snapshot_sha256,
        "world-cut:" + cut.cut_sha256,
    }
    dependency_keys.update("source:" + item.source_sha256 for item in sources)
    return _contribution(
        kind="SKILL_METHOD",
        binding=binding,
        sources=sources,
        entities=tuple(sorted(entities, key=lambda item: item.entity_id)),
        relations=tuple(sorted(relations, key=lambda item: item.relation_id)),
        dependency_source_keys=tuple(sorted(dependency_keys)),
    )


__all__ = [
    "DOMAIN_CONTRIBUTION_SCHEMA",
    "FRAME_BINDING_SCHEMA",
    "FrameBindingV1",
    "WorldDomainContributionV1",
    "compile_skill_method_contribution",
    "compile_software_domain_contribution",
    "compile_tool_capability_contribution",
]
