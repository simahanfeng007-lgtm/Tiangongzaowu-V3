"""Deterministic P18.1 Stage-D migration.

Edits only existing authoritative settings/probe seams.  It does not add a
second credential channel, model runtime, or startup path.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PEIZHI = ROOT / "app/backend/tiangong-backend/v3/peizhi.py"
BRIDGE = ROOT / "app/backend/tiangong-backend/v3/duihua_qiaojie.py"
HTTP_CLIENT = ROOT / "app/backend/tiangong-backend/v3/jineng/http_kehuduan.py"
MAIN_JS = ROOT / "app/main.js"


def replace_py_function(path: Path, name: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    matches = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if len(matches) != 1:
        raise RuntimeError(f"{path}:{name}: expected one function, found {len(matches)}")
    node = matches[0]
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [replacement.rstrip() + "\n"]
    path.write_text("".join(lines), encoding="utf-8")


def find_js_function(source: str, name: str) -> tuple[int, int]:
    needles = [f"async function {name}(", f"function {name}("]
    start = next((source.find(n) for n in needles if source.find(n) >= 0), -1)
    if start < 0:
        raise RuntimeError(f"JS function not found: {name}")
    brace = source.find("{", start)
    if brace < 0:
        raise RuntimeError(f"JS function body not found: {name}")
    depth = 0
    quote = None
    escape = False
    template_expr_depth = 0
    i = brace
    while i < len(source):
        ch = source[i]
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
        else:
            if ch in {'"', "'", "`"}:
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return start, i + 1
        i += 1
    raise RuntimeError(f"unterminated JS function: {name}")


def replace_js_function(path: Path, name: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    start, end = find_js_function(source, name)
    path.write_text(source[:start] + replacement.rstrip() + source[end:], encoding="utf-8")


NORMALIZE_IDENTITY = r'''
def normalize_provider_identity(provider_id: str | None) -> str:
    """Normalize only persisted connection identity, never L4 routing family.

    P18.1 deliberately separates provider identity from optimization family.
    Historical family IDs remain readable, but service names stay service names.
    """
    raw = str(provider_id or "").strip()
    if not raw:
        return CUSTOM_PROVIDER_ID
    key = raw.lower().replace(" ", "").replace("-", "_")
    identity_aliases = {
        "openai": "openai",
        "deepseek": "deepseek",
        "zhipu": "zhipu",
        "zhipuai": "zhipu",
        "zai": "zhipu",
        "z_ai": "zhipu",
        "glm": "zhipu",
        "minimax": "minimax",
        "xiaomi": "mimo",
        "xiaomi_mimo": "mimo",
        "mimo": "mimo",
        "scnet": "scnet",
        "anthropic": "anthropic",
        "custom": CUSTOM_PROVIDER_ID,
        # Already-persisted historical routing-family identities stay valid so
        # upgrades do not orphan their existing endpoint/key bindings.
        "deepseek_v4": "deepseek_v4",
        "glm_5_1": "glm_5_2",
        "glm_5_2": "glm_5_2",
        "minimax_m3": "minimax_m3",
        "gpt": "gpt_5_6",
        "gpt_5_5": "gpt_5_6",
        "gpt_5_6": "gpt_5_6",
    }
    return identity_aliases.get(key, raw)
'''


LLM_SETTINGS = r'''
def _llm_settings() -> dict:
    """Return P18.1 endpoint authority plus legacy UI projections."""
    from .endpoint_security import validate_model_endpoint
    from .model_endpoint import SERVICE_PRESETS, duqu_model_endpoint_config
    from .model_stream_config import resolve_model_capability
    from .peizhi import (
        MOREN_PROVIDER,
        _load_api_config,
        duqu_endpoint_api_miyao,
        duqu_model_reasoning_config,
        duqu_moren_provider,
        l4_provider_display_name,
        l4_provider_presets,
        l4_provider_profiles,
        provider_match_info,
    )

    identity = duqu_moren_provider(MOREN_PROVIDER)
    endpoint = duqu_model_endpoint_config(identity)
    try:
        binding = validate_model_endpoint(endpoint.provider_identity, endpoint.base_url, resolve_dns=False)
        endpoint_key = duqu_endpoint_api_miyao(endpoint.provider_identity, endpoint.base_url)
        endpoint_state = "ready"
    except ValueError:
        binding = None
        endpoint_key = None
        endpoint_state = "rejected"
    credential_state = "configured" if endpoint_key else "not_configured"

    capability = resolve_model_capability(
        endpoint.model_name,
        endpoint.optimization_family,
        endpoint.protocol_family,
        endpoint.service_preset,
        endpoint.endpoint_overrides.get("capability_override")
        if isinstance(endpoint.endpoint_overrides, dict)
        and isinstance(endpoint.endpoint_overrides.get("capability_override"), dict)
        else None,
    )
    if capability.known_model:
        reasoning = duqu_model_reasoning_config(
            endpoint.optimization_family, endpoint.base_url, endpoint.model_name
        )
    else:
        reasoning = {
            "supported": True,
            "control": "raw_optional",
            "raw_optional": True,
            "modes": [],
            "default_mode": "",
            "configured_mode": endpoint.reasoning_mode,
            "effective_mode": endpoint.reasoning_mode,
            "enabled": bool(endpoint.reasoning_mode),
            "private_reasoning_visible": False,
            "known_model": False,
        }

    optimization = _llm_optimization_status()
    active_provider = optimization.get("active_provider") if isinstance(optimization, dict) else {}
    match = provider_match_info(endpoint.provider_identity, endpoint.base_url, endpoint.model_name)
    matched_display_name = _llm_match_display_name(
        match, endpoint.provider_identity, l4_provider_display_name(endpoint.optimization_family)
    )
    match["display_name"] = matched_display_name

    raw = _load_api_config()
    raw_profiles = raw.get("_endpoint_profiles") if isinstance(raw, dict) and isinstance(raw.get("_endpoint_profiles"), dict) else {}
    model_provider_profiles = {}
    for provider_id, profile in raw_profiles.items():
        if not isinstance(profile, dict):
            continue
        model_provider_profiles[str(profile.get("service_preset") or provider_id)] = {
            **profile,
            "provider_identity": provider_id,
        }

    preset = SERVICE_PRESETS.get(endpoint.service_preset)
    return {
        "ok": True,
        # P18.1 first-class authority.
        "service_preset": endpoint.service_preset,
        "provider_identity": endpoint.provider_identity,
        "protocol_family": endpoint.protocol_family,
        "optimization_family": endpoint.optimization_family,
        "base_url": endpoint.base_url,
        "model_name": endpoint.model_name,
        "endpoint_overrides": dict(endpoint.endpoint_overrides),
        "config_fingerprint": endpoint.config_fingerprint,
        "protocol_source": endpoint.protocol_source,
        "effective_capability": capability.as_dict(),
        # Compatibility projection for the existing renderer/diagnostics.
        "provider": endpoint.provider_identity,
        "provider_display_name": preset.preset_id if preset else endpoint.provider_identity,
        "matched_provider": endpoint.optimization_family,
        "matched_provider_display_name": matched_display_name,
        "configured_provider": endpoint.provider_identity,
        "model": endpoint.model_name,
        "configured_model_name": endpoint.model_name,
        "configured_base_url": endpoint.base_url,
        "modelService": endpoint.service_preset,
        "modelProtocol": endpoint.protocol_family,
        "api_key": "configured" if credential_state == "configured" else "missing",
        "credential_state": credential_state,
        "endpoint_state": endpoint_state,
        "provider_match": match,
        "providers": l4_provider_presets(),
        "provider_profiles": l4_provider_profiles(),
        "modelProviderProfiles": model_provider_profiles,
        "reasoning": reasoning,
        "credential_scope": (
            "official_provider" if binding and binding.official
            else binding.custom_scope if binding else "rejected"
        ),
        "endpoint_official": bool(binding and binding.official),
        "optimization": {
            "ok": bool(optimization.get("ok")) if isinstance(optimization, dict) else False,
            "trace_rows": optimization.get("trace_rows") if isinstance(optimization, dict) else 0,
            "active_provider": active_provider,
            "route_recommendations": (optimization.get("route_recommendations") or [])[:5] if isinstance(optimization, dict) else [],
            "observability_gaps": (optimization.get("observability_gaps") or [])[:6] if isinstance(optimization, dict) else [],
        },
    }
'''


SAVE_LLM_SETTINGS = r'''
def _save_llm_settings(payload: dict) -> dict:
    """Persist endpoint/protocol identity without allowing family write-back."""
    from .endpoint_security import validate_model_endpoint
    from .model_endpoint import (
        SERVICE_PRESETS,
        endpoint_profile_patch,
        normalize_service_preset,
        service_default_base_url,
    )
    from .model_stream_config import resolve_model_capability
    from .peizhi import (
        API_PEIZHI_LUJING,
        MOREN_PROVIDER,
        duqu_configured_model_ming,
        duqu_configured_provider_base_url,
        duqu_moren_provider,
        duqu_provider_input_config,
        infer_provider_id,
        l4_provider_display_name,
        normalize_provider_base_url,
        normalize_provider_identity,
        provider_match_info,
        save_model_reasoning_config,
    )
    from .settings_persistence import atomic_write_json

    api_key = str(payload.get("modelApiKey") or payload.get("api_key") or "").strip()
    if api_key:
        return {"ok": False, "error": "credential_must_use_desktop_vault", "error_code": "credential_plaintext_forbidden"}

    current_identity = duqu_moren_provider(MOREN_PROVIDER)
    current_input = duqu_provider_input_config(current_identity)
    has_provider = any(key in payload for key in ("provider_identity", "provider", "modelProvider"))
    raw_provider = str(
        payload.get("provider_identity")
        if "provider_identity" in payload else payload.get("provider")
        if "provider" in payload else payload.get("modelProvider") or ""
    ).strip()

    service_value = payload.get("service_preset") if "service_preset" in payload else payload.get("modelService")
    service_preset = normalize_service_preset(
        service_value or current_input.get("service_preset") or "custom",
        raw_provider or current_identity,
    )
    preset = SERVICE_PRESETS[service_preset]
    identity_provider = normalize_provider_identity(
        raw_provider if has_provider and raw_provider else preset.provider_identity or current_identity
    )

    protocol_value = payload.get("protocol_family") if "protocol_family" in payload else payload.get("modelProtocol")
    if service_preset == "custom" and not str(protocol_value or "").strip():
        return {"ok": False, "error": "protocol_family_required_for_custom", "error_code": "protocol_family_required"}
    try:
        endpoint_profile = endpoint_profile_patch(
            service_preset=service_preset,
            protocol_family=protocol_value or preset.default_protocol,
            endpoint_overrides=payload.get("endpoint_overrides") if isinstance(payload.get("endpoint_overrides"), dict) else {},
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "error_code": "protocol_family_invalid"}
    protocol_family = endpoint_profile["protocol_family"]

    has_base_url = any(key in payload for key in ("base_url", "modelBaseUrl"))
    raw_base = payload.get("base_url") if "base_url" in payload else payload.get("modelBaseUrl")
    previous_base = duqu_configured_provider_base_url(current_identity) if identity_provider == current_identity else ""
    if has_base_url:
        base_url = normalize_provider_base_url(raw_base)
    else:
        base_url = normalize_provider_base_url(previous_base) if previous_base else service_default_base_url(service_preset, protocol_family)
    if not base_url:
        base_url = service_default_base_url(service_preset, protocol_family)
    if not base_url:
        return {"ok": False, "error": "model_base_url_required", "error_code": "model_base_url_required"}

    has_model_name = any(key in payload for key in ("model_name", "modelName", "model"))
    raw_model = payload.get("model_name") if "model_name" in payload else payload.get("modelName") if "modelName" in payload else payload.get("model")
    previous_model = duqu_configured_model_ming(current_identity) if identity_provider == current_identity else ""
    model_name = str(raw_model if has_model_name else previous_model or preset.default_model or "").strip()
    if not model_name:
        return {"ok": False, "error": "model_name_required", "error_code": "model_name_required"}

    try:
        binding = validate_model_endpoint(identity_provider, base_url, resolve_dns=False)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "error_code": "model_endpoint_rejected"}

    optimization_family = infer_provider_id(identity_provider, base_url, model_name)
    capability = resolve_model_capability(
        model_name, optimization_family, protocol_family, service_preset,
        endpoint_profile["endpoint_overrides"].get("capability_override")
        if isinstance(endpoint_profile.get("endpoint_overrides"), dict)
        and isinstance(endpoint_profile["endpoint_overrides"].get("capability_override"), dict)
        else None,
    )
    has_reasoning_mode = any(key in payload for key in ("reasoning_mode", "modelThinkingDepth", "modelThinkingEnabled"))
    reasoning_mode = str(
        payload.get("reasoning_mode") if "reasoning_mode" in payload
        else payload.get("modelThinkingDepth") if "modelThinkingDepth" in payload else ""
    ).strip().lower()
    if "modelThinkingEnabled" in payload and not bool(payload.get("modelThinkingEnabled")):
        reasoning_mode = "off" if capability.known_model else ""

    API_PEIZHI_LUJING.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(API_PEIZHI_LUJING.read_text(encoding="utf-8-sig")) if API_PEIZHI_LUJING.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    data["_default_provider"] = identity_provider
    data["_model_service"] = service_preset
    provider_inputs = data.get("_provider_inputs") if isinstance(data.get("_provider_inputs"), dict) else {}
    provider_inputs[identity_provider] = {
        "provider": identity_provider,
        "service_preset": service_preset,
        "protocol_family": protocol_family,
        "base_url": base_url,
        "model_name": model_name,
    }
    data["_provider_inputs"] = provider_inputs

    base_urls = data.get("_base_urls") if isinstance(data.get("_base_urls"), dict) else {}
    base_urls[identity_provider] = base_url
    data["_base_urls"] = base_urls
    model_names = data.get("_model_names") if isinstance(data.get("_model_names"), dict) else {}
    model_names[identity_provider] = model_name
    data["_model_names"] = model_names

    endpoint_profiles = data.get("_endpoint_profiles") if isinstance(data.get("_endpoint_profiles"), dict) else {}
    previous_profile = endpoint_profiles.get(identity_provider) if isinstance(endpoint_profiles.get(identity_provider), dict) else {}
    endpoint_profiles[identity_provider] = {
        **previous_profile,
        **endpoint_profile,
        "reasoning_mode": reasoning_mode if has_reasoning_mode else str(previous_profile.get("reasoning_mode") or ""),
    }
    data["_endpoint_profiles"] = endpoint_profiles

    if has_reasoning_mode and capability.known_model:
        mode = reasoning_mode
        if not mode:
            mode = capability.reasoning_modes[0] if capability.reasoning_modes else "off"
        try:
            save_model_reasoning_config(
                data,
                provider_id=optimization_family,
                base_url=base_url,
                model_name=model_name,
                mode=mode,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "error_code": "model_reasoning_mode_unsupported"}

    atomic_write_json(API_PEIZHI_LUJING, data)
    result = _llm_settings()
    match = provider_match_info(identity_provider, base_url, model_name)
    matched_display_name = _llm_match_display_name(match, identity_provider, l4_provider_display_name(optimization_family))
    result.update({
        "provider": identity_provider,
        "provider_identity": identity_provider,
        "service_preset": service_preset,
        "protocol_family": protocol_family,
        "optimization_family": optimization_family,
        "matched_provider": optimization_family,
        "matched_provider_display_name": matched_display_name,
        "configured_provider": identity_provider,
        "model": model_name,
        "model_name": model_name,
        "configured_model_name": model_name,
        "base_url": base_url,
        "configured_base_url": base_url,
        "provider_match": match,
        "credential_scope": "official_provider" if binding.official else binding.custom_scope,
        "endpoint_official": binding.official,
    })
    return result
'''


REQUEST_PROVIDER_PROBE = r'''
function requestProviderProbe(url, { method = "GET", apiKey = "", payload = null, headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const body = payload ? JSON.stringify(payload) : "";
    const transport = url.protocol === "http:" ? http : https;
    const mergedHeaders = {
      ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
      ...(headers || {}),
      ...(body ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) } : {}),
    };
    const request = transport.request(url, {
      method,
      headers: mergedHeaders,
      timeout: 15000,
      rejectUnauthorized: true,
    }, (response) => {
      let responseBody = "";
      response.on("data", (chunk) => {
        const remaining = 256 * 1024 - responseBody.length;
        if (remaining > 0) responseBody += String(chunk).slice(0, remaining);
      });
      response.on("end", () => resolve({
        statusCode: response.statusCode || 0,
        body: responseBody,
        latencyMs: Date.now() - started,
      }));
    });
    request.on("timeout", () => request.destroy(Object.assign(new Error("request_timeout"), { code: "ETIMEDOUT" })));
    request.on("error", reject);
    if (body) request.write(body);
    request.end();
  });
}
'''


PROBE_PROVIDER = r'''
async function probeProviderApiConnection() {
  const settings = await desktopModelSettingsRequest("GET");
  if (!settings || settings.ok === false) {
    return { ok: false, error: settings?.error || "model_settings_unavailable" };
  }
  const provider = String(settings.provider_identity || settings.configured_provider || settings.provider || "custom").trim().toLowerCase();
  const servicePreset = String(settings.service_preset || settings.modelService || "custom").trim().toLowerCase();
  const protocolFamily = String(settings.protocol_family || settings.modelProtocol || "openai_chat_completions").trim();
  const baseUrl = String(settings.base_url || settings.configured_base_url || "").trim();
  const modelName = String(settings.model_name || settings.configured_model_name || settings.model || "").trim();
  if (!baseUrl || !modelName) return { ok: false, error: "provider_endpoint_or_model_missing" };
  if (String(settings.credential_state || "") !== "configured") {
    return { ok: false, error: "provider_api_key_missing", protocol_family: protocolFamily };
  }

  let credentialId;
  let apiKey;
  try {
    credentialId = modelCredentialBindingId(provider, baseUrl);
    const envelope = readDesktopCredentialEnvelope(desktopCredentialStorePath());
    const item = envelope.providers?.[credentialId];
    if (!item || item.backend !== "electron_safe_storage" || !safeStorage.isEncryptionAvailable()) {
      return { ok: false, error: "provider_api_key_missing", protocol_family: protocolFamily };
    }
    apiKey = safeStorage.decryptString(Buffer.from(String(item.value || ""), "base64"));
  } catch (error) {
    return { ok: false, error: String(error?.message || error || "credential_read_failed"), protocol_family: protocolFamily };
  }
  if (!apiKey) return { ok: false, error: "provider_api_key_missing", protocol_family: protocolFamily };

  let suffix;
  let payload;
  let requestApiKey = apiKey;
  let headers = {};
  if (protocolFamily === "openai_responses") {
    suffix = "responses";
    payload = { model: modelName, input: "ping", max_output_tokens: 1, store: false, stream: false };
  } else if (protocolFamily === "anthropic_messages") {
    suffix = "v1/messages";
    payload = { model: modelName, messages: [{ role: "user", content: "ping" }], max_tokens: 1, stream: false };
    headers = { "anthropic-version": "2023-06-01" };
    if (servicePreset !== "scnet") {
      requestApiKey = "";
      headers["x-api-key"] = apiKey;
    }
  } else if (protocolFamily === "openai_chat_completions") {
    suffix = "chat/completions";
    payload = { model: modelName, messages: [{ role: "user", content: "ping" }], max_tokens: 1, stream: false };
  } else {
    return { ok: false, error: "unsupported_protocol_family", protocol_family: protocolFamily };
  }

  const endpoint = providerProbeEndpoint(baseUrl, suffix);
  try {
    const response = await requestProviderProbe(endpoint, {
      method: "POST",
      apiKey: requestApiKey,
      payload,
      headers,
    });
    const status = Number(response.statusCode || 0);
    const authValid = ![401, 403].includes(status);
    const protocolValid = status > 0 && ![404, 405, 415, 422].includes(status) && status < 500;
    const modelValid = status > 0 && status < 400;
    return {
      ok: modelValid,
      provider_identity: provider,
      service_preset: servicePreset,
      protocol_family: protocolFamily,
      endpoint_reachable: status > 0,
      auth_valid: authValid,
      protocol_valid: protocolValid,
      model_valid: modelValid,
      streaming_supported: false,
      native_tools_supported: false,
      reasoning_control_supported: false,
      parallel_tool_calls_supported: false,
      structured_output_supported: false,
      continuation_supported: false,
      configured_model_available: modelValid ? true : null,
      http_status: status,
      latency_ms: response.latencyMs,
      probe_evidence: {
        protocol_family: protocolFamily,
        endpoint: endpoint.toString(),
        http_status: status,
        latency_ms: response.latencyMs,
        conservative_tool_capability: true,
      },
      response_preview: modelValid ? "" : String(response.body || "").slice(0, 500),
    };
  } catch (error) {
    return {
      ok: false,
      provider_identity: provider,
      service_preset: servicePreset,
      protocol_family: protocolFamily,
      endpoint_reachable: false,
      auth_valid: false,
      protocol_valid: false,
      model_valid: false,
      streaming_supported: false,
      native_tools_supported: false,
      reasoning_control_supported: false,
      parallel_tool_calls_supported: false,
      structured_output_supported: false,
      continuation_supported: false,
      error: String(error?.message || error || "provider_probe_failed"),
    };
  }
}
'''


RAW_REASONING_HELPER = r'''
def _apply_endpoint_raw_reasoning(endpoint: Any, capability: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply user-entered unknown-model reasoning only when non-empty."""
    if getattr(capability, "reasoning_control", "") != "raw_optional":
        return {"raw_reasoning_sent": False}
    mode = str(getattr(endpoint, "reasoning_mode", "") or "").strip()
    if not mode:
        payload.pop("reasoning_effort", None)
        # Do not manufacture thinking={} for unknown models.
        if isinstance(payload.get("thinking"), dict) and not payload.get("thinking"):
            payload.pop("thinking", None)
        return {"raw_reasoning_sent": False, "raw_reasoning_mode": ""}
    if endpoint.protocol_family in {"openai_chat_completions", "openai_responses"}:
        payload["reasoning_effort"] = mode
    elif endpoint.protocol_family == "anthropic_messages":
        payload["thinking"] = {"type": mode}
    return {"raw_reasoning_sent": True, "raw_reasoning_mode": mode}
