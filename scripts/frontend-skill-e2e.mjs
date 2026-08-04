#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import {
  classifyAssistantCompletion,
  classifyProviderPreflight,
  expectsAutoContinuation,
} from "./lib/frontend-skill-e2e-classifier.mjs";

const endpoint = process.env.TIANGONG_CDP_ENDPOINT || "http://127.0.0.1:9222";
const manifestPath = path.resolve(process.argv[2] || "tests/fixtures/frontend-skill-e2e-cases.json");
const outputDir = path.resolve(process.env.TIANGONG_E2E_OUTPUT_DIR || "output/playwright/skill-e2e");
const firstCase = Math.max(1, Number(process.env.TIANGONG_E2E_START || 1));
const lastCase = Math.max(firstCase, Number(process.env.TIANGONG_E2E_END || Number.MAX_SAFE_INTEGER));
const defaultTimeoutMs = Math.max(30000, Number(process.env.TIANGONG_E2E_TIMEOUT_MS || 300000));
const minimumTimeoutMs = Math.max(30000, Number(process.env.TIANGONG_E2E_MIN_TIMEOUT_MS || 30000));
const allowUnhealthyProvider = process.env.TIANGONG_E2E_ALLOW_UNHEALTHY_PROVIDER === "1";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function frontendTarget() {
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
    const response = await this.call("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (response.exceptionDetails) {
      throw new Error(response.exceptionDetails.exception?.description || "frontend evaluation failed");
    }
    return response.result?.value;
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
  const users = messages.filter((node) => node.classList.contains('user'));
  const assistants = messages.filter((node) => node.classList.contains('assistant') && !node.dataset.progressBubble);
  const last = messages.at(-1);
  const text = (node) => node?.querySelector('.message-content')?.innerText?.trim() || '';
  return {
    title: document.title,
    inputFound: Boolean(input),
    sendFound: Boolean(send),
    sendDisabled: Boolean(send?.disabled),
    busy: Boolean(interrupt && !interrupt.hidden),
    status: document.querySelector('#chatStatus')?.textContent?.trim() || '',
    userCount: users.length,
    assistantCount: assistants.length,
    lastUserId: users.at(-1)?.dataset?.messageId || '',
    lastAssistantId: assistants.at(-1)?.dataset?.messageId || '',
    lastAssistantRequestId: assistants.at(-1)?.dataset?.requestId || '',
    lastAssistantWorkCard: Boolean(assistants.at(-1)?.querySelector('.message-content.work-card')),
    lastAssistantError: Boolean(assistants.at(-1)?.classList.contains('error')),
    lastAssistantClassName: assistants.at(-1)?.className || '',
    lastUserIndex: messages.indexOf(users.at(-1)),
    lastAssistantIndex: messages.indexOf(assistants.at(-1)),
    lastUserText: text(users.at(-1)),
    lastAssistantText: text(assistants.at(-1)),
    lastRole: last?.dataset?.messageRole || '',
    lastText: text(last),
    href: location.href,
  };
})()`;

const providerSettingsExpression = `(async () => {
  try {
    if (typeof window.tiangongDesktop?.getModelSettings !== "function") return {};
    return await window.tiangongDesktop.getModelSettings();
  } catch (error) {
    return { preflight_error: error?.message || String(error) };
  }
})()`;

async function captureScreenshot(client, filename) {
  const result = await client.call("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: false,
  });
  fs.writeFileSync(path.join(outputDir, filename), Buffer.from(result.data, "base64"));
}

async function waitForStableComposer(client, timeoutMs = 120000) {
  const startedAt = Date.now();
  let stableCount = 0;
  let current = await client.evaluate(snapshotExpression);
  while (Date.now() - startedAt < timeoutMs) {
    const ready = current.inputFound
      && current.sendFound
      && !current.sendDisabled
      && !current.busy;
    stableCount = ready ? stableCount + 1 : 0;
    if (stableCount >= 3) return current;
    await sleep(1000);
    current = await client.evaluate(snapshotExpression);
  }
  throw new Error(`frontend composer did not become stably idle: ${JSON.stringify(current)}`);
}

async function submitCase(client, testCase, ordinal) {
  const before = await waitForStableComposer(client);
  const basePrompt = String(testCase.prompt || "").trim();
  if (!basePrompt) throw new Error(`case ${testCase.id || ordinal} has no prompt`);
  // Request IDs are content-derived in parts of the desktop continuity path.
  // A unique visible suffix makes a rerun a new human turn instead of an
  // idempotent replay of an earlier failed smoke case.
  const prompt = `${basePrompt}\n本次前端运行标识：E2E-${Date.now()}-${ordinal}`;
  const submitted = await client.evaluate(`(() => {
    const input = document.querySelector('#messageInput');
    const form = document.querySelector('#composer');
    if (!input || !form) return false;
    input.focus();
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
    setter.call(input, ${JSON.stringify(prompt)});
    input.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      inputType: 'insertText',
      data: ${JSON.stringify(prompt)},
    }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    form.requestSubmit();
    return true;
  })()`);
  if (!submitted) throw new Error("frontend form submission failed");

  const startedAt = Date.now();
  const timeoutMs = Math.max(
    30000,
    minimumTimeoutMs,
    Number(testCase.timeoutMs || defaultTimeoutMs),
  );
  let observedBusy = false;
  let submittedRequestId = "";
  let continuationGrace = null;
  let current = before;
  while (Date.now() - startedAt < timeoutMs) {
    await sleep(1000);
    current = await client.evaluate(snapshotExpression);
    observedBusy ||= current.busy;
    const userAccepted = current.userCount > before.userCount
      || current.lastUserText === prompt
      || (current.lastUserText && current.lastUserText !== before.lastUserText);
    const assistantChanged = current.assistantCount > before.assistantCount
      || (current.lastAssistantId && current.lastAssistantId !== before.lastAssistantId)
      || (current.lastAssistantText && current.lastAssistantText !== before.lastAssistantText);
    const assistantFollowsUser = current.lastAssistantIndex > current.lastUserIndex;
    if (
      userAccepted
      && assistantChanged
      && assistantFollowsUser
      && current.lastAssistantRequestId
      && current.lastAssistantRequestId !== before.lastAssistantRequestId
    ) {
      // A single human turn may legitimately span several renderer requests:
      // the delivery framework schedules a hidden auto-continuation after a
      // checkpoint.  While the submitted user message remains the latest user
      // turn, follow the newest assistant work card as the same delivery
      // lineage instead of freezing on the first checkpoint request.
      if (
        submittedRequestId
        && current.lastAssistantRequestId !== submittedRequestId
      ) {
        continuationGrace = null;
      }
      submittedRequestId = current.lastAssistantRequestId;
    }
    const submittedAssistantIsLast = submittedRequestId
      && current.lastAssistantRequestId === submittedRequestId;
    if (
      userAccepted
      && assistantChanged
      && assistantFollowsUser
      && submittedAssistantIsLast
      && !current.lastAssistantWorkCard
    ) {
      await sleep(2500);
      const confirmed = await client.evaluate(snapshotExpression);
      if (
        confirmed.lastAssistantRequestId !== submittedRequestId
        || confirmed.lastAssistantWorkCard
      ) {
        current = confirmed;
        continue;
      }
      const completion = classifyAssistantCompletion(confirmed);
      if (!completion.ok && expectsAutoContinuation(confirmed)) {
        if (!continuationGrace || continuationGrace.requestId !== submittedRequestId) {
          continuationGrace = {
            requestId: submittedRequestId,
            deadline: Date.now() + 20000,
          };
        }
        if (Date.now() < continuationGrace.deadline) {
          current = confirmed;
          continue;
        }
      }
      return {
        ok: completion.ok,
        id: testCase.id,
        skillId: testCase.skillId,
        elapsedMs: Date.now() - startedAt,
        observedBusy,
        requestId: submittedRequestId,
        completionReason: completion.reason,
        error: completion.ok ? undefined : "frontend assistant reported incomplete or error",
        before,
        after: confirmed,
      };
    }
  }
  return {
    ok: false,
    id: testCase.id,
    skillId: testCase.skillId,
    elapsedMs: Date.now() - startedAt,
    observedBusy,
    error: "frontend task timed out",
    before,
    after: current,
  };
}

async function main() {
  const parsed = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const allCases = Array.isArray(parsed) ? parsed : parsed.cases;
  if (!Array.isArray(allCases) || !allCases.length) throw new Error("skill E2E manifest has no cases");
  const selected = allCases.filter((_item, index) => index + 1 >= firstCase && index + 1 <= lastCase);
  fs.mkdirSync(outputDir, { recursive: true });
  const reportPath = path.join(outputDir, "report.ndjson");
  const target = await frontendTarget();
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.open();
  await client.call("Runtime.enable");
  await client.call("Page.enable");
  const results = [];
  try {
    for (const testCase of selected) {
      const ordinal = allCases.indexOf(testCase) + 1;
      let result;
      let haltAfterResult = false;
      const providerSettings = await client.evaluate(providerSettingsExpression);
      const providerPreflight = classifyProviderPreflight(providerSettings);
      if (!allowUnhealthyProvider && !providerPreflight.ok) {
        result = {
          ok: false,
          id: testCase.id,
          skillId: testCase.skillId,
          infrastructureBlocked: true,
          completionReason: providerPreflight.reason,
          error: `${providerPreflight.reason}: ${providerPreflight.provider || "active_provider"} HTTP ${providerPreflight.httpStatus}`,
          providerPreflight,
        };
        haltAfterResult = true;
      } else {
        try {
          result = await submitCase(client, testCase, ordinal);
        } catch (error) {
          result = {
            ok: false,
            id: testCase.id,
            skillId: testCase.skillId,
            error: error?.message || String(error),
          };
        }
      }
      const screenshot = `${String(ordinal).padStart(2, "0")}-${testCase.id}.png`;
      try {
        await captureScreenshot(client, screenshot);
        result.screenshot = screenshot;
      } catch (error) {
        result.screenshotError = error?.message || String(error);
      }
      fs.appendFileSync(reportPath, `${JSON.stringify(result)}\n`, "utf8");
      process.stdout.write(`${JSON.stringify({
        ordinal,
        id: result.id,
        skillId: result.skillId,
        ok: result.ok,
        elapsedMs: result.elapsedMs,
        error: result.error,
        lastAssistantText: result.after?.lastAssistantText?.slice(0, 500),
        screenshot: result.screenshot,
      })}\n`);
      results.push(result);
      if (haltAfterResult) break;
      if (!result.ok && result.after?.busy) {
        await client.evaluate(`(() => {
          const interrupt = document.querySelector('#interruptRun');
          if (interrupt && !interrupt.hidden) interrupt.click();
          return true;
        })()`);
        await sleep(3000);
      }
      await sleep(1000);
    }
  } finally {
    client.close();
  }
  const failed = results.filter((item) => !item.ok);
  fs.writeFileSync(
    path.join(outputDir, "summary.json"),
    JSON.stringify({
      schema: "tiangong.frontend-skill-e2e.v1",
      manifestPath,
      selected: results.length,
      passed: results.length - failed.length,
      failed: failed.length,
      results,
    }, null, 2),
    "utf8",
  );
  if (failed.length) process.exitCode = 2;
}

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error?.stack || error?.message || String(error) }));
  process.exitCode = 1;
});
