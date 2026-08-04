import { lifeApi } from "../runtime/life-api.mjs";
import { humanizeBackendError } from "../core/formatters.mjs";

const REFRESH_INTERVAL_MS = 30000;
let lifeSummaryTimer = null;

const ICONS = {
  sprout: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 21v-8"/><path d="M12 13c-4.2 0-7-2.9-7-7 4.2 0 7 2.9 7 7Z"/><path d="M12 13c4.2 0 7-2.9 7-7-4.2 0-7 2.9-7 7Z"/></svg>`,
  heart: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.4 6.8a5 5 0 0 0-7.1 0L12 8.1l-1.3-1.3a5 5 0 1 0-7.1 7.1L12 22l8.4-8.1a5 5 0 0 0 0-7.1Z"/><path d="M7.4 12h2.4l1.4-2.6 2 5.2 1.4-2.6h2"/></svg>`,
  scroll: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 4h10a3 3 0 0 1 3 3v13H8a4 4 0 0 1-4-4V7a3 3 0 0 1 3-3Z"/><path d="M8 20a3 3 0 0 0 0-6H4"/><path d="M10 8h6"/><path d="M10 12h5"/></svg>`,
  envelopeClosed: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="6" width="17" height="12" rx="2"/><path d="m4.5 7.2 7.5 6 7.5-6"/><path d="m4.8 17 5.4-5"/><path d="m19.2 17-5.4-5"/></svg>`,
  envelopeOpen: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 10.2V18a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.8"/><path d="m4.8 10.8 6.1 4.4a2 2 0 0 0 2.2 0l6.1-4.4"/><path d="M7 10V6.5A2.5 2.5 0 0 1 9.5 4h5A2.5 2.5 0 0 1 17 6.5V10"/></svg>`,
  trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="m6.5 7 1 13h9l1-13"/><path d="M10 11v5M14 11v5"/></svg>`
};

const KIND_TEXT = {
  idle: "待机",
  auto: "自动",
  autonomous: "自主行动",
  manual: "手动",
  dream: "梦境整理",
  dreaming: "梦境整理",
  dream_summary: "梦境总结",
  reflect: "反思",
  reflection: "反思",
  learn: "学习",
  learning: "学习",
  share: "分享",
  life_share: "行动心得分享",
  self_clean: "自洁",
  clean: "整理",
  cleanup: "整理",
  schedule: "日程",
  daily_plan: "每日日程",
  self_healing: "自我愈合",
  self_learning: "自主学习",
  self_iteration: "自我迭代",
  knowledge_memory_tidy: "知识与记忆梳理",
  connection: "关系维护",
  candidate: "候选",
  completed: "已完成",
  done: "已完成",
  success: "成功",
  failed: "失败",
  skipped: "已跳过",
  running: "进行中",
  pending: "等待中"
};

function esc(value) {
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

function safeObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function numberValue(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function compact(value, limit = 96, fallback = "暂无") {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text) return fallback;
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function firstText(...values) {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (text) return text;
  }
  return "";
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const EXTRA_KIND_PHRASES = {
  "self healing": "自我愈合",
  "self learning": "自主学习",
  "self iteration": "自我迭代",
  "knowledge memory tidy": "知识与记忆梳理",
  "daily plan": "每日日程",
  "dream summary": "梦境总结",
  "life share": "行动心得分享",
  "value score": "价值分",
  "llm": "模型",
  "api": "接口"
};

function translateEnglishFragments(value) {
  let text = String(value ?? "").trim();
  if (!text) return "";
  const lower = text.toLowerCase();
  const normalized = lower.replace(/[_.-]+/g, " ").replace(/\s+/g, " ").trim();
  if (EXTRA_KIND_PHRASES[normalized]) return EXTRA_KIND_PHRASES[normalized];
  if (KIND_TEXT[lower] || KIND_TEXT[lower.replace(/[ .-]+/g, "_")]) {
    return KIND_TEXT[lower] || KIND_TEXT[lower.replace(/[ .-]+/g, "_")];
  }
  let output = text.replace(/[_.-]+/g, " ");
  const entries = Object.entries({ ...KIND_TEXT, ...EXTRA_KIND_PHRASES })
    .map(([key, label]) => [String(key).toLowerCase().replace(/[_.-]+/g, " ").replace(/\s+/g, " ").trim(), label])
    .filter(([key]) => key && /[a-z]/i.test(key))
    .sort((a, b) => b[0].length - a[0].length);
  for (const [key, label] of entries) {
    const pattern = new RegExp(`\\b${escapeRegExp(key).replace(/\\s+/g, "\\s+")}\\b`, "gi");
    output = output.replace(pattern, label);
  }
  return output
    .replace(/\bLLM\b/g, "模型")
    .replace(/\bAPI\b/gi, "接口")
    .replace(/\btrue\b/gi, "是")
    .replace(/\bfalse\b/gi, "否")
    .replace(/\s+/g, " ")
    .trim();
}

function labelForKind(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const lower = text.toLowerCase();
  const normalized = lower.replace(/[ .-]+/g, "_");
  const spaced = lower.replace(/[_.-]+/g, " ").replace(/\s+/g, " ").trim();
  return KIND_TEXT[text] || KIND_TEXT[lower] || KIND_TEXT[normalized] || EXTRA_KIND_PHRASES[spaced] || translateEnglishFragments(text);
}

function formatScore(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toFixed(1);
}

function humanizeActionText(value, limit = 56) {
  let text = String(value ?? "")
    .replace(/<think\b[^>]*>[\s\S]*?(?:<\/think>|$)/gi, " ")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[{}[\]"'`]/g, " ")
    .replace(/\b(trace_id|task_id|value_score|created_at|updated_at|kind|status|summary|reflection)\b[:：]*/gi, " ")
    .replace(/[,_]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (!text) return "这次行动已经记录，等待下一次复盘。";
  text = translateEnglishFragments(text);
  return compact(text, limit, "这次行动已经记录，等待下一次复盘。");
}

