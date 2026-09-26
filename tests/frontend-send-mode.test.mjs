import assert from 'node:assert/strict';
import { inferSendMode, normalizeBackendDeliveryIntent, inferActiveProjectRoot } from '../app/frontend-v2/renderer/core/actions.mjs';
for (const text of ['请执行测试并修复', '不要调用任何工具，只回复 OK', 'Do not use tools', '禁止访问桌面', '新建 demo 项目']) {
  assert.equal(inferSendMode(text, {mode:'auto'}), 'auto');
  assert.equal(inferSendMode(text, {mode:'work'}), 'work');
  assert.equal(inferSendMode(text, {}, [], {mode:'chat'}), 'chat');
  assert.equal(normalizeBackendDeliveryIntent(text), text);
  assert.equal(inferActiveProjectRoot(text, 'C:/workspace'), '');
}
assert.equal(inferActiveProjectRoot('', 'C:/workspace', '', 'C:/workspace/demo'), 'C:/workspace/demo');
assert.equal(inferActiveProjectRoot('', 'C:/workspace', '', 'C:/workspace/../elsewhere'), '');
