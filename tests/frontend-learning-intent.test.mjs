import assert from "node:assert/strict";

import { shouldUseDirectLearning } from "../app/frontend-v2/renderer/core/actions.mjs";

assert.equal(shouldUseDirectLearning("请学习一下这份材料"), true);
assert.equal(shouldUseDirectLearning("learn about pathlib"), true);
assert.equal(
  shouldUseDirectLearning(
    "调用 learning.ingest，只创建 awaiting_user 学习卡，绝不确认、激活、注册或发布"
  ),
  false
);
assert.equal(
  shouldUseDirectLearning("学一下这个流程，但只创建待确认学习卡，禁止激活"),
  false
);
assert.equal(shouldUseDirectLearning("请总结这份材料"), false);

console.log("frontend learning intent routing: ok");
