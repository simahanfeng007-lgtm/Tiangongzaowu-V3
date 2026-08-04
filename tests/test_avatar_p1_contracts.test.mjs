// Avatar P1 契约测试（方案 §7/§8.2/§15/§18/§20，阶段 P1 退出条件）。
// 运行：node --test tests/test_avatar_p1_contracts.test.mjs

import assert from "node:assert/strict";

import {
  AttemptKind,
  LoadAttemptState,
  RuntimeState,
  actionIdempotencyKey,
  clampTtlMs,
  isLoadAttemptTransitionAllowed,
  isRuntimeTransitionAllowed,
  isScheduledActionExpired,
  scheduleBodyAction,
  validateAssetTokenForUse,
  validateAssetTokenShape,
} from "../app/frontend-v2/renderer/avatar/contracts.mjs";
import { createServiceRegistry } from "../app/frontend-v2/renderer/avatar/service-registry.mjs";
import { createLifecycleScope } from "../app/frontend-v2/renderer/avatar/lifecycle.mjs";
import { LoadAttempt, LoadAttemptError } from "../app/frontend-v2/renderer/avatar/load-attempt.mjs";
import { AvatarRuntimeSlots, RuntimeStateError } from "../app/frontend-v2/renderer/avatar/runtime-state.mjs";
import { createFixedStepper } from "../app/frontend-v2/renderer/avatar/fixed-step.mjs";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

function makeToken(overrides = {}) {
  return {
    assetId: "asset-1",
    contentHash: HASH_A,
    byteLength: 1024,
    validationReceiptId: "receipt-1",
    registryEntryVersion: 1,
    issuerEpoch: 7,
    nonce: "nonce-1",
    singleUse: true,
    ...overrides,
  };
}

function makeAvatar(label = "avatar") {
  return { label, disposed: false, dispose() { this.disposed = true; } };
}

function makeAttempt(overrides = {}) {
  return new LoadAttempt({
    attemptId: overrides.attemptId ?? `attempt-${Math.random().toString(36).slice(2)}`,
    attemptKind: overrides.attemptKind ?? AttemptKind.SWITCH,
    rollbackTarget: "rollbackTarget" in overrides ? overrides.rollbackTarget : makeAvatar("rollback"),
    activePhaseDeadline: overrides.activePhaseDeadline ?? 10_000,
    maxContinuousSuspendedMs: overrides.maxContinuousSuspendedMs ?? 5_000,
    maxCumulativeSuspendedMs: overrides.maxCumulativeSuspendedMs ?? 15_000,
    nowMonotonic: overrides.nowMonotonic ?? 0,
  });
}

// 沿主链推进 attempt 到指定状态（从当前位置继续，不回退）。
function advanceTo(attempt, target, now = 100) {
  const chain = [
    "validating", "admitted", "loading", "parsing", "uploading",
    "renderability-probe", "provisional-present", "visibility-probe",
  ];
  const start = chain.indexOf(attempt.state) + 1; // 当前已在链上则跳过已走过的状态
  for (let i = Math.max(start, 0); i < chain.length; i += 1) {
    if (attempt.state === target) break;
    attempt.transition(chain[i], { nowMonotonic: now });
  }
  assert.equal(attempt.state, target);
  return attempt;
}

// ── 1. LoadAttemptState 合法/非法迁移 ────────────────────────

