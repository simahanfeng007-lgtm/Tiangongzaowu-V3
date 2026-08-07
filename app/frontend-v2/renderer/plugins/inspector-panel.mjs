import {
  concisePath,
  formatElapsedMs,
  humanizeBackendText,
  parseBackendSteps,
  spokenBackendText,
  stripStreamEvents,
  stripStatusPayload,
  translateLabel,
  translateValue
} from "../core/formatters.mjs";
import { lifeApi } from "../runtime/life-api.mjs";

function safeArray(value) {
  return Array.isArray(value) ? value : [];
}

export function contextPressurePercent(context = {}, runtimeRatio = 0, fallbackRatio = 0) {
  const row = context && typeof context === "object" ? context : {};
  const budget = Number(row.token_budget || 0);
  const hasAuthoritativeUsage = row.available === true
    && budget > 0
    && (Object.prototype.hasOwnProperty.call(row, "context_utilization_milli")
      || Object.prototype.hasOwnProperty.call(row, "current_context_tokens"));
  if (hasAuthoritativeUsage) {
    const milli = Number(row.context_utilization_milli);
    const ratio = Number.isFinite(milli)
      ? milli / 10
      : (Number(row.current_context_tokens || 0) / budget) * 100;
    return Math.max(0, Math.min(100, ratio));
  }
  const runtime = Number(runtimeRatio || 0) * 100;
  const fallback = Number(fallbackRatio || 0);
  return Math.max(0, Math.min(100, runtime || fallback));
}

function compactText(value, limit = 360) {
  const text = String(value ?? "").replace(/\r\n/g, "\n").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > limit ? `${text.slice(0, limit - 3)}...` : text;
}

function formatExecutionTime(value) {
  const epoch = Number(value || 0);
  const parsed = epoch > 0 ? new Date(epoch) : new Date(String(value || ""));
  if (!Number.isFinite(parsed.getTime())) return "未记录";
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });
}

function escHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalizeTaskStatus(value, fallback = "pending") {
  const text = String(value || fallback).trim();
  if (["ok", "done", "completed", "success", "synced", "ready", "learned", "learned_no_asset"].includes(text)) return "done";
  if (["running", "loading", "pending", "pending_learning", "pending_approval", "queue_ready"].includes(text)) return "running";
  if (["failed", "blocked", "timeout", "not_configured"].includes(text)) return "failed";
  if (["confirmation_required", "needs_attention"].includes(text)) return "confirmation_required";
  if (["empty", "not_enabled", "skipped", "idle"].includes(text)) return "pending";
  return text;
}

function taskFromRun(run) {
  const phase = String(run?.phase || "idle");
  if (phase === "idle" && !run?.stdout && !run?.stderr) return null;
  const ok = run?.ok !== false;
  const text = spokenBackendText(run?.stdout || "") || stripStatusPayload(run?.stdout || "") || run?.stderr || "";
  return {
    tool: "最近执行",
    status: phase === "running" ? "running" : ok ? "done" : "failed",
    summary: compactText(text, 420) || (phase === "running" ? "后台任务正在运行。" : "后台任务已结束。")
  };
}

function tasksFromStatus(runtimeStatus) {
  const payload = runtimeStatus?.payload || {};
  const tasks = [];
  const runtimeState = runtimeStatus?.loading ? "running" : runtimeStatus?.ok ? "done" : runtimeStatus?.ok === false ? "failed" : "pending";
  tasks.push({
    tool: "运行内核",
    status: runtimeState,
    summary: compactText(runtimeStatus?.text || "待连接", 320)
  });

  const planner = payload.runtime?.planner_execution || {};
  if (planner.status && planner.status !== "empty") {
    tasks.push({
      tool: "Planner 执行",
      status: normalizeTaskStatus(planner.status),
      summary: compactText(planner.message || planner.summary || planner.status, 420)
    });
  }

  for (const item of safeArray(payload.lifecycle?.pending_updates).slice(0, 5)) {
    tasks.push({
      tool: item.title || "待确认自主更新",
      status: "confirmation_required",
      summary: compactText(item.summary || item.reason || item.confirmation_effect || "等待用户确认。", 420)
    });
  }

  return tasks;
}

