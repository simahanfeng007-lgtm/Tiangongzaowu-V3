export const providerPresets = {
  openai_compatible: {
    label: "OpenAI compatible",
    provider: "openai",
    baseUrl: "https://api.openai.com/v1",
    model: "gpt-5.6",
    thinking: {
      supported: false,
      modes: [],
      defaultDepth: "",
    },
  },
  deepseek_v4: {
    label: "DeepSeek V4",
    provider: "deepseek_v4",
    baseUrl: "https://api.deepseek.com/v1",
    model: "deepseek-v4-pro",
    thinking: {
      supported: true,
      modes: [
        { value: "enabled", label: "enabled" },
        { value: "disabled", label: "disabled" },
      ],
      defaultDepth: "enabled",
    },
  },
  mimo: {
    label: "MiMo",
    provider: "mimo",
    baseUrl: "https://api.xiaomimimo.com/v1",
    model: "mimo-v2.5-pro",
    thinking: {
      supported: true,
      modes: [
        { value: "auto", label: "auto" },
      ],
      defaultDepth: "auto",
    },
  },
  glm_5_2: {
    label: "Z.AI GLM-5.2",
    provider: "glm_5_2",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    model: "glm-5.2",
    thinking: {
      supported: true,
      modes: [
        { value: "enabled", label: "enabled" },
        { value: "disabled", label: "disabled" },
      ],
      defaultDepth: "enabled",
    },
  },
  minimax_m3: {
    label: "MiniMax M3",
    provider: "minimax_m3",
    baseUrl: "https://api.minimaxi.com/v1",
    model: "MiniMax-M3",
    thinking: {
      supported: true,
      modes: [
        { value: "auto", label: "auto" },
      ],
      defaultDepth: "auto",
    },
  },
  gpt_5_6: {
    label: "OpenAI GPT-5.6",
    provider: "gpt_5_6",
    baseUrl: "https://api.openai.com/v1",
    model: "gpt-5.6",
    thinking: {
      supported: false,
      modes: [],
      defaultDepth: "",
    },
  },
};

const unsupportedThinking = {
  supported: false,
  modes: [],
  defaultDepth: "",
};

const customProviderPreset = {
  label: "Custom",
  provider: "",
  baseUrl: "",
  model: "",
  thinking: unsupportedThinking,
};

const providerThinkingFallbacks = {
  deepseek: providerPresets.deepseek_v4.thinking,
  deepseek_v4: providerPresets.deepseek_v4.thinking,
  zhipu: providerPresets.glm_5_2.thinking,
  glm: providerPresets.glm_5_2.thinking,
  glm_5_1: providerPresets.glm_5_2.thinking,
  glm_5_2: providerPresets.glm_5_2.thinking,
  minimax: providerPresets.minimax_m3.thinking,
  minimax_m3: providerPresets.minimax_m3.thinking,
  mimo: providerPresets.mimo.thinking,
  openrouter: {
    supported: true,
    modes: [
      { value: "low", label: "low" },
      { value: "medium", label: "medium" },
      { value: "high", label: "high" },
      { value: "xhigh", label: "xhigh" },
    ],
    defaultDepth: "high",
  },
};

export function providerOptions(selected = "custom") {
  return Object.entries(providerPresets)
    .map(([value, preset]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${preset.label}</option>`)
    .join("");
}

function modelProfileForService(settings, presetId) {
  const profiles = settings && typeof settings.modelProviderProfiles === "object" && !Array.isArray(settings.modelProviderProfiles)
    ? settings.modelProviderProfiles
    : {};
  const profile = profiles[presetId];
  return profile && typeof profile === "object" && !Array.isArray(profile) ? profile : null;
}

export function applyProviderPreset(settings, presetId) {
  const preset = providerPresets[presetId] || customProviderPreset;
  const serviceId = providerPresets[presetId] ? presetId : "custom";
  const profile = modelProfileForService(settings, presetId);
  const provider = profile?.modelProvider || preset.provider;
  const thinking = providerThinkingCapability(serviceId, provider);
  return {
    ...settings,
    modelService: serviceId,
    modelProvider: provider,
    modelBaseUrl: profile?.modelBaseUrl ?? preset.baseUrl,
    modelName: profile?.modelName ?? preset.model,
    modelApiKey: "",
    modelThinkingEnabled: Boolean(profile?.modelThinkingEnabled) && Boolean(thinking.supported),
    modelThinkingDepth: profile?.modelThinkingDepth || thinking.defaultDepth || "",
    modelMultimodalInput: profile?.modelMultimodalInput || settings?.modelMultimodalInput || "auto",
    modelImageInput: profile?.modelImageInput || settings?.modelImageInput || "auto",
    modelVideoInput: profile?.modelVideoInput || settings?.modelVideoInput || "auto",
    modelAudioInput: profile?.modelAudioInput || settings?.modelAudioInput || "auto",
    webSearchProvider: profile?.webSearchProvider || settings?.webSearchProvider || "auto",
    imageGenerationMode: profile?.imageGenerationMode || settings?.imageGenerationMode || "auto",
  };
}

export function providerThinkingCapability(serviceId = "custom", providerId = "") {
  const service = String(serviceId || "").trim();
  const provider = String(providerId || "").trim().toLowerCase();
  if (provider === "openai" || provider === "openai_compatible" || provider === "gpt_5_6") {
    return unsupportedThinking;
  }
  const preset = providerPresets[service];
  if (preset?.thinking?.supported) return preset.thinking;
  return providerThinkingFallbacks[provider] || unsupportedThinking;
}
