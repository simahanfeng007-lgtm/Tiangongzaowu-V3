// P5 §8.5 自定义模型导入前端流程编排。
//
// 流程（与 §8.5 逐步对齐）：
//   chooseFile（主进程文件选择/限额/受控复制，注入桥）
//   → issueCandidateGrant（主进程签发 CandidateReadGrant opaque 视图，注入桥）
//   → readCandidateBytes（凭 grant 经受控通道读候选快照字节；生产实现走
//     AssetProvider.openValidatedStream(assetHandleForCandidate(grantView))，测试注入替身）
//   → admitVrmModel 结构预检（Worker/主线程同一 validator core，§9）
//   → LicenseGate 摘要（customImportDisplaySummary，§10.3）
//   → Redistribution_Prohibited：给明确提示文案，未确认前阻断（§10.3）
//   → commitCandidate（主进程原子移动到正式模型区，注入桥）
//   → registry.registerAsset 原子提交 → 返回 registryEntryVersion（§8.5 登记顺序）
//   → tokenIssuer.issueToken（仅提交后签发，§8.2/§8.5.5）
//   → runtime.selectModel（凭登记后的 modelId）
//
// 类型隔离纪律（§8.5/§21）：
//   grant 只允许喂 ModelAdmissionGate 预检与主进程 commit，绝不进入 AvatarEngine 输入；
//   本控制器只把 { modelId } 交给 runtime.selectModel，grant 的任何字段不下传。

import { deepFreeze } from "./canonical-hash.mjs";
import { validateCandidateGrantView } from "./candidate-read-grant.mjs";
import { admitVrmModel, VALIDATOR_VERSION } from "./model-admission-gate.mjs";
import { customImportDisplaySummary } from "./model-license-gate.mjs";
import { AdmissionState, AssetScope, computeAuthorizationFingerprint } from "./asset-registry.mjs";
import { assetHandleForCandidate, createAssetProvider } from "./asset-provider.mjs";

export const AVATAR_IMPORT_CONTROLLER_SCHEMA_VERSION = 1;
export const DEFAULT_PENDING_RESUME_LIMIT = 4;
export const DEFAULT_PENDING_RESUME_TTL_MS = 5 * 60_000;

// §10.3 Redistribution_Prohibited 明确提示文案（展示给用户，不替用户推断版权）。
export const REDISTRIBUTION_PROHIBITED_NOTICE =
  "该模型声明 Redistribution_Prohibited（禁止再分发）：仅可在本机个人使用，不得随制品分发、上传或分享。"
  + "继续导入即表示你确认仅在本机使用该模型，并自行承担许可合规责任。";

export class AvatarImportError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "AvatarImportError";
    this.code = code;
  }
}

function fail(status, code, extra = {}) {
  return deepFreeze({ status, ok: false, code, ...extra });
}

