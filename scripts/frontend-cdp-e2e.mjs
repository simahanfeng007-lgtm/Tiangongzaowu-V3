#!/usr/bin/env node

const endpoint = process.env.TIANGONG_CDP_ENDPOINT || "http://127.0.0.1:9224";
const command = process.argv[2] || "inspect";
const timeoutMs = Number(process.env.TIANGONG_E2E_TIMEOUT_MS || 300000);

async function target() {
  const response = await fetch(`${endpoint}/json/list`);
  if (!response.ok) throw new Error(`CDP target discovery failed: HTTP ${response.status}`);
  const targets = await response.json();
  const page = targets.find((item) => item.type === "page" && item.url?.includes("frontend-v2/index.html"));
  if (!page?.webSocketDebuggerUrl) throw new Error("Tiangong frontend CDP target is unavailable");
  return page;
}

class CdpClient {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", () => reject(new Error("CDP WebSocket connection failed")), { once: true });
    });
    this.ws.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message || "CDP command failed"));
      else pending.resolve(message.result);
    });
  }

  call(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.call("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description || "frontend evaluation failed");
    }
    return result.result?.value;
  }

  close() {
    this.ws.close();
  }
}

const snapshotExpression = `(() => {
  const input = document.querySelector('#messageInput');
  const send = document.querySelector('#sendButton');
  const interrupt = document.querySelector('#interruptRun');
  const messages = [...document.querySelectorAll('#messages .message')];
  const last = messages.at(-1);
  const lastUser = messages.filter((node) => node.classList.contains('user')).at(-1);
  const lastAssistant = messages.filter((node) => node.classList.contains('assistant') && !node.dataset.progressBubble).at(-1);
  return {
    title: document.title,
    inputFound: Boolean(input),
    sendFound: Boolean(send),
    sendDisabled: Boolean(send?.disabled),
    busy: Boolean(interrupt && !interrupt.hidden),
    status: document.querySelector('#chatStatus')?.textContent?.trim() || '',
    userCount: messages.filter((node) => node.classList.contains('user')).length,
    assistantCount: messages.filter((node) => node.classList.contains('assistant') && !node.dataset.progressBubble).length,
    lastUserId: lastUser?.dataset?.messageId || '',
    lastAssistantId: lastAssistant?.dataset?.messageId || '',
    lastUserText: lastUser?.querySelector('.message-content')?.innerText?.trim() || '',
    lastAssistantText: lastAssistant?.querySelector('.message-content')?.innerText?.trim() || '',
    lastRole: last?.dataset?.messageRole || '',
    lastText: last?.querySelector('.message-content')?.innerText?.trim() || '',
    href: location.href,
  };
})()`;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  const page = await target();
  const client = new CdpClient(page.webSocketDebuggerUrl);
  await client.open();
  await client.call("Runtime.enable");
  try {
    if (command === "inspect") {
      console.log(JSON.stringify(await client.evaluate(snapshotExpression)));
      return;
    }
    if (command === "eval") {
      const expression = Buffer.from(process.env.TIANGONG_E2E_EXPRESSION_BASE64 || "", "base64").toString("utf8");
      if (!expression.trim()) throw new Error("TIANGONG_E2E_EXPRESSION_BASE64 is empty");
      console.log(JSON.stringify(await client.evaluate(expression)));
      return;
    }
    if (command !== "send") throw new Error(`unsupported command: ${command}`);

    const encoded = process.env.TIANGONG_E2E_PROMPT_BASE64 || "";
    const prompt = Buffer.from(encoded, "base64").toString("utf8").trim();
    if (!prompt) throw new Error("TIANGONG_E2E_PROMPT_BASE64 is empty");
    const before = await client.evaluate(snapshotExpression);
    if (!before.inputFound || !before.sendFound || before.sendDisabled) {
      throw new Error(`frontend composer unavailable: ${JSON.stringify(before)}`);
    }
    if (before.busy) throw new Error(`frontend is already busy: ${JSON.stringify(before)}`);

    const submitted = await client.evaluate(`(() => {
      const input = document.querySelector('#messageInput');
      const form = document.querySelector('#composer');
      if (!input || !form) return false;
      input.focus();
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(input, ${JSON.stringify(prompt)});
      input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: ${JSON.stringify(prompt)} }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      form.requestSubmit();
      return true;
    })()`);
    if (!submitted) throw new Error("frontend form submission failed");

    const startedAt = Date.now();
    let observedBusy = false;
    let current = before;
    while (Date.now() - startedAt < timeoutMs) {
      await sleep(1000);
      current = await client.evaluate(snapshotExpression);
      observedBusy ||= current.busy;
      // The chat DOM is bounded and evicts old messages, so its total counts
      // can stay constant even after a real user/assistant turn.  Treat the
      // actual submitted text and changed latest assistant reply as the
      // durable UI evidence, while retaining count growth as a fallback.
      const userAccepted = current.userCount > before.userCount
        || current.lastUserText === prompt
        || (current.lastUserText
          && current.lastUserText !== before.lastUserText);
      const assistantChanged = current.assistantCount > before.assistantCount
        || (current.lastAssistantId
          && current.lastAssistantId !== before.lastAssistantId)
        || (current.lastAssistantText
          && current.lastAssistantText !== before.lastAssistantText);
      if (userAccepted && assistantChanged && !current.busy) {
        console.log(JSON.stringify({ ok: true, elapsedMs: Date.now() - startedAt, observedBusy, before, after: current }));
        return;
      }
    }
    throw new Error(`frontend task timed out: ${JSON.stringify({ before, current, observedBusy, timeoutMs })}`);
  } finally {
    client.close();
  }
}

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message || String(error) }));
  process.exitCode = 1;
});
