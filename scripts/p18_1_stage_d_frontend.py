"""P18.1 Stage-D renderer migration: service + protocol + endpoint UX."""
from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "app/frontend-v2/renderer/plugins/settings-panel.mjs"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


text = PATH.read_text(encoding="utf-8")
text = replace_once(
    text,
    'import { providerPresets } from "./provider-presets.mjs";',
    'import { providerPresets, providerOptions, protocolOptionsForPreset, resolvePresetBaseUrl, normalizeServicePreset } from "./provider-presets.mjs";',
    "provider preset imports",
)

old_html = '''                <label class="field-row">
                  <span>服务预设</span>
                  <select id="settingsModelPreset"></select>
                </label>
                <label class="field-row">
                  <span>服务商</span>
                  <input id="settingsModelProvider" placeholder="openai / deepseek / glm / mimo / custom" />
                </label>'''
new_html = '''                <label class="field-row">
                  <span>服务商</span>
                  <select id="settingsModelPreset"></select>
                </label>
                <label class="field-row">
                  <span>接口协议</span>
                  <select id="settingsModelProtocol"></select>
                </label>
                <input id="settingsModelProvider" type="hidden" />'''
text = replace_once(text, old_html, new_html, "service/protocol fields")

old_thinking = '''                  <select id="settingsModelThinking"></select>
                  <small id="settingsModelThinkingHint">思考过程只供模型内部使用，前端仅显示自然回复。</small>'''
new_thinking = '''                  <select id="settingsModelThinking"></select>
                  <input id="settingsModelThinkingRaw" style="display:none" placeholder="未知模型：可选原始思考模式，留空=模型默认" />
                  <small id="settingsModelThinkingHint">思考过程只供模型内部使用，前端仅显示自然回复。</small>'''
text = replace_once(text, old_thinking, new_thinking, "raw reasoning field")

old_decl = '''    const presetInput = panel.querySelector("#settingsModelPreset");
    const providerInput = panel.querySelector("#settingsModelProvider");
    const modelInput = panel.querySelector("#settingsModelName");
    const thinkingInput = panel.querySelector("#settingsModelThinking");
    const thinkingHint = panel.querySelector("#settingsModelThinkingHint");
    const baseUrlInput = panel.querySelector("#settingsModelBaseUrl");'''
new_decl = '''    const presetInput = panel.querySelector("#settingsModelPreset");
    const protocolInput = panel.querySelector("#settingsModelProtocol");
    const providerInput = panel.querySelector("#settingsModelProvider");
    const modelInput = panel.querySelector("#settingsModelName");
    const thinkingInput = panel.querySelector("#settingsModelThinking");
    const thinkingRawInput = panel.querySelector("#settingsModelThinkingRaw");
    const thinkingHint = panel.querySelector("#settingsModelThinkingHint");
    const baseUrlInput = panel.querySelector("#settingsModelBaseUrl");'''
text = replace_once(text, old_decl, new_decl, "field declarations")

old_norm = '''function normalizedReasoningCapability(value = null) {
  const modes = Array.isArray(value?.modes)
    ? value.modes.map((item) => {
      if (typeof item !== "string") return item;
      return { value: item, label: reasoningModeLabels[item] || item };
    })
      .filter((item) => item && String(item.value || "").trim())
    : [];
  return {
    supported: value?.supported === true && modes.length > 0,
    modes,
    defaultMode: String(value?.default_mode || value?.defaultDepth || modes[0]?.value || "off"),
    configuredMode: String(value?.configured_mode || value?.effective_mode || ""),
  };
}'''
new_norm = '''function normalizedReasoningCapability(value = null) {
  const modes = Array.isArray(value?.modes)
    ? value.modes.map((item) => {
      if (typeof item !== "string") return item;
      return { value: item, label: reasoningModeLabels[item] || item };
    }).filter((item) => item && String(item.value || "").trim())
    : [];
  const rawOptional = value?.raw_optional === true || value?.rawOptional === true || value?.control === "raw_optional";
  return {
    supported: rawOptional || (value?.supported === true && modes.length > 0),
    rawOptional,
    modes,
    defaultMode: String(value?.default_mode || value?.defaultDepth || modes[0]?.value || ""),
    configuredMode: String(value?.configured_mode || value?.effective_mode || ""),
  };
}'''
text = replace_once(text, old_norm, new_norm, "reasoning capability normalization")

old_selected = '''    function selectedPresetValue(value) {
      const selected = String(value || "").trim();
      return currentPresetRows.some((item) => item.id === selected) ? selected : "";
    }

    function renderPresetInput(selected) {
      const safeSelected = selectedPresetValue(selected);
      presetInput.innerHTML = presetOptions(currentPresetRows, safeSelected);
      presetInput.value = safeSelected;
    }'''
