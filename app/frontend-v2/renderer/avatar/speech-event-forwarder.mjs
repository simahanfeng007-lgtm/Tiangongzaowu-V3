// P5 §17 TTS 事件接入：speech-start / speech-energy / speech-stop 单一所有者转发。
//
// 所有权纪律：
//   1. TTS 播放只能有一个权威所有者（现有 conversation-panel 是回复音频唯一所有者）。
//   2. AvatarRuntime 只接收转发来的语义事件（口型/呼吸/轻微头部动作/说话节奏），
//      禁止启动第二个 TTS 播放器——本模块没有任何播放能力（不创建 Audio/SpeechSynthesis），
//      只有转发，从结构上保证"第二播放器"不存在。
//   3. 事件时间戳一律用注入的前端本地单调时钟，避免系统时间变化导致口型错位（§17）；
//      事件自带的任何时间字段都被忽略。
//
// 接入方式（不侵入 conversation-panel/http-runtime）：owner 句柄由所有者显式 claim；
//   attachWindowBridge 额外提供 window CustomEvent("tiangong-speech") 订阅桥，
//   纯 addEventListener 适配，不改既有业务逻辑。

import { deepFreeze } from "./canonical-hash.mjs";

export const SPEECH_EVENT_FORWARDER_SCHEMA_VERSION = 1;

export const SpeechEventKind = Object.freeze({
  START: "speech-start",
  ENERGY: "speech-energy",
  STOP: "speech-stop",
});

export class SpeechEventForwarderError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "SpeechEventForwarderError";
    this.code = code;
  }
}

function clampEnergy(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return 0;
  return Math.max(0, Math.min(1, num));
}

export function createSpeechEventForwarder({
  nowMonotonic,
  submit, // BodyCommandScheduler.submit（或同形函数）：语音事件经调度器进入 BodyRuntimeState 唯一入口链
  energyTtlMs = 250,
  startStopTtlMs = 5_000,
} = {}) {
  if (typeof nowMonotonic !== "function") {
    throw new SpeechEventForwarderError("clock_required", "SpeechEventForwarder 需要注入单调时钟 nowMonotonic");
  }
  if (typeof submit !== "function") {
    throw new SpeechEventForwarderError("submit_invalid", "SpeechEventForwarder 需要注入 submit（BodyCommandScheduler）");
  }

  let activeOwner = null; // { ownerId, claimedAtMonotonic }
  const counters = { start: 0, energy: 0, stop: 0, rejectedNotOwner: 0 };

  function assertOwner(handle, expected) {
    if (activeOwner === null || activeOwner.ownerId !== expected) {
      counters.rejectedNotOwner += 1;
      throw new SpeechEventForwarderError(
        "speech_not_owner",
        `owner=${expected} 不是当前语音事件所有者（当前=${activeOwner?.ownerId ?? "无"}），事件拒绝转发（§17 单一所有者）`,
      );
    }
    return activeOwner;
  }

  // 事件戳：一律本地单调时钟；payload 自带时间字段不采信（§17）。
  function stamp(kind, extra = {}) {
    return deepFreeze({ kind, atMonotonic: nowMonotonic(), ...extra });
  }

  // 单一所有者申领：已有所有者在先 → 冲突报错（证明不存在第二所有者）。
  function claimOwner(ownerId) {
    const id = String(ownerId ?? "").trim();
    if (!id) {
      throw new SpeechEventForwarderError("owner_id_invalid", "claimOwner 需要非空 ownerId");
    }
    if (activeOwner !== null) {
      throw new SpeechEventForwarderError(
        "speech_owner_conflict",
        `语音事件所有者已被 ${activeOwner.ownerId} 持有（§17 单一所有者），${id} 申领被拒绝`,
      );
    }
    activeOwner = { ownerId: id, claimedAtMonotonic: nowMonotonic() };

    const handle = deepFreeze({
      ownerId: id,
      speechStart(meta = {}) {
        assertOwner(handle, id);
        counters.start += 1;
        const event = stamp(SpeechEventKind.START, {
          speaking: true,
          meta: deepFreeze({ ...(meta === null || typeof meta !== "object" ? {} : meta) }),
        });
        // 经调度器：speaking=true 语义，走 speech 通道节流纪律。
        submit({
          type: SpeechEventKind.START,
          speaking: true,
          ttlMs: startStopTtlMs,
          priority: "high",
          speechEventAtMonotonic: event.atMonotonic,
        });
        return event;
      },
      speechEnergy(energy) {
        assertOwner(handle, id);
        counters.energy += 1;
        const event = stamp(SpeechEventKind.ENERGY, { energy: clampEnergy(energy) });
        submit({
          type: SpeechEventKind.ENERGY,
          speechEnergy: event.energy,
          ttlMs: energyTtlMs,
          priority: "low",
          speechEventAtMonotonic: event.atMonotonic,
        });
        return event;
      },
      speechStop(reason = null) {
        assertOwner(handle, id);
        counters.stop += 1;
        const event = stamp(SpeechEventKind.STOP, { reason: typeof reason === "string" ? reason : null });
        submit({
          type: SpeechEventKind.STOP,
          speaking: false,
          speechEnergy: 0,
          ttlMs: startStopTtlMs,
          priority: "high",
          speechEventAtMonotonic: event.atMonotonic,
        });
        // stop 后自动释放所有权，下一段语音可重新 claim。
        if (activeOwner !== null && activeOwner.ownerId === id) activeOwner = null;
        return event;
      },
      release() {
        if (activeOwner !== null && activeOwner.ownerId === id) activeOwner = null;
      },
    });
    return handle;
  }

  // window 事件桥（非侵入订阅）：detail = { phase: "start"|"energy"|"stop", energy?, reason?, at? }
  //（P6a 起生产端统一用 phase；旧 kind 写法仍兼容兜底）。
  function attachWindowBridge({ target, ownerId = "tts-owner" } = {}) {
    if (target === null || typeof target !== "object" || typeof target.addEventListener !== "function") {
      throw new SpeechEventForwarderError("bridge_target_invalid", "attachWindowBridge 需要可 addEventListener 的 target");
    }
    let owner = null;
    const ensureOwner = () => {
      if (owner === null) owner = claimOwner(ownerId);
      return owner;
    };
    const listener = (domEvent) => {
      const detail = domEvent?.detail;
      if (detail === null || typeof detail !== "object") return;
      const kind = String(detail.phase ?? detail.kind ?? "").toLowerCase();
      try {
        if (kind === "start") ensureOwner().speechStart(detail.meta ?? {});
        else if (kind === "energy") ensureOwner().speechEnergy(detail.energy);
        else if (kind === "stop") {
          ensureOwner().speechStop(detail.reason ?? null);
          owner = null; // stop 释放所有权，下一段重新 claim
        }
      } catch (_error) {
        // 桥接事件异常不阻断业务（owner 冲突等由 counters.rejectedNotOwner 观测）
      }
    };
    target.addEventListener("tiangong-speech", listener);
    return () => {
      target.removeEventListener("tiangong-speech", listener);
      owner?.release();
      owner = null;
    };
  }

  return deepFreeze({
    claimOwner,
    attachWindowBridge,
    // 结构化证明：本模块无任何 TTS 播放能力（§17 禁止第二播放器）。
    ownsTtsPlayback: false,
    get activeOwnerId() {
      return activeOwner?.ownerId ?? null;
    },
    get counters() {
      return deepFreeze({ ...counters });
    },
  });
}
