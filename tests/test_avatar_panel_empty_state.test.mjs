import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  describeAvatarImportResult,
  describeAvatarProjection,
  mergeAvatarCatalog,
} from "../app/frontend-v2/renderer/plugins/avatar-panel.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));

test("avatar panel: clean import-only profile exposes a guided empty state", () => {
  const presentation = describeAvatarProjection({
    runtimeState: "uninitialized",
    currentModel: null,
    pending: null,
    lastRequestedModelId: null,
    lastCommittedModelId: null,
  });

  assert.equal(presentation.selectedModelId, null);
  assert.equal(presentation.modelText, "等待导入");
  assert.equal(presentation.stateText, "等待导入");
  assert.equal(presentation.emptyVisible, true);
  assert.equal(presentation.emptyTitle, "尚未添加身体模型");
  assert.match(presentation.emptyHint, /身体.*VRM/);
});

test("avatar panel: pending and committed models never masquerade as an empty catalog", () => {
  const pending = describeAvatarProjection({
    runtimeState: "running",
    currentModel: null,
    pending: { attemptId: "att_panel_1" },
    lastRequestedModelId: "model:pending",
    lastCommittedModelId: null,
  });
  assert.equal(pending.selectedModelId, "model:pending");
  assert.equal(pending.stateText, "正在加载");
  assert.equal(pending.emptyVisible, true);
  assert.equal(pending.emptyTitle, "正在加载身体模型");

  const committed = describeAvatarProjection({
    runtimeState: "running",
    currentModel: {
      modelId: "model:committed",
      label: "已登记模型",
      contentHash: "a".repeat(64),
    },
    pending: null,
    lastRequestedModelId: "model:committed",
    lastCommittedModelId: "model:committed",
  });
  assert.equal(committed.selectedModelId, "model:committed");
  assert.equal(committed.modelText, "已登记模型");
  assert.equal(committed.stateText, "running");
  assert.equal(committed.emptyVisible, false);
});

test("avatar panel: file-picker cancellation is neutral and never leaks user_cancelled", () => {
  for (const result of [
    { status: "cancelled", ok: false, code: "user_cancelled" },
    { status: "failed", ok: false, code: "user_cancelled" },
  ]) {
    const status = describeAvatarImportResult(result);
    assert.equal(status.message, "已取消选择");
    assert.equal(status.state, "");
    assert.doesNotMatch(status.message, /user_cancelled/);
  }

  const failure = describeAvatarImportResult({
    status: "failed",
    ok: false,
    code: "admission_rejected",
  });
  assert.equal(failure.message, "admission_rejected");
  assert.equal(failure.state, "error");
});

test("avatar panel: catalog merge does not synthesize a selected model", () => {
  const catalog = mergeAvatarCatalog(
    [],
    [{ id: "model:registered", displayName: "已登记但未选择" }],
  );
  assert.equal(catalog.length, 1);

  const presentation = describeAvatarProjection({
    runtimeState: "uninitialized",
    currentModel: null,
    pending: null,
    lastRequestedModelId: null,
    lastCommittedModelId: null,
  });
  assert.equal(presentation.selectedModelId, null);
  assert.equal(presentation.emptyVisible, true);
});

test("avatar panel: chat viewport is an edge-to-edge rounded stage, not a nested card", () => {
  const css = readFileSync(
    `${root}/app/frontend-v2/styles/vrm-inspector.css`,
    "utf8",
  );

  assert.match(
    css,
    /body \.app-shell\[data-page="chat"\] \.inspector \{[\s\S]*?scrollbar-gutter: auto;/,
    "chat inspector should not reserve an empty scrollbar strip beside the VRM stage",
  );
  assert.match(
    css,
    /body \.app-shell\[data-page="chat"\] \.inspector > \.vrm-home-panel \{[\s\S]*?padding: 0;[\s\S]*?border-radius: 0 0 var\(--material-panel-radius\) var\(--material-panel-radius\);/,
  );
  assert.match(
    css,
    /body \.app-shell\[data-page="chat"\] \.vrm-home-panel \.vrm-panel-header \{[\s\S]*?position: absolute;[\s\S]*?border: 0;[\s\S]*?box-shadow: none;/,
  );
  assert.match(
    css,
    /body \.app-shell\[data-page="chat"\] \.vrm-home-panel \.vrm-viewport-card,[\s\S]*?border: 0;[\s\S]*?border-radius: 0 0 var\(--material-panel-radius\) var\(--material-panel-radius\);/,
  );
  assert.doesNotMatch(
    css,
    /body \.app-shell\[data-page="body"\][\s\S]*?position: absolute;/,
  );
});