'''


def patch_http_reasoning() -> None:
    source = HTTP_CLIENT.read_text(encoding="utf-8")
    anchor = "\ndef _inject_native_audio_input("
    if "def _apply_endpoint_raw_reasoning(" not in source:
        idx = source.find(anchor)
        if idx < 0:
            raise RuntimeError("http raw reasoning insertion anchor missing")
        source = source[:idx] + "\n" + RAW_REASONING_HELPER.rstrip() + "\n" + source[idx:]
    old = """reasoning_trace = _apply_reasoning_profile(\n                pid, payload, base_url=base_url, model_name=model_name\n            )"""
    new = old + """\n            raw_reasoning_trace = _apply_endpoint_raw_reasoning(endpoint, capability, payload)\n            reasoning_trace.update(raw_reasoning_trace)"""
    if old not in source:
        if "raw_reasoning_trace = _apply_endpoint_raw_reasoning" not in source:
            raise RuntimeError("http reasoning call anchor missing")
    else:
        source = source.replace(old, new, 1)
    HTTP_CLIENT.write_text(source, encoding="utf-8")


def main() -> None:
    replace_py_function(PEIZHI, "normalize_provider_identity", NORMALIZE_IDENTITY)
    replace_py_function(BRIDGE, "_llm_settings", LLM_SETTINGS)
    replace_py_function(BRIDGE, "_save_llm_settings", SAVE_LLM_SETTINGS)
    patch_http_reasoning()
    replace_js_function(MAIN_JS, "requestProviderProbe", REQUEST_PROVIDER_PROBE)
    replace_js_function(MAIN_JS, "probeProviderApiConnection", PROBE_PROVIDER)
    print("P18.1 Stage D settings/probe migration applied")


if __name__ == "__main__":
    main()
