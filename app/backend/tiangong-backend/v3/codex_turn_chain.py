"""Codex-style turn loop primitives for v3.

This module ports the control-flow shape of openai/codex's run_turn loop into a
small Python core: fresh input is recorded first, tool calls always produce a
function-call output item, and any tool output forces a follow-up model step
before final completion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Optional


TurnItemKind = Literal["user_input", "assistant_message", "function_call", "function_call_output", "pending_input"]


@dataclass
class TurnItem:
    kind: TurnItemKind
    content: Any
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@dataclass
class ParsedToolCall:
    tool_name: str
    tool_args: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@dataclass
class TurnStep:
    index: int
    model_output: str
    tool_call: Optional[ParsedToolCall] = None
    tool_output: Any = None
    needs_follow_up: bool = False


@dataclass
class TurnChainResult:
    final_message: str
    completed: bool
    reason: str
    steps: list[TurnStep] = field(default_factory=list)
    history: list[TurnItem] = field(default_factory=list)


class PendingInputQueue:
    def __init__(self) -> None:
        self._items: list[TurnItem] = []

    def extend(self, items: Iterable[TurnItem]) -> None:
        self._items.extend(items)

    def has_pending_input(self) -> bool:
        return bool(self._items)

    def drain(self) -> list[TurnItem]:
        items = list(self._items)
        self._items.clear()
        return items


ToolParser = Callable[[str], Optional[ParsedToolCall]]
ToolExecutor = Callable[[ParsedToolCall], Any]
ModelSampler = Callable[[list[TurnItem], Optional[TurnStep]], str]
Recorder = Callable[[TurnItem], None]


class CodexTurnChain:
    def __init__(
        self,
        *,
        parse_tool_call: ToolParser,
        execute_tool: ToolExecutor,
        sample_next: ModelSampler,
        pending_input: PendingInputQueue | None = None,
        record_item: Recorder | None = None,
        max_steps: int = 32,
    ) -> None:
        self.parse_tool_call = parse_tool_call
        self.execute_tool = execute_tool
        self.sample_next = sample_next
        self.pending_input = pending_input or PendingInputQueue()
        self.record_item = record_item
        self.max_steps = max(1, int(max_steps or 32))
        self.history: list[TurnItem] = []

    def record(self, item: TurnItem) -> None:
        self.history.append(item)
        if self.record_item:
            self.record_item(item)

    def _record_pending_if_allowed(self, can_drain_pending_input: bool) -> bool:
        if not can_drain_pending_input or not self.pending_input.has_pending_input():
            return False
        for item in self.pending_input.drain():
            pending = item if item.kind == "pending_input" else TurnItem("pending_input", item.content)
            self.record(pending)
        return True

    def run(self, initial_input: str) -> TurnChainResult:
        self.record(TurnItem("user_input", str(initial_input or "")))
        steps: list[TurnStep] = []
        last_message = ""
        previous_step: TurnStep | None = None
        can_drain_pending_input = False

        for index in range(1, self.max_steps + 1):
            self._record_pending_if_allowed(can_drain_pending_input)
            model_output = str(self.sample_next(self.history, previous_step) or "")
            self.record(TurnItem("assistant_message", model_output))
            last_message = model_output

            tool_call = self.parse_tool_call(model_output)
            step = TurnStep(index=index, model_output=model_output, tool_call=tool_call)
            if tool_call is None:
                has_pending = self.pending_input.has_pending_input()
                step.needs_follow_up = has_pending
                steps.append(step)
                if has_pending:
                    previous_step = step
                    can_drain_pending_input = True
                    continue
                return TurnChainResult(
                    final_message=last_message,
                    completed=True,
                    reason="assistant_message_without_follow_up",
                    steps=steps,
                    history=list(self.history),
                )

            call_id = tool_call.call_id or f"call_{index}"
            tool_call.call_id = call_id
            self.record(TurnItem("function_call", {"name": tool_call.tool_name, "arguments": tool_call.tool_args}, tool_call.tool_name, tool_call.tool_args, call_id))
            tool_output = self.execute_tool(tool_call)
            step.tool_output = tool_output
            step.needs_follow_up = True
            self.record(TurnItem("function_call_output", tool_output, tool_call.tool_name, tool_call.tool_args, call_id))
            steps.append(step)
            previous_step = step
            can_drain_pending_input = False

        return TurnChainResult(
            final_message=last_message,
            completed=False,
            reason="max_steps_exceeded",
            steps=steps,
            history=list(self.history),
        )
