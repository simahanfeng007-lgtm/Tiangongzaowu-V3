import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import test from "node:test";

import {
  AVATAR_STORAGE_IPC_KEY_BY_PATH,
  AVATAR_STORAGE_LAYOUT,
  createIpcStorageBackend,
} from "../app/frontend-v2/renderer/avatar/storage-adapter.mjs";

const require = createRequire(import.meta.url);
const {
  AVATAR_STORAGE_DEFINITIONS,
  AVATAR_STORAGE_KEYS,
  createAvatarStorageHost,
} = require("../app/avatar-storage-host.cjs");

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function jsonBytes(value) {
  return encoder.encode(JSON.stringify(value));
}

function parseBytes(bytes) {
  return JSON.parse(decoder.decode(bytes));
}

async function temporaryRoot(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "tiangong-avatar-storage-"));
  t.after(async () => fs.rm(root, { recursive: true, force: true }));
  return root;
}

test("host 固定三个枚举，真实磁盘原子写可可靠替换已有目标", async (t) => {
  assert.deepEqual(
    [...AVATAR_STORAGE_KEYS].sort(),
    ["pending-load-v1", "quarantine-state-v1", "registry-v1"],
  );
  const root = await temporaryRoot(t);
  const host = createAvatarStorageHost({ userDataRoot: root });

  await host.writeAtomic("registry-v1", jsonBytes({ schemaVersion: 1, revision: 1, records: {} }));
  const receipt = await host.writeAtomic(
    "registry-v1",
    jsonBytes({ schemaVersion: 1, revision: 2, records: { custom: { registryEntryVersion: 1 } } }),
  );

  assert.equal(receipt.ok, true);
  assert.equal(receipt.key, "registry-v1");
  assert.equal(JSON.stringify(receipt).includes(root), false);
  // 新宿主实例模拟 Electron 完整重启，必须从同一 userData 恢复。
  const restartedHost = createAvatarStorageHost({ userDataRoot: root });
  assert.equal(parseBytes(await restartedHost.read("registry-v1")).revision, 2);

  const directory = path.join(root, "avatar-models");
  const entries = await fs.readdir(directory);
  assert.equal(entries.some((name) => name.includes(".tmp-")), false);
});

test("host 拒绝未知 key、路径形 key 和绝对路径", async (t) => {
  const host = createAvatarStorageHost({ userDataRoot: await temporaryRoot(t) });
  for (const key of [
    "../registry-v1",
    "avatar-models/registry-v1.json",
    "C:\\Users\\x\\registry-v1.json",
    "/tmp/registry-v1.json",
    "models",
    "",
  ]) {
    await assert.rejects(
      () => host.read(key),
      (error) => error?.code === "storage_key_invalid",
    );
  }
});

test("非法 JSON、未知 schema 和超限写入在替换前失败，旧目标保持", async (t) => {
  const root = await temporaryRoot(t);
  const host = createAvatarStorageHost({ userDataRoot: root });
  const original = { schemaVersion: 1, entry: null, lastTerminal: null };
  await host.writeAtomic("pending-load-v1", jsonBytes(original));

  await assert.rejects(
    () => host.writeAtomic("pending-load-v1", encoder.encode("{broken")),
    (error) => error?.code === "storage_json_invalid",
  );
  await assert.rejects(
    () => host.writeAtomic("pending-load-v1", jsonBytes({ schemaVersion: 2, entry: null })),
    (error) => error?.code === "storage_schema_unsupported",
  );
  await assert.rejects(
    () => host.writeAtomic(
      "pending-load-v1",
      new Uint8Array(AVATAR_STORAGE_DEFINITIONS["pending-load-v1"].maxBytes + 1),
    ),
    (error) => error?.code === "storage_size_invalid",
  );

  assert.deepEqual(parseBytes(await host.read("pending-load-v1")), original);
});

test("原子替换失败保留旧文件并清理同目录 temp", async (t) => {
  const root = await temporaryRoot(t);
  const normalHost = createAvatarStorageHost({ userDataRoot: root });
  await normalHost.writeAtomic(
    "quarantine-state-v1",
    jsonBytes({ schemaVersion: 1, records: { old: true } }),
  );

  const failingHost = createAvatarStorageHost({
    userDataRoot: root,
    replaceFile: async () => {
      const error = new Error("injected replace failure");
      error.code = "EPERM";
      throw error;
    },
  });
  await assert.rejects(
    () => failingHost.writeAtomic(
      "quarantine-state-v1",
      jsonBytes({ schemaVersion: 1, records: { new: true } }),
    ),
    /injected replace failure/,
  );

  assert.deepEqual(
    parseBytes(await normalHost.read("quarantine-state-v1")),
    { schemaVersion: 1, records: { old: true } },
  );
  const directory = path.join(root, "avatar-models", "state");
  assert.equal((await fs.readdir(directory)).some((name) => name.includes(".tmp-")), false);
});

