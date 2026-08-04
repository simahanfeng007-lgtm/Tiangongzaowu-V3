import { cleanSkillDisplayText, skillDisplayDescription, skillDisplayName } from "../core/skill-labels.mjs";

function escHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeArray(value) {
  return Array.isArray(value) ? value : [];
}

function numberValue(value) {
  const next = Number(value || 0);
  return Number.isFinite(next) ? next : 0;
}

function formatDate(value) {
  const timestamp = Number(value || 0);
  if (!timestamp) return "未记录";
  try {
    return new Date(timestamp * (timestamp < 100000000000 ? 1000 : 1)).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
  } catch {
    return "未记录";
  }
}

function statusLabel(value) {
  const text = String(value || "");
  const names = {
    active: "可用",
    candidate: "候选",
    review_ready: "待审阅",
    draft: "草稿",
    disabled: "停用",
    pending: "待处理",
    pending_activation: "待激活",
    failed: "异常"
  };
  return names[text] || cleanSkillDisplayText(text) || "未标记";
}

function statusClass(ability) {
  // P2-11: a failed skill must never be downgraded to a warning just because
  // it is also not currently usable.
  if (ability?.status === "failed") return "failed";
  if (!ability?.runtimeUsable || ability?.status === "disabled" || ability?.status === "review_ready") return "warn";
  if (ability?.status === "active") return "ok";
  return "";
}

function chipList(values, emptyText = "未配置") {
  const items = safeArray(values).map(cleanSkillDisplayText).filter(Boolean).slice(0, 8);
  if (!items.length) return `<span class="skill-chip muted">${escHtml(emptyText)}</span>`;
  return items.map((item) => `<span class="skill-chip">${escHtml(item)}</span>`).join("");
}

function abilityKey(ability) {
  return String(ability?.id || ability?.name || "").trim();
}

function visibleToolNames(ability) {
  const toolNames = safeArray(ability?.toolNames).map(cleanSkillDisplayText).filter(Boolean);
  const toolRefs = safeArray(ability?.toolPackageRefs).map(cleanSkillDisplayText).filter(Boolean);
  return toolNames.length ? toolNames : toolRefs;
}

function toolReferenceInfo(ability) {
  const tools = visibleToolNames(ability);
  if (tools.length) {
    return {
      summary: tools.slice(0, 5).join(" / "),
      count: Math.max(safeArray(ability?.toolPackageRefs).length, safeArray(ability?.toolNames).length)
    };
  }
  if (ability?.modelVisibleSkill && abilityKey(ability)) {
    return { summary: `skill_${abilityKey(ability)}`, count: 1 };
  }
  if (ability?.candidateOnly || ability?.reviewRequired || ability?.status === "review_ready") {
    return { summary: "候选审阅中，尚未绑定运行工具", count: 0 };
  }
  return { summary: "未绑定工具引用", count: 0 };
}

function canDeleteAbility(ability) {
  if (!ability || ability.source === "backend_tool_registry") return false;
  if (ability.canDelete === true) return true;
  const source = String(ability.source || "").trim();
  return ["learning_pipeline", "zizhu_xuexi", "autonomous_learning", "xuexi_lian", "learning_registry"].includes(source)
    || String(ability.id || "").startsWith("nengli_")
    || safeArray(ability.toolNames).some((name) => String(name || "").startsWith("skill_nengli_"));
}

function canActivateAbility(ability) {
  return Boolean(
    ability
    && ability.source === "life_learning"
    && ability.canActivate === true
    && ability.status === "pending_activation"
  );
}

