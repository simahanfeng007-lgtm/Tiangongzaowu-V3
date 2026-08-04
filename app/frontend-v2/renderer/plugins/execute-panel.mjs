const TASK_MODE_NAMES = {
  auto: "自动",
  chat: "聊天",
  work: "工作"
};

const PHASE_NAMES = {
  alive: "存活",
  suspended: "已暂停",
  unknown: "未知",
  chenshui: "沉睡",
  fuhuo: "复活",
  fuzhu: "辅助",
  banzizhu: "半自主",
  zizhu: "自主",
  guancha: "观察",
  pinggu: "评估",
  shiyan: "实验",
  gaijin: "改进"
};

const EMOTION_NAMES = {
  calm: "平静",
  joy: "喜悦",
  worry: "担忧",
  thoughtfulness: "思考",
  surprise: "惊讶",
  anger: "生气",
  sadness: "低落",
  fear: "警觉"
};

const AVAILABILITY_REASON_NAMES = {
  CANONICAL_METRICS_NOT_DEFINED: "权威生命事务未定义合成成长或生命力分数",
  AFFECTIVE_PROJECTION_UNAVAILABLE: "当前后端未挂载权威情感投影",
  FREE_WILL_SCHEDULER_UNAVAILABLE: "当前后端未挂载权威自由意志调度器",
  "canonical life journal does not define synthetic growth or vitality scores": "权威生命事务未定义合成成长或生命力分数",
  "no authoritative affective projection is mounted in this backend build": "当前后端未挂载权威情感投影",
  "no authoritative free-will scheduler is mounted in this backend build": "当前后端未挂载权威自由意志调度器"
};

function availabilityReason(code, reason, fallback) {
  return AVAILABILITY_REASON_NAMES[String(code || "")]
    || AVAILABILITY_REASON_NAMES[String(reason || "")]
    || String(reason || fallback || "暂不可用");
}

function credentialState(value) {
  const labels = { configured: "已配置", missing: "未配置", unavailable: "不可用", unknown: "未读取" };
  const normalized = String(value || "unknown").trim().toLowerCase();
  return labels[normalized] || String(value || "未读取");
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmt(value, fallback = "未读取") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function friendly(value, names, fallback = "未读取") {
  const text = fmt(value, fallback);
  return names[text] || text;
}

function percent(value, max = 1) {
  const number = Number(value);
  const base = Number(max) || 1;
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, Math.round((number / base) * 100)));
}

function percentText(value) {
  if (value === null || typeof value === "undefined" || value === "") return "—";
  return `${percent(value)}%`;
}

function executionTime(record = {}) {
  const epoch = Number(record.completed_at_ms || 0);
  const value = epoch > 0 ? new Date(epoch) : new Date(String(record.committed_at || ""));
  if (!Number.isFinite(value.getTime())) return "未记录";
  return value.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });
}

function compactId(value) {
  const text = String(value || "").trim();
  if (!text) return "未记录";
  return text.length > 24 ? `${text.slice(0, 12)}…${text.slice(-8)}` : text;
}

