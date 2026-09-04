"""P7D.2 orchestration restart cutpoints and skip-forward invariants."""

from __future__ import annotations

import ast
from dataclasses import replace
import inspect
from pathlib import Path
import textwrap
from types import SimpleNamespace

import pytest

from contracts import canonical_json_bytes, canonical_sha256, derive_run_identity
from contracts.verification import (
    AcceptancePredicate,
    VerificationPlan,
    VerificationPlanEntryV2,
)
from tests import test_gateway_worker_composition_resume_p7d2 as resume
from tests.test_docx_qc import docx_bytes
from tests.test_p19_m2_1_artifact_oracle import M21OracleTestBase
from total_gateway.artifact_gate import derive_artifact_revision_identity
from total_gateway.fact_ledger import _batch_digest
from total_gateway.orchestration import GatewayOrchestrationWorker, OrchestrationError
from total_gateway.verification_plan_executor import VerificationPlanExecutor
from total_gateway.verification_repair_coordinator import (
    VerificationRepairCoordinator,
)


def _life_commit_result(payload, *, duplicate: bool = False):
    execution = dict(payload)
    execution["commit_sha256"] = canonical_sha256(
        {
            "domain": "tiangong.life.execution-commit.v1",
            "payload": dict(payload),
        }
    )
    return {
        "ok": True,
        "duplicate": duplicate,
        "execution": execution,
    }


class _FactBatchOverride:
    def __init__(self, delegate, batch) -> None:
        self._delegate = delegate
        self._batch = batch

    def get_batch_for_effect(self, effect_id: str, *, verify_payload: bool = True):
        assert verify_payload is True
        if effect_id == self._batch.result.effect_id:
            return self._batch
        return self._delegate.get_batch_for_effect(
            effect_id,
            verify_payload=verify_payload,
        )

    def __getattr__(self, name):
        return getattr(self._delegate, name)


def _rehash_batch(batch, **updates):
    changed = replace(batch, **updates, batch_sha256="0" * 64)
    return replace(changed, batch_sha256=_batch_digest(changed))


@pytest.mark.parametrize("initial_state", ["CLAIMED", "SIDE_EFFECT_STARTED"])
@pytest.mark.parametrize("mismatch", ["ticket", "scope", "object"])
def test_parent_fact_mismatch_never_promotes_effect_to_succeeded(
    tmp_path: Path,
    initial_state: str,
    mismatch: str,
) -> None:
    runtime = resume._runtime(
        tmp_path / f"{mismatch}-{initial_state.lower()}",
        message_ref=f"{mismatch}-{initial_state.lower()}",
    )
    try:
        claim = resume._claim_parent(runtime)
        response, batch = resume._record_parent_fact(runtime, claim)
        continuation_ticket_id = response.result.ticket_id
        exposed_batch = batch
        if mismatch == "ticket":
            continuation_ticket_id = "ticket_parent_mismatch"
        elif mismatch == "scope":
            exposed_batch = _rehash_batch(batch, tenant_id="tenant_fact_mismatch")
        else:
            payload = canonical_json_bytes(response.result_payload)
            wrong_object = runtime.objects.put_bytes(
                payload,
                kind="payload",
                tenant_id="tenant_object_mismatch",
                link_account_id=runtime.activation.envelope.link_account_id,
                conversation_scope_hash=(
                    runtime.activation.envelope.conversation_scope_hash
                ),
                created_at_ms=batch.observed_at_ms,
            ).reference
            exposed_batch = _rehash_batch(
                batch,
                result_payload_object_id=wrong_object.object_id,
                result_payload_sha256=wrong_object.sha256,
            )

        continuation = resume._continuation(
            runtime,
            claim,
            continuation_ticket_id,
        )
        runtime.worker._store = resume._StoreHarness(
            runtime.store,
            runtime.plan_record,
            continuation,
        )
        runtime.worker._facts = _FactBatchOverride(runtime.facts, exposed_batch)
        if initial_state == "SIDE_EFFECT_STARTED":
            runtime.store.mark_effect_started(
                claim.effect_id,
                started_at_ms=response.result.started_at_ms,
            )

        with pytest.raises(OrchestrationError):
            runtime.worker._durable_composition_parent_resume(
                runtime.activation,
                runtime.plan_record,
                now_ms=1_400,
            )

        head = runtime.store.get_effect(claim.effect_id)
        assert head is not None
        assert head.state == initial_state
        assert head.result is None
    finally:
        resume._close(runtime)


class _CompletedDagExecutor:
    def __init__(self, finalization) -> None:
        self.finalization = finalization
        self.project_calls = 0
        self.dispatch_calls = 0
        self.finalization_calls = 0

    def project_plan(self, _plan):
        self.project_calls += 1
        return SimpleNamespace(
            all_steps_succeeded=True,
            next_step_id=None,
            failed_step_ids=(),
            reconcile_step_ids=(),
            recoverable_step_ids=(),
        )

    def dispatch_next(self, **_kwargs):
        self.dispatch_calls += 1
        raise AssertionError("expired completed DAG attempted child dispatch")

    def finalize_plan(self, _plan):
        self.finalization_calls += 1
        return self.finalization


class _TailStore:
    def __init__(self, snapshots=None) -> None:
        self.completed_sessions: list[tuple[str, str]] = []
        self.completion_decisions: list[tuple[object, int]] = []
        self.snapshots = {} if snapshots is None else dict(snapshots)
        self.registered_artifacts: list[dict] = []
        self.request_id = next(
            (
                entity_id
                for (machine, entity_id) in self.snapshots
                if machine == "request"
            ),
            "req_" + "4" * 64,
        )
        self.run_id = next(
            (
                entity_id.removeprefix("execution-")
                for (machine, entity_id) in self.snapshots
                if machine == "execution"
            ),
            derive_run_identity(self.request_id, 1).run_id,
        )

    def get_active_verification_plan(self, **_kwargs):
        return None

    def list_effects_for_request(self, *_args, **_kwargs):
        return ()

    def get_effect_head_state(self, _effect_id: str) -> str:
        return "SUCCEEDED"

    def get_verification_disposition_by_id(self, _disposition_id: str):
        return None

    def get_snapshot(self, machine: str, entity_id: str):
        snapshot = self.snapshots.get((machine, entity_id))
        if snapshot is not None:
            for name, value in (
                ("machine", machine),
                ("entity_id", entity_id),
                ("request_id", self.request_id),
                ("run_id", self.run_id),
                ("generation", 1),
            ):
                if not hasattr(snapshot, name):
                    setattr(snapshot, name, value)
        return snapshot

    def register_artifact_subject(self, **kwargs):
        self.registered_artifacts.append(kwargs)
        return True

    def record_completion_decision(self, decision, *, recorded_at_ms: int):
        self.completion_decisions.append((decision, recorded_at_ms))
        return SimpleNamespace(decision=decision)

    def complete_session_request(
        self,
        session_scope_hash: str,
        request_id: str,
        *,
        completed_at_ms: int,
    ) -> None:
        assert completed_at_ms >= 0
        self.completed_sessions.append((session_scope_hash, request_id))