new_selected = '''    function selectedPresetValue(value) {
      return normalizeServicePreset(value || "custom");
    }

    function renderPresetInput(selected) {
      const safeSelected = selectedPresetValue(selected);
      presetInput.innerHTML = providerOptions(safeSelected);
      presetInput.value = safeSelected;
    }

    function renderProtocolInput(servicePreset, selected = "") {
      const service = selectedPresetValue(servicePreset);
      protocolInput.innerHTML = protocolOptionsForPreset(service, selected);
      const preset = providerPresets[service] || providerPresets.custom;
      protocolInput.value = String(selected || preset.defaultProtocol || "");
    }'''
text = replace_once(text, old_selected, new_selected, "service/protocol renderers")

old_render = '''    function renderThinkingInput(rawCapability = null, selected = "") {
      const capability = normalizedReasoningCapability(rawCapability);
      thinkingInput.innerHTML = reasoningOptions(capability, selected);
      const active = selected || capability.configuredMode || capability.defaultMode || "off";
      thinkingInput.value = capability.supported ? active : "off";
      thinkingInput.disabled = !capability.supported;
      thinkingHint.textContent = capability.supported
        ? "设置按当前服务商、接口地址和模型单独保存；思考过程不会显示到聊天正文。"
        : "该模型没有已验证的可配置思考参数；不会向接口发送猜测字段。";
    }'''
new_render = '''    function renderThinkingInput(rawCapability = null, selected = "") {
      const capability = normalizedReasoningCapability(rawCapability);
      if (capability.rawOptional) {
        thinkingInput.style.display = "none";
        thinkingInput.disabled = true;
        thinkingRawInput.style.display = "";
        thinkingRawInput.disabled = false;
        thinkingRawInput.value = selected || capability.configuredMode || "";
        thinkingHint.textContent = "未知模型：可选填写原始思考模式；留空时不会发送 thinking/reasoning 控制字段。";
        return;
      }
      thinkingRawInput.style.display = "none";
      thinkingRawInput.disabled = true;
      thinkingInput.style.display = "";
      thinkingInput.innerHTML = reasoningOptions(capability, selected);
      const active = selected || capability.configuredMode || capability.defaultMode || "off";
      thinkingInput.value = capability.supported ? active : "off";
      thinkingInput.disabled = !capability.supported;
      thinkingHint.textContent = capability.supported
        ? "设置按当前服务商、接口协议、接口地址和模型单独保存；思考过程不会显示到聊天正文。"
        : "该模型没有已验证的可配置思考参数；不会向接口发送猜测字段。";
    }'''
text = replace_once(text, old_render, new_render, "thinking renderer")

old_load = '''      const matchedProvider = String(settings.modelMatchedProvider || settings.modelProviderMatch?.provider || "").trim();
      const selected = selectedPresetValue(matchedProvider) || selectedPresetValue(settings.modelService);
      const keepDraft = keepModelDraft();
      renderPresetInput(keepDraft ? presetInput.value : selected);
      if (!keepDraft) {
        providerInput.value = settings.modelProvider || "";
        modelInput.value = settings.modelName || "";
        baseUrlInput.value = settings.modelBaseUrl || "";'''
new_load = '''      const matchedProvider = String(settings.modelMatchedProvider || settings.modelProviderMatch?.provider || "").trim();
      const selected = selectedPresetValue(settings.service_preset || settings.modelService || matchedProvider || "custom");
      const keepDraft = keepModelDraft();
      renderPresetInput(keepDraft ? presetInput.value : selected);
      if (!keepDraft) {
        renderProtocolInput(selected, settings.protocol_family || settings.modelProtocol || "");
        providerInput.value = settings.provider_identity || settings.modelProvider || providerPresets[selected]?.provider || "custom";
        modelInput.value = settings.model_name || settings.modelName || "";
        baseUrlInput.value = settings.base_url || settings.modelBaseUrl || "";
        baseUrlInput.dataset.userEdited = "false";'''
text = replace_once(text, old_load, new_load, "initial model load")

old_refresh = '''      if (!modelFormDirty) {
        renderPresetInput(selectedPresetValue(matchedProvider));
        providerInput.value = data.configured_provider || "";
        modelInput.value = data.configured_model_name || "";
        baseUrlInput.value = data.configured_base_url || "";
        apiKeyInput.value = isCredentialConfigured(data.credential_state || data.api_key || "") ? MASKED_API_KEY : "";
        renderThinkingInput(data.reasoning, data.reasoning?.configured_mode || data.reasoning?.effective_mode || "");
      }'''
