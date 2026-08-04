// §8.5 CandidateReadGrant 渲染侧记录：只持 opaque 字段、单次消费、epoch 校验。
// 类型隔离：grant 只喂 ModelAdmissionGate 预检，本模块不提供任何将其转换为
// AvatarEngine 输入的通路；任何携带路径字段的视图直接拒绝（渲染进程永不接触
// exactResolvedPath/绝对路径，§8.5/§21）。

import { deepFreeze } from "./canonical-hash.mjs";

export class CandidateGrantError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "CandidateGrantError";
    this.code = code;
  }
}

const HEX64 = /^[0-9a-f]{64}$/;
const MAX_GRANT_FIELD_LENGTH = 512;

// 禁止出现在视图中的路径语义键（小写比较）。
const FORBIDDEN_PATH_KEYS = Object.freeze([
  "exactresolvedpath",
  "resolvedpath",
  "absolutepath",
  "filepath",
  "fullpath",
  "realpath",
  "path",
  "dir",
  "directory",
]);
// POSIX 绝对路径 / Windows 盘符路径 / UNC。
const ABSOLUTE_PATH_PATTERN = /^(\/|\\\\|[a-zA-Z]:[\\/])/;

function scanForPathLeak(value) {
  if (value === null || typeof value !== "object") {
    return typeof value === "string" && ABSOLUTE_PATH_PATTERN.test(value);
  }
  if (Array.isArray(value)) return value.some(scanForPathLeak);
  for (const key of Object.keys(value)) {
    if (FORBIDDEN_PATH_KEYS.includes(key.toLowerCase())) return true;
    if (scanForPathLeak(value[key])) return true;
  }
  return false;
}

// 结构校验：返回错误码列表，空数组表示通过。
export function validateCandidateGrantView(view) {
  const errors = [];
  if (view === null || typeof view !== "object") return ["grant_not_object"];
  for (const field of ["grantId", "attemptId", "candidateId", "nonce"]) {
    if (typeof view[field] !== "string" || view[field].length === 0 || view[field].length > MAX_GRANT_FIELD_LENGTH) {
      errors.push(`${field}_invalid`);
    }
  }
  if (typeof view.contentHash !== "string" || !HEX64.test(view.contentHash)) errors.push("content_hash_invalid");
  if (!Number.isInteger(view.byteLength) || view.byteLength <= 0) errors.push("byte_length_invalid");
  if (!Number.isInteger(view.issuerEpoch) || view.issuerEpoch < 0) errors.push("issuer_epoch_invalid");
  if (typeof view.singleUse !== "boolean") errors.push("single_use_invalid");
  if (errors.length === 0 && scanForPathLeak(view)) errors.push("grant_path_leak");
  return errors;
}

// 渲染侧 tracker：登记 opaque 视图、epoch 一致性、单次消费。
// issuerEpoch 可显式注入；为 null 时采用"首个登记 grant 的 epoch 后续强制一致"策略
//（进程重启后旧 epoch 的 grant 必然与新登记的不一致而失效，§8.5）。
export function createCandidateGrantTracker({ issuerEpoch = null } = {}) {
  if (issuerEpoch !== null && (!Number.isInteger(issuerEpoch) || issuerEpoch < 0)) {
    throw new CandidateGrantError("issuer_epoch_invalid", "issuerEpoch 必须是非负整数或 null");
  }
  let epoch = issuerEpoch;
  const known = new Map();
  const consumed = new Set();

  function assertEpoch(view) {
    if (epoch === null) epoch = view.issuerEpoch;
    if (view.issuerEpoch !== epoch) {
      throw new CandidateGrantError(
        "grant_epoch_mismatch",
        `grant issuerEpoch=${view.issuerEpoch} 与当前 epoch=${epoch} 不一致，立即失效`,
      );
    }
  }

  return {
    get issuerEpoch() {
      return epoch;
    },

    registerGrant(view) {
      const errors = validateCandidateGrantView(view);
      if (errors.length > 0) {
        throw new CandidateGrantError(errors[0], `CandidateReadGrant 视图非法: ${errors.join(",")}`);
      }
      assertEpoch(view);
      const frozen = deepFreeze({
        grantId: view.grantId,
        attemptId: view.attemptId,
        candidateId: view.candidateId,
        contentHash: view.contentHash,
        byteLength: view.byteLength,
        issuerEpoch: view.issuerEpoch,
        nonce: view.nonce,
        singleUse: view.singleUse,
      });
      known.set(frozen.grantId, frozen);
      return frozen;
    },

    // 单次消费：second consume 拒绝；消费后渲染侧仅保留 opaque 视图。
    consumeGrant(grantId) {
      const view = known.get(String(grantId || ""));
      if (!view) throw new CandidateGrantError("grant_not_found", "CandidateReadGrant 未登记");
      if (view.issuerEpoch !== epoch) {
        throw new CandidateGrantError("grant_epoch_mismatch", "CandidateReadGrant epoch 已失效");
      }
      if (view.singleUse && consumed.has(view.grantId)) {
        throw new CandidateGrantError("grant_consumed", "CandidateReadGrant 单次消费后已失效");
      }
      consumed.add(view.grantId);
      return view;
    },

    isConsumed(grantId) {
      return consumed.has(String(grantId || ""));
    },

    get trackedCount() {
      return known.size;
    },
  };
}

// 类型隔离哨兵（§8.5）：grant 只允许作为 ModelAdmissionGate 预检输入；
// 返回值标记 forAdmissionPrecheckOnly，不含任何可加载/可解析引擎输入的字段。
export function assertGrantForAdmissionPrecheck(view) {
  const errors = validateCandidateGrantView(view);
  if (errors.length > 0) {
    throw new CandidateGrantError(errors[0], `CandidateReadGrant 不能用于准入预检: ${errors.join(",")}`);
  }
  return deepFreeze({
    forAdmissionPrecheckOnly: true,
    grantId: view.grantId,
    attemptId: view.attemptId,
    candidateId: view.candidateId,
    contentHash: view.contentHash,
    byteLength: view.byteLength,
  });
}
