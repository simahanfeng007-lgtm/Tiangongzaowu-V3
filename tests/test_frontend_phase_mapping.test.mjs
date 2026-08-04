// GF 门（草案 §8）前端协议闸门回归测试。
// 覆盖：
// 1) runPhaseFromStatus 对 AMBIGUOUS / RECONCILE_REQUIRED / PARTIAL / 未知状态的映射；
// 2) 成功判定：模型自报成功但网关裁决缺失/非 COMPLETED → 不算成功；contradiction → incident；
// 3) process_ready / action_ready 分离显示（字段缺失安全降级）。
// 运行：node tests/test_frontend_phase_mapping.test.mjs

import assert from "node:assert/strict";

import { runPhaseFromStatus, terminalSuccessVerdict } from "../app/frontend-v2/renderer/runtime/http-runtime.mjs";
import { gatewayPhaseCardModel } from "../app/frontend-v2/renderer/plugins/conversation-panel.mjs";
import { readinessDisplay } from "../app/frontend-v2/renderer/plugins/runtime-status-block.mjs";

// ── 1. runPhaseFromStatus 相位映射 ─────────────────────────────

// 歧义/待对账：必须映射为 reconcile_required，绝不再是 running
assert.equal(runPhaseFromStatus("AMBIGUOUS"), "reconcile_required");
assert.equal(runPhaseFromStatus("RECONCILE_REQUIRED"), "reconcile_required");
assert.equal(runPhaseFromStatus("ambiguous"), "reconcile_required");
// 网关旁路信号同样触发对账相位（投影 needs_reconciliation）
assert.equal(runPhaseFromStatus("RUNNING", { needsReconciliation: true }), "reconcile_required");

// 部分完成：独立相位 partial，不算成功也不算失败
assert.equal(runPhaseFromStatus("PARTIAL"), "partial");

// 矛盾：优先于一切状态，映射为非成功 incident
assert.equal(runPhaseFromStatus("CONTRADICTION"), "incident");
assert.equal(runPhaseFromStatus("COMPLETED", { contradiction: true }), "incident");
assert.equal(runPhaseFromStatus("RUNNING", { contradiction: true }), "incident");

// 权威成功/失败/取消/等待态保持原语义
assert.equal(runPhaseFromStatus("COMPLETED"), "finished");
assert.equal(runPhaseFromStatus("SUCCEEDED"), "finished");
assert.equal(runPhaseFromStatus("FAILED"), "failed");
assert.equal(runPhaseFromStatus("FAILED_SAFE"), "failed");
assert.equal(runPhaseFromStatus("CANCELLED"), "cancelled");
assert.equal(runPhaseFromStatus("SUPERSEDED"), "cancelled");
assert.equal(runPhaseFromStatus("WAITING_FOR_USER"), "awaiting_user");

// 已知进行中状态仍映射为 running
assert.equal(runPhaseFromStatus("RUNNING"), "running");
assert.equal(runPhaseFromStatus("EXECUTING"), "running");
assert.equal(runPhaseFromStatus("QUEUED"), "running");
assert.equal(runPhaseFromStatus("DELIVERING"), "running");
assert.equal(runPhaseFromStatus("IN_PROGRESS"), "running");
// D-20：session_queue 已激活、聚合机未落第一帧的过渡态必须是 running（启动竞态误卡修复）
assert.equal(runPhaseFromStatus("ACTIVE"), "running");

// 未知状态：安全映射为非成功 unknown，绝不落入 running 的假安心
assert.equal(runPhaseFromStatus("WHATEVER_NEW"), "unknown");
assert.equal(runPhaseFromStatus(""), "unknown");
assert.equal(runPhaseFromStatus(undefined), "unknown");
assert.equal(runPhaseFromStatus(null), "unknown");

// ── 2. 成功判定（terminalSuccessVerdict）──────────────────────

// 网关投影裁决 completed + 有回复 → 唯一成功形态之一
assert.deepEqual(
  terminalSuccessVerdict({
    reply: "任务完成",
    run: { status: "COMPLETED" },
    status: { gateway_projection: { overall_phase: "completed", needs_reconciliation: false } },
  }),
  { ok: true, phase: "finished" },
);

// 无投影时：run 终态 SUCCEEDED + 无矛盾/无对账 → 成功
assert.deepEqual(
  terminalSuccessVerdict({ reply: "任务完成", run: { status: "SUCCEEDED" }, status: {} }),
  { ok: true, phase: "finished" },
);

// 模型文本自报成功（reply 里写满"任务已完成"），但网关裁决缺失且 run 非终态 → 不算成功
assert.deepEqual(
  terminalSuccessVerdict({
    reply: "任务已完成，全部验收通过。",
    run: { status: "EXECUTING" },
    status: {},
  }),
  { ok: false, phase: "unknown" },
);

