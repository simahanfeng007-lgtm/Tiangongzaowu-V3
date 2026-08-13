from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Protocol

from .execution_integrity import decide_task_contract_completion
from .run_context import current_run_context
from .tool_result_contract import normalize_tool_result


class EvidenceCheckPort(Protocol):
    def __call__(
        self,
        user_message: str,
        quality_history: list[dict[str, object]],
        generated_attachments: list[dict[str, str]],
        *,
        required_read_paths: list[str] | None = None,
        final_reply: object = None,
        task_obligations: list[dict[str, object]] | None = None,
    ) -> tuple[bool, str, list[str]]: ...


def project_tool_dispatch(
    meta: dict[str, object] | None,
    result: object,
) -> dict[str, object] | None:
    """Project a raw tool outcome into the existing UI/tool-dispatch shape."""
    if not isinstance(meta, dict):
        return None
    output = dict(meta)
    tool_name = str(output.get("toolName") or output.get("tool_name") or "")
    contract = normalize_tool_result(tool_name, result)
    ok = bool(contract.get("ok"))
    output["status"] = "done" if ok else "failed"
    output["resultStatus"] = str(contract.get("status") or "")
    output["resultContract"] = contract
    if not ok:
        output["resultSummary"] = str(contract.get("error") or contract.get("summary") or "")[:500]
    return output


def attach_tool_result_contract(
    tool_name: str,
    result: object,
    *,
    source_native_id: str = "",
) -> object:
    """Attach the canonical v3 ToolResult contract and publish one post-commit fact."""
    contract = normalize_tool_result(tool_name, result)
    if isinstance(result, dict):
        output: object = dict(result)
        output.setdefault("tool_result_contract", contract)
    else:
        output = {
            "ok": bool(contract.get("ok")),
            "zhuangtai": str(contract.get("status") or ""),
            "value": result,
            "tool_result_contract": contract,
        }

    native_id = str(source_native_id or "").strip() or (
        "tool.result."
        + hashlib.sha256(
            json.dumps(
                {"tool_name": tool_name, "contract": contract},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:32]
    )

    # Keep World Understanding publication downstream of the canonical
    # ToolResult contract.  This module does not execute tools and cannot
    # manufacture a successful observation.
    from world_understanding.post_commit import NativePostCommitEvent, notify_native_post_commit

    run_context = current_run_context()
    causal: dict[str, object] = {}
    if run_context.source_inquiry_id:
        causal = {
            "source_inquiry_id": run_context.source_inquiry_id,
            "autonomous_intent_id": run_context.autonomous_intent_id,
            "gateway_intent_id": run_context.outer_execution_ticket_id,
            "terminal_status": "success" if bool(contract.get("ok")) else "failure",
        }
    notify_native_post_commit(
        NativePostCommitEvent(
            source_kind="TOOL_RESULT",
            source_native_id=native_id,
            producer_ref="v3.tool_result_contract",
            payload={"tool_name": str(tool_name or ""), **contract, **causal},
            occurred_at_ms=int(time.time() * 1000),
        )
    )
    return output


def contract_observed_write(contract: dict[str, object] | None) -> bool:
    """Accept only authoritative observed write evidence for new contracts.

    The legacy write_effect fallback is intentionally retained only for durable
    checkpoints created before write_evidence.v1, matching the historical V3
    resume contract.
    """
    if not isinstance(contract, dict):
        return False
    if "observed_write_effect" in contract or "write_evidence" in contract:
        evidence = contract.get("write_evidence")
        return bool(
            contract.get("observed_write_effect")
            and isinstance(evidence, dict)
            and evidence.get("authoritative") is True
            and (
                evidence.get("changed_files")
                or evidence.get("deleted_files")
                or evidence.get("verified_unchanged_files")
            )
        )
    return bool(contract.get("write_effect"))


def tool_write_verified(tool_name: str, result: object) -> bool:
    """Verify the write completion signal without becoming a second executor."""
    contract = normalize_tool_result(tool_name, result)
    if not contract.get("ok"):
        return False
    evidence = contract.get("write_evidence")
    if contract_observed_write(contract):
        if not isinstance(evidence, dict) or evidence.get("authoritative") is not True:
            return False
        if not (
            evidence.get("changed_files")
            or evidence.get("deleted_files")
            or evidence.get("verified_unchanged_files")
        ):
            return False
        if not isinstance(result, dict):
            return True
        readback = result.get("readback")
        if isinstance(readback, dict):
            return readback.get("ok") is True
        if isinstance(readback, list):
            return bool(readback) and all(
                isinstance(item, dict) and item.get("ok") is True
                for item in readback
            )
        result_evidence = result.get("evidence")
        if isinstance(result_evidence, dict) and result_evidence.get("exists") is True:
            return True
        return True

    # Historical B4 fallback: a successful write-class quality path may prove
    # its output by an existing file when older contract evidence is absent.
    try:
        paths = [
            str(path)
            for path in (contract.get("paths") or [])
            if str(path or "").strip()
        ]
        if paths and all(Path(path).is_file() for path in paths):
            return True
    except Exception:
        pass
    return False


def decide_simple_chain_completion(
    user_message: str,
    quality_history: list[dict[str, object]],
    generated_attachments: list[dict[str, str]],
    *,
    task_contract: dict[str, object] | None,
    evidence_check: EvidenceCheckPort,
    required_read_paths: list[str] | None = None,
    final_reply: object = None,
    task_obligations: list[dict[str, object]] | None = None,
) -> tuple[dict[str, object], bool, str, list[str]]:
    """Apply evidence observations, then delegate the single terminal decision.

    Semantic/task terminal authority remains in execution_integrity's existing
    decide_task_contract_completion().  This boundary only connects the
    evidence port to that authority; it does not create another completion
    engine.
    """
    _evidence_ok, evidence_status, evidence_reasons = evidence_check(
        user_message,
        quality_history,
        generated_attachments,
        required_read_paths=required_read_paths,
        final_reply=final_reply,
        task_obligations=task_obligations,
    )
    return decide_task_contract_completion(
        task_contract,
        evidence_reasons=evidence_reasons,
        evidence_status=evidence_status,
        final_reply=final_reply,
        has_real_observation=bool(quality_history),
    )
