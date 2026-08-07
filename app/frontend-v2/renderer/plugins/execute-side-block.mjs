function row(label, value) {
  return `<div class="rs-line"><span class="rs-label">${label}:</span><span class="rs-value">${value}</span></div>`;
}

function fmt(value, fallback = "未读取") {
  return String(value ?? "").trim() || fallback;
}

export const executeSideBlockPlugin = {
  id: "execute-side-block",
  slot: "context",
  order: 114,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML(
      "beforeend",
      `
        <section id="executeSidePanel" class="side-section execute-side-panel" hidden>
          <div class="section-heading">
            <span>运行概览</span>
            <button id="executeSideRefresh" class="small-command" type="button">刷新</button>
          </div>
          <div id="executeSideRows" class="runtime-state"></div>
        </section>
      `
    );

    const panel = slot.querySelector("#executeSidePanel");
    const rowsEl = panel.querySelector("#executeSideRows");
    const refreshButton = panel.querySelector("#executeSideRefresh");

    function render(runtimeStatus) {
      const payload = runtimeStatus?.payload || {};
      const runtime = payload.runtime || {};
      const operational = runtime.operational || {};
      const scheduler = operational.scheduler || {};
      const latest = operational.latest_execution || {};
      const ok = runtimeStatus?.ok === true;
      const loading = runtimeStatus?.loading === true;
      const degraded = Boolean(payload.degraded || payload.life_kernel_ready === false);
      const connection = loading ? "刷新中" : ok ? (degraded ? "已连接 · 降级" : "已连接") : "离线";
      const taskQueue = `${Number(operational.active_task_count || 0)} / ${Number(operational.task_total || 0)}`;
      const lastStatus = String(latest.status || "COMPLETED").toUpperCase();
      const lastFailed = ["FAILED_SAFE", "FAILED", "FAILURE", "BLOCKED", "ERROR"].includes(lastStatus);
      const lastTime = String(latest.committed_at || "").replace("T", " ").slice(5, 19) || "未记录";
      rowsEl.innerHTML = [
        row("连接", connection),
        row("任务队列", `${taskQueue} 项`),
        row("完成执行", `${Number(operational.completed_execution_count || 0)} / ${Number(operational.execution_total || 0)} 条`),
        row("调度", scheduler.running ? "运行中" : "未运行"),
        row("调度心跳", `${Number(scheduler.tick_count || 0)} 次`),
        row("调度周期", scheduler.interval_seconds ? `${Number(scheduler.interval_seconds)} 秒` : "未配置"),
        row("最近执行", lastFailed ? "失败" : "完成"),
        row("执行时间", lastTime),
        row("模型", fmt(payload.model)),
        row("工作区", fmt(payload.workspace, "未设置"))
      ].join("");
    }

    refreshButton.addEventListener("click", () => actions.refreshStatus?.());
    state.on("runtimeStatus", render);
    state.on("run", () => render(state.snapshot().runtimeStatus));

    function renderPage(page) {
      panel.hidden = page !== "execute";
      if (page === "execute") actions.refreshStatus?.();
    }
    state.on("page", renderPage);
    renderPage(state.snapshot().activePage);
    render(state.snapshot().runtimeStatus);
  }
};
