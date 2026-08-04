import assert from "node:assert/strict";
import { requiresDeterministicWebQa } from "../app/frontend-v2/renderer/core/actions.mjs";

const projectRoot = "C:\\workspace\\artifact";

assert.equal(
  requiresDeterministicWebQa(
    "在目录中创建 assembled.docx 并执行 qc.docx.delivery_check。前端运行标识：E2E-123",
    projectRoot,
  ),
  false,
  "an incidental frontend run marker must not turn a document into a web project",
);

assert.equal(
  requiresDeterministicWebQa(
    "用浏览器操作现有页面并保存 screenshot.png，完成后验证哈希",
    projectRoot,
  ),
  false,
  "browser operation alone does not imply an index.html deliverable",
);

assert.equal(
  requiresDeterministicWebQa(
    "构建一个前端项目并启动测试，交付 index.html",
    projectRoot,
  ),
  true,
  "an explicitly requested frontend product must use deterministic web QA",
);

assert.equal(
  requiresDeterministicWebQa(
    "创建网站并完成验收",
    projectRoot,
  ),
  true,
  "an explicitly requested website must use deterministic web QA",
);

console.log("frontend product verifier routing tests passed");