function row(label, value) {
  return `<div class="kv-row"><span class="kv-key">${esc(label)}</span><span class="kv-value">${esc(value)}</span></div>`;
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

export const executePanelPlugin = {
  id: "execute-panel",
  slot: "conversation",
  order: 210,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML("beforeend", `
      <section class="page-panel execute-page" data-page-panel="execute">
        <header class="page-header">
          <div class="title-group">
            <span class="caption">运行</span>
            <h2>后台状态</h2>
          </div>
          <div class="commandbar-meta">
            <span id="executeStatusPill" class="run-pill">待连接</span>
            <button id="executeRefresh" class="small-command" type="button">刷新</button>
          </div>
        </header>

        <section class="page-body execute-body">
          <div class="dashboard-grid">
            <section class="panel-card">
              <div class="panel-title"><span>后台桥接</span><span id="backendHealth" class="mini-pill">未读取</span></div>
              <div id="backendStatusRows" class="kv-list"></div>
            </section>

            <section class="panel-card">
              <div class="panel-title"><span>模型接入</span><span id="modelHealth" class="mini-pill">未读取</span></div>
              <div id="modelStatusRows" class="kv-list"></div>
            </section>

            <section class="panel-card">
              <div class="panel-title"><span>生命运行</span><span id="bodyHealth" class="mini-pill">状态</span></div>
              <div id="bodyStatusRows" class="kv-list"></div>
            </section>

            <section class="panel-card">
              <div class="panel-title"><span>最近执行终态</span><span id="lastRunPill" class="mini-pill">空闲</span></div>
              <div id="lastRunRows" class="kv-list"></div>
            </section>
          </div>

          <section class="panel-card wide-card">
            <div class="panel-title"><span>运行指标</span><span class="mini-pill">权威投影</span></div>
            <div id="executeMetrics" class="dash-metrics"></div>
          </section>

          <section class="panel-card wide-card">
            <div class="panel-title"><span>执行证据</span><span class="mini-pill">最近终态</span></div>
            <pre id="executeOutput" class="compact-pre"></pre>
          </section>
        </section>
      </section>
    `);

    const panel = slot.querySelector('[data-page-panel="execute"]');
    const statusPill = panel.querySelector("#executeStatusPill");
    const backendHealth = panel.querySelector("#backendHealth");
    const backendRows = panel.querySelector("#backendStatusRows");
    const modelHealth = panel.querySelector("#modelHealth");
    const modelRows = panel.querySelector("#modelStatusRows");
    const bodyHealth = panel.querySelector("#bodyHealth");
    const bodyRows = panel.querySelector("#bodyStatusRows");
    const lastRunPill = panel.querySelector("#lastRunPill");
    const lastRunRows = panel.querySelector("#lastRunRows");
    const metrics = panel.querySelector("#executeMetrics");
    const output = panel.querySelector("#executeOutput");
    const refresh = panel.querySelector("#executeRefresh");

    function renderPage(page) {
      panel.classList.toggle("active", page === "execute");
      if (page === "execute") actions.refreshStatus?.();
    }

    function renderStatus(runtimeStatus) {
      const payload = runtimeStatus?.payload || {};
      const runtime = payload.runtime || {};
      const life = runtime.lifecycle || {};
      const operational = runtime.operational || {};
      const scheduler = operational.scheduler || {};
      const qinggan = runtime.qinggan || {};
      const jiyi = runtime.body?.jiyi_tongji || {};
      const ok = runtimeStatus?.ok === true;
      const loading = runtimeStatus?.loading === true;

      const degraded = Boolean(payload.degraded || payload.life_kernel_ready === false);
      statusPill.textContent = loading ? "刷新中" : ok ? (degraded ? "已连接 · 生命链降级" : "已连接") : "离线";
      statusPill.className = `run-pill ${loading ? "running" : ok && !degraded ? "ok" : ok ? "warn" : "failed"}`;
      backendHealth.textContent = loading ? "刷新中" : ok ? (degraded ? "降级" : "可用") : "不可用";
      backendHealth.className = `mini-pill ${ok && !degraded ? "ok" : loading ? "" : ok ? "warn" : "failed"}`;
      modelHealth.textContent = payload.credential_state === "configured" ? "密钥已配置" : "待配置";
      modelHealth.className = `mini-pill ${payload.credential_state === "configured" ? "ok" : "warn"}`;
      bodyHealth.textContent = life.available === false ? "生命投影不可用" : friendly(life.phase, PHASE_NAMES, "未知");
      bodyHealth.className = `mini-pill ${life.available === false ? "warn" : "ok"}`;

      backendRows.innerHTML = [
        row("服务", fmt(payload.service, "tiangong-v3-qiyuan")),
        row("端口", fmt(payload.chat_port, "7184")),
        row("接口", payload.endpoint_state === "ready" ? "就绪" : "未确认"),
        row("工作区", fmt(payload.workspace, "未设置")),
        row("内核", payload.kernel_importable ? "已挂载" : "未连接")
      ].join("");
      modelRows.innerHTML = [
        row("服务商", fmt(payload.provider, "未读取")),
        row("模型", fmt(payload.model, "未读取")),
        row("地址", fmt(payload.base_url, "后台默认")),
        row("凭据", credentialState(payload.credential_state))
      ].join("");
      bodyRows.innerHTML = [
        row("生命周期", friendly(life.phase, PHASE_NAMES, "未知")),
        row("自主调度", scheduler.running ? "运行中" : "未运行"),
        row("任务队列", `${Number(operational.active_task_count || 0)} / ${Number(operational.task_total || 0)}`),
        row("主情绪", qinggan.available === false ? "情感投影未挂载" : friendly(qinggan.dominant_emotion, EMOTION_NAMES, "未读取")),
        row("记忆数", Number(jiyi.zongshu || 0))
      ].join("");
      metrics.innerHTML = [
        metric("记忆记录", Number(operational.memory_total ?? jiyi.zongshu ?? 0), "当前生命权威记忆"),
        metric("待处理任务", Number(operational.active_task_count || 0), `共 ${Number(operational.task_total || 0)} 项任务`),
        metric("完成执行", Number(operational.completed_execution_count || 0), `执行账本共 ${Number(operational.execution_total || 0)} 条`),
        metric("调度心跳", Number(scheduler.tick_count || 0), "本次后台启动"),
        metric("调度周期", scheduler.interval_seconds ? `${Number(scheduler.interval_seconds)} 秒` : "未配置", scheduler.last_error_type ? `最近错误 ${scheduler.last_error_type}` : "单一生命调度器")
      ].join("");
      renderRun(state.snapshot().lastRun, operational);
    }

    function renderRun(run = {}, operational = {}) {
      const latest = operational.latest_execution || {};
      const hasPersistedExecution = Boolean(latest.request_id || latest.run_id || latest.committed_at);
      const hasLocalRun = run.phase === "running"
        || Boolean(run.stdout || run.stderr)
        || (run.phase && run.phase !== "idle");
      if (!hasLocalRun && hasPersistedExecution) {
        const terminalStatus = String(latest.status || "COMPLETED").toUpperCase();
        const failedTerminal = ["FAILED_SAFE", "FAILED", "FAILURE", "BLOCKED", "ERROR"].includes(terminalStatus);
        lastRunPill.textContent = failedTerminal ? "失败" : "已记录";
        lastRunPill.className = `mini-pill ${failedTerminal ? "failed" : "ok"}`;
        lastRunRows.innerHTML = [
          row("状态", failedTerminal ? "执行失败" : "执行完成"),
          row("完成时间", executionTime(latest)),
          row("请求", compactId(latest.request_id)),
          row("运行", compactId(latest.run_id))
        ].join("");
        output.textContent = [
          `状态: ${terminalStatus}`,
          `请求: ${latest.request_id || "未记录"}`,
          `运行: ${latest.run_id || "未记录"}`,
          `事实证据: ${Array.isArray(latest.fact_ids) ? latest.fact_ids.length : 0} 条`,
          `提交哈希: ${latest.commit_sha256 || "未记录"}`
        ].join("\n");
        return;
      }
      const running = run.phase === "running";
      const failed = run.ok === false;
      lastRunPill.textContent = running ? "运行中" : failed ? "失败" : run.phase === "idle" ? "空闲" : "完成";
      lastRunPill.className = `mini-pill ${running ? "" : failed ? "failed" : run.phase === "idle" ? "" : "ok"}`;
      lastRunRows.innerHTML = [
        row("状态", lastRunPill.textContent),
        row("模式", friendly(run.mode, TASK_MODE_NAMES, "聊天")),
        row("返回", failed ? "异常" : "正常")
      ].join("");
      output.textContent = run.stderr || run.stdout || (hasPersistedExecution ? "执行终态已写入生命账本。" : "暂无执行记录。");
    }

    refresh.addEventListener("click", () => actions.refreshStatus?.());

    state.on("page", renderPage);
    state.on("runtimeStatus", renderStatus);
    state.on("run", (run) => renderRun(run, state.snapshot().runtimeStatus?.payload?.runtime?.operational || {}));

    const snap = state.snapshot();
    renderPage(snap.activePage);
    renderStatus(snap.runtimeStatus);
    renderRun(snap.lastRun, snap.runtimeStatus?.payload?.runtime?.operational || {});
  }
};
