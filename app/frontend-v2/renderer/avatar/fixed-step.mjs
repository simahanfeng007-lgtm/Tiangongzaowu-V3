// §15.5 固定时间步：accumulator + maxSubSteps 上限 + 积压丢弃遥测。
// 时钟由调用方注入（单调秒），窗口隐藏/恢复后必须 reset 清空，不补算。

import {
  FIXED_STEP_SECONDS,
  MAX_FRAME_DELTA_SECONDS,
  MAX_SUB_STEPS,
  PHYSICS_STEPS_DROPPED_METRIC,
} from "./contracts.mjs";

export class FixedStepError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "FixedStepError";
    this.code = code;
  }
}

export function createFixedStepper({
  fixedStep = FIXED_STEP_SECONDS,
  maxFrameDelta = MAX_FRAME_DELTA_SECONDS,
  maxSubSteps = MAX_SUB_STEPS,
  onMetric = null,
} = {}) {
  // §15.5 约束 1/2：fixedStep > 0，maxSubSteps 为正整数。
  if (!Number.isFinite(fixedStep) || fixedStep <= 0) {
    throw new FixedStepError("fixed_step_invalid", "fixedStep 必须大于 0");
  }
  if (!Number.isFinite(maxFrameDelta) || maxFrameDelta <= 0) {
    throw new FixedStepError("frame_delta_invalid", "maxFrameDelta 必须大于 0");
  }
  if (!Number.isInteger(maxSubSteps) || maxSubSteps <= 0) {
    throw new FixedStepError("sub_steps_invalid", "maxSubSteps 必须为正整数");
  }

  let accumulator = 0;
  let lastNow = null;

  function emitMetric(name, value) {
    if (typeof onMetric === "function") onMetric(name, value);
  }

  // 每帧推进。updateSimulation(fixedStep) 由调用方提供，返回本帧实际执行的 subSteps。
  function advance(nowSeconds, updateSimulation) {
    if (!Number.isFinite(nowSeconds)) {
      throw new FixedStepError("clock_required", "advance 需要有限单调秒");
    }
    if (typeof updateSimulation !== "function") {
      throw new FixedStepError("update_required", "advance 需要 updateSimulation 回调");
    }
    const rawDt = lastNow === null ? 0 : nowSeconds - lastNow;
    lastNow = nowSeconds;
    const dt = Math.min(Math.max(rawDt, 0), maxFrameDelta);
    accumulator += dt;
    // §15.5 约束 3：每帧必须重新初始化 subSteps。
    let subSteps = 0;
    while (accumulator >= fixedStep && subSteps < maxSubSteps) {
      updateSimulation(fixedStep);
      accumulator -= fixedStep;
      subSteps += 1;
    }
    // §15.5 约束 4/5：达到上限后丢弃完整积压步，只保留插值余量，杜绝 spiral of death。
    if (accumulator >= fixedStep) {
      const droppedSteps = Math.floor(accumulator / fixedStep);
      accumulator = accumulator % fixedStep;
      emitMetric(PHYSICS_STEPS_DROPPED_METRIC, droppedSteps);
    }
    return subSteps;
  }

  // 窗口隐藏或恢复后（§15.5）：清空 accumulator、重置 lastNow，不补算暂停期间物理时间。
  function reset() {
    accumulator = 0;
    lastNow = null;
  }

  return Object.freeze({
    advance,
    reset,
    get accumulator() { return accumulator; },
    get fixedStep() { return fixedStep; },
    get maxSubSteps() { return maxSubSteps; },
  });
}
