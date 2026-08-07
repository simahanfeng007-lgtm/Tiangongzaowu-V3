const PHASE_NAMES = {
  chenshui: "沉睡",
  fuhuo: "复活",
  fuzhu: "辅助",
  banzizhu: "半自主",
  zizhu: "自主",
  guancha: "观察",
  pinggu: "评估",
  shiyan: "实验",
  gaijin: "改进",
  yanzheng: "验证"
};

const AUTONOMY_NAMES = {
  fuzhu: "辅助",
  banzizhu: "半自主",
  zizhu: "自主",
  wanquan_zizhu: "完全自主"
};

const STATUS_NAMES = {
  empty: "暂无候选",
  candidate: "候选中",
  candidate_ready: "候选就绪",
  review_ready: "待处理",
  model_review: "模型审查",
  draft: "能力草稿",
  sandbox_passed: "沙盒通过",
  active: "已激活",
  disabled: "已停用",
  discarded: "已放弃",
  duplicate_removed: "重复移除",
  no_value: "价值不足",
  pending_card: "待确认学习",
  processing_approved: "已确认待加工",
  draft_ready: "草案待激活",
  quarantined: "已隔离",
  candidate_only: "候选模式",
  pending: "等待中",
  skipped: "已跳过",
  rate_limited: "等待下次 tick",
  ok: "已同步",
  ready: "就绪",
  failed: "失败",
  unavailable: "不可用"
};

const STAGE_NAMES = {
  candidate: "候选",
  pending_card: "待确认学习",
  processing_approved: "已确认待加工",
  model_review: "模型审查",
  draft: "能力草稿",
  draft_ready: "草案待激活",
  sandbox_passed: "沙盒通过",
  review_ready: "待处理",
  active: "已激活",
  quarantined: "已隔离",
  disabled: "已停用"
};

const ACTION_NAMES = {
  confirm_learning: "确认学习",
  process_learning: "生成草案",
  activate_learning: "激活 Skill",
  release_tool: "发布 Tool",
  sandbox_check: "等待检查",
  request_activation: "激活学习",
  monitor: "持续观察",
  none: "无动作"
};

const SKIP_REASON_NAMES = {
  disabled: "开关未启用",
  heartbeat_not_running: "心跳未运行",
  max_consecutive_reached: "连续自主行动达上限",
  user_recently_active: "用户刚活跃",
  curiosity_below_threshold: "好奇心未过阈值",
  "": "暂无阻塞"
};

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pct(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, Math.round(number * 100)));
}