new_refresh = '''      if (!modelFormDirty) {
        const service = selectedPresetValue(data.service_preset || data.modelService || matchedProvider || "custom");
        renderPresetInput(service);
        renderProtocolInput(service, data.protocol_family || data.modelProtocol || "");
        providerInput.value = data.provider_identity || data.configured_provider || providerPresets[service]?.provider || "custom";
        modelInput.value = data.model_name || data.configured_model_name || "";
        baseUrlInput.value = data.base_url || data.configured_base_url || "";
        baseUrlInput.dataset.userEdited = "false";
        apiKeyInput.value = isCredentialConfigured(data.credential_state || data.api_key || "") ? MASKED_API_KEY : "";
        renderThinkingInput(data.reasoning, data.reasoning?.configured_mode || data.reasoning?.effective_mode || "");
      }'''
text = replace_once(text, old_refresh, new_refresh, "refresh model load")

old_change = '''    presetInput.addEventListener("change", () => {
      modelFormDirty = true;
      const row = presetRowById(currentPresetRows, presetInput.value);
      providerInput.value = row?.provider || "";
      modelInput.value = row?.model || "";
      baseUrlInput.value = row?.baseUrl || "";
      apiKeyInput.value = isCredentialConfigured(row?.credentialState) ? MASKED_API_KEY : "";
      renderThinkingInput(row?.reasoning);
      setPill(modelSaveState, "待保存", "warn");
    });'''
new_change = '''    presetInput.addEventListener("change", () => {
      modelFormDirty = true;
      const service = selectedPresetValue(presetInput.value);
      const preset = providerPresets[service] || providerPresets.custom;
      providerInput.value = preset.provider || "custom";
      renderProtocolInput(service, preset.defaultProtocol || "");
      if (!modelInput.value.trim() && preset.model) modelInput.value = preset.model;
      if (baseUrlInput.dataset.userEdited !== "true") {
        baseUrlInput.value = resolvePresetBaseUrl(service, protocolInput.value);
      }
      renderThinkingInput(preset.thinking || null);
      setPill(modelSaveState, "待保存", "warn");
    });

    protocolInput.addEventListener("change", () => {
      modelFormDirty = true;
      if (baseUrlInput.dataset.userEdited !== "true") {
        baseUrlInput.value = resolvePresetBaseUrl(presetInput.value, protocolInput.value);
      }
      setPill(modelSaveState, "待保存", "warn");
    });

    baseUrlInput.addEventListener("input", () => {
      baseUrlInput.dataset.userEdited = "true";
    });'''
text = replace_once(text, old_change, new_change, "preset/protocol change handlers")

old_save = '''        const keyValue = isMaskedApiKey(apiKeyInput.value) ? "" : apiKeyInput.value.trim();
        const selectedPreset = selectedPresetValue(presetInput.value);
        const selectedRow = presetRowById(currentPresetRows, selectedPreset);
        const saved = await actions.saveSettings({
          modelService: selectedPreset || "custom",
          modelProvider: providerInput.value.trim() || selectedRow?.provider || "",
          modelName: modelInput.value.trim() || selectedRow?.model || "",
          modelBaseUrl: baseUrlInput.value.trim() || selectedRow?.baseUrl || "",
          modelThinkingEnabled: !["off", "none", ""].includes(thinkingInput.value),
          modelThinkingDepth: thinkingInput.value,
          ...(keyValue ? { modelApiKey: keyValue } : {})'''
new_save = '''        const keyValue = isMaskedApiKey(apiKeyInput.value) ? "" : apiKeyInput.value.trim();
        const selectedPreset = selectedPresetValue(presetInput.value);
        const selectedConfig = providerPresets[selectedPreset] || providerPresets.custom;
        const reasoningValue = thinkingRawInput.style.display !== "none" ? thinkingRawInput.value.trim() : thinkingInput.value;
        const saved = await actions.saveSettings({
          modelService: selectedPreset,
          service_preset: selectedPreset,
          modelProvider: providerInput.value.trim() || selectedConfig.provider || "custom",
          provider_identity: providerInput.value.trim() || selectedConfig.provider || "custom",
          modelProtocol: protocolInput.value,
          protocol_family: protocolInput.value,
          modelName: modelInput.value.trim() || selectedConfig.model || "",
          model_name: modelInput.value.trim() || selectedConfig.model || "",
          modelBaseUrl: baseUrlInput.value.trim(),
          base_url: baseUrlInput.value.trim(),
          modelThinkingEnabled: !["off", "none", ""].includes(reasoningValue),
          modelThinkingDepth: reasoningValue,
          reasoning_mode: reasoningValue,
          ...(keyValue ? { modelApiKey: keyValue } : {})'''
text = replace_once(text, old_save, new_save, "model save payload")

PATH.write_text(text, encoding="utf-8")
print("P18.1 Stage D renderer migration applied")