export function createAvatarImportController({
  chooseFile,
  issueCandidateGrant,
  readCandidateBytes,
  commitCandidate,
  deleteModelFile = null,
  registry,
  tokenIssuer,
  runtime,
  admissionOptions = {},
  resumeTokenFactory = null,
  nowWallClock = () => Date.now(),
  pendingResumeLimit = DEFAULT_PENDING_RESUME_LIMIT,
  pendingResumeTtlMs = DEFAULT_PENDING_RESUME_TTL_MS,
} = {}) {
  for (const [name, fn] of Object.entries({ chooseFile, issueCandidateGrant, readCandidateBytes, commitCandidate })) {
    if (typeof fn !== "function") {
      throw new AvatarImportError("dependency_invalid", `AvatarImportController 需要注入 ${name}()`);
    }
  }
  if (
    registry === null ||
    typeof registry !== "object" ||
    typeof registry.registerAsset !== "function" ||
    typeof registry.getRecord !== "function"
  ) {
    throw new AvatarImportError("dependency_invalid", "AvatarImportController 需要 AssetRegistry");
  }
  if (tokenIssuer === null || typeof tokenIssuer !== "object" || typeof tokenIssuer.issueToken !== "function") {
    throw new AvatarImportError("dependency_invalid", "AvatarImportController 需要 TokenIssuer");
  }
  if (runtime === null || typeof runtime !== "object" || typeof runtime.selectModel !== "function") {
    throw new AvatarImportError("dependency_invalid", "AvatarImportController 需要 AvatarRuntime");
  }
  if (resumeTokenFactory !== null && typeof resumeTokenFactory !== "function") {
    throw new AvatarImportError("dependency_invalid", "resumeTokenFactory 必须是函数或 null");
  }
  if (typeof nowWallClock !== "function") {
    throw new AvatarImportError("dependency_invalid", "nowWallClock 必须是函数");
  }
  if (!Number.isInteger(pendingResumeLimit) || pendingResumeLimit < 1 || pendingResumeLimit > 32) {
    throw new AvatarImportError("dependency_invalid", "pendingResumeLimit 必须是 [1,32] 整数");
  }
  if (!Number.isFinite(pendingResumeTtlMs) || pendingResumeTtlMs <= 0) {
    throw new AvatarImportError("dependency_invalid", "pendingResumeTtlMs 必须是正数");
  }

  // 许可确认续接只保存 opaque grant + 已冻结预检回执；不保存候选原始字节/路径。
  // Map 有严格容量与 TTL，token 一次性 take 后先删除再 commit，防止双击重复提交。
  const pendingResumes = new Map();
  let fallbackResumeSequence = 0;

  function sweepExpiredPending() {
    const now = nowWallClock();
    for (const [token, pending] of pendingResumes) {
      if (pending.expiresAt <= now) pendingResumes.delete(token);
    }
  }

  function makeResumeToken() {
    if (resumeTokenFactory !== null) {
      const token = resumeTokenFactory();
      if (typeof token !== "string" || token.length < 8 || token.length > 256) {
        throw new AvatarImportError("resume_token_invalid", "resumeTokenFactory 必须返回 [8,256] 字符串");
      }
      return token;
    }
    const cryptoApi = globalThis.crypto;
    if (typeof cryptoApi?.randomUUID === "function") {
      return `air_${cryptoApi.randomUUID()}`;
    }
    if (typeof cryptoApi?.getRandomValues === "function") {
      const words = new Uint32Array(4);
      cryptoApi.getRandomValues(words);
      return `air_${[...words].map((word) => word.toString(16).padStart(8, "0")).join("")}`;
    }
    fallbackResumeSequence += 1;
    return `air_local_${nowWallClock().toString(36)}_${fallbackResumeSequence.toString(36)}`;
  }

  function rememberPending({ grantView, receipt, displayName, licenseSummary }) {
    sweepExpiredPending();
    while (pendingResumes.size >= pendingResumeLimit) {
      pendingResumes.delete(pendingResumes.keys().next().value);
    }
    let resumeToken = makeResumeToken();
    for (let attempt = 0; pendingResumes.has(resumeToken) && attempt < 4; attempt += 1) {
      resumeToken = makeResumeToken();
    }
    if (pendingResumes.has(resumeToken)) {
      throw new AvatarImportError("resume_token_collision", "无法签发唯一许可续接 token");
    }
    pendingResumes.set(resumeToken, Object.freeze({
      grantView: deepFreeze({
        grantId: grantView.grantId,
        attemptId: grantView.attemptId,
        candidateId: grantView.candidateId,
        contentHash: grantView.contentHash,
        byteLength: grantView.byteLength,
        issuerEpoch: grantView.issuerEpoch,
        nonce: grantView.nonce,
        singleUse: grantView.singleUse,
      }),
      receipt,
      displayName,
      licenseSummary,
      expiresAt: nowWallClock() + pendingResumeTtlMs,
    }));
    return resumeToken;
  }

  function takePending(resumeToken) {
    sweepExpiredPending();
    if (typeof resumeToken !== "string" || resumeToken.length === 0) return null;
    const pending = pendingResumes.get(resumeToken) ?? null;
    if (pending !== null) pendingResumes.delete(resumeToken);
    return pending;
  }

  function cancelPending(resumeToken) {
    sweepExpiredPending();
    if (typeof resumeToken !== "string" || resumeToken.length === 0) return false;
    return pendingResumes.delete(resumeToken);
  }

  function listRegisteredModels() {
    if (typeof registry.listRecords !== "function") return Object.freeze([]);
    return deepFreeze(
      registry
        .listRecords()
        .filter((record) =>
          record.scope === AssetScope.MODEL &&
          record.admissionState === AdmissionState.ADMITTED)
        .map((record) => ({
          id: record.assetId,
          modelId: record.assetId,
          displayName: record.displayName || record.assetId,
          contentHash: record.contentHash,
          byteLength: record.byteLength,
          vrmSpecVersion: record.licenseRecord?.vrmSpecVersion === "1.0" ? "1.0" : "0.x",
          source: "custom",
        })),
    );
  }

  async function commitAdmittedCandidate({ grantView, receipt, displayName, licenseSummary }) {
    // 原子移动到正式模型区（主进程；grant 在此完成其唯一用途，不进入引擎）
    const committed = await commitCandidate({ grantView, receiptId: receipt.receiptId, displayName });
    if (committed === null || typeof committed !== "object" ||
        typeof committed.assetId !== "string" || committed.assetId.length === 0 ||
        typeof committed.modelId !== "string" || committed.modelId.length === 0) {
      return fail("failed", "commit_invalid", { detail: "commitCandidate 必须返回 { assetId, modelId }" });
    }
    // 当前 registry 以 assetId 为模型查找键；两者不一致会在 selectModel 后才以
    // model_unknown 失败。提交边界立即拒绝，避免写出一个前端永远不可解析的登记。
    if (committed.modelId !== committed.assetId) {
      return fail("failed", "commit_identity_mismatch", {
        detail: "commitCandidate 的 modelId 必须与 assetId 一致",
      });
    }

    const authorizationFingerprint = computeAuthorizationFingerprint({
      licenseRecord: receipt.licenseRecord,
      admissionLimits: admissionOptions.limits ?? null,
      uriPolicy: admissionOptions.uriPolicy ?? null,
      validatorVersion: VALIDATOR_VERSION,
      contentHash: receipt.contentHash,
      byteLength: receipt.byteLength,
    });
    const registration = {
      assetId: committed.assetId,
      scope: AssetScope.MODEL,
      contentHash: receipt.contentHash,
      byteLength: receipt.byteLength,
      validationReceiptId: receipt.receiptId,
      validatorVersion: VALIDATOR_VERSION,
      authorizationFingerprint,
      displayName,
      licenseRecord: receipt.licenseRecord ?? null,
      reason: "custom-import",
    };
    const reusableRecord = (record) =>
      record !== null &&
      record.scope === AssetScope.MODEL &&
      record.admissionState === AdmissionState.ADMITTED &&
      record.contentHash === registration.contentHash &&
      record.byteLength === registration.byteLength &&
      record.validationReceiptId === registration.validationReceiptId &&
      record.validatorVersion === registration.validatorVersion &&
      record.authorizationFingerprint === registration.authorizationFingerprint;

    // AssetRegistry 原子登记（§8.5：先提交，成功后才允许签发 Token）。
    // 主进程对相同 hash 的正式文件提交是幂等的；registry 同样复用完全一致且
    // 仍 admitted 的既有记录。任何授权/回执/状态漂移都拒绝，不能以“同 hash”
    // 绕过新的安全裁决。
    let record = registry.getRecord(committed.assetId);
    let reused = false;
    if (record !== null) {
      if (!reusableRecord(record)) {
        return fail("failed", "existing_asset_mismatch", {
          detail: "同 assetId 的既有登记与本次准入回执不一致或已不可用",
        });
      }
      reused = true;
    } else {
      try {
        record = await registry.registerAsset(registration);
      } catch (error) {
        // 并发导入同一 hash：另一事务可能刚完成登记。只在完整一致时收敛为复用。
        const concurrent = registry.getRecord(committed.assetId);
        if (error?.code !== "asset_id_exists" || !reusableRecord(concurrent)) throw error;
        record = concurrent;
        reused = true;
      }
    }

    // 登记提交后签发 ValidatedAssetToken（§8.2：绑定 registryEntryVersion）
    const token = tokenIssuer.issueToken(record.assetId);

    // 选择模型：只凭登记后的 modelId；grant 不下传（§8.5 类型隔离）
    const selectResult = runtime.selectModel(committed.modelId);

    return deepFreeze({
      status: "committed",
      ok: true,
      code: null,
      assetId: record.assetId,
      modelId: committed.modelId,
      registryEntryVersion: record.registryEntryVersion,
      token,
      licenseSummary,
      receiptId: receipt.receiptId,
      selectResult,
      reused,
    });
  }

  // options: { acknowledgeLicense?: boolean, resumeToken?: string }。
  // Redistribution_Prohibited 首次预检后返回一次性 token；确认只续接同一候选，不再打开文件。
  async function importCustomModel(options = {}) {
    const suppliedResumeToken =
      typeof options.resumeToken === "string"
        ? options.resumeToken
        : typeof options.resume?.resumeToken === "string"
          ? options.resume.resumeToken
          : null;
    if (suppliedResumeToken !== null) {
      if (options.acknowledgeLicense !== true) {
        return fail("failed", "license_acknowledgement_required");
      }
      const pending = takePending(suppliedResumeToken);
      if (pending === null) {
        return fail("failed", "resume_token_invalid");
      }
      return commitAdmittedCandidate(pending);
    }

    // 1. 文件选择（主进程：只做文件选择、限额、受控复制和流式 SHA-256，§8.5）
    const picked = await chooseFile();
    if (picked === null || typeof picked !== "object" || picked.canceled === true) {
      return fail("cancelled", "user_cancelled");
    }
    const displayName = typeof picked.name === "string" && picked.name.length > 0 ? picked.name : "自定义模型";

    // 2. 签发 CandidateReadGrant（opaque 视图；渲染进程永不接触 exactResolvedPath）
    const grantView = await issueCandidateGrant(picked);
    const grantErrors = validateCandidateGrantView(grantView);
    if (grantErrors.length > 0) {
      return fail("failed", "grant_invalid", { detail: grantErrors.join(",") });
    }

    // 3. 受控读取候选快照（grant 单次消费由主进程裁决）；字节数与 grant 声明必须一致
    const bytes = await readCandidateBytes(grantView);
    if (!(bytes instanceof Uint8Array) || bytes.byteLength !== grantView.byteLength) {
      return fail("failed", "candidate_bytes_mismatch", {
        detail: `候选字节长度 ${bytes?.byteLength ?? 0} != grant 声明 ${grantView.byteLength}`,
      });
    }

    // 4. 结构预检（§9 ModelAdmissionGate；与 P0 同一 validator core）
    const receipt = admitVrmModel(bytes, admissionOptions);
    if (receipt.verdict !== "ADMITTED") {
      return fail("rejected", "admission_rejected", {
        receipt,
        violations: receipt.violations,
      });
    }
    // 预检声明的 contentHash 必须与 grant 一致（§8.6 字节同一性在受控读取层已复核，
    // 这里再交叉一次，防止 grant 与实际字节错配）。
    if (receipt.contentHash !== grantView.contentHash) {
      return fail("failed", "candidate_hash_mismatch", {
        detail: `预检 contentHash 与 grant 不一致（${receipt.contentHash} != ${grantView.contentHash}）`,
      });
    }

    // 5. LicenseGate 摘要（§10.3）：Redistribution_Prohibited 给明确提示，未确认则阻断
    const licenseSummary = customImportDisplaySummary(receipt.licenseRecord);
    if (licenseSummary.redistributionProhibited && options.acknowledgeLicense !== true) {
      const resumeToken = rememberPending({ grantView, receipt, displayName, licenseSummary });
      return deepFreeze({
        status: "license-blocked",
        ok: false,
        code: "redistribution_prohibited",
        notice: REDISTRIBUTION_PROHIBITED_NOTICE,
        licenseSummary,
        receipt,
        resumeToken,
      });
    }

    return commitAdmittedCandidate({ grantView, receipt, displayName, licenseSummary });
  }

  // §8.5 删除已导入模型：只允许 scope=model 且仍 admitted 的自定义模型。
  // 先原子 tombstone（deleted 终态，registryEntryVersion +1），再删正式文件；
  // 文件清理失败不撤销 tombstone（模型已不可发现/不可加载，仅留孤儿字节）。
  async function deleteCustomModel(modelId) {
    if (typeof modelId !== "string" || modelId.length === 0) {
      return fail("failed", "model_id_invalid", { detail: "deleteModel 需要非空 modelId" });
    }
    const record = registry.getRecord(modelId);
    if (record === null) {
      return fail("failed", "model_not_found", { detail: `modelId=${modelId} 不存在` });
    }
    if (record.scope !== AssetScope.MODEL) {
      return fail("failed", "delete_forbidden_scope", {
        detail: "仅允许删除自定义导入模型（内置模型不可删除）",
      });
    }
    if (record.admissionState === AdmissionState.DELETED) {
      return fail("failed", "already_deleted", { detail: `modelId=${modelId} 已是删除终态` });
    }
    if (record.admissionState !== AdmissionState.ADMITTED) {
      return fail("failed", "delete_state_invalid", {
        detail: `modelId=${modelId} 当前状态 ${record.admissionState} 不可删除`,
      });
    }
    if (typeof deleteModelFile !== "function") {
      return fail("failed", "delete_channel_unavailable", { detail: "主进程删除通道未接线" });
    }

    // 1) 先 tombstone：原子持久化；提交失败由调用方捕获，文件保持不动。
    await registry.transitionAdmissionState(modelId, AdmissionState.DELETED, { reason: "user-delete" });

    // 2) 再删正式文件；失败不撤销 tombstone（孤儿文件不可发现）。
    let fileDeleted = null;
    let fileError = null;
    try {
      fileDeleted = await deleteModelFile({ contentHash: record.contentHash });
    } catch (error) {
      fileError = String(error?.message ?? error);
    }
    return deepFreeze({
      status: fileError === null ? "deleted" : "deleted-registry-only",
      ok: true,
      code: null,
      assetId: record.assetId,
      modelId: record.assetId,
      contentHash: record.contentHash,
      fileDeleted: fileDeleted?.deleted === true,
      fileMissing: fileDeleted?.missing === true,
      fileError,
      reason: "user-delete",
    });
  }

  return deepFreeze({
    importCustomModel,
    deleteCustomModel,
    cancelPending,
    listRegisteredModels,
    getPendingCount: () => {
      sweepExpiredPending();
      return pendingResumes.size;
    },
    redistributionProhibitedNotice: REDISTRIBUTION_PROHIBITED_NOTICE,
  });
}

