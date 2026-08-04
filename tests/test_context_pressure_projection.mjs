import assert from "node:assert/strict";
import { contextPressurePercent } from "../app/frontend-v2/renderer/plugins/inspector-panel.mjs";

// A deliberately full Life recall pack is not the current conversation
// window. Only the authoritative current-context counter drives pressure.
assert.equal(contextPressurePercent({
  available: true,
  token_budget: 120000,
  selected_context_tokens: 120000,
  current_context_tokens: 1200,
  context_utilization_milli: 10,
}), 1);

assert.equal(contextPressurePercent({
  available: true,
  token_budget: 120000,
  selected_context_tokens: 120000,
  current_context_tokens: 0,
  context_utilization_milli: 0,
}), 0);

assert.equal(contextPressurePercent({}, 0.42, 90), 42);
assert.equal(contextPressurePercent({}, 0, 140), 100);

console.log("context-pressure-projection: PASS");
