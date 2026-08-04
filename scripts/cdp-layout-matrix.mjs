#!/usr/bin/env node

const endpoint = String(process.argv[2] || "http://127.0.0.1:9223").replace(/\/+$/, "");
const targetsResponse = await fetch(`${endpoint}/json/list`);
if (!targetsResponse.ok) throw new Error(`CDP target list failed: ${targetsResponse.status}`);
const targets = await targetsResponse.json();
const target = targets.find((item) => item?.type === "page" && item?.webSocketDebuggerUrl);
if (!target) throw new Error("CDP page target is missing");

const socket = new WebSocket(target.webSocketDebuggerUrl);
const pending = new Map();
let nextId = 0;
socket.addEventListener("message", (event) => {
  const message = JSON.parse(String(event.data || "{}"));
  if (!message.id || !pending.has(message.id)) return;
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(message.error.message || "CDP command failed"));
  else resolve(message.result || {});
});
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", () => reject(new Error("CDP websocket connection failed")), { once: true });
});

function command(method, params = {}) {
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const response = await command("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
    userGesture: true,
  });
  if (response.exceptionDetails) {
    throw new Error(
      response.exceptionDetails.exception?.description
      || response.exceptionDetails.text
      || "renderer evaluation failed",
    );
  }
  return response.result?.value;
}

const physicalResolutions = [
  [1366, 768],
  [1920, 1080],
  [2560, 1440],
  [3840, 2160],
];
const displayScales = [1, 1.25, 1.5, 1.75, 2];
const pageZooms = [0.8, 1, 1.25, 1.5, 1.75, 2];
const fontScales = [1, 1.25, 1.5];
const failures = [];
let checks = 0;

try {
  await command("Runtime.enable");
  await command("Emulation.setFocusEmulationEnabled", { enabled: true });
  await evaluate(`(async () => {
    document.querySelector('[data-nav="lifecycle"]')?.click();
    await new Promise((resolve) => setTimeout(resolve, 1200));
    return Boolean(document.querySelector('[data-page-panel="lifecycle"]'));
  })()`);

  for (const [physicalWidth, physicalHeight] of physicalResolutions) {
    for (const displayScale of displayScales) {
      for (const pageZoom of pageZooms) {
        // Chromium page zoom changes the number of CSS pixels available to
        // layout.  CSS `zoom` does not: it visually scales fixed sidebars while
        // leaving media-query breakpoints untouched, producing impossible
        // 44px content columns.  Model the real Windows geometry instead.
        const width = Math.max(320, Math.floor(physicalWidth / (displayScale * pageZoom)));
        const height = Math.max(240, Math.floor(physicalHeight / (displayScale * pageZoom)));
        await command("Emulation.setDeviceMetricsOverride", {
          width,
          height,
          deviceScaleFactor: displayScale,
          mobile: false,
          screenWidth: physicalWidth,
          screenHeight: physicalHeight,
        });
        for (const fontScale of fontScales) {
          for (const tab of ["identity", "settings"]) {
            const result = await evaluate(`(async () => {
              document.documentElement.style.fontSize = ${JSON.stringify(`${fontScale * 100}%`)};
              document.querySelector('[data-life-tab=${JSON.stringify(tab)}]')?.click();
              const settleDeadline = performance.now() + 1000;
              do {
                await new Promise((resolve) => setTimeout(resolve, 20));
                const activeTab = document.querySelector('[data-life-tab=${JSON.stringify(tab)}].active');
                const content = document.querySelector('.life-tab-content');
                const visibleContent = content
                  && !content.classList.contains('is-switching')
                  && [...content.children].some((item) => getComputedStyle(item).display !== 'none' && item.getClientRects().length > 0);
                if (activeTab && visibleContent) break;
              } while (performance.now() < settleDeadline);
              const active = Boolean(document.querySelector('[data-life-tab=${JSON.stringify(tab)}].active'));
              const content = document.querySelector('.life-tab-content');
              const contentVisible = Boolean(content
                && !content.classList.contains('is-switching')
                && [...content.children].some((item) => getComputedStyle(item).display !== 'none' && item.getClientRects().length > 0));
              const fields = [...document.querySelectorAll('.life-setting-field')].filter((field) => {
                const style = getComputedStyle(field);
                return style.display !== 'none' && field.getClientRects().length > 0;
              });
              const violations = [];
              for (const [index, field] of fields.entries()) {
                const fieldRect = field.getBoundingClientRect();
                const children = [...field.querySelectorAll(':scope > span, :scope > input, :scope > select, :scope > textarea, :scope > small')]
                  .filter((item) => getComputedStyle(item).display !== 'none' && item.getClientRects().length > 0)
                  .map((item) => ({ tag: item.tagName, rect: item.getBoundingClientRect() }))
                  .sort((left, right) => left.rect.top - right.rect.top);
                if (!Number.isFinite(fieldRect.width) || fieldRect.width <= 0) {
                  violations.push({ index, kind: 'field_width', width: fieldRect.width });
                }
                for (const child of children) {
                  if (child.rect.left < fieldRect.left - 1 || child.rect.right > fieldRect.right + 1) {
                    violations.push({
                      index,
                      kind: 'horizontal_escape',
                      tag: child.tag,
                      field: [fieldRect.left, fieldRect.right, fieldRect.width],
                      child: [child.rect.left, child.rect.right, child.rect.width],
                    });
                  }
                }
                for (let childIndex = 1; childIndex < children.length; childIndex += 1) {
                  const previous = children[childIndex - 1];
                  const current = children[childIndex];
                  if (previous.rect.bottom > current.rect.top + 0.75) {
                    violations.push({
                      index,
                      kind: 'vertical_overlap',
                      previous: previous.tag,
                      current: current.tag,
                      overlap: previous.rect.bottom - current.rect.top,
                    });
                  }
                }
              }
              return { active, contentVisible, fields: fields.length, violations, viewport: [innerWidth, innerHeight] };
            })()`);
            checks += 1;
            if (
              !result
              || !result.active
              || !result.contentVisible
              || (tab === "identity" && result.fields < 1)
              || result.violations.length
            ) {
              failures.push({
                physical: [physicalWidth, physicalHeight],
                displayScale,
                pageZoom,
                fontScale,
                tab,
                result,
              });
            }
          }
        }
      }
    }
  }
} finally {
  try { await command("Emulation.clearDeviceMetricsOverride"); } catch {}
  try {
    await evaluate(`(() => {
      document.documentElement.style.fontSize = '';
      return true;
    })()`);
  } catch {}
  socket.close();
}

const summary = {
  ok: failures.length === 0,
  checks,
  physical_resolutions: physicalResolutions.map(([width, height]) => `${width}x${height}`),
  display_scales: displayScales,
  page_zooms: pageZooms,
  font_scales: fontScales,
  failures: failures.slice(0, 20),
};
process.stdout.write(`${JSON.stringify(summary)}\n`);
if (!summary.ok) process.exitCode = 1;
