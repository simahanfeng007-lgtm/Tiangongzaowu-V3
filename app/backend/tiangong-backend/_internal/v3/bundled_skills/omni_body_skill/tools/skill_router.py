"""Thin Omni client for the sole Skill authority on total-gateway port 7184."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict


SKILL_ROUTER_ACTIONS: Dict[str, Dict[str, Any]] = {
    "skill.route": {
        "risk": "A0",
        "implemented": True,
        "summary": "Ask the Gateway authority to route a task against its pinned Skill catalog.",
    },
    "skill.get": {
        "risk": "A0",
        "implemented": True,
        "summary": "Resolve and activate one exact Skill through the Gateway authority.",
    },
    "skill.read": {
        "risk": "A0",
        "implemented": True,
        "summary": "Resolve and activate one exact Skill through the Gateway authority.",
    },
    "skill.list": {
        "risk": "A0",
        "implemented": True,
        "summary": "List the Gateway authority's pinned Skill catalog.",
    },
    "skill.step.check": {
        "risk": "A0",
        "implemented": True,
        "summary": "Read workflow progress computed by Gateway from machine facts.",
    },
    "skill.progress.report": {
        "risk": "A0",
        "implemented": True,
        "summary": "Alias of fact-derived Skill step status; model claims are ignored.",
    },
}


class SkillGatewayError(RuntimeError):
    pass


def _gateway_origin(runtime: Any) -> str:
    configured = str(
        getattr(getattr(runtime, "config", None), "gateway_url", "")
        or os.environ.get("TIANGONG_TOTAL_GATEWAY_URL")
        or os.environ.get("TIANGONG_GATEWAY_URL")
        or "http://127.0.0.1:7184"
    ).rstrip("/")
    parsed = urllib.parse.urlsplit(configured)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 7184
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise SkillGatewayError("Skill authority origin must be exact loopback port 7184")
    return configured


def _gateway_token(runtime: Any) -> str:
    token = str(
        getattr(getattr(runtime, "config", None), "gateway_token", "")
        or os.environ.get("TIANGONG_BACKEND_INTERNAL_TOKEN")
        or ""
    )
    if len(token) < 32:
        raise SkillGatewayError("Skill authority credential is unavailable")
    return token


def _authority_scope(runtime: Any) -> dict[str, Any]:
    config = getattr(runtime, "config", None)
    request_id = str(getattr(config, "request_id", "") or "")
    run_id = str(getattr(config, "run_id", "") or "")
    generation = getattr(config, "generation", None)
    principal_scope_hash = str(getattr(config, "principal_scope_hash", "") or "")
    if (
        not request_id.startswith("req_")
        or len(request_id) != 68
        or not run_id.startswith("run_")
        or len(run_id) != 68
        or type(generation) is not int
        or generation < 0
        or len(principal_scope_hash) != 64
    ):
        raise SkillGatewayError("Skill request lacks execution-bound authority scope")
    return {
        "request_id": request_id,
        "run_id": run_id,
        "generation": generation,
        "principal_scope_hash": principal_scope_hash,
    }


def _request_gateway(runtime: Any, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    suffix = {
        "skill.route": "route",
        "skill.list": "list",
        "skill.get": "get",
        "skill.read": "read",
        "skill.step.check": "step-check",
    }.get(operation)
    if suffix is None:
        raise SkillGatewayError("unsupported Skill authority operation")
    body = json.dumps(
        {**_authority_scope(runtime), **payload},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{_gateway_origin(runtime)}/api/v1/internal/skills/{suffix}",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "X-Tiangong-Token": _gateway_token(runtime),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if response.status != 200 or len(raw) > 2 * 1024 * 1024:
                raise SkillGatewayError("Skill authority response is invalid")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise SkillGatewayError("Skill authority is unavailable") from exc
    try:
        decoded = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillGatewayError("Skill authority returned invalid JSON") from exc
    if not isinstance(decoded, dict) or decoded.get("status") != "OK":
        raise SkillGatewayError("Skill authority rejected the request")
    return decoded


def _candidate_card(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": candidate.get("skill_id"),
        "version": candidate.get("version"),
        "sha256": candidate.get("sha256"),
        "source_ref": candidate.get("source_ref"),
        "score_millis": candidate.get("score_millis"),
        "required_actions": list(candidate.get("required_actions") or []),
        "missing_actions": list(candidate.get("missing_actions") or []),
        "compatible": candidate.get("compatible") is True,
        "incompatible_reasons": list(candidate.get("incompatible_reasons") or []),
    }


def _failure(action: str, target: str | None, exc: Exception) -> dict[str, Any]:
    return {
        "success": False,
        "action": action,
        "target": target,
        "message": str(exc),
        "evidence": {"authority": "tiangong-total-gateway", "verified": False},
    }


def _skill_route(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(
        args.get("job")
        or args.get("task")
        or args.get("message")
        or args.get("goal")
        or target
        or ""
    ).strip()
    if not query:
        return _failure("skill.route", target, SkillGatewayError("skill.route requires a query"))
    try:
        response = _request_gateway(
            runtime,
            "skill.route",
            {"query": query, "limit": int(args.get("limit", 8)), "decline": bool(args.get("decline", False))},
        )
        selection = dict(response.get("selection") or {})
        candidates = [_candidate_card(dict(item)) for item in list(selection.get("candidates") or [])]
        recommended = next((item for item in candidates if item["compatible"]), None)
        return {
            "success": True,
            "action": "skill.route",
            "target": target,
            "result": {
                "decision": selection.get("decision"),
                "recommended_skill": recommended,
                "skill_card": recommended,
                "candidates": candidates,
                "selection": selection,
                "next_model_action": {
                    "call": "skill.get" if recommended else "skill.list",
                    "skill_id": None if recommended is None else recommended["id"],
                },
            },
            "evidence": {
                "authority": "tiangong-total-gateway",
                "catalog_sha256": response.get("catalog_sha256"),
                "selection_record_sha256": response.get("selection_record_sha256"),
                "verified": True,
            },
        }
    except Exception as exc:
        return _failure("skill.route", target, exc)


def _skill_list(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    payload: dict[str, Any] = {"limit": int(args.get("limit", 32)), "decline": bool(args.get("decline", False))}
    if args.get("query") is not None:
        payload["query"] = str(args["query"])
    try:
        response = _request_gateway(runtime, "skill.list", payload)
        selection = dict(response.get("selection") or {})
        return {
            "success": True,
            "action": "skill.list",
            "target": target,
            "result": {
                "items": [_candidate_card(dict(item)) for item in list(selection.get("candidates") or [])],
                "selection": selection,
            },
            "evidence": {
                "authority": "tiangong-total-gateway",
                "catalog_sha256": response.get("catalog_sha256"),
                "selection_record_sha256": response.get("selection_record_sha256"),
                "verified": True,
            },
        }
    except Exception as exc:
        return _failure("skill.list", target, exc)


def _skill_get(
    runtime: Any,
    target: str | None,
    args: Dict[str, Any],
    *,
    operation: str = "skill.get",
) -> Dict[str, Any]:
    skill_id = str(args.get("skill_id") or target or "").strip()
    if not skill_id:
        return _failure(operation, target, SkillGatewayError(f"{operation} requires skill_id"))
    try:
        response = _request_gateway(runtime, operation, {"skill_id": skill_id})
        selection = dict(response.get("selection") or {})
        active = selection.get("decision") == "activate" and response.get("activation") is not None
        return {
            "success": active,
            "action": operation,
            "target": skill_id,
            "result": {
                "markdown": response.get("content"),
                "selection": selection,
                "activation": response.get("activation"),
            },
            "activation": response.get("activation"),
            "evidence": {
                "authority": "tiangong-total-gateway",
                "catalog_sha256": response.get("catalog_sha256"),
                "selection_record_sha256": response.get("selection_record_sha256"),
                "exists": response.get("content") is not None,
                "verified": active,
            },
        }
    except Exception as exc:
        return _failure(operation, skill_id, exc)


def _skill_step_check(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    skill_id = str(args.get("skill_id") or target or "").strip()
    activation_sha256 = str(
        getattr(getattr(runtime, "config", None), "skill_activation_sha256", "") or ""
    )
    if not skill_id or len(activation_sha256) != 64:
        return _failure(
            "skill.step.check",
            target,
            SkillGatewayError("skill.step.check requires an execution-bound Skill activation"),
        )
    try:
        # completed_actions, last_qc, artifacts, and other model claims are
        # deliberately ignored.  Gateway derives progress only from facts.
        response = _request_gateway(
            runtime,
            "skill.step.check",
            {"skill_id": skill_id, "skill_activation_sha256": activation_sha256},
        )
        return {
            "success": True,
            "action": "skill.step.check",
            "target": skill_id,
            "result": dict(response.get("step") or {}),
            "evidence": {
                "authority": "tiangong-total-gateway",
                "catalog_sha256": response.get("catalog_sha256"),
                "verified": True,
            },
        }
    except Exception as exc:
        return _failure("skill.step.check", skill_id, exc)


def _skill_progress_report(runtime: Any, target: str | None, args: Dict[str, Any]) -> Dict[str, Any]:
    result = _skill_step_check(runtime, target, args)
    result["action"] = "skill.progress.report"
    return result


def handle_skill_router_action(
    runtime: Any,
    op_id: str,
    action: str,
    target: str | None,
    args: Dict[str, Any],
) -> Dict[str, Any]:
    if action == "skill.route":
        result = _skill_route(runtime, target, args)
    elif action == "skill.list":
        result = _skill_list(runtime, target, args)
    elif action == "skill.get":
        result = _skill_get(runtime, target, args, operation="skill.get")
    elif action == "skill.read":
        result = _skill_get(runtime, target, args, operation="skill.read")
    elif action == "skill.step.check":
        result = _skill_step_check(runtime, target, args)
    elif action == "skill.progress.report":
        result = _skill_progress_report(runtime, target, args)
    else:
        result = _failure(action, target, SkillGatewayError("Skill router action is unsupported"))
    result.setdefault("op_id", op_id)
    return result


__all__ = [
    "SKILL_ROUTER_ACTIONS",
    "SkillGatewayError",
    "handle_skill_router_action",
]