assert.equal(isLoadAttemptTransitionAllowed("selecting", "validating"), true);
assert.equal(isLoadAttemptTransitionAllowed("visibility-probe", "committed"), true);
assert.equal(isLoadAttemptTransitionAllowed("visibility-probe", "suspended-probe"), true);
assert.equal(isLoadAttemptTransitionAllowed("suspended-probe", "visibility-probe"), true);
// suspended-probe 只与 visibility-probe 互转，超预算只允许 cancelled
assert.equal(isLoadAttemptTransitionAllowed("suspended-probe", "cancelled"), true);
assert.equal(isLoadAttemptTransitionAllowed("suspended-probe", "committed"), false);
assert.equal(isLoadAttemptTransitionAllowed("suspended-probe", "failed"), false);
assert.equal(isLoadAttemptTransitionAllowed("suspended-probe", "loading"), false);
// committed 只能来自 visibility-probe
assert.equal(isLoadAttemptTransitionAllowed("provisional-present", "committed"), false);
assert.equal(isLoadAttemptTransitionAllowed("loading", "committed"), false);
// 不允许跳级、不允许终态迁出
assert.equal(isLoadAttemptTransitionAllowed("selecting", "loading"), false);
assert.equal(isLoadAttemptTransitionAllowed("committed", "failed"), false);
assert.equal(isLoadAttemptTransitionAllowed("cancelled", "visibility-probe"), false);
// 不存在游离 ready 状态
assert.equal(isLoadAttemptTransitionAllowed("visibility-probe", "ready"), false);

{
  const attempt = makeAttempt();
  assert.throws(
    () => attempt.transition("parsing", { nowMonotonic: 1 }),
    (error) => error instanceof LoadAttemptError && error.code === "transition_illegal",
  );
}

// RuntimeState 迁移表
assert.equal(isRuntimeTransitionAllowed("uninitialized", "running"), true);
assert.equal(isRuntimeTransitionAllowed("running", "context-lost"), true);
assert.equal(isRuntimeTransitionAllowed("context-lost", "recovering"), true);
assert.equal(isRuntimeTransitionAllowed("recovering", "running"), true);
assert.equal(isRuntimeTransitionAllowed("recovering", "degraded"), true);
assert.equal(isRuntimeTransitionAllowed("running", "recovering"), false);
assert.equal(isRuntimeTransitionAllowed("disposed", "running"), false);

// ── 2. committed 前必须经过 visibility-probe ─────────────────

{
  const slots = new AvatarRuntimeSlots();
  const attempt = makeAttempt({ activePhaseDeadline: 1_000 });
  slots.beginLoad(attempt, { nowMonotonic: 0 });
  advanceTo(attempt, "provisional-present");
  // 未进入 visibility-probe，attempt 不可能 committed，直接提交必须拒绝
  assert.throws(
    () => slots.commitPending(makeAvatar("candidate"), { nowMonotonic: 200 }),
    (error) => error instanceof RuntimeStateError && error.code === "commit_requires_visibility_probe",
  );
  advanceTo(attempt, "visibility-probe");
  attempt.commit(300);
  const current = slots.commitPending(makeAvatar("candidate"), { nowMonotonic: 300 });
  assert.equal(current.label, "candidate");
  assert.equal(slots.state, RuntimeState.RUNNING);
  assert.equal(slots.hasCurrent(), true);
}

// ── 3. latest-wins 取消旧 pending，不动 current ─────────────

{
  const slots = new AvatarRuntimeSlots();
  const oldCurrent = makeAvatar("current");
  slots.state = RuntimeState.RUNNING;
  slots.current = oldCurrent;

  const first = makeAttempt({ attemptId: "a1" });
  slots.beginLoad(first, { nowMonotonic: 0 });
  advanceTo(first, "loading");

  const second = makeAttempt({ attemptId: "a2" });
  slots.beginLoad(second, { nowMonotonic: 10 });

  assert.equal(first.state, LoadAttemptState.CANCELLED);
  assert.equal(first.terminalReason, "superseded");
  assert.equal(first.countsAsFailure, false);
  assert.equal(slots.pending, second);
  // current 未被取消、未被释放
  assert.equal(slots.current, oldCurrent);
  assert.equal(oldCurrent.disposed, false);
  assert.equal(slots.hasCurrent(), true);
}

// ── 4. recovery：rollbackTarget=null，失败进 degraded ───────

