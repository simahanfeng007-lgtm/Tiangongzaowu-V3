"""G1 gate evidence: identity state, true CAS, root continuation, dual-read.

These tests are wired into ``scripts/run_v21_gate.py`` for T03a_identity_state,
T04_true_CAS, T28_root_continuation and T19b_contract_runtime (life contracts).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts import CausalEpisode, CausalEpisodeVNext
from contracts.life import (
    LifeAuthorityHead,
    RootContinuationBinding,
    RootExperienceHead,
    RunLifeBinding,
)
from life_service.store import LifeShadowStore, LifeShadowStoreError


H = "a" * 64
RUN = "run_" + "1" * 64
REQ = "req_" + "2" * 64
RUN2 = "run_" + "3" * 64
REQ2 = "req_" + "4" * 64
EVENT = "lev_" + "3" * 64


def make_head(
    *,
    life: str = "life_main",
    identity_revision: int = 1,
    soul_revision: int = 1,
    affect_revision: int = 1,
    deletion_epoch: int = 0,
) -> LifeAuthorityHead:
    return LifeAuthorityHead(
        life_id=life, writer_epoch=1, identity_revision=identity_revision, identity_sha256=H,
        soul_revision=soul_revision, soul_sha256=H, affect_revision=affect_revision, affect_sha256=H,
        deletion_epoch=deletion_epoch, head_sha256="0" * 64,
    ).with_computed_head_sha256()


def make_binding(
    *,
    head_sha256: str,
    binding_id: str = "bind_1",
    life: str = "life_main",
    run: str = RUN,
    req: str = REQ,
    run_sequence: int = 1,
    generation: int = 0,
) -> RunLifeBinding:
    return RunLifeBinding(
        binding_id=binding_id, life_id=life, binding_subject_kind="request",
        binding_subject_id=run, binding_subject_sha256=H, life_authority_head_sha256=head_sha256,
        writer_epoch=1, identity_revision=1, identity_sha256=H, soul_revision=1, soul_sha256=H,
        affect_revision=1, affect_sha256=H, deletion_epoch=0, bound_at_ms=1, binding_source="gateway",
        request_id=req, run_id=run, run_sequence=run_sequence, generation=generation, binding_sha256="0" * 64,
    ).with_computed_binding_sha256()


def make_root(
    *,
    root_id: str = "root_1",
    active_binding_sha256: str,
    status: str = "OPEN",
    waiting_question_id: str | None = None,
) -> RootExperienceHead:
    return RootExperienceHead(
        root_experience_id=root_id, life_id="life_main",
        initial_run_life_binding_sha256=active_binding_sha256,
        active_run_life_binding_sha256=active_binding_sha256,
        root_trigger_event_id=EVENT, root_trigger_event_sha256=H, next_sequence_no=1,
        root_status=status, waiting_question_id=waiting_question_id, head_sha256="0" * 64,
    ).with_computed_head_sha256()


def make_continuation(
    *,
    continuation_id: str = "cont_1",
    previous_root_head_sha256: str,
    previous_binding_sha256: str,
    next_binding_sha256: str,
    reply_to_question_id: str = "q1",
) -> RootContinuationBinding:
    return RootContinuationBinding(
        continuation_id=continuation_id, root_experience_id="root_1",
        reply_to_question_id=reply_to_question_id, previous_binding_sha256=previous_binding_sha256,
        next_binding_sha256=next_binding_sha256, answer_event_id=EVENT, answer_event_sha256=H,
        previous_root_head_sha256=previous_root_head_sha256, continuation_sha256="0" * 64,
    ).with_computed_continuation_sha256()


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "life.shadow.sqlite3"
        with LifeShadowStore.open(path, create=True, now_ms=1) as instance:
            yield instance


def test_t03a_binding_captures_exact_authority_head_and_stale_revisions_are_rejected(store) -> None:
    h1 = make_head()
    assert store.put_life_authority_head(h1, expected_head_sha256=None)
    assert store.put_run_life_binding(make_binding(head_sha256=h1.head_sha256))
    with pytest.raises(LifeShadowStoreError, match="authority head"):
        store.put_run_life_binding(make_binding(head_sha256="0" * 64, binding_id="bind_stale"))
    h2 = make_head(identity_revision=2, soul_revision=2, affect_revision=2)
    assert store.put_life_authority_head(h2, expected_head_sha256=h1.head_sha256)
    with pytest.raises(LifeShadowStoreError, match="authority head"):
        store.put_run_life_binding(make_binding(head_sha256=h1.head_sha256, binding_id="bind_h1_after"))
    assert store.put_run_life_binding(
        make_binding(head_sha256=h2.head_sha256, binding_id="bind_h2", run=RUN2, req=REQ2)
    )
    with pytest.raises(LifeShadowStoreError, match="authority head"):
        store.put_run_life_binding(make_binding(head_sha256=h2.head_sha256, binding_id="bind_no_head", life="life_other"))


def test_t04_true_cas_rejects_stale_commit_after_every_authority_revision_changes(store) -> None:
    h1 = make_head()
    assert store.put_life_authority_head(h1, expected_head_sha256=None)
    h2 = make_head(
        identity_revision=2, soul_revision=2, affect_revision=2, deletion_epoch=1,
    )
    assert store.put_life_authority_head(h2, expected_head_sha256=h1.head_sha256)
    h3 = make_head(
        identity_revision=3, soul_revision=3, affect_revision=3, deletion_epoch=2,
    )
    with pytest.raises(LifeShadowStoreError, match="CAS is stale"):
        store.put_life_authority_head(h3, expected_head_sha256=h1.head_sha256)
    with pytest.raises(LifeShadowStoreError, match="authority head"):
        store.put_run_life_binding(make_binding(head_sha256=h1.head_sha256, binding_id="bind_stale"))


def test_t28_waiting_answer_creates_continuation_binding_and_resumes_root_once(store) -> None:
    h1 = make_head()
    assert store.put_life_authority_head(h1, expected_head_sha256=None)
    b1 = make_binding(head_sha256=h1.head_sha256)
    assert store.put_run_life_binding(b1)
    waiting = make_root(active_binding_sha256=b1.binding_sha256, status="WAITING", waiting_question_id="q1")
    assert store.put_root_experience_head(waiting, expected_head_sha256=None)

    b2 = make_binding(head_sha256=h1.head_sha256, binding_id="bind_2", run=RUN2, req=REQ2)
    assert store.put_run_life_binding(b2)
    resumed = make_root(active_binding_sha256=b2.binding_sha256, status="OPEN")

    with pytest.raises(LifeShadowStoreError, match="continuation"):
        store.put_root_experience_head(resumed, expected_head_sha256=waiting.head_sha256)
    wrong_question = make_continuation(
        previous_root_head_sha256=waiting.head_sha256,
        previous_binding_sha256=b1.binding_sha256, next_binding_sha256=b2.binding_sha256,
        reply_to_question_id="q2",
    )
    with pytest.raises(LifeShadowStoreError, match="preconditions"):
        store.put_root_continuation_binding(wrong_question, next_head=resumed)
    stale_head = make_continuation(
        continuation_id="cont_stale", previous_root_head_sha256="0" * 64,
        previous_binding_sha256=b1.binding_sha256, next_binding_sha256=b2.binding_sha256,
    )
    with pytest.raises(LifeShadowStoreError, match="CAS is stale"):
        store.put_root_continuation_binding(stale_head, next_head=resumed)

    good = make_continuation(
        previous_root_head_sha256=waiting.head_sha256,
        previous_binding_sha256=b1.binding_sha256, next_binding_sha256=b2.binding_sha256,
    )
    assert store.put_root_continuation_binding(good, next_head=resumed)
    assert not store.put_root_continuation_binding(good, next_head=resumed)
    again = make_continuation(
        continuation_id="cont_2", previous_root_head_sha256=resumed.head_sha256,
        previous_binding_sha256=b2.binding_sha256, next_binding_sha256=b2.binding_sha256,
    )
    with pytest.raises(LifeShadowStoreError, match="preconditions"):
        store.put_root_continuation_binding(again, next_head=resumed)


def test_t19b_legacy_and_vnext_contracts_dual_read_with_strict_vnext(store) -> None:
    legacy = CausalEpisode(
        episode_id="cep_" + "4" * 64, life_id="life_main", revision=1, supersedes_episode_sha256=None,
        trigger_event_ids=(EVENT,), context_state_hashes=(H,), intention="intend", prior_prediction="predict",
        candidate_action_ids=("cand_1",), selected_action_id=None, terminal_status="OPEN",
        created_at_ms=1, episode_sha256="0" * 64,
    ).with_computed_episode_sha256()
    assert legacy.has_valid_episode_sha256()
    with pytest.raises(ValidationError):
        CausalEpisodeVNext(**{
            **legacy.model_dump(),
            "sequence_no": 1, "episode_kind": "no_op", "run_life_binding_sha256": H,
        })
    assert store.put_causal_episode(legacy)
    head = make_head()
    assert store.put_life_authority_head(head, expected_head_sha256=None)
    binding = make_binding(head_sha256=head.head_sha256, binding_id="bind_dual")
    assert store.put_run_life_binding(binding)
    root = make_root(root_id="root_dual", active_binding_sha256=binding.binding_sha256)
    assert store.put_root_experience_head(root, expected_head_sha256=None)
    vnext = CausalEpisodeVNext(
        episode_id="cep_" + "5" * 64, life_id="life_main", root_experience_id="root_dual",
        sequence_no=1, episode_kind="no_op", run_life_binding_sha256=binding.binding_sha256,
        candidate_ids=("cand_noop",), selected_candidate_id="cand_noop", terminal_status="CLOSED",
        terminal_reason_code="done", created_at_ms=1, closed_at_ms=2, episode_sha256="0" * 64,
    ).with_computed_episode_sha256()
    assert store.put_causal_episode_vnext(vnext)
    assert not store.put_causal_episode(legacy)
    assert not store.put_causal_episode_vnext(vnext)