// 模型自报成功 + 网关投影裁决 PARTIAL → 不算成功，相位 partial
assert.deepEqual(
  terminalSuccessVerdict({
    reply: "任务已完成。",
    run: { status: "COMPLETED" },
    status: { gateway_projection: { overall_phase: "partial", needs_reconciliation: false } },
  }),
  { ok: false, phase: "partial" },
);

// 模型自报成功 + 网关投影需要对账 → 不算成功，相位 reconcile_required
assert.deepEqual(
  terminalSuccessVerdict({
    reply: "任务已完成。",
    run: { status: "COMPLETED" },
    status: { gateway_projection: { overall_phase: "reconcile_required", needs_reconciliation: true } },
  }),
  { ok: false, phase: "reconcile_required" },
);

// 车道状态 AMBIGUOUS 同样阻断成功（即使 run 自报 COMPLETED）
assert.deepEqual(
  terminalSuccessVerdict({
    reply: "任务已完成。",
    run: { status: "COMPLETED" },
    status: {
      gateway_projection: {
        overall_phase: "completed",
        needs_reconciliation: false,
        delivery: { state: "AMBIGUOUS" },
      },
    },
  }),
  { ok: false, phase: "reconcile_required" },
);

// contradiction：run 与投影都说完成也没用，一律 incident 非成功
assert.deepEqual(
  terminalSuccessVerdict({
    reply: "任务已完成。",
    run: { status: "COMPLETED", contradiction: true },
    status: { gateway_projection: { overall_phase: "completed", needs_reconciliation: false } },
  }),
  { ok: false, phase: "incident" },
);

// 网关裁决 FAILED → 非成功
assert.equal(
  terminalSuccessVerdict({
    reply: "任务已完成。",
    run: { status: "COMPLETED" },
    status: { gateway_projection: { overall_phase: "failed", needs_reconciliation: false } },
  }).ok,
  false,
);

// 成功还必须真的有回复文本：空回复即使裁决 COMPLETED 也不算成功
assert.equal(
  terminalSuccessVerdict({
    reply: "",
    run: { status: "COMPLETED" },
    status: { gateway_projection: { overall_phase: "completed", needs_reconciliation: false } },
  }).ok,
  false,
);

// 投影存在但聚合相位仍是进行中：按未成功处理，不猜
assert.deepEqual(
  terminalSuccessVerdict({
    reply: "任务已完成。",
    run: { status: "COMPLETED" },
    status: { gateway_projection: { overall_phase: "executing", needs_reconciliation: false } },
  }),
  { ok: false, phase: "unknown" },
);

// ── 3. 非成功相位卡片模型（系统裁决提示，非模型发言）───────────

const reconcileCard = gatewayPhaseCardModel("reconcile_required");
assert.equal(reconcileCard.phase, "reconcile_required");
assert.equal(reconcileCard.title, "结果待对账");
assert.ok(reconcileCard.lines.some((line) => /禁止重试或重发/.test(line)));
// reconcile_required 卡片不携带任何动作入口（无按钮/回调字段）
assert.deepEqual(Object.keys(reconcileCard).sort(), ["lines", "phase", "title", "tone"]);

assert.equal(gatewayPhaseCardModel("partial").title, "部分完成");
assert.ok(gatewayPhaseCardModel("partial").lines.some((line) => /不得当作成功/.test(line)));
assert.equal(gatewayPhaseCardModel("incident").title, "结果矛盾");
assert.equal(gatewayPhaseCardModel("unknown").title, "状态未知");
// 成功/进行中等相位不渲染卡片
assert.equal(gatewayPhaseCardModel("finished"), null);
assert.equal(gatewayPhaseCardModel("running"), null);
assert.equal(gatewayPhaseCardModel(""), null);

// ── 4. process_ready / action_ready 分离显示（缺字段安全降级）───

// 后端尚未提供 action_ready：显示"未提供"，绝不假装就绪
assert.deepEqual(readinessDisplay({}), { processLabel: "未读取", actionLabel: "未提供" });
assert.deepEqual(
  readinessDisplay({ process_ready: true }),
  { processLabel: "就绪", actionLabel: "未提供" },
);
assert.deepEqual(
  readinessDisplay({ process_ready: true, action_ready: true }),
  { processLabel: "就绪", actionLabel: "就绪" },
);
assert.deepEqual(
  readinessDisplay({ process_ready: false, action_ready: false }),
  { processLabel: "未就绪", actionLabel: "未就绪" },
);
// undefined / null 与缺失同义
assert.deepEqual(
  readinessDisplay({ process_ready: null, action_ready: null }),
  { processLabel: "未读取", actionLabel: "未提供" },
);

console.log("frontend-phase-mapping: PASS");
