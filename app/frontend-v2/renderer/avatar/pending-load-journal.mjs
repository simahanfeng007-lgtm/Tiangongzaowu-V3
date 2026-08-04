// §9.4 末尾 PendingLoadJournal：进入 parsing/uploading 前必须原子写入；
// committed/cancelled/rejected/failed 后清除；renderer 异常退出时供主进程崩溃归因读取。
// 写入完成（await 返回）才允许进入对应 phase；未知更高 schemaVersion 安全失败不覆盖。

import { deepFreeze } from "./canonical-hash.mjs";
import {
  AVATAR_STORAGE_LAYOUT,
  assertSchemaVersionSupported,
  readJsonFile,
  writeJsonAtomic,
} from "./storage-adapter.mjs";

export const PENDING_LOAD_JOURNAL_SCHEMA_VERSION = 1;

export const JournalPhase = Object.freeze({
  PARSING: "parsing",
  UPLOADING: "uploading",
});
export const JOURNAL_PHASES = Object.freeze(Object.values(JournalPhase));

// 终态清除原因（§22.3：完成后写入空 journal 并保留 terminal 标记）。
export const JOURNAL_TERMINAL_STATES = Object.freeze(["committed", "cancelled", "rejected", "failed"]);

export class PendingLoadJournalError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "PendingLoadJournalError";
    this.code = code;
  }
}

export async function createPendingLoadJournal({
  storage,
  journalPath = AVATAR_STORAGE_LAYOUT.pendingLoadJournalFile,
  nowWallClock = () => Date.now(),
}) {
  if (storage === null || typeof storage !== "object") {
    throw new PendingLoadJournalError("storage_invalid", "PendingLoadJournal 需要注入 storage backend");
  }
  const existing = await readJsonFile(storage, journalPath);
  assertSchemaVersionSupported(existing, PENDING_LOAD_JOURNAL_SCHEMA_VERSION, "PendingLoadJournal");
  let doc = existing ?? { schemaVersion: PENDING_LOAD_JOURNAL_SCHEMA_VERSION, entry: null, lastTerminal: null };

  async function persist(next) {
    await writeJsonAtomic(storage, journalPath, next);
    doc = next;
  }

  return {
    // §9.4：原子写入 attemptId/modelId/contentHash/engineVersion/gpuFingerprint/startedAtWallClock/phase。
    async beginPhase({ attemptId, modelId, contentHash, engineVersion, gpuFingerprint = null, phase }) {
      for (const [key, value] of Object.entries({ attemptId, modelId, contentHash, engineVersion })) {
        if (typeof value !== "string" || value.length === 0) {
          throw new PendingLoadJournalError("journal_field_invalid", `journal.${key} 必须是非空字符串`);
        }
      }
      if (!JOURNAL_PHASES.includes(phase)) {
        throw new PendingLoadJournalError("journal_phase_invalid", `phase 必须是 ${JOURNAL_PHASES.join("|")}`);
      }
      const entry = deepFreeze({
        attemptId,
        modelId,
        contentHash,
        engineVersion,
        gpuFingerprint,
        phase,
        startedAtWallClock: nowWallClock(),
      });
      await persist({ schemaVersion: PENDING_LOAD_JOURNAL_SCHEMA_VERSION, entry, lastTerminal: doc.lastTerminal });
      return entry;
    },

    // 终态清除：证据记账后写入空 journal（FIRST_VISIBLE_FRAME 提交/cancelled/rejected/failed）。
    async clearJournal({ terminalState, attemptId = null, reason = null }) {
      if (!JOURNAL_TERMINAL_STATES.includes(terminalState)) {
        throw new PendingLoadJournalError(
          "journal_terminal_invalid",
          `terminalState 必须是 ${JOURNAL_TERMINAL_STATES.join("|")}`,
        );
      }
      const lastTerminal = deepFreeze({
        terminalState,
        attemptId: attemptId ?? doc.entry?.attemptId ?? null,
        reason,
        endedAtWallClock: nowWallClock(),
      });
      await persist({ schemaVersion: PENDING_LOAD_JOURNAL_SCHEMA_VERSION, entry: null, lastTerminal });
      return lastTerminal;
    },

    // 崩溃归因读取：renderer 异常退出后，主进程在重载前读取（§9.4）。
    readPendingEntry() {
      return doc.entry;
    },
    readLastTerminal() {
      return doc.lastTerminal;
    },
  };
}
