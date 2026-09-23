"""Lossless strict-JSON tool values inside integer-only Gateway contracts."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from contracts import canonical_json_bytes


SCHEMA = "tiangong.composition-final-result.v1"
_FIELDS = {"schema", "composition_final_output_aliases_json",
           "composition_final_output_aliases_sha256", "composition_final_output_aliases_size_bytes",
           "parent_reply"}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8", errors="strict")


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate composition output key")
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError("non-finite composition output")


def encode_composition_final_result(aliases: dict[str, Any], *, parent_reply: str,
                                    execution_requirements_attestation: dict | None = None) -> str:
    """Bind exact finite floats and all other tool JSON without changing them."""
    if not isinstance(aliases, dict) or not aliases or any(type(key) is not str for key in aliases):
        raise ValueError("composition aliases must be a nonempty object")
    raw = _json_bytes(aliases)
    result = {
        "schema": SCHEMA,
        "composition_final_output_aliases_json": raw.decode("utf-8"),
        "composition_final_output_aliases_sha256": hashlib.sha256(raw).hexdigest(),
        "composition_final_output_aliases_size_bytes": len(raw),
        "parent_reply": parent_reply,
    }
    if execution_requirements_attestation is not None:
        from .composition_task_floor import validate_execution_requirements_attestation_shape
        validate_execution_requirements_attestation_shape(execution_requirements_attestation)
        result["execution_requirements_attestation"] = execution_requirements_attestation
    return canonical_json_bytes(result).decode("utf-8")


def decode_composition_final_aliases(result: dict[str, Any]) -> dict[str, Any]:
    """Read new bound JSON text, or the earlier integer-only stored envelope."""
    if "schema" not in result:
        if set(result) != {"composition_final_output_aliases", "parent_reply"}:
            raise ValueError("legacy composition result fields are invalid")
        aliases = result["composition_final_output_aliases"]
    else:
        if result.get("schema") != SCHEMA or set(result) not in (_FIELDS, _FIELDS | {"execution_requirements_attestation"}):
            raise ValueError("composition result envelope is invalid")
        text = result["composition_final_output_aliases_json"]
        if not isinstance(text, str):
            raise ValueError("composition output JSON text is invalid")
        raw = text.encode("utf-8", errors="strict")
        if (type(result["composition_final_output_aliases_size_bytes"]) is not int
                or result["composition_final_output_aliases_size_bytes"] != len(raw)
                or result["composition_final_output_aliases_sha256"] != hashlib.sha256(raw).hexdigest()):
            raise ValueError("composition output JSON digest/size mismatch")
        aliases = json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
        if _json_bytes(aliases) != raw:
            raise ValueError("composition output JSON is not stable strict JSON")
    if not isinstance(aliases, dict) or not aliases:
        raise ValueError("composition output aliases are invalid")
    return aliases


def decode_composition_requirements_attestation(result: dict[str, Any], *, expected_request_id: str,
        expected_request_text: str, expected_plan_id: str, expected_plan_sha256: str,
        allowed_fact_ids) -> dict[str, Any] | None:
    """Called only after Completion/capsule bind the exact final-result bytes."""
    from contracts import canonical_sha256
    from .composition_task_floor import validate_execution_requirements_attestation_shape
    from v3.execution_integrity import build_action_obligations, required_request_outputs
    # Validate the enclosing result as well; unattached attestations are not a
    # standalone authority channel.
    decode_composition_final_aliases(result)
    value = result.get("execution_requirements_attestation")
    if value is None:
        return None
    validate_execution_requirements_attestation_shape(value)
    obligations = build_action_obligations(expected_request_text)
    if (value["request_id"] != expected_request_id
            or value["request_text_sha256"] != hashlib.sha256(expected_request_text.encode("utf-8")).hexdigest()
            or value["executable_plan_id"] != expected_plan_id
            or value["executable_plan_sha256"] != expected_plan_sha256
            or not set(value["supporting_fact_ids"]).issubset(set(allowed_fact_ids))
            or value["obligations_count"] != len(obligations)
            or value["obligations_sha256"] != canonical_sha256(obligations)
            or value["required_outputs"] != required_request_outputs(expected_request_text)):
        raise ValueError("composition requirements request/plan/fact binding mismatch")
    return value
