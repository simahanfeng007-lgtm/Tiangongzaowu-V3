import assert from "node:assert/strict";
import test from "node:test";

import { verifyFrontendModuleClosure } from "../scripts/verify-frontend-module-closure.mjs";

function fixture(files) {
  const normalized = new Map(Object.entries(files));
  return {
    packagedFiles: new Set(normalized.keys()),
    readText: (path) => {
      if (!normalized.has(path)) throw new Error(`unexpected read: ${path}`);
      return normalized.get(path);
    },
  };
}

test("packaged frontend closure rejects a transitively missing local module", () => {
  const archive = fixture({
    "/frontend-v2/index.html": '<script type="module" src="./renderer/app.mjs"></script>',
    "/frontend-v2/renderer/app.mjs": 'import { plugins } from "./plugins/index.mjs";',
    "/frontend-v2/renderer/plugins/index.mjs": 'import "./lifecycle-panel.mjs";',
  });
  assert.throws(
    () => verifyFrontendModuleClosure(archive),
    /packaged frontend module dependency is missing: \/frontend-v2\/renderer\/plugins\/lifecycle-panel\.mjs/,
  );
});

test("packaged frontend closure follows module entries, import maps, and dynamic imports", () => {
  const archive = fixture({
    "/frontend-v2/index.html": `
      <script type="importmap">{"imports":{"widget/":"../vendor/widget/"}}</script>
      <script src="./renderer/app.mjs" type="module"></script>
    `,
    "/frontend-v2/renderer/app.mjs": `
      import { mount } from "widget/index.mjs";
      export { ready } from "./ready.mjs";
      void import("./lazy.mjs");
      mount();
    `,
    "/frontend-v2/renderer/ready.mjs": "export const ready = true;",
    "/frontend-v2/renderer/lazy.mjs": "export const lazy = true;",
    "/vendor/widget/index.mjs": "export const mount = () => {};",
  });
  assert.deepEqual(
    verifyFrontendModuleClosure(archive),
    { entryCount: 1, moduleCount: 4 },
  );
});
