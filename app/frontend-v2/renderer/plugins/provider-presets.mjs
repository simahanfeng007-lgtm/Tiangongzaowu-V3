export const PROTOCOL_FAMILIES = Object.freeze({
  OPENAI_CHAT_COMPLETIONS: "openai_chat_completions",
  OPENAI_RESPONSES: "openai_responses",
  ANTHROPIC_MESSAGES: "anthropic_messages",
});

const thinking = Object.freeze({
  gpt: {
    supported: true,
    modes: ["off", "minimal", "low", "medium", "high", "xhigh"].map((value) => ({ value, label: value })),
    defaultDepth: "medium",
  },
  deepseek: {
    supported: true,
    modes: [
      { value: "off", label: "关闭" },
      { value: "high", label: "高" },
      { value: "max", label: "最大" },
    ],
    defaultDepth: "high",
  },
  glm: {
    supported: true,
    modes: ["off", "minimal", "low", "medium", "high", "xhigh", "max"].map((value) => ({ value, label: value })),
    defaultDepth: "high",
  },
  minimax: {
    supported: true,
    modes: [{ value: "off", label: "关闭" }, { value: "auto", label: "自动" }],
    defaultDepth: "auto",
  },
  mimo: {
    supported: true,
    modes: [{ value: "off", label: "关闭" }, { value: "on", label: "开启" }],
    defaultDepth: "on",
  },
  kimi: {
    supported: true,
    modes: ["low", "high", "max"].map((value) => ({ value, label: value })),
    defaultDepth: "max",
  },
});

const rawOptionalThinking = Object.freeze({
  supported: true,
  rawOptional: true,
  control: "raw_optional",
  modes: [],
  defaultDepth: "",
});

const unsupportedThinking = Object.freeze({
  supported: false,
  modes: [],
  defaultDepth: "",
});

export const providerPresets = Object.freeze({
  openai: {
    label: "OpenAI",
    provider: "openai",
    protocols: [PROTOCOL_FAMILIES.OPENAI_RESPONSES, PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS],
    defaultProtocol: PROTOCOL_FAMILIES.OPENAI_RESPONSES,
    baseUrls: {
      [PROTOCOL_FAMILIES.OPENAI_RESPONSES]: "https://api.openai.com/v1",
      [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS]: "https://api.openai.com/v1",
    },
    model: "gpt-5.6",
    thinking: thinking.gpt,
  },
  deepseek: {
    label: "DeepSeek",
    provider: "deepseek",
    protocols: [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS],
    defaultProtocol: PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS,
    baseUrls: { [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS]: "https://api.deepseek.com/v1" },
    model: "deepseek-v4-pro",
    thinking: thinking.deepseek,
  },
  zhipu: {
    label: "智谱 AI",
    provider: "zhipu",
    protocols: [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS],
    defaultProtocol: PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS,
    baseUrls: { [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS]: "https://open.bigmodel.cn/api/paas/v4" },
    model: "glm-5.2",
    thinking: thinking.glm,
  },
  minimax: {
    label: "MiniMax",
    provider: "minimax",
    protocols: [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS],
    defaultProtocol: PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS,
    baseUrls: { [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS]: "https://api.minimaxi.com/v1" },
    model: "MiniMax-M3",
    thinking: thinking.minimax,
  },
  mimo: {
    label: "MiMo",
    provider: "mimo",
    protocols: [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS],
    defaultProtocol: PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS,
    baseUrls: { [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS]: "https://api.xiaomimimo.com/v1" },
    model: "mimo-v2.5-pro",
    thinking: thinking.mimo,
  },
  kimi: {
    label: "Kimi",
    provider: "kimi",
    protocols: [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS],
    defaultProtocol: PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS,
    baseUrls: { [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS]: "https://api.moonshot.cn/v1" },
    model: "kimi-k3",
    thinking: thinking.kimi,
  },
  scnet: {
    label: "SCNet",
    provider: "scnet",
    protocols: [
      PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS,
      PROTOCOL_FAMILIES.OPENAI_RESPONSES,
      PROTOCOL_FAMILIES.ANTHROPIC_MESSAGES,
    ],
    defaultProtocol: PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS,
    baseUrls: {
      [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS]: "https://api.scnet.cn/api/llm/v1",
      [PROTOCOL_FAMILIES.OPENAI_RESPONSES]: "https://api.scnet.cn/api/llm/v1",
      [PROTOCOL_FAMILIES.ANTHROPIC_MESSAGES]: "https://api.scnet.cn/api/llm/anthropic",
    },
    model: "",
    thinking: rawOptionalThinking,
  },
  generic_openai: {
    label: "通用 OpenAI 兼容接口",
    provider: "custom",
    protocols: [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS, PROTOCOL_FAMILIES.OPENAI_RESPONSES],
    defaultProtocol: PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS,
    baseUrls: {},
    model: "",
    thinking: rawOptionalThinking,
  },
  generic_anthropic: {
    label: "通用 Anthropic 兼容接口",
    provider: "custom",
    protocols: [PROTOCOL_FAMILIES.ANTHROPIC_MESSAGES],
    defaultProtocol: PROTOCOL_FAMILIES.ANTHROPIC_MESSAGES,
    baseUrls: {},
    model: "",
    thinking: rawOptionalThinking,
  },
  custom: {
    label: "自定义",
    provider: "custom",
    protocols: [
      PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS,
      PROTOCOL_FAMILIES.OPENAI_RESPONSES,
      PROTOCOL_FAMILIES.ANTHROPIC_MESSAGES,
    ],
    defaultProtocol: "",
    baseUrls: {},
    model: "",
    thinking: rawOptionalThinking,
  },
});

