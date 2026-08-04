import { renderUserAvatar } from "../core/user-avatar.mjs";
import { requestVoiceOutput } from "../runtime/http-runtime.mjs";

const DEFAULT_LOGO_SRC = "../assets/tiangong-avatar.png";

const FALLBACK_VOICE_PRESETS = [
  { id: "xiaoxiao_warm", label: "晓晓·温柔", lang: "zh-CN", rate: 0.95, pitch: 1.06, volume: 1, preferred_names: ["Xiaoxiao", "Microsoft Xiaoxiao", "zh-CN-XiaoxiaoNeural"] },
  { id: "xiaoyi_gentle", label: "晓伊·轻语", lang: "zh-CN", rate: 0.90, pitch: 1.10, volume: 1, preferred_names: ["Xiaoyi", "Microsoft Xiaoyi", "zh-CN-XiaoyiNeural"] },
  { id: "xiaoxuan_bright", label: "晓萱·明亮", lang: "zh-CN", rate: 0.97, pitch: 1.02, volume: 1, preferred_names: ["Xiaoxuan", "Microsoft Xiaoxuan", "zh-CN-XiaoxuanNeural"] },
  { id: "yunxi_calm", label: "云希·沉稳", lang: "zh-CN", rate: 0.93, pitch: 0.95, volume: 1, preferred_names: ["Yunxi", "Microsoft Yunxi", "zh-CN-YunxiNeural"] },
  { id: "xiaohan_clear", label: "晓涵·清澈", lang: "zh-CN", rate: 1.0, pitch: 1.0, volume: 1, preferred_names: ["Xiaohan", "Microsoft Xiaohan", "zh-CN-XiaohanNeural"] },
  { id: "custom", label: "授权声线配置", lang: "zh-CN", rate: 1, pitch: 1, volume: 1, preferred_names: [] }
];

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function personaName(settings) {
  return String(settings?.personaName || "起源").trim() || "起源";
}

function renderAvatar(target, settings) {
  target.innerHTML = "";
  const img = document.createElement("img");
  img.src = String(settings?.personaAvatarDataUrl || "") || DEFAULT_LOGO_SRC;
  img.alt = "角色头像";
  target.appendChild(img);
}

function numberValue(value, fallback = 1) {
  const next = Number(value);
  return Number.isFinite(next) ? next : fallback;
}

function shortPath(value) {
  const text = String(value || "");
  if (!text) return "";
  const parts = text.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || text;
}

function voicePresetRows(settings) {
  const rows = Array.isArray(settings.bodyVoicePresets) && settings.bodyVoicePresets.length
    ? settings.bodyVoicePresets
    : FALLBACK_VOICE_PRESETS;
  return rows.map((item) => ({
    id: String(item.id || "custom"),
    label: String(item.label || item.id || "自定义"),
    lang: String(item.lang || "zh-CN"),
    rate: numberValue(item.rate, 1),
    pitch: numberValue(item.pitch, 1),
    volume: numberValue(item.volume, 1),
    preferredNames: Array.isArray(item.preferred_names) ? item.preferred_names.map(String) : [],
  }));
}

function optionRows(rows, selected) {
  return rows.map((item) => `<option value="${esc(item.id)}" ${item.id === selected ? "selected" : ""}>${esc(item.label)}</option>`).join("");
}

function supportsSpeech() {
  return typeof window !== "undefined" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
}

function speechVoices() {
  if (!supportsSpeech()) return [];
  try {
    return window.speechSynthesis.getVoices?.() || [];
  } catch {
    return [];
  }
}

function pickVoice(settings, presetRows, voices) {
  const selectedName = String(settings.bodyVoiceName || "").trim();
  if (selectedName) {
    const selected = voices.find((voice) => String(voice.name || "") === selectedName);
    if (selected) return selected;
  }
  const preset = presetRows.find((item) => item.id === settings.bodyVoicePreset) || presetRows[0];
  const lang = String(settings.bodyVoiceLang || preset?.lang || "zh-CN").toLowerCase();
  const sameLang = voices.filter((voice) => String(voice.lang || "").toLowerCase().startsWith(lang.slice(0, 2)));
  const preferred = preset?.preferredNames || [];
  return sameLang.find((voice) => preferred.some((name) => String(voice.name || "").toLowerCase().includes(String(name).toLowerCase())))
    || sameLang[0]
    || voices[0]
    || null;
}

