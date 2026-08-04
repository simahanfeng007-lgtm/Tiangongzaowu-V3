import { translateValue } from "../core/formatters.mjs";

function escHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function metric(label, value, hint = "", tone = "") {
  return `
    <article class="persona-side-metric ${escHtml(tone)}">
      <span>${escHtml(label)}</span>
      <strong>${escHtml(value)}</strong>
      <small>${escHtml(hint)}</small>
    </article>
  `;
}

function characterCount(value) {
  return Array.from(String(value || "")).length;
}

function modelAvatar(settings) {
  if (settings.personaAvatarDataUrl) {
    return `<img src="${escHtml(settings.personaAvatarDataUrl)}" alt="模型头像" />`;
  }
  return `<span>${escHtml((settings.personaName || "临").slice(0, 1))}</span>`;
}

export const personaSideBlockPlugin = {
  id: "persona-side-block",
  slot: "context",
  order: 112,
  mount({ slot, state }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section id="personaSidePanel" class="side-section persona-side-panel" hidden>
          <div class="section-heading">
            <span>身体摘要</span>
            <span id="personaSideState" class="mini-pill">同步中</span>
          </div>
          <div id="personaSideMetrics" class="persona-side-metrics"></div>
          <div class="persona-model-card">
            <div class="persona-model-title">
              <span>角色状态</span>
              <small>只读映射</small>
            </div>
            <div class="persona-model-body">
              <div id="personaSideAvatar" class="persona-side-avatar"></div>
              <div id="personaSideRows" class="persona-side-rows"></div>
            </div>
          </div>
        </section>
      `
    );

    const panel = slot.querySelector("#personaSidePanel");
    const statePill = panel.querySelector("#personaSideState");
    const metrics = panel.querySelector("#personaSideMetrics");
    const avatar = panel.querySelector("#personaSideAvatar");
    const rows = panel.querySelector("#personaSideRows");

    function renderPage(page) {
      panel.hidden = page !== "body";
    }

    function render() {
      const snap = state.snapshot();
      const settings = snap.settings || {};
      const affective = snap.runtimeStatus?.payload?.runtime?.interface_wiring?.affective || {};
      const affectiveReady = Boolean(affective.emotion_engine_attached || affective.enabled || affective.status === "ok");
      const callsign = settings.userCallsign || settings.userAlias || "待补充";
      const work = settings.userWork || "待补充";
      const contextEnabled = settings.userContextEnabled !== false;

      statePill.textContent = snap.runtimeStatus?.ok === false ? "待连接" : "正常";
      statePill.className = `mini-pill ${snap.runtimeStatus?.ok === false ? "warn" : "ok"}`;
      metrics.innerHTML = [
        metric("称谓", callsign, "模型称呼用户"),
        metric("工作", work, "用户角色"),
        metric("上下文卡", contextEnabled ? "开启" : "关闭", "后续提示词拼接", contextEnabled ? "running" : ""),
        metric("人格底稿字数", characterCount(settings.soulPrompt), "模型人格底稿")
      ].join("");

      avatar.innerHTML = modelAvatar(settings);
      rows.innerHTML = [
        ["模型名", settings.personaName || "临渊者"],
        ["状态", affectiveReady ? "情感在线" : "平静待机"],
        ["声线", settings.bodyVoiceCustomName || settings.bodyVoicePreset || "起源清声"],
        ["朗读", settings.bodyVoiceReplyEnabled ? "开启" : "关闭"],
        ["主题", translateValue(settings.themeStyle || "ink_teal")],
        ["头像", settings.personaAvatarDataUrl ? "已设置" : "默认"]
      ].map(([label, value]) => `
        <div class="persona-side-row">
          <span>${escHtml(label)}</span>
          <strong>${escHtml(value)}</strong>
        </div>
      `).join("");
    }

    state.on("page", renderPage);
    state.on("settings", render);
    state.on("runtimeStatus", render);
    const snap = state.snapshot();
    renderPage(snap.activePage);
    render();
  }
};
