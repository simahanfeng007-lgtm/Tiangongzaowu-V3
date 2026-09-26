"""Structured step-order and historical attestation validation.

Task prose creates no plan floor or output requirement.
"""
from __future__ import annotations

import os
import hashlib
import re
from contracts import canonical_sha256
from pathlib import Path

from contracts.composition_profile import WORKSPACE_WRITE_ACTIONS


def _path(value: str, root: Path) -> str:
    value = str(value).strip().replace("\\", os.sep)
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return os.path.normcase(str(path.resolve(strict=False)))


def validate_request_plan_floor(user_text: str, steps, *, workspace_root: Path) -> None:
    """Compatibility API: task prose does not create mandatory plan steps."""
    return None


def validate_workspace_step_order(steps, *, workspace_root: Path) -> None:
    """Conflicting workspace operations require a sealed DAG dependency."""
    ancestors = {}
    earlier = []
    for step in steps:
        closure = set(step.depends_on)
        for dependency in step.depends_on:
            closure.update(ancestors.get(dependency, ()))
        ancestors[step.step_id] = closure
        if step.execution_profile_id is None or not step.target_skeleton:
            continue
        target = _path(step.target_skeleton, workspace_root)
        for previous, previous_target in earlier:
            mutates = step.action_id in WORKSPACE_WRITE_ACTIONS | {"python.run"} or previous.action_id in WORKSPACE_WRITE_ACTIONS | {"python.run"}
            overlap = (target == previous_target or target.startswith(previous_target + os.sep)
                       or previous_target.startswith(target + os.sep)
                       or step.action_id == "python.run" or previous.action_id == "python.run")
            if mutates and overlap and previous.step_id not in closure:
                raise ValueError("composition.task_floor.target_dependency_missing")
        earlier.append((step, target))


def _required_output_witnesses(user_text: str, workspace_root: Path | None) -> tuple[list[str], list[dict]]:
    """No file obligation is inferred from a path mentioned in prose."""
    return [], []


def validate_request_execution_floor(user_text: str, receipts, *, workspace_root: Path | None = None) -> dict:
    """Do not manufacture task acceptance requirements from user prose.

    Actual child execution, artifacts and plan-bound verification are checked
    by their existing structured pipelines. This retired prose floor cannot
    claim that it verified a user's business outcome.
    """
    return {"obligations_count": 0, "obligations_sha256": canonical_sha256([]),
        "required_outputs": [], "output_witnesses": [],
        "execution_requirements_verified": False, "business_outcome_verified": False}


_ATTESTATION_SCHEMA = "tiangong.composition-execution-requirements.v1"
_ATTESTATION_FIELDS = {"schema", "request_id", "request_text_sha256", "executable_plan_id", "executable_plan_sha256",
    "supporting_fact_ids", "execution_completed_at_ms", "obligations_count", "obligations_sha256", "required_outputs",
    "output_witnesses", "execution_requirements_verified", "business_outcome_verified", "sha256"}


def seal_execution_requirements_attestation(evidence: dict, *, request_id: str, user_text: str,
        executable_plan_id: str, executable_plan_sha256: str, supporting_fact_ids, execution_completed_at_ms: int) -> dict:
    value = {**evidence, "schema": _ATTESTATION_SCHEMA, "request_id": request_id,
        "request_text_sha256": hashlib.sha256(user_text.encode("utf-8")).hexdigest(),
        "executable_plan_id": executable_plan_id, "executable_plan_sha256": executable_plan_sha256,
        "supporting_fact_ids": list(supporting_fact_ids), "execution_completed_at_ms": execution_completed_at_ms}
    value["sha256"] = canonical_sha256(value)
    validate_execution_requirements_attestation_shape(value)
    return value


def validate_execution_requirements_attestation_shape(value: dict) -> None:
    if (not isinstance(value, dict) or set(value) != _ATTESTATION_FIELDS or value.get("schema") != _ATTESTATION_SCHEMA
            or value.get("business_outcome_verified") is not False
            or type(value.get("execution_requirements_verified")) is not bool
            or type(value.get("obligations_count")) is not int or value["obligations_count"] < 0
            or type(value.get("execution_completed_at_ms")) is not int or value["execution_completed_at_ms"] < 0
            or any(not isinstance(value.get(key), str) or not value[key] for key in ("request_id", "executable_plan_id"))
            or any(not isinstance(value.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", value[key])
                   for key in ("sha256", "request_text_sha256", "executable_plan_sha256", "obligations_sha256"))
            or value["sha256"] != canonical_sha256({key: item for key, item in value.items() if key != "sha256"})):
        raise ValueError("composition requirements attestation is invalid")
    facts, required, witnesses = value["supporting_fact_ids"], value["required_outputs"], value["output_witnesses"]
    if (not isinstance(facts, list) or not facts or any(not isinstance(item, str) or not item for item in facts)
            or len(facts) != len(set(facts)) or not isinstance(required, list)
            or any(not isinstance(item, str) or not item for item in required) or len(required) != len(set(required))
            or not isinstance(witnesses, list) or len(witnesses) != len(required)
            or value["execution_requirements_verified"] is not bool(value["obligations_count"] or required)):
        raise ValueError("composition requirements evidence is invalid")
    for target, witness in zip(required, witnesses, strict=True):
        if (not isinstance(witness, dict)
                or set(witness) != {"requested_path", "native_path", "exists", "size_bytes", "sha256", "source"}
                or witness["requested_path"] != target or witness["exists"] is not True
                or witness["source"] != "gateway.native-final-output-probe"
                or not isinstance(witness["native_path"], str) or not Path(witness["native_path"]).is_absolute()
                or type(witness["size_bytes"]) is not int or not 0 <= witness["size_bytes"] <= 4194304
                or not isinstance(witness["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", witness["sha256"])):
            raise ValueError("composition requirements output witness is invalid")
