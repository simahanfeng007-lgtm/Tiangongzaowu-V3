// 终局消息唯一化回归测试：同一轮任务只允许出现一条 assistant 收尾。
// 覆盖：
//  - 终局事件先到（force_stopped + 自然回复）→ 填充占位消息，流式路径不再重复/标红
//  - 流式路径先完成 → 终局事件后到 → 不再追加 final-* 重复消息
//  - 无自然回复 + 执行失败 → 允许红色兜底错误（单条）
//  - 终局事件交付自然回复 + 流式路径只有错误 → 只保留黑色自然回复（红+黑回归）
import assert from "node:assert/strict";
import { createActions } from "../app/frontend-v2/renderer/core/actions.mjs";
import { createState } from "../app/frontend-v2/renderer/core/state.mjs";

// ── Node 测试环境补齐（localStorage / window / CustomEvent）─────────────
const storage = new Map();
globalThis.localStorage = {
  getItem(key) {
    return storage.has(String(key)) ? storage.get(String(key)) : null;
  },
  setItem(key, value) {
    storage.set(String(key), String(value));
  },
  removeItem(key) {
    storage.delete(String(key));
  },
};
if (!globalThis.CustomEvent) {
  globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
      this.type = String(type || "");
      this.detail = options.detail;
    }
  };
}
if (!globalThis.window) {
  const target = new EventTarget();
  target.setTimeout = (fn, ms) => setTimeout(fn, ms);
  target.clearTimeout = (timer) => clearTimeout(timer);
  globalThis.window = target;
}

const NATURAL_A = "任务被迫停在这里了，我直说一下情况：环境探测已完成。";
const NATURAL_B = "任务已完成，《测试文件.md》已落到工作区。";
const NATURAL_D = "这一步没跑通，但我会把真实情况讲清楚：浏览器通道不可用。";
const GATEWAY_A = "req_gateway_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const GATEWAY_B = "req_gateway_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

const state = createState();
const sentPayloads = [];
let sendStreamBehavior = null;

const runtime = {
  async send() {
    return { ok: true, stdout: "", stderr: "" };
  },
  async sendStream(payload) {
    sentPayloads.push(payload);
    if (typeof sendStreamBehavior !== "function") {
      return { ok: true, stdout: "", stderr: "" };
    }
    return sendStreamBehavior(payload);
  },
  async status() {
    return { ok: true, stdout: "状态：正常" };
  },
};

const actions = createActions({ runtime, state });

function assistantMessages() {
  return state.snapshot().messages.filter((item) => item.role === "assistant");
}

function dispatchTerminal(payload) {
  window.dispatchEvent(new CustomEvent("tiangong-terminal-run", { detail: payload }));
}

// ── 场景 A：终局事件先到（force_stopped + 自然回复）─────────────────────
sendStreamBehavior = (payload) => {
  dispatchTerminal({
    run: {
      final_response: NATURAL_A,
      simple_chain_status: "force_stopped",
      terminal: { status: "force_stopped", reason: "测试" },
      generated_attachments: [],
    },
    requestId: payload.requestId,
    gatewayRequestId: GATEWAY_A,
    presentationRequestId: payload.requestId,
    sessionId: payload.sessionId,
  });
  return {
    ok: false,
    phase: "force_stopped",
    stdout: JSON.stringify({ huifu: NATURAL_A, simple_chain_status: "force_stopped" }),
    stderr: "执行链未完成",
    simple_chain_status: "force_stopped",
    attachments: [],
    generated_attachments: [],
  };
};
await actions.sendMessage("测试强制停止自然回复", [], { mode: "work", useStream: true });
{
  const assistants = assistantMessages();
  assert.equal(assistants.length, 1, "终局事件先到时只能有一条 assistant 收尾");
  assert.equal(assistants[0].content, NATURAL_A);
  assert.equal(assistants[0].error, false, "有自然回复时不得标红");
  assert.equal(assistants[0].meta?.runId, GATEWAY_A, "终局消息应绑定网关 runId");
  assert.doesNotMatch(assistants[0].id, /^final-/, "有占位消息时必须填充占位，不再新增 final-*");
}

// ── 场景 B：流式路径先完成，终局事件后到 → 不追加重复消息 ─────────────
state.clearMessages();
const runBRequestId = { current: "" };
sendStreamBehavior = (payload) => {
  runBRequestId.current = payload.requestId;
  return {
    ok: true,
    phase: "finished",
    stdout: JSON.stringify({ huifu: NATURAL_B, simple_chain_status: "complete" }),
    stderr: "",
    simple_chain_status: "complete",
    attachments: [],
    generated_attachments: [],
  };
};
await actions.sendMessage("测试终局事件后到", [], { mode: "work", useStream: true });
{
  const beforeLate = assistantMessages();
  assert.equal(beforeLate.length, 1, "流式路径完成后只有一条 assistant 收尾");
  assert.equal(beforeLate[0].content, NATURAL_B);
  assert.equal(beforeLate[0].error, false);
}
dispatchTerminal({
  run: {
    final_response: NATURAL_B,
    simple_chain_status: "complete",
    terminal: { status: "completed" },
    generated_attachments: [],
  },
  requestId: runBRequestId.current,
  gatewayRequestId: GATEWAY_B,
  presentationRequestId: runBRequestId.current,
  sessionId: state.snapshot().activeSessionId,
});
{
  const afterLate = assistantMessages();
  assert.equal(afterLate.length, 1, "迟到的终局事件不得追加重复消息");
  assert.equal(afterLate[0].content, NATURAL_B);
}

// ── 场景 C：无自然回复 + 执行失败 → 单条红色兜底 ───────────────────────
state.clearMessages();
sendStreamBehavior = () => ({
  ok: false,
  phase: "failed",
  stdout: "",
  stderr: "网关执行未成功完成\n处理建议：请按错误码检查运行日志\n错误码：gatewayrequestfailed",
  attachments: [],
  generated_attachments: [],
});
await actions.sendMessage("测试纯失败兜底", [], { mode: "work", useStream: true });
{
  const assistants = assistantMessages();
  assert.equal(assistants.length, 1, "纯失败场景也只能有一条 assistant 收尾");
  assert.equal(assistants[0].error, true, "没有自然回复时才允许红色兜底");
  assert.match(assistants[0].content, /后端执行失败|错误码/);
}

// ── 场景 D：终局事件交付自然回复 + 流式路径只有错误 → 只保留黑色 ───────
state.clearMessages();
sendStreamBehavior = (payload) => {
  dispatchTerminal({
    run: {
      final_response: NATURAL_D,
      simple_chain_status: "force_stopped",
      terminal: { status: "force_stopped", reason: "测试" },
      generated_attachments: [],
    },
    requestId: payload.requestId,
    gatewayRequestId: GATEWAY_A,
    presentationRequestId: payload.requestId,
    sessionId: payload.sessionId,
  });
  return {
    ok: false,
    phase: "failed",
    stdout: "",
    stderr: "网关执行未成功完成\n处理建议：请按错误码检查运行日志\n错误码：gatewayrequestfailed",
    attachments: [],
    generated_attachments: [],
  };
};
await actions.sendMessage("测试红黑并存回归", [], { mode: "work", useStream: true });
{
  const assistants = assistantMessages();
  assert.equal(assistants.length, 1, "红黑并存时必须只保留一条（黑）");
  assert.equal(assistants[0].content, NATURAL_D);
  assert.equal(assistants[0].error, false, "有自然回复时红色错误不得覆盖");
  assert.doesNotMatch(assistants[0].content, /后端执行失败/);
}

console.log("frontend-terminal-message-dedupe: PASS");