function numberValue(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function friendly(value, names, fallback = "未读取") {
  const text = String(value ?? "").trim();
  if (!text) return fallback;
  return names[text] || text;
}

function metric(label, value, hint = "") {
  return `
    <div class="dash-metric">
      <span>${esc(label)}</span>
      <strong>${esc(value)}</strong>
      ${hint ? `<em>${esc(hint)}</em>` : ""}
    </div>
  `;
}

function overviewCard(label, value, hint = "", tone = "") {
  return `
    <article class="lifecycle-overview-card ${esc(tone)}">
      <span>${esc(label)}</span>
      <strong>${esc(value)}</strong>
      ${hint ? `<small>${esc(hint)}</small>` : ""}
    </article>
  `;
}

function bar(label, value) {
  const width = pct(value);
  return `
    <div class="bar-row">
      <span class="bar-label">${esc(label)}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${width}%"></span></span>
      <strong>${width}%</strong>
    </div>
  `;
}

function row(label, value) {
  return `<div class="kv-row"><span class="kv-key">${esc(label)}</span><span class="kv-value">${esc(value)}</span></div>`;
}

function candidateRow(item = {}, capabilities = {}) {
  const title = item.title || item.summary || "未命名学习卡";
  const summary = item.summary && item.summary !== title ? item.summary : "候选待学习，不会自动注册技能或激活工具。";
  const id = item.card_id || item.id || item.learning_id || "";
  const state = String(item.status || item.promotion_stage || "candidate").toLowerCase();
  const stage = String(item.promotion_stage || item.status || "candidate").toLowerCase();
  const confirmStates = ["awaiting_user", "pending_card", "candidate"];
  const action = item.next_action || (state === "processing_approved" ? "process_learning" : confirmStates.includes(state) || stage === "candidate" ? "confirm_learning" : "request_activation");
  const badges = [
    item.priority || "",
    friendly(state, STAGE_NAMES, friendly(item.status, STATUS_NAMES, "候选")),
    item.auto_drafted ? "自主学习" : "",
    item.risk_level ? `${item.risk_level}${item.risk_label ? ` ${item.risk_label}` : ""}` : "",
    Number.isFinite(Number(item.score)) ? `score ${Number(item.score).toFixed(2)}` : ""
  ].filter(Boolean);
  const canConfirm = capabilities.confirm && id && item.can_confirm_learning && confirmStates.includes(state);
  const canProcess = capabilities.process && id && item.can_process_learning && state === "processing_approved";
  const canRequest = capabilities.request && id && item.can_request_activation && ["draft", "sandbox_passed"].includes(stage);
  const canActivate = capabilities.activate && id && item.can_activate_learning && (state === "draft_ready" || ["review_ready", "sandbox_passed"].includes(stage));
  const canRelease = capabilities.release && id && item.can_release_learning && ["review_ready", "sandbox_passed"].includes(stage);
  const canDiscard = capabilities.discard && id && item.can_discard_learning && !["active", "disabled"].includes(stage) && !["active", "learned", "accepted"].includes(state);
  const autoLearnHint = stage === "candidate" && item.auto_learn_allowed ? "会自动学习" : "";
  const primaryControl = canConfirm
    ? `<button class="small-command" type="button" data-learn-card="${esc(id)}" title="确认后只批准加工，不会自动激活或发布">确认学习</button>`
    : canProcess
      ? `<button class="small-command" type="button" data-process-card="${esc(id)}" title="把已确认学习卡加工为能力草案">生成草案</button>`
      : canRequest
        ? `<button class="small-command" type="button" data-request-activation="${esc(id)}" title="提交激活前复审">申请激活</button>`
        : "";
  const secondaryState = canRelease
    ? `<button class="small-command" type="button" data-release-card="${esc(id)}" title="A3-A4 通过用户审核后发布为模型可调用 Tool">发布 Tool</button>`
    : canActivate
    ? `<button class="small-command" type="button" data-activate-card="${esc(id)}" title="激活为生命系统 Skill，不注册可执行工具">激活 Skill</button>`
    : `<span class="learning-action-state">${esc(
        state === "active" || stage === "active"
          ? "已激活"
          : state === "processing_approved"
            ? "待加工"
          : state === "draft_ready"
            ? "草案待激活"
          : state === "quarantined"
            ? "已隔离"
          : stage === "model_review"
            ? "模型审查"
          : stage === "review_ready"
            ? (item.risk_level && !["A0", "A1", "A2"].includes(item.risk_level) ? "后端审阅" : "待处理")
            : autoLearnHint || (stage === "candidate" ? "学习队列" : "等待")
      )}</span>`;
  const discardControl = canDiscard
    ? `<button class="small-command subtle-command" type="button" data-discard-card="${esc(id)}" title="放弃并从学习队列隐藏这张卡">放弃</button>`
    : "";
  return `
    <div class="learning-candidate-row">
      <div class="learning-candidate-main">
        <strong>${esc(title)}</strong>
        <p>${esc(summary)}</p>
        <div class="learning-candidate-meta">
          ${badges.map((badge) => `<span>${esc(badge)}</span>`).join("")}
        </div>
        <p class="learning-candidate-note">${esc(item.governance_note || friendly(action, ACTION_NAMES, "等待后端状态推进"))}</p>
      </div>
      <div class="learning-candidate-actions">
        ${primaryControl}
        ${secondaryState}
        ${discardControl}
      </div>
    </div>
  `;
}

function renderCandidateList(cards = [], capabilities = {}) {
  if (!cards.length) {
    return `<div class="empty-detail compact">暂无自主学习候选卡。</div>`;
  }
  return `<div class="learning-candidate-list">${cards.slice(0, 5).map((item) => candidateRow(item, capabilities)).join("")}</div>`;
}

export const lifecyclePanelPlugin = {
  id: "lifecycle-panel",
  slot: "conversation",
  order: 218,
  mount({ slot, state, actions, kernel }) {
    slot.insertAdjacentHTML("beforeend", `
      <section class="page-panel lifecycle-page lifecycle-panel-page" data-page-panel="lifecycle">
        <header class="page-header">
          <div class="title-group">
            <span class="caption">生命</span>
            <h2>生命周期系统</h2>
          </div>
          <div class="commandbar-meta">
            <span id="lifeStatePill" class="mini-pill">未读取</span>
            <button id="lifeRefresh" class="small-command" type="button">刷新</button>
          </div>
        </header>

        <section class="page-body lifecycle-body">
          <section class="panel-card lifecycle-ops-card">
            <div class="panel-title"><span>生命周期操作</span><span class="mini-pill">高风险</span></div>
            <p class="field-hint">解绑后，当前生命与桌面会话解除绑定；身份数据保留在生命库中。</p>
            <button id="lifeUnbind" class="small-command danger-command" type="button">解绑当前生命</button>
          </section>
          <section id="lifeOverview" class="lifecycle-overview-grid"></section>

          <section class="lifecycle-main-grid">
            <section class="panel-card lifecycle-learning-card">
              <div class="panel-title"><span>自主学习与确认</span><span id="learningPill" class="mini-pill">未读取</span></div>
              <div id="learningMetrics" class="learning-status-grid lifecycle-learning-summary"></div>
              <div id="learningCandidates" class="learning-candidate-list"></div>
            </section>

          </section>

          <section class="lifecycle-diagnostics-grid">
            <section class="panel-card">
              <div class="panel-title"><span>生命体征</span><span class="mini-pill">后台状态</span></div>
              <div id="lifeMetrics" class="dash-metrics lifecycle-vitals-grid"></div>
            </section>

            <section class="panel-card">
              <div class="panel-title"><span>情绪</span><span class="mini-pill">状态</span></div>
              <div id="emotionBars" class="bar-list"></div>
            </section>
          </section>

          <section class="panel-card lifecycle-system-card">
            <div class="panel-title"><span>系统诊断</span><span class="mini-pill">记忆 / 进化 / 安全</span></div>
            <div class="lifecycle-system-grid">
              <div>
                <h3>记忆</h3>
                <div id="memoryRows" class="kv-list"></div>
              </div>
              <div>
                <h3>进化</h3>
                <div id="evolveRows" class="kv-list"></div>
              </div>
              <div>
                <h3>安全</h3>
                <div id="safetyRows" class="kv-list"></div>
              </div>
            </div>
          </section>
        </section>
      </section>
    `);

    const panel = slot.querySelector('[data-page-panel="lifecycle"].lifecycle-panel-page');
    const pill = panel.querySelector("#lifeStatePill");
    const refresh = panel.querySelector("#lifeRefresh");
    const unbind = panel.querySelector("#lifeUnbind");
    const lifeOverview = panel.querySelector("#lifeOverview");
    const lifeMetrics = panel.querySelector("#lifeMetrics");
    const emotionBars = panel.querySelector("#emotionBars");
    const memoryRows = panel.querySelector("#memoryRows");
    const evolveRows = panel.querySelector("#evolveRows");
    const safetyRows = panel.querySelector("#safetyRows");
    const learningPill = panel.querySelector("#learningPill");
    const learningMetrics = panel.querySelector("#learningMetrics");
    const learningCandidates = panel.querySelector("#learningCandidates");
    let latestLearningCards = [];
    let panelLearning = null;

    async function loadPanelLearning() {
      try {
        const res = await kernel?.request?.("/api/v1/v3/life/panel", {
          method: "GET",
          timeoutMs: 15000,
        });
        if (res && res.learning) {
          panelLearning = res.learning;
          render(state.snapshot().runtimeStatus);
        }
      } catch (_error) {
        // 拉取失败保留上一次数据，面板其它区域不受影响。
      }
    }

    function renderPage(page) {
      panel.classList.toggle("active", page === "lifecycle");
      if (page === "lifecycle") {
        actions.refreshStatus?.();
        loadPanelLearning();
      }
    }

    // P2-16: a user-triggerable unbind entry for the lifecycle identity.
    unbind?.addEventListener("click", async () => {
      const life = state.snapshot()?.life || {};
      const lifeId = String(life?.identity?.life_id || life?.life_id || life?.authority?.life_id || "").trim();
      if (!lifeId) {
        pill.textContent = "无已绑定生命";
        pill.className = "mini-pill warn";
        return;
      }
      if (!window.confirm(`确定解绑生命 ${lifeId.slice(0, 16)}…？`)) return;
      try {
        const result = await kernel?.request?.("/api/v1/v3/life/identity/unbind", {
          method: "POST",
          body: { life_id: lifeId, reason: "user_unbind" },
        });
        if (result?.ok === false) {
          pill.textContent = result?.error || "解绑失败";
          pill.className = "mini-pill failed";
          return;
        }
        pill.textContent = "已解绑";
        pill.className = "mini-pill ok";
        actions.refreshStatus?.();
      } catch (error) {
        pill.textContent = error?.message || "解绑失败";
        pill.className = "mini-pill failed";
      }
    });

    function render(runtimeStatus) {
      const payload = runtimeStatus?.payload || {};
      const runtime = payload.runtime || {};
      const body = runtime.body || {};
      const life = runtime.lifecycle || {};
      const qinggan = runtime.qinggan || {};
      const memory = body.jiyi_tongji || {};
      const jinhua = runtime.jinhua || {};
      const anquan = runtime.anquan || {};
      const freeWill = payload.lifecycle?.free_will || runtime.free_will || {};
      const learning = panelLearning || payload.learning || {};
      const legacyCards = learning.learning_cards || {};
      const latestCards = Array.isArray(learning.latest)
        ? learning.latest
        : Array.isArray(legacyCards.latest)
          ? legacyCards.latest
          : [];
      const cards = {
        latest: latestCards,
        total: numberValue(learning.candidate_count) || numberValue(legacyCards.total),
        candidate: numberValue(learning.candidate_count) || numberValue(legacyCards.candidate),
        draft: legacyCards.draft,
        seconds_until_next: legacyCards.seconds_until_next,
        last_reason: legacyCards.last_reason,
      };
      const ok = runtimeStatus?.ok === true;
      const draftCount = numberValue(cards.draft);
      const candidateCount = numberValue(cards.candidate);
      const autoLearnable = latestCards.filter((card) => {
        const stage = card.promotion_stage || card.status || "candidate";
        return stage === "candidate" && card.auto_learn_allowed;
      }).length;
      const manualReady = latestCards.filter((card) => {
        const state = String(card.status || card.promotion_stage || "candidate").toLowerCase();
        const stage = String(card.promotion_stage || card.status || "candidate").toLowerCase();
        return Boolean(card.can_confirm_learning)
          || Boolean(card.can_process_learning)
          || Boolean(card.can_activate_learning)
          || Boolean(card.can_release_learning)
          || ["pending_card", "processing_approved", "draft_ready"].includes(state)
          || (stage === "candidate" && !card.auto_learn_allowed);
      }).length;
      const hasAction = Boolean(freeWill.latest_autonomous_action?.trace_id);
      const blocked = Boolean(freeWill.skip_reason);
      const freeWillRunning = Boolean(freeWill.heartbeat_running);
      const nextTickText = cards.seconds_until_next === null || typeof cards.seconds_until_next === "undefined" ? "未知" : `${cards.seconds_until_next}s`;

      pill.textContent = runtimeStatus?.loading ? "刷新中" : ok ? "已同步" : "离线";
      pill.className = `mini-pill ${ok ? "ok" : runtimeStatus?.loading ? "" : "failed"}`;
      lifeOverview.innerHTML = [
        overviewCard("生命状态", freeWillRunning ? "正在运行" : ok ? "已同步" : "离线", `阶段 ${friendly(life.phase, PHASE_NAMES, "复活")} / 成长 ${pct(life.growth)}%`, ok ? "ok" : "failed"),
        overviewCard("自主学习", `${draftCount} 草稿 / ${autoLearnable} 自主学`, `需确认 ${manualReady}，下次 tick ${nextTickText}`, manualReady ? "warn" : "ok"),
        overviewCard("安全边界", friendly(anquan.zizhu_jibie, AUTONOMY_NAMES, "辅助"), `连续自主 ${numberValue(anquan.lianxu_zizhu_xingdong || freeWill.consecutive_actions)} / 信任 ${pct(anquan.xinren_jiaozhun ?? 0.5)}%`, "neutral")
      ].join("");
      lifeMetrics.innerHTML = [
        metric("阶段", friendly(life.phase, PHASE_NAMES, "复活"), "当前生命周期"),
        metric("成长", `${pct(life.growth)}%`, "学习沉淀"),
        metric("生命力", `${pct(life.vitality ?? 1)}%`, "活跃程度"),
        metric("唤醒", Number(life.wakeCount || 0), "累计次数"),
        metric("沉默", `${Math.round(Number(life.silenceSeconds || 0))}s`, "最近间隔")
      ].join("");
      emotionBars.innerHTML = [
        bar("喜悦", qinggan.joy),
        bar("担忧", qinggan.worry),
        bar("思考", qinggan.thoughtfulness),
        bar("惊讶", qinggan.surprise),
        bar("好奇", freeWill.curiosity ?? qinggan.curiosity),
        bar("负荷", qinggan.allostatic_load)
      ].join("");
      memoryRows.innerHTML = [
        row("总数", Number(memory.zongshu || 0)),
        row("最近检索", memory.zuijin_jiansuo || "暂无"),
        row("最近数量", Number(memory.zuijin_zongshu || 0)),
        row("分层", JSON.stringify(memory.geceng_fenbu || {}))
      ].join("");
      evolveRows.innerHTML = [
        row("阶段", friendly(jinhua.dangqian_jieduan, PHASE_NAMES, "观察")),
        row("候选", Array.isArray(jinhua.gaijin_houxuan) ? jinhua.gaijin_houxuan.length : 0),
        row("活跃实验", jinhua.huoyue_shiyan || "无")
      ].join("");
      safetyRows.innerHTML = [
        row("自主级别", friendly(anquan.zizhu_jibie, AUTONOMY_NAMES, "辅助")),
        row("信任校准", `${pct(anquan.xinren_jiaozhun ?? 0.5)}%`),
        row("连续自主", Number(anquan.lianxu_zizhu_xingdong || 0))
      ].join("");

      learningPill.textContent = manualReady ? "待确认" : autoLearnable ? "自主学习中" : learning.candidate_only ? "候选模式" : friendly(learning.status, STATUS_NAMES, "已同步");
      learningPill.className = `mini-pill ${manualReady ? "warn" : cards.total ? "ok" : ""}`;
      learningMetrics.innerHTML = [
        metric("候选总数", numberValue(cards.total), "后端真实队列"),
        metric("自主学", autoLearnable, "A0-A2 自动复审"),
        metric("需处理", manualReady, "确认 / 加工 / 激活 / 发布"),
        metric("能力草稿", draftCount, "draft"),
        metric("下次 tick", nextTickText, cards.last_reason || "队列刷新")
      ].join("");
      latestLearningCards = latestCards;
      learningCandidates.innerHTML = renderCandidateList(latestCards, {
        confirm: Boolean(actions.learnLearningExperience),
        process: Boolean(actions.processLearningCard),
        request: Boolean(actions.requestLearningActivation),
        activate: Boolean(actions.activateLearningCard),
        release: Boolean(actions.releaseLearningCard),
        discard: Boolean(actions.discardLearningCard)
      });
    }

    panel.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-learn-card], [data-process-card], [data-request-activation], [data-activate-card], [data-release-card], [data-discard-card]");
      if (!button || button.disabled) return;
      const id = String(button.dataset.learnCard || button.dataset.processCard || button.dataset.requestActivation || button.dataset.activateCard || button.dataset.releaseCard || button.dataset.discardCard || "").trim();
      if (!id) return;
      const item = latestLearningCards.find((card) => String(card.card_id || card.id || card.learning_id || "") === id) || {};
      const previousText = button.textContent;
      button.disabled = true;
      button.textContent = "处理中";
      try {
        if (button.dataset.learnCard) {
          await actions.learnLearningExperience?.(id, item);
        } else if (button.dataset.processCard) {
          await actions.processLearningCard?.(id, item);
        } else if (button.dataset.requestActivation) {
          await actions.requestLearningActivation?.(id, item);
        } else if (button.dataset.activateCard) {
          await actions.activateLearningCard?.(id, item);
        } else if (button.dataset.releaseCard) {
          await actions.releaseLearningCard?.(id, item);
        } else if (button.dataset.discardCard) {
          await actions.discardLearningCard?.(id, item);
        }
      } finally {
        button.textContent = previousText;
        button.disabled = false;
      }
    });

    refresh.addEventListener("click", () => actions.refreshStatus?.());
    state.on("page", renderPage);
    state.on("runtimeStatus", render);

    const snap = state.snapshot();
    renderPage(snap.activePage);
    render(snap.runtimeStatus);
    loadPanelLearning();
  }
};