{
  // recovery attempt 构造即强制 rollbackTarget=null
  assert.throws(
    () => makeAttempt({ attemptKind: AttemptKind.RECOVERY, rollbackTarget: makeAvatar("x") }),
    (error) => error.code === "rollback_target_invalid",
  );

  const slots = new AvatarRuntimeSlots();
  slots.state = RuntimeState.RUNNING;
  slots.current = makeAvatar("old");
  slots.contextLost({ recoverySnapshot: { posture: "idle" } });
  // context-lost 后旧 current 已失效并移出槽位，"current 存在"判定为 false
  assert.equal(slots.state, RuntimeState.CONTEXT_LOST);
  assert.equal(slots.hasCurrent(), false);
  assert.deepEqual(slots.recoverySnapshot, { posture: "idle" });

  const recovery = makeAttempt({
    attemptKind: AttemptKind.RECOVERY,
    rollbackTarget: null,
    activePhaseDeadline: 5_000,
  });
  slots.beginRecovery(recovery);
  assert.equal(slots.state, RuntimeState.RECOVERING);
  assert.equal(recovery.rollbackTarget, null);

  // 恢复失败 → degraded，不访问旧 GPU 资源
  slots.recoveryFailed({ reason: "first-visible-frame-timeout" });
  assert.equal(slots.state, RuntimeState.DEGRADED);
  assert.equal(recovery.state, LoadAttemptState.FAILED);
  assert.equal(recovery.countsAsFailure, true);
  assert.equal(slots.pending, null);
  assert.equal(slots.hasCurrent(), false);
}

// ── 5. suspended 累计超限 → cancelled 且不计失败 ────────────

{
  const attempt = makeAttempt({
    maxContinuousSuspendedMs: 1_000,
    maxCumulativeSuspendedMs: 2_000,
    activePhaseDeadline: 60_000,
  });
  advanceTo(attempt, "visibility-probe");

  // 第一段挂起 900ms（未到连续上限），恢复后 deadline 顺延
  attempt.transition("suspended-probe", { nowMonotonic: 1_000 });
  const deadlineBefore = attempt.activePhaseDeadline;
  attempt.transition("visibility-probe", { nowMonotonic: 1_900 });
  assert.equal(attempt.cumulativeSuspendedMs, 900);
  assert.equal(attempt.activePhaseDeadline, deadlineBefore + 900);

  // 第二段挂起使累计超过 2_000ms → cancelled，且 suspended-probe 不能直接 failed
  attempt.transition("suspended-probe", { nowMonotonic: 2_000 });
  assert.equal(attempt.checkSuspensionBudget(3_300), true);
  assert.equal(attempt.state, LoadAttemptState.CANCELLED);
  assert.equal(attempt.terminalReason, "suspension-budget-exceeded");
  assert.equal(attempt.countsAsFailure, false);
  assert.ok(attempt.cumulativeSuspendedMs >= 2_000);

  // 连续挂起超限同样 → cancelled
  const attempt2 = makeAttempt({ maxContinuousSuspendedMs: 500, maxCumulativeSuspendedMs: 9_000 });
  advanceTo(attempt2, "visibility-probe");
  attempt2.transition("suspended-probe", { nowMonotonic: 0 });
  assert.equal(attempt2.checkSuspensionBudget(499), false);
  assert.equal(attempt2.checkSuspensionBudget(500), true);
  assert.equal(attempt2.state, LoadAttemptState.CANCELLED);
  assert.equal(attempt2.countsAsFailure, false);

  // 挂起期间 activePhaseDeadline 暂停计时，不误判到期
  const attempt3 = makeAttempt({ activePhaseDeadline: 1_000 });
  advanceTo(attempt3, "visibility-probe");
  attempt3.transition("suspended-probe", { nowMonotonic: 900 });
  assert.equal(attempt3.isActivePhaseExpired(5_000), false);
}

// ── 6. ValidatedAssetToken 校验 ──────────────────────────────

