const INCOMPLETE_PATTERNS = [
  /没有真正完成/u,
  /没有达到可交付完成标准/u,
  /未达到可交付完成标准/u,
  /已停止继续假完成/u,
  /正在自动续作/u,
  /未完成原因/u,
  /did not meet its acceptance gate/iu,
  /explicitly (?:named deliverables|requested actions) are missing/iu,
  /simple_chain_incomplete/iu,
  /请求已中断/u,
  /工具执行失败/u,
  /后端执行失败/u,
  /gateway_?request_?failed/iu,
  /backend execution failed/iu,
  /frontend task timed out/iu,
];

const AUTO_CONTINUATION_PATTERNS = [
  /正在自动续作/u,
  /无需回复[“"'']?继续/u,
  /auto[-_ ]?continu/iu,
];

export function expectsAutoContinuation(snapshot = {}) {
  const text = String(snapshot.lastAssistantText || "");
  return AUTO_CONTINUATION_PATTERNS.some((pattern) => pattern.test(text));
}

export function classifyAssistantCompletion(snapshot = {}) {
  if (snapshot.lastAssistantError) {
    return { ok: false, reason: "assistant_error_class" };
  }
  const text = String(snapshot.lastAssistantText || "");
  if (INCOMPLETE_PATTERNS.some((pattern) => pattern.test(text))) {
    return { ok: false, reason: "assistant_reported_incomplete" };
  }
  return { ok: true, reason: "assistant_finished" };
}

export function classifyProviderPreflight(settings = {}) {
  const active = settings?.optimization?.active_provider || {};
  const httpStatus = Number(active.last_http_status || 0);
  const health = String(active.health || "").trim().toLowerCase();
  const accountLevelRejection = [401, 402, 403].includes(httpStatus);
  if (health === "failed" && accountLevelRejection) {
    return {
      ok: false,
      reason: "provider_account_blocked",
      provider: String(active.provider || settings.provider || ""),
      httpStatus,
    };
  }
  return {
    ok: true,
    reason: "provider_preflight_ok",
    provider: String(active.provider || settings.provider || ""),
    httpStatus,
  };
}
