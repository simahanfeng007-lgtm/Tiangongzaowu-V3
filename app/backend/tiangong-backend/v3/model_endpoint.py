"""P18.1 model endpoint authority.

This module owns *connection selection only*.  It deliberately keeps four
identities separate:

service_preset -> UI/defaults
provider_identity -> persisted user identity
protocol_family -> wire protocol
optimization_family -> L4 advisory family

The optimization family is always derived last and can never choose the URL,
model, credential scope, or protocol.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Mapping


MODEL_ENDPOINT_SCHEMA = "tiangong.v3.model_endpoint_config.v1"


class ProtocolFamily(str, Enum):
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"


PROTOCOL_FAMILIES = tuple(item.value for item in ProtocolFamily)


@dataclass(frozen=True, slots=True)
class ServicePreset:
    preset_id: str
    provider_identity: str
    default_protocol: str
    base_urls: Mapping[str, str]
    default_model: str = ""
    endpoint_overrides: Mapping[str, Any] = field(default_factory=dict)


SERVICE_PRESETS: dict[str, ServicePreset] = {
    "openai": ServicePreset(
        "openai", "openai", ProtocolFamily.OPENAI_RESPONSES.value,
        {
            ProtocolFamily.OPENAI_RESPONSES.value: "https://api.openai.com/v1",
            ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value: "https://api.openai.com/v1",
        },
        "gpt-5.6",
    ),
    "deepseek": ServicePreset(
        "deepseek", "deepseek", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
        {ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value: "https://api.deepseek.com/v1"},
        "deepseek-v4-pro",
    ),
    "zhipu": ServicePreset(
        "zhipu", "zhipu", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
        {ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value: "https://open.bigmodel.cn/api/paas/v4"},
        "glm-5.2",
    ),
    "minimax": ServicePreset(
        "minimax", "minimax", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
        {ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value: "https://api.minimaxi.com/v1"},
        "MiniMax-M3",
    ),
    # bug-fix: MiMo Token Plan endpoint 支持（2026-08-25）
    "mimo": ServicePreset(
        "mimo", "mimo", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
        {
            ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value: "https://api.xiaomimimo.com/v1",
            "mimo_token_plan": "https://token-plan-cn.xiaomimimo.com/v1",
        },
        "mimo-v2.5-pro",
    ),
    "scnet": ServicePreset(
        "scnet", "scnet", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
        {
            ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value: "https://api.scnet.cn/api/llm/v1",
            ProtocolFamily.OPENAI_RESPONSES.value: "https://api.scnet.cn/api/llm/v1",
            ProtocolFamily.ANTHROPIC_MESSAGES.value: "https://api.scnet.cn/api/llm/anthropic",
        },
        "",
        {
            "auth_scheme_by_protocol": {
                ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value: "bearer",
                ProtocolFamily.OPENAI_RESPONSES.value: "bearer",
                ProtocolFamily.ANTHROPIC_MESSAGES.value: "bearer",
            },
            "anthropic_version": "2023-06-01",
            "responses_store": False,
        },
    ),
    "generic_openai": ServicePreset(
        "generic_openai", "custom", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
        {}, "",
    ),
    "generic_anthropic": ServicePreset(
        "generic_anthropic", "custom", ProtocolFamily.ANTHROPIC_MESSAGES.value,
        {}, "",
        {"auth_scheme_by_protocol": {ProtocolFamily.ANTHROPIC_MESSAGES.value: "x-api-key"}, "anthropic_version": "2023-06-01"},
    ),
    "custom": ServicePreset(
        "custom", "custom", ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
        {}, "",
    ),
}


_LEGACY_SERVICE_ALIASES = {
    "gpt_5_6": "openai",
    "openai_compatible": "openai",
    "openai": "openai",
    "deepseek_v4": "deepseek",
    "deepseek": "deepseek",
    "glm_5_2": "zhipu",
    "zhipu": "zhipu",
    "minimax_m3": "minimax",
    "minimax": "minimax",
    "mimo": "mimo",
    "scnet": "scnet",
    "generic_openai": "generic_openai",
    "generic_anthropic": "generic_anthropic",
    "custom": "custom",
}


@dataclass(frozen=True, slots=True)
class ModelEndpointConfig:
    service_preset: str
    provider_identity: str
    protocol_family: str
    base_url: str
    model_name: str
    credential_scope: str
    reasoning_mode: str
    endpoint_overrides: Mapping[str, Any]
    optimization_family: str
    config_fingerprint: str
    schema: str = MODEL_ENDPOINT_SCHEMA
    protocol_source: str = "configured"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "service_preset": self.service_preset,
            "provider_identity": self.provider_identity,
            "protocol_family": self.protocol_family,
            "base_url": self.base_url,
            "model_name": self.model_name,
            "credential_scope": self.credential_scope,
            "reasoning_mode": self.reasoning_mode,
            "endpoint_overrides": dict(self.endpoint_overrides),
            "optimization_family": self.optimization_family,
            "config_fingerprint": self.config_fingerprint,
            "protocol_source": self.protocol_source,
        }


def normalize_protocol_family(value: Any, *, default: str = "") -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "chat": ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
        "chat_completions": ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
        "openai_chat": ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
        "openai_chat_completions": ProtocolFamily.OPENAI_CHAT_COMPLETIONS.value,
        "responses": ProtocolFamily.OPENAI_RESPONSES.value,
        "openai_response": ProtocolFamily.OPENAI_RESPONSES.value,
        "openai_responses": ProtocolFamily.OPENAI_RESPONSES.value,
        "anthropic": ProtocolFamily.ANTHROPIC_MESSAGES.value,
        "messages": ProtocolFamily.ANTHROPIC_MESSAGES.value,
        "anthropic_messages": ProtocolFamily.ANTHROPIC_MESSAGES.value,
    }
    normalized = aliases.get(text, text)
    if normalized in PROTOCOL_FAMILIES:
        return normalized
    fallback = str(default or "").strip()
    if fallback in PROTOCOL_FAMILIES:
        return fallback
    raise ValueError(f"unsupported protocol_family: {value}")


def normalize_service_preset(value: Any, provider_identity: str = "") -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if text in SERVICE_PRESETS:
        return text
    identity = str(provider_identity or "").strip().lower().replace("-", "_")
    return _LEGACY_SERVICE_ALIASES.get(identity, "custom")


def service_default_base_url(service_preset: str, protocol_family: str) -> str:
    preset = SERVICE_PRESETS.get(normalize_service_preset(service_preset), SERVICE_PRESETS["custom"])
    return str(preset.base_urls.get(protocol_family) or "").rstrip("/")


def _safe_settings() -> dict[str, Any]:
    from . import peizhi

    try:
        data = peizhi._load_api_config()  # one existing non-secret settings authority
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _credential_scope(provider_identity: str, base_url: str) -> str:
    try:
        from .endpoint_security import validate_model_endpoint

        binding = validate_model_endpoint(provider_identity, base_url, resolve_dns=False)
        return "official_provider" if binding.official else str(binding.custom_scope or "custom_endpoint")
    except Exception:
        return "unresolved"


def duqu_model_endpoint_config(provider_identity: str | None = None) -> ModelEndpointConfig:
    """Read the configured endpoint first; derive L4 optimization last.

    A configured base URL/model/protocol is never replaced by the optimization
    family.  Legacy settings without a protocol receive a compatibility default
    and are explicitly marked ``protocol_source=legacy_default``.
    """
    from . import peizhi

    configured_identity = peizhi.normalize_provider_identity(
        provider_identity or peizhi.duqu_moren_provider(peizhi.MOREN_PROVIDER)
    )
    settings = _safe_settings()
    inputs = settings.get("_provider_inputs") if isinstance(settings.get("_provider_inputs"), dict) else {}
    row = inputs.get(configured_identity) if isinstance(inputs.get(configured_identity), dict) else {}

    literal_provider = peizhi.normalize_provider_identity(row.get("provider") or configured_identity)
    endpoint_profiles = settings.get("_endpoint_profiles") if isinstance(settings.get("_endpoint_profiles"), dict) else {}
    profile = endpoint_profiles.get(configured_identity) if isinstance(endpoint_profiles.get(configured_identity), dict) else {}

    service_preset = normalize_service_preset(
        profile.get("service_preset") or row.get("service_preset") or settings.get("_model_service"),
        literal_provider,
    )
    preset = SERVICE_PRESETS.get(service_preset, SERVICE_PRESETS["custom"])

    raw_protocol = profile.get("protocol_family") or row.get("protocol_family")
    if raw_protocol:
        protocol_family = normalize_protocol_family(raw_protocol)
        protocol_source = "configured"
    else:
        protocol_family = normalize_protocol_family(preset.default_protocol)
        protocol_source = "legacy_default"

    # Literal user values are authoritative. Defaults are consulted only when
    # a literal value is absent, and are keyed by service+protocol, never L4.
    literal_base = str(row.get("base_url") or peizhi.duqu_configured_provider_base_url(configured_identity) or "").strip()
    base_url = peizhi.normalize_provider_base_url(literal_base) if literal_base else service_default_base_url(service_preset, protocol_family)
    literal_model = str(row.get("model_name") or peizhi.duqu_configured_model_ming(configured_identity) or "").strip()
    model_name = literal_model or str(preset.default_model or "").strip()

    endpoint_overrides: dict[str, Any] = dict(preset.endpoint_overrides or {})
    if isinstance(profile.get("endpoint_overrides"), dict):
        endpoint_overrides.update(profile["endpoint_overrides"])

    # Optimization is a derived attribute only.  It must be calculated *after*
    # endpoint/protocol/model authority has been fixed.
    optimization_family = peizhi.infer_provider_id(literal_provider, base_url, model_name)

    # Endpoint-scoped raw reasoning is authoritative for unknown models. Known
    # model family settings remain compatible with the existing L4 config.
    reasoning_mode = str(profile.get("reasoning_mode") or "").strip()
    if not reasoning_mode:
        try:
            reasoning = peizhi.duqu_model_reasoning_config(optimization_family, base_url, model_name)
            reasoning_mode = str(reasoning.get("configured_mode") or "")
        except Exception:
            reasoning_mode = ""

    fingerprint_payload = {
        "service_preset": service_preset,
        "provider_identity": literal_provider,
        "protocol_family": protocol_family,
        "base_url": base_url,
        "model_name": model_name,
        "reasoning_mode": reasoning_mode,
        "endpoint_overrides": endpoint_overrides,
    }
    return ModelEndpointConfig(
        service_preset=service_preset,
        provider_identity=literal_provider,
        protocol_family=protocol_family,
        base_url=str(base_url or "").rstrip("/"),
        model_name=model_name,
        credential_scope=_credential_scope(literal_provider, str(base_url or "")) if base_url else "unresolved",
        reasoning_mode=reasoning_mode,
        endpoint_overrides=endpoint_overrides,
        optimization_family=optimization_family,
        config_fingerprint=_fingerprint(fingerprint_payload),
        protocol_source=protocol_source,
    )


def endpoint_profile_patch(
    *,
    service_preset: Any,
    protocol_family: Any,
    endpoint_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the non-secret endpoint profile before persistence."""
    service = normalize_service_preset(service_preset)
    protocol = normalize_protocol_family(protocol_family, default=SERVICE_PRESETS[service].default_protocol)
    return {
        "service_preset": service,
        "protocol_family": protocol,
        "endpoint_overrides": dict(endpoint_overrides or {}),
    }
