import assert from "node:assert/strict";

class FakeNode {
  constructor(tagName = "#text", text = "") {
    this.tagName = String(tagName).toUpperCase();
    this.textContent = text;
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.listeners = new Map();
    this.className = "";
    this.classList = {
      add: (...names) => {
        const values = new Set(this.className.split(/\s+/).filter(Boolean));
        names.forEach((name) => values.add(name));
        this.className = [...values].join(" ");
      },
    };
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  addEventListener(type, listener) {
    const rows = this.listeners.get(type) || [];
    rows.push(listener);
    this.listeners.set(type, rows);
  }

  setAttribute(name, value) {
    this[name] = String(value);
  }

  set innerHTML(value) {
    if (value === "") this.children = [];
  }

  get childNodes() {
    return this.children;
  }
}

function descendants(node) {
  return [node, ...node.children.flatMap((child) => descendants(child))];
}

globalThis.document = {
  createElement: (tagName) => new FakeNode(tagName),
  createTextNode: (text) => new FakeNode("#text", String(text)),
  body: new FakeNode("body"),
};

const externalCalls = [];
globalThis.window = {
  setTimeout: () => 0,
  tiangongDesktop: {
    openExternal: async (url) => {
      externalCalls.push(url);
      return { ok: true };
    },
  },
};

const { renderMessageContent } = await import(
  "../app/frontend-v2/renderer/core/message-renderer.mjs"
);

const mediaContainer = new FakeNode("div");
renderMessageContent(mediaContainer, "MEDIA:C:\\work\\交付\\成品.png");
const mediaNodes = descendants(mediaContainer);
assert.equal(mediaNodes.filter((node) => node.tagName === "IMG").length, 1);
assert.equal(mediaNodes.filter((node) => node.tagName === "FIGURE").length, 1);
assert.equal(
  mediaNodes.some((node) => String(node.textContent || "").includes("MEDIA:")),
  false,
);
assert.equal(
  mediaNodes.some((node) => node.tagName === "BUTTON" && node.textContent === "保存"),
  true,
);

const linkContainer = new FakeNode("div");
renderMessageContent(linkContainer, "[官网](https://example.com/docs)");
const link = descendants(linkContainer).find((node) => node.tagName === "A");
assert.ok(link);
assert.equal(link.rel, "noreferrer noopener");
let prevented = false;
for (const listener of link.listeners.get("click") || []) {
  await listener({ preventDefault: () => { prevented = true; } });
}
await Promise.resolve();
assert.equal(prevented, true);
assert.deepEqual(externalCalls, ["https://example.com/docs"]);

console.log("media renderer contract tests passed");