function providerVoiceId(settings, presetRows) {
  const configured = String(settings.bodyVoiceNativeId || "").trim();
  if (configured) return configured;
  const preset = presetRows.find((item) => item.id === settings.bodyVoicePreset);
  return String((preset?.preferredNames || []).find((name) => /Neural$/i.test(String(name))) || "");
}

function speakPreview(settings, presetRows, text, onEnd) {
  if (!supportsSpeech()) return false;
  const clean = String(text || "").trim();
  if (!clean) return false;
  const voices = speechVoices();
  const utterance = new SpeechSynthesisUtterance(clean);
  // P2-01: the preview state must be reset when playback actually ends or
  // fails, otherwise the UI stays stuck on "试听中" forever.
  utterance.onend = () => { if (onEnd) onEnd(); };
  utterance.onerror = () => { if (onEnd) onEnd(); };
  utterance.lang = String(settings.bodyVoiceLang || "zh-CN");
  utterance.rate = numberValue(settings.bodyVoiceRate, 1);
  utterance.pitch = numberValue(settings.bodyVoicePitch, 1);
  utterance.volume = numberValue(settings.bodyVoiceVolume, 1);
  const voice = pickVoice(settings, presetRows, voices);
  if (voice) utterance.voice = voice;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
  return true;
}

function transientSettingsFailure(error) {
  return /timeout|timed out|network|fetch|connection|econn|502|503|504|service.*unavailable|服务暂时不可用/i
    .test(String(error?.message || error || ""));
}

async function saveSettingsWithRetry(actions, patch) {
  let lastError = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      return await actions.saveSettings(patch);
    } catch (error) {
      lastError = error;
      if (attempt >= 2 || !transientSettingsFailure(error)) throw error;
      await new Promise((resolve) => window.setTimeout(resolve, 200 * (attempt + 1)));
    }
  }
  throw lastError || new Error("身体设置保存失败");
}

