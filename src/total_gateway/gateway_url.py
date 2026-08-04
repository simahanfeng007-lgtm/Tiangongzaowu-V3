"""Canonical loopback URL used for Gateway-to-backend authority callbacks."""

from __future__ import annotations

import os
import re
import urllib.parse
from typing import Mapping

from . import DEFAULT_PORT


DEFAULT_GATEWAY_URL = f"http://127.0.0.1:{DEFAULT_PORT}"


class GatewayUrlError(ValueError):
    pass


def normalize_gateway_url(
    value: str | None,
    *,
    expected_port: int | None = None,
) -> str:
    raw = str(value or "").strip()
    if not raw:
        port = DEFAULT_PORT if expected_port is None else expected_port
        raw = f"http://127.0.0.1:{port}"
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise GatewayUrlError("gateway_url.port_invalid") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1 <= port <= 65_535
    ):
        raise GatewayUrlError("gateway_url.loopback_http_origin_required")
    if expected_port is not None and port != expected_port:
        raise GatewayUrlError("gateway_url.port_mismatch")
    return f"http://127.0.0.1:{port}"


def gateway_url_from_environment(
    environ: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if environ is None else environ
    raw_port = str(source.get("TIANGONG_GATEWAY_PORT") or DEFAULT_PORT).strip()
    if raw_port == "0":
        # Port zero is a test-only bind request whose real port is unknown
        # until the HTTP server exists. Tests that exercise callbacks supply
        # TIANGONG_GATEWAY_URL explicitly; other ephemeral tests keep the
        # inert production default.
        return normalize_gateway_url(
            source.get("TIANGONG_GATEWAY_URL") or DEFAULT_GATEWAY_URL
        )
    if re.fullmatch(r"[1-9][0-9]{0,4}", raw_port) is None:
        raise GatewayUrlError("gateway_url.environment_port_invalid")
    port = int(raw_port)
    if port > 65_535:
        raise GatewayUrlError("gateway_url.environment_port_invalid")
    return normalize_gateway_url(
        source.get("TIANGONG_GATEWAY_URL"),
        expected_port=port,
    )


__all__ = [
    "DEFAULT_GATEWAY_URL",
    "GatewayUrlError",
    "gateway_url_from_environment",
    "normalize_gateway_url",
]
