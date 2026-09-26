"""Replay-stable memory metadata from explicit fields and typed relations.

Natural-language content is preserved and validated as data. It never creates
causal relations, user preferences, rules, goals or retention decisions.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from contracts import canonical_sha256

CLASSIFIER_VERSION = "tiangong.life.memory-classifier.v2"

MEMORY_TYPES = {
    "working",
    "episodic",
    "semantic",
    "procedural",
    "relationship",
    "preference",
    "rule",
    "goal",
    "skill",
    "causal",
    "observation",
}

_TYPE_ALIASES = {
    "auto": "",
    "fact": "semantic",
    "facts": "semantic",
    "event": "episodic",
    "episode": "episodic",
    "procedure": "procedural",
    "constraint": "rule",
    "hard_constraint": "rule",
    "user_preference": "preference",
    "cause": "causal",
    "effect": "causal",
}

CAUSAL_RELATION_KINDS = {
    "causes",
    "caused_by",
    "leads_to",
    "result_of",
    "triggers",
    "enables",
    "prevents",
    "contributes_to",
    "explains",
}
NONCAUSAL_RELATION_KINDS = {
    "supports",
    "related_to",
    "contradicts",
    "refines",
    "derived_from",
    "temporal_before",
    "supersedes",
    "legacy_unclassified",
}
ALLOWED_RELATION_KINDS = CAUSAL_RELATION_KINDS | NONCAUSAL_RELATION_KINDS

def _nfc_text(value: str) -> str:
    text = unicodedata.normalize("NFC", value)
    if "\x00" in text:
        raise ValueError("memory text contains NUL")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
        raise ValueError("memory text contains a forbidden control character")
    return text


def _flatten(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 12:
        raise ValueError("memory payload nesting is too deep")
    if value is None:
        return []
    if isinstance(value, str):
        return [_nfc_text(value)]
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if isinstance(value, int):
        return [str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("memory payload contains a non-finite number")
        return [str(value)]
    if isinstance(value, Mapping):
        rows: list[str] = []
        for key in sorted(value, key=lambda item: str(item)):
            rows.append(_nfc_text(str(key)))
            rows.extend(_flatten(value[key], depth=depth + 1))
        return rows
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        rows = []
        for item in value:
            rows.extend(_flatten(item, depth=depth + 1))
        return rows
    raise ValueError("memory payload contains a non-canonical value")


def normalize_relations(relations: Any) -> list[dict[str, Any]]:
    if relations is None:
        return []
    if not isinstance(relations, list):
        raise ValueError("memory relations must be an array")
    if len(relations) > 1024:
        raise ValueError("memory relations exceed the bound")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(relations):
        if not isinstance(raw, Mapping):
            raise ValueError("memory relation must be an object")
        kind = str(raw.get("kind") or raw.get("relation_kind") or "related_to").strip().lower()
        if kind not in ALLOWED_RELATION_KINDS:
            raise ValueError("memory relation kind is unsupported")
        target = str(
            raw.get("target_memory_id")
            or raw.get("target_ref")
            or raw.get("target")
            or ""
        ).strip()
        if target and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}", target):
            raise ValueError("memory relation target is invalid")
        semantic_relation = {
            "kind": kind,
            "target_ref": target,
            "direction": str(raw.get("direction") or "forward").strip().lower() or "forward",
            "evidence": raw.get("evidence") if "evidence" in raw else None,
        }
        fingerprint = canonical_sha256(semantic_relation)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        normalized.append({**semantic_relation, "index": len(normalized)})
    return normalized


def _explicit_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = _TYPE_ALIASES.get(raw, raw)
    if raw and raw not in MEMORY_TYPES:
        raise ValueError("memory type is unsupported")
    return raw


def classify_memory(
    *,
    content: Any,
    provenance: Mapping[str, Any] | None,
    relations: Any,
    requested_memory_type: Any = "",
    requested_causal_role: Any = "",
    epistemic_status: str = "user_asserted",
    confidence_milli: int = 800,
    priority: int = 900,
) -> dict[str, Any]:
    """Return a replay-stable classification and normalized relation set."""

    normalized_relations = normalize_relations(relations)
    provenance_value = dict(provenance or {})
    _flatten({"content": content, "provenance": provenance_value})
    keys = {
        str(key).casefold()
        for value in (content, provenance_value)
        if isinstance(value, Mapping)
        for key in value
    }
    relation_kinds = {row["kind"] for row in normalized_relations}
    cause_relation = bool(
        relation_kinds
        & {"causes", "leads_to", "triggers", "enables", "prevents", "contributes_to", "explains"}
    )
    effect_relation = bool(relation_kinds & {"caused_by", "result_of"})
    causal_relation = cause_relation or effect_relation
    has_cause = cause_relation or bool(
        keys & {"cause", "cause_ref", "cause_memory_id", "reason", "because"}
    )
    has_effect = effect_relation or bool(
        keys & {"effect", "effect_ref", "effect_memory_id", "result", "outcome", "consequence"}
    )
    has_action = bool(keys & {"action", "decision", "intervention", "tool_action"})
    has_goal = bool(keys & {"goal", "objective", "target"})
    has_constraint = bool(keys & {"constraint", "rule", "policy", "forbidden"})
    has_preference = bool(keys & {"preference", "likes", "dislikes"})
    has_skill = bool(keys & {"skill", "capability", "procedure", "steps"})
    has_procedure = bool(keys & {"steps", "procedure", "workflow", "instructions"})
    has_relationship = bool(keys & {"relationship_id", "person_id", "customer_id", "contact_id"})
    has_episode = bool(keys & {"event_id", "request_id", "run_id", "timestamp", "occurred_at"})

    explicit = _explicit_type(requested_memory_type)
    reason_codes: list[str] = []
    derived_type = "semantic"
    if has_cause or has_effect:
        derived_type = "causal"
        reason_codes.append("memory.classifier.causal_evidence")
    elif has_constraint:
        derived_type = "rule"
        reason_codes.append("memory.classifier.constraint")
    elif has_goal:
        derived_type = "goal"
        reason_codes.append("memory.classifier.goal")
    elif has_preference:
        derived_type = "preference"
        reason_codes.append("memory.classifier.preference")
    elif has_procedure:
        derived_type = "procedural"
        reason_codes.append("memory.classifier.procedure")
    elif has_skill:
        derived_type = "skill"
        reason_codes.append("memory.classifier.skill")
    elif has_relationship:
        derived_type = "relationship"
        reason_codes.append("memory.classifier.relationship")
    elif has_episode:
        derived_type = "episodic"
        reason_codes.append("memory.classifier.episode")
    else:
        reason_codes.append("memory.classifier.semantic_default")

    # Preserve a specific caller type, but generic semantic/working hints do not
    # suppress stronger causal evidence.  The original hint remains auditable.
    if explicit and not (derived_type == "causal" and explicit in {"semantic", "working", "observation"}):
        memory_type = explicit
        reason_codes.append("memory.classifier.explicit_type")
    else:
        memory_type = derived_type

    requested_role = str(requested_causal_role or "").strip().lower()
    allowed_roles = {
        "cause",
        "effect",
        "action",
        "outcome",
        "goal",
        "constraint",
        "observation",
        "context",
        "causal_summary",
        "unknown",
    }
    if requested_role and requested_role not in allowed_roles:
        raise ValueError("causal role is unsupported")
    if has_cause and has_effect:
        derived_causal_role = "causal_summary"
    elif has_cause:
        derived_causal_role = "cause"
    elif has_effect:
        derived_causal_role = "outcome" if "outcome" in keys else "effect"
    else:
        derived_causal_role = ""
    if requested_role and derived_causal_role:
        compatible = (
            requested_role == derived_causal_role
            or {requested_role, derived_causal_role} <= {"effect", "outcome"}
        )
        if not compatible:
            raise ValueError("explicit causal role contradicts derived evidence")
    if requested_role:
        causal_role = requested_role
        reason_codes.append("memory.classifier.explicit_causal_role")
    elif has_cause and has_effect:
        causal_role = "causal_summary"
    elif has_cause:
        causal_role = "cause"
    elif has_effect:
        causal_role = "outcome" if "outcome" in keys else "effect"
    elif has_action:
        causal_role = "action"
    elif has_goal:
        causal_role = "goal"
    elif has_constraint:
        causal_role = "constraint"
    elif epistemic_status in {"observed", "verified"} or memory_type in {"episodic", "observation"}:
        causal_role = "observation"
    else:
        causal_role = "context"

    assertion_kind = {
        "preference": "user_preference",
        "rule": "hard_constraint",
        "goal": "goal",
        "relationship": "relationship",
        "skill": "skill",
        "procedural": "skill",
        "causal": "causal_summary",
        "episodic": "observation",
        "observation": "observation",
        "working": "observation",
        "semantic": "observation",
    }[memory_type]

    if memory_type == "working":
        retention_class = "ACTIVE_WORKING"
    elif memory_type == "episodic":
        retention_class = "CHECKPOINT"
    elif memory_type in {"goal", "rule", "preference", "relationship", "procedural", "skill", "causal"}:
        retention_class = "LONG_TERM_MEMORY"
    elif priority >= 3000 or confidence_milli >= 950:
        retention_class = "LONG_TERM_MEMORY"
    else:
        retention_class = "CHECKPOINT"

    causal_refs: set[str] = set()
    for relation in normalized_relations:
        if relation["target_ref"]:
            causal_refs.add(relation["target_ref"])
    if isinstance(content, Mapping):
        for key in (
            "cause_ref",
            "effect_ref",
            "cause_memory_id",
            "effect_memory_id",
            "source_memory_id",
            "target_memory_id",
        ):
            value = str(content.get(key) or "").strip()
            if value and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}", value):
                causal_refs.add(value)

    classification = {
        "schema": CLASSIFIER_VERSION,
        "requested_memory_type": explicit or "auto",
        "memory_type": memory_type,
        "assertion_kind": assertion_kind,
        "causal_role": causal_role,
        "retention_class": retention_class,
        "causal": bool(memory_type == "causal" or causal_relation or has_cause or has_effect),
        "causal_refs": sorted(causal_refs),
        "relation_kinds": sorted(relation_kinds),
        "reason_codes": sorted(set(reason_codes)),
    }
    classification["classification_sha256"] = canonical_sha256(classification)
    return {
        "classification": classification,
        "relations": normalized_relations,
    }


__all__ = [
    "ALLOWED_RELATION_KINDS",
    "CAUSAL_RELATION_KINDS",
    "CLASSIFIER_VERSION",
    "MEMORY_TYPES",
    "classify_memory",
    "normalize_relations",
]
