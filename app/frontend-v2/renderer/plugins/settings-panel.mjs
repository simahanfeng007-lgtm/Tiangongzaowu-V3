import { qrTextToSvgDataUrl } from "../core/qr-code.mjs";
import { providerPresets } from "./provider-presets.mjs";

const DEFAULT_WECHAT_DIRECT = {
  enabled: false,
  base_url: "https://ilinkai.weixin.qq.com",
  bot_type: "3",
  bot_token: "",
  account_id: "",
  auto_reply: true
};

const DEFAULT_WECHAT_CALLBACK = {
  enabled: false,
  provider: "official_account",
  host: "127.0.0.1",
  port: 7188,
  path: "/wechat/callback",
  token: "",
  encoding_aes_key: "",
  receive_id: ""
};

const DEFAULT_FEISHU = {
  enabled: false,
  mode: "long_connection",
  app_id: "",
  app_secret: "",
  verification_token: "",
  encrypt_key: ""
};

// The embedded gateway supervisor normally restores a stopped process within a
// few seconds, but cold Windows hosts and antivirus scanning can extend that
// window.  UI preference writes are idempotent, so a bounded 15.5 s backoff is
// safer than surfacing a transient restart as a failed product action.
const SETTINGS_NETWORK_RETRY_DELAYS_MS = Object.freeze([500, 1000, 2000, 4000, 8000]);

