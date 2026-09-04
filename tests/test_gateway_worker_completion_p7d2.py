"""Focused execution tests for the P7D.2 orchestration completion seam."""

from __future__ import annotations

import ast
import copy
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import total_gateway.verification_plan_executor as executor_module
import total_gateway.verification_repair_coordinator as coordinator_module
from total_gateway.orchestration import OrchestrationError


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION_SOURCE = ROOT / "src" / "total_gateway" / "orchestration.py"
PARENT_EFFECT = "eff_" + "1" * 64
ATTEMPT_ONE = "eff_" + "2" * 64
LEAF_EFFECT = "eff_" + "3" * 64
SECOND_LEAF_EFFECT = "eff_" + "0" * 64


def _is_desktop_branch(node: ast.AST) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    comparison = node.test
    return (
        isinstance(comparison.left, ast.Attribute)
        and isinstance(comparison.left.value, ast.Name)
        and comparison.left.value.id == "envelope"
        and comparison.left.attr == "channel"
        and len(comparison.ops) == 1
        and isinstance(comparison.ops[0], ast.Eq)
        and len(comparison.comparators) == 1
        and isinstance(comparison.comparators[0], ast.Constant)
        and comparison.comparators[0].value == "desktop"
    )


def _assigned_name(statement: ast.stmt) -> str | None:
    if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
        return None
    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    return next(
        (item.id for item in targets if isinstance(item, ast.Name)),
        None,
    )


def _completion_exercise(evaluate_desktop_completion, verification_snapshot):
    tree = ast.parse(
        ORCHESTRATION_SOURCE.read_text(encoding="utf-8"),
        filename=str(ORCHESTRATION_SOURCE),
    )
    worker = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "GatewayOrchestrationWorker"
    )
    continuation = next(
        node
        for node in worker.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_continue_after_parent_success"
    )
    desktop = next(
        node for node in ast.walk(continuation) if _is_desktop_branch(node)
    )
    start = next(
        index
        for index, statement in enumerate(desktop.body)
        if _assigned_name(statement) == "composition_required_effect_ids"
    )
    finish = next(
        index
        for index, statement in enumerate(desktop.body[start:], start=start)
        if _assigned_name(statement) == "decision"
    )
    statements = copy.deepcopy(desktop.body[start : finish + 1])
    wrapper = ast.parse(
        "def exercise(self, envelope, request_id, run_id, generation, "
        "parent_effect_id, artifacts, reply, composition_finalization, "
        "composition_completion_at_ms=None, durable_resume=False):\n    pass\n"
    ).body[0]
    assert isinstance(wrapper, ast.FunctionDef)
    wrapper.body = statements + [
        ast.Return(value=ast.Name(id="decision", ctx=ast.Load()))
    ]
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    namespace = {
        "OrchestrationError": OrchestrationError,
        "_verification_snapshot": verification_snapshot,
        "evaluate_desktop_completion": evaluate_desktop_completion,
        "time": time,
    }
    exec(compile(module, str(ORCHESTRATION_SOURCE), "exec"), namespace)
    return namespace["exercise"], desktop


class _CompletionStore:
    def __init__(self, *, active_plan=None, readiness=None) -> None:
        self.active_plan = active_plan
        self.readiness = readiness
        self.failure_evidence: dict[str, Any] = {}
        self.readiness_reads: list[dict[str, Any]] = []

    def get_active_verification_plan(self, **kwargs):
        return self.active_plan

    def get_effect_head_state(self, effect_id: str):
        return "SUCCEEDED"

    def get_verification_disposition_by_id(self, disposition_id: str):
        return None

    def get_verification_failure_evidence_by_id(self, evidence_id: str):
        return self.failure_evidence.get(evidence_id)

    def get_latest_verification_readiness(self, **kwargs):
        self.readiness_reads.append(kwargs)
        return self.readiness


def _run_completion(exercise, worker, composition_finalization=None):
    return exercise(
        worker,
        SimpleNamespace(channel="desktop"),
        "request-p7d2",
        "run-p7d2",
        SimpleNamespace(generation=4),
        PARENT_EFFECT,
        (),
        "done",
        composition_finalization,
        1_700_000_000_000 if composition_finalization is not None else None,
    )


def test_composition_completion_uses_parent_leaves_and_all_attempt_lineage() -> None:
    captured: dict[str, Any] = {}

    def evaluate(**kwargs):
        captured.update(kwargs)
        return kwargs

    exercise, _ = _completion_exercise(evaluate, lambda *_args: None)
    store = _CompletionStore()
    worker = SimpleNamespace(
        _store=store,
        _objects=object(),
        _facts=object(),
    )
    finalization = SimpleNamespace(
        parent_effect_id=PARENT_EFFECT,
        leaf_effect_ids=(LEAF_EFFECT, SECOND_LEAF_EFFECT, LEAF_EFFECT),
        lineage_effect_ids=(
            LEAF_EFFECT,
            ATTEMPT_ONE,
            LEAF_EFFECT,
            SECOND_LEAF_EFFECT,
        ),
    )

    _run_completion(exercise, worker, finalization)

    assert captured["execution_effect_id"] is None
    assert captured["execution_effect_ids"] == (
        PARENT_EFFECT,
        LEAF_EFFECT,
        SECOND_LEAF_EFFECT,
    )
    assert captured["execution_lineage_effect_ids"] == (
        PARENT_EFFECT,
        LEAF_EFFECT,
        ATTEMPT_ONE,
        SECOND_LEAF_EFFECT,
    )


