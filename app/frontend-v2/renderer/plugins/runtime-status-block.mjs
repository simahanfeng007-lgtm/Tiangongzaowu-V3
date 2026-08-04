function line(label, value) {
  const row = document.createElement("div");
  row.className = "rs-line";
  const labelNode = document.createElement("span");
  labelNode.className = "rs-label";
  labelNode.textContent = `${label}:`;
  const valueNode = document.createElement("span");
  valueNode.className = "rs-value";
  const display = value && typeof value === "object"
    ? Object.entries(value).slice(0, 4).map(([key, item]) => `${key}:${String(item)}`).join(" · ")
    : String(value ?? "").trim();
  valueNode.textContent = display || "\u672a\u8bfb\u53d6";
  row.append(labelNode, valueNode);
  return row;
}

function permissionLabel(payload = {}) {
  const value = String(payload.permission_label || payload.permission_mode || "");
  const labels = {
    request_approval: "\u8bf7\u6c42\u6279\u51c6",
    auto_approval: "\u66ff\u6211\u5ba1\u6279",
    full_access: "\u5b8c\u5168\u8bbf\u95ee\u6743\u9650",
    "A0-A4_AUTO_A5_SIGNED_LEASE": "A0-A4 \u81ea\u52a8\uff0cA5 \u7b7e\u540d\u79df\u7ea6",
    custom: "\u81ea\u5b9a\u4e49(config)"
  };
  return labels[value] || value || "\u672a\u914d\u7f6e";
}

function credentialLabel(value) {
  const labels = {
    configured: "已配置",
    missing: "未配置",
    unavailable: "不可用",
    unknown: "未读取"
  };
  const normalized = String(value || "unknown").trim().toLowerCase();
  return labels[normalized] || String(value || "未读取");
}

function phaseLabel(value) {
  const labels = {
    alive: "存活",
    active: "运行中",
    running: "运行中",
    idle: "空闲",
    suspended: "已暂停",
    degraded: "降级运行",
    fuhuo: "恢复中",
    unknown: "未知"
  };
  const normalized = String(value || "unknown").trim().toLowerCase();
  return labels[normalized] || String(value || "未知");
}

function driveSummary(payload = {}) {
  const roots = payload.policy?.drive_roots || payload.runtime_environment?.drive_roots || [];
  if (!Array.isArray(roots) || !roots.length) return "\u672a\u8bfb\u53d6";
  return roots.slice(0, 8).join(" ");
}

// GF 门（草案 §8）：进程就绪与行动就绪分行展示的纯函数（便于 node 测试）。
// process_ready：true→就绪 / false→未就绪 / 缺失→未读取（沿用既有风格）。
// action_ready：true→就绪 / false→未就绪 / 缺失→"未提供"——后端尚无此字段，
// 安全降级，绝不假装就绪。
export function readinessDisplay(payload = {}) {
  const process = payload?.process_ready;
  const action = payload?.action_ready;
  return {
    processLabel: process === true ? "就绪" : process === false ? "未就绪" : "未读取",
    actionLabel: action === true ? "就绪" : action === false ? "未就绪" : "未提供",
  };
}

export const runtimeStatusBlockPlugin = {
  id: "runtime-status-block",
  slot: "context",
  order: 130,
  mount({ slot, state, actions }) {
    slot.insertAdjacentHTML("beforeend", `
      <section class="side-section default-context-section">
        <div class="section-heading">
          <span>\u8fd0\u884c\u540e\u53f0</span>
          <button id="refreshStatus" class="small-command" type="button">\u5237\u65b0</button>
        </div>
        <div id="runtimeState" class="runtime-state"></div>
      </section>
    `);

    const button = slot.querySelector("#refreshStatus");
    const status = slot.querySelector("#runtimeState");

    function render(next) {
      const payload = next?.payload || {};
      const life = payload.runtime?.lifecycle || {};
      const kernel = payload.frontend_kernel || {};
      const degraded = Boolean(payload.degraded || kernel.phase === "degraded" || kernel.phase === "incompatible");
      const connection = next?.loading
        ? "刷新中"
        : !next?.ok
          ? "离线"
          : degraded
            ? "已连接（降级）"
            : "已连接";
      const lifeState = payload.life_kernel_ready === false
        ? "不可用"
        : life.available === false
          ? "未挂载"
          : "就绪";
      // GF 门：进程就绪/行动就绪分行显示；action_ready 缺字段时显示"未提供"
      const readiness = readinessDisplay(payload);
      status.innerHTML = "";
      status.append(
        line("连接", connection),
        line("前端内核", kernel.phase || "unknown"),
        line("生命内核", lifeState),
        line("进程就绪", readiness.processLabel),
        line("行动就绪", readiness.actionLabel),
        ...(payload.readiness_observed_at ? [line("就绪时点", String(payload.readiness_observed_at).replace("T", " ").slice(5, 19))] : []),
        ...(payload.readiness_source ? [line("就绪来源", payload.readiness_source)] : []),
        ...(payload.life_error ? [line("生命故障", payload.life_error)] : []),
        line("\u7aef\u53e3", payload.chat_port || "7184"),
        line("\u6a21\u578b", payload.model || "\u672a\u8bfb\u53d6"),
        line("\u6743\u9650", permissionLabel(payload)),
        line("\u5de5\u4f5c\u533a", payload.workspace || "\u672a\u8bbe\u7f6e"),
        line("\u53ef\u7528\u76d8", driveSummary(payload)),
        line("\u51ed\u636e", credentialLabel(payload.credential_state)),
        line("\u9636\u6bb5", phaseLabel(life.phase))
      );
      // P2-29: make online-update availability explicit instead of invisible;
      // the default stays fail-closed until a trust anchor is configured.
      if (typeof window.tiangongDesktop?.getUpdateStatus === "function") {
        window.tiangongDesktop.getUpdateStatus()
          .then((up) => {
            const phase = String(up?.phase || up?.status || "");
            const label = /trust|unconfigured|not_configured|idle|unknown/i.test(phase)
              ? "未配置（安全 fail-closed）"
              : (phase || "未知");
            status.append(line("在线更新", label));
          })
          .catch(() => {});
      }
    }

    button.addEventListener("click", () => actions.refreshStatus());
    state.on("runtimeStatus", render);
    render(state.snapshot().runtimeStatus);
    // 身体页左侧只保留身体摘要：运行后台区块在 body 页隐藏。
    const section = status.closest("section");
    const renderPage = (page) => {
      if (section) section.hidden = page === "body";
    };
    state.on("page", renderPage);
    renderPage(state.snapshot().activePage);
  }
};
