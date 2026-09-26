import assert from 'node:assert/strict';
import { shouldUseDirectLearning } from '../app/frontend-v2/renderer/core/actions.mjs';
for (const text of ['请学习一下这份材料', 'learn about pathlib', '调用 learning.ingest', '请总结这份材料']) {
  assert.equal(shouldUseDirectLearning(text), false);
}
