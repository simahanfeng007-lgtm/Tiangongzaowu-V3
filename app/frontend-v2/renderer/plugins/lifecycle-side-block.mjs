const STATUS_NAMES = {
  empty: "暂无候选",
  pending: "学习中",
  synced: "已同步",
  needs_attention: "需处理",
  candidate_ready: "候选队列",
  review_ready: "待处理",
  model_review: "模型审查",
  draft: "能力草稿",
  sandbox_passed: "沙盒通过",
  candidate: "候选中",
  candidate_only: "候选模式",
  learned: "已学习",
  learned_no_asset: "已学习，无新增资产",
  pending_learning: "待学习",
  pending_approval: "待确认",
  skipped_by_user: "已跳过",
  skipped_by_judge: "判定无需学习",
  failed: "失败",
  not_configured: "未启用",
  ready: "就绪",
  queue_ready: "队列就绪",
  running: "运行中",
  active: "活跃",
  discarded: "已放弃",
  duplicate_removed: "重复移除",
  no_value: "价值不足",
  stopped: "未运行",
  unknown: "未知",
  loading: "读取中"
};

const STAGE_NAMES = {
  candidate: "候选",
  model_review: "模型审查",
  draft: "能力草稿",
  sandbox_passed: "沙盒通过",
  review_ready: "待处理",
  active: "已激活",
  disabled: "已停用"
};

const SOURCE_NAMES = {
  chat: "聊天",
  code: "代码任务",
  file: "文件任务",
  runtime_task: "运行任务",
  free_will: "自由意志",
  manual_approval: "手动触发",
  xintiao_p5: "心跳运行",
  xintiao_zizhu: "自由意志行动",
  LLM_zizhu: "自由意志行动",
  zizhu_xuexi: "自主学习队列"
};

const SKIP_REASON_NAMES = {
  disabled: "开关关闭",
  heartbeat_not_running: "心跳未运行",
  user_recently_active: "用户刚活跃",
  consecutive_limit: "连续次数达上限",
  curiosity_below_threshold: "好奇心未超过阈值",
  phase_too_early: "生命阶段过早",
  no_trigger_record: "暂无自主行动记录"
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function numberValue(value) {
  const next = Number(value || 0);
  return Number.isFinite(next) ? next : 0;
}

function formatScore(value) {
  const next = Number(value);
  return Number.isFinite(next) ? next.toFixed(2) : "0.00";
}

function statusText(value) {
  const text = String(value || "");
  return STATUS_NAMES[text] || text || "未知";
}

function sourceText(value) {
  const text = String(value || "");
  return SOURCE_NAMES[text] || text || "未知来源";
}

function skipText(value) {
  const text = String(value || "");
  return SKIP_REASON_NAMES[text] || text || "暂无";
}

function pillClass(status, pending, failed) {
  if (failed > 0 || status === "needs_attention" || status === "failed" || status === "blocked") return "mini-pill failed";
  if (pending > 0 || status === "pending" || status === "pending_learning" || status === "pending_approval" || status === "review_ready" || status === "model_review") return "mini-pill warn";
  if (status === "synced" || status === "ready" || status === "queue_ready" || status === "learned" || status === "candidate_ready" || status === "running" || status === "active") return "mini-pill ok";
  return "mini-pill";
}

function metric(label, value, hint = "") {
  return `
    <div class="side-learning-metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      ${hint ? `<small>${escapeHtml(hint)}</small>` : ""}
    </div>
  `;
}

function renderFreeWillStatus(runtimeStatus, refs) {
  const payload = runtimeStatus?.payload || {};
  const freeWill = payload.lifecycle?.free_will || payload.runtime?.free_will || {};
  const latestAction = freeWill.latest_autonomous_action || {};
  const hasAction = Boolean(latestAction.trace_id || latestAction.started_at);
  const status = !freeWill.enabled
    ? "disabled"
    : hasAction
      ? "ready"
      : freeWill.heartbeat_running
        ? "running"
        : "stopped";
  const skipReason = freeWill.skip_reason || (hasAction ? "" : "no_trigger_record");
  const skipDetail = freeWill.skip_detail || skipText(skipReason);

  refs.freeWillPill.textContent = hasAction ? "有行动记录" : freeWill.heartbeat_running ? "正在运行" : statusText(status);
  refs.freeWillPill.className = pillClass(status, 0, 0);
  refs.freeWillGrid.innerHTML = [
    metric("行动开关", freeWill.enabled === false ? "关闭" : "开启"),
    metric("运行状态", freeWill.heartbeat_running ? "正在运行" : "未运行", freeWill.heartbeat_interval_seconds ? `${freeWill.heartbeat_interval_seconds}s` : ""),
    metric("行动就绪", freeWill.ready_for_action ? "是" : "否", skipDetail),
    metric("最近行动", hasAction ? (latestAction.trigger || latestAction.trace_id || "已记录") : "暂无", hasAction ? (latestAction.started_at || "") : "无 xintiao_zizhu / LLM_zizhu 记录"),
    metric("好奇心", formatScore(freeWill.curiosity), `阈值 > ${formatScore(freeWill.curiosity_threshold ?? 0.5)}`),
    metric("连续次数", `${numberValue(freeWill.consecutive_actions)}/${numberValue(freeWill.max_consecutive_actions || 5)}`)
  ].join("");
}

function renderLearningStatus(runtimeStatus, refs) {
  const payload = runtimeStatus?.payload || {};
  const learning = payload.learning || {};
  const pool = learning.learning_cards || {};
  const skillQueue = learning.skill_queue || {};
  const toolRequests = learning.tool_requests || {};
  const latest = Array.isArray(pool.latest) ? pool.latest : [];
  const pending = numberValue(pool.pending_learning);
  const failed = numberValue(pool.failed);
  const status = runtimeStatus?.loading ? "loading" : (learning.status || "empty");

  refs.learningPill.textContent = runtimeStatus?.loading ? "读取中" : statusText(status);
  refs.learningPill.className = pillClass(status, pending, failed);
  refs.learningGrid.innerHTML = [
    metric("学习卡", numberValue(pool.total), "候选队列"),
    metric("自主学", latest.filter((item) => (item.promotion_stage || item.status || "candidate") === "candidate" && item.auto_learn_allowed).length, "A0-A2 自动复审"),
    metric("需确认", latest.filter((item) => (item.promotion_stage || item.status || "candidate") === "candidate" && !item.auto_learn_allowed).length, "A3-A4 激活学习"),
    metric("模型审查", numberValue(pool.model_review)),
    metric("能力草稿", numberValue(pool.draft)),
    metric("上次 tick", pool.last_tick_iso || "暂无", pool.last_reason || ""),
    metric("工具请求", numberValue(toolRequests.production_requests), statusText(toolRequests.status || skillQueue.status))
  ].join("");

  refs.learningList.innerHTML = "";
  if (!runtimeStatus?.ok) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = runtimeStatus?.text || "运行内核状态未连接。";
    refs.learningList.appendChild(empty);
    return;
  }
  if (!latest.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "暂无自主学习候选卡。";
    refs.learningList.appendChild(empty);
    return;
  }

  for (const item of latest.slice(0, 4)) {
    const row = document.createElement("div");
    row.className = "side-learning-row";
    const title = item.title || item.summary || "未命名学习卡";
    const summary = item.summary || "候选待学习，不会自动注册技能或激活工具。";
    const stage = item.promotion_stage || item.status || "candidate";
    const labels = [
      item.priority ? `优先级：${item.priority} / ${formatScore(item.score)}` : "",
      item.risk_level ? `风险：${item.risk_level}${item.risk_label ? ` ${item.risk_label}` : ""}` : "",
      `阶段：${STAGE_NAMES[stage] || statusText(stage)}`,
      item.auto_drafted ? "自主学习" : "",
      sourceText(item.source),
      item.candidate_only ? "候选队列" : "",
      item.review_before_activation ? "需复审" : "",
      item.last_error ? `错误：${item.last_error}` : ""
    ].filter(Boolean);
    row.innerHTML = `
      <strong>${escapeHtml(title)}</strong>
      <p>${escapeHtml(summary)}</p>
      <p>${escapeHtml(labels.join(" · ") || "等待自主学习链处理")}</p>
      <span class="${pillClass(item.status, item.status === "pending_learning" || item.status === "review_ready" ? 1 : 0, item.status === "failed" ? 1 : 0)}">${escapeHtml(statusText(item.status))}</span>
    `;
    refs.learningList.appendChild(row);
  }
}

