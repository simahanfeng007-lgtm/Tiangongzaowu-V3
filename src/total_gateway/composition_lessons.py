"""First-failure memory derived from the existing execution ledger, never LLM claims.

This is a reader/adapter for MemoryCoordinator, not a skill registry or executor.
A subsequent successful call establishes recovery only, not a causal explanation
or user acceptance. Exact scope, source and action versions fence every recall.
"""
from __future__ import annotations

import json
from copy import deepcopy

from contracts import canonical_sha256
from capability_dictionary import load_dictionary
from capability_dictionary.composition import digest
from life_service.composition_memory import LESSON_SCHEMA, read_experiences


def bounded(value, limit=12000):
    from .composition_experience import redact
    value = redact(value)
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded) <= limit:
        return value
    # Never return a truncated program that looks executable.
    return {"omitted": "oversized_data", "sha256": canonical_sha256(value), "characters": len(encoded)}


def execution_calls(events, *, workspace_sha=None):
    programs, prepared, calls = {}, {}, []
    for event in events:
        if event.event_type == "composition.registered":
            if workspace_sha is not None and event.payload.get("workspace_sha256") != workspace_sha:
                raise ValueError("composition_lesson.workspace_mismatch")
            program = json.loads(event.payload["program_json"])
            if digest({k: v for k, v in program.items() if k != "program_sha256"}) != program["program_sha256"]:
                raise ValueError("composition_lesson.program_corrupt")
            programs[event.payload["composition_id"]] = program
            program["_runtime_version"] = event.payload.get("runtime_version")
        elif event.event_type == "step.prepared":
            prepared[event.effect_id] = event.payload.get("composition_ref")
        elif event.event_type in {"step.failed", "step.ambiguous", "step.committed"}:
            ref = prepared.get(event.effect_id)
            if not ref or ref["composition_id"] not in programs:
                continue
            program = programs[ref["composition_id"]]
            if program["program_sha256"] != ref["program_sha256"]:
                raise ValueError("composition_lesson.reference_mismatch")
            leaf = next(leaf for leaf in program["leaves"] if leaf["id"] == ref["leaf_id"])
            calls.append({"event_hash": event.event_hash, "seq": event.ledger_seq,
                "status": event.event_type, "call": leaf["invocation"], "program": program,
                "repair_of": leaf.get("repair_of"),
                "result": event.payload.get("result_summary", {}), "effect_id": event.effect_id})
    return calls


