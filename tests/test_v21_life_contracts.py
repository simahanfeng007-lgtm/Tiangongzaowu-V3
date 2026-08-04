from __future__ import annotations

import pytest
import tempfile
from pathlib import Path
from pydantic import ValidationError

from contracts import ActionCandidateVNext, CausalEpisodeVNext
from contracts.life import LifeAuthorityHead, RootExperienceHead, RunLifeBinding
from life_service.store import LifeShadowStore, LifeShadowStoreError


H = "a" * 64
RUN = "run_" + "1" * 64
EVENT = "lev_" + "2" * 64


def authority() -> LifeAuthorityHead:
    return LifeAuthorityHead(
        life_id="life_main", writer_epoch=1, identity_revision=1, identity_sha256=H,
        soul_revision=1, soul_sha256=H, affect_revision=1, affect_sha256=H,
        deletion_epoch=0, head_sha256="0" * 64,
    ).with_computed_head_sha256()


def test_authority_head_is_self_hashing_and_immutable() -> None:
    item = authority()
    assert item.head_sha256 == item.computed_head_sha256()
    assert item.model_copy(update={"identity_revision": 2}).head_sha256 != item.model_copy(update={"identity_revision": 2}).computed_head_sha256()


def test_request_binding_requires_a_complete_run_tuple_and_exact_subject() -> None:
    values = dict(
        binding_id="bind_1", life_id="life_main", binding_subject_kind="request",
        binding_subject_id=RUN, binding_subject_sha256=H, life_authority_head_sha256=H,
        writer_epoch=1, identity_revision=1, identity_sha256=H, soul_revision=1,
        soul_sha256=H, affect_revision=1, affect_sha256=H, deletion_epoch=0,
        bound_at_ms=1, binding_source="gateway", request_id="req_" + "3" * 64,
        run_id=RUN, run_sequence=1, generation=0, binding_sha256="0" * 64,
    )
    assert RunLifeBinding(**values).with_computed_binding_sha256().binding_subject_id == RUN
    with pytest.raises(ValidationError):
        RunLifeBinding(**{**values, "run_sequence": None})
    with pytest.raises(ValidationError):
        RunLifeBinding(**{**values, "binding_subject_id": "other"})


def test_root_terminal_and_waiting_shapes_are_not_interchangeable() -> None:
    values = dict(root_experience_id="root_1", life_id="life_main", initial_run_life_binding_sha256=H,
        active_run_life_binding_sha256=H, root_trigger_event_id=EVENT, root_trigger_event_sha256=H,
        next_sequence_no=1, root_status="OPEN", head_sha256="0" * 64)
    assert RootExperienceHead(**values).with_computed_head_sha256().root_status == "OPEN"
    with pytest.raises(ValidationError):
        RootExperienceHead(**{**values, "root_status": "CLOSED"})
    with pytest.raises(ValidationError):
        RootExperienceHead(**{**values, "waiting_question_id": "question_1"})


def test_two_actions_are_two_ordered_children() -> None:
    first = CausalEpisodeVNext(
        episode_id="cep_" + "4" * 64, life_id="life_main", root_experience_id="root_1",
        sequence_no=1, episode_kind="external_action", run_life_binding_sha256=H,
        candidate_ids=("candidate_1",), selected_candidate_id="candidate_1", terminal_status="CLOSED",
        terminal_reason_code="outcome.recorded", created_at_ms=1, closed_at_ms=2, episode_sha256="0" * 64,
    ).with_computed_episode_sha256()
    second = CausalEpisodeVNext(
        episode_id="cep_" + "5" * 64, life_id="life_main", root_experience_id="root_1",
        sequence_no=2, predecessor_episode_id=first.episode_id, predecessor_episode_sha256=first.episode_sha256,
        episode_kind="external_action", run_life_binding_sha256=H, candidate_ids=("candidate_2",),
        selected_candidate_id="candidate_2", terminal_status="CLOSED", terminal_reason_code="outcome.recorded",
        created_at_ms=3, closed_at_ms=4, episode_sha256="0" * 64,
    ).with_computed_episode_sha256()
    assert (first.sequence_no, second.sequence_no) == (1, 2)
    with pytest.raises(ValidationError):
        CausalEpisodeVNext(**{**second.model_dump(), "predecessor_episode_sha256": None})


