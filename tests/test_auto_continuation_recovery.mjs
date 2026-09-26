import assert from 'node:assert/strict';
import {autoContinuationDecision, autoContinuationPrompt, autoContinuationStopNotice, classifyRunInput, claimsCompleted, requiresDeterministicWebQa} from '../app/frontend-v2/renderer/core/actions.mjs';
for (const text of ['已执行工具步数：99', '需要付款，公开部署，购买', '缺少测试，质量验收失败', '请回复继续，以上是计划']) {
  const done = autoContinuationDecision({result:{simple_chain_status:'complete'},displayText:text,runOptions:{rootGoal:'创建网页并验收'.repeat(100)}});
  assert.equal(done.shouldContinue,false);
  assert.equal(done.requiresUser,false);
  assert.equal(done.thinCompletion,false);
  assert.equal(done.stopReason,'');
  assert.equal(claimsCompleted({simple_chain_status:'complete'},text),true);
}
const blocked = autoContinuationDecision({result:{simple_chain_status:'awaiting_user'},displayText:'成功',runOptions:{}});
assert.equal(blocked.requiresUser,true);
assert.match(autoContinuationStopNotice(blocked),/任务已暂停/);
const pending = autoContinuationDecision({result:{simple_chain_status:'incomplete'},displayText:'已经全部完成',runOptions:{}});
assert.equal(pending.recoverable,true);
assert.equal(pending.reason,'checkpoint');
assert.equal(pending.verificationDebt,false);
assert.doesNotMatch(autoContinuationPrompt(pending),/禁止新建|必须先修改/);
assert.equal(claimsCompleted({ok:false,simple_chain_status:'complete'},'已完成'),false);
assert.equal(requiresDeterministicWebQa('网页测试上线','C:/workspace'),false);
assert.deepEqual(classifyRunInput('纠偏：改用方案二'),{kind:'guide',text:'改用方案二'});
assert.deepEqual(classifyRunInput('继续'),{kind:'queue',text:'继续'});
