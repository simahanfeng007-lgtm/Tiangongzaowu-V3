"""L1 typed perception over existing Direct/Derived Known; no new truth authority."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from contracts.world_understanding._base import WorldRecordRef
from world_understanding.known.set import KnownRecord, known_ref
from world_understanding.common.scope import require_exact_scope
from .frame import SoftwareWorldFrame

PerceptionKind = Literal["IDENTITY", "STRUCTURE", "EVENT", "OBSERVATION"]

ENTITY_IDENTITY_TYPES = {
    "REPOSITORY_IDENTITY": "Repository",
    "WORKTREE_IDENTITY": "Worktree",
    "FILE_IDENTITY": "File",
    "MODULE_IDENTITY": "Module",
    "CLASS_IDENTITY": "Class",
    "FUNCTION_IDENTITY": "Function",
    "METHOD_IDENTITY": "Method",
    "TOOL_IDENTITY": "Tool",
    "RUNTIME_IDENTITY": "Runtime",
    "GATEWAY_IDENTITY": "Gateway",
    "GRANT_IDENTITY": "Grant",
    "EXECUTION_TICKET_IDENTITY": "ExecutionTicket",
    "KNOWLEDGE_DOCUMENT_IDENTITY": "KnowledgeDocument",
    "MEMORY_STORE_IDENTITY": "MemoryStore",
}
RELATION_TYPES = frozenset({
    "CONTAINS", "DEFINES", "IMPORTS", "DIRECT_CALLS", "CALL_REACHABLE",
    "USES", "READS", "WRITES", "REGISTERED_AS", "BELONGS_TO", "LOCATED_IN",
})
FORBIDDEN_SEMANTIC_RELATIONS = frozenset({"GUARDED_BY", "AUTHORITATIVE_FOR", "IS_BOUNDARY_OF"})

@dataclass(frozen=True, slots=True)
class SoftwarePerception:
    frame_id: str
    kind: PerceptionKind
    proposition_type: str
    subject_ref: str
    object_text: str | None
    known_ref: WorldRecordRef
    record: KnownRecord


def _object_text(record: KnownRecord) -> str | None:
    value = record.object_value
    if value is None:
        return None
    if value.kind == "string":
        return value.string_value
    if value.kind == "entity_ref":
        return value.entity_ref
    return None


def classify_known(record: KnownRecord) -> PerceptionKind:
    if record.proposition_type in ENTITY_IDENTITY_TYPES:
        return "IDENTITY"
    if record.proposition_type in RELATION_TYPES or record.proposition_type in FORBIDDEN_SEMANTIC_RELATIONS:
        return "STRUCTURE"
    if record.proposition_type in {"CHAIN_EVENT_RECORDED", "EVENT_PRECEDES", "FILE_CREATED", "FILE_DELETED", "FILE_CONTENT_CHANGED"}:
        return "EVENT"
    return "OBSERVATION"


def perceive_known(frame: SoftwareWorldFrame, records: tuple[KnownRecord, ...]) -> tuple[SoftwarePerception, ...]:
    output = []
    for record in records:
        require_exact_scope(frame.scope, record.world_scope)
        output.append(SoftwarePerception(
            frame_id=frame.frame_id,
            kind=classify_known(record),
            proposition_type=record.proposition_type,
            subject_ref=record.subject_ref,
            object_text=_object_text(record),
            known_ref=known_ref(record),
            record=record,
        ))
    return tuple(output)

__all__ = [
    "PerceptionKind", "ENTITY_IDENTITY_TYPES", "RELATION_TYPES", "FORBIDDEN_SEMANTIC_RELATIONS",
    "SoftwarePerception", "classify_known", "perceive_known",
]
