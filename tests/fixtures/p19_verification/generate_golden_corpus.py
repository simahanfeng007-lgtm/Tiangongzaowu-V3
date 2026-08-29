"""P19-R2 M0 语料生成器（确定性输出）。

用法：python tests/fixtures/p19_verification/generate_golden_corpus.py
输出：tests/fixtures/p19_verification/golden/p19_m0_corpus.v0.1.jsonl

规则（对照 docs/p19-r2/TRACE_SCHEMA_v0.1.json 与 FAILURE_TAXONOMY_v0.1.txt）：
- trace_id = p19tr_ + sha256(category + 序号)[:10]，确定性可复算；
- provenance.kind=real 必须给 evidence_refs，格式为
  "tests/<file>.py::test_name" 或 "tests/<file>.py::TestClass::test_name"，
  且选择器必须真实存在（由 test_p19_m0_baseline.py 校验）；
- happy path（期望 PASS）占比受控（M5 PR 门 ≤50%）；
- 全部条目当前 enforcement 上限 RECORD/ALERT（M0 阶段无 BLOCK）；
- 2026-08-29 审核修正：expected_status 是理想 verifier 的单谓词状态，
  与"是否允许交付"分离；min_data_rows 降级 synthetic（现实现用
  max_row 含表头且无专项测试）；幂等命中为 PASS；扫描 PDF 为
  INCONCLUSIVE；渠道歧义归 delivery 域。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA = "tiangong.p19.trace.v0.1"
HERE = Path(__file__).resolve().parent
OUT = HERE / "golden" / "p19_m0_corpus.v0.1.jsonl"

# (category, format, task_kind, risk, expected, enforcement, platform,
#  provenance_kind, evidence_refs, description, predicate_hint)
ROWS: list[tuple] = [
    # 01 false_obligation ------------------------------------------------
    ("false_obligation", "text", "file_write", "MEDIUM", "FAIL", "RECORD", "none", "real",
     ["tests/test_codex_write_evidence_recognition.py::test_quoted_filename_does_not_spawn_execution_obligation"],
     '文件名含"验证"字样只新增了不该有的 execution/test 义务；用户明确要求的 file creation effect obligation 必须保留', []),
    ("false_obligation", "text", "chat", "MEDIUM", "FAIL", "RECORD", "none", "real",
     ["tests/test_codex_write_evidence_recognition.py::test_frontend_contract_banner_does_not_spawn_obligations"],
     "前端【连续执行契约】横幅文本不得参与义务推导", []),
    ("false_obligation", "text", "file_write", "LOW", "FAIL", "RECORD", "none", "synthetic", [],
     "用户引用他人消息中提到的文件名（非本人要求）不得派生义务", []),
    ("false_obligation", "text", "file_write", "LOW", "FAIL", "RECORD", "none", "synthetic", [],
     "模型复述用户目标词不构成新义务来源", []),
    # 02 evidence_missing ------------------------------------------------
    ("evidence_missing", "effect", "file_write", "HIGH", "FAIL", "RECORD", "none", "real",
     ["tests/test_codex_write_evidence_recognition.py::test_plain_result_without_evidence_still_not_a_write"],
     "工具 success=true 但无 changed_files/pre-post/verified 证据，模型自报完成", []),
    ("evidence_missing", "effect", "file_write", "MEDIUM", "FAIL", "RECORD", "none", "synthetic", [],
     "声称已删除文件但无 deleted_files + post not exists 证据", []),
    ("evidence_missing", "effect", "code_change", "HIGH", "FAIL", "RECORD", "none", "synthetic", [],
     "shell 退出码 0 但无 broker delta/repository snapshot，不得推导文件变化", []),
    ("evidence_missing", "effect", "file_write", "LOW", "INCONCLUSIVE", "RECORD", "none", "synthetic", [],
     "普通只读查询成功，不产生写证据（合法，不应误判为缺失）", []),
    # 03 evidence_provenance_unknown --------------------------------------
    ("evidence_provenance_unknown", "effect", "file_write", "CRITICAL", "FAIL", "RECORD", "none", "synthetic", [],
     "普通模型 JSON 伪造 codex_evidence.actual.write_effect 外形", ["effect.required_change_observed"]),
    ("evidence_provenance_unknown", "effect", "file_write", "CRITICAL", "FAIL", "RECORD", "none", "synthetic", [],
     "write_evidence.v1 跨 request 重放（旧证据冒充本轮）", []),
    ("evidence_provenance_unknown", "effect", "file_write", "HIGH", "FAIL", "RECORD", "none", "synthetic", [],
     "write_evidence.v1 跨 generation 重放", []),
    ("evidence_provenance_unknown", "effect", "file_write", "HIGH", "FAIL", "RECORD", "none", "synthetic", [],
     "effect_id 替换攻击（证据绑定被调包）", []),
    # 04 effect_state_mismatch --------------------------------------------
    ("effect_state_mismatch", "effect", "file_write", "HIGH", "ERROR", "RECORD", "none", "real",
     ["tests/test_completion_gate_head_consistency.py::CompletionGateHeadConsistencyTests::test_fact_succeeded_but_head_ambiguous_is_not_success"],
     "effect head 与 FactLedger 投影不一致（fail-closed）", []),
    ("effect_state_mismatch", "effect", "file_write", "HIGH", "ERROR", "RECORD", "none", "synthetic", [],
     "effect 卡 CLAIMED/SIDE_EFFECT_STARTED 超 240s（watchdog AMBIGUOUS）", []),
    ("effect_state_mismatch", "effect", "file_write", "MEDIUM", "FAIL", "RECORD", "none", "synthetic", [],
     "覆盖写无 pre/post hash 且非可信 broker delta", ["effect.target_sha256_matches"]),
    ("effect_state_mismatch", "effect", "file_write", "MEDIUM", "PASS", "RECORD", "none", "synthetic", [],
     "幂等重写 verified_unchanged_files 合法命中——目标状态已由权威信息证明在位（合法完成证据）", ["effect.idempotent_target_verified"]),
    # 05 artifact_shell ----------------------------------------------------
    ("artifact_shell", "docx", "office_generation", "HIGH", "FAIL", "RECORD", "none", "real",
     ["tests/test_office_content_gate.py::OfficeContentGateTests::test_shell_files_are_rejected"],
     "DOCX 仅标题无正文（空壳）", ["docx.min_body_items"]),
    ("artifact_shell", "xlsx", "office_generation", "HIGH", "FAIL", "RECORD", "none", "real",
     ["tests/test_office_content_gate.py::OfficeContentGateTests::test_shell_files_are_rejected"],
     "XLSX 空表（无非空单元格）", ["artifact.nonempty"]),
    ("artifact_shell", "xlsx", "office_generation", "HIGH", "FAIL", "RECORD", "none", "real",
     ["tests/test_office_content_gate.py::OfficeContentGateTests::test_missing_columns_reported"],
     "XLSX 请求列缺失", ["xlsx.required_columns"]),
    ("artifact_shell", "xlsx", "office_generation", "HIGH", "FAIL", "RECORD", "none", "synthetic", [],
     "XLSX 数据行数不足——当前实现用 max_row（含表头）比较，且无专项测试；M2 实现 data_rows（表头后非空行）并补测试后升级 real", ["xlsx.min_data_rows"]),
    ("artifact_shell", "pptx", "office_generation", "MEDIUM", "FAIL", "RECORD", "none", "synthetic", [],
     "PPTX 全部空白页/仅母版无实际内容", ["pptx.min_nonempty_slides"]),
    ("artifact_shell", "pdf", "office_generation", "MEDIUM", "INCONCLUSIVE", "RECORD", "none", "synthetic", [],
     "image-only PDF 文本不可提取（谓词无法判定，不得伪装 PASS）", []),
    # 06 artifact_placeholder ----------------------------------------------
    ("artifact_placeholder", "xlsx", "office_generation", "MEDIUM", "FAIL", "ALERT", "none", "real",
     ["tests/test_office_content_gate.py::OfficeContentGateTests::test_placeholder_rows_are_rejected"],
     "当前启发式：数据行≥2 且全部相同即判疑似占位（不校验是否真为 TODO/示例 token）；存在合法重复误报风险，只允许 ALERT", []),
    ("artifact_placeholder", "docx", "office_generation", "MEDIUM", "FAIL", "ALERT", "none", "synthetic", [],
     "DOCX 正文含明确 placeholder token（待补充/在此填写）", []),
    ("artifact_placeholder", "xlsx", "office_generation", "MEDIUM", "PASS", "ALERT", "none", "synthetic", [],
     "合法重复数据行（真实业务允许重复，不得 BLOCK——I-23）", ["xlsx.min_distinct_rows"]),
    ("artifact_placeholder", "csv", "office_generation", "LOW", "FAIL", "ALERT", "none", "synthetic", [],
     "CSV 占位行与合法行混合", ["csv.required_columns"]),
    ("artifact_placeholder", "docx", "office_generation", "LOW", "FAIL", "ALERT", "none", "synthetic", [],
     "模板字段未填写的合同骨架（疑似而非确证）", []),
    # 07 semantic_requirement_uncompiled ------------------------------------
    ("semantic_requirement_uncompiled", "docx", "office_generation", "MEDIUM", "INCONCLUSIVE", "RECORD", "none", "synthetic", [],
     "用户要求'写得专业'——无确定性 predicate，标记 uncompiled", []),
    ("semantic_requirement_uncompiled", "text", "chat", "LOW", "INCONCLUSIVE", "RECORD", "none", "synthetic", [],
     "用户要求'内容正确'——事实性归语义层，首批不验证", []),
    ("semantic_requirement_uncompiled", "docx", "office_generation", "LOW", "INCONCLUSIVE", "RECORD", "none", "synthetic", [],
     "用户要求'有气势的演讲稿'——风格维度禁入首批", []),
    # 08 false_block ---------------------------------------------------------
    ("false_block", "docx", "office_generation", "HIGH", "PASS", "ALERT", "none", "synthetic", [],
     "合法一页短通知 DOCX（正文少于通用阈值但满足用户要求）", ["docx.min_visible_text_chars"]),
    ("false_block", "xlsx", "office_generation", "HIGH", "PASS", "ALERT", "none", "synthetic", [],
     "合法只有表头+1行数据的登记表（用户只要1行）", ["xlsx.min_data_rows"]),
    ("false_block", "xlsx", "office_generation", "MEDIUM", "PASS", "ALERT", "none", "synthetic", [],
     "用户要求 N 个相同示例——distinct 行数即等于 N（合法重复）", ["xlsx.min_distinct_rows"]),
    ("false_block", "pdf", "office_generation", "MEDIUM", "INCONCLUSIVE", "ALERT", "none", "synthetic", [],
     "扫描版合法 PDF：文本谓词无法判定为 INCONCLUSIVE（不伪装 PASS）；enforcement ALERT 不阻断交付", []),
    # 09 repair_loop_stalled --------------------------------------------------
    ("repair_loop_stalled", "xlsx", "office_generation", "MEDIUM", "FAIL", "RECORD", "none", "synthetic", [],
     "同一缺失列缺口修复两轮仍失败——应停止自动循环 honest incomplete", []),
    ("repair_loop_stalled", "docx", "office_generation", "LOW", "FAIL", "RECORD", "none", "synthetic", [],
     "失败签名变化（新缺口）——可修复但受 request 总上限3约束", []),
    ("repair_loop_stalled", "effect", "file_write", "HIGH", "FAIL", "RECORD", "none", "synthetic", [],
     "修复指令要求删除其他产物（越权动作必须拒绝）", []),
    # 10 ambiguous_side_effect ------------------------------------------------
    ("ambiguous_side_effect", "delivery", "delivery", "HIGH", "FAIL", "RECORD", "none", "real",
     ["tests/test_delivery_outbox.py::DeliveryOutboxTests::test_orphaned_crossed_boundary_never_resends_and_enters_reconciliation"],
     "渠道投递跨边界孤儿进入 reconciliation，不重发不盲重试", []),
    ("ambiguous_side_effect", "effect", "file_write", "CRITICAL", "ERROR", "RECORD", "none", "synthetic", [],
     "AMBIGUOUS 后禁止盲重试，必须 reconcile（I-18）", []),
    ("ambiguous_side_effect", "effect", "file_write", "HIGH", "ERROR", "RECORD", "none", "synthetic", [],
     "重启恢复把无终态 backend effect 置 FAILED_FINAL（effect 级收口）", []),
    # 11 completion_self_deadlock（回归保持类） -------------------------------
    ("completion_self_deadlock", "effect", "file_write", "HIGH", "PASS", "RECORD", "none", "real",
     ["tests/test_codex_write_evidence_recognition.py::test_verify_completion_ignores_own_inflight_envelope"],
     "本代外层 execution envelope 例外不阻断自身完成提案（PR#41 e173da4）", []),
    ("completion_self_deadlock", "effect", "file_write", "HIGH", "PASS", "RECORD", "none", "real",
     ["tests/test_codex_write_evidence_recognition.py::test_authoritative_write_evidence_counts_two_stability_signals"],
     "权威写入双稳定信号（observed+verified 同时贡献，PR#41 e173da4）", []),
    # 12 frontend_banner_contamination（回归保持类） ---------------------------
    ("frontend_banner_contamination", "text", "chat", "MEDIUM", "PASS", "RECORD", "none", "real",
     ["tests/test_codex_write_evidence_recognition.py::test_frontend_contract_banner_does_not_spawn_obligations"],
     "横幅剥离后正常完成不回归（PR#41 94816b7）", []),
    # 13 quoted_data_contamination（回归保持类） -------------------------------
    ("quoted_data_contamination", "text", "file_write", "MEDIUM", "PASS", "RECORD", "none", "real",
     ["tests/test_codex_write_evidence_recognition.py::test_quoted_filename_does_not_spawn_execution_obligation"],
     '引号剥离后：file creation effect 义务保留、"验证"字样的额外 execution/test 义务不产生（PR#41 fc834c2）', []),
    # 14 artifact_only_delivery（回归保持类） ----------------------------------
    ("artifact_only_delivery", "docx", "office_generation", "MEDIUM", "PASS", "RECORD", "none", "real",
     ["tests/test_execution_chain_efficiency_fixes.py::ArtifactOnlyDeliveryTests::test_bridge_accepts_artifact_only_success"],
     "桥接层接受 artifact-only 成功（PR#41 b6b032a 修B）", []),
    ("artifact_only_delivery", "xlsx", "office_generation", "MEDIUM", "PASS", "RECORD", "none", "real",
     ["tests/test_execution_chain_efficiency_fixes.py::ArtifactOnlyDeliveryTests::test_orchestration_synthesizes_reply_from_artifacts"],
     "空正文+有产物→机器合成交付文案，不整单失败", []),
    # 15 memory_cognition_task_closeout（回归保持类） ---------------------------
    ("memory_cognition_task_closeout", "repository", "cognition", "HIGH", "PASS", "RECORD", "none", "real",
     ["tests/test_life_cognition_execution_wiring.py::test_cognition_task_executes_and_writes_memory_relation"],
     "认知任务经执行回路完成并写回 proposed_relations（PR#41 5c4392f）", []),
    ("memory_cognition_task_closeout", "repository", "cognition", "MEDIUM", "PASS", "RECORD", "none", "real",
     ["tests/test_life_cognition_execution_wiring.py::test_learning_noop_records_reason"],
     "noop 带原因码 + 停机恢复叙事事件（PR#41 5c4392f）", []),
    ("memory_cognition_task_closeout", "repository", "cognition", "MEDIUM", "PASS", "RECORD", "none", "real",
     ["tests/test_memory_unification_landings.py::WorkFingerprintPipelineTests::test_projection_consumes_key_facts"],
     "工作指纹/心灵日记/镜像刷新记忆归一（PR#41 d2f2ccd）", []),
    # 补充：verifier 基础设施自身（ERROR 类） ---------------------------------
    ("evidence_missing", "docx", "office_generation", "MEDIUM", "ERROR", "RECORD", "none", "synthetic", [],
     "docx 解析库抛异常——必须 ERROR 不得静默当无缺口（P0-04）", []),
    ("artifact_shell", "xlsx", "office_generation", "MEDIUM", "ERROR", "RECORD", "windows_only", "synthetic", [],
     "Windows 文件句柄未释放导致读回失败（R-09 场景）", []),
    ("artifact_shell", "xlsx", "office_generation", "LOW", "ERROR", "RECORD", "none", "synthetic", [],
     "损坏 zip 容器（malformed office 文件）", []),
    ("artifact_shell", "xlsx", "office_generation", "LOW", "INCONCLUSIVE", "RECORD", "none", "synthetic", [],
     "unsupported format 无对应 inspector", []),
    ("evidence_provenance_unknown", "repository", "code_change", "HIGH", "FAIL", "RECORD", "none", "synthetic", [],
     "直接修改生成镜像（source-authority 必须 BLOCK 候选，先 RECORD）", ["repository.no_generated_mirror_direct_edit"]),
    ("evidence_missing", "repository", "code_change", "HIGH", "FAIL", "RECORD", "none", "synthetic", [],
     "测试命令未运行但模型说全绿", ["repository.tests_passed"]),
    ("evidence_provenance_unknown", "repository", "code_change", "HIGH", "FAIL", "RECORD", "none", "synthetic", [],
     "删除失败测试使套件变绿（test tampering）", ["repository.no_test_tampering"]),
    ("false_obligation", "repository", "code_change", "MEDIUM", "PASS", "RECORD", "none", "synthetic", [],
     "合法代码任务：required_paths_changed 命中+forbidden_paths 未动（happy）", ["repository.required_paths_changed"]),
    ("artifact_shell", "xlsx", "office_generation", "LOW", "PASS", "RECORD", "none", "synthetic", [],
     "表头+3行数据满足至少3行要求（happy；现行 max_row 实现对表头+2行会漏检，M2 修正）", ["xlsx.min_data_rows"]),
    ("artifact_shell", "docx", "office_generation", "LOW", "PASS", "RECORD", "none", "synthetic", [],
     "含 table cell 正文的合法 DOCX（计量须覆盖表格，P1-01）", ["docx.min_visible_text_chars"]),
]


def trace_id(category: str, seq: int) -> str:
    digest = hashlib.sha256(f"{category}:{seq}".encode("utf-8")).hexdigest()[:10]
    return f"p19tr_{digest}"


def build() -> list[dict]:
    counters: dict[str, int] = {}
    rows: list[dict] = []
    for (category, fmt, kind, risk, expected, enforcement, platform,
         prov_kind, refs, description, hints) in ROWS:
        counters[category] = counters.get(category, 0) + 1
        provenance: dict = {"kind": prov_kind}
        if prov_kind == "real":
            assert refs, f"real 条目必须给 evidence_refs: {category}"
            for ref in refs:
                assert "::" in ref, f"real evidence_ref 必须带测试选择器: {ref}"
            provenance["evidence_refs"] = refs
        elif refs:
            raise AssertionError("synthetic 条目不得带 evidence_refs")
        row = {
            "trace_id": trace_id(category, counters[category]),
            "schema_version": SCHEMA,
            "category": category,
            "format": fmt,
            "task_kind": kind,
            "risk": risk,
            "expected_status": expected,
            "enforcement": enforcement,
            "platform_sensitivity": platform,
            "provenance": provenance,
            "description": description,
        }
        if hints:
            row["predicate_hint"] = hints
        rows.append(row)
    return rows


def main() -> None:
    rows = build()
    seen: set[str] = set()
    for row in rows:
        if row["trace_id"] in seen:
            raise AssertionError(f"trace_id 冲突: {row['trace_id']}")
        seen.add(row["trace_id"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    happy = sum(1 for r in rows if r["expected_status"] == "PASS")
    real = sum(1 for r in rows if r["provenance"]["kind"] == "real")
    print(f"写入 {len(rows)} 条 → {OUT}")
    print(f"  类别数={len({r['category'] for r in rows})}/15")
    print(f"  real={real} synthetic={len(rows) - real}")
    print(f"  happy(PASS)={happy} 占比={happy / len(rows):.0%}")


if __name__ == "__main__":
    main()