def test_no_composition_preserves_single_parent_effect_path() -> None:
    captured: dict[str, Any] = {}

    def evaluate(**kwargs):
        captured.update(kwargs)
        return kwargs

    exercise, _ = _completion_exercise(evaluate, lambda *_args: None)
    worker = SimpleNamespace(
        _store=_CompletionStore(),
        _objects=object(),
        _facts=object(),
    )

    _run_completion(exercise, worker)

    assert captured["execution_effect_id"] == PARENT_EFFECT
    assert captured["execution_effect_ids"] is None
    assert captured["execution_lineage_effect_ids"] == ()


def test_p19_completion_processes_all_failures_once_without_repair(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    executor_calls: list[dict[str, Any]] = []
    process_calls: list[dict[str, Any]] = []
    readiness = SimpleNamespace(verification_ready=False)
    dispositions = (
        SimpleNamespace(
            plan_entry_id="entry-b",
            failure_evidence_id="evidence-b",
        ),
        SimpleNamespace(
            plan_entry_id="entry-a",
            failure_evidence_id="evidence-a",
        ),
    )

    class Executor:
        def __init__(self, **kwargs) -> None:
            executor_calls.append({"init": kwargs})

        def execute(self, **kwargs):
            executor_calls.append({"execute": kwargs})
            return readiness

    class Coordinator:
        def __init__(self, **kwargs) -> None:
            process_calls.append({"init": kwargs})

        def process_readiness(self, **kwargs):
            process_calls.append({"process_readiness": kwargs})
            return list(dispositions)

    monkeypatch.setattr(executor_module, "VerificationPlanExecutor", Executor)
    monkeypatch.setattr(
        coordinator_module,
        "VerificationRepairCoordinator",
        Coordinator,
    )

    def evaluate(**kwargs):
        captured.update(kwargs)
        return kwargs

    snapshots: list[tuple[Any, str]] = []

    def snapshot(store, digest):
        snapshots.append((store, digest))
        return "snapshot"

    exercise, desktop = _completion_exercise(evaluate, snapshot)
    active_plan = SimpleNamespace(registry_snapshot_sha256="a" * 64)
    store = _CompletionStore(active_plan=active_plan, readiness=readiness)
    evidence_a = SimpleNamespace(failure_evidence_id="evidence-a")
    evidence_b = SimpleNamespace(failure_evidence_id="evidence-b")
    store.failure_evidence = {
        "evidence-a": evidence_a,
        "evidence-b": evidence_b,
    }
    worker = SimpleNamespace(
        _store=store,
        _objects=object(),
        _facts=object(),
    )

    _run_completion(
        exercise,
        worker,
        SimpleNamespace(
            parent_effect_id=PARENT_EFFECT,
            leaf_effect_ids=(LEAF_EFFECT,),
            lineage_effect_ids=(LEAF_EFFECT,),
        ),
    )

    assert len([item for item in executor_calls if "execute" in item]) == 1
    assert len(
        [item for item in process_calls if "process_readiness" in item]
    ) == 1
    assert [item.plan_entry_id for item in captured["verification_dispositions"]] == [
        "entry-a",
        "entry-b",
    ]
    assert captured["verification_failure_evidences"] == (
        evidence_a,
        evidence_b,
    )
    reader = captured["readiness_authority_reader"]
    assert reader(
        request_id="request-p7d2",
        run_id="run-p7d2",
        generation=4,
    ) is readiness
    assert store.readiness_reads == [
        {
            "request_id": "request-p7d2",
            "run_id": "run-p7d2",
            "generation": 4,
        }
    ]
    composition_split = next(
        node
        for node in ast.walk(desktop)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "composition_finalization is not None"
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "process_readiness"
            for statement in node.body
            for child in ast.walk(statement)
        )
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute_repair_loop"
        for statement in composition_split.body
        for node in ast.walk(statement)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute_repair_loop"
        for statement in composition_split.orelse
        for node in ast.walk(statement)
    )


def test_p19_all_pass_composition_uses_empty_plural_authority_set(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    readiness = SimpleNamespace(verification_ready=True)

    class Executor:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, **_kwargs):
            return readiness

    monkeypatch.setattr(executor_module, "VerificationPlanExecutor", Executor)

    def evaluate(**kwargs):
        captured.update(kwargs)
        return kwargs

    exercise, _ = _completion_exercise(evaluate, lambda *_args: "snapshot")
    active_plan = SimpleNamespace(registry_snapshot_sha256="a" * 64)
    store = _CompletionStore(active_plan=active_plan, readiness=readiness)
    worker = SimpleNamespace(
        _store=store,
        _objects=object(),
        _facts=object(),
    )

    _run_completion(
        exercise,
        worker,
        SimpleNamespace(
            parent_effect_id=PARENT_EFFECT,
            leaf_effect_ids=(LEAF_EFFECT,),
            lineage_effect_ids=(LEAF_EFFECT,),
        ),
    )

    assert captured["verification_dispositions"] == ()
    assert captured["verification_failure_evidences"] == ()
    assert captured["readiness_authority_reader"](
        request_id="request-p7d2",
        run_id="run-p7d2",
        generation=4,
    ) is readiness