const legacyPresetAliases = Object.freeze({
  openai_compatible: "openai",
  gpt_5_6: "openai",
  deepseek_v4: "deepseek",
  glm_5_2: "zhipu",
  minimax_m3: "minimax",
  kimi_k3: "kimi",
});

export function normalizeServicePreset(value = "custom") {
  const raw = String(value || "").trim().toLowerCase();
  const normalized = legacyPresetAliases[raw] || raw;
  return Object.prototype.hasOwnProperty.call(providerPresets, normalized) ? normalized : "custom";
}

export function providerOptions(selected = "custom") {
  const normalized = normalizeServicePreset(selected);
  return Object.entries(providerPresets)
    .map(([value, preset]) => `<option value="${value}" ${value === normalized ? "selected" : ""}>${preset.label}</option>`)
    .join("");
}

export function protocolOptionsForPreset(presetId = "custom", selected = "") {
  const service = normalizeServicePreset(presetId);
  const preset = providerPresets[service];
  const effective = String(selected || preset.defaultProtocol || "");
  const labels = {
    [PROTOCOL_FAMILIES.OPENAI_CHAT_COMPLETIONS]: "OpenAI Chat Completions",
    [PROTOCOL_FAMILIES.OPENAI_RESPONSES]: "OpenAI Responses",
    [PROTOCOL_FAMILIES.ANTHROPIC_MESSAGES]: "Anthropic Messages",
  };
  const empty = service === "custom" ? '<option value="">请选择接口协议</option>' : "";
  return empty + preset.protocols
    .map((value) => `<option value="${value}" ${value === effective ? "selected" : ""}>${labels[value] || value}</option>`)
    .join("");
}

export function resolvePresetBaseUrl(presetId = "custom", protocolFamily = "") {
  const service = normalizeServicePreset(presetId);
  const preset = providerPresets[service];
  const protocol = String(protocolFamily || preset.defaultProtocol || "");
  return preset.baseUrls?.[protocol] || "";
}

function modelProfileForService(settings, presetId) {
  const profiles = settings && typeof settings.modelProviderProfiles === "object" && !Array.isArray(settings.modelProviderProfiles)
    ? settings.modelProviderProfiles
    : {};
  const profile = profiles[presetId];
  return profile && typeof profile === "object" && !Array.isArray(profile) ? profile : null;
}

