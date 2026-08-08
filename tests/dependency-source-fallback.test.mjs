import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  ELECTRON_FALLBACK_MIRROR,
  NPM_FALLBACK_REGISTRY,
} from "../scripts/install-node-dependencies.mjs";

test("dependency fallbacks are explicit and do not replace upstream defaults", () => {
  assert.equal(NPM_FALLBACK_REGISTRY, "https://registry.npmmirror.com");
  assert.equal(ELECTRON_FALLBACK_MIRROR, "https://npmmirror.com/mirrors/electron/");
  const pythonInstaller = readFileSync(
    new URL("../scripts/install-python-dependencies.py", import.meta.url),
    "utf8",
  );
  assert.match(
    pythonInstaller,
    /https:\/\/mirrors\.tuna\.tsinghua\.edu\.cn\/pypi\/web\/simple/,
  );
  assert.match(pythonInstaller, /PIP_EXTRA_INDEX_URL/);
  assert.match(pythonInstaller, /TIANGONG_DISABLE_DEPENDENCY_FALLBACK/);
});
