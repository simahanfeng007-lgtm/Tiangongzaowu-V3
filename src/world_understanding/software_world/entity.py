"""L2 deterministic software-entity materialization and revisioning."""
from __future__ import annotations
from dataclasses import dataclass
from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef, WorldValue
from contracts.world_understanding.entity import (
    WorldAttribute, WorldEntity, EntityResolutionCandidate,
    derive_entity_id, derive_entity_candidate_id,
)
from contracts.world_understanding.time import WorldTime
from .perception import SoftwarePerception, ENTITY_IDENTITY_TYPES
from .frame import SoftwareWorldFrame

@dataclass(frozen=True, slots=True)
class EntitySeed:
    entity_type: str
    stable_anchor: str
    canonical_name: str
    basis_ref: WorldRecordRef
    time: WorldTime
    truth_state: str
    epistemic_state: str
    attributes: tuple[tuple[str, str], ...] = ()
    aliases: tuple[str, ...] = ()


def entity_ref(entity: WorldEntity) -> WorldRecordRef:
    return WorldRecordRef(record_type="world_entity", record_id=entity.entity_id, revision=entity.revision, sha256=entity.entity_sha256)


def _attribute(key: str, value: str) -> WorldAttribute:
    return WorldAttribute(key=key, value=WorldValue(kind="string", string_value=value), attribute_sha256="0" * 64).with_computed_hash()


def _attrs(values: dict[str, str | None]) -> tuple[WorldAttribute, ...]:
    return tuple(_attribute(key, value) for key, value in sorted(values.items()) if value is not None and str(value) != "")


def anchor_hash(entity_type: str, stable_anchor: str) -> str:
    if not stable_anchor.strip():
        raise ValueError("entity stable anchor must be explicit")
    return canonical_sha256({
        "domain": "tiangong.world.software-entity-anchor.v1",
        "entity_type": entity_type,
        "stable_anchor": stable_anchor,
    })


def seeds_from_perceptions(perceptions: tuple[SoftwarePerception, ...]) -> tuple[EntitySeed, ...]:
    output = []
    for perception in perceptions:
        entity_type = ENTITY_IDENTITY_TYPES.get(perception.proposition_type)
        if entity_type is None or perception.object_text is None:
            continue
        record = perception.record
        output.append(EntitySeed(
            entity_type=entity_type,
            stable_anchor=perception.subject_ref,
            canonical_name=perception.object_text,
            basis_ref=perception.known_ref,
            time=record.time,
            truth_state=record.truth_state,
            epistemic_state=record.epistemic_state,
        ))
    return tuple(output)


def build_entity(frame: SoftwareWorldFrame, seed: EntitySeed, *, previous: WorldEntity | None = None,
                 attributes: dict[str, str | None] | None = None, aliases: tuple[str, ...] = (),
                 lifecycle: str = "ACTIVE") -> WorldEntity:
    identity_anchor_hash = anchor_hash(seed.entity_type, seed.stable_anchor)
    entity_id = derive_entity_id(life_id=frame.scope.life_id, domain_id=frame.scope.domain_id, identity_anchor_hash=identity_anchor_hash)
    if previous is not None and previous.entity_id != entity_id:
        raise ValueError("ENTITY_IDENTITY_DRIFT")
    merged_aliases = set(seed.aliases)
    merged_aliases.update(aliases)
    source_refs = {seed.basis_ref.sort_key(): seed.basis_ref}
    if previous is not None:
        merged_aliases.update(previous.aliases)
        if previous.canonical_name != seed.canonical_name:
            merged_aliases.add(previous.canonical_name)
        for ref in previous.source_observation_refs:
            source_refs[ref.sort_key()] = ref
    intended_aliases = tuple(sorted(alias for alias in merged_aliases if alias and alias != seed.canonical_name))
    if attributes is not None:
        intended_attributes = _attrs(attributes)
    elif seed.attributes:
        intended_attributes = _attrs(dict(seed.attributes))
    elif previous is not None:
        intended_attributes = previous.attributes
    else:
        intended_attributes = ()
    intended_locations = () if previous is None else previous.location_refs
    intended_sources = tuple(source_refs[key] for key in sorted(source_refs))
    if previous is not None and (
        previous.entity_type == seed.entity_type
        and previous.canonical_name == seed.canonical_name
        and previous.aliases == intended_aliases
        and previous.attributes == intended_attributes
        and previous.location_refs == intended_locations
        and previous.source_observation_refs == intended_sources
        and previous.truth_state == seed.truth_state
        and previous.epistemic_state == seed.epistemic_state
        and previous.lifecycle == lifecycle
        and previous.time == seed.time
    ):
        return previous
    revision = 1 if previous is None else previous.revision + 1
    entity = WorldEntity(
        entity_id=entity_id,
        scope=frame.scope,
        entity_type=seed.entity_type,
        identity_anchor_hash=identity_anchor_hash,
        canonical_name=seed.canonical_name,
        aliases=intended_aliases,
        attributes=intended_attributes,
        location_refs=intended_locations,
        source_observation_refs=intended_sources,
        truth_state=seed.truth_state,
        epistemic_state=seed.epistemic_state,
        lifecycle=lifecycle,
        replacement_refs=(),
        revision=revision,
        supersedes_entity_sha256=None if previous is None else previous.entity_sha256,
        time=seed.time,
        entity_sha256="0" * 64,
    ).with_computed_hash()
    return entity


