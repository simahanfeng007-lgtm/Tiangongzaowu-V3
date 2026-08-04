"""In-process service ports used by the single-port Tiangong application.

The public HTTP boundary remains 7184.  These ports preserve each subsystem's
contract while removing loopback service calls and version drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ServiceResponse:
    status: int
    payload: dict[str, Any]
    content_type: str = "application/json; charset=utf-8"


@runtime_checkable
class JsonServicePort(Protocol):
    def request(
        self,
        method: str,
        target: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> tuple[int, dict[str, Any], str]: ...

    def health_payload(self) -> dict[str, Any]: ...

    def ready_payload(self, *, now_ms: int | None = None) -> tuple[int, dict[str, Any]]: ...

    def close(self) -> None: ...


__all__ = ["JsonServicePort", "ServiceResponse"]

class CompatibilityJsonClient:
    """Adapter from an in-process service port to the frozen-client contract."""

    def __init__(self, service: JsonServicePort) -> None:
        self._service = service

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None,
        *,
        timeout_seconds: float,
        backend_started: bool = False,
        before_request=None,
    ) -> tuple[int, dict[str, Any], str]:
        del backend_started
        if before_request is not None:
            import time

            before_request(time.time_ns() // 1_000_000)
        status, value, _content_type = self._service.request(
            method,
            path,
            payload,
            timeout_seconds=timeout_seconds,
        )
        import hashlib
        import json

        raw = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return status, value, hashlib.sha256(raw).hexdigest()


__all__.append("CompatibilityJsonClient")