def _invoke_tail(
    worker,
    *,
    executor,
    store,
    response,
    durable_resume: bool,
):
    request_id = "req_" + "4" * 64
    run_id = derive_run_identity(request_id, 1).run_id
    generation = SimpleNamespace(generation=1)
    envelope = SimpleNamespace(
        channel="desktop",
        text="resume completed DAG",
        principal_scope_hash="7" * 64,
    )
    activation = SimpleNamespace(
        entry=SimpleNamespace(
            request_id=request_id,
            session_scope_hash="session-scope-p7d2",
        ),
        envelope=envelope,
    )
    expired_plan = SimpleNamespace(
        step_bindings=(SimpleNamespace(), SimpleNamespace()),
        expires_at_ms=1_000,
    )
    worker._composition_steps = executor
    worker._store = store
    worker._continue_after_parent_success(
        activation=activation,
        envelope=envelope,
        generation=generation,
        request_id=request_id,
        run_id=run_id,
        run_sequence=1,
        composition_plan_record=SimpleNamespace(executable_plan=expired_plan),
        execution_entity="execution-" + run_id,
        delivery_entity="delivery-" + run_id,
        parent_effect_id=executor.finalization.parent_effect_id,
        observed_at=2_000,
        response=response,
        artifact_intent_id="artifact-intent",
        workspace_id="workspace-p7d2",
        manifest=None,
        action=None,
        permission=None,
        outer_registry=None,
        transport=None,
        arguments={},
        grants=(),
        resources=(),
        life=None,
        life_id="life-p7d2",
        life_evidence_ref="lev_" + "6" * 64,
        output_root_id="output-p7d2",
        durable_resume=durable_resume,
    )
    return request_id, run_id


def test_expired_fully_succeeded_dag_finalizes_without_authorize_or_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_effect_id = "eff_" + "1" * 64
    first_effect_id = "eff_" + "2" * 64
    leaf_effect_id = "eff_" + "3" * 64
    finalization = SimpleNamespace(
        parent_effect_id=parent_effect_id,
        leaf_effect_ids=(leaf_effect_id,),
        lineage_effect_ids=(first_effect_id, leaf_effect_id),
        fact_ids=("fact-child-first", "fact-child-leaf"),
        final_output_aliases={"answer": "durable"},
        completed_at_ms=2_000,
    )
    executor = _CompletedDagExecutor(finalization)
    store = _TailStore()
    completion_calls: list[dict] = []
    monkeypatch.setattr(
        "total_gateway.orchestration.evaluate_desktop_completion",
        lambda **kwargs: (
            completion_calls.append(kwargs)
            or SimpleNamespace(decision_sha256="d" * 64)
        ),
    )
    monkeypatch.setattr(
        "total_gateway.orchestration.persist_terminal_completion",
        lambda *_args, **_kwargs: None,
    )

    worker = object.__new__(GatewayOrchestrationWorker)
    worker._composition_steps = executor
    worker._store = store
    worker._objects = object()
    worker._facts = object()
    worker._advance = lambda *_args, **_kwargs: None
    worker._commit_life_execution = lambda **_kwargs: None
    worker._terminalize_composition_failure = lambda *_args, **_kwargs: None

    response = SimpleNamespace(
        result=SimpleNamespace(fact_ids=("fact-parent",)),
        result_payload={"reply_text": "parent reply"},
        response_sha256="e" * 64,
    )

    _invoke_tail(
        worker,
        executor=executor,
        store=store,
        response=response,
        durable_resume=False,
    )

    assert executor.project_calls == 1
    assert executor.dispatch_calls == 0
    assert executor.finalization_calls == 1
    assert len(completion_calls) == 1
    assert completion_calls[0]["execution_effect_ids"] == (
        parent_effect_id,
        leaf_effect_id,
    )
    assert completion_calls[0]["execution_lineage_effect_ids"] == (
        parent_effect_id,
        first_effect_id,
        leaf_effect_id,
    )


def test_shared_success_tail_guards_artifact_and_delivery_reentry_structure() -> None:
    source = inspect.getsource(
        GatewayOrchestrationWorker._continue_after_parent_success
    )
    tree = ast.parse(textwrap.dedent(source))
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    def dotted(call: ast.Call) -> str:
        parts: list[str] = []
        node = call.func
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    def has_durable_guard(call: ast.Call) -> bool:
        node: ast.AST | None = call
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.If):
                condition = ast.unparse(node.test)
                if "durable_resume" in condition or "snapshot" in condition:
                    return True
        return False

    # Artifact QC and delivery may already be past their initial transition
    # after a crash.  The shared tail must read both durable states before it
    # attempts ArtifactGate/QC, and every delivery transition must use the
    # skip-forward helper instead of the raw state-machine transition.
    assert source.count("get_snapshot(") >= 2
    external_artifact_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and dotted(node)
        in {
            "gate.accept",
            "docx_qc.evaluate",
            "integrity_qc.evaluate",
        }
    ]
    assert {dotted(node) for node in external_artifact_calls} == {
        "gate.accept",
        "docx_qc.evaluate",
        "integrity_qc.evaluate",
    }
    assert all(has_durable_guard(node) for node in external_artifact_calls)

    raw_delivery_transitions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and dotted(node) == "self._advance"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "delivery"
    ]
    assert raw_delivery_transitions == []


