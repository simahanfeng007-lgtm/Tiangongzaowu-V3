import assert from "node:assert/strict";

import { createActions } from "../app/frontend-v2/renderer/core/actions.mjs";
import { createState } from "../app/frontend-v2/renderer/core/state.mjs";
import { createHttpRuntime } from "../app/frontend-v2/renderer/runtime/http-runtime.mjs";
import {
  applyProviderPreset,
  providerThinkingCapability,
} from "../app/frontend-v2/renderer/plugins/provider-presets.mjs";

const storage = new Map();
globalThis.localStorage = {
  getItem(key) {
    return storage.has(String(key)) ? storage.get(String(key)) : null;
  },
  setItem(key, value) {
    storage.set(String(key), String(value));
  },
  removeItem(key) {
    storage.delete(String(key));
  },
};
if (!globalThis.CustomEvent) {
  globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
      this.type = String(type || "");
      this.detail = options.detail;
    }
  };
}
if (!globalThis.window) {
  const target = new EventTarget();
  target.setTimeout = (fn, ms) => setTimeout(fn, ms);
  target.clearTimeout = (timer) => clearTimeout(timer);
  globalThis.window = target;
}

function assistantText(state) {
  return String(
    state.snapshot().messages.findLast((item) => item.role === "assistant")?.content || "",
  );
}

// 真人操作序列：发送请求 -> 看见自然回复 -> 工具开始 -> 工具完成 -> 最终回复。
// 私有推理会随模型供应商返回，但永远不进入任何前端回调或消息状态。
{
  const state = createState();
  const timeline = [];
  const naturalBeforeTool = "我先读取工作区状态，再根据结果继续处理。";
  const naturalAfterTool = `${naturalBeforeTool}\n状态已经读取，我正在整理最终结论。`;
  const finalReply = "检查完成：工作区状态正常。";
  const privateReasoning = "这里是模型私有推理，绝不能显示给用户。";

  const runtime = {
    async send() {
      throw new Error("本场景必须走流式路径");
    },
    async sendStream(payload, callbacks = {}) {
      timeline.push({ type: "request", message: payload.message });

      callbacks.onStageText?.(naturalBeforeTool, {
        kind: "model_content",
        private_reasoning_present: Boolean(privateReasoning),
      });
      timeline.push({ type: "natural_reply", text: assistantText(state) });
      assert.equal(assistantText(state), naturalBeforeTool, "工具开始前必须先显示模型自然回复");
      assert.doesNotMatch(assistantText(state), /私有推理|绝不能显示/);

      callbacks.onToolCall?.({
        call_id: "call-1",
        name: "omni_body",
        action: "workspace.status",
        label: "读取工作区状态",
      });
      timeline.push({ type: "tool_call", visibleText: assistantText(state) });
      assert.equal(assistantText(state), naturalBeforeTool, "工具卡不得覆盖自然回复");

      callbacks.onToolResult?.({
        call_id: "call-1",
        name: "omni_body",
        ok: true,
        summary: "工作区状态正常",
      });
      timeline.push({ type: "tool_result", visibleText: assistantText(state) });

      callbacks.onStageText?.(naturalAfterTool, { kind: "model_content" });
      timeline.push({ type: "continued_reply", text: assistantText(state) });

      return {
        ok: true,
        phase: "finished",
        stdout: JSON.stringify({ huifu: finalReply, simple_chain_status: "complete" }),
        stderr: "",
        simple_chain_status: "complete",
        attachments: [],
        generated_attachments: [],
      };
    },
    async status() {
      return { ok: true, stdout: "状态正常" };
    },
  };

  const actions = createActions({ runtime, state });
  await actions.sendMessage("请检查工作区状态", [], { mode: "work", useStream: true });

  assert.deepEqual(
    timeline.map((item) => item.type),
    ["request", "natural_reply", "tool_call", "tool_result", "continued_reply"],
  );
  assert.equal(assistantText(state), finalReply, "最终自然回复应收口并替换阶段快照");
  assert.doesNotMatch(JSON.stringify(state.snapshot()), /模型私有推理|绝不能显示/);
}

// 真人设置序列：切换模型 -> 选择该供应商支持的思考档位 -> 保存。
{
  const savedBodies = [];
  window.tiangongDesktop = {
    async setModelSettings(body) {
      savedBodies.push({ ...body });
      return {
        ok: true,
        configured_provider: body.provider,
        configured_base_url: body.base_url,
        configured_model_name: body.model_name,
        reasoning: {
          supported: true,
          enabled: !["off", "none", ""].includes(String(body.reasoning_mode || "")),
          configured_mode: body.reasoning_mode,
          effective_mode: body.reasoning_mode,
          modes: providerThinkingCapability(body.provider, body.provider).modes.map((item) => item.value),
        },
      };
    },
  };
  const runtime = createHttpRuntime();
  const cases = [
    ["deepseek_v4", "max"],
    ["mimo", "on"],
    ["glm_5_2", "low"],
    ["minimax_m3", "auto"],
    ["gpt_5_6", "high"],
  ];

  for (const [service, mode] of cases) {
    const preset = applyProviderPreset({}, service);
    const capability = providerThinkingCapability(service, preset.modelProvider);
    assert.equal(capability.supported, true, `${service} 应显示思考设置`);
    assert.equal(
      capability.modes.some((item) => item.value === mode),
      true,
      `${service} 应只提供后端声明的档位`,
    );
    const saved = await runtime.setSettings({
      ...preset,
      modelThinkingEnabled: mode !== "off",
      modelThinkingDepth: mode,
    });
    assert.equal(saved.modelThinkingDepth, mode);
    assert.equal(saved.modelThinkingEnabled, mode !== "off");
  }

  assert.deepEqual(
    savedBodies.map((item) => [item.provider, item.reasoning_mode]),
    cases,
    "每次保存必须把用户选择的真实档位送到可信模型配置通道",
  );
  assert.equal(
    [...storage.values()].some((value) => /api[_-]?key/i.test(value)),
    false,
    "前端持久化不得夹带凭据",
  );
}

console.log("frontend natural reply + tool turn simulation: PASS");
