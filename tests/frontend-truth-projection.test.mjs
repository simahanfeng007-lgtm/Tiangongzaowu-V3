import test from "node:test";
import assert from "node:assert/strict";

import {
  historicalKindRemainsHistorical,
  projectMessageKind,
  projectRunStatus,
} from "../app/frontend-v2/renderer/core/truth-projection.mjs";

test("T20 unknown run state never defaults to ready or completed", () => {
  for (const raw of [null, undefined, "", "unknown", "weird", 0, false]) {
    assert.equal(projectRunStatus(raw), "unknown");
  }
  assert.equal(projectRunStatus("ready"), "ready");
  assert.equal(projectRunStatus("completed"), "completed");
});

test("T20 unknown message kind never projects as assistant", () => {
  for (const kind of ["status", "system_status", "unknown", "", null]) {
    assert.equal(projectMessageKind({ kind }), "unknown");
  }
  assert.equal(projectMessageKind({ kind: "assistant" }), "assistant");
  assert.equal(projectMessageKind({ role: "user" }), "user");
  assert.equal(projectMessageKind({ role: "system" }), "system");
});

test("T20 historical stays historical; unknown previews are suppressed", () => {
  assert.equal(historicalKindRemainsHistorical("assistant"), "assistant");
  assert.equal(historicalKindRemainsHistorical("user"), "user");
  assert.equal(historicalKindRemainsHistorical("system"), "system");
  assert.equal(historicalKindRemainsHistorical(""), "unknown");
});
