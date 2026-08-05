import assert from "node:assert/strict";
import {
  autoContinuationDecision,
  autoContinuationPrompt,
  autoContinuationStopNotice,
  classifyRunInput,
  inferActiveProjectRoot,
} from "../app/frontend-v2/renderer/core/actions.mjs";

const missingVerification = [
  "本轮没有达到可交付完成标准，已停止继续假完成。",
  "已执行工具步数：2",
  "未完成原因：",
  "- requested verification/test step is missing after the latest mutation",
].join("\n");

const first = autoContinuationDecision({
  result: { simple_chain_status: "incomplete" },
  displayText: missingVerification,
  sendMode: "work",
  runOptions: {},
});

assert.equal(first.shouldContinue, true);
assert.equal(first.reason, "verification_debt");
assert.equal(first.nextState.verificationDebtAttempts, 1);
const prompt = autoContinuationPrompt(first);
assert.match(prompt, /验证补偿/);
assert.match(prompt, /禁止新建、写入、追加、移动、删除、打包或覆盖/);
assert.match(prompt, /不得再次修改/);

const repeated = autoContinuationDecision({
  result: { simple_chain_status: "incomplete" },
  displayText: missingVerification,
  sendMode: "work",
  runOptions: { autoContinueState: first.nextState },
});

assert.equal(repeated.shouldContinue, true);
assert.equal(repeated.reason, "verification_debt");
assert.equal(repeated.nextState.verificationDebtAttempts, 2);
assert.equal(autoContinuationStopNotice(repeated), "");

const ordinaryCheckpoint = autoContinuationDecision({
  result: { simple_chain_status: "incomplete" },
  displayText: "本轮尚未完成，但已经推进到下一阶段。",
  sendMode: "work",
  runOptions: {},
});

assert.equal(ordinaryCheckpoint.shouldContinue, true);
assert.equal(ordinaryCheckpoint.reason, "checkpoint");
assert.equal(ordinaryCheckpoint.nextState.consecutiveNoProgressFailures, 1);

const secondNoProgressFailure = autoContinuationDecision({
  result: { simple_chain_status: "incomplete" },
  displayText: "仍未完成，本轮也没有生成新的机器证据。",
  sendMode: "work",
  runOptions: { autoContinueState: ordinaryCheckpoint.nextState },
});
assert.equal(secondNoProgressFailure.shouldContinue, true);
assert.equal(secondNoProgressFailure.reason, "checkpoint");
assert.equal(secondNoProgressFailure.nextState.consecutiveNoProgressFailures, 2);
assert.equal(autoContinuationStopNotice(secondNoProgressFailure), "");

const structuredProgressResetsFailureCount = autoContinuationDecision({
  result: {
    simple_chain_status: "incomplete",
    simple_chain_meta: {
      run_state: {
        completed_actions: [{ action: "file.write", ok: true }],
      },
    },
  },
  displayText: "已产生一个新的结构化完成动作，仍需继续验收。",
  sendMode: "work",
  runOptions: { autoContinueState: ordinaryCheckpoint.nextState },
});
assert.equal(structuredProgressResetsFailureCount.shouldContinue, true);
assert.equal(structuredProgressResetsFailureCount.nextState.consecutiveNoProgressFailures, 0);

const narratedToolCountIsNotEvidence = autoContinuationDecision({
  result: { simple_chain_status: "incomplete" },
  displayText: "已执行工具步数：99\n但没有结构化执行证据。",
  sendMode: "work",
  runOptions: {},
});
assert.equal(narratedToolCountIsNotEvidence.nextState.consecutiveNoProgressFailures, 1);

const repeatGuardCheckpoint = autoContinuationDecision({
  result: { simple_chain_status: "failed" },
  displayText: [
    "这次检查没有真正完成。",
    "卡点：同一工具和参数被重复调用。",
    "下一步：下一轮应基于已保留结果改用父目录列举后再验证。",
  ].join("\n"),
  sendMode: "work",
  runOptions: {},
});
assert.equal(repeatGuardCheckpoint.shouldContinue, true);
assert.equal(repeatGuardCheckpoint.reason, "explicit_checkpoint");

const structuredQcBehindRepeatGuard = autoContinuationDecision({
  result: {
    simple_chain_status: "incomplete",
    simple_chain_meta: {
      run_state: {
        delivery: { active_failures: [], active_gaps: [] },
        gaps: [
          "mutation suffix does not match requested deliverable suffixes",
          "quality acceptance failed: score=59",
          "quality acceptance detail: video_unreadable; missing_hook_spec",
        ],
      },
    },
  },
  displayText: [
    "这次检查没有真正完成。",
    "下一步：下一轮应基于已保留结果继续验证。",
  ].join("\n"),
  sendMode: "work",
  runOptions: {},
});
assert.equal(structuredQcBehindRepeatGuard.shouldContinue, true);
assert.match(autoContinuationPrompt(structuredQcBehindRepeatGuard), /quality acceptance failed: score=59/);
assert.match(autoContinuationPrompt(structuredQcBehindRepeatGuard), /missing_hook_spec/);
assert.doesNotMatch(autoContinuationPrompt(structuredQcBehindRepeatGuard), /mutation suffix does not match/);
assert.match(autoContinuationPrompt(structuredQcBehindRepeatGuard), /质量门返工/);

