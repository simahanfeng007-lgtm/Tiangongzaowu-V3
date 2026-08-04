import test from "node:test";
import assert from "node:assert/strict";

import { deliverProactiveChats } from "../app/frontend-v2/renderer/plugins/life-summary-block.mjs";

function fakeState() {
  const added = [];
  return {
    added,
    addMessage(role, content, _flag, meta = {}) {
      const message = { role, content, ...meta, sessionId: "sess-1" };
      added.push(message);
      return message;
    },
    snapshot() {
      return { activeSessionId: "sess-1" };
    },
  };
}

test("proactive delivery: pending endpoint shape delivers and acks", async () => {
  // This is the exact payload the loadSummary call site builds from
  // GET /api/v1/v3/life/proactive-chat/pending → { ok, messages }.
  const payload = {
    proactive_chat: {
      pending: [
        {
          message_id: "learnmsg_abc",
          kind: "learning_report",
          text: "我刚学完光合作用，要点已整理进知识库。",
          created_at: "2026-07-30T08:00:00Z",
        },
      ],
    },
  };
  const state = fakeState();
  const acked = [];
  const result = await deliverProactiveChats(payload, state, async (id, sessionId) => {
    acked.push([id, sessionId]);
    return { ok: true, delivered: true };
  });
  assert.deepEqual(result, { delivered: 1, failed: 0 });
  assert.equal(state.added.length, 1);
  assert.equal(state.added[0].role, "assistant");
  assert.equal(state.added[0].content, "我刚学完光合作用，要点已整理进知识库。");
  assert.equal(state.added[0].kind, "life_proactive_chat");
  assert.deepEqual(acked, [["learnmsg_abc", "sess-1"]]);
});

test("proactive delivery: ack failure keeps message and counts failed", async () => {
  const payload = {
    proactive_chat: {
      pending: [
        { message_id: "learnmsg_retry", text: "稍后重试 ack 的分享" },
      ],
    },
  };
  const state = fakeState();
  const result = await deliverProactiveChats(payload, state, async () => ({ ok: false, error: "down" }));
  assert.deepEqual(result, { delivered: 0, failed: 1 });
  assert.equal(state.added.length, 1, "stable id keeps the bubble; a later poll retries only the ack");
});

test("proactive delivery: raw panel payload without proactive_chat is a no-op", async () => {
  const state = fakeState();
  const result = await deliverProactiveChats({ inbox: { items: [] } }, state, async () => ({ ok: true }));
  assert.deepEqual(result, { delivered: 0, failed: 0 });
  assert.equal(state.added.length, 0);
});
