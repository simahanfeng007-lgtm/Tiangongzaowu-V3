import assert from "node:assert/strict";

import { createActions } from "../app/frontend-v2/renderer/core/actions.mjs";
import { createState } from "../app/frontend-v2/renderer/core/state.mjs";
import { progressDisplayText } from "../app/frontend-v2/renderer/plugins/conversation-panel.mjs";

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

function waitUntil(predicate, timeoutMs = 1500) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => {
      if (predicate()) {
        resolve();
        return;
      }
      if (Date.now() - started >= timeoutMs) {
        reject(new Error("waitUntil timeout"));
        return;
      }
      setTimeout(poll, 5);
    };
    poll();
  });
}

const visibleProgress = {
  phase: "running",
  ok: null,
  requestId: "req_visibility",
  steps: [
    {
      id: "tool_visible",
      title: "读取文件",
      status: "done",
      summary: "读取完成",
      toolName: "file.read",
      meta: { type: "TOOL_FINISHED" },
    },
    {
      id: "repeat_guard_internal",
      title: "内部重复诊断",
      status: "failed",
      summary: "不应上屏的完整诊断",
      meta: { visibility: "internal" },
    },
  ],
};
assert.equal(progressDisplayText(visibleProgress), "调用工具：file.read");

const filteringState = createState();
const filteringSession = filteringState.snapshot().activeSessionId;
filteringState.startRunProgress(filteringSession, "req_filter");
filteringState.applyRunProgress(filteringSession, {
  requestId: "req_filter",
  id: "internal_only",
  title: "内部步骤",
  status: "failed",
  summary: "内部诊断",
  meta: { visibility: "internal" },
});
assert.equal(
  filteringState.snapshot().runProgress.steps.some((step) => step.id === "internal_only"),
  false,
);

const sent = [];
const guided = [];
let failConversationAudit = false;
const runtime = {
  async send(payload) {
    sent.push(payload);
    return {
      ok: true,
      stdout: JSON.stringify({
        huifu: "队列任务完成",
        simple_chain_status: "complete",
      }),
      stderr: "",
      simple_chain_status: "complete",
    };
  },
  async guide(payload) {
    guided.push(payload);
    return { ok: true };
  },
  async cancel() {
    return { ok: true, interrupted: true };
  },
  async conversationEvents() {
    if (failConversationAudit) throw new Error("audit route unavailable");
    return { ok: true };
  },
};

const queueState = createState();
const actions = createActions({ runtime, state: queueState });
const sessionA = queueState.snapshot().activeSessionId;
queueState.startRunProgress(sessionA, "req_a");
queueState.setBusy(sessionA, true);

const queuedA = await actions.handleRunInput("A 会话的下一任务");
assert.equal(queuedA.queued, true);
assert.equal(queuedA.position, 1);

const guideResult = await actions.handleRunInput("纠偏：保持当前目标");
assert.equal(guideResult.guided, true);
assert.equal(guideResult.ok, true);
assert.equal(guided.length, 1);
assert.equal(guided[0].message, "保持当前目标");

await actions.startNewConversation();
const sessionB = queueState.snapshot().activeSessionId;
assert.notEqual(sessionB, sessionA);
queueState.startRunProgress(sessionB, "req_b");
queueState.setBusy(sessionB, true);
const queuedB = await actions.handleRunInput("B 会话的下一任务");
assert.equal(queuedB.queued, true);

queueState.setBusy(sessionB, false);
actions.switchConversation(sessionB);
await waitUntil(() => sent.length === 1);
assert.match(sent[0].message, /B 会话的下一任务/);

// The inactive A queue must not block B, and it must remain FIFO-pending until
// A becomes active again.
actions.switchConversation(sessionA);
await waitUntil(() => sent.length === 2);
assert.match(sent[1].message, /A 会话的下一任务/);
assert.equal(
  queueState.snapshot().messages.filter((item) => item.content === "A 会话的下一任务").length,
  1,
);

failConversationAudit = true;
const clearResult = await actions.clearConversation();
assert.equal(clearResult.ok, true);
assert.equal(clearResult.localOnly, true);
assert.equal(queueState.snapshot().messages.length, 0);

console.log("frontend-control-plane: PASS");
