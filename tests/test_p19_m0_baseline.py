"""P19-R2 M0 交付物锁定测试。

锁定对象（docs/p19-r2/ + tests/fixtures/p19_verification/）：
1. 四份基线文档存在且声明了正确基线；
2. TRACE_SCHEMA_v0.1.json 可解析；
3. M0 语料逐条合规（枚举/必填/trace_id 确定性）；
4. real 条目的 evidence_refs 必须指向仓库内真实文件；
5. happy path 占比 ≤ 50%（禁止语料被 happy 淹没）。

本测试不触碰任何生产代码——M0 是只读审计分支。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs" / "p19-r2"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "p19_verification"
CORPUS = FIXTURES / "golden" / "p19_m0_corpus.v0.1.jsonl"
BASELINE = "e29bb9bafc213bd8b473ba7a9da014962a17e6d1"

CATEGORIES = {
    "false_obligation", "evidence_missing", "evidence_provenance_unknown",
    "effect_state_mismatch", "artifact_shell", "artifact_placeholder",
    "semantic_requirement_uncompiled", "false_block", "repair_loop_stalled",
    "ambiguous_side_effect", "completion_self_deadlock",
    "frontend_banner_contamination", "quoted_data_contamination",
    "artifact_only_delivery", "memory_cognition_task_closeout",
}
FORMATS = {"docx", "xlsx", "pptx", "pdf", "csv", "txt", "md", "effect",
           "repository", "delivery", "text", "mixed"}
TASK_KINDS = {"office_generation", "file_write", "code_change", "chat",
              "autonomous", "cognition", "delivery"}
RISKS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
STATUSES = {"PASS", "FAIL", "INCONCLUSIVE", "ERROR"}
ENFORCEMENTS = {"RECORD", "ALERT", "BLOCK"}


def _load_corpus() -> list[dict]:
    lines = CORPUS.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines if line.strip()]
    assert rows, "语料为空"
    return rows


def test_baseline_documents_exist_and_declare_baseline() -> None:
    for name in (
        "BASELINE_AUDIT.txt",
        "AUTHORITY_MAP.txt",
        "COMPLETION_CALL_GRAPH.txt",
        "FAILURE_TAXONOMY_v0.1.txt",
        "TRACE_SCHEMA_v0.1.json",
    ):
        path = DOCS / name
        assert path.is_file(), f"缺少 M0 文档: {name}"
        assert path.stat().st_size > 0
    audit = (DOCS / "BASELINE_AUDIT.txt").read_text(encoding="utf-8")
    assert BASELINE in audit


def test_trace_schema_parses() -> None:
    schema = json.loads((DOCS / "TRACE_SCHEMA_v0.1.json").read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == "tiangong.p19.trace.v0.1"
    assert set(schema["properties"]["category"]["enum"]) == CATEGORIES


def test_corpus_rows_conform() -> None:
    seen: set[str] = set()
    for row in _load_corpus():
        assert row["schema_version"] == "tiangong.p19.trace.v0.1"
        assert row["category"] in CATEGORIES
        assert row["format"] in FORMATS
        assert row["task_kind"] in TASK_KINDS
        assert row["risk"] in RISKS
        assert row["expected_status"] in STATUSES
        assert row["enforcement"] in ENFORCEMENTS
        assert row["platform_sensitivity"] in {"none", "windows_only", "linux_only"}
        assert row["trace_id"] not in seen
        seen.add(row["trace_id"])
        assert len(row["description"]) <= 400


def test_corpus_covers_all_categories_and_size() -> None:
    rows = _load_corpus()
    assert {r["category"] for r in rows} == CATEGORIES
    assert 50 <= len(rows) <= 100, "M0 起步语料应在 50-100 条"


def test_corpus_trace_ids_are_deterministic() -> None:
    counters: dict[str, int] = {}
    for row in _load_corpus():
        category = row["category"]
        counters[category] = counters.get(category, 0) + 1
        digest = hashlib.sha256(
            f"{category}:{counters[category]}".encode("utf-8")
        ).hexdigest()[:10]
        assert row["trace_id"] == f"p19tr_{digest}"


def test_real_rows_have_existing_evidence_refs() -> None:
    """real 条目的 evidence_refs 必须是「文件::[Class::]test」选择器且真实存在。

    2026-08-29 审核收紧：只验证文件存在曾让「min_data_rows 表头剔除」
    这种无专项测试的案例被误标 real。现在要求选择器末段测试名在
    文件中有 def 定义、中段类名有 class 定义。
    """
    for row in _load_corpus():
        provenance = row["provenance"]
        if provenance["kind"] == "real":
            refs = provenance.get("evidence_refs")
            assert refs, f"real 条目缺 evidence_refs: {row['trace_id']}"
            for ref in refs:
                assert "::" in ref, f"evidence_ref 必须带选择器: {ref}"
                path_text, *selectors = ref.split("::")
                path = REPO_ROOT / path_text
                assert path.is_file(), f"evidence_ref 文件不存在: {ref}"
                source = path.read_text(encoding="utf-8")
                for index, segment in enumerate(selectors):
                    is_last = index == len(selectors) - 1
                    if is_last:
                        assert f"def {segment}(" in source, (
                            f"测试函数不存在: {segment} ({ref})"
                        )
                    else:
                        assert (f"class {segment}(" in source
                                or f"class {segment}:" in source), (
                            f"测试类不存在: {segment} ({ref})"
                        )
        else:
            assert "evidence_refs" not in provenance


def test_happy_path_share_bounded() -> None:
    rows = _load_corpus()
    happy = sum(1 for r in rows if r["expected_status"] == "PASS")
    assert happy / len(rows) <= 0.5


def test_m0_records_no_enforcement_above_alert() -> None:
    """M0 阶段没有 BLOCK 权限：语料执法档不得高于 ALERT。"""
    for row in _load_corpus():
        assert row["enforcement"] in {"RECORD", "ALERT"}


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
