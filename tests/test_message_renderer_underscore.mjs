import test from "node:test";
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

class FakeNode {
  constructor(tagName = "#text", text = "") {
    this.tagName = tagName.toUpperCase(); this.textContent = text;
    this.children = []; this.dataset = {}; this.style = {};
    this.classList = { add() {} };
  }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener() {}
  setAttribute(name, value) { this[name] = value; }
  set innerHTML(value) { if (value === "") this.children = []; }
  get childNodes() { return this.children; }
}
globalThis.document = {
  createElement: (tag) => new FakeNode(tag),
  createTextNode: (text) => new FakeNode("#text", text),
};
globalThis.window = {};
const rendererUrl = process.argv[2]
  ? pathToFileURL(process.argv[2])
  : new URL("../app/frontend-v2/renderer/core/message-renderer.mjs", import.meta.url);
const { renderMessageContent } = await import(rendererUrl);
function all(node) { return [node, ...node.children.flatMap(all)]; }
function visible(node) { return node.textContent + node.children.map(visible).join(""); }
function render(text) {
  const root = new FakeNode("div"); renderMessageContent(root, text); return root;
}

for (const input of [
  "创建 summarize_orders.py 和 test_summarize_orders.py，字段 order_id 与 paid_total。",
  "summarize_orders.py order_id paid_total paid_count",
  "foo_bar_baz foo__bar__baz module_name.field_value",
  "中文_字段_名称 café_au_lait имя_поля",
  "路径 D:\\task_dir\\summarize_orders.py 和 task_dir/test_orders.py",
]) {
  test(`prose identifiers retain exact underscores: ${input}`, () => {
    const tree = render(input);
    assert.equal(visible(tree), input);
    assert.equal(all(tree).filter((node) => ["EM", "STRONG"].includes(node.tagName)).length, 0);
  });
}

for (const [input, output, tag] of [
  ["_强调_", "强调", "EM"],
  ["__强调__", "强调", "STRONG"],
  ["这是 _真正强调_ 的文字", "这是 真正强调 的文字", "EM"],
  ["(_强调_)，(__加粗__)", "(强调)，(加粗)", "EM"],
  ["_foo_bar_", "foo_bar", "EM"],
  ["__paid_total__", "paid_total", "STRONG"],
  ["_one_two_ and paid_total", "one_two and paid_total", "EM"],
  ["*italic* **strong** ~~removed~~", "italic strong removed", "STRONG"],
]) {
  test(`real emphasis remains: ${input}`, () => {
    const tree = render(input);
    assert.equal(visible(tree), output);
    assert.ok(all(tree).some((node) => node.tagName === tag));
  });
}

test("whitespace and unmatched underscore delimiters remain literal", () => {
  for (const input of ["_ foo_", "_foo _", "a _ b", "filename_with_unmatched_", "foo__bar"]) {
    assert.equal(visible(render(input)), input);
  }
});

test("inline and fenced code keep literal delimiters", () => {
  const inline = render("`summarize_orders.py __paid_total__` _强调_");
  const code = all(inline).find((node) => node.tagName === "CODE");
  assert.equal(code.textContent, "summarize_orders.py __paid_total__");
  const fenced = render("```python\npaid_total = order_id\n__literal__\n```");
  assert.equal(all(fenced).find((node) => node.tagName === "CODE").textContent,
    "paid_total = order_id\n__literal__");
});

test("link labels and targets retain underscores", () => {
  const tree = render("[summarize_orders.py](https://example.com/task_dir/order_id)");
  const link = all(tree).find((node) => node.tagName === "A");
  assert.equal(visible(link), "summarize_orders.py");
  assert.equal(link.href, "https://example.com/task_dir/order_id");
});
