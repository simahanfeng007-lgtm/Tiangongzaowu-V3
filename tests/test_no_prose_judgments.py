"""Prose is model input, never an implicit runtime policy or artifact contract."""
from copy import deepcopy

import pytest

from v3 import execution_integrity as integrity
from v3.simple_chain import kernel
from life_service.explicit_memory import detect_explicit_intent
from tests.test_task_execution_simplification import observation
from tests import test_docx_qc as docx_support
from total_gateway.artifact_qc import ArtifactIntegrityQcService


PROMPTS = [
    "根据 products.csv 生成 sales.xlsx。禁止访问桌面。",
    "用法示例：python calc.py input.json output.json。实际读取 orders.json，输出 result.json。",
    "请读取 README.md，然后修改代码并运行三项测试。",
    "别执行 python.run，只解释怎么运行。",
    "你能读取目录吗？", "先不要改那个文件。", "请处理某个附件。",
    "报告各 20 项，至少 3000 字，正文至少 16pt。",
    "请交付 Word / PPT / PDF / Excel，放桌面，发给我。",
    "依次使用 file.read、file.write，再通过 qc.ppt.delivery_check。",
    "学习这份材料，只创建待确认学习卡。",
    "听音频再回答问题。", "请记住，我的名字是测试员。", "继续", "???",
    "Only explain. Do not call any tools.", "Please create, test and deliver report.docx.",
]


@pytest.mark.parametrize("prompt", PROMPTS)
def test_prose_creates_no_runtime_requirements_or_route(prompt):
    assert integrity.runtime_execution_floor(prompt) == integrity.ACT_UNKNOWN
    assert integrity.build_action_obligations(prompt) == []
    assert integrity.request_target_bindings(prompt) == []
    assert integrity.extract_forbidden_actions(prompt) == []
    assert kernel._runtime_detects_work_intent(prompt) is False
    assert kernel._simple_chain_parse_requirements(prompt) == []
    assert kernel._simple_chain_explicit_deliverable_paths(prompt) == []
    assert kernel._simple_chain_explicit_action_sequence(prompt) == []
    assert kernel._simple_chain_expected_suffixes(prompt) == set()
    contract = integrity.initialize_task_contract(prompt)
    assert contract['desired_facts'] == []
    assert contract['clarification_required'] is False
    assert detect_explicit_intent(prompt).triggered is False


@pytest.mark.parametrize("reply", ["已全部完成。", "还没有完成？", "我来帮你。", "x"])
def test_reply_wording_never_masks_failed_tool(reply):
    failure = observation('python.run', ok=False, contract={'ok': False, 'may_mutate': False})
    allowed, status, reasons = kernel._simple_chain_evidence_check('任意任务', [failure], [], final_reply=reply)
    assert not allowed and status == 'failed' and reasons


def test_successful_read_does_not_acquire_an_extra_mutation_or_stability_gate():
    prompt = '根据 products.csv 生成 sales.xlsx，输入文件不要改。'
    payload = observation('file.read', 'products.csv', result={'content': 'item,price\na,1'})
    contract = integrity.reconcile_task_contract(None, None, user_text=prompt, action='file.read')
    contract = integrity.update_task_contract_evidence(contract, payload)
    updated, allowed, status, reasons = kernel._simple_chain_life_completion_gate(
        prompt, [payload], [], task_contract=contract, final_reply='模型负责判断是否完成。', task_obligations=[])
    assert allowed and status == 'complete' and reasons == []
    assert updated['required_stability'] == 0


@pytest.mark.parametrize('text', ['', '好', '各 100 项'])
def test_docx_delivery_checks_structure_and_bytes_without_word_floor(text):
    fixture = docx_support.DocxQcTests('test_1000_real_words_pass_and_deleted_text_is_not_counted')
    fixture.setUp()
    try:
        accepted = fixture.prepare(docx_support.docx_bytes(text))
        qc = ArtifactIntegrityQcService(fixture.object_store, fixture.fact_ledger)
        result = qc.evaluate(accepted, run_sequence=1, checked_at_ms=30_000)
        assert result.passed
        assert result.registration.record.result.check_id == 'qc.artifact.delivery_integrity'
        replay = qc.evaluate(accepted, run_sequence=1, checked_at_ms=30_001)
        assert replay.registration.record == result.registration.record
    finally:
        fixture.tearDown()


def test_structured_memory_selection_has_no_keyword_or_expiry_guess():
    assert not detect_explicit_intent('请永远记住，今天叫我甲').triggered
    chosen = detect_explicit_intent('任意内容', explicit=True, expiry_kind='this_turn')
    assert chosen.triggered and chosen.expiry_kind == 'this_turn'
    assert chosen.reason_codes == ('explicit_memory_request',)
    with pytest.raises(ValueError):
        detect_explicit_intent('任意内容', explicit=True, expiry_kind='guessed')


def test_registered_factual_requirement_still_requires_real_execution():
    requirement = {'id':'explicit-run', 'kind':'execution', 'target_path':'calc.py',
                   'actionable':True, 'evidence_predicate':'command_execution', 'status':'pending'}
    assert integrity.execution_integrity_blockers('只说完成也没用', [], obligations=[requirement])
    failed = observation('python.run', 'calc.py', result={'execution':{'ok':False,'returncode':1}})
    assert not integrity.obligation_is_satisfied(requirement, [failed])
    success = observation('python.run', 'calc.py', result={'execution':{'ok':True,'returncode':0}})
    assert integrity.obligation_is_satisfied(requirement, [success])
