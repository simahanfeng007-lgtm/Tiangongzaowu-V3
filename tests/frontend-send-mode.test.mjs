import assert from "node:assert/strict";

import { inferSendMode } from "../app/frontend-v2/renderer/core/actions.mjs";

const responseOnly = "真实任务执行质检：不要调用任何工具，不要修改文件，只回复唯一文本：QA_OK";
assert.equal(inferSendMode(responseOnly, { mode: "work" }), "chat");
assert.equal(
  inferSendMode("Do not use any tools; just reply QA_OK.", { mode: "work" }),
  "chat",
);
assert.equal(inferSendMode("请执行测试并修复失败项", { mode: "auto" }), "work");
assert.equal(
  inferSendMode(responseOnly, {}, [{ id: "selected" }], { forceSelectedSkills: true }),
  "work",
);

console.log("frontend send mode routing: ok");
