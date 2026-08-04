import assert from "node:assert/strict";
import {
  classifyAssistantCompletion,
  classifyProviderPreflight,
  expectsAutoContinuation,
} from "../scripts/lib/frontend-skill-e2e-classifier.mjs";

assert.deepEqual(
  classifyAssistantCompletion({ lastAssistantError: true, lastAssistantText: "任意文字" }),
  { ok: false, reason: "assistant_error_class" },
);
assert.equal(classifyAssistantCompletion({ lastAssistantText: "任务没有真正完成。" }).ok, false);
assert.equal(classifyAssistantCompletion({ lastAssistantText: "本轮没有达到可交付完成标准，已停止继续假完成。" }).ok, false);
assert.equal(classifyAssistantCompletion({ lastAssistantText: "qc.docx.delivery_check did not meet its acceptance gate (score=59)" }).ok, false);
assert.equal(expectsAutoContinuation({ lastAssistantText: "已保存检查点，正在自动续作（1/24）。无需回复“继续”。" }), true);
assert.equal(expectsAutoContinuation({ lastAssistantText: "任务已完成。" }), false);
assert.equal(classifyAssistantCompletion({ lastAssistantText: "请求已中断，请稍后重试。" }).ok, false);
assert.equal(classifyAssistantCompletion({ lastAssistantText: "工具执行失败。" }).ok, false);
assert.equal(classifyAssistantCompletion({ lastAssistantText: "后端执行失败：执行链未能成功完成请求。错误码：gateway_request_failed" }).ok, false);
assert.equal(classifyAssistantCompletion({ lastAssistantText: "gatewayrequestfailed" }).ok, false);
assert.deepEqual(
  classifyAssistantCompletion({ lastAssistantText: "操作已经完成，产物已写入工作区。" }),
  { ok: true, reason: "assistant_finished" },
);
assert.deepEqual(
  classifyProviderPreflight({
    provider: "deepseek_v4",
    optimization: {
      active_provider: {
        provider: "deepseek_v4",
        health: "failed",
        last_http_status: 402,
      },
    },
  }),
  {
    ok: false,
    reason: "provider_account_blocked",
    provider: "deepseek_v4",
    httpStatus: 402,
  },
);
assert.equal(
  classifyProviderPreflight({
    provider: "mimo",
    optimization: { active_provider: { provider: "mimo", health: "healthy", last_http_status: 200 } },
  }).ok,
  true,
);

console.log("frontend skill E2E classification tests passed");
