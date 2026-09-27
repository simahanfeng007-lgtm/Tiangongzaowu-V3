"""Retired legacy keyword scores never create completion or risk authority."""
from types import SimpleNamespace
import pytest
from omni_body_skill.tools import delivery_kernel
from v3.jinhua.houxuan_shengcheng import _candidate_type, _candidate_risk
from v3.jinhua.yanzheng_shenpi import JinhuaYanzhengShenpi
from v3.jinhua.shuxue_qiaojie import _fenxi_jieguo_wenben
from v3.shangxiawen_xujie import _history_assistant_content
from life_service.learning_executor import _needs_network, _screen


@pytest.mark.parametrize('text', ['已全部完成', 'not finished', '创新高效但失败error成功', '危险插件sudo流程tool tool_evolution'])
def test_legacy_descriptions_do_not_set_risk_score_or_completion(text):
    assert set(_fenxi_jieguo_wenben(text).values()) == {0}
    item = {'expression_target': 'context', 'risk_level': 'A2'}
    assert _candidate_type(item, text) == 'prompt_candidate'
    assert _candidate_risk('prompt_candidate', item, text) == 'A2'
    validator = object.__new__(JinhuaYanzhengShenpi)
    assert validator._effective_risk({'candidate_type': 'prompt_candidate', **item}, text) == 'A2'
    history = _history_assistant_content(text)
    assert history == '聊天回复: ' + text
    assert 'completion_evidence=missing' not in history


# This retained action also has a managed-document structural-check branch.
# Metadata-only business wrappers are removed; their public entry-point
# rejection is verified in test_atomic_capability_pruning.py.
SEMANTIC_QC = [delivery_kernel._qc_docx]


@pytest.mark.parametrize('action', SEMANTIC_QC, ids=[x.__name__ for x in SEMANTIC_QC])
def test_retired_quality_scores_are_unassessed_not_fabricated_pass(tmp_path, action):
    path = tmp_path/'content.md'
    text = '执行摘要 风险 授权 核心结论 待补充。TODO 不是未完成证据。'
    path.write_text(text)
    def resolve(target, *, must_exist=False):
        candidate = tmp_path/target
        if must_exist and not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate
    runtime = SimpleNamespace(_resolve=resolve, _rel=lambda p:p.name)
    value = action(runtime, path.name, {})
    assert value['success'] is True
    assert value['result']['assessment_mode'] == 'content_observation_only'
    assert value['result']['text_preview'] == text
    assert value['result']['content_quality'] == 'unassessed'
    assert 'score' not in value['result'] and 'acceptance' not in value['result']
    assert value['evidence']['bytes'] == len(text.encode())
    with pytest.raises(FileNotFoundError):
        action(runtime, 'missing.md', {})


def test_learning_research_uses_metadata_not_news_words_or_relevance_keywords():
    assert not _needs_network('今天最新API价格', {'content': 'official docs'})
    assert _needs_network('ordinary notes', {'requires_network': True})
    row = {'title': 'different language', 'url': 'https://example.test', 'content': 'actual source material'}
    accepted, rejected = _screen([row], '毫无词汇重叠的主题')
    assert accepted == [row] and rejected == []
