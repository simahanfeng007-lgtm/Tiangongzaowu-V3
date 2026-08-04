const DEFAULT_LOGO_SRC = "../assets/tiangong-avatar.png";

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

export const personaPanelPlugin = {
  id: "persona-panel",
  slot: "conversation",
  order: 215,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML("beforeend", `
      <section class="page-panel persona-page" data-page-panel="persona">
        <header class="page-header">
          <div class="title-group">
            <span class="caption">角色</span>
            <h2>角色设定</h2>
          </div>
          <div class="commandbar-meta">
            <span id="personaSaveState" class="mini-pill">未修改</span>
            <button id="personaSave" class="small-command" type="button">保存</button>
          </div>
        </header>

        <section class="page-body persona-body">
          <section class="panel-card persona-identity-card">
            <div class="panel-title"><span>AI 角色</span><span class="mini-pill">头像 · 名字 · Soul</span></div>

            <div class="persona-avatar-editor">
              <div id="personaAvatarPreview" class="persona-avatar-preview"></div>
              <div class="avatar-actions">
                <button id="personaChooseAvatar" class="small-command" type="button">选择头像</button>
                <button id="personaClearAvatar" class="small-command muted-command" type="button">清除</button>
              </div>
            </div>

            <div class="settings-form">
              <label class="field-row">
                <span>角色名</span>
                <input id="personaName" type="text" maxlength="32" placeholder="起源" />
              </label>
              <p class="field-hint">角色名是人格呈现名，可与权威身份名不同；身份名由生命系统管理。</p>
            </div>
          </section>

          <section class="panel-card wide-card persona-soul-card">
            <div class="panel-title"><span>后台 Soul</span><span class="mini-pill">写入角色设定</span></div>
            <textarea id="personaSoulPrompt" class="persona-textarea" maxlength="6000" placeholder="留空时使用后台默认 Soul。这里可以写角色底色、说话方式、边界和执行偏好。"></textarea>
            <div class="textarea-meta">
              <span id="soulPromptCount">0 / 6000</span>
              <button id="personaClearSoul" class="small-command muted-command" type="button">清空</button>
            </div>
          </section>

          <section class="panel-card persona-user-card">
            <div class="panel-title"><span>用户信息</span><span class="mini-pill">称谓 · 工作</span></div>

            <div class="persona-avatar-editor">
              <div id="userAvatarPreview" class="persona-avatar-preview"></div>
              <div class="avatar-actions">
                <button id="userChooseAvatar" class="small-command" type="button">选择头像</button>
                <button id="userClearAvatar" class="small-command muted-command" type="button">清除</button>
              </div>
            </div>

            <div class="settings-form">
              <label class="field-row">
                <span>你的名字</span>
                <input id="userName" type="text" maxlength="32" placeholder="公子" />
              </label>
              <label class="field-row">
                <span>身份 / 工作</span>
                <input id="userTitle" type="text" maxlength="64" placeholder="如：全栈工程师、独立开发者" />
              </label>
            </div>
          </section>
        </section>
      </section>
    `);

    const panel = slot.querySelector('[data-page-panel="persona"]');
    const saveState = panel.querySelector("#personaSaveState");
    const saveButton = panel.querySelector("#personaSave");
    const nameInput = panel.querySelector("#personaName");
    const avatarPreview = panel.querySelector("#personaAvatarPreview");
    const chooseAvatar = panel.querySelector("#personaChooseAvatar");
    const clearAvatar = panel.querySelector("#personaClearAvatar");
    const userAvatarPreview = panel.querySelector("#userAvatarPreview");
    const userChooseAvatar = panel.querySelector("#userChooseAvatar");
    const userClearAvatar = panel.querySelector("#userClearAvatar");
    const userNameInput = panel.querySelector("#userName");
    const userTitleInput = panel.querySelector("#userTitle");
    const soulInput = panel.querySelector("#personaSoulPrompt");
    const soulCount = panel.querySelector("#soulPromptCount");
    const clearSoul = panel.querySelector("#personaClearSoul");
    let dirty = false;
    let avatarDataUrl = "";
    let userAvatarDataUrl = "";

    function setSaveState(label, className = "") {
      if (!saveState) return;
      saveState.textContent = label;
      saveState.className = `mini-pill ${className}`.trim();
    }

    function updateCount() {
      soulCount.textContent = `${soulInput.value.length} / 6000`;
    }

    function markDirty() {
      dirty = true;
      setSaveState("待保存", "warn");
      updateCount();
    }

    function renderPage(page) {
      panel.classList.toggle("active", page === "persona");
    }

    function renderSettings(settings) {
      if (dirty) return;
      nameInput.value = personaName(settings);
      avatarDataUrl = String(settings.personaAvatarDataUrl || "");
      userAvatarDataUrl = String(settings.userAvatarDataUrl || "");
      soulInput.value = String(settings.soulPrompt || "");
      userNameInput.value = String(settings.userName || "");
      userTitleInput.value = String(settings.userTitle || "");
      renderAvatar(avatarPreview, settings);
      renderAvatar(userAvatarPreview, { personaAvatarDataUrl: userAvatarDataUrl });
      updateCount();
      setSaveState("未修改");
    }

    function collectSettingsPayload() {
      return {
        personaName: nameInput.value.trim() || "起源",
        personaAvatarDataUrl: avatarDataUrl,
        soulPrompt: soulInput.value.trim(),
        userName: userNameInput.value.trim(),
        userTitle: userTitleInput.value.trim(),
        userAvatarDataUrl: userAvatarDataUrl,
      };
    }

    saveButton.addEventListener("click", async () => {
      setSaveState("保存中");
      saveButton.disabled = true;
      try {
        const saved = await actions.saveSettings(collectSettingsPayload());
        dirty = false;
        renderSettings(saved);
        setSaveState("已保存", "ok");
      } catch (error) {
        setSaveState(error?.message || "保存失败", "failed");
      } finally {
        saveButton.disabled = false;
      }
    });

    chooseAvatar.addEventListener("click", async () => {
      const previousAvatar = avatarDataUrl;
      const next = await actions.choosePersonaAvatar();
      const selectedAvatar = String(next?.personaAvatarDataUrl || "");
      if (!selectedAvatar || selectedAvatar === previousAvatar) return;
      avatarDataUrl = selectedAvatar;
      renderAvatar(avatarPreview, { personaAvatarDataUrl: avatarDataUrl });
      markDirty();
    });

    clearAvatar.addEventListener("click", () => {
      avatarDataUrl = "";
      renderAvatar(avatarPreview, { personaAvatarDataUrl: "" });
      markDirty();
    });

    userChooseAvatar.addEventListener("click", async () => {
      const next = await actions.chooseUserAvatar();
      const selected = String(next?.userAvatarDataUrl || "");
      if (!selected || selected === userAvatarDataUrl) return;
      userAvatarDataUrl = selected;
      renderAvatar(userAvatarPreview, { personaAvatarDataUrl: userAvatarDataUrl });
      markDirty();
    });

    userClearAvatar.addEventListener("click", () => {
      userAvatarDataUrl = "";
      renderAvatar(userAvatarPreview, { personaAvatarDataUrl: "" });
      markDirty();
    });

    clearSoul.addEventListener("click", () => {
      soulInput.value = "";
      markDirty();
    });

    for (const input of [nameInput, soulInput, userNameInput, userTitleInput]) {
      input.addEventListener("change", markDirty);
      input.addEventListener("input", markDirty);
    }

    state.on("page", renderPage);
    state.on("settings", renderSettings);
    const snap = state.snapshot();
    renderPage(snap.activePage);
    renderSettings(snap.settings);
  }
};
