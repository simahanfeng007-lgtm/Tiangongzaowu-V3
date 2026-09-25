"""Resolve a finite observation target from hash-bound World records.

Only file revalidation has a measurable discriminator here. Semantic causal
hypotheses remain open until a real discriminator exists; no guessed targets.
"""
from __future__ import annotations
import ntpath
from contracts.canonical import canonical_sha256
from contracts.world_understanding._base import WorldRecordRef


def file_observation_context(inquiry, snapshot):
    if (snapshot is None or snapshot.state.scope != inquiry.scope
            or snapshot.state_ref != inquiry.source_world_state_ref
            or len(inquiry.subject_refs) != 1
            or inquiry.missing_evidence_types != ("revalidation_observation",)):
        return None
    ref = inquiry.subject_refs[0]
    if ref.record_type != "world_entity":
        return None
    for entity in getattr(snapshot, "entities", ()):
        actual = WorldRecordRef(record_type="world_entity", record_id=entity.entity_id,
            revision=entity.revision, sha256=entity.entity_sha256)
        if (actual != ref or entity.entity_type != "File" or entity.lifecycle != "ACTIVE"
                or not entity.has_valid_hash() or entity.scope != inquiry.scope):
            continue
        return {"subject_ref": ref.model_dump(mode="json"), "target": entity.canonical_name,
            "allowed_actions": ["file.hash", "file.read"], "purpose": "revalidate_file_identity_and_content_hash",
            "evidence_required": "native file digest and a fresh revision of this exact File entity",
            "authorization": "NONE"}
    return None


def observation_work_key(inquiry):
    return canonical_sha256({"scope": inquiry.scope.model_dump(mode="json"),
        "subjects": [ref.model_dump(mode="json") for ref in inquiry.subject_refs],
        "missing": inquiry.missing_evidence_types})


def measured_file_gain(inquiry, record, envelope, snapshot):
    """No credit for an unrelated success or the disappearance of an old ref."""
    context = record.get("observation_context")
    payload = envelope.payload_inline or {}
    binding = payload.get("observation_binding")
    if not isinstance(context, dict) or not isinstance(binding, dict) or payload.get("ok") is not True:
        return False, ()
    if (binding.get("action") not in context["allowed_actions"]
            or ntpath.normcase(ntpath.normpath(str(binding.get("target") or ""))) != ntpath.normcase(ntpath.normpath(context["target"]))
            or not binding.get("file_sha256")):
        return False, ()
    old = inquiry.subject_refs[0]
    unresolved_ids = {ref.record_id for ref in (*snapshot.state.stale_refs, *snapshot.state.unresolved_conflict_refs,
        *(() if snapshot.uncertainty is None else snapshot.uncertainty.refs))}
    for entity in getattr(snapshot, "entities", ()):
        attrs = {a.key: a.value.string_value for a in entity.attributes if a.value.kind == "string"}
        if (entity.entity_id == old.record_id and entity.revision > (old.revision or 0)
                and entity.entity_sha256 != old.sha256 and entity.entity_id not in unresolved_ids
                and entity.has_valid_hash() and entity.scope == inquiry.scope
                and entity.truth_state == "TRUE" and entity.epistemic_state == "CURRENT"
                and attrs.get("content_sha256") == binding["file_sha256"]
                and attrs.get("observation_envelope_id") == envelope.envelope_id):
            return True, (WorldRecordRef(record_type="world_entity", record_id=entity.entity_id,
                revision=entity.revision, sha256=entity.entity_sha256),)
    return False, ()
