"""Conservative request requirements independent of model-selected steps.

This prevents obviously incomplete plans. It is a minimum evidence floor,
not a proof of arbitrary business correctness or full natural-language intent.
"""
from __future__ import annotations

import os
import hashlib
import re
from contracts import canonical_sha256
from runtime_security.composition_path import resolve_composition_path
from pathlib import Path

from contracts.composition_profile import WORKSPACE_WRITE_ACTIONS


def _path(value: str, root: Path) -> str:
    value = str(value).strip().replace("\\", os.sep)
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return os.path.normcase(str(path.resolve(strict=False)))


def validate_request_plan_floor(user_text: str, steps, *, workspace_root: Path) -> None:
    from v3.execution_integrity import action_has_observation_semantics, build_action_obligations
    obligations = build_action_obligations(user_text)
    actions = {step.action_id for step in steps}
    writer_targets = {_path(step.target_skeleton, workspace_root) for step in steps
                      if step.action_id in WORKSPACE_WRITE_ACTIONS and step.target_skeleton}
    for item in obligations:
        kind = item.get("kind")
        target = item.get("target_path")
        if kind == "observation" and not any(action_has_observation_semantics(action) for action in actions):
            raise ValueError("composition.task_floor.observation_missing")
        if kind == "effect":
            if item.get("target_state") == "absent":
                raise ValueError("composition.task_floor.deletion_unsupported")
            if not actions.intersection(WORKSPACE_WRITE_ACTIONS | {"python.run"}):
                raise ValueError("composition.task_floor.write_missing")
            if target and _path(target, workspace_root) not in writer_targets and "python.run" not in actions:
                raise ValueError("composition.task_floor.output_missing")
        if kind == "execution":
            predicate = item.get("evidence_predicate")
            if predicate == "sha256_digest":
                if "file.hash" not in actions:
                    raise ValueError("composition.task_floor.hash_missing")
            elif "python.run" not in actions:
                raise ValueError("composition.task_floor.execution_missing")


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
    from v3.execution_integrity import required_request_outputs
    required = required_request_outputs(user_text)
    if required and workspace_root is None:
        raise ValueError("composition.task_floor.output_workspace_missing")
    witnesses = []
    for target in required:
        try:
            path = resolve_composition_path(workspace_root, target)
            before = path.stat()
            if not path.is_file() or before.st_size > 4194304:
                raise ValueError("output must be a bounded regular file")
            with path.open("rb") as stream:
                content = stream.read(4194305)
            after = path.stat()
            if (len(content) > 4194304 or len(content) != after.st_size
                    or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    or resolve_composition_path(workspace_root, target) != path):
                raise ValueError("output changed during verification")
        except (OSError, ValueError) as exc:
            raise ValueError("composition.task_floor.required_output_unverified:" + target) from exc
        witnesses.append({"requested_path": target, "native_path": str(path), "exists": True,
            "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
            "source": "gateway.native-final-output-probe"})
    return required, witnesses


def validate_request_execution_floor(user_text: str, receipts, *, workspace_root: Path | None = None) -> dict:
    """Use authenticated child Facts plus current system-observed output files.

    Native output probes establish existence/content identity at closeout;
    they do not assert semantic business correctness or create execution Facts.
    """
    from v3.execution_integrity import build_action_obligations, obligation_is_satisfied
    from v3.tool_result_contract import normalize_tool_result
    history = []
    for request, raw in receipts:
        history.append({"ok": raw.get("ok") is True, "tool_action": request.action_id,
            "tool_args": {"target": request.target, "args": request.materialized_arguments},
            "tool_result": raw, "tool_result_contract": normalize_tool_result("omni_body", raw)})
    obligations = build_action_obligations(user_text)
    pending = [item["id"] for item in obligations if not obligation_is_satisfied(item, history)]
    if pending:
        raise ValueError("composition.task_floor.required_evidence_missing:" + ",".join(pending))
    required, witnesses = _required_output_witnesses(user_text, workspace_root)
    return {"obligations_count": len(obligations), "obligations_sha256": canonical_sha256(obligations),
        "required_outputs": required, "output_witnesses": witnesses,
        "execution_requirements_verified": bool(obligations or required), "business_outcome_verified": False}


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