export const lifecycleSideBlockPlugin = {
  id: "lifecycle-side-block",
  slot: "context",
  order: 111,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section id="lifecycleSidePanel" class="side-section lifecycle-side-panel" hidden>
          <div class="section-heading">
            <span>生命状态</span>
            <button id="sideLifecycleRefresh" class="small-command" type="button">刷新</button>
          </div>

          <div class="life-side-card">
            <div class="life-side-title">
              <span>自由意志实际状态</span>
              <span id="sideFreeWillStatusPill" class="mini-pill">未读取</span>
            </div>
            <div id="sideFreeWillStatusGrid" class="side-learning-grid"></div>
          </div>

          <div class="life-side-card">
            <div class="life-side-title">
              <span>自主学习候选</span>
              <span id="sideLearningStatusPill" class="mini-pill">未读取</span>
            </div>
            <div id="sideLearningStatusGrid" class="side-learning-grid"></div>
            <div class="life-side-title compact">
              <span>最近候选卡</span>
            </div>
            <div id="sideLearningCandidateList" class="side-learning-list"></div>
          </div>
        </section>
      `
    );

    const panel = slot.querySelector("#lifecycleSidePanel");
    const refreshButton = panel.querySelector("#sideLifecycleRefresh");
    const refs = {
      freeWillPill: panel.querySelector("#sideFreeWillStatusPill"),
      freeWillGrid: panel.querySelector("#sideFreeWillStatusGrid"),
      learningPill: panel.querySelector("#sideLearningStatusPill"),
      learningGrid: panel.querySelector("#sideLearningStatusGrid"),
      learningList: panel.querySelector("#sideLearningCandidateList")
    };

    function renderPage(page) {
      panel.hidden = page !== "lifecycle";
      if (page === "lifecycle") actions.refreshStatus?.();
    }

    refreshButton.addEventListener("click", () => actions.refreshStatus?.());

    state.on("page", renderPage);
    state.on("runtimeStatus", (runtimeStatus) => {
      renderFreeWillStatus(runtimeStatus, refs);
      renderLearningStatus(runtimeStatus, refs);
    });

    const snap = state.snapshot();
    renderPage(snap.activePage);
    renderFreeWillStatus(snap.runtimeStatus, refs);
    renderLearningStatus(snap.runtimeStatus, refs);
  }
};
