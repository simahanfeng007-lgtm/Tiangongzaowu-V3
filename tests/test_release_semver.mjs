// P2-20 regression: version comparison must follow SemVer and reuse the
// authoritative release-binding comparator (secure-updater no longer ships a
// divergent local implementation).
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { compareSemver } = require("../app/lib/release-binding.js");

test("prerelease is older than the release", () => {
  assert.equal(compareSemver("3.0.3-beta", "3.0.3"), -1);
  assert.equal(compareSemver("3.0.3", "3.0.3-beta"), 1);
});

test("equal versions compare zero", () => {
  assert.equal(compareSemver("3.0.3", "3.0.3"), 0);
});

test("patch/minor/major ordering", () => {
  assert.equal(compareSemver("3.0.4", "3.0.3"), 1);
  assert.equal(compareSemver("3.1.0", "3.0.99"), 1);
  assert.equal(compareSemver("4.0.0", "3.999.999"), 1);
});

test("huge version numbers do not lose precision", () => {
  assert.equal(compareSemver("3.0.99999999999999999999", "3.0.99999999999999999998"), 1);
});

test("prerelease identifiers follow semver precedence", () => {
  assert.equal(compareSemver("1.0.0-alpha", "1.0.0-alpha.1"), -1);
  assert.equal(compareSemver("1.0.0-alpha.1", "1.0.0-alpha.beta"), -1);
  assert.equal(compareSemver("1.0.0-alpha.beta", "1.0.0-beta"), -1);
});
