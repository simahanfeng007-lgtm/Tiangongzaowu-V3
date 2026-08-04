const BENIGN_RESIZE_OBSERVER_MESSAGES = new Set([
  "ResizeObserver loop completed with undelivered notifications.",
  "ResizeObserver loop limit exceeded",
]);

const asText = (value, fallback = "unknown renderer error") => {
  const text = String(value ?? "").trim();
  return text || fallback;
};

export function isBenignResizeObserverMessage(value) {
  return BENIGN_RESIZE_OBSERVER_MESSAGES.has(asText(value, ""));
}

export function rendererWindowErrorDetail(event = {}) {
  return asText(
    event?.error?.stack
      || event?.error?.message
      || event?.message,
  );
}

export function rendererRejectionDetail(event = {}) {
  return asText(
    event?.reason?.stack
      || event?.reason?.message
      || event?.reason,
    "unknown renderer rejection",
  );
}

export function createRendererErrorBoundary({
  showFatal,
  writeDiagnostic = () => {},
} = {}) {
  if (typeof showFatal !== "function") {
    throw new TypeError("showFatal must be a function");
  }
  const reportedNonFatal = new Set();

  return Object.freeze({
    onWindowError(event = {}) {
      const message = asText(event?.message || event?.error?.message, "");
      const detail = rendererWindowErrorDetail(event);
      if (isBenignResizeObserverMessage(message)) {
        event?.preventDefault?.();
        if (!reportedNonFatal.has(message)) {
          reportedNonFatal.add(message);
          writeDiagnostic("renderer-resize-observer-warning", detail.slice(0, 500));
        }
        return Object.freeze({ fatal: false, code: "resize_observer_warning" });
      }
      showFatal(detail);
      return Object.freeze({ fatal: true, code: "renderer_error" });
    },

    onUnhandledRejection(event = {}) {
      showFatal(rendererRejectionDetail(event));
      return Object.freeze({ fatal: true, code: "unhandled_rejection" });
    },
  });
}
