"use strict";

// Runs in Electron's main process so DPAPI-backed safeStorage can decrypt the
// desktop credential without exposing it to the renderer, shell, or output.
const fs = require("fs");
const https = require("https");
const path = require("path");
const { app, safeStorage } = require("electron");

// safeStorage is scoped to Chromium's user-data profile.  Match the shipped
// desktop identity before Electron initializes so this diagnostic reads the
// same DPAPI context as the running application, not a helper-specific one.
const APP_NAME = "tiangong-v3-qiyuan";
const USER_DATA_NAME = "天工造物 v3.0.3 完整版";
const appDataRoot = process.env.APPDATA || "";
app.setName(APP_NAME);
app.setPath("userData", path.join(appDataRoot, USER_DATA_NAME));

const runtimeRoot = path.join(
  appDataRoot,
  USER_DATA_NAME,
  "runtime",
);
const credentialPath = path.join(runtimeRoot, "state", "desktop_provider_credentials.json");
const configPath = path.join(process.env.USERPROFILE || "", ".tiangong", "api_keys.json");

function print(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function providerConfig() {
  const source = JSON.parse(fs.readFileSync(configPath, "utf8"));
  const provider = String(source._default_provider || "").trim();
  const input = source._provider_inputs?.[provider] || {};
  return {
    provider,
    base_url: String(input.base_url || source._base_urls?.[provider] || "").trim(),
    model_name: String(input.model_name || source._model_names?.[provider] || "").trim(),
  };
}

function requestModels(apiKey) {
  return new Promise((resolve, reject) => {
    const request = https.request("https://api.deepseek.com/models", {
      method: "GET",
      headers: { Authorization: `Bearer ${apiKey}` },
      timeout: 12_000,
    }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { if (body.length < 64 * 1024) body += chunk; });
      response.on("end", () => resolve({ status: response.statusCode || 0, body }));
    });
    request.on("timeout", () => request.destroy(Object.assign(new Error("request_timeout"), { code: "ETIMEDOUT" })));
    request.on("error", reject);
    request.end();
  });
}

async function run() {
  try {
    if (!fs.existsSync(configPath)) {
      return print({ ok: false, stage: "configuration", error_code: "model_configuration_missing" });
    }
    let config;
    try {
      config = providerConfig();
    } catch (_error) {
      return print({ ok: false, stage: "configuration", error_code: "model_configuration_invalid" });
    }
    if (config.provider !== "deepseek_v4" || config.base_url !== "https://api.deepseek.com") {
      return print({ ok: false, stage: "configuration", provider: config.provider, error_code: "unsupported_test_target" });
    }
    if (!fs.existsSync(credentialPath)) {
      return print({ ok: false, stage: "credential_lookup", provider: config.provider, error_code: "credential_store_missing" });
    }
    let credentials;
    try {
      credentials = JSON.parse(fs.readFileSync(credentialPath, "utf8"));
    } catch (_error) {
      return print({ ok: false, stage: "credential_lookup", provider: config.provider, error_code: "credential_store_invalid" });
    }
    const entry = credentials?.providers?.deepseek_v4;
    if (entry?.scheme !== "electron-safe-storage-v1" || typeof entry.value !== "string") {
      return print({ ok: false, stage: "credential_lookup", provider: config.provider, error_code: "credential_missing" });
    }
    if (!safeStorage.isEncryptionAvailable()) {
      return print({ ok: false, stage: "credential_decrypt", provider: config.provider, error_code: "safe_storage_unavailable" });
    }
    let apiKey;
    try {
      apiKey = safeStorage.decryptString(Buffer.from(entry.value, "base64"));
    } catch (_error) {
      return print({ ok: false, stage: "credential_decrypt", provider: config.provider, error_code: "credential_decrypt_failed" });
    }
    if (!apiKey) {
      return print({ ok: false, stage: "credential_decrypt", provider: config.provider, error_code: "credential_empty" });
    }
    try {
      const response = await requestModels(apiKey);
      const body = JSON.parse(response.body || "{}");
      const providerCode = String(body?.error?.code || body?.code || "").replace(/[^A-Za-z0-9_.:-]/g, "").slice(0, 120);
      const modelIds = Array.isArray(body?.data)
        ? body.data.map((item) => String(item?.id || "")).filter(Boolean)
        : [];
      return print({
        ok: response.status >= 200 && response.status < 300,
        stage: "provider_models",
        provider: config.provider,
        model_name: config.model_name,
        http_status: response.status,
        error_code: providerCode || null,
        configured_model_available: modelIds.includes(config.model_name),
      });
    } catch (error) {
      return print({
        ok: false,
        stage: "provider_models",
        provider: config.provider,
        error_code: error?.code === "ETIMEDOUT" ? "request_timeout" : "provider_transport_failed",
      });
    }
  } catch (error) {
    return print({ ok: false, stage: "internal", error_code: "connection_test_failed" });
  }
}

app.whenReady().then(run).finally(() => app.quit());
