"""Incremental L0-L3 Software World updater.

No filesystem/git/network scan is permitted. Repository and parser adapters
must supply already-observed evidence through Known/GitCommitDelta.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from contracts.world_understanding.entity import WorldEntity, EntityResolutionCandidate
from contracts.world_understanding.relation import WorldRelation
from world_understanding.common.epistemic import EpistemicPlane
from world_understanding.known.set import KnownRecord
from .frame import SoftwareWorldFrame
from .perception import perceive_known, SoftwarePerception
from .entity import (
    EntitySeed,
    ambiguous_resolution,
    anchor_hash,
    build_entity,
    new_file_entity,
    revise_file_entity,
    seeds_from_perceptions,
)
from .git_delta import GitCommitDelta, GitPathChange
from .graph import SparseWorldGraph
from .relation import materialize_relation

@dataclass(frozen=True, slots=True)
class SoftwareWorldUpdateStats:
    known_delta_count: int
    git_change_count: int
    entity_upserts: int
    relation_upserts: int
    relation_removals: int
    entities_examined: int
    relations_examined: int
    full_rescan: bool = False

@dataclass(frozen=True, slots=True)
class SoftwareWorldUpdateResult:
    graph: SparseWorldGraph
    perceptions: tuple[SoftwarePerception, ...]
    touched_entity_ids: tuple[str, ...]
    touched_relation_ids: tuple[str, ...]
    identity_candidates: tuple[EntityResolutionCandidate, ...]
    diagnostics: tuple[str, ...]
    stats: SoftwareWorldUpdateStats

class _Journal:
    __slots__ = ("graph", "old_entities", "old_relations", "old_frame_revision")
    def __init__(self, graph: SparseWorldGraph) -> None:
        self.graph = graph
        self.old_entities: dict[str, WorldEntity | None] = {}
        self.old_relations: dict[str, WorldRelation | None] = {}
        self.old_frame_revision = graph.frame_revision_hash

    def entity(self, entity_id: str) -> None:
        if entity_id not in self.old_entities:
            self.old_entities[entity_id] = self.graph.entity(entity_id)

    def relation(self, relation_id: str) -> None:
        if relation_id not in self.old_relations:
            self.old_relations[relation_id] = self.graph.relation(relation_id)

    def rollback(self) -> None:
        for relation_id in tuple(self.old_relations):
            self.graph.delete_relation(relation_id)
        for relation_id, old in self.old_relations.items():
            if old is not None:
                self.graph.upsert_relation(old)
        for entity_id in tuple(self.old_entities):
            self.graph.delete_entity(entity_id)
        for entity_id, old in self.old_entities.items():
            if old is not None:
                self.graph.upsert_entity(old)
        self.graph.frame_revision_hash = self.old_frame_revision

class SoftwareWorldUpdater:
    __slots__ = ("epistemic_plane",)
    def __init__(self, epistemic_plane: EpistemicPlane | None = None) -> None:
        self.epistemic_plane = epistemic_plane or EpistemicPlane()

    def _remove_touching_relations(
        self,
        graph: SparseWorldGraph,
        journal: _Journal,
        entity_id: str,
        touched_relations: set[str],
    ) -> int:
        rows = graph.relations_touching(entity_id)
        for relation in rows:
            journal.relation(relation.relation_id)
            graph.delete_relation(relation.relation_id)
            touched_relations.add(relation.relation_id)
        return len(rows)

    def _apply_git_change(
        self,
        frame: SoftwareWorldFrame,
        graph: SparseWorldGraph,
        change: GitPathChange,
        journal: _Journal,
        touched_entities: set[str],
        touched_relations: set[str],
        candidates: list[EntityResolutionCandidate],
        diagnostics: list[str],
    ) -> tuple[int, int]:
        source_ref = change.source_ref
        if change.change_kind == "ADD":
            entity = new_file_entity(
                frame,
                path=change.new_path or "",
                commit=frame.commit,
                blob_sha=change.new_blob_sha,
                basis_ref=source_ref,
                explicit_identity_anchor=change.explicit_identity_anchor,
            )
            journal.entity(entity.entity_id)
            graph.upsert_entity(entity)
            touched_entities.add(entity.entity_id)
            return 1, 0

        path = change.old_path or ""
        matches = graph.file_entities(path)
        if len(matches) > 1:
            if source_ref is not None:
                candidates.append(
                    ambiguous_resolution(
                        frame,
                        basis_ref=source_ref,
                        candidates=matches,
                        reason="AMBIGUOUS_FILE_IDENTITY",
                    )
                )
            diagnostics.append("AMBIGUOUS_FILE_IDENTITY")
            return 0, 0
        if len(matches) == 0:
            if change.change_kind == "MODIFY":
                entity = new_file_entity(
                    frame,
                    path=change.new_path or path,
                    commit=frame.commit,
                    blob_sha=change.new_blob_sha,
                    basis_ref=source_ref,
                    explicit_identity_anchor=change.explicit_identity_anchor,
                )
                journal.entity(entity.entity_id)
                graph.upsert_entity(entity)
                touched_entities.add(entity.entity_id)
                diagnostics.append("MODIFY_UNSEEN_FILE_SEEDED")
                return 1, 0
            diagnostics.append(
                "RENAME_OR_DELETE_SOURCE_UNRESOLVED"
                if change.change_kind in {"RENAME", "MOVE", "DELETE"}
                else "FILE_SOURCE_UNRESOLVED"
            )
            return 0, 0

        previous = matches[0]
        relation_removals = self._remove_touching_relations(
            graph, journal, previous.entity_id, touched_relations
        )
        journal.entity(previous.entity_id)
        if change.change_kind == "DELETE":
            entity = revise_file_entity(
                frame,
                previous,
                new_path=previous.canonical_name,
                commit=frame.commit,
                blob_sha=change.old_blob_sha,
                basis_ref=source_ref,
                lifecycle="RETIRED",
            )
        else:
            entity = revise_file_entity(
                frame,
                previous,
                new_path=change.new_path or path,
                commit=frame.commit,
                blob_sha=change.new_blob_sha,
                basis_ref=source_ref,
                lifecycle="ACTIVE",
            )
        graph.upsert_entity(entity)
        touched_entities.add(entity.entity_id)
        return 1, relation_removals

    def _apply_structure_retirement(
        self,
        frame: SoftwareWorldFrame,
        graph: SparseWorldGraph,
        perception: SoftwarePerception,
        journal: _Journal,
        touched_entities: set[str],
        touched_relations: set[str],
        diagnostics: list[str],
    ) -> tuple[int, int]:
        if perception.object_text is None:
            diagnostics.append("STRUCTURE_RETIREMENT_PAYLOAD_MISSING")
            return 0, 0
        try:
            payload = json.loads(perception.object_text)
        except (TypeError, ValueError):
            diagnostics.append("STRUCTURE_RETIREMENT_PAYLOAD_INVALID")
            return 0, 0
        entity_type = str(payload.get("entity_type") or "")
        canonical_name = str(payload.get("canonical_name") or "")
        if entity_type not in {"Module", "Class", "Function", "Method"} or not canonical_name:
            diagnostics.append("STRUCTURE_RETIREMENT_PAYLOAD_INVALID")
            return 0, 0

        from contracts.world_understanding.entity import derive_entity_id
        expected_id = derive_entity_id(
            life_id=frame.scope.life_id,
            domain_id=frame.scope.domain_id,
            identity_anchor_hash=anchor_hash(entity_type, perception.subject_ref),
        )
        previous = graph.entity(expected_id)
        if previous is None:
            diagnostics.append("STRUCTURE_RETIREMENT_SOURCE_UNRESOLVED")
            return 0, 0
        removals = self._remove_touching_relations(
            graph, journal, previous.entity_id, touched_relations
        )
        seed = EntitySeed(
            entity_type=entity_type,
            stable_anchor=perception.subject_ref,
            canonical_name=canonical_name,
            basis_ref=perception.known_ref,
            time=perception.record.time,
            truth_state=perception.record.truth_state,
            epistemic_state=perception.record.epistemic_state,
        )
        entity = build_entity(
            frame, seed, previous=previous, lifecycle="RETIRED"
        )
        if entity.entity_sha256 == previous.entity_sha256:
            return 0, removals
        journal.entity(entity.entity_id)
        graph.upsert_entity(entity)
        touched_entities.add(entity.entity_id)
        return 1, removals

    def update(
        self,
        *,
        frame: SoftwareWorldFrame,
        graph: SparseWorldGraph | None = None,
        known_delta: tuple[KnownRecord, ...] = (),
        git_delta: GitCommitDelta | None = None,
    ) -> SoftwareWorldUpdateResult:
        graph = graph or SparseWorldGraph(frame)
        graph.require_frame(frame)
        if git_delta is not None:
            git_delta.validate_frame(frame)
        perceptions = perceive_known(frame, known_delta)
        journal = _Journal(graph)
        touched_entities: set[str] = set()
        touched_relations: set[str] = set()
        candidates: list[EntityResolutionCandidate] = []
        diagnostics: list[str] = []
        entity_upserts = 0
        relation_upserts = 0
        relation_removals = 0
        entities_examined = 0
        relations_examined = 0
        try:
            stable_perceptions: list[SoftwarePerception] = []
            for perception in perceptions:
                decision = self.epistemic_plane.evaluate_known(
                    perception.record, expected_scope=frame.scope
                )
                if not decision.admissible or not decision.stable_promotion:
                    diagnostics.append(
                        "GAMMA_BLOCKED:" + ",".join(decision.reason_codes)
                    )
                    continue
                if (
                    perception.kind in {"IDENTITY", "STRUCTURE"}
                    and perception.record.truth_state != "TRUE"
                ):
                    diagnostics.append("P6_MATERIALIZATION_REQUIRES_TRUE")
                    continue
                stable_perceptions.append(perception)

            seeds = seeds_from_perceptions(tuple(stable_perceptions))
            entities_examined += len(seeds)
            from contracts.world_understanding.entity import derive_entity_id
            for seed in seeds:
                expected_id = derive_entity_id(
                    life_id=frame.scope.life_id,
                    domain_id=frame.scope.domain_id,
                    identity_anchor_hash=anchor_hash(
                        seed.entity_type, seed.stable_anchor
                    ),
                )
                previous = graph.entity(expected_id)
                entity = build_entity(frame, seed, previous=previous)
                if previous is None or entity.entity_sha256 != previous.entity_sha256:
                    journal.entity(entity.entity_id)
                    graph.upsert_entity(entity)
                    touched_entities.add(entity.entity_id)
                    entity_upserts += 1
                    if previous is not None:
                        relation_removals += self._remove_touching_relations(
                            graph, journal, entity.entity_id, touched_relations
                        )

            apply_git = (
                git_delta is not None and not graph.has_git_delta(git_delta.delta_id)
            )
            if git_delta is not None and not apply_git:
                diagnostics.append("GIT_DELTA_ALREADY_APPLIED")
            if apply_git:
                for change in git_delta.changes:
                    entities_examined += 1
                    upserts, removals = self._apply_git_change(
                        frame,
                        graph,
                        change,
                        journal,
                        touched_entities,
                        touched_relations,
                        candidates,
                        diagnostics,
                    )
                    entity_upserts += upserts
                    relation_removals += removals

            for perception in stable_perceptions:
                if perception.proposition_type != "STRUCTURE_ENTITY_RETIRED":
                    continue
                entities_examined += 1
                upserts, removals = self._apply_structure_retirement(
                    frame,
                    graph,
                    perception,
                    journal,
                    touched_entities,
                    touched_relations,
                    diagnostics,
                )
                entity_upserts += upserts
                relation_removals += removals

            for perception in stable_perceptions:
                if perception.kind != "STRUCTURE":
                    continue
                relations_examined += 1
                result = materialize_relation(graph, perception)
                if result.relation is None:
                    if result.reason_code != "NOT_A_P6_RELATION":
                        diagnostics.append(result.reason_code)
                    continue
                relation = result.relation
                journal.relation(relation.relation_id)
                graph.upsert_relation(relation)
                touched_relations.add(relation.relation_id)
                relation_upserts += 1

            graph.advance_frame(frame)
            if git_delta is not None and apply_git:
                graph.mark_git_delta(git_delta.delta_id)
        except Exception:
            journal.rollback()
            raise

        stats = SoftwareWorldUpdateStats(
            len(known_delta),
            0 if git_delta is None or not apply_git else len(git_delta.changes),
            entity_upserts,
            relation_upserts,
            relation_removals,
            entities_examined,
            relations_examined,
            False,
        )
        return SoftwareWorldUpdateResult(
            graph,
            perceptions,
            tuple(sorted(touched_entities)),
            tuple(sorted(touched_relations)),
            tuple(candidates),
            tuple(sorted(set(diagnostics))),
            stats,
        )


__all__ = ["SoftwareWorldUpdateStats", "SoftwareWorldUpdateResult", "SoftwareWorldUpdater"]