assert.deepEqual(validateAssetTokenShape(makeToken()), []);
// hash 必须 64 位小写 hex：大写、短哈希、非 hex 全拒绝
assert.ok(validateAssetTokenShape(makeToken({ contentHash: HASH_A.toUpperCase() })).includes("content_hash_invalid"));
assert.ok(validateAssetTokenShape(makeToken({ contentHash: "abc123" })).includes("content_hash_invalid"));
assert.ok(validateAssetTokenShape(makeToken({ contentHash: "g".repeat(64) })).includes("content_hash_invalid"));
assert.ok(validateAssetTokenShape(makeToken({ registryEntryVersion: 0 })).includes("registry_entry_version_invalid"));
assert.ok(validateAssetTokenShape(makeToken({ byteLength: -1 })).includes("byte_length_invalid"));
assert.ok(validateAssetTokenShape(makeToken({ singleUse: "yes" })).includes("single_use_invalid"));

{
  const record = {
    assetId: "asset-1",
    contentHash: HASH_A,
    byteLength: 1024,
    validationReceiptId: "receipt-1",
    registryEntryVersion: 1,
    issuerEpoch: 7,
  };
  assert.equal(validateAssetTokenForUse(makeToken(), record).ok, true);
  // registryEntryVersion 变化 → 旧 Token 拒绝（§8.2 吊销语义）
  assert.deepEqual(
    validateAssetTokenForUse(makeToken(), { ...record, registryEntryVersion: 2 }).errors,
    ["registry_entry_version_mismatch"],
  );
  // hash 不一致拒绝
  assert.equal(validateAssetTokenForUse(makeToken(), { ...record, contentHash: HASH_B }).ok, false);
  // receipt / issuerEpoch / byteLength 任一不一致均拒绝
  assert.equal(validateAssetTokenForUse(makeToken(), { ...record, validationReceiptId: "r2" }).ok, false);
  assert.equal(validateAssetTokenForUse(makeToken(), { ...record, issuerEpoch: 8 }).ok, false);
  assert.equal(validateAssetTokenForUse(makeToken(), { ...record, byteLength: 2048 }).ok, false);
}

// ── 7. TTL 由前端本地单调时钟计算 ───────────────────────────

{
  const wire = {
    schema: "body-action/v1",
    backendInstanceId: "backend-A",
    turnId: "turn-1",
    sequence: 3,
    sourceCreatedAt: 1_700_000_000_000, // 后端墙钟，仅诊断
    ttlMs: 250,
    gesture: "wave",
  };
  const scheduled = scheduleBodyAction(wire, { nowMonotonic: 1_000, maxAllowedTTL: 500 });
  assert.equal(scheduled.receivedAtMonotonic, 1_000);
  assert.equal(scheduled.deadlineMonotonic, 1_250);
  // 篡改 sourceCreatedAt 不影响 deadline：deadline 只由本地时钟 + clamp(ttl) 决定
  const tampered = scheduleBodyAction({ ...wire, sourceCreatedAt: -999_999 }, { nowMonotonic: 1_000, maxAllowedTTL: 500 });
  assert.equal(tampered.deadlineMonotonic, scheduled.deadlineMonotonic);
  assert.equal(isScheduledActionExpired(scheduled, 1_249), false);
  assert.equal(isScheduledActionExpired(scheduled, 1_250), true);
  // TTL clamp 到 [0, maxAllowedTTL]
  assert.equal(clampTtlMs(10_000, 500), 500);
  assert.equal(clampTtlMs(-5, 500), 0);
  assert.equal(scheduleBodyAction(wire, { nowMonotonic: 0, maxAllowedTTL: 100 }).deadlineMonotonic, 100);
  // 缺时钟直接抛错，禁止隐式用墙钟
  assert.throws(() => scheduleBodyAction(wire, {}));
}

// ── 8. 幂等键：不同 backendInstanceId 隔离 + sessionEpoch 降级 ─

