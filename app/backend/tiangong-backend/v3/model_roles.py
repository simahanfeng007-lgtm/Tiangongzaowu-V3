"""Pin model roles from existing user endpoint/credential authorities per run."""
from . import peizhi
from .model_endpoint import _safe_settings, duqu_model_endpoint_config


def public_model(endpoint):
    return {"provider": endpoint.provider_identity, "name": endpoint.model_name,
            "protocol": endpoint.protocol_family, "config_fingerprint": endpoint.config_fingerprint}


def configured_models(executor):
    settings = _safe_settings()
    # Shared credential aliases do not mean the user configured every bundled
    # model/preset, possibly on a different billing endpoint.
    identities = {executor.provider_identity}
    for field in ("_provider_inputs", "_endpoint_profiles", "_api_keys", "_base_urls"):
        if isinstance(settings.get(field), dict):
            identities.update(key for key, value in settings[field].items() if value)
    models, seen = [executor], {(executor.base_url, executor.model_name, executor.protocol_family)}
    for identity in sorted(identities):
        try:
            endpoint = duqu_model_endpoint_config(identity)
            key = (endpoint.base_url, endpoint.model_name, endpoint.protocol_family)
            if (key not in seen and endpoint.base_url and endpoint.model_name
                    and peizhi.provider_credential_state(endpoint.provider_identity, endpoint.base_url) == "configured"):
                models.append(endpoint)
                seen.add(key)
        except Exception:
            # Invalid/unconfigured profiles are not extra model capacity.
            continue
    return models


def select_roles(models):
    executor = models[0]
    alternatives = sorted(models[1:], key=lambda e: (
        e.provider_identity == executor.provider_identity, e.model_name == executor.model_name,
        e.provider_identity, e.model_name))
    judge = alternatives[0] if alternatives else executor
    challenger = alternatives[1] if len(alternatives) > 1 else None
    return {"executor": executor, "judge": judge, "challenger": challenger,
            "fallbacks": ([executor] if alternatives else []) + [e for e in alternatives[1:] if e is not challenger],
            "mode": "multiple_models" if alternatives else "single_model_isolated_contexts"}


def input_budget(endpoint, *, output_reserve=8192):
    """Endpoint-specific context budget; unknown models use a conservative cap."""
    overrides = getattr(endpoint, "endpoint_overrides", {}) or {}
    configured = overrides.get("context_window_tokens")
    if type(configured) is int and configured > 0:
        window = configured
    else:
        from .model_stream_config import resolve_model_capability
        capability = resolve_model_capability(
            endpoint.model_name, getattr(endpoint, "optimization_family", ""), endpoint.protocol_family,
            getattr(endpoint, "service_preset", "custom"), overrides)
        window = capability.max_context
    return max(1, window - output_reserve)
