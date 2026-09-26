"""Pin model roles from existing user endpoint/credential authorities per run."""
from . import peizhi
from .model_endpoint import _safe_settings, duqu_model_endpoint_config


def public_model(endpoint):
    return {"provider": endpoint.provider_identity, "name": endpoint.model_name,
            "protocol": endpoint.protocol_family, "config_fingerprint": endpoint.config_fingerprint}


def configured_models(executor):
    settings = _safe_settings()
    identities = {executor.provider_identity, *peizhi.L4_PROVIDER_IDS}
    for field in ("_provider_inputs", "_endpoint_profiles"):
        if isinstance(settings.get(field), dict):
            identities.update(settings[field])
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
            "fallbacks": [e for e in alternatives[1:] if e is not challenger] + ([executor] if alternatives else []),
            "mode": "multiple_models" if alternatives else "single_model_isolated_contexts"}


def input_budget(endpoint, *, output_reserve=8192):
    """Endpoint-specific context budget; unknown models use a conservative cap."""
    overrides = getattr(endpoint, "endpoint_overrides", {}) or {}
    configured = overrides.get("context_window_tokens")
    if type(configured) is int and configured > output_reserve:
        window = configured
    else:
        window = 32768
        try:
            from tiangong_kernel.l4_action_grounding.model_provider_adapter import all_provider_factsheets
            for factsheet in all_provider_factsheets().values():
                if factsheet.default_model_id == endpoint.model_name:
                    window = int(factsheet.context_window_tokens)
                    break
        except Exception:
            pass
    return max(1, window - output_reserve)
