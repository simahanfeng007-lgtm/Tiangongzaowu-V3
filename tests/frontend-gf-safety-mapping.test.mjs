// D-13 / GF 门：前端安全映射回归测试（node 内嵌断言风格，同 frontend-*.test.mjs）。
// 覆盖：
//  - runPhaseFromStatus：AMBIGUOUS/RECONCILE_REQUIRED/PARTIAL 不再归入 running；
//    未知状态归入 unknown 而非 running；矛盾优先于一切。
//  - terminalSuccessVerdict：成功只认 CompletionGate/机器终态裁决字段；
//    模型自报 zhuangtai 文本不构成成功；无裁决字段 → 非成功（未知/待对账）。
//  - progressDisplayText：新相位独立文案（需对账/部分完成/矛盾/未知），绝不显示"完成"。
import assert from "node:assert/strict";
import {
  runPhaseFromStatus,
  terminalSuccessVerdict,
} from "../app/frontend-v2/renderer/runtime/http-runtime.mjs";
import { chainStatusLabel, progressDisplayText } from "../app/frontend-v2/renderer/plugins/conversation-panel.mjs";

// 状态条标签：force_stopped/interrupted 必须优先读 simple_chain_status，
// 不能因网关把终态压成 FAILED 而显示成“执行失败”。
assert.equal(chainStatusLabel("force_stopped", "failed"), "已强制停止");
assert.equal(chainStatusLabel("interrupted", "finished"), "已中断");
assert.equal(chainStatusLabel("incomplete", "failed"), "未完成");
assert.equal(chainStatusLabel("", "failed"), "执行失败");

// ── 相位映射：安全状态不得落入 running ─────────────────────────────
// force_stopped：对抗审查 P1-9 修复——强制停止必须独立映射，不得落到 unknown。
assert.equal(runPhaseFromStatus("FORCE_STOPPED"), "force_stopped");
assert.equal(runPhaseFromStatus("force_stopped"), "force_stopped");
assert.notEqual(runPhaseFromStatus("FORCE_STOPPED"), "unknown");

assert.equal(runPhaseFromStatus("AMBIGUOUS"), "reconcile_required");
assert.equal(runPhaseFromStatus("RECONCILE_REQUIRED"), "reconcile_required");
assert.equal(runPhaseFromStatus("PARTIAL"), "partial");
assert.equal(runPhaseFromStatus("CONTRADICTION"), "incident");
assert.equal(runPhaseFromStatus("COMPLETED"), "finished");
assert.equal(runPhaseFromStatus("RUNNING"), "running");
assert.equal(runPhaseFromStatus("FAILED"), "failed");
assert.equal(runPhaseFromStatus("CANCELLED"), "cancelled");
// 未知/缺失状态：绝不归入 running（缺陷核心）
assert.equal(runPhaseFromStatus("SOMETHING_NEW"), "unknown");
assert.equal(runPhaseFromStatus(""), "unknown");
assert.equal(runPhaseFromStatus(undefined), "unknown");
// 旁路信号：网关标记对账/矛盾时，即使状态文本正常也不显示成功相位
assert.equal(runPhaseFromStatus("COMPLETED", { needsReconciliation: true }), "reconcile_required");
assert.equal(runPhaseFromStatus("COMPLETED", { contradiction: true }), "incident");
assert.equal(runPhaseFromStatus("RUNNING", { needsReconciliation: true }), "reconcile_required");

// ── 终态成功判定：只认机器裁决 ─────────────────────────────────────
// 机器终态 COMPLETED + 有回复 → 成功
assert.deepEqual(
  terminalSuccessVerdict({ reply: "已完成", run: { status: "COMPLETED" } }),
  { ok: true, phase: "finished" },
);
// 无回复 → 非成功（没有可交付文本）
assert.deepEqual(
  terminalSuccessVerdict({ reply: "", run: { status: "COMPLETED" } }),
  { ok: false, phase: "finished" },
);
// 模型自报 zhuangtai=wancheng 但无机器终态 → 非成功（缺陷核心：文本不能伪造成功）
const forged = terminalSuccessVerdict({ reply: JSON.stringify({ zhuangtai: "wancheng", reply: "搞定了" }), run: {} });
assert.equal(forged.ok, false);
assert.equal(forged.phase, "unknown");
// PARTIAL / AMBIGUOUS / RECONCILE_REQUIRED → 非成功且相位独立
assert.deepEqual(
  terminalSuccessVerdict({ reply: "x", run: { status: "PARTIAL" } }),
  { ok: false, phase: "partial" },
);
assert.deepEqual(
  terminalSuccessVerdict({ reply: "x", run: { status: "AMBIGUOUS" } }),
  { ok: false, phase: "reconcile_required" },
);
assert.deepEqual(
  terminalSuccessVerdict({ reply: "x", run: { status: "RECONCILE_REQUIRED" } }),
  { ok: false, phase: "reconcile_required" },
);
// 网关投影是 CompletionGate 裁决载体：投影 completed → 成功
assert.deepEqual(
  terminalSuccessVerdict({
    reply: "已完成",
    run: { status: "COMPLETED" },
    status: { gateway_projection: { overall_phase: "completed" } },
  }),
  { ok: true, phase: "finished" },
);
// 投影待对账：即便 run 自报 COMPLETED 也非成功（对账优先）
assert.deepEqual(
  terminalSuccessVerdict({
    reply: "已完成",
    run: { status: "COMPLETED" },
    status: { gateway_projection: { overall_phase: "reconcile_required" } },
  }),
  { ok: false, phase: "reconcile_required" },
);
// 投影存在但未给出完成态：按未成功处理，不猜
assert.deepEqual(
  terminalSuccessVerdict({
    reply: "已完成",
    run: { status: "COMPLETED" },
    status: { gateway_projection: { overall_phase: "in_progress" } },
  }),
  { ok: false, phase: "unknown" },
);
// 矛盾标记：一律非成功 incident
assert.equal(
  terminalSuccessVerdict({ reply: "x", run: { status: "COMPLETED", contradiction: true } }).phase,
  "incident",
);
// 车道级歧义（任一车道 AMBIGUOUS）→ 待对账，非成功
assert.equal(
  terminalSuccessVerdict({
    reply: "x",
    run: { status: "COMPLETED" },
    status: { gateway_projection: { delivery: { state: "AMBIGUOUS" } } },
  }).ok,
  false,
);

// ── 面板文案：新相位独立显示，绝不显示"完成" ──────────────────────
assert.equal(progressDisplayText({ phase: "reconcile_required" }), "结果待对账，禁止重试");
assert.equal(progressDisplayText({ phase: "partial" }), "部分完成");
assert.equal(progressDisplayText({ phase: "incident" }), "结果矛盾，按非成功处理");
assert.equal(progressDisplayText({ phase: "unknown" }), "状态未知，按未成功处理");
assert.equal(progressDisplayText({ phase: "finished" }), "完成");
assert.notEqual(progressDisplayText({ phase: "partial", ok: true }), "完成");
assert.notEqual(progressDisplayText({ phase: "reconcile_required", ok: true }), "完成");

console.log("frontend-gf-safety-mapping: all assertions passed");