// ── P6a §8.5 真实桥：chooseFile→grant→commit / delete 的 preload IPC 接线 ────
// desktop 形 { avatarImport: { chooseFile, commitCandidate, deleteModelFile },
//              avatarAsset: { issueCandidateGrant, openChannel } }（preload 窄桥；
// 全程只传 opaque 字段，绝对路径不出主进程，§8.5/§21）。
// 产物形态与 window.tiangongAvatarImport 约定一致：{ importCustomModel, deleteModel }。
export function createAvatarImportBridge({
  desktop,
  registry,
  tokenIssuer,
  runtime = null,
  getRuntime = null,
  admissionOptions = {},
  providerOptions = {},
} = {}) {
  if (desktop === null || typeof desktop !== "object" ||
      typeof desktop.avatarImport?.chooseFile !== "function" ||
      typeof desktop.avatarImport?.commitCandidate !== "function" ||
      typeof desktop.avatarImport?.deleteModelFile !== "function" ||
      typeof desktop.avatarAsset?.issueCandidateGrant !== "function" ||
      typeof desktop.avatarAsset?.openChannel !== "function") {
    throw new AvatarImportError(
      "desktop_bridge_invalid",
      "AvatarImportBridge 需要 desktop.{avatarImport.chooseFile/commitCandidate/deleteModelFile, avatarAsset.issueCandidateGrant/openChannel}",
    );
  }
  const resolveRuntime = () => {
    const instance = typeof getRuntime === "function" ? getRuntime() : runtime;
    if (instance === null || typeof instance !== "object" || typeof instance.selectModel !== "function") {
      throw new AvatarImportError("runtime_unavailable", "导入提交时 AvatarRuntime 不可用");
    }
    return instance;
  };
  // 候选字节受控读取：与 P2b 同一 AssetProvider/分块协议，重组后 SHA-256 复核（§8.6）。
  const provider = createAssetProvider({
    channelFactory: (descriptor) => desktop.avatarAsset.openChannel(descriptor),
    ...providerOptions,
  });
  const controller = createAvatarImportController({
    chooseFile: () => desktop.avatarImport.chooseFile(),
    issueCandidateGrant: (picked) =>
      desktop.avatarAsset.issueCandidateGrant({
        attemptId: picked.attemptId,
        candidateId: picked.candidateId,
        contentHash: picked.contentHash,
        byteLength: picked.byteLength,
      }),
    readCandidateBytes: async (grantView) => {
      const stream = provider.openValidatedStream(assetHandleForCandidate(grantView));
      return new Uint8Array(await stream.done);
    },
    // grant 的唯一用途到此为止；commit 只带 opaque 三元组（attemptId/contentHash/byteLength）。
    commitCandidate: ({ grantView }) =>
      desktop.avatarImport.commitCandidate({
        attemptId: grantView.attemptId,
        contentHash: grantView.contentHash,
        byteLength: grantView.byteLength,
      }),
    deleteModelFile: (payload) => desktop.avatarImport.deleteModelFile(payload),
    registry,
    tokenIssuer,
    runtime: { selectModel: (modelId) => resolveRuntime().selectModel(modelId) },
    admissionOptions,
  });
  return deepFreeze({
    importCustomModel: (options = {}) => controller.importCustomModel(options),
    deleteModel: (modelId) => controller.deleteCustomModel(modelId),
    cancelPending: (resumeToken) => controller.cancelPending(resumeToken),
    listRegisteredModels: () => controller.listRegisteredModels(),
    controller,
  });
}

// 挂到 window 约定位（avatar-panel 消费 window.tiangongAvatarImport）。返回桥便于诊断。
export function installAvatarImportBridge(target, deps) {
  const sink = target ?? (typeof window !== "undefined" ? window : null);
  if (sink === null) {
    throw new AvatarImportError("bridge_target_invalid", "installAvatarImportBridge 需要 window 形 target");
  }
  const bridge = createAvatarImportBridge(deps);
  sink.tiangongAvatarImport = bridge;
  return bridge;
}