export async function runSettingsMutationWithRecovery(
  operation,
  {
    retryDelaysMs = SETTINGS_NETWORK_RETRY_DELAYS_MS,
    wait = (ms) => new Promise((resolve) => globalThis.setTimeout(resolve, ms)),
    onRetry = () => {},
  } = {},
) {
  let retryIndex = 0;
  while (true) {
    try {
      return await operation();
    } catch (error) {
      const retryDelay = Number(retryDelaysMs[retryIndex]);
      if (error?.code !== "network_error" || !Number.isFinite(retryDelay) || retryDelay < 0) {
        throw error;
      }
      retryIndex += 1;
      onRetry({ attempt: retryIndex, delayMs: retryDelay, error });
      await wait(retryDelay);
    }
  }
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function providerPresetRows(settings = {}) {
  const rows = [];
  const seen = new Set();
  const seenProviders = new Set();
  const profiles = settings.modelProviderProfiles && typeof settings.modelProviderProfiles === "object" && !Array.isArray(settings.modelProviderProfiles)
    ? settings.modelProviderProfiles
    : {};
  const backendRows = Array.isArray(settings.modelProviderPresets) ? settings.modelProviderPresets : [];
  for (const item of backendRows) {
    if (!item || typeof item !== "object") continue;
    const id = String(item.id || item.provider_id || "").trim();
    const provider = String(item.provider_id || item.id || "").trim();
    if (!id || seen.has(id)) continue;
    const profile = profiles[provider] || profiles[id] || {};
    seen.add(id);
    if (provider) seenProviders.add(provider);
    rows.push({
      id,
      label: String(item.display_name || id).trim(),
      provider: String(profile.provider || item.configured_provider || provider).trim(),
      model: String(profile.configured_model_name || profile.model_name || item.configured_model_name || item.default_model || item.model || "").trim(),
      baseUrl: String(profile.configured_base_url || profile.base_url || item.configured_base_url || item.base_url || "").trim(),
      credentialState: String(profile.credential_state || item.credential_state || "").trim(),
    });
  }
  for (const [id, preset] of Object.entries(providerPresets)) {
    if (seen.has(id)) continue;
    if (id === "openai_compatible" && seenProviders.has("gpt_5_5")) continue;
    seen.add(id);
    if (preset.provider) seenProviders.add(preset.provider);
    rows.push({
      id,
      label: preset.label || id,
      provider: preset.provider || id,
      model: preset.model || "",
      baseUrl: preset.baseUrl || "",
      credentialState: String((profiles[preset.provider] || profiles[id] || {}).credential_state || "").trim(),
    });
  }
  return rows;
}

function presetOptions(rows, selected = "") {
  return [
    `<option value="">自动匹配 / 手动输入</option>`,
    ...rows.map((item) => `<option value="${esc(item.id)}" ${item.id === selected ? "selected" : ""}>${esc(item.label)}</option>`),
  ].join("");
}

function safeJson(text) {
  try {
    return JSON.parse(String(text || "{}"));
  } catch {
    return {};
  }
}

const MASKED_API_KEY = "※※※※※※※※";

function isMaskedApiKey(value) {
  return /^[*※]+$/.test(String(value || "").trim());
}

function isCredentialConfigured(value) {
  return ["configured", "已配置"].includes(String(value || "").trim());
}

function presetRowById(rows, value) {
  const selected = String(value || "").trim();
  return rows.find((item) => item.id === selected || item.provider === selected) || null;
}

function secretValue(value) {
  return value ? String(value) : "";
}

function linkStateText(link) {
  const state = String(link?.state || "unknown");
  if (state === "disabled") return "关闭";
  if (state === "starting") return "启动中";
  if (state === "running" || state === "ready" || state === "available") return "运行中";
  if (state === "waiting_login") return "等待扫码";
  if (state === "waiting_confirm") return "等待确认";
  if (state === "need_verifycode") return "需要配对数字";
  if (state === "login_expired") return "二维码过期";
  if (state === "missing_credentials") return "等待登录";
  if (state === "missing_dependency") return "缺少依赖";
  if (state === "error") return "异常";
  return state;
}

function formatLinkTime(seconds) {
  const value = Number(seconds || 0);
  if (!value) return "";
  try {
    return new Date(value * 1000).toLocaleTimeString();
  } catch {
    return "";
  }
}

function linkDiagnosticText(link) {
  if (!link || typeof link !== "object") return "";
  const rows = [];
  const receiveAt = formatLinkTime(link.last_receive_at || link.last_message_at);
  if (link.last_receive_preview) rows.push(`最近收到${receiveAt ? ` ${receiveAt}` : ""}: ${link.last_receive_preview}`);
  if (link.last_reply_preview) rows.push(`最近回复: ${link.last_reply_preview}`);
  const send = link.last_send_result;
  if (send && typeof send === "object") {
    if (send.ok) {
      rows.push(`最近发送: 成功，${Number(send.parts || 0)} 段`);
    } else if (send.error) {
      rows.push(`最近发送: 失败，${send.error}`);
    } else if (send.skipped) {
      rows.push(`最近发送: 跳过，${send.skipped}`);
    }
  }
  if (link.last_ignored_reason) {
    rows.push(`最近忽略: ${link.last_ignored_reason}${link.last_ignored_message_type ? ` (${link.last_ignored_message_type})` : ""}`);
  }
  if (Number.isFinite(Number(link.last_poll_message_count))) {
    rows.push(`最近轮询消息数: ${Number(link.last_poll_message_count)}`);
  }
  return rows.join("\n");
}

function actionText(result, fallback = "操作") {
  if (!result) return "";
  if (result.message) return String(result.message);
  if (result.ok) return `${fallback}已完成。`;
  return `${fallback}未完成：${result.error || result.stderr || result.code || "未知错误"}`;
}

function applyTheme(themeStyle) {
  const value = ["ink_teal", "bronze_gear", "jade_light", "cosmos_dark", "ink_wash", "nordic_light"].includes(themeStyle) ? themeStyle : "ink_teal";
  document.documentElement.dataset.theme = value;
  // P2-15: ink_wash/nordic_light are light themes in the core; native
  // controls must follow the same semantics instead of being forced dark.
  document.documentElement.style.colorScheme = ["jade_light", "ink_wash", "nordic_light"].includes(value) ? "light" : "dark";
  try { window.localStorage?.setItem("tiangong-v3-theme", value); } catch {}
  window.tiangongDesktop?.setThemeStyle?.(value).catch?.(() => {});
}

export const settingsPanelPlugin = {
  id: "settings-panel",
  slot: "conversation",
  order: 220,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML("beforeend", `
      <section class="page-panel settings-page" data-page-panel="settings">
        <header class="page-header">
          <div class="title-group">
            <span class="caption">设置</span>
            <h2>系统设置</h2>
          </div>
          <div class="commandbar-meta">
            <button id="settingsRefresh" class="small-command" type="button">刷新后台</button>
          </div>
        </header>

        <section class="page-body settings-body settings-dashboard">
          <div class="settings-primary-stack">
            <section class="panel-card settings-card-model">
              <div class="panel-title">
                <span>模型凭据</span>
                <div class="panel-actions">
                  <span id="modelSaveState" class="mini-pill">未修改</span>
                  <button id="settingsSaveModel" class="small-command" type="button">保存设置</button>
                  <button id="settingsProbeProviderApi" class="small-command" type="button">API 探针</button>
                </div>
              </div>
              <div class="settings-form">
                <label class="field-row">
                  <span>服务预设</span>
                  <select id="settingsModelPreset"></select>
                </label>
                <label class="field-row">
                  <span>服务商</span>
                  <input id="settingsModelProvider" placeholder="openai / deepseek / glm / mimo / custom" />
                </label>
                <label class="field-row">
                  <span>模型名称</span>
                  <input id="settingsModelName" placeholder="provider model id" />
                </label>
                <label class="field-row">
                  <span>接口地址</span>
                  <input id="settingsModelBaseUrl" placeholder="https://api.example.com/v1" />
                </label>
                <label class="field-row">
                  <span>API Key</span>
                  <input id="settingsModelApiKey" type="password" autocomplete="new-password" placeholder="输入后由本机加密凭据系统保存" />
                  <small>已保存的密钥只显示掩码，不会写入前端本地设置。</small>
                </label>
                <button id="settingsDeleteModelKey" class="small-command subtle-command" type="button">删除当前服务商密钥</button>
              </div>
            </section>

            <section class="panel-card settings-card-workspace settings-card-composite">
              <div class="panel-title">
                <span>工作区</span>
                <span class="mini-pill">3 项设置</span>
              </div>

              <div class="settings-composite-list">
                <section class="settings-composite-item" data-settings-group="workspace">
                  <div class="settings-subgroup-title">
                    <span>工作区路径</span>
                    <div class="panel-actions">
                      <span id="workspaceSaveState" class="mini-pill">未修改</span>
                      <button id="settingsSaveWorkspace" class="small-command" type="button">保存</button>
                    </div>
                  </div>
                  <div class="settings-form">
                    <label class="field-row">
                      <span>路径</span>
                      <input id="settingsWorkspaceRoot" spellcheck="false" placeholder="选择或输入工作区目录" />
                    </label>
                    <label class="field-row">
                      <span>写入范围</span>
                      <select id="settingsWorkspaceMode">
                        <option value="workspace">工作区（默认）</option>
                        <option value="full">全盘</option>
                      </select>
                    </label>
                    <div class="settings-actions-row">
                      <button id="settingsChooseWorkspace" class="small-command" type="button">选择目录</button>
                      <button id="settingsOpenWorkspace" class="small-command subtle-command" type="button">打开目录</button>
                    </div>
                  </div>
                </section>

                <section class="settings-composite-item" data-settings-group="permission">
                  <div class="settings-subgroup-title">
                    <span>权限模式</span>
                    <div class="panel-actions">
                      <span id="permissionSaveState" class="mini-pill">未修改</span>
                      <button id="settingsSavePermission" class="small-command" type="button">保存</button>
                    </div>
                  </div>
                  <div class="settings-form">
                    <label class="field-row">
                      <span>模式</span>
                      <select id="settingsPermissionMode">
                        <option value="request_approval">请求批准（仅 A0 自主执行）</option>
                        <option value="auto_approval">替我审批（A0-A2 自主执行）</option>
                        <option value="full_access">完全访问权限（A0-A4 自主执行）</option>
                        <option value="custom">自定义</option>
                      </select>
                      <small>该模式随当前生命标识保存；A5 始终由执行链签名门禁确认。</small>
                    </label>
                    <label id="settingsPermissionRiskRow" class="field-row" hidden>
                      <span>自主行动自动上限</span>
                      <select id="settingsPermissionRiskMax">
                        <option value="A0">A0 · 仅内部整理</option>
                        <option value="A1">A1 · 低风险行动</option>
                        <option value="A2">A2 · 常规可逆行动</option>
                        <option value="A3">A3 · 较高影响行动</option>
                        <option value="A4">A4 · 高影响但非 A5</option>
                      </select>
                      <small>只约束生命自主调度；用户在对话中明确下达的任务仍由当前执行链逐项判定。</small>
                    </label>
                  </div>
                </section>

                <section class="settings-composite-item" data-settings-group="interface">
                  <div class="settings-subgroup-title">
                    <span>界面偏好</span>
                    <span id="uiSaveState" class="mini-pill">未修改</span>
                  </div>
                  <div class="settings-form">
                    <label class="field-row">
                      <span>主题</span>
                      <select id="settingsTheme">
                        <option value="ink_teal">玄墨青绿</option>
                        <option value="bronze_gear">青铜机括</option>
                        <option value="jade_light">玉简浅色</option>
                        <option value="cosmos_dark">星渊宇宙</option>
                        <option value="ink_wash">宣墨国风</option>
                        <option value="nordic_light">素欧简约</option>
                      </select>
                    </label>
                    <button id="settingsSaveUi" class="block-command" type="button">保存界面偏好</button>
                  </div>
                </section>
              </div>
            </section>

          </div>

          <section class="panel-card settings-card-links">
            <div class="panel-title">
              <span>通信连接</span>
              <div class="panel-actions">
                <span id="linkSaveState" class="mini-pill">未读取</span>
                <button id="settingsRefreshLinks" class="small-command" type="button">刷新</button>
                <button id="settingsSaveLinks" class="small-command" type="button">应用微信状态</button>
              </div>
            </div>

            <details class="link-group" open>
              <summary class="settings-subtitle">微信 Bot</summary>
              <div class="settings-form compact-link-form">
                <label class="field-row">
                  <span>启用微信</span>
                  <select id="linkWechatEnabled">
                    <option value="false">关闭</option>
                    <option value="true">开启</option>
                  </select>
                </label>
                <label class="field-row">
                  <span>Bot Token</span>
                  <input id="linkWechatBotToken" type="text" disabled placeholder="由扫码登录安全写入，不在界面显示" />
                </label>
                <label class="field-row">
                  <span>Bot ID</span>
                  <input id="linkWechatAccountId" disabled placeholder="由扫码登录自动绑定" />
                </label>
                <label class="field-row">
                  <span>配对数字</span>
                  <input id="linkWechatVerifyCode" inputmode="numeric" autocomplete="one-time-code" placeholder="手机微信要求时填写" />
                </label>
                <div class="settings-actions-row">
                  <button id="linkWechatLoginStart" class="small-command" type="button">生成登录二维码</button>
                  <button id="linkWechatLoginWait" class="small-command" type="button">确认登录状态</button>
                  <button id="linkWechatStart" class="small-command" type="button">启动连接</button>
                  <button id="linkWechatStop" class="small-command subtle-command" type="button">停止连接</button>
                </div>
                <div class="channel-connect-detail">连接顺序：生成二维码 → 微信扫码 → 确认登录状态；登录成功后再启动连接。</div>
                <div id="linkWechatQrWrap" class="wechat-qr-preview" hidden>
                  <img id="linkWechatQrImage" alt="微信登录二维码" />
                  <div id="linkWechatQrText" class="channel-connect-detail"></div>
                </div>
                <div id="linkWechatDiagnostics" class="channel-connect-detail link-diagnostics" hidden></div>
              </div>
            </details>

            <details class="link-group">
              <summary class="settings-subtitle">飞书（桌面安全绑定尚未开放）</summary>
              <div class="settings-form compact-link-form">
                <label class="field-row">
                  <span>启用飞书</span>
                  <select id="linkFeishuEnabled" disabled>
                    <option value="false">关闭</option>
                    <option value="true">开启</option>
                  </select>
                </label>
                <label class="field-row">
                  <span>App ID</span>
                  <input id="linkFeishuAppId" disabled placeholder="不接受不完整的应用凭据" />
                </label>
                <label class="field-row">
                  <span>App Secret</span>
                  <input id="linkFeishuAppSecret" type="password" disabled placeholder="请等待安全绑定流程" />
                </label>
              </div>
            </details>

            <pre id="linkActionOutput" class="link-action-output"></pre>
          </section>
        </section>
      </section>
    `);

    const panel = slot.querySelector('[data-page-panel="settings"]');
    const refresh = panel.querySelector("#settingsRefresh");
    const modelSaveState = panel.querySelector("#modelSaveState");
    const saveModel = panel.querySelector("#settingsSaveModel");
    const probeProviderApi = panel.querySelector("#settingsProbeProviderApi");
    const deleteModelKey = panel.querySelector("#settingsDeleteModelKey");
    const presetInput = panel.querySelector("#settingsModelPreset");
    const providerInput = panel.querySelector("#settingsModelProvider");
    const modelInput = panel.querySelector("#settingsModelName");
    const baseUrlInput = panel.querySelector("#settingsModelBaseUrl");
    const apiKeyInput = panel.querySelector("#settingsModelApiKey");
    const workspaceSaveState = panel.querySelector("#workspaceSaveState");
    const workspaceInput = panel.querySelector("#settingsWorkspaceRoot");
    const workspaceMode = panel.querySelector("#settingsWorkspaceMode");
    const chooseWorkspaceButton = panel.querySelector("#settingsChooseWorkspace");
    const saveWorkspace = panel.querySelector("#settingsSaveWorkspace");
    const openWorkspaceButton = panel.querySelector("#settingsOpenWorkspace");
    const permissionSaveState = panel.querySelector("#permissionSaveState");
    const permissionModeInput = panel.querySelector("#settingsPermissionMode");
    const permissionRiskRow = panel.querySelector("#settingsPermissionRiskRow");
    const permissionRiskInput = panel.querySelector("#settingsPermissionRiskMax");
    const savePermission = panel.querySelector("#settingsSavePermission");
    const uiSaveState = panel.querySelector("#uiSaveState");
    const themeInput = panel.querySelector("#settingsTheme");
    const saveUi = panel.querySelector("#settingsSaveUi");
    const linkSaveState = panel.querySelector("#linkSaveState");
    const refreshLinks = panel.querySelector("#settingsRefreshLinks");
    const saveLinks = panel.querySelector("#settingsSaveLinks");
    const linkActionOutput = panel.querySelector("#linkActionOutput");
    const linkWechatEnabled = panel.querySelector("#linkWechatEnabled");
    const linkWechatBotToken = panel.querySelector("#linkWechatBotToken");
    const linkWechatAccountId = panel.querySelector("#linkWechatAccountId");
    const linkWechatVerifyCode = panel.querySelector("#linkWechatVerifyCode");
    const linkWechatQrWrap = panel.querySelector("#linkWechatQrWrap");
    const linkWechatQrImage = panel.querySelector("#linkWechatQrImage");
    const linkWechatQrText = panel.querySelector("#linkWechatQrText");
    const linkWechatDiagnostics = panel.querySelector("#linkWechatDiagnostics");
    const linkFeishuEnabled = panel.querySelector("#linkFeishuEnabled");
    const linkFeishuAppId = panel.querySelector("#linkFeishuAppId");
    const linkFeishuAppSecret = panel.querySelector("#linkFeishuAppSecret");
    const linkButtons = {
      wechat_direct_login_start: panel.querySelector("#linkWechatLoginStart"),
      wechat_direct_login_wait: panel.querySelector("#linkWechatLoginWait"),
      wechat_direct_start: panel.querySelector("#linkWechatStart"),
      wechat_direct_stop: panel.querySelector("#linkWechatStop")
    };
    let activeWechatSessionKey = "";
    let lastLinkSettings = {};
    let currentPresetRows = providerPresetRows();
    let modelFormDirty = false;

    function setPill(node, label, className = "") {
      node.textContent = label;
      node.className = `mini-pill ${className}`.trim();
    }

    function selectedPresetValue(value) {
      const selected = String(value || "").trim();
      return currentPresetRows.some((item) => item.id === selected) ? selected : "";
    }

    function renderPresetInput(selected) {
      const safeSelected = selectedPresetValue(selected);
      presetInput.innerHTML = presetOptions(currentPresetRows, safeSelected);
      presetInput.value = safeSelected;
    }

    function keepModelDraft() {
      return modelFormDirty && [presetInput, providerInput, modelInput, baseUrlInput, apiKeyInput].includes(document.activeElement);
    }

    function renderPermissionFields(mode, riskMax = "A4") {
      const validModes = new Set(["request_approval", "auto_approval", "full_access", "custom"]);
      const validRiskLevels = new Set(["A0", "A1", "A2", "A3", "A4"]);
      permissionModeInput.value = validModes.has(String(mode || "")) ? String(mode) : "full_access";
      permissionRiskInput.value = validRiskLevels.has(String(riskMax || "").toUpperCase()) ? String(riskMax).toUpperCase() : "A4";
      permissionRiskRow.hidden = permissionModeInput.value !== "custom";
    }

    function renderPage(page) {
      panel.classList.toggle("active", page === "settings");
      if (page === "settings") {
        actions.refreshStatus?.();
        actions.refreshConfig?.();
        loadLinks();
      }
    }

    function renderSettings(settings) {
      currentPresetRows = providerPresetRows(settings);
      const matchedProvider = String(settings.modelMatchedProvider || settings.modelProviderMatch?.provider || "").trim();
      const selected = selectedPresetValue(matchedProvider) || selectedPresetValue(settings.modelService);
      const keepDraft = keepModelDraft();
      renderPresetInput(keepDraft ? presetInput.value : selected);
      if (!keepDraft) {
        providerInput.value = settings.modelProvider || "";
        modelInput.value = settings.modelName || "";
        baseUrlInput.value = settings.modelBaseUrl || "";
        const activeRow = presetRowById(currentPresetRows, selected) || presetRowById(currentPresetRows, matchedProvider);
        apiKeyInput.value = isCredentialConfigured(activeRow?.credentialState) ? MASKED_API_KEY : "";
      }
      workspaceInput.value = settings.workspace || "";
      if (workspaceMode) {
        workspaceMode.value = settings.workspace_mode === "full" ? "full" : "workspace";
        syncWorkspaceModeControls();
      } else {
        openWorkspaceButton.disabled = !workspaceInput.value.trim();
      }
      renderPermissionFields(settings.permissionMode, settings.permissionRiskMax);
      permissionModeInput.disabled = false;
      permissionRiskInput.disabled = false;
      savePermission.disabled = false;
      themeInput.value = settings.themeStyle || "ink_teal";
      applyTheme(themeInput.value);
    }

    function renderConfig(config) {
      if (config.loading || !config.ok) return;
      const data = safeJson(config.stdout);
      if (Array.isArray(data.providers)) {
        currentPresetRows = providerPresetRows({ modelProviderPresets: data.providers, modelProviderProfiles: data.provider_profiles });
        renderPresetInput(presetInput.value);
      }
      const match = data.provider_match || {};
      const configuredProvider = String(data.configured_provider || "").trim();
      const matchedProviderRaw = String(match.provider || data.matched_provider || data.provider || "").trim();
      const matchedProvider = match.reason === "unmatched_openai_compatible_fallback" && configuredProvider
        ? configuredProvider
        : matchedProviderRaw;
      if (!modelFormDirty) {
        renderPresetInput(selectedPresetValue(matchedProvider));
        providerInput.value = data.configured_provider || "";
        modelInput.value = data.configured_model_name || "";
        baseUrlInput.value = data.configured_base_url || "";
        apiKeyInput.value = isCredentialConfigured(data.credential_state || data.api_key || "") ? MASKED_API_KEY : "";
      }
    }

    function renderQr(result = {}) {
      const qrcodeUrl = String(result.qrcode_url || "").trim();
      const clearQr = () => {
        linkWechatQrWrap.hidden = true;
        linkWechatQrImage.hidden = true;
        linkWechatQrImage.onerror = null;
        linkWechatQrImage.removeAttribute("src");
        linkWechatQrText.textContent = "";
      };
      if (!qrcodeUrl || qrcodeUrl.length > 6 * 1024 * 1024) {
        clearQr();
        return;
      }
      linkWechatQrWrap.hidden = false;
      linkWechatQrImage.onerror = null;
      const isDataImage = /^data:image\/(?:png|jpeg|jpg|gif|webp);base64,/i.test(qrcodeUrl);
      const isHttpUrl = /^https?:/i.test(qrcodeUrl);
      const generatedQr = isHttpUrl && qrcodeUrl.length <= 4096 ? qrTextToSvgDataUrl(qrcodeUrl) : "";
      if (isDataImage) {
        linkWechatQrImage.hidden = false;
        linkWechatQrImage.src = qrcodeUrl;
      } else if (generatedQr) {
        linkWechatQrImage.hidden = false;
        linkWechatQrImage.src = generatedQr;
      } else {
        clearQr();
        return;
      }
      linkWechatQrText.textContent = qrcodeUrl;
    }

    function renderLinks(result = {}) {
      const available = result.ok === true;
      if (!available) {
        setPill(linkSaveState, "通信服务暂时离线", "warn");
        linkActionOutput.textContent = result.error || "通信连接服务当前不可用；请刷新后台后重试。";
        return;
      }
      const settings = result.settings || {};
      const links = result.links || {};
      lastLinkSettings = settings;
      const wechat = settings.wechat || {};
      const direct = { ...DEFAULT_WECHAT_DIRECT, ...(wechat.direct || {}) };
      const feishu = { ...DEFAULT_FEISHU, ...(settings.feishu || {}) };
      const wechatState = String(links.wechat_direct?.state || "unknown");
      const wechatActive = !["unknown", "disabled", "missing_credentials", "available", "closed"].includes(wechatState);
      linkWechatEnabled.value = String(Boolean(
        (wechat.enabled && wechat.mode === "direct_bot" && direct.enabled) || wechatActive
      ));
      linkWechatBotToken.value = secretValue(direct.bot_token);
      linkWechatAccountId.value = direct.account_id || "";
      const feishuState = String(links.feishu?.state || "unknown");
      linkFeishuEnabled.value = String(Boolean(
        feishu.enabled || !["unknown", "disabled", "missing_credentials", "closed"].includes(feishuState)
      ));
      linkFeishuAppId.value = feishu.app_id || "";
      linkFeishuAppSecret.value = secretValue(feishu.app_secret);
      const wechatText = linkStateText(links.wechat_direct);
      const feishuText = linkStateText(links.feishu);
      if (available) setPill(linkSaveState, `微信 ${wechatText} / 飞书 ${feishuText}`);
      const diagnosticText = linkDiagnosticText(links.wechat_direct);
      if (linkWechatDiagnostics) {
        linkWechatDiagnostics.textContent = diagnosticText;
        linkWechatDiagnostics.hidden = !diagnosticText;
      }
      if (links.wechat_direct?.session_key) activeWechatSessionKey = links.wechat_direct.session_key;
      renderQr({ qrcode_url: links.wechat_direct?.qrcode_url || "" });
    }

    async function loadLinks() {
      if (!actions.gatewayLinksStatus) return;
      const result = await actions.gatewayLinksStatus();
      renderLinks(result);
    }

    function markLinksDirty() {
      setPill(linkSaveState, "待保存", "warn");
    }

    function linkPayload() {
      const existingWechat = lastLinkSettings.wechat || {};
      const existingDirect = { ...DEFAULT_WECHAT_DIRECT, ...(existingWechat.direct || {}) };
      const existingCallback = { ...DEFAULT_WECHAT_CALLBACK, ...(existingWechat.callback || {}) };
      const existingFeishu = { ...DEFAULT_FEISHU, ...(lastLinkSettings.feishu || {}) };
      const directEnabled = linkWechatEnabled.value === "true";
      return {
        wechat: {
          enabled: directEnabled,
          mode: "direct_bot",
          direct: {
            ...existingDirect,
            enabled: directEnabled,
            bot_token: linkWechatBotToken.value.trim(),
            account_id: linkWechatAccountId.value.trim(),
            base_url: existingDirect.base_url || DEFAULT_WECHAT_DIRECT.base_url,
            bot_type: existingDirect.bot_type || DEFAULT_WECHAT_DIRECT.bot_type,
            auto_reply: existingDirect.auto_reply !== false
          },
          callback: {
            ...existingCallback,
            enabled: false
          }
        },
        feishu: {
          ...existingFeishu,
          enabled: linkFeishuEnabled.value === "true",
          mode: "long_connection",
          app_id: linkFeishuAppId.value.trim(),
          app_secret: linkFeishuAppSecret.value.trim()
        }
      };
    }

    async function runLinkAction(action) {
      const button = linkButtons[action];
      if (button) button.disabled = true;
      linkActionOutput.textContent = "正在处理...";
      try {
        const payload = { action };
        if (activeWechatSessionKey) payload.session_key = activeWechatSessionKey;
        if (action === "wechat_direct_login_wait" && linkWechatVerifyCode.value.trim()) {
          payload.verify_code = linkWechatVerifyCode.value.trim();
        }
        const result = await actions.gatewayLinksAction?.(payload);
        if (action === "wechat_direct_start" && result?.error === "missing_credentials") {
          const login = await actions.gatewayLinksAction?.({ action: "wechat_direct_login_start" });
          if (login?.session_key) activeWechatSessionKey = login.session_key;
          renderQr(login);
          linkActionOutput.textContent = actionText(login, "微信登录");
          await loadLinks();
          return;
        }
        if (result?.session_key) activeWechatSessionKey = result.session_key;
        renderQr(result);
        linkActionOutput.textContent = actionText(result, "微信连接");
        if (result?.need_verify_code) linkWechatVerifyCode.focus();
        await loadLinks();
      } catch (error) {
        linkActionOutput.textContent = error?.message || String(error);
      } finally {
        if (button) button.disabled = false;
      }
    }

    [providerInput, modelInput, baseUrlInput, apiKeyInput].forEach((input) => {
      input.addEventListener("focus", () => {
        if (input === apiKeyInput && isMaskedApiKey(apiKeyInput.value)) apiKeyInput.select();
      });
      input.addEventListener("input", () => {
        modelFormDirty = true;
        if (input !== apiKeyInput) presetInput.value = "";
        setPill(modelSaveState, "待保存", "warn");
      });
    });

    presetInput.addEventListener("change", () => {
      modelFormDirty = true;
      const row = presetRowById(currentPresetRows, presetInput.value);
      providerInput.value = row?.provider || "";
      modelInput.value = row?.model || "";
      baseUrlInput.value = row?.baseUrl || "";
      apiKeyInput.value = isCredentialConfigured(row?.credentialState) ? MASKED_API_KEY : "";
      setPill(modelSaveState, "待保存", "warn");
    });

    [linkWechatEnabled, linkWechatBotToken, linkWechatAccountId, linkWechatVerifyCode, linkFeishuEnabled, linkFeishuAppId, linkFeishuAppSecret]
      .forEach((input) => input.addEventListener("input", markLinksDirty));

    workspaceInput.addEventListener("input", () => {
      syncWorkspaceModeControls();
      setPill(workspaceSaveState, "待保存", "warn");
    });

    if (workspaceMode) {
      workspaceMode.addEventListener("change", () => {
        syncWorkspaceModeControls();
        setPill(workspaceSaveState, "待保存", "warn");
      });
    }

    function syncWorkspaceModeControls() {
      const full = workspaceMode?.value === "full";
      if (workspaceInput) workspaceInput.disabled = full;
      if (chooseWorkspaceButton) chooseWorkspaceButton.disabled = full;
      if (openWorkspaceButton) openWorkspaceButton.disabled = full || !workspaceInput?.value.trim();
    }

    permissionModeInput.addEventListener("change", () => {
      renderPermissionFields(permissionModeInput.value, permissionRiskInput.value);
      setPill(permissionSaveState, "待保存", "warn");
    });

    permissionRiskInput.addEventListener("change", () => {
      setPill(permissionSaveState, "\u5f85\u4fdd\u5b58", "warn");
    });

    themeInput.addEventListener("change", () => {
      applyTheme(themeInput.value);
      setPill(uiSaveState, "待保存", "warn");
    });

    saveModel.addEventListener("click", async () => {
      setPill(modelSaveState, "保存中");
      saveModel.disabled = true;
      try {
        const keyValue = isMaskedApiKey(apiKeyInput.value) ? "" : apiKeyInput.value.trim();
        const selectedPreset = selectedPresetValue(presetInput.value);
        const selectedRow = presetRowById(currentPresetRows, selectedPreset);
        const saved = await actions.saveSettings({
          modelService: selectedPreset || "custom",
          modelProvider: providerInput.value.trim() || selectedRow?.provider || "",
          modelName: modelInput.value.trim() || selectedRow?.model || "",
          modelBaseUrl: baseUrlInput.value.trim() || selectedRow?.baseUrl || "",
          ...(keyValue ? { modelApiKey: keyValue } : {})
        });
        modelFormDirty = false;
        renderSettings(saved);
        setPill(modelSaveState, "已保存", "ok");
        await actions.refreshConfig?.();
      } catch (error) {
        setPill(modelSaveState, error?.message || "保存失败", "failed");
      } finally {
        saveModel.disabled = false;
      }
    });

    probeProviderApi?.addEventListener("click", async () => {
      const bridge = window.tiangongDesktop;
      if (typeof bridge?.probeProviderApi !== "function") {
        setPill(modelSaveState, "安全探针通道不可用", "failed");
        return;
      }
      setPill(modelSaveState, "探测中");
      probeProviderApi.disabled = true;
      try {
        const result = await bridge.probeProviderApi();
        const latency = Number.isFinite(result?.latency_ms) ? ` · ${result.latency_ms}ms` : "";
        if (result?.ok) {
          const modelNote = result.configured_model_available === false
            ? " · 配置模型不在列表"
            : (result.configured_model_available ? " · 模型在线" : "");
          setPill(
            modelSaveState,
            `连接正常 HTTP ${result.http_status}${latency}${modelNote}`,
            result.configured_model_available === false ? "warn" : "ok",
          );
        } else {
          const status = result?.http_status ? ` HTTP ${result.http_status}` : "";
          const code = String(result?.error_code || result?.stage || "").toLowerCase();
          let human = "模型服务探测失败";
          if (code.includes("model_endpoint_invalid") || code.includes("endpoint_missing")) {
            human = "请先保存有效的模型服务地址（Base URL）";
          } else if (code.includes("untrusted") || code.includes("not_trusted") || code.includes("forbidden")) {
            human = "该模型服务地址不在允许的范围内";
          } else if (code.includes("auth") || code.includes("401") || code.includes("403") || code.includes("key")) {
            human = "鉴权失败：请检查 API Key、权限、余额或服务商控制台配置";
          } else if (code.includes("timeout") || code.includes("timed_out")) {
            human = "连接超时：请检查网络或服务商状态";
          } else if (code.includes("dns") || code.includes("network") || code.includes("econn") || code.includes("fetch")) {
            human = "无法连接模型服务：请检查网络";
          }
          setPill(
            modelSaveState,
            `${human}${status}${latency}${result?.error_code ? `（${result.error_code}）` : ""}`,
            "failed",
          );
        }
      } catch (error) {
        setPill(modelSaveState, error?.message || "API 探测失败", "failed");
      } finally {
        probeProviderApi.disabled = false;
      }
    });

    deleteModelKey.addEventListener("click", async () => {
      const provider = providerInput.value.trim() || presetRowById(currentPresetRows, selectedPresetValue(presetInput.value))?.provider || "";
      setPill(modelSaveState, "删除中");
      deleteModelKey.disabled = true;
      try {
        await actions.deleteProviderApiKey(provider);
        apiKeyInput.value = "";
        modelFormDirty = false;
        setPill(modelSaveState, "密钥已删除", "ok");
      } catch (error) {
        setPill(modelSaveState, error?.message || "删除失败", "failed");
      } finally {
        deleteModelKey.disabled = false;
      }
    });

    chooseWorkspaceButton.addEventListener("click", async () => {
      setPill(workspaceSaveState, "选择中");
      chooseWorkspaceButton.disabled = true;
      saveWorkspace.disabled = true;
      try {
        const saved = await actions.chooseWorkspace?.();
        renderSettings(saved || state.snapshot().settings);
        setPill(workspaceSaveState, saved?.canceled ? "未修改" : "已保存", saved?.canceled ? "" : "ok");
        await actions.refreshStatus?.();
      } catch (error) {
        setPill(workspaceSaveState, error?.message || "选择失败", "failed");
      } finally {
        chooseWorkspaceButton.disabled = false;
        saveWorkspace.disabled = false;
      }
    });

    saveWorkspace.addEventListener("click", async () => {
      setPill(workspaceSaveState, "保存中");
      saveWorkspace.disabled = true;
      chooseWorkspaceButton.disabled = true;
      try {
        const saved = await actions.saveSettings({
          workspace: workspaceInput.value.trim(),
          workspace_mode: workspaceMode?.value === "full" ? "full" : "workspace",
        });
        renderSettings(saved);
        setPill(workspaceSaveState, "已保存", "ok");
        await actions.refreshStatus?.();
      } catch (error) {
        setPill(workspaceSaveState, error?.message || "保存失败", "failed");
      } finally {
        saveWorkspace.disabled = false;
        chooseWorkspaceButton.disabled = false;
      }
    });

    savePermission.addEventListener("click", async () => {
      setPill(permissionSaveState, "\u4fdd\u5b58\u4e2d");
      savePermission.disabled = true;
      try {
        const saved = await actions.saveSettings({
          permissionMode: permissionModeInput.value,
          permissionRiskMax: permissionRiskInput.value
        });
        renderSettings(saved);
        setPill(permissionSaveState, "\u5df2\u4fdd\u5b58", "ok");
        await actions.refreshStatus?.();
      } catch (error) {
        setPill(permissionSaveState, error?.message || "\u4fdd\u5b58\u5931\u8d25", "failed");
      } finally {
        savePermission.disabled = false;
      }
    });

    openWorkspaceButton.addEventListener("click", () => {
      const target = workspaceInput.value.trim();
      if (target) actions.openPath?.(target);
    });

    saveLinks.addEventListener("click", async () => {
      setPill(linkSaveState, "保存中");
      saveLinks.disabled = true;
      try {
        const result = await actions.saveGatewayLinks?.(linkPayload());
        renderLinks(result);
        setPill(linkSaveState, result?.ok ? "已保存" : "保存失败", result?.ok ? "ok" : "failed");
      } catch (error) {
        setPill(linkSaveState, error?.message || "保存失败", "failed");
      } finally {
        saveLinks.disabled = false;
      }
    });

    saveUi.addEventListener("click", async () => {
      const desiredTheme = themeInput.value;
      setPill(uiSaveState, "保存中");
      saveUi.disabled = true;
      try {
        const saved = await runSettingsMutationWithRecovery(
          () => actions.saveSettings({ themeStyle: desiredTheme }),
          {
            onRetry: () => setPill(uiSaveState, "后端恢复中，正在重试"),
          },
        );
        renderSettings(saved);
        setPill(uiSaveState, "已保存", "ok");
      } catch (error) {
        const message = error?.code === "network_error"
          ? "后端暂时不可用，请稍后重试"
          : error?.message || "保存失败";
        setPill(uiSaveState, message, "failed");
      } finally {
        saveUi.disabled = false;
      }
    });

    refresh.addEventListener("click", async () => {
      await actions.refreshStatus?.();
      await actions.refreshConfig?.();
      await loadLinks();
    });
    refreshLinks.addEventListener("click", loadLinks);
    Object.entries(linkButtons).forEach(([action, button]) => {
      button?.addEventListener("click", () => runLinkAction(action));
    });

    state.on("page", renderPage);
    state.on("settings", renderSettings);
    state.on("backendConfig", renderConfig);

    const snap = state.snapshot();
    renderPage(snap.activePage);
    renderSettings(snap.settings);
    renderConfig(snap.backendConfig);
  }
};