@pytest.mark.parametrize(
    "kind,executable,needs_question",
    [
        ("action", True, False),
        ("minimal_probe", True, False),
        ("observation", False, False),
        ("reflection", False, False),
        ("wait", False, False),
        ("reject", False, False),
        ("no_op", False, False),
        ("ask_user", False, True),
        ("respond", False, True),
    ],
)
def test_candidate_shape_matches_kind(kind: str, executable: bool, needs_question: bool) -> None:
    values = dict(
        candidate_id="acd_" + "6" * 64, life_id="life_main", episode_id="cep_" + "4" * 64,
        candidate_kind=kind, objective="reply", expected_outcome="user sees result", evidence_refs=("fact_1",),
        proposed_at_ms=1, expires_at_ms=2, candidate_sha256="0" * 64,
    )
    if executable:
        values.update(action_id="file.write", args_sha256=H, workspace_id="ws_1")
    if needs_question:
        values["question_or_expression_ref"] = "expression_1"
    candidate = ActionCandidateVNext(**values).with_computed_candidate_sha256()
    assert (candidate.action_id is not None) == executable
    if executable:
        with pytest.raises(ValidationError):
            ActionCandidateVNext(**{k: v for k, v in values.items() if k not in {"action_id", "args_sha256", "workspace_id"}})
    else:
        with pytest.raises(ValidationError):
            ActionCandidateVNext(**{**values, "action_id": "file.write", "args_sha256": H, "workspace_id": "ws_1"})
    if needs_question:
        with pytest.raises(ValidationError):
            ActionCandidateVNext(**{**values, "question_or_expression_ref": None})
    else:
        with pytest.raises(ValidationError):
            ActionCandidateVNext(**{**values, "question_or_expression_ref": "expression_1"})


def test_child_store_requires_exact_predecessor_and_is_idempotent() -> None:
    head = authority()
    binding = RunLifeBinding(
        binding_id="bind_child_1", life_id="life_main", binding_subject_kind="request",
        binding_subject_id=RUN, binding_subject_sha256=H, life_authority_head_sha256=head.head_sha256,
        writer_epoch=1, identity_revision=1, identity_sha256=H, soul_revision=1, soul_sha256=H,
        affect_revision=1, affect_sha256=H, deletion_epoch=0, bound_at_ms=1, binding_source="gateway",
        request_id="req_" + "3" * 64, run_id=RUN, run_sequence=1, generation=0, binding_sha256="0" * 64,
    ).with_computed_binding_sha256()
    root = RootExperienceHead(
        root_experience_id="root_2", life_id="life_main", initial_run_life_binding_sha256=binding.binding_sha256,
        active_run_life_binding_sha256=binding.binding_sha256, root_trigger_event_id=EVENT,
        root_trigger_event_sha256=H, next_sequence_no=1, root_status="OPEN", head_sha256="0" * 64,
    ).with_computed_head_sha256()
    first = CausalEpisodeVNext(
        episode_id="cep_" + "7" * 64, life_id="life_main", root_experience_id="root_2", sequence_no=1,
        episode_kind="external_action", run_life_binding_sha256=binding.binding_sha256, candidate_ids=("c_1",),
        selected_candidate_id="c_1", terminal_status="CLOSED", terminal_reason_code="done",
        created_at_ms=1, closed_at_ms=2, episode_sha256="0" * 64,
    ).with_computed_episode_sha256()
    second = CausalEpisodeVNext(
        episode_id="cep_" + "8" * 64, life_id="life_main", root_experience_id="root_2", sequence_no=2,
        predecessor_episode_id=first.episode_id, predecessor_episode_sha256=first.episode_sha256,
        episode_kind="external_action", run_life_binding_sha256=binding.binding_sha256, candidate_ids=("c_2",),
        selected_candidate_id="c_2", terminal_status="CLOSED", terminal_reason_code="done",
        created_at_ms=3, closed_at_ms=4, episode_sha256="0" * 64,
    ).with_computed_episode_sha256()
    with tempfile.TemporaryDirectory() as temporary:
        with LifeShadowStore.open(Path(temporary) / "life.shadow.sqlite3", create=True, now_ms=1) as store:
            assert store.put_life_authority_head(head, expected_head_sha256=None)
            assert store.put_run_life_binding(binding)
            assert store.put_root_experience_head(root, expected_head_sha256=None)
            assert store.put_causal_episode_vnext(first)
            assert store.put_causal_episode_vnext(second)
            assert not store.put_causal_episode_vnext(second)
            with pytest.raises(LifeShadowStoreError):
                store.put_causal_episode_vnext(
                    second.model_copy(update={
                        "episode_id": "cep_" + "9" * 64, "sequence_no": 3, "predecessor_episode_sha256": H,
                    }).with_computed_episode_sha256()
                )
            with pytest.raises(LifeShadowStoreError):
                store.put_causal_episode_vnext(
                    first.model_copy(update={
                        "terminal_reason_code": "tampered", "episode_sha256": "0" * 64,
                    }).with_computed_episode_sha256()
                )
            with pytest.raises(LifeShadowStoreError):
                store.put_causal_episode_vnext(
                    CausalEpisodeVNext(**{
                        **second.model_dump(), "episode_id": "cep_" + "a" * 64, "root_experience_id": "root_ghost",
                    }).with_computed_episode_sha256()
                )
