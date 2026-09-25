"""Durable, user-endorsed composition DATA in the existing memory authority.

These records are examples for a model to adapt, never executable capabilities
or grants. All writes go through MemoryCoordinator and its atomic active head.
"""
from __future__ import annotations

import json

from contracts import MemoryPromotionDisposition, canonical_json_bytes, canonical_sha256, derive_promotion_key
from .memory_coordinator import l1_derivation_id

SCHEMA = "tiangong.composition-experience.v1"
LESSON_SCHEMA = "tiangong.composition-lesson.v1"
POLICY = "composition-experience-v1"


def is_experience_payload(value):
    """Managed examples must only be recalled by their scoped active-head reader."""
    if isinstance(value, (str, bytes)):
        try:
            value = json.loads(value)
        except (ValueError, UnicodeDecodeError):
            return False
    return isinstance(value, dict) and value.get("schema") in {SCHEMA, LESSON_SCHEMA}


def read_experiences(coordinator, *, life_id, principal_ref, schema=SCHEMA):
    if schema not in {SCHEMA, LESSON_SCHEMA}:
        raise ValueError("composition_memory.schema_invalid")
    store = coordinator.store
    result = []
    for derivation in store.list_memory_derivations(life_id=life_id, layer="L3_EXPERIENCE", active_only=True, limit=4096):
        if derivation.principal_ref != principal_ref or derivation.privacy_scope != "private":
            continue
        if not derivation.claim_key.startswith("composition-experience:"):
            continue
        head = store.get_active_memory_head(life_id=life_id, principal_ref=principal_ref,
            claim_key=derivation.claim_key, layer="L3_EXPERIENCE")
        if head is None or head.derivation_id != derivation.derivation_id:
            continue
        assertion = store.get_memory_assertion(derivation.memory_id, derivation.memory_revision)
        if assertion is None or assertion.lifecycle_status != "active":
            continue
        payload = json.loads(store.read_protected_payload(assertion.protected_payload_id))
        if payload.get("schema") == schema and payload.get("may_execute") is False and payload.get("may_authorize") is False:
            result.append((payload, derivation))
    return result


def commit_experience(coordinator, *, payload, life_id, principal_ref, event_id, now_ms):
    """Called exclusively by the coordinator; retains an L1 event and L3 head."""
    if payload.get("schema") not in {SCHEMA, LESSON_SCHEMA} or payload.get("may_execute") is not False or payload.get("may_authorize") is not False:
        raise ValueError("composition_memory.invalid_data_contract")
    automatic = payload["schema"] == LESSON_SCHEMA
    experience_id = payload["experience_id"]
    claim = "composition-experience:" + experience_id
    store = coordinator.store
    previous = store.get_active_memory_head(life_id=life_id, principal_ref=principal_ref,
        claim_key=claim, layer="L3_EXPERIENCE")
    if previous:
        old = json.loads(store.read_protected_payload(store.get_memory_assertion(previous.memory_id, previous.memory_revision).protected_payload_id))
        if event_id in old.get("event_ids", []):
            return {"ok": True, "experience_id": experience_id, "memory_id": previous.memory_id,
                "derivation_id": previous.derivation_id, "duplicate": True, "status": old["status"]}
    # A feedback retry cannot turn one observation into multiple successes.
    payload = {**payload, "event_ids": sorted(set(payload.get("event_ids", [])) | {event_id})}
    now_ms = max(now_ms, (previous.created_at_ms + 2) if previous else 0)
    event_ref = "lev_" + canonical_sha256({"experience": experience_id, "event": event_id})
    plaintext = canonical_json_bytes(payload)
    parent = store.get_memory_derivation(l1_derivation_id(life_id=life_id, source_event_id=event_ref))
    if parent is None:
        coordinator.commit_contract_assertion(plaintext=plaintext,
            memory_id="mem_" + canonical_sha256({"event": event_ref}), life_id=life_id, principal_ref=principal_ref,
            assertion_kind="observation", epistemic_status="observed" if automatic else "user_asserted", lifecycle_status="active",
            privacy_scope="private", retention_class="CHECKPOINT", source_event_ids=(event_ref,),
            valid_from_ms=now_ms, created_at_ms=now_ms)
        parent = store.get_memory_derivation(l1_derivation_id(life_id=life_id, source_event_id=event_ref))
    else:
        assertion = store.get_memory_assertion(parent.memory_id, parent.memory_revision)
        if store.read_protected_payload(assertion.protected_payload_id) != plaintext:
            raise ValueError("composition_memory.event_rebound")
        now_ms = max(now_ms, parent.created_at_ms + 1)
    parents = tuple(sorted((parent,) + ((previous,) if previous else ()), key=lambda item: item.derivation_id))
    roots = tuple(sorted({root for item in parents for root in item.lineage_root_event_ids}))
    hashes = tuple(sorted({item.memory_assertion_sha256 for item in parents}))
    key = derive_promotion_key(policy_version=POLICY, life_id=life_id, target_layer="L3_EXPERIENCE",
        parent_assertion_sha256=hashes, semantic_domain="CAPABILITY_KNOWLEDGE", claim_key=claim, lineage_root_event_ids=roots)
    disposition = MemoryPromotionDisposition(promotion_key=key, life_id=life_id, principal_ref=principal_ref,
        target_layer="L3_EXPERIENCE", claim_key=claim, semantic_domain="CAPABILITY_KNOWLEDGE", policy_version=POLICY,
        parent_assertion_sha256=hashes, lineage_root_event_ids=roots, allowed=True,
        reason_codes=("composition_experience.execution_observation" if automatic else "composition_experience.user_endorsed_data",), support_milli=500, counter_milli=0,
        independence_group_count=1, recurrence_count=1, valid_from_ms=now_ms, created_at_ms=now_ms + 1,
        disposition_sha256="0" * 64).with_computed_disposition_sha256()
    assertion, derivation, created = coordinator._materialize_promotion(disposition=disposition, parents=parents,
        principal_ref=principal_ref, privacy_scope="private", plaintext=plaintext, created_at_ms=now_ms + 1,
        policy_version=POLICY, head_guard=(claim, "L3_EXPERIENCE", previous.derivation_sha256 if previous else None), observed_only=automatic)
    return {"ok": True, "experience_id": experience_id, "memory_id": assertion.memory_id,
        "derivation_id": derivation.derivation_id, "duplicate": not created, "status": payload["status"]}