function formatMinutes(value) {
  if (value === null || typeof value === "undefined" || value === "") return "未提供";
  const number = Number(value);
  if (!Number.isFinite(number)) return "未知";
  if (number <= 0) return "即将触发";
  if (number < 60) return `${Math.round(number)} 分钟`;
  const hours = Math.floor(number / 60);
  const minutes = Math.round(number % 60);
  return minutes ? `${hours} 小时 ${minutes} 分钟` : `${hours} 小时`;
}

function formatDate(value) {
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

function fetchLifePanelPayload() {
  return lifeApi.getPanel();
}

function markInboxRead(messageIdValue) {
  const message_id = String(messageIdValue || "").trim();
  if (!message_id) return Promise.resolve({ ok: false, error: "empty_message_id" });
  return lifeApi.markInboxRead(message_id);
}

function deleteInboxMessage(messageIdValue) {
  const message_id = String(messageIdValue || "").trim();
  if (!message_id) return Promise.resolve({ ok: false, error: "empty_message_id" });
  return lifeApi.deleteInboxMessage(message_id);
}

function ackProactiveChat(messageIdValue, sessionId = "") {
  const message_id = String(messageIdValue || "").trim();
  if (!message_id) return Promise.resolve({ ok: false, error: "empty_message_id" });
  return lifeApi.ackProactiveChat(message_id, { session_id: String(sessionId || "") });
}

export async function deliverProactiveChats(panelPayload = {}, state, ack = ackProactiveChat) {
  const pending = safeArray(safeObject(panelPayload.proactive_chat).pending);
  let delivered = 0;
  let failed = 0;
  for (const item of pending) {
    const id = firstText(item.message_id, item.id);
    const message = firstText(item.message, item.content, item.text);
    if (!id || !message || !state?.addMessage) continue;
    const sessionId = String(state.snapshot?.().activeSessionId || "");
    const storedMessage = state.addMessage("assistant", message, false, {
      id,
      at: item.created_at,
      kind: "life_proactive_chat",
      requestId: item.source_event_id || null,
    });
    try {
      const result = await ack(id, String(storedMessage?.sessionId || sessionId));
      if (result?.ok === false || result?.delivered === false) throw new Error(result?.error || "proactive_chat_ack_failed");
      delivered += 1;
    } catch {
      // The stable message id prevents a duplicate bubble. A later poll retries
      // only the acknowledgement until the life ledger confirms delivery.
      failed += 1;
    }
  }
  return { delivered, failed };
}

function messageId(item = {}, index = 0) {
  return firstText(item.message_id, item.id, item.created_at, item.title, `inbox-${index}`);
}

export function inboxMessageIsRead(item = {}, id = "", localReadIds = new Set()) {
  const status = String(item.status || "").trim().toLowerCase();
  return Boolean(item.read) || status === "read" || status === "deleted" || localReadIds.has(id);
}

export function deliverInboxMessageToChat(item = {}, state, id = "") {
  if (!state?.addMessage) return null;
  const inboxId = firstText(id, item.message_id, item.id);
  const title = translateEnglishFragments(firstText(item.title, item.subject, "生命信箱"));
  const body = translateEnglishFragments(firstText(
    item.human_summary,
    item.llm_summary,
    item.message,
    item.summary,
    item.description,
    title
  ));
  if (!inboxId || !body) return null;
  const sessionId = String(state.snapshot?.().activeSessionId || "desktop");
  return state.addMessage("assistant", `【生命信箱 · ${title}】\n\n${body}`, false, {
    id: `life-inbox:${sessionId}:${inboxId}`,
    at: item.created_at,
    kind: "life_inbox",
    requestId: null,
  });
}

function renderInboxTitles(inbox = {}, localReadIds = new Set()) {
  const items = safeArray(inbox.items).slice(0, 6);
  if (!items.length) return `<div class="life-summary-empty">暂无信箱消息</div>`;

  return `
    <div class="life-summary-inbox-list" role="list">
      ${items.map((item, index) => {
        const id = messageId(item, index);
        const read = inboxMessageIsRead(item, id, localReadIds);
        const title = compact(translateEnglishFragments(firstText(item.title, item.subject, item.message, item.summary, "未命名消息")), 46, "未命名消息");
        return `
          <article class="life-summary-inbox-row" role="listitem">
            <div class="life-summary-inbox-actions">
              <button class="life-summary-inbox-toggle" type="button" data-life-inbox-id="${esc(id)}" aria-label="在聊天区打开 ${esc(title)}">
                <span class="life-inbox-envelope">${read ? ICONS.envelopeOpen : ICONS.envelopeClosed}</span>
                <strong>${esc(title)}</strong>
                <em>${read ? "已读" : "未读"}</em>
              </button>
              <button class="life-summary-inbox-delete" type="button" data-life-inbox-delete="${esc(id)}" title="删除消息" aria-label="删除 ${esc(title)}">${ICONS.trash}</button>
            </div>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function renderLifeGlyph(status = "活跃") {
  return `
    <div class="life-summary-glyph" aria-label="生命状态：${esc(status)}">
      <span class="life-glyph-ring"></span>
      <span class="life-glyph-core"></span>
      <span class="life-glyph-sprout"></span>
    </div>
  `;
}

function renderSummary(data = {}, loading = false, error = "", localReadIds = new Set()) {
  const summary = safeObject(data.summary);
  const inbox = safeObject(data.inbox);
  const recent = safeObject(
    Object.keys(safeObject(summary.recent_autonomous_action)).length
      ? summary.recent_autonomous_action
      : summary.recent_action
  );
  const recentResult = safeObject(recent.result);
  const completed = numberValue(summary.completed_tasks_today);
  const inboxItems = safeArray(inbox.items);
  const derivedUnread = inboxItems.filter((item, index) => {
    const id = messageId(item, index);
    return !inboxMessageIsRead(item, id, localReadIds);
  }).length;
  const unread = inboxItems.length ? derivedUnread : Math.max(0, numberValue(inbox.unread_count) - localReadIds.size);
  const canonicalStatus = String(summary.today_status || "").toLowerCase();
  const status = loading
    ? "读取中"
    : error
      ? "离线"
      : completed > 0 || canonicalStatus === "active"
        ? "活跃"
        : canonicalStatus === "alive"
          ? "存活"
          : canonicalStatus
            ? canonicalStatus
            : "未知";
  const valueScore = formatScore(recent.value_score ?? recent.score);
  const recentTitle = compact(translateEnglishFragments(firstText(recent.title, labelForKind(recent.kind), "暂无已完成行动")), 58, "暂无已完成行动");
  const humanSummary = humanizeActionText(firstText(recent.human_summary, recent.llm_summary, recent.summary, recent.reflection, recentResult.self_summary, recentResult.reflection, recentResult.summary, recentResult.outcome), 62);

  return `
    <header class="life-summary-head">
      <div>
        <span class="life-summary-caption">生命链</span>
        <h2>今日小结</h2>
      </div>
      <span class="life-state-dot ${error ? "failed" : "breathe"}" title="${esc(status)}"></span>
    </header>

    <section class="life-summary-card life-summary-today">
      <div class="life-summary-card-title">
        <span>${ICONS.sprout}</span>
        <strong>今日小结</strong>
      </div>
      <div class="life-summary-metrics">
        <div>
          <span>状态</span>
          <strong>${esc(status)}</strong>
        </div>
        <div>
          <span>完成</span>
          <strong>${esc(completed)}</strong>
        </div>
      </div>
      <p>下次心跳：${esc(formatMinutes(summary.next_heavy_tick_minutes))}</p>
    </section>

    <div class="life-summary-scroll" aria-label="最近行动">
      <section class="life-summary-card">
        <div class="life-summary-card-title">
          <span>${ICONS.heart}</span>
          <strong>最近行动</strong>
        </div>
        <strong class="life-summary-action-title">${esc(recentTitle)}</strong>
        <div class="life-summary-score">价值分：<span>${esc(valueScore)}</span></div>
        <p>${esc(humanSummary)}</p>
      </section>

      ${error ? `<div class="life-summary-error">${esc(error)}</div>` : ""}
    </div>
  `;
}

export const lifeSummaryBlockPlugin = {
  id: "life-summary-block",
  slot: "inspector",
  order: 7,
  mount({ slot, state }) {
    slot.insertAdjacentHTML("beforeend", `
      <section class="life-summary-panel" data-life-summary hidden aria-label="生命链摘要"></section>
    `);

    const panel = slot.querySelector("[data-life-summary]");
    let payload = null;
    let generation = 0;
    let loading = false;
    let lastLoadedAt = 0;
    let lastError = "";
    let localReadIds = new Set();

    function renderPage(page) {
      panel.hidden = page !== "chat";
      if (page === "chat" && (!payload || Date.now() - lastLoadedAt > REFRESH_INTERVAL_MS)) {
        void loadSummary("page");
      }
    }

    function render(error = lastError) {
      lastError = error ? humanizeBackendError(error) : "";
      panel.innerHTML = renderSummary(payload || {}, loading, lastError, localReadIds);
    }

    async function loadSummary(reason = "manual") {
      const current = ++generation;
      loading = true;
      if (!payload || reason === "manual") render();
      try {
        // The panel payload has no proactive_chat section: pending proactive
        // messages live behind their own endpoint and must be fetched in
        // parallel, then folded into the shape deliverProactiveChats reads.
        const [data, pendingResult] = await Promise.all([
          fetchLifePanelPayload(),
          lifeApi.getPendingProactiveChats().catch(() => null),
        ]);
        if (current !== generation) return;
        payload = data && typeof data === "object" ? data : {};
        const pending = safeArray(pendingResult?.messages);
        await deliverProactiveChats({ proactive_chat: { pending } }, state);
        loading = false;
        lastLoadedAt = Date.now();
        render(payload.ok === false ? (payload.error || "生命链接口返回失败") : "");
      } catch (error) {
        if (current !== generation) return;
        loading = false;
        render(error?.message || String(error));
      }
    }

    panel.addEventListener("click", async (event) => {
      const deleteButton = event.target.closest("[data-life-inbox-delete]");
      if (deleteButton) {
        event.preventDefault();
        event.stopPropagation();
        const deleteId = deleteButton.dataset.lifeInboxDelete || "";
        if (!deleteId) return;
        deleteButton.disabled = true;
        try {
          await deleteInboxMessage(deleteId);
          if (payload?.inbox && Array.isArray(payload.inbox.items)) {
            payload.inbox.items = payload.inbox.items.filter((item, index) => messageId(item, index) !== deleteId);
            payload.inbox.unread_count = payload.inbox.items.filter((item, index) => {
              const id = messageId(item, index);
              return !inboxMessageIsRead(item, id, localReadIds);
            }).length;
          }
          localReadIds.delete(deleteId);
          render("");
        } catch (error) {
          render(error?.message || String(error));
        }
        return;
      }
      const button = event.target.closest("[data-life-inbox-id]");
      if (!button) return;
      event.preventDefault();
      const scroller = panel.querySelector(".life-summary-scroll");
      const previousTop = scroller ? scroller.scrollTop : 0;
      const nextId = button.dataset.lifeInboxId || "";
      const inboxItem = safeArray(payload?.inbox?.items).find((item, index) => messageId(item, index) === nextId);
      if (nextId && inboxItem) {
        deliverInboxMessageToChat(inboxItem, state, nextId);
        const wasRead = inboxMessageIsRead(inboxItem, nextId, localReadIds);
        localReadIds.add(nextId);
        if (payload?.inbox && Array.isArray(payload.inbox.items)) {
          payload.inbox.items = payload.inbox.items.map((item, index) => (
            messageId(item, index) === nextId ? { ...item, read: true, status: "read" } : item
          ));
          if (!wasRead) payload.inbox.unread_count = Math.max(0, numberValue(payload.inbox.unread_count) - 1);
        }
        if (payload?.inbox?.available !== false) {
          try {
            const result = await markInboxRead(nextId);
            if (result?.ok === false || result?.found === false) {
              throw new Error(result?.error || "生命信箱已读状态保存失败");
            }
          } catch (error) {
            if (!wasRead) localReadIds.delete(nextId);
            if (!wasRead && payload?.inbox && Array.isArray(payload.inbox.items)) {
              payload.inbox.items = payload.inbox.items.map((item, index) => (
                messageId(item, index) === nextId ? { ...item, read: false, status: "unread" } : item
              ));
              payload.inbox.unread_count = numberValue(payload.inbox.unread_count) + 1;
            }
            render(error?.message || String(error));
            return;
          }
        }
      }

      render();
      window.requestAnimationFrame(() => {
        const nextScroller = panel.querySelector(".life-summary-scroll");
        if (nextScroller) nextScroller.scrollTop = previousTop;
      });
    });

    state.on("page", renderPage);
    window.addEventListener("tiangong-life-changed", () => {
      payload = null;
      localReadIds.clear();
      void loadSummary("life-change");
    });

    const snap = state.snapshot();
    renderPage(snap.activePage);
    render();
    void loadSummary("mount");

    if (!lifeSummaryTimer) {
      lifeSummaryTimer = window.setInterval(() => {
        if (state.snapshot().activePage === "chat") void loadSummary("timer");
      }, REFRESH_INTERVAL_MS);
    }
  }
};
