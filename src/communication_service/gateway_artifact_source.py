"""Strict streaming ArtifactContentSource backed by authenticated 7184 egress."""

from __future__ import annotations

import http.client
import json
from typing import Any
from urllib.parse import urlsplit

from contracts import DeliveryPartGrant, canonical_json_bytes


class GatewayArtifactSourceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise GatewayArtifactSourceError("gateway_artifact.response.duplicate_key")
        result[key] = value
    return result


class _GatewayArtifactStream:
    def __init__(
        self,
        response: http.client.HTTPResponse,
        connection: http.client.HTTPConnection,
    ) -> None:
        self._response = response
        self._connection = connection
        self._closed = False

    def read(self, amount: int = -1) -> bytes:
        if self._closed:
            return b""
        return self._response.read(amount)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._response.close()
        finally:
            self._connection.close()


class LoopbackGatewayArtifactSource:
    def __init__(self, gateway_origin: str, token: str, *, port: int | None = None) -> None:
        parsed = urlsplit(gateway_origin)
        resolved_port = parsed.port
        if (
            resolved_port is None
            or not 1 <= resolved_port <= 65_535
            or (port is not None and resolved_port != port)
            or parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or not 32 <= len(token) <= 512
        ):
            raise ValueError("gateway artifact source configuration is invalid")
        self._token = token
        self._port = resolved_port

    def open_artifact(
        self,
        grant: DeliveryPartGrant,
        *,
        timeout_seconds: int,
    ) -> _GatewayArtifactStream:
        if (
            grant.kind != "artifact"
            or grant.size_bytes is None
            or grant.content_sha256 is None
            or not 1 <= timeout_seconds <= 3_600
        ):
            raise GatewayArtifactSourceError("gateway_artifact.request.invalid")
        body = canonical_json_bytes(
            {
                "grant": grant.model_dump(mode="json"),
                "timeout_seconds": timeout_seconds,
            }
        )
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self._port,
            timeout=float(timeout_seconds),
        )
        response: http.client.HTTPResponse | None = None
        try:
            connection.request(
                "POST",
                "/api/v1/internal/channel/artifacts/fetch",
                body=body,
                headers={
                    "Accept": "application/octet-stream",
                    "Cache-Control": "no-store",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "X-Tiangong-Communication-Token": self._token,
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                raw = response.read(65_537)
                code = "gateway_artifact.response.rejected"
                if len(raw) <= 65_536:
                    try:
                        value = json.loads(
                            raw.decode("utf-8", errors="strict"),
                            object_pairs_hook=_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                        )
                        if isinstance(value, dict) and canonical_json_bytes(value) == raw:
                            code = str(value.get("reason_code") or code)[:160]
                    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                        pass
                raise GatewayArtifactSourceError(code)
            content_type = str(response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            raw_length = str(response.getheader("Content-Length") or "")
            if (
                content_type != "application/octet-stream"
                or not raw_length.isascii()
                or not raw_length.isdecimal()
                or int(raw_length) != grant.size_bytes
                or response.getheader("X-Tiangong-Content-SHA256") != grant.content_sha256
                or response.getheader("X-Tiangong-Artifact-Manifest-SHA256")
                != grant.artifact_manifest_sha256
            ):
                raise GatewayArtifactSourceError("gateway_artifact.response.binding_invalid")
            stream = _GatewayArtifactStream(response, connection)
            response = None
            return stream
        except GatewayArtifactSourceError:
            raise
        except Exception as exc:
            raise GatewayArtifactSourceError("gateway_artifact.transport.failed") from exc
        finally:
            if response is not None:
                response.close()
                connection.close()


__all__ = ["GatewayArtifactSourceError", "LoopbackGatewayArtifactSource"]
