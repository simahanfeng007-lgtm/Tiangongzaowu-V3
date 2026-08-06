// 模型错误提示可读性回归：用户必须能从报错知道“为什么”和“怎么办”。
import assert from "node:assert/strict";
import { humanizeBackendError } from "../app/frontend-v2/renderer/core/formatters.mjs";

// 结构化 LLM 错误（后端 [LLM错误: ...] 带 http_status）
const structured429 = humanizeBackendError(
  "[LLM错误: reason=quota; provider=openai; model=gpt-4o; http_status=429; hint=限流或额度不足（HTTP 429）]",
);
assert.match(structured429, /HTTP 429/);
assert.match(structured429, /服务商控制台/);
assert.match(structured429, /处理建议/);

// 非结构化文本也要给出原因和动作
const plain429 = humanizeBackendError("Provider rate limit or quota was reached; HTTP 429");
assert.match(plain429, /HTTP 429/);
assert.match(plain429, /额度已用完|每分钟\/每日调用次数超限/);
assert.match(plain429, /服务商控制台/);

const plain401 = humanizeBackendError("401 invalid api key");
assert.match(plain401, /HTTP 401\/403/);
assert.match(plain401, /API Key/);

const plain404 = humanizeBackendError("model_not_found 404; check model name and Base URL");
assert.match(plain404, /HTTP 404/);

console.log("error-hints: all assertions passed");