def revise_file_entity(frame: SoftwareWorldFrame, previous: WorldEntity, *, new_path: str,
                       commit: str, blob_sha: str | None, basis_ref: WorldRecordRef | None,
                       lifecycle: str = "ACTIVE") -> WorldEntity:
    if previous.entity_type != "File":
        raise ValueError("FILE_REVISION_REQUIRES_FILE_ENTITY")
    # preserve the original identity anchor hash instead of deriving a new path-based identity
    aliases = set(previous.aliases)
    if previous.canonical_name != new_path:
        aliases.add(previous.canonical_name)
    attrs = {
        item.key: item.value.string_value
        for item in previous.attributes
        if item.value.kind == "string" and item.value.string_value is not None
    }
    attrs.update({"path": new_path, "commit": commit, "blob_sha": blob_sha})
    source_refs = {ref.sort_key(): ref for ref in previous.source_observation_refs}
    if basis_ref is not None:
        source_refs[basis_ref.sort_key()] = basis_ref
    entity = WorldEntity(
        entity_id=previous.entity_id,
        scope=frame.scope,
        entity_type="File",
        identity_anchor_hash=previous.identity_anchor_hash,
        canonical_name=new_path,
        aliases=tuple(sorted(alias for alias in aliases if alias and alias != new_path)),
        attributes=_attrs(attrs),
        location_refs=(),
        source_observation_refs=tuple(source_refs[key] for key in sorted(source_refs)),
        truth_state="TRUE",
        epistemic_state="CURRENT",
        lifecycle=lifecycle,
        replacement_refs=(),
        revision=previous.revision + 1,
        supersedes_entity_sha256=previous.entity_sha256,
        time=frame.time,
        entity_sha256="0" * 64,
    ).with_computed_hash()
    return entity


def new_file_entity(frame: SoftwareWorldFrame, *, path: str, commit: str, blob_sha: str | None,
                    basis_ref: WorldRecordRef | None, explicit_identity_anchor: str | None = None) -> WorldEntity:
    stable_anchor = explicit_identity_anchor or canonical_sha256({
        "domain": "tiangong.world.software-file-first-seen.v1",
        "life_id": frame.scope.life_id,
        "repository": frame.repository,
        "worktree": frame.worktree,
        "first_seen_commit": commit,
        "path": path,
    })
    if basis_ref is None:
        basis_ref = WorldRecordRef(record_type="software_world_frame", record_id=frame.frame_id, revision=None, sha256=frame.frame_revision_hash)
    seed = EntitySeed("File", stable_anchor, path, basis_ref, frame.time, "TRUE", "CURRENT")
    return build_entity(frame, seed, attributes={"path": path, "commit": commit, "blob_sha": blob_sha})


def ambiguous_resolution(frame: SoftwareWorldFrame, *, basis_ref: WorldRecordRef,
                         candidates: tuple[WorldEntity, ...], reason: str) -> EntityResolutionCandidate:
    refs = tuple(sorted((entity_ref(entity) for entity in candidates), key=lambda ref: ref.sort_key()))
    basis = (basis_ref,)
    candidate_id = derive_entity_candidate_id(
        world_scope_hash=frame.scope.world_scope_hash,
        state="AMBIGUOUS",
        basis_refs=basis,
        candidate_entity_refs=refs,
    )
    return EntityResolutionCandidate(
        candidate_id=candidate_id,
        scope=frame.scope,
        state="AMBIGUOUS",
        basis_refs=basis,
        candidate_entity_refs=refs,
        resolution_score_milli=0,
        reason_codes=(reason,),
        candidate_sha256="0" * 64,
    ).with_computed_hash()

__all__ = [
    "EntitySeed", "entity_ref", "anchor_hash", "seeds_from_perceptions", "build_entity",
    "revise_file_entity", "new_file_entity", "ambiguous_resolution",
]