@pytest.mark.parametrize(
    ("initial_delivery", "expected_delivery_transitions"),
    [
        ("TICKET_ISSUED", ("SENDING", "CHANNEL_ACCEPTED")),
        ("SENDING", ("CHANNEL_ACCEPTED",)),
        ("CHANNEL_ACCEPTED", ()),
    ],
)
def test_durable_delivery_tail_only_moves_forward_and_completes_session(
    monkeypatch: pytest.MonkeyPatch,
    initial_delivery: str,
    expected_delivery_transitions: tuple[str, ...],
) -> None:
    parent_effect_id = "eff_" + "1" * 64
    executor = _CompletedDagExecutor(
        SimpleNamespace(
            parent_effect_id=parent_effect_id,
            leaf_effect_ids=("eff_" + "3" * 64,),
            lineage_effect_ids=("eff_" + "2" * 64, "eff_" + "3" * 64),
            fact_ids=("fact-child-first", "fact-child-leaf"),
            final_output_aliases={},
            completed_at_ms=2_000,
        )
    )
    request_id = "req_" + "4" * 64
    run_id = derive_run_identity(request_id, 1).run_id
    delivery_entity = "delivery-" + run_id
    store = _TailStore(
        {
            ("execution", "execution-" + run_id): SimpleNamespace(
                state="SUCCEEDED", is_terminal=True
            ),
            ("delivery", delivery_entity): SimpleNamespace(
                state=initial_delivery, is_terminal=False
            ),
            ("request", request_id): SimpleNamespace(
                state="DELIVERING", is_terminal=False
            ),
        }
    )
    monkeypatch.setattr(
        "total_gateway.orchestration.evaluate_desktop_completion",
        lambda **_kwargs: SimpleNamespace(decision_sha256="d" * 64),
    )
    monkeypatch.setattr(
        "total_gateway.orchestration.persist_terminal_completion",
        lambda *_args, **_kwargs: None,
    )
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._objects = object()
    worker._facts = object()
    worker._commit_life_execution = lambda **_kwargs: None
    worker._terminalize_composition_failure = lambda *_args, **_kwargs: None
    transitions: list[tuple[str, str]] = []

    def advance(machine, entity_id, to_state, **_kwargs):
        transitions.append((machine, to_state))
        store.snapshots[(machine, entity_id)] = SimpleNamespace(
            state=to_state,
            is_terminal=to_state in {"COMPLETED", "DELIVERED"},
        )
        return store.snapshots[(machine, entity_id)]

    worker._advance = advance
    response = SimpleNamespace(
        result=SimpleNamespace(fact_ids=("fact-parent",)),
        result_payload={"reply_text": "parent reply"},
        response_sha256="e" * 64,
    )

    _invoke_tail(
        worker,
        executor=executor,
        store=store,
        response=response,
        durable_resume=True,
    )

    assert tuple(
        state for machine, state in transitions if machine == "delivery"
    ) == expected_delivery_transitions
    assert store.snapshots[("request", request_id)].state == "COMPLETED"
    assert store.completed_sessions == [("session-scope-p7d2", request_id)]


def test_durable_qc_passed_artifact_reuses_manifest_without_gate_or_qc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_effect_id = "eff_" + "1" * 64
    executor = _CompletedDagExecutor(
        SimpleNamespace(
            parent_effect_id=parent_effect_id,
            leaf_effect_ids=("eff_" + "3" * 64,),
            lineage_effect_ids=("eff_" + "2" * 64, "eff_" + "3" * 64),
            fact_ids=("fact-child-first", "fact-child-leaf"),
            final_output_aliases={},
            completed_at_ms=2_000,
        )
    )
    request_id = "req_" + "4" * 64
    run_id = derive_run_identity(request_id, 1).run_id
    content_sha256 = "8" * 64
    artifact_identity = derive_artifact_revision_identity(
        request_id=request_id,
        run_id=run_id,
        run_sequence=1,
        generation=1,
        artifact_intent_id="artifact-intent-1",
        revision=1,
        content_sha256=content_sha256,
    )
    descriptor = {
        "object_id": "obj-artifact-p7d2",
        "sha256": content_sha256,
        "size_bytes": 12,
        "filename": "result.txt",
        "mime": "text/plain",
        "format_id": "txt",
    }
    manifest = SimpleNamespace(
        artifact_revision_id=artifact_identity.artifact_revision_id,
        request_id=request_id,
        run_id=run_id,
        generation=1,
        source_effect_id=parent_effect_id,
        producer_fact_id="fact-parent",
        workspace_id="workspace-p7d2",
        content_object_id=descriptor["object_id"],
        sha256=content_sha256,
        size_bytes=descriptor["size_bytes"],
        filename=descriptor["filename"],
        mime=descriptor["mime"],
        format_id=descriptor["format_id"],
        manifest_sha256="9" * 64,
    )
    qc_record = SimpleNamespace(
        result=SimpleNamespace(status="PASSED"),
        manifest=manifest,
    )

    class Facts:
        def __init__(self) -> None:
            self.qc_reads = 0

        def get_artifact_qc(self, artifact_revision_id, **kwargs):
            self.qc_reads += 1
            assert artifact_revision_id == manifest.artifact_revision_id
            assert kwargs["verify_payload"] is True
            return qc_record

    class ForbiddenArtifactRuntime:
        def __init__(self, *_args) -> None:
            pass

        def accept(self, *_args, **_kwargs):
            raise AssertionError("durable QC artifact re-entered ArtifactGate")

        def evaluate(self, *_args, **_kwargs):
            raise AssertionError("durable QC artifact repeated QC execution")

    for symbol in (
        "ArtifactGate",
        "DocxQcService",
        "ArtifactIntegrityQcService",
    ):
        monkeypatch.setattr(
            f"total_gateway.orchestration.{symbol}",
            ForbiddenArtifactRuntime,
        )
    captured_artifacts: list[tuple] = []
    monkeypatch.setattr(
        "total_gateway.orchestration.evaluate_desktop_completion",
        lambda **kwargs: (
            captured_artifacts.append(kwargs["artifacts"])
            or SimpleNamespace(decision_sha256="d" * 64)
        ),
    )
    monkeypatch.setattr(
        "total_gateway.orchestration.persist_terminal_completion",
        lambda *_args, **_kwargs: None,
    )
    store = _TailStore(
        {
            ("execution", "execution-" + run_id): SimpleNamespace(
                state="SUCCEEDED", is_terminal=True
            ),
            ("delivery", "delivery-" + run_id): SimpleNamespace(
                state="CHANNEL_ACCEPTED", is_terminal=False
            ),
            ("request", request_id): SimpleNamespace(
                state="DELIVERING", is_terminal=False
            ),
            ("artifact", manifest.artifact_revision_id): SimpleNamespace(
                state="QC_PASSED", is_terminal=True
            ),
        }
    )
    facts = Facts()
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._objects = object()
    worker._facts = facts
    worker._initialize = lambda *_args, **_kwargs: None
    worker._commit_life_execution = lambda **_kwargs: None
    worker._terminalize_composition_failure = lambda *_args, **_kwargs: None

    def advance(machine, entity_id, to_state, **_kwargs):
        store.snapshots[(machine, entity_id)] = SimpleNamespace(
            state=to_state,
            is_terminal=to_state == "COMPLETED",
        )
        return store.snapshots[(machine, entity_id)]

    worker._advance = advance
    response = SimpleNamespace(
        result=SimpleNamespace(fact_ids=("fact-parent",)),
        result_payload={"reply_text": "artifact ready", "artifacts": [descriptor]},
        response_sha256="e" * 64,
    )

    _invoke_tail(
        worker,
        executor=executor,
        store=store,
        response=response,
        durable_resume=True,
    )

    assert facts.qc_reads == 1
    assert captured_artifacts == [(manifest,)]
    assert store.registered_artifacts[0]["artifact_revision_id"] == (
        manifest.artifact_revision_id
    )


