// Avatar P1+P2a+P2b+P4 统一导出：Core 服务与生命周期契约 + 安全资源层 + 受控传输消费端 + 运行时。

export * from "./contracts.mjs";
export * from "./service-registry.mjs";
export * from "./lifecycle.mjs";
export * from "./load-attempt.mjs";
export * from "./runtime-state.mjs";
export * from "./fixed-step.mjs";
export * from "./canonical-hash.mjs";
export * from "./model-license-gate.mjs";
export * from "./model-admission-gate.mjs";
export * from "./storage-adapter.mjs";
export * from "./asset-registry.mjs";
export * from "./validated-asset-token.mjs";
export * from "./model-quarantine.mjs";
export * from "./pending-load-journal.mjs";
export * from "./asset-provider.mjs";
export * from "./candidate-read-grant.mjs";
// P4：AvatarRuntime（状态机 + 资源估算 + 有条件事务切换 + RenderSurface + 诊断 + 恢复）
export * from "./model-resource-estimator.mjs";
export * from "./render-surface-controller.mjs";
export * from "./visibility-probe.mjs";
export * from "./suspension-guard.mjs";
export * from "./body-runtime-state.mjs";
export * from "./diagnostics.mjs";
export * from "./recovery-controller.mjs";
export * from "./avatar-runtime.mjs";
// P5：业务接入（动作协议/调度/profile/服务模式/状态链/主题/TTS/导入）
export * from "./body-performance-adapter.mjs";
export * from "./body-command-scheduler.mjs";
export * from "./body-runtime-profile.mjs";
export * from "./avatar-service.mjs";
export * from "./avatar-store.mjs";
export * from "./theme-presentation.mjs";
export * from "./presentation-settings.mjs";
export * from "./speech-event-forwarder.mjs";
export * from "./avatar-import-controller.mjs";
