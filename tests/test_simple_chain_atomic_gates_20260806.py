# -*- coding: utf-8 -*-
"""2026-08-06 原子修复回归：B1/B2/B4 完成门不变量。

B1/B3：请求了可交付产物但没有成功写动作/附件 → 硬 gap（含中文《》产物名解析）。
B2：交付物齐备后不得空转——提前收尾的前置条件（final gate 已 complete）。
B4：写工具带 readback/哈希事实但契约缺 write_effect 时，用磁盘回读证据兜底。
B7 边界：说明性语境（说明 file.read）与“参考 README.md”不得被当成交付要求。
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _write_contract_payload(target: str) -> dict:
    return {
        "ok": True,
        "tool_action": "file.write",
        "tool_args": {
            "action": "file.write",
            "target": target,
            "args": {"content": "Blender 不可用：PATH 未找到。"},
        },
        "tool_result_contract": {
            "ok": True,
            "paths": [target],
            "observed_write_effect": True,
            "write_evidence": {
                "authoritative": True,
                "source": "tool_post_readback",
                "changed_files": [target],
                "post": [{"path": target, "exists": True, "is_file": True}],
            },
        },
    }


def test_b1_chinese_bracketed_deliverable_is_hard_gap() -> None:
    from v3.zongdiaodu import (
        _simple_chain_explicit_deliverable_paths,
        _simple_chain_final_hard_gate,
        _simple_chain_no_deliverable_gap,
    )

    message = "生成《设计桥可用性.md》到工作区，环境不可用就说明原因。"
    assert _simple_chain_explicit_deliverable_paths(message) == ["设计桥可用性.md"]
    gap = _simple_chain_no_deliverable_gap(message, [], [])
    assert gap and "设计桥可用性.md" in gap[0]
    allowed, status, reasons = _simple_chain_final_hard_gate(
        message,
        [],
        [],
        final_reply="环境不可用：Blender 未安装。",
    )
    assert allowed is False
    assert status == "incomplete"
    assert reasons


def test_b1_gap_clears_after_successful_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from v3.zongdiaodu import (
        _simple_chain_final_hard_gate,
        _simple_chain_no_deliverable_gap,
    )

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    target = tmp_path / "设计桥可用性.md"
    target.write_text("Blender 不可用：PATH 未找到。", encoding="utf-8")
    history = [_write_contract_payload(str(target))]
    message = "生成《设计桥可用性.md》到工作区，环境不可用就说明原因。"
    assert _simple_chain_no_deliverable_gap(message, history, []) == []
    allowed, status, reasons = _simple_chain_final_hard_gate(
        message,
        history,
        [],
        final_reply="已完成，结论是 Blender 不可用。",
    )
    assert allowed is True
    assert status == "complete"
    assert not reasons


def test_b1_reference_input_is_not_deliverable() -> None:
    from v3.zongdiaodu import _simple_chain_no_deliverable_gap

    assert _simple_chain_no_deliverable_gap("参见 README.md 和 docs/guide.md，然后总结", [], []) == []
    assert _simple_chain_no_deliverable_gap("参考 README.md 总结一下", [], []) == []


def test_b7_explain_context_is_not_deliverable() -> None:
    from v3.zongdiaodu import _simple_chain_no_deliverable_gap

    assert _simple_chain_no_deliverable_gap("说明 file.read 的参数并给出示例", [], []) == []


def test_b1_delivery_intent_and_format_request_are_hard_gap() -> None:
    from v3.zongdiaodu import _simple_chain_no_deliverable_gap

    assert _simple_chain_no_deliverable_gap("把 output/e2e 打包成 zip 发给我", [], []) != []
    assert _simple_chain_no_deliverable_gap("生成一份《数字化转型商业提案》Word 文档（.docx）", [], []) != []


def test_b4_write_readback_evidence_fallback() -> None:
    from v3.tool_result_contract import normalize_tool_result

    contract = normalize_tool_result(
        "omni_body",
        {
            "action": "file.write",
            "ok": True,
            "path": r"C:\ws\设计桥可用性.md",
            "readback": {
                "ok": True,
                "path": r"C:\ws\设计桥可用性.md",
                "sha256": "abc123",
                "size_bytes": 10,
            },
        },
    )
    evidence = contract["write_evidence"]
    assert contract["observed_write_effect"] is True
    assert evidence is not None
    assert evidence["source"] == "tool_post_readback"
    assert r"C:\ws\设计桥可用性.md" in evidence["changed_files"]
    assert any(item["path"] == r"C:\ws\设计桥可用性.md" for item in contract["generated_attachments"])


def test_b4_read_and_execution_never_self_certify() -> None:
    from v3.tool_result_contract import normalize_tool_result

    for result in (
        {
            "action": "file.read",
            "ok": True,
            "path": r"C:\ws\a.md",
            "readback": {"ok": True},
            "evidence": {"exists": True, "sha256": "x"},
        },
        {
            "action": "shell.run",
            "ok": True,
            "returncode": 0,
            "readback": {"ok": True},
        },
        {
            "action": "file.write",
            "ok": True,
            "path": r"C:\ws\a.md",
            "readback": {"ok": False},
        },
        {
            "action": "file.write",
            "ok": True,
            "path": r"C:\ws\a.md",
        },
    ):
        contract = normalize_tool_result("omni_body", result)
        assert contract["observed_write_effect"] is False
        assert contract["write_evidence"] is None


def test_b2_strip_tool_markup_and_gate_complete_prerequisite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from v3.zongdiaodu import (
        _simple_chain_final_hard_gate,
        _simple_chain_strip_tool_markup,
    )

    assert _simple_chain_strip_tool_markup("好的，先写入。<invoke>...</invoke>") == "好的，先写入。"
    assert _simple_chain_strip_tool_markup("<tool_call>{\"name\":\"x\"}</tool_call>") == ""

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    target = tmp_path / "设计桥可用性.md"
    target.write_text("Blender 不可用：PATH 未找到。", encoding="utf-8")
    history = [_write_contract_payload(str(target))]
    # 交付物已齐备且无 gap：这是 B2 提前收尾（不再执行下一个工具）的触发条件。
    allowed, status, reasons = _simple_chain_final_hard_gate(
        "生成《设计桥可用性.md》到工作区，环境不可用就说明原因。",
        history,
        [],
        final_reply="已写入，结论是 Blender 不可用。",
    )
    assert allowed is True
    assert status == "complete"
    assert not reasons


def test_b6_continue_decision_hints_content_shortage_is_fixable() -> None:
    from v3.zongdiaodu import _simple_chain_continue_decision_payload

    payload = _simple_chain_continue_decision_payload(
        "req_x",
        ["written content cjk_chars=983 < required 2500"],
        {},
    )
    assert "REQUIRED deliverable threshold" in payload["instruction"]
    assert "file.append" in payload["instruction"]
    payload_plain = _simple_chain_continue_decision_payload("req_y", ["some other gap"], {})
    assert "REQUIRED deliverable threshold" not in payload_plain["instruction"]


def test_b1_delivery_guard_payload_demands_write_tool() -> None:
    from v3.zongdiaodu import _simple_chain_delivery_guard_payload

    payload = _simple_chain_delivery_guard_payload(
        "req_z",
        "检查 Blender 是否可用并输出《设计桥可用性.md》",
        ["no successful write action or generated attachment for requested deliverable:设计桥可用性.md"],
        "shell.run",
        4,
        {},
    )
    assert payload["expected_deliverables"] == ["设计桥可用性.md"]
    assert "file.write" in payload["instruction"]
    assert "Stop read-only probing now" in payload["instruction"]


def test_b4_disk_existence_fallback_for_write_without_contract_flag(tmp_path: Path) -> None:
    from v3.zongdiaodu import (
        _simple_chain_mutation_payload_satisfies_request,
        _tool_write_verified,
    )

    target = tmp_path / "设计桥可用性.md"
    target.write_text("Blender 不可用。", encoding="utf-8")
    payload = {
        "ok": True,
        "tool_action": "file.write",
        "tool_args": {"action": "file.write", "target": str(target), "args": {"content": "Blender 不可用。"}},
        "tool_result_contract": {
            "ok": True,
            "paths": [str(target)],
            "observed_write_effect": False,
            "write_effect": False,
            "write_evidence": None,
        },
    }
    ok, issues = _simple_chain_mutation_payload_satisfies_request(
        f"生成《设计桥可用性.md》到工作区",
        payload,
    )
    assert ok is True, issues
    assert _tool_write_verified("omni_body", {
        "action": "file.write",
        "ok": True,
        "path": str(target),
        "readback": {"ok": True, "path": str(target), "size_bytes": target.stat().st_size},
    }) is True


def test_b4_omni_body_tuple_result_is_unwrapped() -> None:
    from v3.tool_result_contract import normalize_tool_result

    tuple_result = [
        "omni_body",
        {"action": "file.list", "target": "output", "args": {}},
        {
            "schema": "tiangong.v3.omni_body.v1",
            "ok": True,
            "zhuangtai": "wancheng",
            "action": "file.list",
            "result": {
                "action": "file.list",
                "count": 1,
                "entries": [{"name": "e2e", "path": r"C:\ws\output\e2e", "type": "dir"}],
                "success": True,
            },
        },
    ]
    contract = normalize_tool_result("omni_body", tuple_result)
    assert contract["ok"] is True
    assert contract["status"] == "wancheng"


def test_b4_write_evidence_post_counts_as_verification() -> None:
    from v3.zongdiaodu import (
        _simple_chain_has_post_mutation_verification,
        _simple_chain_requires_command_verification,
    )

    payload = {
        "ok": True,
        "tool_action": "docx.create",
        "tool_args": {"action": "docx.create", "target": r"C:\ws\办公桥测试.docx", "args": {}},
        "tool_result_contract": {
            "ok": True,
            "paths": [r"C:\ws\办公桥测试.docx"],
            "observed_write_effect": True,
            "write_evidence": {
                "authoritative": True,
                "source": "tool_post_readback",
                "changed_files": [r"C:\ws\办公桥测试.docx"],
                "post": [
                    {
                        "path": r"C:\ws\办公桥测试.docx",
                        "exists": True,
                        "is_file": True,
                        "size_bytes": 1234,
                        "sha256": "abc",
                    }
                ],
            },
        },
    }
    assert _simple_chain_has_post_mutation_verification([payload]) is True
    assert _simple_chain_has_post_mutation_verification(
        [payload], "用办公原生桥生成一个《办公桥测试.docx》测试文件"
    ) is True
    no_post = {
        **payload,
        "tool_result_contract": {
            "ok": True,
            "paths": [r"C:\ws\办公桥测试.docx"],
            "observed_write_effect": True,
            "write_evidence": {
                "authoritative": True,
                "changed_files": [],
                "post": [],
            },
        },
    }
    assert _simple_chain_has_post_mutation_verification([no_post]) is False
    # 明确要求运行测试时，写回读不能冒充验证命令。
    assert _simple_chain_requires_command_verification("确保全部测试通过") is True
    assert _simple_chain_requires_command_verification("运行 python -m pytest tests -q") is True
    assert _simple_chain_has_post_mutation_verification(
        [payload],
        "创建项目并运行 python -m pytest tests -q，确保全部测试通过",
    ) is False
    # 平台兜底写入不计入变更顺序：其前的真实验证仍然有效。
    pytest_payload = {
        "ok": True,
        "tool_action": "shell.run",
        "tool_args": {"action": "shell.run", "target": "", "args": {"command": "python -m pytest tests -q"}},
        "tool_result_contract": {"ok": True, "paths": [], "observed_write_effect": False, "write_effect": False},
    }
    fallback_payload = {
        "ok": True,
        "tool_action": "file.write",
        "tool_args": {"action": "file.write", "target": "测试报告.md", "args": {"content": ""}},
        "tool_result_contract": {
            "ok": True,
            "paths": ["测试报告.md"],
            "observed_write_effect": True,
            "write_evidence": {"authoritative": True, "source": "platform_fallback", "changed_files": ["测试报告.md"]},
        },
        "summary": "platform fallback write",
    }
    assert _simple_chain_has_post_mutation_verification(
        [payload, pytest_payload, fallback_payload],
        "创建项目并运行 python -m pytest tests -q，确保全部测试通过",
    ) is True


def test_b6_novel_honors_explicit_user_word_count() -> None:
    from v3.zongdiaodu import _novel_chapter_min_chars

    assert _novel_chapter_min_chars(
        "写一篇科幻小说第一章（≥1000 字）《回声年》，保存到工作区",
        "file.write",
        {"target": "回声年 第一章.md", "args": {}},
    ) == 1000
    assert _novel_chapter_min_chars(
        "写一篇科幻小说第一章《回声年》，保存到工作区",
        "file.write",
        {"target": "回声年 第一章.md", "args": {}},
    ) == 2500


def test_b6_hard_continue_payload_demands_write() -> None:
    from v3.zongdiaodu import _simple_chain_hard_continue_payload

    payload = _simple_chain_hard_continue_payload(
        "req_h",
        ["written content cjk_chars=1274 < required 2500"],
        {},
    )
    assert "not optional" in payload["instruction"]
    assert "file.append" in payload["instruction"]


def test_b1_monitor_yields_to_delivery_guard_budget(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from v3.zongdiaodu import _simple_chain_monitor_yields_to_guard

    msg = "用工具箱汇总工作区文件并生成《文件清单.md》"
    assert _simple_chain_monitor_yields_to_guard(msg, [], [], 0) is True
    assert _simple_chain_monitor_yields_to_guard(msg, [], [], 9) is False
    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "README.md").write_text("# readme", encoding="utf-8")
    assert _simple_chain_monitor_yields_to_guard("参考 README.md 总结一下", [], [], 0) is False
    # 交付物仍缺失时（即使已有部分写入）监视器继续让路，交给 guard/兜底收口。
    assert _simple_chain_monitor_yields_to_guard(msg, [], [], 0) is True


def test_b1_platform_fallback_writes_deliverable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from v3.zongdiaodu import (
        _simple_chain_fallback_write_deliverable,
        _simple_chain_final_hard_gate,
    )

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    items = _simple_chain_fallback_write_deliverable(
        "用浏览器自动化打开 example.com 并截图保存，输出《浏览器测试.md》",
        [{"ok": True, "tool_action": "shell.run", "summary": "probe"}],
        ["no successful write action or generated attachment for requested deliverable:浏览器测试.md"],
        "req_fb",
    )
    assert items and (tmp_path / "浏览器测试.md").is_file()
    assert (tmp_path / "浏览器测试.md").read_text(encoding="utf-8").startswith("# 浏览器测试.md")

    model_write = {
        "ok": True,
        "tool_action": "file.write",
        "tool_args": {"action": "file.write", "target": str(tmp_path / "browser_snapshot.txt"), "args": {"content": "snapshot"}},
        "tool_result_contract": {
            "ok": True,
            "paths": [str(tmp_path / "browser_snapshot.txt")],
            "observed_write_effect": True,
            "write_evidence": {
                "authoritative": True,
                "source": "tool_post_readback",
                "changed_files": [str(tmp_path / "browser_snapshot.txt")],
                "post": [{"path": str(tmp_path / "browser_snapshot.txt"), "exists": True, "is_file": True, "size_bytes": 8, "sha256": "abc"}],
            },
        },
    }
    fallback_write = {
        "ok": True,
        "tool_action": "file.write",
        "tool_args": {"action": "file.write", "target": str(tmp_path / "浏览器测试.md"), "args": {"content": ""}},
        "tool_result_contract": {
            "ok": True,
            "paths": [str(tmp_path / "浏览器测试.md")],
            "observed_write_effect": True,
            "write_evidence": {"authoritative": True, "source": "platform_fallback", "changed_files": [str(tmp_path / "浏览器测试.md")]},
        },
        "summary": "platform fallback write",
    }
    allowed, status, reasons = _simple_chain_final_hard_gate(
        "用浏览器自动化打开 example.com 并截图保存，输出《浏览器测试.md》",
        [model_write, fallback_write],
        items,
        final_reply="已生成报告。",
    )
    assert allowed is True, reasons
    assert status == "complete"


def test_multi_deliverable_project_does_not_flag_intermediate_writes() -> None:
    from v3.zongdiaodu import (
        _simple_chain_preflight_issues,
        _simple_chain_strict_single_deliverable,
    )

    project_prompt = (
        "创建完整 Python CLI 项目 markdown-wiki 到工作区 markdown-wiki/ 目录："
        "1) pyproject.toml；2) src/mdwiki/__init__.py、cli.py（init/build/serve/watch）、"
        "parser.py（把 Markdown 转 HTML，支持标题/列表/链接/代码块/表格）、server.py；"
        "3) tests/test_parser.py 与 tests/test_cli.py；4) README.md；5) examples/ 下 3 个示例 .md 页面。"
        "运行 python -m pytest tests -q 确保通过，并把测试输出写入《测试报告.md》。"
    )
    single_prompt = "用计算机操作技能读取当前工作区文件数并报告，输出《工作区统计.md》"
    assert _simple_chain_strict_single_deliverable(project_prompt) is False
    assert _simple_chain_strict_single_deliverable(single_prompt) is True
    assert _simple_chain_strict_single_deliverable(
        "创建 Python 库项目 textutils：pyproject.toml、src/textutils/*.py、tests/*.py、README.md，输出《测试报告.md》"
    ) is False
    issues = _simple_chain_preflight_issues(
        project_prompt,
        "file.write",
        {"action": "file.write", "target": "markdown-wiki/pyproject.toml", "args": {"content": "x"}},
    )
    assert not any("target mismatch" in issue or "suffix mismatch" in issue for issue in issues)
    single_issues = _simple_chain_preflight_issues(
        single_prompt,
        "file.write",
        {"action": "file.write", "target": "elsewhere.txt", "args": {"content": "x"}},
    )
    assert any("target mismatch" in issue for issue in single_issues)


def test_multi_file_project_allows_empty_scaffold_files() -> None:
    from v3.zongdiaodu import _simple_chain_allows_empty_scaffold

    project_prompt = (
        "创建 Python 项目：pyproject.toml、src/pkg/__init__.py、tests/test_x.py，"
        "并输出《测试报告.md》。"
    )
    single_prompt = "输出《工作区统计.md》"
    assert _simple_chain_allows_empty_scaffold(
        project_prompt, {"target": "markdown-wiki/tests/__init__.py", "args": {}}
    ) is True
    assert _simple_chain_allows_empty_scaffold(
        single_prompt, {"target": "工作区统计.md", "args": {}}
    ) is False
    assert _simple_chain_allows_empty_scaffold(
        single_prompt, {"target": "pkg/__init__.py", "args": {}}
    ) is True


def test_repeat_escalation_fallback_writes_deliverable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from v3.zongdiaodu import _simple_chain_try_fallback_delivery

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    allowed, items = _simple_chain_try_fallback_delivery(
        xiaoxi="完成《智能体工具链分析》项目到工作区 agent-tools/ 目录，输出《verification.md》，并运行验证命令确认结果",
        quality_history=[],
        generated_attachments=[],
        gap_reasons=["explicitly named deliverables are missing: verification.md"],
        request_id="req_repeat",
        required_read_paths=None,
        final_reply="",
    )
    assert items and (tmp_path / "verification.md").is_file()
    assert allowed is False  # 无真实验证时兜底不能伪造通过


def test_platform_run_verification_executes_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from v3.zongdiaodu import (
        _simple_chain_platform_run_verification,
        _simple_chain_requires_command_verification,
    )

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    script = tmp_path / "mdsummary.py"
    script.write_text(
        "import sys\n"
        "text = open(sys.argv[1], encoding='utf-8').read()\n"
        "print('TITLE:', [line for line in text.splitlines() if line.startswith('# ')][0])\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# 项目简介\n正文\n", encoding="utf-8")
    history: list[dict] = []
    attachments: list[dict] = []
    prompt = "请运行 python mdsummary.py README.md，把真实输出写入 summary.md"
    assert _simple_chain_requires_command_verification(prompt) is True
    ok = _simple_chain_platform_run_verification(
        user_message=prompt,
        quality_history=history,
        generated_attachments=attachments,
        request_id="req_v",
    )
    assert ok is True
    assert any(p.get("tool_action") == "shell.run" for p in history)
    assert (tmp_path / "summary.md").read_text(encoding="utf-8").startswith("TITLE:")
    assert attachments


def test_content_prose_tokens_are_not_requested_paths() -> None:
    """file.write 的正文提到 mdsummary.py/README.md 不得被当成要覆盖的路径。"""
    from v3.zongdiaodu import _simple_chain_requested_paths

    args = {
        "action": "file.write",
        "target": "md-tools/summary.md",
        "args": {
            "content": "运行 python mdsummary.py README.md 后把真实输出写入 summary.md。",
        },
    }
    requested = _simple_chain_requested_paths(args)
    assert requested == ["md-tools/summary.md"]
    assert not any(
        str(p).lower().endswith(("mdsummary.py", "readme.md"))
        for p in requested
    )


def test_python_run_code_tokens_still_extracted() -> None:
    """python.run 的 code 是真实命令文本，路径 token 仍必须被提取。"""
    from v3.zongdiaodu import _simple_chain_requested_paths

    args = {
        "action": "python.run",
        "target": "",
        "args": {
            "code": (
                "import subprocess, sys\n"
                "subprocess.run([sys.executable, 'mdsummary.py', 'README.md'])\n"
            ),
        },
    }
    requested = [str(p).lower() for p in _simple_chain_requested_paths(args)]
    assert any(p.endswith("mdsummary.py") for p in requested)
    assert any(p.endswith("readme.md") for p in requested)


def test_directory_paths_are_not_protected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """目录是容器不是产物：保护目录会误伤后续写入该目录的新文件。"""
    from v3.zongdiaodu import (
        _simple_chain_protect_paths,
        _simple_chain_protected_key,
    )

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "md-tools").mkdir()
    protected: set[str] = set()
    payload = {
        "ok": True,
        "tool_action": "file.list",
        "tool_args": {"action": "file.list", "target": "md-tools", "args": {}},
        "tool_result_contract": {
            "ok": True,
            "paths": [str(tmp_path / "md-tools"), str(tmp_path / "md-tools" / "research.md")],
            "write_effect": False,
        },
    }
    _simple_chain_protect_paths(
        protected,
        "omni_body",
        payload["tool_args"],
        payload,
        payload,
    )
    key_dir = _simple_chain_protected_key(str(tmp_path / "md-tools"))
    key_file = _simple_chain_protected_key(str(tmp_path / "md-tools" / "research.md"))
    assert key_dir not in protected
    assert key_file in protected


def test_protected_block_ignores_prose_mentions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """写新文件时正文提到已保护文件不得触发保护拦截。"""
    from v3.zongdiaodu import (
        _simple_chain_protected_block,
        _simple_chain_protected_key,
    )

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "md-tools").mkdir()
    (tmp_path / "md-tools" / "research.md").write_text("研究内容", encoding="utf-8")
    protected = {
        _simple_chain_protected_key(str(tmp_path / "md-tools" / "research.md")),
        _simple_chain_protected_key(str(tmp_path / "md-tools" / "README.md")),
    }
    args = {
        "action": "file.write",
        "target": "md-tools/summary.md",
        "args": {
            "content": "运行 python mdsummary.py README.md 后把真实输出写入 summary.md。",
        },
    }
    assert _simple_chain_protected_block("omni_body", args, protected) == []


def test_missing_deliverable_detects_subdirectory_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """裸文件名产物放在任务指定的项目子目录里也算已交付。"""
    from v3.zongdiaodu import _simple_chain_missing_deliverable_paths

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "md-tools").mkdir()
    (tmp_path / "md-tools" / "README.md").write_text(
        "# Markdown 摘要工具示例\n\n# 项目简介\n\n# 使用方法\n",
        encoding="utf-8",
    )
    (tmp_path / "md-tools" / "summary.md").write_text("摘要输出", encoding="utf-8")
    missing = _simple_chain_missing_deliverable_paths(
        "全部产物放工作区 md-tools/ 目录：README.md、summary.md",
        [],
        [],
    )
    assert missing == []
