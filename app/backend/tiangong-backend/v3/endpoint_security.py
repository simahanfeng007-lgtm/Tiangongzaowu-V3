"""Model endpoint validation and credential binding.

The provider credential and the network endpoint are one security fact. Official
provider keys are only released to official provider origins. A custom OpenAI-
compatible endpoint has an independent credential slot keyed by its canonical
origin, so changing ``base_url`` can never exfiltrate an already stored vendor
key.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import os
import socket
from urllib.parse import urlparse


class EndpointSecurityError(ValueError):
    pass


_OFFICIAL_HOSTS: dict[str, frozenset[str]] = {
    "deepseek_v4": frozenset({"api.deepseek.com"}),
    "deepseek": frozenset({"api.deepseek.com"}),
    "glm_5_2": frozenset({"open.bigmodel.cn"}),
    "glm_5_1": frozenset({"open.bigmodel.cn"}),
    "zhipu": frozenset({"open.bigmodel.cn"}),
    "gpt_5_6": frozenset({"api.openai.com"}),
    "openai": frozenset({"api.openai.com"}),
    "anthropic": frozenset({"api.anthropic.com"}),
    "minimax_m3": frozenset({"api.minimaxi.com"}),
    "minimax": frozenset({"api.minimaxi.com"}),
    "google": frozenset({"generativelanguage.googleapis.com"}),
    # bug-fix: MiMo Token Plan endpoint 支持（2026-08-25）
    "mimo": frozenset({"api.xiaomimimo.com", "token-plan-cn.xiaomimimo.com"}),
}


@dataclass(frozen=True)
class EndpointBinding:
    provider_id: str
    base_url: str
    origin: str
    host: str
    port: int
    official: bool
    custom_scope: str | None
    resolved_ips: tuple[str, ...]


def canonical_origin(base_url: str) -> str:
    parsed = urlparse(str(base_url or "").strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if scheme not in {"https", "http"} or not host:
        raise EndpointSecurityError("endpoint_url_invalid")
    if parsed.username or parsed.password or parsed.fragment:
        raise EndpointSecurityError("endpoint_userinfo_or_fragment_forbidden")
    port = parsed.port or (443 if scheme == "https" else 80)
    default = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    return f"{scheme}://{host}" if default else f"{scheme}://{host}:{port}"


def custom_scope_id(base_url: str) -> str:
    return "endpoint_" + hashlib.sha256(canonical_origin(base_url).encode("utf-8")).hexdigest()


def is_official_endpoint(provider_id: str, base_url: str) -> bool:
    parsed = urlparse(str(base_url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme.lower() == "https" and host in _OFFICIAL_HOSTS.get(str(provider_id), frozenset())


def _is_forbidden_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve(host: str, port: int) -> tuple[str, ...]:
    try:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise EndpointSecurityError("endpoint_dns_resolution_failed") from exc
    addresses = tuple(sorted({str(row[4][0]).split("%", 1)[0] for row in rows if row and row[4]}))
    if not addresses:
        raise EndpointSecurityError("endpoint_dns_resolution_empty")
    return addresses


def validate_model_endpoint(
    provider_id: str,
    base_url: str,
    *,
    resolve_dns: bool = True,
    environ: dict[str, str] | None = None,
) -> EndpointBinding:
    env = environ if environ is not None else os.environ
    value = str(base_url or "").strip().rstrip("/")
    origin = canonical_origin(value)
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    official = is_official_endpoint(provider_id, value)
    if parsed.scheme.lower() != "https":
        allow_local = str(env.get("TIANGONG_ALLOW_LOCAL_MODEL_ENDPOINT") or "").lower() in {"1", "true", "yes", "on"}
        if not allow_local:
            raise EndpointSecurityError("endpoint_https_required")
    addresses: tuple[str, ...] = ()
    if resolve_dns:
        addresses = _resolve(host, port)
        if any(_is_forbidden_address(item) for item in addresses):
            allow_local = str(env.get("TIANGONG_ALLOW_LOCAL_MODEL_ENDPOINT") or "").lower() in {"1", "true", "yes", "on"}
            # Local/private endpoints are a deliberate no-vendor-key mode only.
            if not allow_local or official:
                raise EndpointSecurityError("endpoint_private_or_local_address_forbidden")
    return EndpointBinding(
        provider_id=str(provider_id),
        base_url=value,
        origin=origin,
        host=host,
        port=port,
        official=official,
        custom_scope=None if official else custom_scope_id(value),
        resolved_ips=addresses,
    )
