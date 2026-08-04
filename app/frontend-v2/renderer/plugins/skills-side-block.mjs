function escHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function numberValue(value) {
  const next = Number(value || 0);
  return Number.isFinite(next) ? next : 0;
}

function categoryTotal(categories, key) {
  return categories.reduce((sum, item) => sum + numberValue(item[key]), 0);
}

function safeArray(value) {
  return Array.isArray(value) ? value : [];
}

function uniqueAbilityCount(abilities) {
  const ids = new Set();
  for (const ability of safeArray(abilities)) {
    const key = String(ability?.id || ability?.name || "").trim().toLowerCase();
    if (key) ids.add(key);
  }
  return ids.size || safeArray(abilities).length;
}

function uniqueToolNames(abilities) {
  const names = new Set();
  for (const ability of safeArray(abilities)) {
    for (const key of ["toolNames", "toolPackageRefs"]) {
      for (const item of safeArray(ability?.[key])) {
        const name = String(item || "").trim();
        if (name) names.add(name);
      }
    }
  }
  return names;
}

function summaryCount(summary, key, fallback) {
  return Object.prototype.hasOwnProperty.call(summary || {}, key)
    ? numberValue(summary[key])
    : fallback;
}

export const skillsSideBlockPlugin = {
  id: "skills-side-block",
  slot: "context",
  order: 112,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section id="skillsSidePanel" class="side-section skills-side-panel" hidden>
          <div class="section-heading">
            <span>工具分类</span>
            <button id="skillsSideRefresh" class="small-command" type="button">刷新</button>
          </div>

          <div class="skills-side-summary">
            <div>
              <span>技能包</span>
              <strong id="skillsSideAbilityCount">0</strong>
            </div>
            <div>
              <span>运行工具</span>
              <strong id="skillsSideToolCount">0</strong>
            </div>
          </div>

          <div id="skillsSideState" class="mini-pill">未读取</div>
          <div id="skillsCategoryList" class="skills-category-list"></div>
        </section>
      `
    );

    const panel = slot.querySelector("#skillsSidePanel");
    const refreshButton = panel.querySelector("#skillsSideRefresh");
    const statePill = panel.querySelector("#skillsSideState");
    const abilityCountEl = panel.querySelector("#skillsSideAbilityCount");
    const toolCountEl = panel.querySelector("#skillsSideToolCount");
    const listEl = panel.querySelector("#skillsCategoryList");
    let catalog = { ok: null, categories: [], abilities: [], summary: {} };
    let loaded = false;
    let loading = false;
    let lastLoadedAt = 0;

    function setState(text, ok = null) {
      statePill.textContent = text;
      statePill.className = `mini-pill ${ok === true ? "ok" : ok === false ? "failed" : ""}`;
    }

    function currentCategory() {
      return state.snapshot().activeSkillCategory || "all";
    }

    function render() {
      const categories = Array.isArray(catalog.categories) ? catalog.categories : [];
      const summary = catalog.summary || {};
      const active = currentCategory();
      const totalAbilities = summaryCount(summary, "abilityCount", uniqueAbilityCount(catalog.abilities));
      const totalTools = summaryCount(
        summary,
        "skillEntryCount",
        summaryCount(summary, "runtimeToolCount", summaryCount(summary, "toolCount", uniqueToolNames(catalog.abilities).size || categoryTotal(categories, "toolCount")))
      );

      abilityCountEl.textContent = String(totalAbilities);
      toolCountEl.textContent = String(totalTools);

      const allActive = active === "all" ? " active" : "";
      const rows = [
        `
          <button class="skills-category-item${allActive}" type="button" data-skill-category="all">
            <span class="skills-category-head">
              <strong>全部分类</strong>
              <em>${totalTools} 运行工具</em>
            </span>
            <span class="skills-category-desc">汇总所有模型可见能力分类，只用于查看与筛选。</span>
            <span class="skills-category-meta">${totalTools} 个运行工具</span>
          </button>
        `,
        ...categories.map((item) => {
          const id = String(item.id || "other");
          const isActive = active === id ? " active" : "";
          return `
            <button class="skills-category-item${isActive}" type="button" data-skill-category="${escHtml(id)}">
              <span class="skills-category-head">
                <strong>${escHtml(item.label || id)}</strong>
                <em>${numberValue(item.abilityCount)} 技能</em>
              </span>
              <span class="skills-category-desc">${escHtml(item.description || "等待分类说明")}</span>
              <span class="skills-category-meta">${numberValue(item.toolCount)} 个运行工具</span>
            </button>
          `;
        })
      ];

      listEl.innerHTML = rows.join("");
    }

    async function refreshSkills(quiet = false) {
      if (loading) return;
      loading = true;
      refreshButton.disabled = true;
      if (!quiet) setState("读取中");
      try {
        const result = await actions.listSkills?.();
        catalog = {
          ok: result?.ok !== false,
          categories: Array.isArray(result?.categories) ? result.categories : [],
          abilities: Array.isArray(result?.abilities) ? result.abilities : [],
          summary: result?.summary || {},
          error: result?.error || ""
        };
        loaded = true;
        lastLoadedAt = Date.now();
        setState(catalog.ok ? "已同步" : (catalog.error || "读取失败"), catalog.ok);
      } catch (error) {
        catalog = { ok: false, categories: [], abilities: [], summary: {}, error: error?.message || String(error) };
        loaded = true;
        setState(catalog.error || "读取失败", false);
      } finally {
        loading = false;
        refreshButton.disabled = false;
        render();
      }
    }

    function renderPage(page) {
      panel.hidden = page !== "skills";
      if (page === "skills" && (!loaded || Date.now() - lastLoadedAt > 2500)) void refreshSkills(loaded);
    }

    listEl.addEventListener("click", (event) => {
      const button = event.target.closest("[data-skill-category]");
      if (!button) return;
      actions.setActiveSkillCategory?.(button.dataset.skillCategory || "all");
    });
    refreshButton.addEventListener("click", () => refreshSkills());

    state.on("page", renderPage);
    state.on("runProgress", (progress) => {
      if (state.snapshot().activePage === "skills" && progress?.phase === "finished") void refreshSkills(true);
    });
    state.on("skillCategory", render);

    renderPage(state.snapshot().activePage);
    render();
  }
};