// 思考能力只由实际模型名决定：供应商名称不参与判定。
const modelThinkingRules = Object.freeze([
  { prefixes: ["deepseek-v4", "deepseek_v4"], thinking: thinking.deepseek },
  { prefixes: ["minimax-m3", "minimax_m3"], thinking: thinking.minimax },
  { prefixes: ["mimo-v2.5", "mimo_v2.5", "xiaomi-mimo-v2.5"], thinking: thinking.mimo },
  { prefixes: ["glm-5.2", "glm_5_2"], thinking: thinking.glm },
  { prefixes: ["kimi-k3", "kimi_k3"], thinking: thinking.kimi },
  { prefixes: ["gpt-5", "gpt_5", "o1", "o3", "o4"], thinking: thinking.gpt },
]);

export function modelThinkingCapability(modelName = "") {
  const model = String(modelName || "").trim().toLowerCase();
  return modelThinkingRules.find((rule) => rule.prefixes.some((prefix) => model.startsWith(prefix)))?.thinking
    || unsupportedThinking;
}

export function applyProviderPreset(settings, presetId, { preserveBaseUrl = false, protocolFamily = "" } = {}) {
  const serviceId = normalizeServicePreset(presetId);
  const preset = providerPresets[serviceId];
  const profile = modelProfileForService(settings, serviceId);
  const protocol = String(
    protocolFamily
    || profile?.protocol_family
    || profile?.modelProtocol
    || preset.defaultProtocol
    || ""
  );
  const provider = String(profile?.provider_identity || profile?.modelProvider || preset.provider || "custom");
  const baseUrl = preserveBaseUrl
    ? String(settings?.modelBaseUrl || "")
    : String(profile?.base_url || profile?.modelBaseUrl || resolvePresetBaseUrl(serviceId, protocol) || "");
  const model = String(profile?.model_name ?? profile?.modelName ?? preset.model ?? "");
  const modelCapability = modelThinkingCapability(model);
  const thinkingCapability = profile?.reasoning
    || (modelCapability.supported ? modelCapability : (preset.thinking || rawOptionalThinking));
  return {
    ...settings,
    modelService: serviceId,
    service_preset: serviceId,
    modelProvider: provider,
    provider_identity: provider,
    modelProtocol: protocol,
    protocol_family: protocol,
    modelBaseUrl: baseUrl,
    modelName: model,
    modelApiKey: "",
    modelThinkingEnabled: profile?.reasoning
      ? profile.reasoning.enabled !== false
      : Boolean(profile?.modelThinkingEnabled) || Boolean(thinkingCapability?.supported),
    modelThinkingDepth: profile?.reasoning?.configured_mode
      || profile?.reasoning?.effective_mode
      || profile?.modelThinkingDepth
      || thinkingCapability?.defaultDepth
      || "",
    modelThinkingCapability: thinkingCapability,
    modelMultimodalInput: profile?.modelMultimodalInput || settings?.modelMultimodalInput || "auto",
    modelImageInput: profile?.modelImageInput || settings?.modelImageInput || "auto",
    modelVideoInput: profile?.modelVideoInput || settings?.modelVideoInput || "auto",
    modelAudioInput: profile?.modelAudioInput || settings?.modelAudioInput || "auto",
    webSearchProvider: profile?.webSearchProvider || settings?.webSearchProvider || "auto",
    imageGenerationMode: profile?.imageGenerationMode || settings?.imageGenerationMode || "auto",
  };
}

export function providerThinkingCapability(serviceId = "custom", providerId = "", capability = null) {
  if (capability && typeof capability === "object") return capability;
  const service = normalizeServicePreset(serviceId);
  const provider = String(providerId || "").trim().toLowerCase();
  const preset = providerPresets[service];
  if (preset?.thinking) return preset.thinking;
  if (provider.includes("deepseek")) return thinking.deepseek;
  if (provider.includes("glm") || provider.includes("zhipu")) return thinking.glm;
  if (provider.includes("minimax")) return thinking.minimax;
  if (provider.includes("mimo")) return thinking.mimo;
  if (provider.includes("gpt") || provider === "openai") return thinking.gpt;
  return rawOptionalThinking;
}