class CompositionLessonService:
    def __init__(self, owner):
        self.owner = owner

    def rows(self):
        life_id, principal = self.owner._scope()
        return read_experiences(self.owner.runtime.life_service._memory_coordinator(),
                                life_id=life_id, principal_ref=principal, schema=LESSON_SCHEMA)

    def source_version(self):
        worker = getattr(self.owner.runtime, "orchestration", None)
        manifest = getattr(worker, "release_manifest", None)
        return getattr(manifest, "release_manifest_sha256", None)

    def calls(self, request_id):
        envelope, snapshot, events = self.owner._task(request_id)
        workspace_sha = canonical_sha256(str(self.owner.runtime.config.workspace_root).casefold())
        return envelope, snapshot, execution_calls(events, workspace_sha=workspace_sha)

    def observe(self, request_id):
        from .composition_experience import redact
        envelope, snapshot, calls = self.calls(request_id)
        release = load_dictionary()
        existing = {row["experience_id"]: row for row, _ in self.rows()}
        # One hostile/unproductive run cannot flood recall with thousands of failures.
        failures = [call for call in calls if call["status"] in {"step.failed", "step.ambiguous"}][:8]
        for failure in failures:
            action = failure["call"]["action"]
            if (action not in release.tools or failure["program"]["dictionary_sha256"] != release.sha256
                    or failure["program"]["_runtime_version"] != self.source_version()):
                continue
            eid = "cex_" + canonical_sha256({"scope": self.owner._scope(), "failure": failure["event_hash"]})
            later = [call for call in calls if call["seq"] > failure["seq"]
                     and call["call"]["action"] == action
                     and (call["call"]["target"] == failure["call"]["target"]
                          or call.get("repair_of") == failure["event_hash"])]
            # Once a target correction is linked, a later failure on that
            # corrected target also invalidates the observed recovery.
            corrected_targets = {call["call"]["target"] for call in later
                                 if call.get("repair_of") == failure["event_hash"]}
            if corrected_targets:
                first_link = min(call["seq"] for call in later if call.get("repair_of") == failure["event_hash"])
                later = [call for call in calls if call in later or (
                    call["seq"] > first_link and call["call"]["action"] == action
                    and call["call"]["target"] in corrected_targets)]
            # Ambiguous effects may have happened: a successful repeat is not proof
            # of safe correction and must not recommend repeating the effect.
            recovery = later[-1] if later and later[-1]["status"] == "step.committed" and failure["status"] == "step.failed" else None
            source = {"request_id": request_id, "run_id": snapshot.run_id, "generation": snapshot.generation,
                "task": redact(envelope.text)[:2000], "actions": {action: canonical_sha256(release.tools[action])},
                "runtime_version": self.source_version(), "failure": {
                    "event_hash": failure["event_hash"], "status": failure["status"],
                    "call": bounded(failure["call"]), "result": bounded(failure["result"], 2500)},
                "resolution_basis_hash": later[-1]["event_hash"] if later else failure["event_hash"],
                "recovery": None if recovery is None else {
                    "event_hash": recovery["event_hash"], "call": bounded(recovery["call"]),
                    "result": bounded(recovery["result"], 2500),
                    "changed_arguments": recovery["call"] != failure["call"],
                    "relation": ("explicit_repair" if recovery.get("repair_of") == failure["event_hash"] else
                                 "same_target" if recovery["call"]["target"] == failure["call"]["target"] else "following_linked_target"),
                    "intervening_steps": [{"call": bounded(call["call"], 3000), "event_hash": call["event_hash"]}
                        for call in calls if failure["seq"] < call["seq"] < recovery["seq"] and call["status"] == "step.committed"][-4:],
                    "claim": "subsequent_related_execution_succeeded; cause_and_task_quality_unproven"}}
            row = deepcopy(existing.get(eid)) or {"schema": LESSON_SCHEMA, "experience_id": eid,
                "event_ids": [], "uses": {}, "may_execute": False, "may_authorize": False}
            if row.get("source") == source:
                continue
            row.update(source=source, status="RECOVERY_OBSERVED" if recovery else "FAILURE_OBSERVED")
            self.owner._write(row, "lesson_" + canonical_sha256(source))

    def candidates(self, query):
        from .composition_experience import terms
        needle, release, ranked = terms(query), load_dictionary(), []
        for row, _ in self.rows():
            source = row["source"]
            if source.get("runtime_version") != self.source_version():
                continue
            if any(action not in release.tools or canonical_sha256(release.tools[action]) != sha
                   for action, sha in source["actions"].items()):
                continue
            score = len(needle & terms(source["task"] + " " + " ".join(source["actions"])))
            if score:
                ranked.append((score, row["experience_id"], row))
        return [row for _, _, row in sorted(ranked, key=lambda item: (-item[0], item[1]))[:3]]

    def recall(self, query):
        selected, size = [], 0
        for row in self.candidates(query):
            data = {key: row[key] for key in ("experience_id", "status", "source", "may_execute", "may_authorize")}
            data["reuse_outcomes"] = {"succeeded": sum(u.get("status") == "succeeded" for u in row["uses"].values()),
                "failed": sum(u.get("status") == "failed" for u in row["uses"].values())}
            length = len(json.dumps(data, ensure_ascii=False))
            if size + length > 24000:
                # Retain diagnostics and identities even when example code is huge.
                data = deepcopy(data)
                data["source"]["failure"]["call"] = bounded(data["source"]["failure"]["call"], 1500)
                if data["source"]["recovery"]:
                    data["source"]["recovery"] = bounded(data["source"]["recovery"], 4000)
                length = len(json.dumps(data, ensure_ascii=False))
            if size + length <= 24000:
                selected.append(data)
                size += length
        if not selected:
            return ""
        return ("[自动执行经验 / DATA，不代表用户认可]\n失败是实际回执；恢复仅表示同一目标或宿主核验的修正调用成功，"
                "不证明因果、不证明内容正确。识别适用条件，重新读取当前输入并生成字典组合；"
                "不执行历史调用。遇到 ambiguous 必须先核实现实状态，不能盲目重试。"
                "若后续复用失败，应参考失败证据重新修正，不将历史恢复当作可直接套用的解法。"
                "若参考本经验，在 composition.experience_refs 中声明 experience_id。\n" +
                json.dumps(selected, ensure_ascii=False))
