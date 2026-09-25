"""Compile model-authored Tools and a Skill from dictionary action primitives.

This is a task-local program, not a published capability or permission. The
Gateway records its exact bytes before dispatch. Every leaf still needs the
normal action schema, Policy, Grant, containment and factual result checks.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from . import DictionaryError, load_dictionary

SCHEMA = "tiangong.task-composition.v1"
DISCOVERY_ACTIONS = frozenset({"system.capabilities", "system.action_schema", "system.health"})
MAX_LEAVES = 32
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def composition_prompt(release) -> str:
    # Capability names are drawn from actual dictionary definitions; this is
    # protocol guidance, never a prewritten task/industry Skill.
    available = [name for name, row in release.tools.items()
        if row["runtime"].get("implemented") and row["binding"]["kind"] != "alias"
        and not name.startswith(("skill.", "skill_"))]
    return (
        "[字典能力组合]\n"
        f"当前字典 {release.version}，摘要 {release.sha256}。\n"
        "根据用户语义自行生成任务 Tool 和 Skill。字典不提供预置业务 Skill。"
        "Tool 是一个或多个原子动作的有序组合；Skill 是对本轮生成 Tool 的步骤编排。"
        "所有任务动作必须由 omni_body 的 composition 提交；不得在顶层直接调用任务动作。"
        "纯聊天不需要组合。只可直接调用 system.capabilities、system.action_schema、system.health 发现能力。\n"
        "组合结构：{\"composition\":{\"tools\":[{\"id\":\"read_input\",\"description\":\"读取需要的信息\","
        "\"actions\":[{\"action\":\"file.read\",\"target\":\"实际输入路径\",\"args\":{}}]}],"
        "\"skill\":{\"id\":\"inspect\",\"description\":\"取得事实供下一轮组合\","
        "\"steps\":[{\"id\":\"s1\",\"tool\":\"read_input\",\"depends_on\":[]}]}}}。\n"
        "每次只提交一个组合。ID 用英文、数字、下划线。Tool 的 actions 顺序执行，"
        "Skill 通过 depends_on 指明步骤依赖。可以组合多个 Tool，不需要把每个原子动作拆成一轮。"
        "参数必须是已经确定的具体值，不支持插值表达式。需要先读取未知数据时只组合观察步骤；"
        "观察结果回来后再决定后续组合，不猜文件内容或执行结果。失败后依据真实回执修正组合。"
        "若某动作在修正之前的失败，可在该 action 对象的 repair_of 字段填写失败回执 execution_evidence.event_hash；"
        "允许改正 target，宿主会核对同一任务的失败及后续实际结果。不要把无关成功声明为修复。"
        "组合登记不是执行成功，组合成功不是整个任务完成；须核实用户要求的产物与执行事实。"
        "复杂参数先用 system.action_schema 查询。已有正确成果避免重写，取消或结果不明须先核对。\n"
        "可组合的已实现原子能力（依赖与权限在执行时检查）：" + ", ".join(available)
    )


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _object(value, required, optional=(), *, path="composition"):
    if type(value) is not dict or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        error = DictionaryError("composition.fields_invalid")
        keys = set(value) if type(value) is dict else set()
        error.composition_repair = {"path": path, "expected_type": "object", "received_type": type(value).__name__,
            "required_fields": sorted(required), "optional_fields": sorted(optional),
            "missing_fields": sorted(set(required) - keys),
            "unexpected_fields": sorted(str(key)[:80] for key in keys - set(required) - set(optional))[:16]}
        raise error
    return value


def _id(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise DictionaryError("composition.id_invalid")
    return value


def _description(value):
    if type(value) is not str or not value.strip() or len(value) > 1000:
        raise DictionaryError("composition.description_invalid")


def _data(value, depth=0):
    if depth > 16:
        raise DictionaryError("composition.arguments_too_deep")
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or key.startswith("__"):
                raise DictionaryError("composition.authority_field")
            _data(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _data(item, depth + 1)
    elif value is not None and type(value) not in (str, int, float, bool):
        raise DictionaryError("composition.arguments_invalid")


def compile_task_composition(proposal, *, release=None) -> dict:
    """Validate the entire program before producing any executable leaf.

    Tools compose ordered concrete action invocations. The Skill composes those
    generated Tools in a DAG. Unknown future values require a later model turn;
    strings are never evaluated as expressions or implicit output references.
    """
    release = release or load_dictionary()
    _object(proposal, {"tools", "skill"}, {"experience_refs"})
    refs = proposal.get("experience_refs", [])
    if (type(refs) is not list or len(refs) > 3 or len(set(str(ref) for ref in refs)) != len(refs)
            or any(type(ref) is not str or not re.fullmatch(r"cex_[0-9a-f]{64}", ref) for ref in refs)):
        raise DictionaryError("composition.experience_refs_invalid")
    try:
        raw = json.dumps(proposal, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise DictionaryError("composition.json_invalid") from exc
    if len(raw.encode("utf-8")) > 262144:
        raise DictionaryError("composition.too_large")
    definitions = proposal["tools"]
    if type(definitions) is not list or not 1 <= len(definitions) <= 16:
        raise DictionaryError("composition.tools_invalid")
    tools = {}
    for tool_index, tool in enumerate(definitions):
        _object(tool, {"id", "description", "actions"}, path=f"composition.tools[{tool_index}]")
        tool_id = _id(tool["id"])
        _description(tool["description"])
        if tool_id in tools:
            raise DictionaryError("composition.tool_duplicate")
        actions = tool["actions"]
        if type(actions) is not list or not 1 <= len(actions) <= MAX_LEAVES:
            raise DictionaryError("composition.actions_invalid")
        for action_index, action in enumerate(actions):
            _object(action, {"action", "args"}, {"target", "repair_of"}, path=f"composition.tools[{tool_index}].actions[{action_index}]")
            if "repair_of" in action and (type(action["repair_of"]) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", action["repair_of"])):
                raise DictionaryError("composition.repair_reference_invalid")
            action_id = action["action"]
            if type(action_id) is not str or action_id.startswith(("skill.", "skill_")):
                raise DictionaryError("composition.fixed_skill_forbidden")
            row = release.tools.get(action_id)
            if row is None or not row["runtime"].get("implemented"):
                raise DictionaryError("composition.action_unavailable:" + action_id)
            if type(action.get("target", "")) is not str or type(action["args"]) is not dict:
                raise DictionaryError("composition.invocation_invalid")
            _data(action["args"])
        tools[tool_id] = tool
    skill = _object(proposal["skill"], {"id", "description", "steps"}, path="composition.skill")
    _id(skill["id"])
    _description(skill["description"])
    if type(skill["steps"]) is not list or not 1 <= len(skill["steps"]) <= 16:
        raise DictionaryError("composition.skill_steps_invalid")
    steps = {}
    for step_index, step in enumerate(skill["steps"]):
        _object(step, {"id", "tool", "depends_on"}, path=f"composition.skill.steps[{step_index}]")
        step_id = _id(step["id"])
        if step_id in steps:
            raise DictionaryError("composition.step_duplicate")
        if type(step["tool"]) is not str or step["tool"] not in tools:
            raise DictionaryError("composition.tool_missing")
        deps = step["depends_on"]
        if type(deps) is not list or any(type(dep) is not str for dep in deps) or len(set(deps)) != len(deps):
            raise DictionaryError("composition.dependencies_invalid")
        steps[step_id] = step
    if {step["tool"] for step in steps.values()} != set(tools):
        raise DictionaryError("composition.unused_tool")
    pending, ordered = dict(steps), []
    while pending:
        ready = [key for key, step in pending.items() if set(step["depends_on"]) <= set(ordered)]
        if not ready:
            raise DictionaryError("composition.dependency_cycle_or_missing")
        for key in ready:
            ordered.append(key)
            del pending[key]
    leaves = []
    # Serial materialization respects every dependency and avoids speculative
    # parallel writes. It does not infer a business workflow for the model.
    for key in ordered:
        step = steps[key]
        for ordinal, action in enumerate(tools[step["tool"]]["actions"], 1):
            invocation = {"target": "", **copy.deepcopy(action)}
            repair_of = invocation.pop("repair_of", None)
            leaf = {"id": f"{key}.{ordinal}", "tool_id": step["tool"],
                    "skill_step_id": key, "invocation": invocation}
            if repair_of is not None:
                leaf["repair_of"] = repair_of
            leaves.append(leaf)
    if len(leaves) > MAX_LEAVES:
        raise DictionaryError("composition.too_many_actions")
    program = {"schema": SCHEMA, "dictionary_sha256": release.sha256,
        "proposal": copy.deepcopy(proposal), "leaves": leaves}
    return {**program, "program_sha256": digest(program)}


class CompositionCursor:
    """In-memory projection only; durable registration/effects live in Gateway."""
    def __init__(self, program: dict, registration: dict, provider_turn):
        self.program, self.registration, self.provider_turn = program, registration, provider_turn
        self.index = 0
        self.results = []

    @property
    def leaf(self):
        return self.program["leaves"][self.index]

    def reference(self):
        return {"composition_id": self.registration["composition_id"],
            "program_sha256": self.program["program_sha256"], "leaf_id": self.leaf["id"]}

    def observe(self, result, *, success: bool):
        self.results.append({"leaf_id": self.leaf["id"], "tool_id": self.leaf["tool_id"],
            "action": self.leaf["invocation"]["action"], "ok": success, "result": result})
        self.index += 1
        return success and self.index < len(self.program["leaves"])

    def result(self):
        return {"schema": "tiangong.task-composition-result.v1",
            "composition_id": self.registration["composition_id"],
            "program_sha256": self.program["program_sha256"],
            "dictionary_sha256": self.program["dictionary_sha256"],
            "generated_tool_ids": [tool["id"] for tool in self.program["proposal"]["tools"]],
            "generated_skill_id": self.program["proposal"]["skill"]["id"],
            "ok": self.index == len(self.program["leaves"]) and all(row["ok"] for row in self.results),
            "results": self.results,
            "not_executed": [row["id"] for row in self.program["leaves"][self.index:]],
            "instruction": "根据实际结果继续组合剩余能力；本组合成功不代表用户总任务完成。"}
