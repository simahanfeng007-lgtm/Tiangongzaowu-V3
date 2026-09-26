"""Only a structured clarification signal changes runtime lifecycle state."""
import pytest
from v3 import execution_integrity as integrity
from v3.simple_chain import kernel


@pytest.mark.parametrize('text', ['把那个文件改成英文版', 'Which file?', '请问您指哪位王总？', '已完成。'])
def test_words_do_not_create_a_clarification_state(text):
    contract = integrity.initialize_task_contract(text)
    _, allowed, status, reasons = kernel._simple_chain_life_completion_gate(
        text, [], [], task_contract=contract, final_reply=text)
    assert allowed and status == 'complete' and reasons == []
    assert not kernel._simple_chain_is_clarification_question(text)


def test_explicit_clarification_state_still_parks_the_task():
    contract = integrity.initialize_task_contract('opaque task text')
    updated, allowed, status, reasons = integrity.decide_task_contract_completion(
        contract, evidence_status='clarify')
    assert allowed and status == 'clarify' and reasons == []
    assert updated['phase'] == 'WAITING'