def test_life_commit_cutpoint_reuses_persisted_execution_success_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-Life/pre-delivery crash must replay the exact shared tail bytes."""

    class CrashBeforeDelivery(RuntimeError):
        pass

    parent_effect_id = "eff_" + "1" * 64
    executor = _CompletedDagExecutor(
        SimpleNamespace(
            parent_effect_id=parent_effect_id,
            leaf_effect_ids=("eff_" + "3" * 64,),
            lineage_effect_ids=("eff_" + "2" * 64, "eff_" + "3" * 64),
            fact_ids=("fact-child-first", "fact-child-leaf"),
            final_output_aliases={},
            completed_at_ms=2_000,
        )
    )
    request_id = "req_" + "4" * 64
    run_id = derive_run_identity(request_id, 1).run_id
    execution_entity = "execution-" + run_id
    delivery_entity = "delivery-" + run_id
    persisted_success_ms = 2_000
    first_wall_ms = 10_000
    resumed_wall_ms = 90_000
    store = _TailStore(
        {
            ("request", request_id): SimpleNamespace(
                state="EXECUTING", updated_at_ms=1_900, is_terminal=False
            ),
            ("execution", execution_entity): SimpleNamespace(
                state="CLAIMED", updated_at_ms=1_900, is_terminal=False
            ),
            ("delivery", delivery_entity): SimpleNamespace(
                state="NOT_PLANNED", updated_at_ms=1_900, is_terminal=False
            ),
        }
    )
    active_plan = SimpleNamespace(
        registry_snapshot_sha256="a" * 64,
        verification_plan_id="vpl_" + "a" * 64,
        plan_sha256="b" * 64,
    )
    store.get_active_verification_plan = lambda **_kwargs: active_plan
    persisted_readiness: list[object] = []
    store.get_latest_verification_readiness = lambda **_kwargs: (
        None if not persisted_readiness else persisted_readiness[0]
    )
    monkeypatch.setattr(
        "total_gateway.orchestration._verification_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    readiness_times: list[int] = []
    readiness_hashes: list[str] = []

    class P19Executor:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, *, evaluated_at_ms: int, artifact_manifests):
            assert artifact_manifests == ()
            readiness_times.append(evaluated_at_ms)
            digest = canonical_sha256(
                {"domain": "test.p7d2.readiness-time.v1", "at": evaluated_at_ms}
            )
            readiness_hashes.append(digest)
            readiness = SimpleNamespace(
                verification_ready=True,
                readiness_sha256=digest,
                verification_plan_id=active_plan.verification_plan_id,
                verification_plan_sha256=active_plan.plan_sha256,
                request_id=request_id,
                run_id=run_id,
                generation=1,
                registry_snapshot_sha256=active_plan.registry_snapshot_sha256,
                evaluated_at_ms=evaluated_at_ms,
                has_valid_identity=lambda: True,
            )
            persisted_readiness.append(readiness)
            return readiness

    monkeypatch.setattr(
        "total_gateway.verification_plan_executor.VerificationPlanExecutor",
        P19Executor,
    )
    decision_hashes: list[str] = []
    completion_readiness_hashes: list[str] = []

    def completion(**kwargs):
        completion_readiness_hashes.append(
            kwargs["verification_readiness"].readiness_sha256
        )
        digest = canonical_sha256(
            {
                "domain": "test.p7d2.decision-time.v1",
                "readiness": kwargs["verification_readiness"].readiness_sha256,
                "text": kwargs["candidate_text"],
            }
        )
        decision_hashes.append(digest)
        return SimpleNamespace(decision_sha256=digest)

    monkeypatch.setattr(
        "total_gateway.orchestration.evaluate_desktop_completion",
        completion,
    )
    persisted_completions: list[dict] = []
    monkeypatch.setattr(
        "total_gateway.orchestration.persist_terminal_completion",
        lambda _store, _decision, **kwargs: persisted_completions.append(kwargs),
    )
    wall_ms = [first_wall_ms]
    monkeypatch.setattr(
        "total_gateway.orchestration.time.time_ns",
        lambda: wall_ms[0] * 1_000_000,
    )

    worker = object.__new__(GatewayOrchestrationWorker)
    worker._objects = object()
    worker._facts = object()
    worker._terminalize_composition_failure = lambda *_args, **_kwargs: None
    repository_reads: list[dict] = []

    def repository_evidence(request):
        repository_reads.append(dict(request))
        return {
            "schema": "test.repository-evidence.v1",
            "sample": len(repository_reads),
        }

    worker._repository_evidence_provider = repository_evidence
    life_commits: list[dict] = []

    def commit_life(payload):
        if life_commits and payload != life_commits[0]:
            raise AssertionError("life commit_conflict")
        life_commits.append(dict(payload))
        return _life_commit_result(payload)

    worker._life_execution_commit = commit_life

    def recover_life(method, path, payload, *, timeout_seconds):
        assert method == "POST"
        assert path == "/api/v1/v3/life/execution/recover"
        assert payload == {"request_id": request_id}
        assert timeout_seconds == 10.0
        if not life_commits:
            return 200, {"ok": True, "found": False}, "0" * 64
        stable = dict(life_commits[0])
        if stable.get("repository_evidence") is None:
            stable.pop("repository_evidence", None)
        stable["commit_sha256"] = canonical_sha256(
            {
                "domain": "tiangong.life.execution-commit.v1",
                "payload": stable,
            }
        )
        return (
            200,
            {"ok": True, "found": True, "execution": stable},
            "0" * 64,
        )

    worker._life_compat_client = SimpleNamespace(request=recover_life)
    crash_before_delivery = [True]
    delivery_evidence: list[tuple[str, str]] = []

    def advance(machine, entity_id, to_state, *, now_ms, **kwargs):
        if machine == "delivery" and to_state == "PLANNED" and crash_before_delivery[0]:
            raise CrashBeforeDelivery("post-Life/pre-delivery cutpoint")
        snapshot = SimpleNamespace(
            state=to_state,
            updated_at_ms=now_ms,
            is_terminal=to_state in {"COMPLETED", "DELIVERED"},
        )
        store.snapshots[(machine, entity_id)] = snapshot
        if machine == "delivery" and to_state == "CHANNEL_ACCEPTED":
            delivery_evidence.append((kwargs["fact_id"], kwargs["evidence_sha256"]))
        return snapshot

    worker._advance = advance
    response = SimpleNamespace(
        result=SimpleNamespace(fact_ids=("fact-parent",)),
        result_payload={"reply_text": "stable parent reply"},
        response_sha256="e" * 64,
    )

    with pytest.raises(CrashBeforeDelivery):
        _invoke_tail(
            worker,
            executor=executor,
            store=store,
            response=response,
            durable_resume=False,
        )
    assert store.snapshots[("execution", execution_entity)].updated_at_ms == (
        persisted_success_ms
    )
    assert len(life_commits) == 1
    assert repository_reads == []

    crash_before_delivery[0] = False
    wall_ms[0] = resumed_wall_ms
    _invoke_tail(
        worker,
        executor=executor,
        store=store,
        response=response,
        durable_resume=True,
    )

    assert executor.dispatch_calls == 0
    assert readiness_times == [persisted_success_ms]
    assert len(persisted_readiness) == 1
    assert completion_readiness_hashes == [readiness_hashes[0], readiness_hashes[0]]
    assert decision_hashes[0] == decision_hashes[1]
    assert len(life_commits) == 1
    assert repository_reads == []
    assert life_commits[0]["completed_at_ms"] == persisted_success_ms
    assert persisted_completions[0]["created_at_ms"] == persisted_success_ms
    expected_desktop_evidence = canonical_sha256(
        {
            "artifact_manifests": [],
            "completion_decision_sha256": decision_hashes[0],
            "domain": "tiangong.gateway.desktop-result-available.v1",
            "request_id": request_id,
            "response_sha256": response.response_sha256,
            "run_id": run_id,
        }
    )
    assert delivery_evidence == [
        (
            "fact-desktop-result-" + expected_desktop_evidence[:32],
            expected_desktop_evidence,
        )
    ]
    assert delivery_evidence[0][0] == life_commits[0]["fact_ids"][0]
    assert store.snapshots[("request", request_id)].state == "COMPLETED"
    assert store.completed_sessions == [("session-scope-p7d2", request_id)]


def test_life_journal_persist_failure_keeps_composition_tail_recoverable(
    tmp_path: Path,
) -> None:
    from life_service.embedded_runtime import EmbeddedLifeRuntime

    data_root = tmp_path / "life-data"
    runtime_root = tmp_path / "life-runtime"
    life = EmbeddedLifeRuntime(
        data_root=data_root,
        runtime_root=runtime_root,
        mode="embedded",
    )
    request_id = "req_" + "7" * 64
    run_id = derive_run_identity(request_id, 1).run_id
    life_id = str(life._active()["life_id"])
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._life_execution_commit = life.commit_execution
    worker._life_compat_client = life
    repository_reads: list[object] = []
    worker._repository_evidence_provider = lambda value: (
        repository_reads.append(value) or {"sample": len(repository_reads)}
    )

    original_persist = life._persist
    persist_failures = [0]

    def fail_once(life_id_arg="", *, force=False):
        if persist_failures[0] == 0:
            persist_failures[0] += 1
            raise OSError("disk-full-after-journal")
        return original_persist(life_id_arg, force=force)

    life._persist = fail_once
    call = {
        "request_id": request_id,
        "run_id": run_id,
        "generation": 1,
        "life_id": life_id,
        "session_scope_hash": "a" * 64,
        "principal_scope_hash": "b" * 64,
        "workspace_id": "workspace-p7d2-life",
        "user_goal": "finish the durable composition",
        "final_result": "verified result",
        "fact_ids": ("fact-p7d2-life",),
        "completed_at_ms": 5_000,
        "require_authority": True,
    }
    try:
        with pytest.raises(OrchestrationError) as caught:
            worker._commit_life_execution(**call)
        assert caught.value.code == "orchestration.life.tail_retry_required"
        assert caught.value.ambiguous is True
        assert repository_reads == []

        class PreserveStore:
            def get_snapshot(self, machine, entity_id):
                assert machine == "request"
                assert entity_id == request_id
                return SimpleNamespace(state="VALIDATING_ARTIFACTS")

            def __getattr__(self, name):
                raise AssertionError(f"tail retry attempted mutation: {name}")

        worker._store = PreserveStore()
        activation = SimpleNamespace(
            entry=SimpleNamespace(
                request_id=request_id,
                session_scope_hash="a" * 64,
            ),
            generation=SimpleNamespace(
                run_id=run_id,
                generation=1,
            ),
        )
        worker._finalize_unhandled(activation, caught.value)
    finally:
        life._persist = original_persist
        life.close()

    recovered_life = EmbeddedLifeRuntime(
        data_root=data_root,
        runtime_root=runtime_root,
        mode="embedded",
    )
    try:
        worker._life_execution_commit = recovered_life.commit_execution
        worker._life_compat_client = recovered_life
        recovered = worker._commit_life_execution(
            **call,
            recover_existing=True,
        )
        assert recovered is not None
        assert recovered["duplicate"] is True
        assert recovered["execution"]["request_id"] == request_id
        assert repository_reads == []
        status, journal, _ = recovered_life.request(
            "GET",
            "/api/v1/v3/life/journal/verify",
            None,
        )
        assert status == 200
        assert journal["valid"] is True
        assert journal["event_count"] == 1
    finally:
        recovered_life.close()


def test_malformed_life_commit_response_never_crosses_completion_tail() -> None:
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._life_execution_commit = lambda _payload: {"ok": True}
    recover_calls: list[str] = []

    def recover(method, path, payload, *, timeout_seconds):
        recover_calls.append(payload["request_id"])
        return 200, {"ok": True, "found": False}, "0" * 64

    worker._life_compat_client = SimpleNamespace(request=recover)
    worker._repository_evidence_provider = lambda _value: (_ for _ in ()).throw(
        AssertionError("strict composition tail sampled repository provider")
    )
    with pytest.raises(OrchestrationError) as caught:
        worker._commit_life_execution(
            request_id="req_" + "8" * 64,
            run_id="run_" + "9" * 64,
            generation=1,
            life_id="life-p7d2",
            session_scope_hash="a" * 64,
            principal_scope_hash="b" * 64,
            workspace_id="workspace-p7d2-life",
            user_goal="finish",
            final_result="done",
            fact_ids=("fact-p7d2-life",),
            completed_at_ms=5_000,
            require_authority=True,
        )
    assert caught.value.code == "orchestration.life.tail_retry_required"
    assert caught.value.ambiguous is True
    assert len(recover_calls) == 2


def test_life_commit_response_rejects_equal_but_type_changed_core() -> None:
    worker = object.__new__(GatewayOrchestrationWorker)

    def type_changed(payload):
        result = _life_commit_result(payload)
        result["execution"]["generation"] = 1.0
        return result

    worker._life_execution_commit = type_changed
    worker._life_compat_client = SimpleNamespace(
        request=lambda *_args, **_kwargs: (
            200,
            {"ok": True, "found": False},
            "0" * 64,
        )
    )
    worker._repository_evidence_provider = None
    with pytest.raises(OrchestrationError) as caught:
        worker._commit_life_execution(
            request_id="req_" + "a" * 64,
            run_id="run_" + "b" * 64,
            generation=1,
            life_id="life-p7d2",
            session_scope_hash="c" * 64,
            principal_scope_hash="d" * 64,
            workspace_id="workspace-p7d2-life",
            user_goal="finish",
            final_result="done",
            fact_ids=("fact-p7d2-life",),
            completed_at_ms=5_000,
            require_authority=True,
        )
    assert caught.value.code == "orchestration.life.tail_retry_required"


def test_life_recovery_requires_the_commit_authority() -> None:
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._life_execution_commit = None
    with pytest.raises(OrchestrationError) as caught:
        worker._commit_life_execution(
            request_id="req_" + "1" * 64,
            run_id="run_" + "2" * 64,
            generation=1,
            life_id="life-p7d2",
            session_scope_hash="3" * 64,
            principal_scope_hash="4" * 64,
            workspace_id="workspace-p7d2-life",
            user_goal="finish",
            final_result="done",
            fact_ids=("fact-p7d2-life",),
            completed_at_ms=5_000,
            recover_existing=True,
        )
    assert caught.value.code == "orchestration.life.tail_retry_required"


def test_sealed_composition_tail_bypasses_generic_reexecution_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_id = "req_" + "c" * 64
    run_id = derive_run_identity(request_id, 1).run_id
    decision = SimpleNamespace(
        request_id=request_id,
        run_id=run_id,
        generation=1,
        outcome="COMPLETED",
        can_transition_request_completed=True,
        has_valid_sha256=lambda: True,
    )
    store = SimpleNamespace(
        get_executable_composition_plan_for_request=(
            lambda *_args, **_kwargs: SimpleNamespace()
        ),
        list_completion_decisions=(
            lambda *_args, **_kwargs: (SimpleNamespace(decision=decision),)
        ),
    )
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._store = store
    activation = SimpleNamespace(
        entry=SimpleNamespace(request_id=request_id),
        generation=SimpleNamespace(
            run_id=run_id,
            generation=1,
            revision=99,
        ),
    )
    monkeypatch.setenv("TIANGONG_REQUEST_REEXECUTION", "0")

    assert worker._has_sealed_composition_tail(activation) is True
    assert worker._should_reexecute_without_outbox(activation) is True


def test_startup_retires_terminal_request_left_on_active_session() -> None:
    request_id = "req_" + "d" * 64
    completed: list[tuple[str, str, int, bool]] = []
    pending = [request_id]

    def complete(
        session_scope_hash,
        completed_request_id,
        *,
        completed_at_ms,
        release_generation,
    ):
        completed.append(
            (
                session_scope_hash,
                completed_request_id,
                completed_at_ms,
                release_generation,
            )
        )
        pending.clear()

    worker = object.__new__(GatewayOrchestrationWorker)
    worker._store = SimpleNamespace(
        list_terminal_active_session_request_ids=(
            lambda **_kwargs: tuple(pending)
        ),
        get_request_entry=(
            lambda candidate: SimpleNamespace(
                request_id=candidate,
                session_scope_hash="e" * 64,
            )
        ),
        complete_session_request=complete,
    )

    assert worker._retire_one_stranded_terminal_session(now_ms=9_000) is True
    assert completed == [("e" * 64, request_id, 9_000, False)]
    assert worker._retire_one_stranded_terminal_session(now_ms=9_001) is False


def test_startup_watchdog_preserves_exact_fact_backed_composition_parent(
    tmp_path: Path,
) -> None:
    runtime = resume._runtime(tmp_path / "startup-exact-fact", message_ref="exact-fact")
    try:
        claim = resume._claim_parent(runtime)
        _response, batch = resume._record_parent_fact(runtime, claim)
        runtime.store.mark_effect_started(claim.effect_id, started_at_ms=1_300)
        runtime.worker._store = resume._StoreHarness(
            runtime.store,
            runtime.plan_record,
            None,
        )

        protected = runtime.worker._composition_parent_started_effect_ids()
        recovered = runtime.store.recover_started_effects(
            now_ms=1_400,
            exclude_pipeline_versions=("tiangong.composition-step.v1",),
            exclude_effect_ids=protected,
        )

        head = runtime.store.get_effect(claim.effect_id)
        assert protected == (claim.effect_id,)
        assert recovered == ()
        assert head is not None
        assert head.state == "SIDE_EFFECT_STARTED"
        assert head.result is None
        assert runtime.facts.get_batch_for_effect(claim.effect_id) == batch
    finally:
        resume._close(runtime)


@pytest.mark.parametrize(
    ("request_state", "delivery_state"),
    [
        ("FAILED", "NOT_PLANNED"),
        ("EXECUTING", "CANCELLED"),
    ],
)
def test_terminal_shared_tail_state_rejects_before_any_external_authority(
    monkeypatch: pytest.MonkeyPatch,
    request_state: str,
    delivery_state: str,
) -> None:
    parent_effect_id = "eff_" + "1" * 64
    executor = _CompletedDagExecutor(
        SimpleNamespace(
            parent_effect_id=parent_effect_id,
            leaf_effect_ids=("eff_" + "3" * 64,),
            lineage_effect_ids=("eff_" + "2" * 64, "eff_" + "3" * 64),
            fact_ids=("fact-child-first", "fact-child-leaf"),
            final_output_aliases={},
            completed_at_ms=2_000,
        )
    )
    request_id = "req_" + "4" * 64
    run_id = derive_run_identity(request_id, 1).run_id
    store = _TailStore(
        {
            ("request", request_id): SimpleNamespace(
                state=request_state,
                updated_at_ms=2_000,
                is_terminal=request_state == "FAILED",
            ),
            ("execution", "execution-" + run_id): SimpleNamespace(
                state="SUCCEEDED", updated_at_ms=2_000, is_terminal=True
            ),
            ("delivery", "delivery-" + run_id): SimpleNamespace(
                state=delivery_state,
                updated_at_ms=2_000,
                is_terminal=delivery_state == "CANCELLED",
            ),
        }
    )
    active_plan = SimpleNamespace(registry_snapshot_sha256="a" * 64)
    store.get_active_verification_plan = lambda **_kwargs: active_plan
    store.get_latest_verification_readiness = lambda **_kwargs: None
    monkeypatch.setattr(
        "total_gateway.orchestration._verification_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    external_calls = {"p19": 0, "completion": 0, "life": 0}

    class P19Executor:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute(self, **_kwargs):
            external_calls["p19"] += 1
            return SimpleNamespace(
                verification_ready=True,
                readiness_sha256="b" * 64,
            )

    monkeypatch.setattr(
        "total_gateway.verification_plan_executor.VerificationPlanExecutor",
        P19Executor,
    )

    def completion(**_kwargs):
        external_calls["completion"] += 1
        return SimpleNamespace(decision_sha256="d" * 64)

    monkeypatch.setattr(
        "total_gateway.orchestration.evaluate_desktop_completion",
        completion,
    )
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._objects = object()
    worker._facts = object()
    worker._repository_evidence_provider = None
    worker._terminalize_composition_failure = lambda *_args, **_kwargs: None
    worker._advance = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("terminal shared tail attempted a transition")
    )

    def commit_life(_payload):
        external_calls["life"] += 1
        return {"ok": True}

    worker._life_execution_commit = commit_life
    response = SimpleNamespace(
        result=SimpleNamespace(fact_ids=("fact-parent",)),
        result_payload={"reply_text": "must not continue"},
        response_sha256="e" * 64,
    )

    with pytest.raises(
        OrchestrationError,
        match="orchestration.resume.terminal_state_conflict",
    ):
        _invoke_tail(
            worker,
            executor=executor,
            store=store,
            response=response,
            durable_resume=True,
        )

    assert executor.project_calls == 0
    assert external_calls == {"p19": 0, "completion": 0, "life": 0}
    if delivery_state == "CANCELLED":
        assert external_calls["life"] == 0


def test_completion_clock_covers_latest_leaf_fact_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_effect_id = "eff_" + "1" * 64
    first_effect_id = "eff_" + "2" * 64
    leaf_effect_id = "eff_" + "3" * 64
    executor = _CompletedDagExecutor(
        SimpleNamespace(
            parent_effect_id=parent_effect_id,
            leaf_effect_ids=(leaf_effect_id,),
            lineage_effect_ids=(first_effect_id, leaf_effect_id),
            fact_ids=("fact-child-first", "fact-child-leaf"),
            final_output_aliases={},
            completed_at_ms=5_000,
        )
    )
    request_id = "req_" + "4" * 64
    run_id = derive_run_identity(request_id, 1).run_id
    leaf_observed_at_ms = 5_000
    store = _TailStore(
        {
            ("request", request_id): SimpleNamespace(
                state="EXECUTING", updated_at_ms=2_000, is_terminal=False
            ),
            ("execution", "execution-" + run_id): SimpleNamespace(
                state="SUCCEEDED", updated_at_ms=2_000, is_terminal=True
            ),
            ("delivery", "delivery-" + run_id): SimpleNamespace(
                state="NOT_PLANNED", updated_at_ms=2_000, is_terminal=False
            ),
        }
    )

    class Facts:
        def get_batch_for_effect(self, effect_id, *, verify_payload=True):
            assert verify_payload is True
            observed_at_ms = (
                leaf_observed_at_ms if effect_id == leaf_effect_id else 4_000
            )
            return SimpleNamespace(observed_at_ms=observed_at_ms)

    completion_times: list[int] = []
    persisted_times: list[int] = []
    monkeypatch.setattr(
        "total_gateway.orchestration.evaluate_desktop_completion",
        lambda **_kwargs: SimpleNamespace(decision_sha256="d" * 64),
    )
    monkeypatch.setattr(
        "total_gateway.orchestration.persist_terminal_completion",
        lambda _store, _decision, **kwargs: persisted_times.append(
            kwargs["created_at_ms"]
        ),
    )
    worker = object.__new__(GatewayOrchestrationWorker)
    worker._objects = object()
    worker._facts = Facts()
    worker._repository_evidence_provider = None
    worker._terminalize_composition_failure = lambda *_args, **_kwargs: None

    def commit_life(payload):
        completion_times.append(payload["completed_at_ms"])
        return _life_commit_result(payload)

    worker._life_execution_commit = commit_life
    worker._life_compat_client = SimpleNamespace(
        request=lambda *_args, **_kwargs: (
            200,
            {"ok": True, "found": False},
            "0" * 64,
        )
    )

    def advance(machine, entity_id, to_state, *, now_ms, **_kwargs):
        snapshot = SimpleNamespace(
            state=to_state,
            updated_at_ms=now_ms,
            is_terminal=to_state in {"COMPLETED", "DELIVERED"},
        )
        store.snapshots[(machine, entity_id)] = snapshot
        return snapshot

    worker._advance = advance
    response = SimpleNamespace(
        result=SimpleNamespace(fact_ids=("fact-parent",)),
        result_payload={"reply_text": "leaf-backed completion"},
        response_sha256="e" * 64,
    )

    _invoke_tail(
        worker,
        executor=executor,
        store=store,
        response=response,
        durable_resume=True,
    )

    assert completion_times == [leaf_observed_at_ms]
    assert persisted_times == [leaf_observed_at_ms]


def _build_p19_artifact_runtime(artifact_bytes, minimum_chars):
    runtime = M21OracleTestBase(methodName="runTest")
    runtime.setUp()
    runtime._register_lineage_request()
    assert runtime.gateway_store.put_registry_snapshot(
        runtime.snapshot,
        recorded_at_ms=1_500,
    )
    manifest = runtime._passed_manifest(
        artifact_bytes,
        filename="partial-prefix.docx",
        format_id="docx",
        declared_mime=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )
    entries = []
    for minimum in minimum_chars:
        predicate = AcceptancePredicate.create(
            predicate_type="artifact.min_visible_text_chars",
            subject_kind="artifact",
            params={"min_chars": minimum},
        )
        entries.append(
            VerificationPlanEntryV2(
                plan_entry_id="vpe_" + "0" * 64,
                verifier_id="verifier.artifact_content",
                verifier_version="3",
                predicate=predicate,
                subject_identity=manifest.artifact_revision_id,
                evaluation_phase="POST_EXECUTION",
                required=True,
                entry_sha256="0" * 64,
            ).with_computed_sha256()
        )
    plan = VerificationPlan(
        verification_plan_id="vpl_" + "0" * 64,
        request_id=runtime.request.request_id,
        run_id=runtime.run.run_id,
        generation=2,
        registry_snapshot_sha256=runtime.snapshot.snapshot_sha256,
        entries=tuple(sorted(entries, key=lambda item: item.plan_entry_id)),
        plan_sha256="0" * 64,
    ).with_computed_sha256()
    assert runtime.gateway_store.put_verification_plan(
        plan,
        recorded_at_ms=1_600,
    )
    runtime.gateway_store.activate_verification_plan(
        request_id=plan.request_id,
        run_id=plan.run_id,
        generation=plan.generation,
        verification_plan_id=plan.verification_plan_id,
        verification_plan_sha256=plan.plan_sha256,
        registry_snapshot_sha256=plan.registry_snapshot_sha256,
        activated_at_ms=1_700,
    )
    return runtime, plan, manifest


def test_executor_resume_reuses_stable_clock_record_and_only_runs_missing_entry(
) -> None:
    stable_evaluated_at_ms = 31_000
    runtime = None
    try:
        runtime, plan, manifest = _build_p19_artifact_runtime(
            docx_bytes("字" * 50),
            (200, 300),
        )
        first_entry, second_entry = plan.entries
        interrupted = VerificationPlanExecutor(
            snapshot=runtime.snapshot,
            store=runtime.gateway_store,
            object_store=runtime.object_store,
            fact_ledger=runtime.fact_ledger,
            plan=plan,
        )
        interrupted._dispatch_entry(
            first_entry,
            evaluated_at_ms=stable_evaluated_at_ms,
            manifests_by_revision={manifest.artifact_revision_id: manifest},
        )
        prefix_records = runtime.gateway_store.list_verification_records(
            request_id=plan.request_id,
            run_id=plan.run_id,
            generation=plan.generation,
        )
        assert len(prefix_records) == 1
        persisted_first = prefix_records[0]

        oracle_calls: list[str] = []

        class CountingOracle:
            def __init__(self, delegate) -> None:
                self._delegate = delegate

            def evaluate(self, manifest_, predicate, **kwargs):
                oracle_calls.append(predicate.predicate_id)
                return self._delegate.evaluate(manifest_, predicate, **kwargs)

        recovered = VerificationPlanExecutor(
            snapshot=runtime.snapshot,
            store=runtime.gateway_store,
            object_store=runtime.object_store,
            fact_ledger=runtime.fact_ledger,
            plan=plan,
            resume_evaluated_at_ms=stable_evaluated_at_ms,
        )
        recovered._artifact_oracle = CountingOracle(recovered._artifact_oracle)
        readiness = recovered.execute(
            evaluated_at_ms=stable_evaluated_at_ms,
            artifact_manifests=(manifest,),
        )

        records = runtime.gateway_store.list_verification_records(
            request_id=plan.request_id,
            run_id=plan.run_id,
            generation=plan.generation,
        )
        by_predicate = {item.predicate_id: item for item in records}
        assert oracle_calls == [second_entry.predicate.predicate_id]
        assert len(records) == 2
        assert canonical_json_bytes(
            by_predicate[first_entry.predicate.predicate_id].model_dump(
                mode="json"
            )
        ) == canonical_json_bytes(persisted_first.model_dump(mode="json"))
        assert {item.evaluated_at_ms for item in records} == {
            stable_evaluated_at_ms
        }
        assert not readiness.verification_ready
        assert len(readiness.entry_assessments) == 2
    finally:
        if runtime is not None:
            runtime.tearDown()


def test_partial_disposition_prefix_resumes_without_generation_budget_drift(
) -> None:
    """A stable readiness retry reuses its prefix and only fills its suffix."""

    stable_now_ms = 41_000
    artifact_bytes = docx_bytes("字" * 50)

    baseline = resumed = None
    try:
        baseline, baseline_plan, baseline_manifest = (
            _build_p19_artifact_runtime(
                artifact_bytes,
                (200, 250, 300, 350),
            )
        )
        baseline_readiness = VerificationPlanExecutor(
            snapshot=baseline.snapshot,
            store=baseline.gateway_store,
            object_store=baseline.object_store,
            fact_ledger=baseline.fact_ledger,
            plan=baseline_plan,
        ).execute(
            evaluated_at_ms=31_000,
            artifact_manifests=(baseline_manifest,),
        )
        assert not baseline_readiness.verification_ready
        assert len(baseline_readiness.entry_assessments) == 4
        baseline_dispositions = VerificationRepairCoordinator(
            store=baseline.gateway_store
        ).process_readiness(
            plan=baseline_plan,
            readiness=baseline_readiness,
            now_ms=stable_now_ms,
        )
        assert len(baseline_dispositions) == 4
        assert [item.action for item in baseline_dispositions] == ["REPAIR"] * 4

        resumed, resumed_plan, resumed_manifest = _build_p19_artifact_runtime(
            artifact_bytes,
            (200, 250, 300, 350),
        )
        resumed_readiness = VerificationPlanExecutor(
            snapshot=resumed.snapshot,
            store=resumed.gateway_store,
            object_store=resumed.object_store,
            fact_ledger=resumed.fact_ledger,
            plan=resumed_plan,
        ).execute(
            evaluated_at_ms=31_000,
            artifact_manifests=(resumed_manifest,),
        )
        assert resumed_plan.plan_sha256 == baseline_plan.plan_sha256
        assert (
            resumed_readiness.readiness_sha256
            == baseline_readiness.readiness_sha256
        )

        persisted_prefix = baseline_dispositions[0]
        prefix_evidence = (
            baseline.gateway_store.get_verification_failure_evidence_by_id(
                persisted_prefix.failure_evidence_id
            )
        )
        assert prefix_evidence is not None
        assert resumed.gateway_store.put_verification_failure_evidence(
            prefix_evidence,
            recorded_at_ms=stable_now_ms,
        )
        assert resumed.gateway_store.put_verification_disposition(
            persisted_prefix,
            recorded_at_ms=stable_now_ms,
        )

        new_disposition_ids: list[str] = []

        class CountingStore:
            def __getattr__(self, name):
                return getattr(resumed.gateway_store, name)

            def put_verification_disposition(self, disposition, **kwargs):
                new_disposition_ids.append(
                    disposition.verification_disposition_id
                )
                return resumed.gateway_store.put_verification_disposition(
                    disposition,
                    **kwargs,
                )

        recovered_dispositions = VerificationRepairCoordinator(
            store=CountingStore()
        ).process_readiness(
            plan=resumed_plan,
            readiness=resumed_readiness,
            now_ms=stable_now_ms,
            reuse_persisted_prefix=True,
        )

        assert new_disposition_ids == [
            item.verification_disposition_id
            for item in baseline_dispositions[1:]
        ]
        assert [
            (item.verification_disposition_id, item.action)
            for item in recovered_dispositions
        ] == [
            (item.verification_disposition_id, item.action)
            for item in baseline_dispositions
        ]
        assert canonical_json_bytes(
            [item.model_dump(mode="json") for item in recovered_dispositions]
        ) == canonical_json_bytes(
            [item.model_dump(mode="json") for item in baseline_dispositions]
        )
        stored_dispositions = resumed.gateway_store.list_all_verification_dispositions(
            request_id=resumed_plan.request_id,
            run_id=resumed_plan.run_id,
            generation=resumed_plan.generation,
        )
        assert {
            (item.verification_disposition_id, item.action)
            for item in stored_dispositions
        } == {
            (item.verification_disposition_id, item.action)
            for item in baseline_dispositions
        }
    finally:
        if resumed is not None:
            resumed.tearDown()
        if baseline is not None:
            baseline.tearDown()
