import { lifeApi } from "../runtime/life-api.mjs";

export const lifeIdentitySideBlockPlugin = {
  id: "life-identity-side-block",
  slot: "context",
  order: 135,
  mount({ slot, state }) {
    const section = document.createElement("section");
    section.className = "side-section life-identity-side-block";
    section.hidden = true;
    section.innerHTML = `
      <div class="section-heading"><span>生命身份</span></div>
      <div class="life-identity-side-grid">
        <div class="life-side-form">
          <strong>新建生命</strong>
          <label class="life-setting-field">
            <span>生命名称</span>
            <input type="text" data-side-create-name value="起源" maxlength="40" />
          </label>
          <div class="life-action-row">
            <button type="button" data-side-identity-create>确认新建生命</button>
          </div>
        </div>
        <div class="life-side-form">
          <strong>绑定已有身份</strong>
          <label class="life-setting-field">
            <span>生命数据目录</span>
            <input type="text" data-side-bind-root placeholder="选择包含 identity 文件夹的生命目录" />
          </label>
          <div class="life-action-row">
            <button type="button" data-side-bind-choose>选择目录</button>
            <button type="button" data-side-identity-bind>验证并绑定</button>
          </div>
        </div>
      </div>
    `;
    slot.insertAdjacentElement("beforeend", section);

    function renderPage(page) {
      section.hidden = page !== "lifecycle";
      if (page === "lifecycle") {
        lifeApi.getPanel()
          .then((payload) => {
            const root = payload?.identity?.root || "";
            const input = section.querySelector("[data-side-bind-root]");
            if (input && root) input.value = root;
          })
          .catch(() => {});
      }
    }
    state.on("page", renderPage);
    renderPage(state.snapshot().activePage);

    section.addEventListener("click", async (event) => {
      const button = event.target.closest("button");
      if (!button || button.disabled) return;

      const run = async (label, fn) => {
        const previous = button.textContent;
        button.disabled = true;
        button.textContent = "处理中";
        try {
          await fn();
          window.dispatchEvent(new CustomEvent("life:identity-changed"));
        } catch (_error) {
          // 失败保持界面可重试，不吞掉后续流程。
        } finally {
          button.textContent = previous;
          button.disabled = false;
        }
      };

      if (event.target.closest("[data-side-identity-create]")) {
        const name = section.querySelector("[data-side-create-name]")?.value || "起源";
        await run("确认新建生命", () => lifeApi.createIdentity(name));
        return;
      }
      if (event.target.closest("[data-side-bind-choose]")) {
        const bridge = window.tiangongDesktop;
        if (bridge?.chooseStorageRoot) {
          const result = await bridge.chooseStorageRoot({ purpose: "lifeIdentity" });
          if (!result?.canceled && result?.path) {
            const input = section.querySelector("[data-side-bind-root]");
            if (input) input.value = result.path;
          }
        }
        return;
      }
      if (event.target.closest("[data-side-identity-bind]")) {
        const root = section.querySelector("[data-side-bind-root]")?.value || "";
        if (root) await run("验证并绑定", () => lifeApi.bindIdentity(root));
        return;
      }
    });
  },
};
