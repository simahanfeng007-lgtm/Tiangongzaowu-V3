"""真机复现回归：file.write 落盘成功但 codex_evidence 不被契约识别 → 完成门连环打回。

2026-08-29 真机捕获：omni_body file.write 工具返回
codex_evidence.actual.write_effect=true + attachments 真实路径 + checks 全绿，
但 normalize_tool_result 判 observed_write_effect=false（候选枚举不下钻
codex_evidence/actual），生命完成门按纪律拒绝对"无证据写入"盖章，
correction 三轮 stalled → honest incomplete。文件明明已经在磁盘上。
"""

from __future__ import annotations

from v3.tool_result_contract import normalize_tool_result

# 真机原始结果（截取自 req_158fd29d... 的 tool_1 raw_preview，结构保真；
# 截断尾部含 zhuangtai=wancheng 的成功信号，此处补回）
REAL_CODEX_WRITE_RESULT = {
    "zhuangtai": "wancheng",
    "codex_evidence": {
        "actual": {
            "action": "file.write",
            "attachments": [
                {
                    "kind": "document",
                    "path": "C:\\Users\\77571\\AppData\\Local\\TiangongV3-SourceWork\\workspace\\真机测试记录.txt",
                }
            ],
            "paths": [
                "真机测试记录.txt",
                "C:\\Users\\77571\\AppData\\Local\\TiangongV3-SourceWork\\workspace\\真机测试记录.txt",
            ],
            "text_stats": {"cjk_chars": 17, "total_chars": 32, "preview": "天工造物v3真机测试 2026-08-29：文件执行链验证成功。"},
            "write_effect": True,
        },
        "checks": {
            "ok": True,
            "path_matches_expected": True,
            "suffix_matches_expected": True,
        },
        "schema": "tiangong.v3.codex_evidence.v1",
    }
}


def test_codex_write_result_counts_as_observed_write() -> None:
    contract = normalize_tool_result("omni_body", REAL_CODEX_WRITE_RESULT)
    assert contract["ok"] is True
    assert contract["observed_write_effect"] is True, (
        "codex_evidence.actual.write_effect=true 必须被识别为已观测写入"
    )
    evidence = contract["write_evidence"]
    assert evidence is not None
    assert evidence["source"] == "codex_tool_evidence"
    assert any("真机测试记录.txt" in path for path in evidence["changed_files"])
    assert any("真机测试记录.txt" in path for path in contract["paths"])


def test_plain_result_without_evidence_still_not_a_write() -> None:
    contract = normalize_tool_result("omni_body", {"ok": True, "status": "wancheng", "neirong": "只是读了一下"})
    assert contract["observed_write_effect"] is False
    assert contract["write_evidence"] is None


def test_snapshot_evidence_path_still_works() -> None:
    """既有 pre/post 快照证据路径不被 codex 识别破坏（新文件：pre 不存在 + post 存在）。"""
    result = {
        "ok": True,
        "action": "file.write",
        "snapshots": [{"path": "C:\\w\\新文件.txt", "existed": False}],
        "evidence": {
            "path": "C:\\w\\新文件.txt",
            "exists": True,
            "is_file": True,
            "size_bytes": 32,
            "sha256": "a" * 64,
        },
    }
    contract = normalize_tool_result("omni_body", result)
    assert contract["write_evidence"] is not None
    assert any("新文件.txt" in path for path in contract["write_evidence"]["changed_files"])


def test_backupless_overwrite_stays_fail_closed() -> None:
    """覆盖写无备份可对账 → 不产出写入证据（fail-closed 语义保持不变）。"""
    result = {
        "ok": True,
        "snapshots": [{"path": "C:\\w\\旧文档.txt", "existed": True}],
        "evidence": {
            "path": "C:\\w\\旧文档.txt",
            "exists": True,
            "is_file": True,
            "size_bytes": 10,
            "sha256": "a" * 64,
        },
    }
    contract = normalize_tool_result("omni_body", result)
    assert contract["write_evidence"] is None