test("read 对磁盘损坏、未知 schema 和超限文件安全失败", async (t) => {
  const root = await temporaryRoot(t);
  const host = createAvatarStorageHost({ userDataRoot: root });
  const target = path.join(root, "avatar-models", "state", "pending-load-v1.json");
  await fs.mkdir(path.dirname(target), { recursive: true });

  await fs.writeFile(target, "{broken", "utf8");
  await assert.rejects(
    () => host.read("pending-load-v1"),
    (error) => error?.code === "storage_json_invalid",
  );
  await fs.writeFile(target, JSON.stringify({ schemaVersion: 99 }), "utf8");
  await assert.rejects(
    () => host.read("pending-load-v1"),
    (error) => error?.code === "storage_schema_unsupported",
  );
  await fs.writeFile(
    target,
    new Uint8Array(AVATAR_STORAGE_DEFINITIONS["pending-load-v1"].maxBytes + 1),
  );
  await assert.rejects(
    () => host.read("pending-load-v1"),
    (error) => error?.code === "storage_size_invalid",
  );
});

test("IPC backend 只把三条精确逻辑路径映射为枚举 key", async () => {
  const stored = new Map();
  const calls = [];
  const bridge = {
    async read(key) {
      calls.push(["read", key]);
      return stored.get(key) ?? null;
    },
    async writeAtomic(key, bytes) {
      calls.push(["write", key]);
      stored.set(key, new Uint8Array(bytes));
      return { ok: true, key, byteLength: bytes.byteLength };
    },
  };
  const backend = createIpcStorageBackend({ bridge });
  const registryBytes = jsonBytes({ schemaVersion: 1, revision: 1, records: {} });
  await backend.writeBytesAtomic(AVATAR_STORAGE_LAYOUT.registryFile, registryBytes);
  assert.deepEqual(parseBytes(await backend.readBytes(AVATAR_STORAGE_LAYOUT.registryFile)), {
    schemaVersion: 1,
    revision: 1,
    records: {},
  });
  assert.deepEqual(calls, [
    ["write", "registry-v1"],
    ["read", "registry-v1"],
  ]);
  assert.deepEqual(AVATAR_STORAGE_IPC_KEY_BY_PATH, {
    "avatar-models/registry-v1.json": "registry-v1",
    "avatar-models/state/pending-load-v1.json": "pending-load-v1",
    "avatar-models/state/quarantine-policy-state-v1.json": "quarantine-state-v1",
  });
});

test("IPC backend 拒绝路径变体、删除能力和伪造写入回执", async () => {
  const backend = createIpcStorageBackend({
    bridge: {
      read: async () => null,
      writeAtomic: async () => ({ ok: true, key: "wrong", byteLength: 1 }),
    },
  });
  for (const logicalPath of [
    "../avatar-models/registry-v1.json",
    "avatar-models\\registry-v1.json",
    "avatar-models/models/abc.vrm",
    "avatar-models/temp/x",
    "C:\\registry-v1.json",
  ]) {
    await assert.rejects(
      () => backend.readBytes(logicalPath),
      (error) => error?.code === "path_not_allowed",
    );
  }
  await assert.rejects(
    () => backend.remove(AVATAR_STORAGE_LAYOUT.registryFile),
    (error) => error?.code === "operation_unsupported",
  );
  await assert.rejects(
    () => backend.writeBytesAtomic(AVATAR_STORAGE_LAYOUT.registryFile, new Uint8Array([1])),
    (error) => error?.code === "bridge_response_invalid",
  );
});

test("main/preload/package 均接入窄桥且主进程使用 handleTrusted", async () => {
  const repository = path.resolve(import.meta.dirname, "..");
  const [main, preload, builder] = await Promise.all([
    fs.readFile(path.join(repository, "app", "main.js"), "utf8"),
    fs.readFile(path.join(repository, "app", "preload.js"), "utf8"),
    fs.readFile(path.join(repository, "electron-builder.config.cjs"), "utf8"),
  ]);
  assert.match(main, /require\("\.\/avatar-storage-host\.cjs"\)/);
  assert.match(main, /createAvatarStorageHost\(\{ userDataRoot: app\.getPath\("userData"\) \}\)/);
  assert.match(main, /handleTrusted\("avatarStorage:read"/);
  assert.match(main, /handleTrusted\("avatarStorage:writeAtomic"/);
  assert.match(preload, /avatarStorage: Object\.freeze\(\{/);
  assert.match(preload, /requireAvatarStorageKey\(key\)/);
  assert.match(preload, /"registry-v1": 4 \* 1024 \* 1024/);
  assert.match(preload, /"pending-load-v1": 64 \* 1024/);
  assert.match(preload, /"quarantine-state-v1": 2 \* 1024 \* 1024/);
  assert.match(preload, /"avatarStorage:read", requireAvatarStorageKey\(key\)/);
  assert.match(preload, /"avatarStorage:writeAtomic"/);
  assert.match(builder, /"avatar-storage-host\.cjs"/);
});