export const skillsPanelPlugin = {
  id: "skills-panel",
  slot: "conversation",
  order: 216,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section class="page-panel skills-page" data-page-panel="skills">
          <header class="page-header">
            <div class="title-group">
              <span class="caption">技能</span>
              <h2>技能与能力包</h2>
            </div>
            <div class="commandbar-meta">
              <span id="skillsPanelState" class="mini-pill">未读取</span>
              <button id="skillsPanelRefresh" class="small-command" type="button">刷新</button>
            </div>
          </header>

          <section class="page-body skills-body">
            <section class="panel-card skills-list-card">
              <div class="panel-title">
                <span id="skillsListTitle">能力包列表</span>
                <span id="skillsListCount" class="mini-pill">0 个</span>
              </div>
              <div id="skillsList" class="skills-list"></div>
            </section>
          </section>
        </section>
      `
    );

    const panel = slot.querySelector('[data-page-panel="skills"]');
    const statePill = panel.querySelector("#skillsPanelState");
    const refreshButton = panel.querySelector("#skillsPanelRefresh");
    const listTitle = panel.querySelector("#skillsListTitle");
    const listCount = panel.querySelector("#skillsListCount");
    const listEl = panel.querySelector("#skillsList");
    let catalog = { ok: null, categories: [], abilities: [], summary: {} };
    let loaded = false;
    let loading = false;
    let lastLoadedAt = 0;
    const deletingIds = new Set();
    const activatingIds = new Set();

    function setState(text, ok = null) {
      statePill.textContent = text;
      statePill.className = `mini-pill ${ok === true ? "ok" : ok === false ? "failed" : ""}`;
    }

    function activeCategory() {
      return state.snapshot().activeSkillCategory || "all";
    }

    function categoryLabel(id) {
      if (id === "all") return "全部分类";
      const category = safeArray(catalog.categories).find((item) => item.id === id);
      return cleanSkillDisplayText(category?.label || id) || "未分类";
    }

    function visibleAbilities() {
      const category = activeCategory();
      const abilities = safeArray(catalog.abilities);
      return category === "all" ? abilities : abilities.filter((item) => item.category === category);
    }

    function renderAbility(ability) {
      const tone = statusClass(ability);
      const id = abilityKey(ability);
      const displayName = cleanSkillDisplayText(skillDisplayName(ability));
      const displayDescription = cleanSkillDisplayText(skillDisplayDescription(ability));
      const toolInfo = toolReferenceInfo(ability);
      const risk = cleanSkillDisplayText(ability.riskLevel || ability.maxDangerLevel || (ability.requiresConfirmation ? "需确认" : "常规"));
      const visibleId = cleanSkillDisplayText(ability.id || "未记录 ID") || "未记录 ID";
      const deletable = canDeleteAbility(ability);
      const activatable = canActivateAbility(ability);
      const deleting = deletingIds.has(id);
      const activating = activatingIds.has(id);
      const learnedActions = deletable ? `
        <div class="skill-card-actions">
          <button class="skill-delete-control" type="button" data-delete-skill="${escHtml(id)}" ${deleting || activating ? "disabled" : ""} title="删除这个已学习技能及其生成执行包">${deleting ? "删除中" : "删除"}</button>
          <button class="skill-activate-control ${ability.runtimeUsable ? "available" : ""}" type="button" data-activate-skill="${escHtml(id)}" ${activating || deleting || !activatable ? "disabled" : ""} title="${ability.runtimeUsable ? "这个技能已注册可用" : "正式注册这个技能"}">${activating ? "激活中" : ability.runtimeUsable ? "可用" : "激活"}</button>
        </div>
      ` : `<span class="mini-pill ${escHtml(tone)}">${escHtml(statusLabel(ability.status))}</span>`;
      return `
        <article class="skill-card ${escHtml(tone)}" data-skill-id="${escHtml(id)}">
          <div class="skill-card-head">
            <div>
              <strong>${escHtml(displayName || cleanSkillDisplayText(ability.name) || visibleId || "未命名能力包")}</strong>
              <span>${escHtml(visibleId)}</span>
            </div>
            ${learnedActions}
          </div>
          <p class="skill-desc">${escHtml(displayDescription || "暂无能力说明")}</p>
          <div class="skill-meta-grid">
            <div><span>分类</span><strong>${escHtml(categoryLabel(ability.category))}</strong></div>
            <div><span>等级</span><strong>${escHtml(cleanSkillDisplayText(ability.level) || "未标记")}</strong></div>
            <div><span>风险</span><strong>${escHtml(risk || "常规")}</strong></div>
            <div><span>更新</span><strong>${escHtml(formatDate(ability.updatedAt))}</strong></div>
          </div>
          <div class="skill-chip-row">
            ${chipList(ability.taskIntents, "未配置意图")}
          </div>
          <div class="skill-tool-row">
            <span>工具引用</span>
            <strong title="${escHtml(toolInfo.summary)}">${escHtml(toolInfo.summary)}</strong>
            <em>${numberValue(toolInfo.count)} 项</em>
          </div>
        </article>
      `;
    }

    function render() {
      const category = activeCategory();
      const abilities = visibleAbilities();
      listTitle.textContent = categoryLabel(category);
      listCount.textContent = `${abilities.length} 个`;
      if (loading && !loaded) {
        listEl.innerHTML = `<div class="knowledge-empty">正在读取技能资产</div>`;
        return;
      }
      if (catalog.ok === false) {
        listEl.innerHTML = `<div class="knowledge-empty failed">${escHtml(catalog.error || "技能资产读取失败")}</div>`;
        return;
      }
      if (!abilities.length) {
        listEl.innerHTML = `<div class="knowledge-empty">这个分类下暂时没有技能包</div>`;
        return;
      }
      listEl.innerHTML = abilities.map(renderAbility).join("");
    }

    async function refreshSkills(quiet = false) {
      if (loading) return;
      loading = true;
      refreshButton.disabled = true;
      if (!quiet) setState("读取中");
      render();
      try {
        const result = await actions.listSkills?.();
        catalog = {
          ok: result?.ok !== false,
          categories: safeArray(result?.categories),
          abilities: safeArray(result?.abilities),
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
      const active = page === "skills";
      panel.classList.toggle("active", active);
      if (active && (!loaded || Date.now() - lastLoadedAt > 2500)) void refreshSkills(loaded);
    }

    refreshButton.addEventListener("click", () => refreshSkills());
    listEl.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-delete-skill], [data-activate-skill]");
      if (!button) return;
      const activating = button.hasAttribute("data-activate-skill");
      const id = activating ? (button.dataset.activateSkill || "") : (button.dataset.deleteSkill || "");
      const ability = safeArray(catalog.abilities).find((item) => abilityKey(item) === id);
      if (activating) {
        if (!ability || !canActivateAbility(ability)) {
          setState(ability?.runtimeUsable ? "技能已经可用" : "这个技能不能激活", false);
          return;
        }
        activatingIds.add(id);
        setState("激活中");
        render();
        try {
          const result = await actions.activateSkill?.({ artifact_id: ability.artifactId || id });
          if (!result?.ok) {
            setState(result?.error || "激活失败", false);
            return;
          }
          setState("已激活", true);
          await refreshSkills(true);
        } catch (error) {
          setState(error?.message || "激活失败", false);
        } finally {
          activatingIds.delete(id);
          render();
        }
        return;
      }
      if (!ability || !canDeleteAbility(ability)) {
        setState("核心技能不能删除", false);
        return;
      }
      const displayName = cleanSkillDisplayText(skillDisplayName(ability)) || id;
      const ok = window.confirm(`删除已学习技能「${displayName}」？\n\n将删除这个学习生成的 Skill 和对应动态执行包；它引用的系统原有工具能力不会被删除。`);
      if (!ok) return;
      deletingIds.add(id);
      setState("删除中");
      render();
      try {
        const result = await actions.deleteSkill?.({ artifact_id: ability.artifactId || id });
        if (!result?.ok) {
          setState(result?.error || "删除失败", false);
          return;
        }
        setState("已删除", true);
        await refreshSkills(true);
      } catch (error) {
        setState(error?.message || "删除失败", false);
      } finally {
        deletingIds.delete(id);
        render();
      }
    });
    state.on("page", renderPage);
    state.on("runProgress", (progress) => {
      if (state.snapshot().activePage === "skills" && progress?.phase === "finished") void refreshSkills(true);
    });
    state.on("skillCategory", render);

    renderPage(state.snapshot().activePage);
    render();
  }
};
