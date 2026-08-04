#!/usr/bin/env node

const endpoint = String(process.argv[2] || "http://127.0.0.1:9223").replace(/\/+$/, "");
const expression = String(process.env.TIANGONG_CDP_EXPRESSION || "").trim();
if (!expression) throw new Error("TIANGONG_CDP_EXPRESSION is required");

const targetsResponse = await fetch(`${endpoint}/json/list`);
if (!targetsResponse.ok) throw new Error(`cdp target list failed: ${targetsResponse.status}`);
const targets = await targetsResponse.json();
const target = targets.find((item) => item?.type === "page" && item?.webSocketDebuggerUrl);
if (!target) throw new Error("cdp page target is missing");

const socket = new WebSocket(target.webSocketDebuggerUrl);
const pending = new Map();
let nextId = 0;

socket.addEventListener("message", (event) => {
  const message = JSON.parse(String(event.data || "{}"));
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message || "cdp command failed"));
  else resolve(message.result || {});
});

await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", () => reject(new Error("cdp websocket connection failed")), { once: true });
});

function command(method, params = {}) {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

try {
  await command("Runtime.enable");
  const evaluated = await command("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
    userGesture: true,
  });
  if (evaluated.exceptionDetails) {
    const detail = evaluated.exceptionDetails.exception?.description
      || evaluated.exceptionDetails.text
      || "renderer evaluation failed";
    throw new Error(detail);
  }
  process.stdout.write(`${JSON.stringify(evaluated.result?.value ?? null)}\n`);
} finally {
  socket.close();
}