def test_quoted_filename_does_not_spawn_execution_obligation() -> None:
    """第二个真机根因：引号内文件名（"验证通过.txt"/"真机测试记录.txt"）
    含 验证/测试 字样，被义务动词匹配器当成请求动词，凭空派生 execution
    义务——写文件成功仍被要求"测试证据"。剥离引号片段后只应剩 effect。"""
    from v3.execution_integrity import build_action_obligations, obligation_is_satisfied

    for message in (
        '帮我创建一个txt文件，文件名叫"真机测试记录.txt"，内容写："测试内容"。',
        '再试一次：帮我创建一个txt文件，文件名叫"验证通过.txt"，内容写："验证内容"。',
    ):
        obligations = build_action_obligations(message)
        kinds = [ob["kind"] for ob in obligations]
        assert "execution" not in kinds, f"引号内文件名不应派生 execution 义务: {kinds}"
        assert "effect" in kinds

    # 真正的执行/观察请求语义保留
    assert "execution" in [ob["kind"] for ob in build_action_obligations("帮我把项目跑一遍测试")]
    assert "observation" in [ob["kind"] for ob in build_action_obligations("读取一下工作区的README.md")]

    # 闭环：effect 义务被 codex 修复后的写入证据满足
    contract = normalize_tool_result("omni_body", REAL_CODEX_WRITE_RESULT)
    quality_payload = {
        "ok": contract["ok"],
        "tool_action": "file.write",
        "tool_result_contract": contract,
    }
    obligations = build_action_obligations(
        '帮我创建一个txt文件，文件名叫"验证通过.txt"，内容写："x"。'
    )
    assert all(obligation_is_satisfied(ob, [quality_payload]) for ob in obligations), (
        "codex写入证据 + effect义务 必须闭环满足（真机旗舰bug的完整修复）"
    )


def test_authoritative_write_evidence_counts_two_stability_signals() -> None:
    """第三层修复：权威写入证据（含内嵌独立校验）产生双稳定信号。
    L2 任务此前要求两个成功工具轮（写+读回），简单文件创建被 1/2 卡死；
    写入被观测 + 写入被核验 = 两个独立事实信号，单轮即达稳定性。"""
    from v3.execution_integrity import (
        decide_task_contract_completion,
        initialize_task_contract,
        update_task_contract_evidence,
    )

    contract = initialize_task_contract('创建文件"完成.txt"，内容："x"。')
    contract["effective_level"] = "L2"
    c = normalize_tool_result("omni_body", REAL_CODEX_WRITE_RESULT)
    payload = {
        "ok": c["ok"],
        "tool_action": "file.write",
        "tool_args": {"action": "file.write", "target": "真机测试记录.txt"},
        "tool_result_contract": c,
    }
    updated = update_task_contract_evidence(contract, payload, round_number=1)
    assert updated["stability_signals"] == ["evidence_round:1", "write_verified:1"]
    _final, allowed, status, reasons = decide_task_contract_completion(
        updated, evidence_reasons=[], evidence_status="complete",
        final_reply="已完成", has_real_observation=True,
    )
    assert allowed is True and status == "complete", reasons

    # 无证据写入仍只有单信号（纪律不放松）
    plain = initialize_task_contract('创建文件"无证据.txt"。')
    plain["effective_level"] = "L2"
    plain_payload = {"ok": True, "tool_action": "file.write", "tool_args": {"target": "无证据.txt"}}
    plain_updated = update_task_contract_evidence(plain, plain_payload, round_number=1)
    assert plain_updated["stability_signals"] == ["evidence_round:1"]


def test_verify_completion_ignores_own_inflight_envelope() -> None:
    """effect_ledger 死锁修复（源码契约）：完成提案不得把本代外层 execution
    effect 的 in-flight 状态算作未决债务——提案发生在后端执行中途，外层
    effect 必然 SIDE_EFFECT_STARTED，否则结构性不可完成。"""
    from pathlib import Path as _Path

    text = (_Path(__file__).resolve().parents[1] / "src" / "total_gateway" / "regenerative_provider.py").read_text(encoding="utf-8")
    assert "_is_own_inflight_envelope" in text
    assert "record.claim.generation == identity.generation" in text


if __name__ == "__main__":
    test_codex_write_result_counts_as_observed_write()
    test_plain_result_without_evidence_still_not_a_write()
    test_snapshot_evidence_path_still_works()
    test_backupless_overwrite_stays_fail_closed()
    test_quoted_filename_does_not_spawn_execution_obligation()
    print("all passed")