export const bodyPanelPlugin = {
  id: "body-panel",
  slot: "conversation",
  order: 215,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML("beforeend", `
      <section class="page-panel body-page" data-page-panel="body">
        <header class="page-header">
          <div class="title-group">
            <span class="caption">身体</span>
            <h2>身体设定</h2>
          </div>
          <div class="commandbar-meta">
            <span id="bodySaveState" class="mini-pill">未修改</span>
            <button id="bodySave" class="small-command" type="button">保存</button>
          </div>
        </header>

        <section class="page-body body-creator">
          <section class="panel-card body-voice-card">
            <div class="panel-title"><span>声线塑形</span><span id="bodyVoiceState" class="mini-pill">未读取</span></div>
            <div class="settings-form body-form">
              <label class="field-row">
                <span>回复朗读</span>
                <select id="bodyVoiceReplyEnabled">
                  <option value="true">开启</option>
                  <option value="false">关闭</option>
                </select>
              </label>
              <label class="field-row">
                <span>声线预设</span>
                <select id="bodyVoicePreset"></select>
              </label>
              <label class="field-row">
                <span>输出引擎</span>
                <select id="bodyVoiceOutputMode">
                  <option value="auto">自动（模型原生优先）</option>
                  <option value="native_model">仅模型原生（需部署配置）</option>
                  <option value="edge_tts">神经语音服务</option>
                  <option value="browser_tts">本机浏览器朗读</option>
                </select>
              </label>
              <label class="field-row">
                <span>原生语音 ID</span>
                <input id="bodyVoiceNativeId" type="text" maxlength="160" placeholder="由已配置的模型语音服务提供" />
              </label>
              <label class="field-row">
                <span>系统声音</span>
                <select id="bodySystemVoice"></select>
              </label>
              <label class="field-row">
                <span>自定义名</span>
                <input id="bodyCustomVoiceName" type="text" maxlength="80" placeholder="自定义声音" />
              </label>
              <label class="field-row">
                <span>声音样本（仅授权资料）</span>
                <input id="bodyCustomVoicePath" type="text" readonly placeholder="未选择" />
              </label>
              <label class="field-row">
                <span>样本授权</span>
                <select id="bodyVoiceSampleConsent">
                  <option value="false">仅本地保管，不参与合成</option>
                  <option value="true">授权给未来已配置的合规声线服务</option>
                </select>
              </label>
              <div class="settings-actions-row">
                <button id="bodyChooseVoiceSample" class="small-command" type="button">选择授权样本</button>
                <button id="bodyClearVoiceSample" class="small-command muted-command" type="button">清除样本</button>
                <button id="bodyTestVoice" class="small-command" type="button">试听</button>
              </div>
              <label class="body-slider-row">
                <span>语速</span>
                <input id="bodyVoiceRate" type="range" min="0.5" max="1.6" step="0.01" />
                <strong id="bodyVoiceRateValue">1.00</strong>
              </label>
              <label class="body-slider-row">
                <span>音高</span>
                <input id="bodyVoicePitch" type="range" min="0.5" max="1.8" step="0.01" />
                <strong id="bodyVoicePitchValue">1.04</strong>
              </label>
              <label class="body-slider-row">
                <span>音量</span>
                <input id="bodyVoiceVolume" type="range" min="0" max="1" step="0.01" />
                <strong id="bodyVoiceVolumeValue">1.00</strong>
              </label>
            </div>
          </section>

          <section class="panel-card body-role-card">
            <div class="panel-title"><span>人物与称呼</span><span class="mini-pill">生命 · 用户</span></div>
            <div class="body-role-grid">
              <!-- 左：AI 角色 -->
              <div class="body-role-col">
                <div class="body-role-col-title">生命</div>
                <div class="persona-avatar-editor">
                  <div id="bodyAvatarEditor" class="persona-avatar-preview"></div>
                  <div class="avatar-actions">
                    <button id="bodyChooseAvatar" class="small-command" type="button">选择头像</button>
                    <button id="bodyClearAvatar" class="small-command muted-command" type="button">清除</button>
                  </div>
                </div>
                <div class="settings-form body-form">
                  <label class="field-row">
                    <span>生命名字</span>
                    <input id="bodyPersonaName" type="text" maxlength="32" placeholder="起源" />
                  </label>
                  <label class="field-row">
                    <span>体态预设</span>
                    <select id="bodyPreset">
                      <option value="standard">标准体态</option>
                      <option value="light">轻盈体态</option>
                      <option value="steady">沉稳体态</option>
                      <option value="custom">自定义体态</option>
                    </select>
                  </label>
                </div>
              </div>
              <!-- 右：用户信息 -->
              <div class="body-role-col">
                <div class="body-role-col-title">用户信息</div>
                <div class="persona-avatar-editor">
                  <div id="userAvatarEditor" class="persona-avatar-preview"></div>
                  <div class="avatar-actions">
                    <button id="userChooseAvatar" class="small-command" type="button">选择头像</button>
                    <button id="userClearAvatar" class="small-command muted-command" type="button">清除</button>
                  </div>
                </div>
                <div class="settings-form body-form">
                  <label class="field-row">
                    <span>希望生命如何称呼你</span>
                    <input id="bodyUserName" type="text" maxlength="32" placeholder="公子" />
                  </label>
                  <label class="field-row">
                    <span>身份 / 工作</span>
                    <input id="bodyUserTitle" type="text" maxlength="64" placeholder="全栈工程师" />
                  </label>
                </div>
              </div>
            </div>
          </section>

        </section>
      </section>
    `);

    const panel = slot.querySelector('[data-page-panel="body"]');
    const saveState = panel.querySelector("#bodySaveState");
    const saveButton = panel.querySelector("#bodySave");
    const voiceState = panel.querySelector("#bodyVoiceState");
    const replyEnabledInput = panel.querySelector("#bodyVoiceReplyEnabled");
    const voicePresetInput = panel.querySelector("#bodyVoicePreset");
    const outputModeInput = panel.querySelector("#bodyVoiceOutputMode");
    const nativeVoiceIdInput = panel.querySelector("#bodyVoiceNativeId");
    const systemVoiceInput = panel.querySelector("#bodySystemVoice");
    const customVoiceNameInput = panel.querySelector("#bodyCustomVoiceName");
    const customVoicePathInput = panel.querySelector("#bodyCustomVoicePath");
    const sampleConsentInput = panel.querySelector("#bodyVoiceSampleConsent");
    const chooseVoiceSample = panel.querySelector("#bodyChooseVoiceSample");
    const clearVoiceSample = panel.querySelector("#bodyClearVoiceSample");
    const testVoice = panel.querySelector("#bodyTestVoice");
    const rateInput = panel.querySelector("#bodyVoiceRate");
    const pitchInput = panel.querySelector("#bodyVoicePitch");
    const volumeInput = panel.querySelector("#bodyVoiceVolume");
    const rateValue = panel.querySelector("#bodyVoiceRateValue");
    const pitchValue = panel.querySelector("#bodyVoicePitchValue");
    const volumeValue = panel.querySelector("#bodyVoiceVolumeValue");
    const avatarEditor = panel.querySelector("#bodyAvatarEditor");
    const chooseAvatar = panel.querySelector("#bodyChooseAvatar");
    const clearAvatar = panel.querySelector("#bodyClearAvatar");
    const personaInput = panel.querySelector("#bodyPersonaName");
    const bodyPresetInput = panel.querySelector("#bodyPreset");
    const userAvatarEditor = panel.querySelector("#userAvatarEditor");
    const userChooseAvatar = panel.querySelector("#userChooseAvatar");
    const userClearAvatar = panel.querySelector("#userClearAvatar");
    const userNameInput = panel.querySelector("#bodyUserName");
    const userTitleInput = panel.querySelector("#bodyUserTitle");

    let dirty = false;
    let avatarDataUrl = "";
    let userAvatarDataUrl = "";
    let voiceRows = FALLBACK_VOICE_PRESETS;
    let voicesLoaded = [];

    function setSaveState(label, className = "") {
      if (!saveState) return;
      saveState.textContent = label;
      saveState.className = `mini-pill ${className}`.trim();
    }

    function currentSettingsPatch() {
      return {
        personaName: personaInput.value.trim() || "起源",
        personaAvatarDataUrl: avatarDataUrl,
        bodyPreset: bodyPresetInput.value || "standard",
        userName: userNameInput.value.trim(),
        userCallsign: userNameInput.value.trim(),
        userTitle: userTitleInput.value.trim(),
        userWork: userTitleInput.value.trim(),
        userAvatarDataUrl: userAvatarDataUrl,
        bodyVoiceReplyEnabled: replyEnabledInput.value === "true",
        bodyVoicePreset: voicePresetInput.value || "custom",
        bodyVoiceName: systemVoiceInput.value,
        bodyVoiceCustomName: customVoiceNameInput.value.trim(),
        bodyVoiceCustomPath: customVoicePathInput.value.trim(),
        bodyVoiceOutputMode: outputModeInput.value || "auto",
        bodyVoiceNativeId: nativeVoiceIdInput.value.trim(),
        bodyVoiceSampleConsent: sampleConsentInput.value === "true",
        bodyVoiceLang: (voiceRows.find((item) => item.id === voicePresetInput.value)?.lang) || "zh-CN",
        bodyVoiceRate: numberValue(rateInput.value, 1),
        bodyVoicePitch: numberValue(pitchInput.value, 1.04),
        bodyVoiceVolume: numberValue(volumeInput.value, 1),
      };
    }

    function emitBodyHotPreview() {
      window.dispatchEvent(new CustomEvent("tiangong-body-hot-preview", {
        detail: {
          ...currentSettingsPatch(),
          bodyVoicePresets: voiceRows
        }
      }));
    }

    function renderVoiceOptions(selectedVoiceName = "") {
      voicesLoaded = speechVoices();
      const selected = String(selectedVoiceName || "");
      systemVoiceInput.innerHTML = [
        `<option value="">自动匹配</option>`,
        ...voicesLoaded.map((voice) => {
          const value = String(voice.name || "");
          const label = `${voice.name || "Voice"} · ${voice.lang || ""}`;
          return `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`;
        })
      ].join("");
      systemVoiceInput.value = voicesLoaded.some((voice) => String(voice.name || "") === selected) ? selected : "";
      voiceState.textContent = supportsSpeech() ? `${voicesLoaded.length} 声音` : "不可用";
      voiceState.className = `mini-pill ${supportsSpeech() ? "ok" : "warn"}`;
    }

    function updatePreview() {
      const patch = currentSettingsPatch();
      rateValue.textContent = numberValue(rateInput.value, 1).toFixed(2);
      pitchValue.textContent = numberValue(pitchInput.value, 1.04).toFixed(2);
      volumeValue.textContent = numberValue(volumeInput.value, 1).toFixed(2);
      renderAvatar(avatarEditor, { personaAvatarDataUrl: avatarDataUrl, personaName: patch.personaName });
      renderUserAvatar(userAvatarEditor, { userAvatarDataUrl }, { className: "persona-avatar-img" });
    }

    function markDirty() {
      dirty = true;
      setSaveState("待保存", "warn");
      updatePreview();
      emitBodyHotPreview();
    }

    function renderPage(page) {
      panel.classList.toggle("active", page === "body");
    }

    function renderSettings(settings) {
      if (dirty) return;
      voiceRows = voicePresetRows(settings);
      avatarDataUrl = String(settings.personaAvatarDataUrl || "");
      const selectedPreset = String(settings.bodyVoicePreset || "qiyuan_clear");
      voicePresetInput.innerHTML = optionRows(voiceRows, selectedPreset);
      voicePresetInput.value = voiceRows.some((item) => item.id === selectedPreset) ? selectedPreset : "custom";
      renderVoiceOptions(settings.bodyVoiceName || "");
      replyEnabledInput.value = String(Boolean(settings.bodyVoiceReplyEnabled));
      outputModeInput.value = ["auto", "native_model", "edge_tts", "browser_tts"].includes(String(settings.bodyVoiceOutputMode))
        ? String(settings.bodyVoiceOutputMode)
        : "auto";
      nativeVoiceIdInput.value = String(settings.bodyVoiceNativeId || "");
      customVoiceNameInput.value = String(settings.bodyVoiceCustomName || "");
      customVoicePathInput.value = String(settings.bodyVoiceCustomPath || "");
      sampleConsentInput.value = String(Boolean(settings.bodyVoiceSampleConsent));
      rateInput.value = numberValue(settings.bodyVoiceRate, 1);
      pitchInput.value = numberValue(settings.bodyVoicePitch, 1.04);
      volumeInput.value = numberValue(settings.bodyVoiceVolume, 1);
      personaInput.value = personaName(settings);
      bodyPresetInput.value = settings.bodyPreset || "standard";
      userNameInput.value = String(settings.userCallsign || settings.userName || "");
      userTitleInput.value = String(settings.userWork || settings.userTitle || "");
      userAvatarDataUrl = String(settings.userAvatarDataUrl || "");
      renderUserAvatar(userAvatarEditor, { userAvatarDataUrl }, { className: "persona-avatar-img" });
      setSaveState("未修改");
      updatePreview();
    }

    voicePresetInput.addEventListener("change", () => {
      const preset = voiceRows.find((item) => item.id === voicePresetInput.value);
      if (preset) {
        rateInput.value = preset.rate;
        pitchInput.value = preset.pitch;
        volumeInput.value = preset.volume;
      }
      markDirty();
    });

    chooseVoiceSample.addEventListener("click", async () => {
      const next = await actions.chooseVoiceSample?.();
      if (next?.canceled) return;
      customVoicePathInput.value = String(next?.bodyVoiceCustomPath || "");
      customVoiceNameInput.value = String(next?.bodyVoiceCustomName || shortPath(customVoicePathInput.value) || customVoiceNameInput.value);
      voicePresetInput.value = "custom";
      markDirty();
      voiceState.textContent = "样本已保管，未参与合成";
      voiceState.className = "mini-pill warn";
    });

    clearVoiceSample.addEventListener("click", () => {
      customVoicePathInput.value = "";
      customVoiceNameInput.value = "";
      markDirty();
    });

    testVoice.addEventListener("click", async () => {
      const settings = currentSettingsPatch();
      const text = `${personaInput.value || "起源"}，声线输出链路已就绪。`;
      const resetPreviewState = () => {
        voiceState.textContent = "已就绪";
        voiceState.className = "mini-pill ok";
      };
      voiceState.textContent = "正在协商输出引擎";
      try {
        if (settings.bodyVoiceOutputMode !== "browser_tts") {
          const result = await requestVoiceOutput({
            text,
            mode: settings.bodyVoiceOutputMode,
            voice_id: providerVoiceId(settings, voiceRows),
          });
          if (result?.ok && result.audio_base64) {
            const bytes = Uint8Array.from(atob(String(result.audio_base64)), (char) => char.charCodeAt(0));
            const url = URL.createObjectURL(new Blob([bytes], { type: String(result.mime || "audio/mpeg") }));
            const audio = new Audio(url);
            audio.volume = numberValue(settings.bodyVoiceVolume, 1);
            audio.addEventListener("ended", () => {
              URL.revokeObjectURL(url);
              resetPreviewState();
            }, { once: true });
            audio.addEventListener("error", () => {
              URL.revokeObjectURL(url);
              resetPreviewState();
            }, { once: true });
            await audio.play();
            voiceState.textContent = `试听中 · ${result.engine === "native_model" ? "模型原生" : "神经语音"}`;
            voiceState.className = "mini-pill ok";
            return;
          }
        }
      } catch {
        // Explicitly continue to the local browser fallback below.
      }
      const ok = speakPreview(settings, voiceRows, text, resetPreviewState);
      voiceState.textContent = ok ? "试听中 · 本机浏览器降级" : "当前没有可用输出引擎";
      voiceState.className = `mini-pill ${ok ? "warn" : "failed"}`;
    });

    chooseAvatar.addEventListener("click", async () => {
      const next = await actions.choosePersonaAvatar?.();
      if (next?.canceled) return;
      avatarDataUrl = String(next?.personaAvatarDataUrl || avatarDataUrl);
      markDirty();
    });

    clearAvatar.addEventListener("click", () => {
      avatarDataUrl = "";
      markDirty();
    });

    userChooseAvatar.addEventListener("click", async () => {
      const next = await actions.chooseUserAvatar?.();
      if (next?.canceled) return;
      userAvatarDataUrl = String(next?.userAvatarDataUrl || userAvatarDataUrl);
      markDirty();
    });

    userClearAvatar.addEventListener("click", () => {
      userAvatarDataUrl = "";
      markDirty();
    });

    for (const input of [
      replyEnabledInput, outputModeInput, nativeVoiceIdInput, systemVoiceInput,
      customVoiceNameInput, customVoicePathInput, sampleConsentInput,
      rateInput, pitchInput, volumeInput,
      personaInput, bodyPresetInput,
      userNameInput, userTitleInput
    ]) {
      input.addEventListener("input", markDirty);
      input.addEventListener("change", markDirty);
    }

    saveButton.addEventListener("click", async () => {
      setSaveState("保存中");
      saveButton.disabled = true;
      try {
        const saved = await saveSettingsWithRetry(actions, currentSettingsPatch());
        dirty = false;
        renderSettings(saved);
        setSaveState("已保存", "ok");
      } catch (error) {
        setSaveState(error?.message || "保存失败", "failed");
      } finally {
        saveButton.disabled = false;
      }
    });

    if (supportsSpeech()) {
      const updateVoices = () => {
        const selected = systemVoiceInput.value;
        renderVoiceOptions(selected);
      };
      window.speechSynthesis.addEventListener?.("voiceschanged", updateVoices);
      renderVoiceOptions();
    }

    state.on("page", renderPage);
    state.on("settings", renderSettings);
    const snap = state.snapshot();
    renderPage(snap.activePage);
    renderSettings(snap.settings);
  }
};
