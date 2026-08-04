"""Authenticated Gateway ingress for life-owned action proposals."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from typing import Any

from contracts import ActionIntent, canonical_json_bytes

from .policy_engine import PolicyEngineError, validate_authorization_source_refs


MAX_LIFE_ACTION_INTENT_BYTES = 256 * 1024


class LifeActionIntentApiError(ValueError):
    def __init__(self, status: int, reason_code: str) -> None:
        super().__init__(reason_code)
        self.status = status
        self.reason_code = reason_code


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LifeActionIntentApiError(400, "life_action_intent.duplicate_json_key")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class LifeActionIntentResponse:
    status_code: int
    payload: dict[str, Any]


class LifeActionIntentApi:
    """Intake is deliberately non-authorizing until PolicyEngine receives full evidence."""

    def __init__(self, token: str) -> None:
        if not 32 <= len(token) <= 512:
            raise ValueError("life action-intent token is invalid")
        self._token = token

    def authorize(self, token: str) -> bool:
        return bool(token) and hmac.compare_digest(
            token.encode("utf-8"), self._token.encode("utf-8")
        )

    def submit(self, body: bytes, *, now_ms: int) -> LifeActionIntentResponse:
        if not body or len(body) > MAX_LIFE_ACTION_INTENT_BYTES:
            raise LifeActionIntentApiError(
                413 if body else 400, "life_action_intent.size_invalid"
            )
        try:
            wire = json.loads(
                body.decode("utf-8", errors="strict"),
                object_pairs_hook=_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    LifeActionIntentApiError(400, "life_action_intent.non_finite_json")
                ),
            )
        except LifeActionIntentApiError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LifeActionIntentApiError(400, "life_action_intent.invalid_json") from exc
        if not isinstance(wire, dict):
            raise LifeActionIntentApiError(400, "life_action_intent.root_invalid")
        if wire.get("schema") == "tiangong.life.action-intent-candidate.v1":
            # Frozen life decisions contain prose plus model-reported risk, not
            # an exact executable action.  Persisting or executing them would
            # recreate the old bypass, so the compatibility bridge is rejected.
            return LifeActionIntentResponse(
                422,
                {
                    "status": "REJECTED",
                    "policy_decision_id": "",
                    "effects_started": False,
                    "execution_ticket_issued": False,
                    "reason_code": "policy.exact_action_intent_required",
                },
            )
        if set(wire) != {"schema", "intent"} or wire.get("schema") != "tiangong.life.action-intent.v2":
            raise LifeActionIntentApiError(400, "life_action_intent.envelope_invalid")
        raw_intent = wire.get("intent")
        if isinstance(raw_intent, dict) and raw_intent.get("source_refs") is not None:
            # D-08 production wiring: a proposal that carries provenance (the
            # contracts-vNext shape) has its authorization sources checked
            # before anything else.  EXTERNAL_DATA / TOOL_DATA may describe
            # where data came from, but the moment they are presented as the
            # intent's authorization provenance the proposal is refused; a
            # model or router can never promote untrusted content into
            # user-grade authority.
            try:
                validate_authorization_source_refs(raw_intent["source_refs"])
            except PolicyEngineError as exc:
                reason = str(exc)
                status = 422 if reason.startswith("policy.provenance_elevation") else 400
                return LifeActionIntentResponse(
                    status,
                    {
                        "status": "REJECTED",
                        "policy_decision_id": "",
                        "effects_started": False,
                        "execution_ticket_issued": False,
                        "reason_code": (
                            reason.split(":", 1)[0]
                            if reason.startswith("policy.provenance_elevation")
                            else "life_action_intent.contract_invalid"
                        ),
                    },
                )
        try:
            # strict=False is deliberate: the globally strict ContractModel
            # cannot materialize tuple fields from JSON arrays under this
            # pydantic version, so a canonical wire intent would never parse
            # at all.  Type coercion is bounded by the contract itself: the
            # model validators recompute source_set/canonical_invocation
            # digests, and has_valid_sha256() below binds the parsed object to
            # its self-hash before any further check runs.
            intent = ActionIntent.model_validate_json(
                canonical_json_bytes(wire["intent"]), strict=False
            )
        except ValueError as exc:
            raise LifeActionIntentApiError(400, "life_action_intent.contract_invalid") from exc
        if (
            intent.source != "life_scheduler"
            or not intent.has_valid_sha256()
            or not intent.created_at_ms <= now_ms <= intent.expires_at_ms
        ):
            raise LifeActionIntentApiError(409, "life_action_intent.stale_or_invalid")
        # D-08 production wiring: the intent's own provenance set is the
        # authorization claim.  EXTERNAL_DATA / TOOL_DATA may describe where
        # data came from, but the moment they stand as authorization sources
        # the proposal is refused; a model or router can never promote
        # untrusted content into user-grade authority.
        try:
            validate_authorization_source_refs(intent.source_refs)
        except PolicyEngineError as exc:
            reason = str(exc)
            return LifeActionIntentResponse(
                422 if reason.startswith("policy.provenance_elevation") else 400,
                {
                    "status": "REJECTED",
                    "policy_decision_id": "",
                    "intent_id": intent.intent_id,
                    "intent_sha256": intent.intent_sha256,
                    "effects_started": False,
                    "execution_ticket_issued": False,
                    "reason_code": (
                        reason.split(":", 1)[0]
                        if reason.startswith("policy.provenance_elevation")
                        else "life_action_intent.contract_invalid"
                    ),
                },
            )
        # P6 closes the bypass.  P7 will supply authoritative viability and
        # causal evidence before this route may call PolicyEngine.  It is safer
        # to reject a valid proposal than to invent missing impact facts.
        return LifeActionIntentResponse(
            202,
            {
                "status": "REJECTED",
                "policy_decision_id": "",
                "intent_id": intent.intent_id,
                "intent_sha256": intent.intent_sha256,
                "effects_started": False,
                "execution_ticket_issued": False,
                "reason_code": "policy.authoritative_impact_evidence_required",
            },
        )


__all__ = [
    "LifeActionIntentApi",
    "LifeActionIntentApiError",
    "LifeActionIntentResponse",
    "MAX_LIFE_ACTION_INTENT_BYTES",
]
