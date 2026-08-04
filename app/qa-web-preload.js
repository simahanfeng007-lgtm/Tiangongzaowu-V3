const { contextBridge } = require("electron");

const runtimeErrors = [];
window.addEventListener("error", (event) => runtimeErrors.push(String(event?.error?.message || event?.message || "page error").slice(0, 500)));
window.addEventListener("unhandledrejection", (event) => runtimeErrors.push(String(event?.reason?.message || event?.reason || "unhandled rejection").slice(0, 500)));

function isVisible(element) {
  if (!element || element.disabled) return false;
  const style = getComputedStyle(element);
  return element.getClientRects().length > 0 && style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
}

contextBridge.exposeInMainWorld("__tiangongCollectWebQa", () => {
  const buttons = Array.from(document.querySelectorAll("button")).filter(isVisible).map((element) => ({
    id: String(element.id || ""),
    label: String(element.innerText || element.textContent || "").trim().replace(/\s+/g, " ").slice(0, 120),
    bound: typeof element.onclick === "function" || Boolean(element.closest("form")) || element.hasAttribute("data-action") || element.hasAttribute("data-window-action"),
  }));
  const bodyText = String(document.body?.innerText || "");
  const placeholderMatches = Array.from(new Set(bodyText.match(/(?:\bundefined\b|\bNaN\b|TODO|FIXME|稍后实现|占位符)/gi) || [])).slice(0, 20);
  const localAssets = Array.from(document.querySelectorAll("script[src],link[rel='stylesheet'][href]"))
    .map((element) => String(element.getAttribute("src") || element.getAttribute("href") || ""))
    .filter((value) => value && !/^(?:https?:|data:)/i.test(value));
  return {
    title: String(document.title || ""), readyState: String(document.readyState || ""), bodyChars: bodyText.length,
    buttons, unboundButtons: buttons.filter((item) => !item.bound), placeholderMatches, localAssets,
    runtimeErrors: runtimeErrors.slice(0, 20),
  };
});
