"""Authenticated loopback API for the single Gateway Skill authority."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .skill_authority import SkillAuthority, SkillAuthorityError
from .store import StoreError


MAX_SKILL_API_REQUEST_BYTES = 65_536
_PATH_OPERATIONS = {
    "/api/v1/internal/skills/route": "skill.route",
    "/api/v1/internal/skills/list": "skill.list",
    "/api/v1/internal/skills/get": "skill.get",
    "/api/v1/internal/skills/read": "skill.read",
    "/api/v1/internal/skills/step-check": "skill.step.check",
}


class SkillApiError(RuntimeError):
    def __init__(self, status: int, reason_code: str) -> None:
        super().__init__(reason_code)
        self.status = status
        self.reason_code = reason_code


class SkillApiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    request_id: str = Field(pattern=r"^req_[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run_[0-9a-f]{64}$")
    generation: int = Field(ge=0)
    principal_scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    query: str | None = Field(default=None, min_length=1, max_length=16_384)
    skill_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$"
    )
    decline: bool = False
    limit: int = Field(default=32, ge=1, le=32)
    skill_activation_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def validate_fields(self) -> "SkillApiRequest":
        if self.decline and self.skill_id is not None:
            raise ValueError("declined Skill request cannot identify a Skill")
        return self


@dataclass(frozen=True)
class SkillApiResponse:
    status: int
    payload: dict[str, Any]


def _reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SkillApiError(400, "skill_api.json.duplicate_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise SkillApiError(400, "skill_api.json.non_finite")


class SkillInternalApiRouter:
    def __init__(self, authority: SkillAuthority, token: str) -> None:
        if len(token) < 32:
            raise ValueError("Skill API token is missing or weak")
        self.authority = authority
        self._token = token

    @staticmethod
    def handles_path(path: str) -> bool:
        return path in _PATH_OPERATIONS

    def authorize(self, token: str) -> bool:
        return bool(token) and hmac.compare_digest(self._token, token)

    @staticmethod
    def _decode(body: bytes) -> SkillApiRequest:
        if not body or len(body) > MAX_SKILL_API_REQUEST_BYTES:
            raise SkillApiError(413, "skill_api.request.size_invalid")
        try:
            value = json.loads(
                body.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_pairs,
                parse_constant=_reject_constant,
            )
            return SkillApiRequest.model_validate(value, strict=True)
        except SkillApiError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SkillApiError(400, "skill_api.request.invalid") from exc

    @staticmethod
    def _validate_operation(operation: str, request: SkillApiRequest) -> None:
        if operation == "skill.route":
            if request.skill_id is not None or request.query is None:
                raise SkillApiError(400, "skill_api.route.shape_invalid")
        elif operation == "skill.list":
            if request.skill_id is not None or request.skill_activation_sha256 is not None:
                raise SkillApiError(400, "skill_api.list.shape_invalid")
        elif operation in {"skill.get", "skill.read"}:
            if request.query is not None or request.skill_id is None or request.decline:
                raise SkillApiError(400, "skill_api.resolve.shape_invalid")
        elif operation == "skill.step.check":
            if (
                request.query is not None
                or request.skill_id is None
                or request.decline
                or request.skill_activation_sha256 is None
            ):
                raise SkillApiError(400, "skill_api.step_check.shape_invalid")

    def dispatch(
        self,
        method: str,
        path: str,
        content_type: str,
        body: bytes,
        *,
        now_ms: int,
    ) -> SkillApiResponse:
        if method != "POST":
            raise SkillApiError(405, "skill_api.method_not_allowed")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise SkillApiError(415, "skill_api.content_type_invalid")
        operation = _PATH_OPERATIONS.get(path)
        if operation is None:
            raise SkillApiError(404, "skill_api.route_not_found")
        request = self._decode(body)
        self._validate_operation(operation, request)
        try:
            if operation == "skill.step.check":
                assert request.skill_id is not None
                assert request.skill_activation_sha256 is not None
                status = self.authority.step_check(
                    request_id=request.request_id,
                    run_id=request.run_id,
                    generation=request.generation,
                    principal_scope_hash=request.principal_scope_hash,
                    skill_id=request.skill_id,
                    activation_sha256=request.skill_activation_sha256,
                    checked_at_ms=now_ms,
                )
                return SkillApiResponse(
                    200,
                    {
                        "status": "OK",
                        "operation": operation,
                        "catalog_sha256": self.authority.catalog_sha256,
                        "capability_manifest_sha256": self.authority.capability_manifest.sha256,
                        "step": status.as_dict(),
                    },
                )
            resolved = self.authority.model_request(
                operation,
                request_id=request.request_id,
                run_id=request.run_id,
                generation=request.generation,
                principal_scope_hash=request.principal_scope_hash,
                decided_at_ms=now_ms,
                query=request.query,
                skill_id=request.skill_id,
                decline=request.decline,
                limit=request.limit,
            )
        except (SkillAuthorityError, StoreError, ValueError) as exc:
            raise SkillApiError(409, "skill_api.authority_rejected") from exc
        return SkillApiResponse(
            200,
            {
                "status": "OK",
                "operation": operation,
                "catalog_sha256": self.authority.catalog_sha256,
                "capability_manifest_sha256": self.authority.capability_manifest.sha256,
                "selection_record_sha256": resolved.selection.record_sha256,
                "selection": resolved.resolution.record.model_dump(mode="json"),
                "content": resolved.resolution.content,
                "activation": (
                    None
                    if resolved.activation is None
                    else resolved.activation.model_dump(mode="json")
                ),
            },
        )


__all__ = [
    "MAX_SKILL_API_REQUEST_BYTES",
    "SkillApiError",
    "SkillApiRequest",
    "SkillApiResponse",
    "SkillInternalApiRouter",
]
