import test from "node:test";
import assert from "node:assert/strict";
import { spokenBackendText, splitThinkBlocks, stripStreamEvents, sanitizeVisibleText } from "../app/frontend-v2/renderer/core/formatters.mjs";
import { cleanChatDisplayText, boundedMessageContent, MESSAGE_MAX_CONTENT } from "../app/frontend-v2/renderer/core/text-presentation.mjs";
import { claimsCompleted } from "../app/frontend-v2/renderer/core/actions.mjs";

test("file contents survive every presentation cleanup byte for byte", () => {
  const body = '<think>这是数据</think>\n<system-reminder>literal</system-reminder>\n<tool_call>example</tool_call>\n__TIANGONG_STREAM_EVENT__ data\n  trailing  \n\n\nlast';
  const text = `文件原文：\n\n\`\`\`text\n${body}\n\`\`\``;
  assert.equal(cleanChatDisplayText(sanitizeVisibleText(spokenBackendText(stripStreamEvents(text)))), text);
});

test("only prose thinking is extracted, including streaming open blocks", () => {
  assert.equal(spokenBackendText('<think>private</think>结果'), '结果');
  assert.equal(spokenBackendText('```think\nprivate\n```\n结果'), '结果');
  assert.equal(spokenBackendText('结果<think>private'), '结果');
  const literal = '标签 `<think>literal</think>` 保持';
  assert.equal(spokenBackendText(literal), literal);
  assert.equal(splitThinkBlocks('```json\n{"x":"<think>value</think>"}\n```').thoughts, '');
});

test("unfinished and nested code fences retain raw data", () => {
  for (const text of ['```text\n<think>still data', '````text\n```\n<think>x</think>\n```\n````', '~~~text\n<think>x</think>\n~~~']) {
    assert.equal(spokenBackendText(text), text);
  }
});

test("15000-character outputs no longer lose their ending", () => {
  const text = 'a'.repeat(15000) + '\n完整结尾';
  assert.equal(boundedMessageContent(spokenBackendText(text)), text);
  const limited = boundedMessageContent('a'.repeat(MESSAGE_MAX_CONTENT + 1));
  assert.ok(limited.includes('显示已截断'));
  assert.equal(limited.length, MESSAGE_MAX_CONTENT);
  assert.equal(boundedMessageContent(limited), limited);
});

test("frontend completion is based on structured state, not generated claims", () => {
  assert.equal(claimsCompleted({ok:true}, '任务已完成'), false);
  assert.equal(claimsCompleted({ok:false, simple_chain_status:'complete'}, ''), false);
  assert.equal(claimsCompleted({ok:true, task_completed:false, simple_chain_status:'complete'}, ''), false);
  assert.equal(claimsCompleted({ok:true, task_completed:true}, ''), true);
  assert.equal(claimsCompleted({ok:true, simple_chain_status:'complete'}, ''), true);
});