function combinedTasks(run, runtimeStatus) {
  const parsed = parseBackendSteps(run?.stdout || "")
    .map((step) => ({
      tool: step.tool,
      status: normalizeTaskStatus(step.status),
      summary: step.summary
    }))
    .filter((task) => !isBackgroundPostprocessTask(task));
  const fromRun = taskFromRun(run);
  const tasks = [...(fromRun ? [fromRun] : []), ...parsed, ...tasksFromStatus(runtimeStatus)];
  const seen = new Set();
  return tasks.filter((task) => {
    const key = `${task.tool}\n${task.status}\n${compactText(task.summary, 120)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 18);
}

const statusIcons = {
  pending: "○",
  running: "◉",
  done: "✓",
  failed: "!",
  confirmation_required: "?"
};

const statusClasses = {
  pending: "pending",
  running: "running",
  done: "done",
  failed: "failed",
  confirmation_required: "confirmation"
};

const statusPriority = {
  failed: 5,
  confirmation_required: 4,
  running: 3,
  pending: 2,
  done: 1
};

const pageChrome = {
  chat: { caption: "观察", title: "执行状态" },
  execute: { caption: "执行", title: "运行仪表盘" },
  knowledge: { caption: "知识", title: "知识统计" },
  skills: { caption: "技能", title: "技能统计" },
  lifecycle: { caption: "生命", title: "生命周期仪表盘" },
  persona: { caption: "人设", title: "用户卡片" },
  settings: { caption: "设置", title: "系统边界" }
};

const chartColors = {
  done: "rgba(34, 197, 94, 0.82)",
  running: "rgba(20, 184, 166, 0.86)",
  failed: "rgba(239, 68, 68, 0.86)",
  pending: "rgba(148, 163, 184, 0.42)",
  confirmation_required: "rgba(245, 158, 11, 0.86)",
  neutral: "rgba(96, 165, 250, 0.76)",
  ink: "rgba(125, 211, 252, 0.74)"
};

function includesAny(text, tokens) {
  const lower = String(text || "").toLowerCase();
  return tokens.some((token) => lower.includes(String(token).toLowerCase()));
}

function isBackgroundPostprocessTask(task) {
  const raw = `${task?.tool || ""} ${task?.summary || ""}`;
  return includesAny(raw, [
    "_hou_chuli",
    "postprocess",
    "后处理",
    "经验合成",
    "经验学习",
    "自主学习",
    "技能学习",
    "工具生产",
    "晋升",
    "遗忘"
  ]);
}

function taskFamily(task) {
  if (String(task?.tool || "") === "最近执行") {
    return { key: "recent", title: "最近执行" };
  }
  const raw = `${task.tool || ""} ${task.summary || ""}`;
  if (includesAny(raw, ["_hebing_panding", "合并判定", "能力路由", "chat/work", "ModelPlanner", "PlanBridge", "Planner"])) {
    return { key: "route", title: "合并判定与能力路由" };
  }
  if (includesAny(raw, ["_shouji_xinxi", "记忆召回", "L1", "L2", "L3", "L4", "L5", "上下文", "最近10条"])) {
    return { key: "context", title: "输入、上下文与检索" };
  }
  if (includesAny(raw, ["_qinggan_ka", "七情六欲", "情感", "总情感"])) {
    return { key: "emotion", title: "情感系统" };
  }
  if (includesAny(raw, ["_shuaxin_tishi_ci", "Soul", "提示词", "系统底层", "事件卡"])) {
    return { key: "prompt", title: "提示词拼接" };
  }
  if (includesAny(raw, ["_hou_chuli", "后处理", "经验", "自主学习", "技能学习", "工具生产", "晋升", "遗忘"])) {
    return { key: "postprocess", title: "后处理与学习" };
  }
  if (includesAny(raw, ["生命周期", "自主更新", "待确认"])) {
    return { key: "lifecycle", title: "生命周期确认" };
  }
  if (includesAny(raw, ["write_line", "add_user", "add_assistant", "session", "输出", "会话"])) {
    return { key: "session", title: "输出与会话" };
  }
  if (includesAny(raw, ["运行内核", "Runtime", "HealthState", "SignalKind", "接口状态"])) {
    return { key: "runtime", title: "运行内核" };
  }
  if (includesAny(raw, ["最近执行"])) {
    return { key: "recent", title: "最近执行" };
  }
  if (/^步骤\s+\d+/.test(String(task.tool || ""))) {
    return { key: "backend_steps", title: "后台执行步骤" };
  }
  if (includesAny(raw, ["code", "file", "tool", "工具", "执行"])) {
    return { key: "execution", title: "执行链路" };
  }
  const title = translateLabel(task.tool);
  return { key: `tool:${title}`, title };
}

function groupedTasks(tasks) {
  const groups = new Map();
  for (const task of tasks) {
    const family = taskFamily(task);
    if (!groups.has(family.key)) {
      groups.set(family.key, {
        key: family.key,
        title: family.title,
        status: "done",
        summary: "",
        items: []
      });
    }
    const group = groups.get(family.key);
    group.items.push(task);
    if ((statusPriority[task.status] || 0) > (statusPriority[group.status] || 0)) {
      group.status = task.status;
    }
    if (task.summary) group.summary = compactText(task.summary, 140);
  }
  return [...groups.values()];
}

function makeStepNode(step) {
  const node = document.createElement("div");
  node.className = "step";
  const statusClass = statusClasses[step.status] || "";
  if (statusClass) node.classList.add(statusClass);

  const title = document.createElement("div");
  title.className = "step-title";
  const icon = document.createElement("span");
  icon.className = "step-icon";
  icon.textContent = statusIcons[step.status] || "○";
  const tool = document.createElement("span");
  tool.textContent = translateLabel(step.tool);
  const stepStatus = document.createElement("span");
  stepStatus.textContent = translateValue(step.status);
  const summary = document.createElement("pre");
  summary.textContent = humanizeBackendText(step.summary) || step.summary;
  title.appendChild(icon);
  title.appendChild(tool);
  title.appendChild(stepStatus);
  node.appendChild(title);
  node.appendChild(summary);
  return node;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function buildLogText(run, runtimeStatus) {
  const sections = [];
  const add = (title, value) => {
    const text = stripStreamEvents(value || "");
    if (text) sections.push(`[${title}]\n${text}`);
  };
  add("最近执行 stdout", run?.stdout);
  add("最近执行 stderr", run?.stderr);
  add("运行状态 stdout", runtimeStatus?.stdout);
  add("运行状态 stderr", runtimeStatus?.stderr);
  if (!sections.length && runtimeStatus?.text) add("运行状态", runtimeStatus.text);
  return sections.join("\n\n") || "暂无后台运行日志。";
}

function clamp(value, min = 0, max = 100) {
  const number = Number(value);
  if (!Number.isFinite(number)) return min;
  return Math.max(min, Math.min(max, number));
}

function formatCount(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  if (number >= 10000) return `${(number / 10000).toFixed(1)}万`;
  return String(Math.round(number));
}

function formatShortDate(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const parsed = Date.parse(text);
  if (!Number.isFinite(parsed)) return text;
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    }).format(new Date(parsed));
  } catch {
    return text;
  }
}

const lifecycleTextMap = {
  "self healing": "自我愈合",
  "self_learning": "自主学习",
  "self learning": "自主学习",
  "self_iteration": "自我迭代",
  "self iteration": "自我迭代",
  "daily_plan": "每日日程",
  "daily plan": "每日日程",
  "knowledge_memory_tidy": "知识与记忆梳理",
  "knowledge memory tidy": "知识与记忆梳理",
  "life_share": "行动心得分享",
  "life share": "行动心得分享",
  "candidate_only": "候选模式",
  "candidate only": "候选模式",
  "risk_gated_model_review_learning": "按风险分级，并经过模型复审",
  "heartbeat_running_action_guarded": "心跳运行中，行动受保护",
  "schedule_required": "日程必做",
  "required_schedule": "日程必做",
  "budget_exhausted": "模型预算已用完",
  "llm_not_available": "模型未就绪",
  "life_llm_daily_attempt_budget_exhausted": "模型尝试预算已用完",
  "life_llm_daily_success_budget_exhausted": "模型成功预算已用完",
  "no_selected_candidates_in_current_window": "当前时间窗口没有可执行任务",
  "user_active_deferred_without_refresh": "用户活跃，重任务已暂缓",
  "user_active_low_risk_healing": "用户活跃时只做低风险自愈",
  "not_due": "未到重心跳时间",
  "waiting first tick": "等待首次生命心跳",
  "waiting_first_tick": "等待首次生命心跳",
  "AFFECTIVE_PROJECTION_UNAVAILABLE": "当前后端未挂载权威情感投影",
  "FREE_WILL_SCHEDULER_UNAVAILABLE": "当前后端未挂载权威自由意志调度器",
  "no authoritative affective projection is mounted in this backend build": "当前后端未挂载权威情感投影",
  "no authoritative free-will scheduler is mounted in this backend build": "当前后端未挂载权威自由意志调度器",
  "no authoritative daily model budget projection": "当前后端没有权威模型预算投影",
  "xintiao_zizhu": "自主心跳",
  "LLM_zizhu": "模型自主行动"
};

function escapeLifecycleRegex(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function translateLifecycleText(value) {
  let text = String(value || "").trim();
  if (!text) return "";
  const normalized = text.toLowerCase().replace(/[_.-]+/g, " ").replace(/\s+/g, " ").trim();
  if (lifecycleTextMap[text] || lifecycleTextMap[text.toLowerCase()] || lifecycleTextMap[normalized]) {
    return lifecycleTextMap[text] || lifecycleTextMap[text.toLowerCase()] || lifecycleTextMap[normalized];
  }
  const entries = Object.entries(lifecycleTextMap).sort((a, b) => b[0].length - a[0].length);
  for (const [key, label] of entries) {
    const pattern = new RegExp(`\\b${escapeLifecycleRegex(key).replace(/[_. -]+/g, "[_. -]+")}\\b`, "gi");
    text = text.replace(pattern, label);
  }
  return text
    .replace(/curiosity=([0-9.]+).*?>\s*([0-9.]+)/i, (_, a, b) => `好奇心 ${Number(a).toFixed(2)}，需高于 ${b}`)
    .replace(/drive=([0-9.]+)/gi, (_, a) => `驱动力 ${Number(a).toFixed(2)}`)
    .replace(/score=([0-9.]+)/gi, (_, a) => `分数 ${Number(a).toFixed(2)}`)
    .replace(/true/gi, "是")
    .replace(/false/gi, "否")
    .replace(/LLM/g, "模型")
    .replace(/api/gi, "接口")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function statusTone(status) {
  if (status === "failed") return "failed";
  if (status === "running") return "running";
  if (status === "done") return "ok";
  if (status === "confirmation_required") return "warn";
  return "";
}

function metricCard({ label, value, hint = "", tone = "" }) {
  return `
    <article class="dash-metric ${escHtml(tone)}">
      <span>${escHtml(label)}</span>
      <strong>${escHtml(value)}</strong>
      ${hint ? `<em>${escHtml(hint)}</em>` : ""}
    </article>
  `;
}

function metricsGrid(items) {
  return `<div class="dash-metrics">${items.map(metricCard).join("")}</div>`;
}

function kvRows(rows) {
  return `
    <div class="dash-kv">
      ${rows.map(([label, value, tone = "", attrs = ""]) => `
        <div class="dash-kv-row ${escHtml(tone)}"${attrs}>
          <span>${escHtml(label)}</span>
          <strong title="${escHtml(value)}">${escHtml(value)}</strong>
        </div>
      `).join("")}
    </div>
  `;
}

function barList(items) {
  const clean = items.filter((item) => Number(item.value || 0) > 0);
  if (!clean.length) return `<div class="empty-detail compact">暂无统计数据</div>`;
  const max = Math.max(1, ...clean.map((item) => Number(item.value || 0)));
  return `
    <div class="bar-list">
      ${clean.map((item) => {
        const width = clamp((Number(item.value || 0) / max) * 100, 4, 100);
        return `
          <div class="bar-row">
            <span class="bar-label" title="${escHtml(item.label)}">${escHtml(item.label)}</span>
            <span class="bar-track"><span class="bar-fill ${escHtml(item.tone || "")}" style="width:${width}%"></span></span>
            <strong>${escHtml(formatCount(item.value))}</strong>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function donutChart(items, centerText, caption = "") {
  const clean = items.filter((item) => Number(item.value || 0) > 0);
  const total = clean.reduce((sum, item) => sum + Number(item.value || 0), 0);
  if (!total) {
    return `
      <div class="donut-wrap">
        <div class="donut-ring empty"><span>${escHtml(centerText || "0")}</span></div>
        ${caption ? `<div class="donut-caption">${escHtml(caption)}</div>` : ""}
      </div>
    `;
  }
  let cursor = 0;
  const slices = clean.map((item) => {
    const start = cursor;
    cursor += (Number(item.value || 0) / total) * 100;
    return `${item.color || chartColors.neutral} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
  }).join(", ");
  return `
    <div class="donut-wrap">
      <div class="donut-ring" style="--donut-bg: conic-gradient(${slices})"><span>${escHtml(centerText || formatCount(total))}</span></div>
      ${caption ? `<div class="donut-caption">${escHtml(caption)}</div>` : ""}
    </div>
  `;
}

function dashSection(title, body, note = "") {
  return `
    <section class="dash-section">
      <div class="dash-section-title">
        <span>${escHtml(title)}</span>
        ${note ? `<small>${escHtml(note)}</small>` : ""}
      </div>
      ${body}
    </section>
  `;
}

function taskStatusItems(tasks) {
  const counts = { done: 0, running: 0, failed: 0, pending: 0, confirmation_required: 0 };
  for (const task of tasks) {
    const status = normalizeTaskStatus(task.status);
    counts[status] = Number(counts[status] || 0) + 1;
  }
  return [
    { label: "完成", value: counts.done, color: chartColors.done, tone: "ok" },
    { label: "运行", value: counts.running, color: chartColors.running, tone: "running" },
    { label: "失败", value: counts.failed, color: chartColors.failed, tone: "failed" },
    { label: "待确认", value: counts.confirmation_required, color: chartColors.confirmation_required, tone: "warn" },
    { label: "等待", value: counts.pending, color: chartColors.pending, tone: "" }
  ];
}

function backendStatusLabel(runtimeStatus) {
  if (runtimeStatus?.loading) return "检查中";
  if (runtimeStatus?.ok === true) return "正常";
  if (runtimeStatus?.ok === false) return "失败";
  return "未连接";
}

function latestDateLabel(value) {
  const raw = String(value || "");
  if (!raw) return "未记录";
  return raw.replace("T", " ").slice(0, 16);
}

function summarizeKnowledgeDocuments(documents) {
  const docs = safeArray(documents);
  const byType = new Map();
  const byStatus = new Map();
  let totalBytes = 0;
  let totalCitations = 0;
  let latest = "";
  for (const doc of docs) {
    const suffix = String(doc?.suffix || doc?.file_type || "文件").toLowerCase().replace(/^\./, "") || "文件";
    const status = String(doc?.status || "已索引");
    byType.set(suffix, Number(byType.get(suffix) || 0) + 1);
    byStatus.set(status, Number(byStatus.get(status) || 0) + 1);
    totalBytes += Number(doc?.size_bytes || 0);
    totalCitations += Number(doc?.citation_count || 0);
    const createdAt = String(doc?.created_at || "");
    if (createdAt && createdAt > latest) latest = createdAt;
  }
  return {
    total: docs.length,
    totalBytes,
    totalCitations,
    latest,
    byType: [...byType.entries()].map(([label, value]) => ({ label: label.toUpperCase(), value, tone: "neutral" })),
    byStatus: [...byStatus.entries()].map(([label, value]) => ({ label: translateValue(label), value, tone: label === "failed" ? "failed" : "ok" }))
  };
}

function wiringValue(payload, path, fallback = "") {
  let current = payload;
  for (const key of path) {
    if (!current || typeof current !== "object") return fallback;
    current = current[key];
  }
  return current ?? fallback;
}

export const inspectorPanelPlugin = {
  id: "inspector-panel",
  slot: "inspector",
  order: 300,
  mount({ slot, state, actions }) {
    // 右侧观察栏是共享容器：先挂载的生命链摘要（order 5）必须保留在顶部，
    // 因此这里用 beforeend 追加，而不是 innerHTML 覆盖。
    slot.insertAdjacentHTML(
      "beforeend",
      `
      <header class="inspector-header">
        <div>
          <div id="inspectorCaption" class="caption">观察</div>
          <h2 id="inspectorTitle">执行状态</h2>
        </div>
        <span id="lastRunStatus" class="run-pill">空闲</span>
      </header>

      <div id="runMeta" class="run-meta"></div>

      <div id="chatInspector" class="chat-inspector">
        <div class="detail-tabs" role="tablist" aria-label="观察视图">
          <button id="tabSteps" class="detail-tab active" data-view="steps" type="button" role="tab" aria-selected="true" aria-controls="stepsPanel">任务</button>
          <button id="tabRaw" class="detail-tab" data-view="raw" type="button" role="tab" aria-selected="false" aria-controls="rawPanel">日志</button>
        </div>

        <section id="stepsPanel" class="detail-panel active" role="tabpanel" aria-labelledby="tabSteps">
          <div id="steps" class="steps"></div>
        </section>

        <section id="rawPanel" class="detail-panel" role="tabpanel" aria-labelledby="tabRaw" hidden>
          <div id="rawOutput" class="raw-output log-days"></div>
        </section>
      </div>

      <section id="dashboardPanel" class="dashboard-panel" hidden></section>
    `);

    // P2-14: on narrow windows the inspector column is hidden; provide a
    // floating "执行观察" entry that opens it as an overlay.
    const openToggle = document.createElement("button");
    openToggle.type = "button";
    openToggle.className = "execution-observation-toggle";
    openToggle.textContent = "执行观察";
    openToggle.setAttribute("aria-label", "打开执行观察");
    openToggle.addEventListener("click", () => {
      const open = document.documentElement.dataset.inspectorOpen === "true";
      document.documentElement.dataset.inspectorOpen = open ? "false" : "true";
    });
    document.body.appendChild(openToggle);

    const caption = slot.querySelector("#inspectorCaption");
    const title = slot.querySelector("#inspectorTitle");
    const status = slot.querySelector("#lastRunStatus");
    const meta = slot.querySelector("#runMeta");
    const chatInspector = slot.querySelector("#chatInspector");
    const dashboardPanel = slot.querySelector("#dashboardPanel");
    const stepsEl = slot.querySelector("#steps");
    const rawOutput = slot.querySelector("#rawOutput");
    const stepsPanel = slot.querySelector("#stepsPanel");
    const rawPanel = slot.querySelector("#rawPanel");
    let dailyLogs = [];
    let dailyLogError = "";
    let dailyLogLoading = false;
    let lastDailyLogRefresh = 0;
    let lastRunRequestId = "";
    let knowledgeStats = { loaded: false, loading: false, error: "", documents: [], lastLoaded: 0 };
    let skillsStats = { loaded: false, loading: false, error: "", categories: [], abilities: [], summary: {}, generatedIndexPath: "", lastLoaded: 0 };
    let lifePanelStats = { loaded: false, loading: false, error: "", data: {}, lastLoaded: 0 };
    let userCardDirty = false;
    let userCardDraft = null;
    const dismissedTaskGroups = new Set();
    const expandedTaskGroups = new Set();
    const collapsedTaskGroups = new Set();

    for (const button of slot.querySelectorAll(".detail-tab")) {
      button.addEventListener("click", () => {
        const view = button.dataset.view;
        for (const item of slot.querySelectorAll(".detail-tab")) {
          item.classList.toggle("active", item === button);
          item.setAttribute("aria-selected", item === button ? "true" : "false");
        }
        stepsPanel.classList.toggle("active", view === "steps");
        rawPanel.classList.toggle("active", view === "raw");
        stepsPanel.hidden = view !== "steps";
        rawPanel.hidden = view !== "raw";
        if (view === "raw") void refreshDailyLogs();
      });
    }

    function defaultGroupOpen(group) {
      return ["failed", "running", "confirmation_required"].includes(group.status);
    }

    function isGroupOpen(group) {
      if (expandedTaskGroups.has(group.key)) return true;
      if (collapsedTaskGroups.has(group.key)) return false;
      return defaultGroupOpen(group);
    }

    function toggleTaskGroup(key, currentlyOpen) {
      if (!key) return;
      if (currentlyOpen) {
        expandedTaskGroups.delete(key);
        collapsedTaskGroups.add(key);
      } else {
        collapsedTaskGroups.delete(key);
        expandedTaskGroups.add(key);
      }
      renderInspector();
    }

    stepsEl.addEventListener("click", (event) => {
      const button = event.target.closest("[data-task-delete]");
      if (button) {
        event.preventDefault();
        event.stopPropagation();
        dismissedTaskGroups.add(button.dataset.taskDelete);
        renderInspector();
        return;
      }

      const toggle = event.target.closest("[data-task-toggle]");
      if (!toggle) return;
      toggleTaskGroup(toggle.dataset.taskToggle, toggle.getAttribute("aria-expanded") === "true");
    });

    stepsEl.addEventListener("keydown", (event) => {
      if (!["Enter", " "].includes(event.key) || event.target.closest("[data-task-delete]")) return;
      const toggle = event.target.closest("[data-task-toggle]");
      if (!toggle) return;
      event.preventDefault();
      toggleTaskGroup(toggle.dataset.taskToggle, toggle.getAttribute("aria-expanded") === "true");
    });

    rawOutput.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-log-action]");
      if (!button) return;
      const date = button.dataset.logDate;
      const action = button.dataset.logAction;
      if (!date) return;
      button.disabled = true;
      try {
        const result = action === "delete"
          ? (window.confirm(`删除 ${date} 的运行日志？`) ? await actions.deleteDailyLog(date) : { ok: true, logs: dailyLogs })
          : await actions.openDailyLog(date);
        if (!result?.ok) {
          dailyLogError = result?.error || "日志打开失败。";
          renderLogs();
        } else if (action === "delete") {
          dailyLogs = Array.isArray(result.logs) ? result.logs : dailyLogs.filter((item) => item.date !== date);
          renderLogDependentViews();
        }
      } finally {
        button.disabled = false;
      }
    });

    function userCardFromSettings(settings = {}) {
      return {
        userDisplayName: String(settings.userDisplayName || ""),
        userCallsign: String(settings.userCallsign || ""),
        userWork: String(settings.userWork || ""),
        userAvatarDataUrl: String(settings.userAvatarDataUrl || ""),
        userProfileSummary: String(settings.userProfileSummary || ""),
        userContextEnabled: settings.userContextEnabled !== false
      };
    }

    function currentUserCard(settings = {}) {
      if (!userCardDraft || !userCardDirty) {
        userCardDraft = userCardFromSettings(settings);
      }
      return { ...userCardDraft };
    }

    function readUserCardForm() {
      const current = currentUserCard(state.snapshot().settings);
      for (const field of dashboardPanel.querySelectorAll("[data-user-card-field]")) {
        const key = field.dataset.userCardField;
        if (!key) continue;
        current[key] = field.type === "checkbox" ? field.checked : field.value;
      }
      userCardDraft = current;
      return current;
    }

    function markUserCardDirty() {
      userCardDirty = true;
      readUserCardForm();
      const pill = dashboardPanel.querySelector("#userCardSaveState");
      if (pill) {
        pill.textContent = "待保存";
        pill.className = "mini-pill warn";
      }
    }

    dashboardPanel.addEventListener("input", (event) => {
      if (event.target.closest("[data-user-card-field]")) markUserCardDirty();
    });

    dashboardPanel.addEventListener("change", (event) => {
      if (event.target.closest("[data-user-card-field]")) markUserCardDirty();
    });

    function openLifeScheduleTab() {
      if (typeof actions?.setActivePage === "function") actions.setActivePage("lifecycle");
      else state.setActivePage?.("lifecycle");
      // The life panel is already mounted; trigger its own tab button so the
      // existing delegated click handler performs the switch to 日程.
      window.setTimeout(() => {
        document.querySelector('[data-life-tab="schedule"]')?.click();
      }, 0);
    }

    dashboardPanel.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (!event.target.closest("[data-open-life-schedule]")) return;
      event.preventDefault();
      openLifeScheduleTab();
    });

    dashboardPanel.addEventListener("click", async (event) => {
      if (event.target.closest("[data-open-life-schedule]")) {
        openLifeScheduleTab();
        return;
      }
      const button = event.target.closest("[data-user-card-action]");
      if (!button) return;
      const action = button.dataset.userCardAction;
      const snap = state.snapshot();

      if (action === "clear-avatar") {
        userCardDraft = { ...currentUserCard(snap.settings), userAvatarDataUrl: "" };
        userCardDirty = true;
        renderDashboard("persona", state.snapshot());
        return;
      }

      if (action === "choose-avatar") {
        readUserCardForm();
        userCardDirty = true;
        if (!actions.chooseUserAvatar) return;
        button.disabled = true;
        try {
          const next = await actions.chooseUserAvatar?.();
          const selectedAvatar = String(next?.userAvatarDataUrl || userCardDraft?.userAvatarDataUrl || "");
          userCardDraft = {
            ...currentUserCard(next || snap.settings),
            ...readUserCardForm(),
            userAvatarDataUrl: selectedAvatar
          };
          userCardDirty = true;
          const saved = await actions.saveSettings?.(userCardDraft);
          userCardDirty = false;
          userCardDraft = userCardFromSettings(saved || state.snapshot().settings);
          renderDashboard("persona", state.snapshot());
        } finally {
          button.disabled = false;
        }
        return;
      }

      if (action === "save") {
        const payload = readUserCardForm();
        const pill = dashboardPanel.querySelector("#userCardSaveState");
        if (pill) {
          pill.textContent = "保存中";
          pill.className = "mini-pill";
        }
        button.disabled = true;
        try {
          const saved = await actions.saveSettings?.(payload);
          userCardDirty = false;
          userCardDraft = userCardFromSettings(saved || state.snapshot().settings);
          renderDashboard("persona", state.snapshot());
        } finally {
          button.disabled = false;
        }
      }
    });

    async function refreshDailyLogs(force = false) {
      if (!actions?.listDailyLogs || dailyLogLoading) return;
      const now = Date.now();
      if (!force && dailyLogs.length && now - lastDailyLogRefresh < 3000) return;
      dailyLogLoading = true;
      dailyLogError = "";
      renderLogDependentViews();
      try {
        const result = await actions.listDailyLogs();
        dailyLogs = Array.isArray(result?.logs) ? result.logs : [];
        dailyLogError = result?.ok === false ? (result.error || "日志读取失败。") : "";
      } catch (error) {
        dailyLogs = [];
        dailyLogError = error?.message || String(error);
      } finally {
        dailyLogLoading = false;
        lastDailyLogRefresh = Date.now();
        renderLogDependentViews();
      }
    }

    async function refreshKnowledgeStats(force = false) {
      if (!actions?.listKnowledge || knowledgeStats.loading) return;
      const now = Date.now();
      if (!force && knowledgeStats.loaded && now - knowledgeStats.lastLoaded < 6000) return;
      knowledgeStats = { ...knowledgeStats, loading: true, error: "" };
      renderDashboard("knowledge", state.snapshot());
      try {
        const result = await actions.listKnowledge();
        knowledgeStats = {
          loaded: true,
          loading: false,
          error: result?.ok === false ? (result.error || "知识库读取失败。") : "",
          documents: Array.isArray(result?.documents) ? result.documents : [],
          lastLoaded: Date.now()
        };
      } catch (error) {
        knowledgeStats = {
          loaded: true,
          loading: false,
          error: error?.message || String(error),
          documents: [],
          lastLoaded: Date.now()
        };
      }
      renderDashboard("knowledge", state.snapshot());
    }

    async function refreshSkillsStats(force = false) {
      if (!actions?.listSkills || skillsStats.loading) return;
      const now = Date.now();
      if (!force && skillsStats.loaded && now - skillsStats.lastLoaded < 6000) return;
      skillsStats = { ...skillsStats, loading: true, error: "" };
      renderDashboard("skills", state.snapshot());
      try {
        const result = await actions.listSkills();
        skillsStats = {
          loaded: true,
          loading: false,
          error: result?.ok === false ? (result.error || "技能资产读取失败。") : "",
          categories: Array.isArray(result?.categories) ? result.categories : [],
          abilities: Array.isArray(result?.abilities) ? result.abilities : [],
          summary: result?.summary || {},
          generatedIndexPath: result?.generatedIndexPath || "",
          lastLoaded: Date.now()
        };
      } catch (error) {
        skillsStats = {
          // P1-16: a transient read failure must not overwrite the last
          // trustworthy inventory with an empty one.
          ...skillsStats,
          loaded: true,
          loading: false,
          error: error?.message || String(error),
          lastLoaded: Date.now()
        };
      }
      renderDashboard("skills", state.snapshot());
    }

    async function refreshLifePanelStats(force = false) {
      if (lifePanelStats.loading) return;
      const now = Date.now();
      if (!force && lifePanelStats.loaded && now - lifePanelStats.lastLoaded < 6000) return;
      lifePanelStats = { ...lifePanelStats, loading: true, error: "" };
      renderDashboard("lifecycle", state.snapshot());
      try {
        const result = await lifeApi.getPanel();
        lifePanelStats = {
          loaded: true,
          loading: false,
          error: result?.ok === false ? (result.error || "生命权威投影读取失败。") : "",
          data: result && typeof result === "object" ? result : {},
          lastLoaded: Date.now()
        };
      } catch (error) {
        lifePanelStats = {
          loaded: true,
          loading: false,
          error: error?.message || String(error),
          data: {},
          lastLoaded: Date.now()
        };
      }
      renderDashboard("lifecycle", state.snapshot());
    }

    function renderLogDependentViews() {
      const page = state.snapshot().activePage || "chat";
      if (page === "chat") renderLogs();
      if (page === "execute") renderDashboard("execute", state.snapshot());
    }

    function renderLogs() {
      const snap = state.snapshot();
      const run = snap.lastRun || {};
      const runtimeStatus = snap.runtimeStatus || {};
      rawOutput.innerHTML = "";

      if (!actions?.listDailyLogs) {
        rawOutput.textContent = buildLogText(run, runtimeStatus);
        return;
      }

      if (dailyLogLoading) {
        const loading = document.createElement("div");
        loading.className = "empty-detail";
        loading.textContent = "正在读取日志";
        rawOutput.appendChild(loading);
      }

      if (dailyLogError) {
        const error = document.createElement("div");
        error.className = "log-error";
        error.textContent = dailyLogError;
        rawOutput.appendChild(error);
      }

      if (!dailyLogs.length && !dailyLogLoading) {
        const empty = document.createElement("div");
        empty.className = "empty-detail";
        empty.textContent = "暂无按天日志";
        rawOutput.appendChild(empty);
        const fallback = buildLogText(run, runtimeStatus);
        if (fallback && fallback !== "暂无后台运行日志。") {
          const preview = document.createElement("pre");
          preview.className = "recent-log-preview";
          preview.textContent = fallback;
          rawOutput.appendChild(preview);
        }
        return;
      }

      const list = document.createElement("div");
      list.className = "log-day-list";
      for (const log of dailyLogs) {
        const row = document.createElement("div");
        row.className = "log-day-row";
        row.title = log.path || "";

        const main = document.createElement("span");
        main.className = "log-day-main";
        const date = document.createElement("strong");
        date.textContent = log.date;
        const metaText = document.createElement("span");
        metaText.textContent = `${Number(log.count || 0)} 条 · ${formatBytes(log.sizeBytes)}`;
        main.appendChild(date);
        main.appendChild(metaText);

        const actionsEl = document.createElement("span");
        actionsEl.className = "log-day-actions";

        const open = document.createElement("button");
        open.className = "log-open-label";
        open.type = "button";
        open.dataset.logAction = "open";
        open.dataset.logDate = log.date;
        open.textContent = "打开";

        const remove = document.createElement("button");
        remove.className = "log-delete";
        remove.type = "button";
        remove.dataset.logAction = "delete";
        remove.dataset.logDate = log.date;
        remove.title = "删除这天日志";
        remove.setAttribute("aria-label", `删除 ${log.date} 的运行日志`);
        remove.textContent = "×";

        actionsEl.appendChild(open);
        actionsEl.appendChild(remove);

        row.appendChild(main);
        row.appendChild(actionsEl);
        list.appendChild(row);
      }
      rawOutput.appendChild(list);
    }

    function renderChatInspector(snap) {
      const run = snap.lastRun || {};
      const runtimeStatus = snap.runtimeStatus || {};
      if (run.requestId && run.requestId !== lastRunRequestId) {
        dismissedTaskGroups.clear();
        expandedTaskGroups.clear();
        collapsedTaskGroups.clear();
        lastRunRequestId = run.requestId;
      }

      const steps = combinedTasks(run, runtimeStatus);
      stepsEl.innerHTML = "";

      if (!steps.length) {
        const empty = document.createElement("div");
        empty.className = "empty-detail";
        empty.textContent = "暂无任务";
        stepsEl.appendChild(empty);
      }

      const groups = groupedTasks(steps).filter((group) => !dismissedTaskGroups.has(group.key));
      if (!groups.length && steps.length) {
        const empty = document.createElement("div");
        empty.className = "empty-detail";
        empty.textContent = "当前任务视图已清空";
        stepsEl.appendChild(empty);
      }

      for (const group of groups) {
        const groupOpen = isGroupOpen(group);
        const node = document.createElement("div");
        node.className = "task-group";
        const groupClass = statusClasses[group.status] || "";
        if (groupClass) node.classList.add(groupClass);
        node.classList.toggle("expanded", groupOpen);

        const summary = document.createElement("div");
        summary.className = "task-group-summary";
        summary.dataset.taskToggle = group.key;
        summary.setAttribute("role", "button");
        summary.setAttribute("tabindex", "0");
        summary.setAttribute("aria-expanded", String(groupOpen));
        const groupTitle = document.createElement("span");
        groupTitle.className = "task-group-title";
        groupTitle.textContent = group.title;
        const count = document.createElement("span");
        count.className = "task-count";
        count.textContent = `${group.items.length} 条`;
        const groupStatus = document.createElement("span");
        groupStatus.className = "task-group-status";
        groupStatus.textContent = translateValue(group.status);
        const remove = document.createElement("button");
        remove.className = "task-delete";
        remove.type = "button";
        remove.dataset.taskDelete = group.key;
        remove.title = "删除这组任务";
        remove.setAttribute("aria-label", `删除 ${group.title}`);
        remove.textContent = "×";
        const preview = document.createElement("span");
        preview.className = "task-group-preview";
        preview.textContent = humanizeBackendText(group.summary) || group.summary || "暂无摘要";

        summary.appendChild(groupTitle);
        summary.appendChild(count);
        summary.appendChild(groupStatus);
        summary.appendChild(remove);
        summary.appendChild(preview);
        node.appendChild(summary);

        const body = document.createElement("div");
        body.className = "task-group-body";
        const items = document.createElement("div");
        items.className = "task-group-items";
        for (const step of group.items) {
          items.appendChild(makeStepNode(step));
        }
        body.appendChild(items);
        node.appendChild(body);
        stepsEl.appendChild(node);
      }

      renderLogs();
    }

    function renderChrome(page, snap) {
      const run = snap.lastRun || {};
      const runtimeStatus = snap.runtimeStatus || {};
      const chrome = pageChrome[page] || pageChrome.chat;
      caption.textContent = chrome.caption;
      title.textContent = chrome.title;

      const running = run.phase === "running";
      const idle = run.phase === "idle";
      const hasRuntimeFailure = runtimeStatus.ok === false;

      if (page === "chat") {
        status.textContent = running ? "运行中" : hasRuntimeFailure ? "异常" : idle ? "空闲" : run.ok ? "完成" : "失败";
        status.className = `run-pill ${running ? "running" : hasRuntimeFailure ? "failed" : idle ? "" : run.ok ? "ok" : "failed"}`;
        meta.textContent = [
          run.code !== "" && run.code !== undefined ? `退出码 ${run.code}` : null,
          run.elapsedMs ? `耗时 ${formatElapsedMs(run.elapsedMs)}` : null,
          runtimeStatus.payload?.pending_confirmations ? `待确认 ${runtimeStatus.payload.pending_confirmations}` : null
        ].filter(Boolean).join("  ·  ");
        return;
      }

      status.textContent = backendStatusLabel(runtimeStatus);
      status.className = `run-pill ${runtimeStatus?.loading ? "running" : runtimeStatus?.ok === false ? "failed" : runtimeStatus?.ok === true ? "ok" : ""}`;
      meta.textContent = [
        runtimeStatus.payload?.model ? `模型 ${runtimeStatus.payload.model}` : null
      ].filter(Boolean).join("  ·  ");
    }

    function renderExecuteDashboard(snap) {
      const run = snap.lastRun || {};
      const runtimeStatus = snap.runtimeStatus || {};
      const payload = runtimeStatus.payload || {};
      const operational = payload.runtime?.operational || {};
      const persistedExecution = operational.latest_execution || {};
      const hasPersistedExecution = Boolean(
        persistedExecution.request_id
        || persistedExecution.run_id
        || persistedExecution.committed_at
      );
      const tasks = combinedTasks(run, runtimeStatus);
      const groups = groupedTasks(tasks);
      const planner = payload.runtime?.planner_execution || {};
      const statusItems = taskStatusItems(tasks);
      const pendingConfirmations = Number(payload.pending_confirmations || 0);
      const familyItems = groups.map((group) => ({
        label: group.title,
        value: group.items.length,
        tone: statusTone(group.status)
      }));
      const statusBars = statusItems.map((item) => ({ label: item.label, value: item.value, tone: item.tone }));
      const totalTasks = tasks.length;

      return `
        ${metricsGrid([
          { label: "最近执行", value: run.elapsedMs ? formatElapsedMs(run.elapsedMs) : hasPersistedExecution ? formatExecutionTime(persistedExecution.completed_at_ms || persistedExecution.committed_at) : "暂无记录", hint: run.phase === "running" ? "正在执行" : hasPersistedExecution ? translateValue(persistedExecution.status || "COMPLETED") : "本次会话未执行", tone: run.phase === "running" ? "running" : run.ok === false ? "failed" : "" },
          { label: "任务队列", value: `${Number(operational.active_task_count || 0)}`, hint: `共 ${Number(operational.task_total || 0)} 项`, tone: Number(operational.active_task_count || 0) ? "running" : "" },
          { label: "待确认", value: `${pendingConfirmations}`, hint: "生命周期/权限确认", tone: pendingConfirmations ? "warn" : "" },
          { label: "执行账本", value: `${Number(operational.execution_total || 0)}`, hint: `完成 ${Number(operational.completed_execution_count || 0)} · 失败 ${Number(operational.failed_execution_count || 0)}`, tone: Number(operational.failed_execution_count || 0) ? "failed" : "" }
        ])}
        ${dashSection(
          "执行分布",
          `
            <div class="dash-chart-grid">
              ${donutChart(statusItems, `${totalTasks}`, "任务状态")}
              ${barList(statusBars)}
            </div>
          `,
          "只显示统计，不重复任务明细"
        )}
        ${dashSection(
          "链路占比",
          barList(familyItems),
          "按合并判定、上下文、提示词、执行等归类"
        )}
        ${dashSection(
          "路由概览",
          kvRows([
            ["Planner", translateValue(planner.status || "未返回")],
            ["模型路由", "模型主导，系统只保留硬边界"]
          ]),
          "不提供手动工具选择入口"
        )}
      `;
    }

    function renderKnowledgeDashboard() {
      if (knowledgeStats.loading && !knowledgeStats.loaded) {
        return `<div class="empty-detail">正在读取知识库统计</div>`;
      }
      if (knowledgeStats.error) {
        return `
          ${metricsGrid([{ label: "知识库", value: "读取失败", hint: knowledgeStats.error, tone: "failed" }])}
          ${dashSection("统计", `<div class="empty-detail compact">修复桥接后会自动恢复</div>`)}
        `;
      }
      const summary = summarizeKnowledgeDocuments(knowledgeStats.documents);
      return `
        ${metricsGrid([
          { label: "文档", value: `${summary.total}`, hint: "已入库文件" },
          { label: "引用片段", value: `${formatCount(summary.totalCitations)}`, hint: "可检索上下文" },
          { label: "总容量", value: formatBytes(summary.totalBytes), hint: "索引来源大小" },
          { label: "最近入库", value: latestDateLabel(summary.latest), hint: knowledgeStats.loading ? "刷新中" : "已同步" }
        ])}
        ${dashSection("文件类型", barList(summary.byType), "仅做总量统计")}
        ${dashSection("解析状态", barList(summary.byStatus), "不重复文档列表")}
      `;
    }

    function renderSkillsDashboard(snap) {
      if (skillsStats.loading && !skillsStats.loaded) {
        return `<div class="empty-detail">正在读取技能统计</div>`;
      }
      if (skillsStats.error) {
        return `
          ${metricsGrid([{ label: "技能资产", value: "读取失败", hint: skillsStats.error, tone: "failed" }])}
          ${dashSection("统计", `<div class="empty-detail compact">桥接恢复后会自动刷新</div>`)}
        `;
      }

      const categories = safeArray(skillsStats.categories);
      const abilities = safeArray(skillsStats.abilities);
      const summary = skillsStats.summary || {};
      const activeCategory = snap.activeSkillCategory || "all";
      const selectedCategory = categories.find((item) => item.id === activeCategory);
      const visibleAbilities = activeCategory === "all"
        ? abilities
        : abilities.filter((item) => item.category === activeCategory);
      const runtimeToolCount = Number(summary.runtimeToolCount || categories.reduce((sum, item) => sum + Number(item.toolCount || 0), 0));
      const runtimeUsableCount = Number(summary.runtimeUsableCount || abilities.filter((item) => item.runtimeUsable !== false).length);
      const generatedCount = Number(summary.generatedCount || abilities.filter((item) => item.generated).length);
      const generatedActiveCount = Number(summary.generatedActiveCount || abilities.filter((item) => item.generatedStage === "active").length);
      const generatedCandidateCount = Number(summary.generatedCandidateCount || abilities.filter((item) => item.generatedStage === "candidate").length);
      const categoryBars = categories.map((item) => ({
        label: item.label || item.id,
        value: Number(item.abilityCount || 0),
        tone: item.id === activeCategory ? "running" : ""
      }));
      const toolBars = categories.map((item) => ({
        label: item.label || item.id,
        value: Number(item.toolCount || 0),
        tone: item.id === activeCategory ? "running" : ""
      }));
      const statusBars = Object.entries(summary.statusCounts || {}).map(([label, value]) => ({
        label: translateValue(label),
        value: Number(value || 0),
        tone: label === "active" ? "ok" : label === "failed" ? "failed" : ""
      }));
      const levelBars = Object.entries(summary.levelCounts || {}).map(([label, value]) => ({
        label: label.toUpperCase(),
        value: Number(value || 0),
        tone: ""
      }));

      return `
        ${metricsGrid([
          { label: "技能包", value: `${Number(summary.abilityCount || abilities.length)}`, hint: `${visibleAbilities.length} 个当前可见` },
          { label: "可运行", value: `${runtimeUsableCount}`, hint: "runtime_usable 能力包", tone: runtimeUsableCount ? "ok" : "" },
          { label: "新生成", value: `${generatedCount}`, hint: `${generatedActiveCount} 已激活 / ${generatedCandidateCount} 候选`, tone: generatedCount ? "running" : "" },
          { label: "工具分类", value: `${Number(summary.toolCategoryCount || categories.length)}`, hint: "模型可见大分类" },
          { label: "运行工具", value: `${runtimeToolCount}`, hint: `${Number(summary.abilityToolPackageCount || 0)} 个技能工具包` }
        ])}
        ${dashSection(
          "技能分类占比",
          barList(categoryBars),
          "随左侧分类同步高亮"
        )}
        ${dashSection(
          "工具分类容量",
          barList(toolBars),
          "统计运行时工具，不提供手动调用入口"
        )}
        ${dashSection("状态分布", barList(statusBars), "只读统计")}
        ${dashSection("等级分布", barList(levelBars), "只读统计")}
        ${dashSection(
          "当前分类",
          kvRows([
            ["分类", activeCategory === "all" ? "全部分类" : (selectedCategory?.label || activeCategory)],
            ["技能包", `${visibleAbilities.length}`],
            ["运行工具", activeCategory === "all" ? `${runtimeToolCount}` : `${Number(selectedCategory?.toolCount || 0)}`],
            ["说明", activeCategory === "all" ? "查看全部技能资产" : (selectedCategory?.description || "未记录")]
          ])
        )}
        ${dashSection(
          "自动整理",
          kvRows([
            ["整理状态", "已接入 R18/R20 新生成资产"],
            ["候选资产", `${generatedCandidateCount}`],
            ["激活资产", `${generatedActiveCount}`],
            ["索引", skillsStats.generatedIndexPath || "等待首次刷新"]
          ]),
          "同一套卡片格式，不提供手动工具选择"
        )}
      `;
    }

    function renderLifecycleDashboard(snap) {
      const payload = snap.runtimeStatus?.payload || {};
      const lifePanel = lifePanelStats.data || {};
      const activation = payload.lifecycle?.runtime?.activation || {};
      const lifecycleRuntime = payload.lifecycle?.runtime || {};
      const learningPayload = payload.learning || {};
      const learning = learningPayload.learning_cards || {};
      // The embedded Life kernel is the single authority.  The legacy runtime
      // intentionally has no second heartbeat, so reading it here made a
      // healthy scheduler look like a permanently inactive 20% subsystem.
      const freeWill = lifePanel.free_will || payload.lifecycle?.free_will || payload.runtime?.free_will || {};
      const authorityLearning = lifePanel.learning || {};
      const authorityCards = safeArray(authorityLearning.latest).filter((item) => !["discarded", "disabled", "duplicate_removed", "no_value"].includes(String(item?.status || item?.promotion_stage || "").toLowerCase()));
      const learningCandidateCount = Number(authorityLearning.candidate_count ?? authorityCards.length ?? learning.pending_learning ?? 0);
      const learningConfirmCount = authorityCards.filter((item) => Boolean(item?.can_confirm_learning)).length;
      const scheduler = lifePanel.scheduler || {};
      const latestAction = lifePanel.summary?.recent_action || freeWill.latest_autonomous_action || {};
      const latestAutonomousAction = freeWill.latest_autonomous_action || {};
      const autonomyTasks = safeArray(lifePanel.tasks);
      const autonomyStatusOf = (item) => String(item?.status || "").toLowerCase();
      const runningAutonomyCount = autonomyTasks.filter((item) => autonomyStatusOf(item) === "running").length;
      const queuedAutonomyCount = autonomyTasks.filter((item) => autonomyStatusOf(item) === "pending").length;
      // "待确认"只统计真正等待用户确认的条目：status=awaiting_user，且当任务
      // 对象带 requires_user 字段时必须为 true，不与内部自主任务混合。
      const awaitingUserAutonomyCount = autonomyTasks.filter((item) =>
        autonomyStatusOf(item) === "awaiting_user" && item?.requires_user !== false
      ).length;
      const blockedAutonomyCount = autonomyTasks.filter((item) => autonomyStatusOf(item) === "blocked").length;
      // These tasks are internal autonomous activities (requires_user=false),
      // not user to-dos.  Report the plan composition instead of a single
      // misleading "待执行" bucket.
      const internalAutonomyTotal = runningAutonomyCount + queuedAutonomyCount + awaitingUserAutonomyCount + blockedAutonomyCount;
      const autonomyPlanSummary = internalAutonomyTotal
        ? `今日内部自主计划 ${internalAutonomyTotal} 项（运行 ${runningAutonomyCount} / 排队 ${queuedAutonomyCount} / 待确认 ${awaitingUserAutonomyCount}${blockedAutonomyCount ? ` / 受阻 ${blockedAutonomyCount}` : ""}）`
        : "";
      const hasFreeWillAction = Boolean(
        latestAutonomousAction.id
        || latestAutonomousAction.task_id
        || latestAutonomousAction.request_id
        || latestAutonomousAction.trace_id
        || latestAutonomousAction.started_at
      );
      const hasExecutionRecord = Boolean(
        latestAction.id
        || latestAction.request_id
        || latestAction.trace_id
        || latestAction.started_at
        || latestAction.committed_at
      );
      const skipReasonNames = {
        disabled: "开关关闭",
        heartbeat_not_running: "心跳未运行",
        user_recently_active: "用户刚活跃",
        consecutive_limit: "连续次数达上限",
        curiosity_below_threshold: "好奇心未超过阈值",
        phase_too_early: "生命阶段过早",
        no_trigger_record: "暂无自主行动记录",
        not_enabled: "功能未开启",
        no_llm: "模型未就绪",
        api_not_configured: "模型接口未配置",
        budget_exhausted: "模型预算已用完",
        no_selected_candidates_in_current_window: "当前时间窗口没有可执行任务",
        user_active_deferred_without_refresh: "用户活跃，重任务已暂缓",
        life_llm_daily_attempt_budget_exhausted: "模型尝试预算已用完",
        life_llm_daily_success_budget_exhausted: "模型成功预算已用完"
      };
      const humanizeLifecycleText = (value) => {
        const text = String(value || "").trim();
        if (!text) return "";
        if (skipReasonNames[text]) return skipReasonNames[text];
        if (/xintiao_zizhu|LLM_zizhu/i.test(text)) return "暂无自主心跳或自主行动记录";
        return translateLifecycleText(text);
      };
      const skipReason = humanizeLifecycleText(scheduler.suspended_reason)
        || humanizeLifecycleText(scheduler.last_judgment?.reason)
        || humanizeLifecycleText(freeWill.skip_detail)
        || humanizeLifecycleText(freeWill.skip_reason)
        || (hasFreeWillAction
          ? ""
          : internalAutonomyTotal
            ? autonomyPlanSummary
            : "暂无自主心跳或自主行动记录");
      const freeWillState = scheduler.status === "suspended"
        ? "已暂停"
        : freeWill.enabled === false
        ? "已关闭"
        : hasFreeWillAction
          ? "有自主行动"
          : internalAutonomyTotal
            ? "自主计划进行中"
          : freeWill.heartbeat_running
            ? "正在运行"
            : "未运行";
      const pendingUpdates = safeArray(payload.lifecycle?.pending_updates);
      const localContextChars = safeArray(snap.messages).reduce((total, item) => total + String(item?.content || "").length, 0);
      const fallbackContextRatio = localContextChars ? clamp((localContextChars / 120000) * 100) : 0;
      const authoritativeContext = lifePanel.context || {};
      const hasAuthoritativeContextRatio = authoritativeContext.available === true
        && Number(authoritativeContext.token_budget) > 0
        && (Object.prototype.hasOwnProperty.call(authoritativeContext, "context_utilization_milli")
          || Object.prototype.hasOwnProperty.call(authoritativeContext, "current_context_tokens"));
      const runtimeContextRatio = Number(activation.context_usage_ratio || 0) * 100;
      const contextRatio = contextPressurePercent(
        authoritativeContext,
        Number(activation.context_usage_ratio || 0),
        fallbackContextRatio,
      );
      const contextMetricLabel = hasAuthoritativeContextRatio || runtimeContextRatio ? "上下文压力" : "对话窗口占用";
      const memoryActive = Boolean(activation.memory_recall_active || wiringValue(payload, ["runtime", "interface_wiring", "memory_recall", "memory_store_attached"], false));
      const affectiveActive = Boolean(activation.affective_active || wiringValue(payload, ["runtime", "interface_wiring", "affective", "emotion_engine_attached"], false));
      const lifecycleActive = Boolean(activation.lifecycle_active || pendingUpdates.length || lifecycleRuntime.scheduler_attached);
      const forgettingActive = Boolean(activation.forgetting_active || wiringValue(payload, ["runtime", "interface_wiring", "forgetting_review", "reviewer_attached"], false));
      const freeWillHeartbeatActive = Boolean(freeWill.heartbeat_running || activation.free_will_heartbeat_active);
      const activeCount = [memoryActive, affectiveActive, lifecycleActive, forgettingActive, freeWillHeartbeatActive].filter(Boolean).length;
      const stability = Math.round((activeCount / 5) * 100);

      return `
        ${metricsGrid([
          { label: "接线健康", value: `${stability}%`, hint: `${activeCount}/5 运行接口`, tone: stability >= 75 ? "ok" : "warn" },
          { label: contextMetricLabel, value: `${Math.round(contextRatio)}%`, hint: hasAuthoritativeContextRatio ? "当前对话占用 / 生命可用预算" : "当前对话窗口估算", tone: contextRatio > 75 ? "warn" : "" },
          { label: "待确认", value: `${pendingUpdates.length}`, hint: "自主更新闭环", tone: pendingUpdates.length ? "warn" : "" },
          { label: "学习候选", value: `${learningCandidateCount}`, hint: `需确认 ${learningConfirmCount}`, tone: learningCandidateCount ? "running" : "" }
        ])}
        ${dashSection(
          "生命体征",
          `
            <div class="vital-board">
              <div class="life-vital-glyph ${stability >= 75 ? "stable" : "watch"}" aria-label="生命状态 ${stability}%">
                <span class="life-vital-ring"></span>
                <span class="life-vital-core ${affectiveActive ? "active" : ""}"></span>
                <span class="life-vital-leaf left ${memoryActive ? "active" : ""}"></span>
                <span class="life-vital-leaf right ${lifecycleActive ? "active" : ""}"></span>
                <span class="life-vital-root ${forgettingActive ? "active" : ""}"></span>
                <span class="life-vital-spark ${contextRatio > 75 ? "warn" : "active"}"></span>
              </div>
              ${kvRows([
                ["记忆召回", memoryActive ? "活跃" : "待机", memoryActive ? "ok" : ""],
                ["情感系统", affectiveActive ? "活跃" : "待机", affectiveActive ? "ok" : ""],
                ["生命周期", lifecycleActive ? "活跃" : "待机", lifecycleActive ? "ok" : ""],
                ["遗忘检查", forgettingActive ? "活跃" : "待机", forgettingActive ? "ok" : ""],
                ["自由意志心跳", freeWillHeartbeatActive ? "正在运行" : "未运行", freeWillHeartbeatActive ? "ok" : ""],
                ["自主行动", [hasFreeWillAction ? "有可验证记录" : "", internalAutonomyTotal ? autonomyPlanSummary : ""].filter(Boolean).join("；") || "暂无可验证记录", awaitingUserAutonomyCount ? "warn" : hasFreeWillAction ? "ok" : internalAutonomyTotal ? "running" : "warn", internalAutonomyTotal ? ' data-open-life-schedule role="button" tabindex="0" title="点击查看生命日程" style="cursor:pointer"' : ""],
                ...(awaitingUserAutonomyCount ? [["待你确认", `${awaitingUserAutonomyCount} 项自主活动等待你的确认`, "warn", ' data-open-life-schedule role="button" tabindex="0" title="点击查看生命日程" style="cursor:pointer"']] : []),
                ["执行终态", hasExecutionRecord ? "有记录" : "暂无", hasExecutionRecord ? "ok" : "warn"]
              ])}
            </div>
          `,
          "用状态映射，不重复中间的确认列表"
        )}
        ${dashSection(
          "策略摘要",
          kvRows([
            ["自由意志状态", freeWillState],
            ["最近自主行动", [hasFreeWillAction ? (latestAutonomousAction.started_at ? "已记录（" + formatShortDate(latestAutonomousAction.started_at) + "）" : "已记录") : "", internalAutonomyTotal ? autonomyPlanSummary : ""].filter(Boolean).join("；") || "暂无可验证记录"],
            ["最近执行终态", hasExecutionRecord ? "已记录" : "暂无"],
            ["未触发原因", skipReason || "暂无"],
            ["学习卡总数", String(authorityCards.length || Number(learning.total || 0))],
            ["需确认候选", `${learningConfirmCount} / ${authorityCards.length ? "候选模式" : (learningPayload.candidate_only ? "候选模式" : "可学习")}`]
          ])
        )}
      `;
    }

    function renderPersonaDashboard(snap) {
      const settings = snap.settings || {};
      const userCard = currentUserCard(settings);
      const userName = userCard.userDisplayName || "";
      const userCallsign = userCard.userCallsign || "";
      const userWork = userCard.userWork || "";
      const userSummary = userCard.userProfileSummary || "";
      const userInitial = (userName || userCallsign || "用").slice(0, 1);
      const avatar = userCard.userAvatarDataUrl
        ? `<img src="${escHtml(userCard.userAvatarDataUrl)}" alt="用户头像" />`
        : `<span>${escHtml(userInitial)}</span>`;

      return `
        ${dashSection(
          "用户卡片",
          `
            <div class="user-card-editor">
              <div class="user-card-avatar-row">
                <div class="profile-avatar">${avatar}</div>
                <div class="user-card-avatar-actions">
                  <button class="small-command" type="button" data-user-card-action="choose-avatar">选择头像</button>
                  <button class="small-command muted-command" type="button" data-user-card-action="clear-avatar">清除</button>
                </div>
              </div>
              <label class="side-field">
                <span>用户姓名</span>
                <input data-user-card-field="userDisplayName" maxlength="32" value="${escHtml(userName)}" placeholder="例如：子涛" />
              </label>
              <label class="side-field">
                <span>模型称谓</span>
                <input data-user-card-field="userCallsign" maxlength="32" value="${escHtml(userCallsign)}" placeholder="例如：老板、先生、老师" />
              </label>
              <label class="side-field">
                <span>用户工作</span>
                <input data-user-card-field="userWork" maxlength="80" value="${escHtml(userWork)}" placeholder="例如：产品负责人" />
              </label>
              <label class="side-field side-field-tall">
                <span>用户画像</span>
                <textarea data-user-card-field="userProfileSummary" maxlength="1200" placeholder="写用户背景、偏好、当前项目、沟通方式等。">${escHtml(userSummary)}</textarea>
              </label>
              <label class="inline-check user-card-check">
                <input data-user-card-field="userContextEnabled" type="checkbox" ${userCard.userContextEnabled ? "checked" : ""} />
                <span>允许后续作为用户卡片插入提示词</span>
              </label>
              <div class="user-card-actions">
                <span id="userCardSaveState" class="mini-pill ${userCardDirty ? "warn" : "ok"}">${userCardDirty ? "待保存" : "已同步"}</span>
                <button class="small-command" type="button" data-user-card-action="save">保存用户卡片</button>
              </div>
            </div>
          `,
          "中间栏保留人物设定，这里编辑用户资料"
        )}
      `;
    }

    function renderSettingsDashboard(snap) {
      const settings = snap.settings || {};
      const runtimeStatus = snap.runtimeStatus || {};
      const payload = runtimeStatus.payload || {};
      const backendConfig = snap.backendConfig || {};
      const freeWill = payload.lifecycle?.free_will || payload.runtime?.free_will || {};
      const freeWillState = freeWill.enabled === false
        ? "已关闭"
        : freeWill.heartbeat_running
          ? "正在运行"
          : "未运行";
      return `
        ${metricsGrid([
          { label: "后端", value: backendStatusLabel(runtimeStatus), hint: runtimeStatus.text || "运行状态", tone: runtimeStatus.ok === true ? "ok" : runtimeStatus.ok === false ? "failed" : "" },
          { label: "端点", value: translateValue(payload.endpoint_state || "unknown"), hint: payload.provider || settings.modelProvider || "provider", tone: payload.endpoint_state === "ready" ? "ok" : payload.endpoint_state ? "" : "warn" },
          { label: "密钥", value: translateValue(payload.credential_state || "unknown"), hint: "本机保存", tone: payload.credential_state === "configured" ? "ok" : "warn" },
          { label: "运行内核", value: payload.kernel_importable ? "可导入" : "未确认", hint: backendConfig.loading ? "校验中" : "Python 运行核", tone: payload.kernel_importable ? "ok" : "" }
        ])}
        ${dashSection(
          "系统边界",
          kvRows([
            ["能力路由", settings.plannerMode ? translateValue(settings.plannerMode) : "模型主导"],
            ["工具选择", settings.toolMode ? translateValue(settings.toolMode) : "模型自主选择"],
            ["风险边界", "A5 硬边界"]
          ]),
          "只显示真实生效的后台策略"
        )}
        ${dashSection(
          "运行设置摘要",
          kvRows([
            ["模型服务", settings.modelService || payload.provider || "未设置"],
            ["模型名", settings.modelName || payload.model || "未设置"],
            ["自由意志状态", freeWillState]
          ]),
          "不重复中间表单控件"
        )}
      `;
    }

    function renderDashboard(page, snap) {
      if (page === "knowledge" && !knowledgeStats.loaded && !knowledgeStats.loading) {
        void refreshKnowledgeStats();
      }
      if (page === "skills" && !skillsStats.loaded && !skillsStats.loading) {
        void refreshSkillsStats();
      }
      if (page === "lifecycle" && !lifePanelStats.loaded && !lifePanelStats.loading) {
        void refreshLifePanelStats();
      }
      const body = page === "execute"
        ? renderExecuteDashboard(snap)
        : page === "knowledge"
          ? renderKnowledgeDashboard(snap)
          : page === "skills"
            ? renderSkillsDashboard(snap)
            : page === "lifecycle"
              ? renderLifecycleDashboard(snap)
              : page === "persona"
                ? renderPersonaDashboard(snap)
                : page === "settings"
                  ? renderSettingsDashboard(snap)
                  : "";
      dashboardPanel.innerHTML = body || `<div class="empty-detail">暂无仪表盘</div>`;
    }

    function renderInspector() {
      const snap = state.snapshot();
      const page = snap.activePage || "chat";
      renderChrome(page, snap);
      const isChat = page === "chat";
      chatInspector.hidden = !isChat;
      dashboardPanel.hidden = isChat;
      dashboardPanel.classList.toggle("active", !isChat);
      if (isChat) {
        renderChatInspector(snap);
      } else {
        renderDashboard(page, snap);
      }
    }

    state.on("run", () => {
      renderInspector();
      const run = state.snapshot().lastRun || {};
      if (run.phase === "finished") void refreshDailyLogs(true);
    });
    state.on("runtimeStatus", renderInspector);
    state.on("settings", renderInspector);
    state.on("backendConfig", renderInspector);
    state.on("skillCategory", renderInspector);
    state.on("page", (page) => {
      renderInspector();
      if (page === "execute") void refreshDailyLogs();
      if (page === "knowledge") void refreshKnowledgeStats();
      if (page === "skills") void refreshSkillsStats();
      if (page === "lifecycle") void refreshLifePanelStats();
    });
    window.addEventListener("tiangong-life-changed", () => {
      lifePanelStats.loaded = false;
      if (state.snapshot().activePage === "lifecycle") void refreshLifePanelStats(true);
    });
    renderInspector();
    void refreshDailyLogs(true);
  },
};