{
  const base = { turnId: "turn-1", sequence: 5 };
  const keyA = actionIdempotencyKey({ ...base, backendInstanceId: "backend-A" });
  const keyB = actionIdempotencyKey({ ...base, backendInstanceId: "backend-B" });
  assert.notEqual(keyA, keyB); // 后端重启换 instanceId 后旧动作不被误判为重复
  assert.equal(keyA, actionIdempotencyKey({ ...base, backendInstanceId: "backend-A" }));

  const legacy1 = actionIdempotencyKey({ ...base, sessionEpoch: "epoch-1" });
  const legacy2 = actionIdempotencyKey({ ...base, sessionEpoch: "epoch-2" });
  assert.notEqual(legacy1, legacy2); // 重连换 epoch 隔离旧连接补发
  assert.notEqual(legacy1, keyA);
  assert.throws(() => actionIdempotencyKey(base)); // 两者皆缺拒绝
  assert.throws(() => actionIdempotencyKey({ ...base, backendInstanceId: "b", sequence: -1 }));
}

// ── 9. 固定时间步：subSteps 递增 / 上限 / 积压丢弃遥测 ──────

{
  // 默认常量与 §15.5 一致
  const defaults = createFixedStepper();
  assert.equal(defaults.fixedStep, 1 / 60);
  assert.equal(defaults.maxSubSteps, 4);

  // 步进断言使用二进制精确分数（1/64、0.125），避免 1/60 浮点余量干扰计数
  const metrics = [];
  const stepper = createFixedStepper({
    fixedStep: 1 / 64,
    maxFrameDelta: 0.125,
    maxSubSteps: 4,
    onMetric: (name, value) => metrics.push([name, value]),
  });
  const stepped = [];
  const update = (dt) => stepped.push(dt);

  // 首帧 dt=0，不推进
  assert.equal(stepper.advance(0, update), 0);
  // 一帧 1/64 → 1 个子步
  assert.equal(stepper.advance(1 / 64, update), 1);
  // 两帧时间 → 2 个子步（subSteps 每帧重新初始化并递增）
  assert.equal(stepper.advance(1 / 64 + 2 / 64, update), 2);
  assert.equal(stepped.length, 3);
  assert.ok(stepped.every((dt) => dt === 1 / 64));

  // 极大帧 delta：clamp 到 maxFrameDelta=0.125（=8 步），最多 4 子步，积压 4 步丢弃 + 遥测
  assert.equal(stepper.advance(1 / 64 + 100, update), 4);
  assert.deepEqual(metrics, [["PHYSICS_STEPS_DROPPED", 4]]);
  // 只保留插值余量，余量 < fixedStep
  assert.ok(stepper.accumulator < 1 / 64);
  // 下一帧无积压：普通帧回到 1 子步，无 spiral of death
  assert.equal(stepper.advance(1 / 64 + 100 + 1 / 64, update), 1);
  assert.equal(metrics.length, 1);

  // 隐藏恢复后 reset：清空 accumulator 与 lastNow，不补算
  stepper.advance(1 / 64 + 100 + 2 / 64 + 0.5, update); // clamp 0.125 → 4 步 + 丢弃
  stepper.reset();
  assert.equal(stepper.accumulator, 0);
  assert.equal(stepper.advance(9_999, update), 0); // reset 后首帧 dt=0，不补算隐藏期间
  const metricCount = metrics.length;
  stepper.reset();
  stepper.advance(20_000, update);
  assert.equal(metrics.length, metricCount); // 补算被禁止，无新增丢弃遥测

  // 参数约束
  assert.throws(() => createFixedStepper({ fixedStep: 0 }));
  assert.throws(() => createFixedStepper({ maxSubSteps: 0 }));
  assert.throws(() => createFixedStepper({ maxSubSteps: 1.5 }));
}

// ── 10. lifecycle：重复 mount 不泄漏，unmount 全释放 ────────

