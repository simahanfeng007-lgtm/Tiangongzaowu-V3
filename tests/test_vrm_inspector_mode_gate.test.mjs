import test from "node:test";
import assert from "node:assert/strict";

import {
  shouldMountLegacyVrmPanel,
  vrmInspectorPanelPlugin,
} from "../app/frontend-v2/renderer/plugins/vrm-inspector-panel.mjs";
import {
  AVATAR_MODE_FLAG_KEY,
  AvatarRenderMode,
} from "../app/frontend-v2/renderer/avatar/avatar-service.mjs";

function withBrowserGlobals({ storage }, run) {
  const previousStorage = globalThis.localStorage;
  const previousWindow = globalThis.window;
  globalThis.localStorage = storage;
  globalThis.window = {
    addEventListener() {},
    removeEventListener() {},
    setTimeout,
  };
  try {
    return run();
  } finally {
    if (previousStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousStorage;
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
}

function createPanelStub() {
  return {
    hidden: false,
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
}

function createStateStub() {
  const snapshot = {
    activePage: "chat",
    messages: [],
    settings: {
      personaName: "起源",
      themeStyle: "ink_teal",
    },
  };
  return {
    snapshot: () => snapshot,
    on: () => () => {},
  };
}

test("legacy VRM mode gate: null flag 缺省为 direct，不插入 legacy iframe", () => {
  const storage = { getItem: () => null };
  withBrowserGlobals({ storage }, () => {
    let inserted = "";
    const slot = {
      insertAdjacentHTML(_position, html) { inserted += html; },
      querySelector() { throw new Error("direct 缺省不应查询 legacy DOM"); },
    };
    assert.equal(shouldMountLegacyVrmPanel(storage), false);
    vrmInspectorPanelPlugin.mount({ slot, state: null });
    assert.equal(inserted, "");
    assert.equal(inserted.includes("<iframe"), false);
  });
});

test("legacy VRM mode gate: 显式 legacy-iframe 才挂载两块 legacy iframe", () => {
  const storage = {
    getItem(key) {
      return key === AVATAR_MODE_FLAG_KEY ? AvatarRenderMode.LEGACY_IFRAME : null;
    },
  };
  withBrowserGlobals({ storage }, () => {
    let inserted = "";
    const homePanel = createPanelStub();
    const bodyPanel = createPanelStub();
    const slot = {
      insertAdjacentHTML(_position, html) { inserted += html; },
      querySelector(selector) {
        if (selector === '[data-vrm-panel="chat"]') return homePanel;
        if (selector === '[data-vrm-panel="body"]') return bodyPanel;
        return null;
      },
    };
    assert.equal(shouldMountLegacyVrmPanel(storage), true);
    vrmInspectorPanelPlugin.mount({ slot, state: createStateStub() });
    assert.match(inserted, /data-vrm-frame="chat"/);
    assert.match(inserted, /data-vrm-frame="body"/);
    assert.equal((inserted.match(/<iframe/g) ?? []).length, 2);
  });
});