const qcCheckpoint = autoContinuationDecision({
  result: { simple_chain_status: "incomplete" },
  displayText: [
    "本轮没有达到可交付完成标准。",
    "未完成原因：",
    "qc.docx.delivery_check did not meet its acceptance gate (score=59)",
  ].join("\n"),
  sendMode: "work",
  runOptions: {},
});
assert.equal(qcCheckpoint.shouldContinue, true);
assert.match(
  autoContinuationPrompt(qcCheckpoint),
  /qc\.docx\.delivery_check did not meet its acceptance gate \(score=59\)/,
);
assert.match(autoContinuationPrompt(qcCheckpoint), /必须先修改产生该 QC 输入的源产物/);
const qcFollowup = autoContinuationDecision({
  result: { simple_chain_status: "incomplete" },
  displayText: "explicitly requested actions are missing: docx.create, file.hash",
  sendMode: "work",
  runOptions: { autoContinueState: qcCheckpoint.nextState },
});
assert.match(
  autoContinuationPrompt(qcFollowup),
  /qc\.docx\.delivery_check did not meet its acceptance gate \(score=59\)/,
);

assert.deepEqual(classifyRunInput("顺便再帮我做第二件事"), {
  kind: "queue",
  text: "顺便再帮我做第二件事",
});
assert.deepEqual(classifyRunInput("纠偏：回到主目标"), {
  kind: "guide",
  text: "回到主目标",
});
assert.deepEqual(classifyRunInput("/guide 不要改数据库"), {
  kind: "guide",
  text: "不要改数据库",
});

assert.equal(
  inferActiveProjectRoot("", "C:\\work", "", "", "旧产物 C:\\work\\release.zip"),
  "",
);
assert.equal(
  inferActiveProjectRoot("请继续 alpha 项目", "C:\\work", "", "", ""),
  "C:\\work\\alpha",
);

// FE-07 regression: normal terminal states never carry the stop notice.
const casualChat = autoContinuationDecision({
  result: { simple_chain_status: "chat_reply" },
  displayText: "午安呀～",
  sendMode: "chat",
  runOptions: {},
});
assert.equal(casualChat.shouldContinue, false);
assert.equal(casualChat.stopReason, "");
assert.equal(autoContinuationStopNotice(casualChat), "");

const casualChatInWorkMode = autoContinuationDecision({
  result: { simple_chain_status: "chat_reply" },
  displayText: "午安呀～",
  sendMode: "work",
  runOptions: {},
});
assert.equal(casualChatInWorkMode.stopReason, "");
assert.equal(autoContinuationStopNotice(casualChatInWorkMode), "");

const completedTask = autoContinuationDecision({
  result: { simple_chain_status: "complete" },
  displayText: "任务已完成。",
  sendMode: "work",
  runOptions: {},
});
assert.equal(completedTask.stopReason, "");
assert.equal(autoContinuationStopNotice(completedTask), "");

const completedTaskZh = autoContinuationDecision({
  result: { stdout: JSON.stringify({ zhuangtai: "wancheng" }) },
  displayText: "任务已完成。",
  sendMode: "work",
  runOptions: {},
});
assert.equal(completedTaskZh.stopReason, "");
assert.equal(autoContinuationStopNotice(completedTaskZh), "");

// Direct plain chat: backend returns empty simple_chain_status with no tool
// chain, which is a normal terminal and must never carry the stop notice.
const directChatNoStatus = autoContinuationDecision({
  result: { simple_chain_status: "" },
  displayText: "午安呀～",
  sendMode: "chat",
  runOptions: {},
});
assert.equal(directChatNoStatus.stopReason, "");
assert.equal(autoContinuationStopNotice(directChatNoStatus), "");

const directChatNoStatusWork = autoContinuationDecision({
  result: { simple_chain_status: "" },
  displayText: "好的，直接回复。",
  sendMode: "work",
  runOptions: {},
});
assert.equal(directChatNoStatusWork.stopReason, "");
assert.equal(autoContinuationStopNotice(directChatNoStatusWork), "");

// FE-07 preserved: a genuinely failed work run still explains why it stops.
const failedWorkRun = autoContinuationDecision({
  result: { simple_chain_status: "failed" },
  displayText: "服务暂时不可用，本轮没有继续。",
  sendMode: "work",
  runOptions: {},
});
assert.equal(failedWorkRun.stopReason, "not_recoverable");
assert.match(autoContinuationStopNotice(failedWorkRun), /任务已停止/);

const awaitingUserRun = autoContinuationDecision({
  result: { simple_chain_status: "awaiting_user" },
  displayText: "需要你决定后才能继续。",
  sendMode: "work",
  runOptions: {},
});
assert.equal(awaitingUserRun.stopReason, "requires_user");
assert.match(autoContinuationStopNotice(awaitingUserRun), /任务已暂停/);

console.log("auto-continuation-recovery: PASS");
