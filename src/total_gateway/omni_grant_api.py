"""Loopback-only API for one-shot Omni capability grants."""
from __future__ import annotations

import hmac
import json
from typing import Any

from .omni_grant_authority import OmniGrantAuthority, OmniGrantAuthorityError


MAX_OMNI_GRANT_REQUEST_BYTES = 2 * 1024 * 1024
OMNI_GRANT_PATH = "/api/v1/internal/omni/grant"


class OmniGrantApiError(RuntimeError):
    def __init__(self, status: int, reason_code: str) -> None:
        super().__init__(reason_code)
        self.status = status
        self.reason_code = reason_code


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OmniGrantApiError(400, "omni_grant_api.duplicate_json_key")
        result[key] = value
    return result


class OmniGrantInternalApiRouter:
    def __init__(self, authority: OmniGrantAuthority, token: str) -> None:
        if len(token) < 32:
            raise ValueError("Omni grant API token is invalid")
        self.authority = authority
        self._token = token

    @staticmethod
    def handles_path(path: str) -> bool:
        return path.split("?", 1)[0] == OMNI_GRANT_PATH

    def authorize(self, provided: str) -> bool:
        return bool(provided) and hmac.compare_digest(
            self._token.encode("utf-8"), provided.encode("utf-8")
        )

    def dispatch(self, method: str, path: str, body: bytes) -> tuple[int, dict[str, Any]]:
        if method != "POST" or path.split("?", 1)[0] != OMNI_GRANT_PATH:
            raise OmniGrantApiError(405, "omni_grant_api.method_not_allowed")
        if not body or len(body) > MAX_OMNI_GRANT_REQUEST_BYTES:
            raise OmniGrantApiError(413, "omni_grant_api.request_size_invalid")
        try:
            payload = json.loads(
                body.decode("utf-8", errors="strict"),
                object_pairs_hook=_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    OmniGrantApiError(400, "omni_grant_api.non_finite_number")
                ),
            )
        except OmniGrantApiError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OmniGrantApiError(400, "omni_grant_api.invalid_json") from exc
        if not isinstance(payload, dict):
            raise OmniGrantApiError(400, "omni_grant_api.object_required")
        try:
            return 200, self.authority.issue(payload)
        except OmniGrantAuthorityError as exc:
            raise OmniGrantApiError(exc.status, exc.code) from exc


__all__ = [
    "MAX_OMNI_GRANT_REQUEST_BYTES",
    "OMNI_GRANT_PATH",
    "OmniGrantApiError",
    "OmniGrantInternalApiRouter",
]