{
  const rafQueue = new Map();
  let rafSeq = 0;
  const env = {
    requestAnimationFrame: (cb) => { const id = ++rafSeq; rafQueue.set(id, cb); return id; },
    cancelAnimationFrame: (id) => { rafQueue.delete(id); },
    ResizeObserver: class {
      constructor(cb) { this.cb = cb; this.observed = []; this.disconnected = false; }
      observe(target) { this.observed.push(target); }
      disconnect() { this.disconnected = true; }
    },
  };
  const fakeTarget = () => {
    const listeners = [];
    return {
      listeners,
      addEventListener(type, handler) { listeners.push([type, handler]); },
      removeEventListener(type, handler) {
        const index = listeners.findIndex(([t, h]) => t === type && h === handler);
        if (index >= 0) listeners.splice(index, 1);
      },
    };
  };

  const scope = createLifecycleScope(env);
  const target = fakeTarget();
  const busOffs = [];
  scope.mount({
    mount(ctx) {
      ctx.trackDomListener(target, "click", () => {});
      ctx.trackSubscription(() => busOffs.push("bus"));
      ctx.trackSubscription(() => busOffs.push("state"));
      ctx.trackResizeObserver({}, () => {});
      ctx.trackRaf(() => {});
      ctx.trackRaf(() => {});
      return () => busOffs.push("plugin-cleanup");
    },
  });
  assert.deepEqual(scope.counts(), {
    subscriptions: 2, listeners: 1, observers: 1, raf: 2, objectUrls: 0, cleanups: 1,
  });

  // 重复 mount 拒绝，不增加任何登记
  assert.throws(() => scope.mount({ mount() {} }));
  assert.equal(scope.counts().listeners, 1);
  assert.equal(scope.counts().raf, 2);

  // unmount 全量释放：监听器移除、RAF 取消、observer 断开、订阅与 cleanup 执行
  const after = scope.unmount();
  assert.deepEqual(after, { subscriptions: 0, listeners: 0, observers: 0, raf: 0, objectUrls: 0, cleanups: 0 });
  assert.equal(target.listeners.length, 0);
  assert.equal(rafQueue.size, 0);
  assert.deepEqual(busOffs, ["state", "bus", "plugin-cleanup"]); // 订阅逆序释放，插件 cleanup 最后

  // 重复 unmount 幂等，无二次副作用
  scope.unmount();
  assert.equal(busOffs.length, 3);
  // 释放后禁止再登记
  assert.throws(() => scope.trackSubscription(() => {}));

  // 新 scope 可再次 mount/unmount，循环无泄漏
  for (let i = 0; i < 3; i += 1) {
    const round = createLifecycleScope(env);
    const t = fakeTarget();
    round.mount({ mount(ctx) { ctx.trackDomListener(t, "x", () => {}); ctx.trackRaf(() => {}); } });
    round.unmount();
    assert.deepEqual(round.counts(), { subscriptions: 0, listeners: 0, observers: 0, raf: 0, objectUrls: 0, cleanups: 0 });
    assert.equal(t.listeners.length, 0);
  }
  assert.equal(rafQueue.size, 0);
}

// ── 11. service-registry：单例、重复注册拒绝、dispose 全清理 ─

{
  const registry = createServiceRegistry();
  const disposed = [];
  const runtime = { id: "runtime", dispose: () => disposed.push("runtime") };
  const scheduler = { id: "scheduler", dispose: () => disposed.push("scheduler") };

  registry.registerService("avatar-runtime", runtime);
  registry.registerService("body-scheduler", scheduler);
  assert.equal(registry.getService("avatar-runtime"), runtime); // 单例：同 id 同实例
  assert.equal(registry.hasService("body-scheduler"), true);
  assert.throws(() => registry.registerService("avatar-runtime", { id: "impostor" })); // 重复注册拒绝
  assert.equal(registry.getService("avatar-runtime"), runtime); // 未被覆盖
  assert.throws(() => registry.getService("missing"));

  const result = registry.disposeAllServices();
  assert.equal(result.disposed, 2);
  assert.deepEqual(disposed, ["scheduler", "runtime"]); // 逆序释放
  assert.equal(registry.hasService("avatar-runtime"), false);
  // 清理后可重新注册同名服务
  registry.registerService("avatar-runtime", { id: "next" });
  assert.equal(registry.getService("avatar-runtime").id, "next");
}

console.log("test_avatar_p1_contracts: all assertions passed");
