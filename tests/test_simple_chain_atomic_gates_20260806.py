# -*- coding: utf-8 -*-
"""2026-08-06 原子修复回归：B1/B2/B4 完成门不变量。

B1/B3：请求了可交付产物但没有成功写动作/附件 → 硬 gap（含中文《》产物名解析）。
B2：交付物齐备后不得空转——提前收尾的前置条件（final gate 已 complete）。
B4：写工具带 readback/哈希事实但契约缺 write_effect 时，用磁盘回读证据兜底。
B7 边界：说明性语境（说明 file.read）与“参考 README.md”不得被当成交付要求。
"""
from __future__ import annotations

import json
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


def test_completion_correction_is_evidence_only_and_bounded() -> None:
    from v3.zongdiaodu import (
        _simple_chain_completion_correction_payload,
        _simple_chain_completion_correction_state,
    )

    state = {
        "completion_correction": {
            "attempts_used": 2,
            "attempts_max": 99,
            "last_blockers": [],
            "exhausted": False,
        }
    }
    correction = _simple_chain_completion_correction_state(state)
    assert correction["attempts_used"] == 2
    assert correction["attempts_max"] == 3
    payload = _simple_chain_completion_correction_payload(
        "req_x",
        ["written content cjk_chars=983 < required 2500"],
        state,
    )
    assert payload["attempts_remaining"] == 1
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in (
        "file.append",
        "file.write",
        "file.read",
        "file.hash",
        "omni_body",
        "exactly one",
        "stop read-only",
    ):
        assert forbidden not in serialized


def test_route_enforcing_completion_guards_are_removed() -> None:
    from v3 import zongdiaodu as scheduler

    for removed in (
        "_simple_chain_delivery_guard_payload",
        "_simple_chain_content_guard_payload",
        "_simple_chain_hard_continue_payload",
        "_simple_chain_explicit_action_guard_payload",
        "_simple_chain_final_gap_retry_payload",
        "_simple_chain_continue_decision_payload",
    ):
        assert not hasattr(scheduler, removed)


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
    # 任何验证后的真实写入都会使先前验证过期；不再为平台来源开后门。
    pytest_payload = {
        "ok": True,
        "tool_action": "shell.run",
        "tool_args": {"action": "shell.run", "target": "", "args": {"command": "python -m pytest tests -q"}},
        "tool_result_contract": {"ok": True, "paths": [], "observed_write_effect": False, "write_effect": False},
    }
    later_write_payload = {
        "ok": True,
        "tool_action": "file.write",
        "tool_args": {"action": "file.write", "target": "src/core.py", "args": {"content": ""}},
        "tool_result_contract": {
            "ok": True,
            "paths": ["src/core.py"],
            "observed_write_effect": True,
            "write_evidence": {"authoritative": True, "source": "tool_result", "changed_files": ["src/core.py"]},
        },
        "summary": "later model write",
    }
    assert _simple_chain_has_post_mutation_verification(
        [payload, pytest_payload, later_write_payload],
        "创建项目并运行 python -m pytest tests -q，确保全部测试通过",
    ) is False


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


def test_protected_block_allows_same_run_overwrite_but_blocks_delete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """本轮已写产物的覆盖写允许迭代；删除/移动仍受保护。"""
    from v3.zongdiaodu import (
        _simple_chain_protected_block,
        _simple_chain_protected_key,
    )

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "md-tools").mkdir()
    readme = tmp_path / "md-tools" / "README.md"
    readme.write_text("# Markdown 摘要工具\n", encoding="utf-8")
    protected = {
        _simple_chain_protected_key(str(readme)),
    }
    overwrite_args = {
        "action": "file.write",
        "target": "md-tools/README.md",
        "args": {"content": "# Markdown 摘要工具\n\n# 项目简介\n\n# 使用方法\n"},
    }
    assert _simple_chain_protected_block("omni_body", overwrite_args, protected) == []
    delete_args = {
        "action": "file.delete_to_trash",
        "target": "md-tools/README.md",
        "args": {},
    }
    assert _simple_chain_protected_block("omni_body", delete_args, protected) != []


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


def test_missing_deliverable_ignores_backup_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """备份/归档目录里的旧产物不得被当成当前任务的已交付产物。"""
    from v3.zongdiaodu import _simple_chain_missing_deliverable_paths

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "md-tools.bak-20260807").mkdir()
    (tmp_path / "md-tools.bak-20260807" / "summary.md").write_text("旧产物", encoding="utf-8")
    (tmp_path / "md-tools.bak-20260807" / "report.md").write_text("旧产物", encoding="utf-8")
    missing = _simple_chain_missing_deliverable_paths(
        "全部产物放工作区 md-tools/ 目录：summary.md、report.md",
        [],
        [],
    )
    assert set(missing) == {"summary.md", "report.md"}

    (tmp_path / "md-tools").mkdir()
    (tmp_path / "md-tools" / "report.md").write_text("新产物", encoding="utf-8")
    missing2 = _simple_chain_missing_deliverable_paths(
        "全部产物放工作区 md-tools/ 目录：summary.md、report.md",
        [],
        [],
    )
    assert missing2 == ["summary.md"]


def test_missing_deliverable_scoped_to_project_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """指定项目目录后，无关目录里的同名旧产物不得算已交付。"""
    from v3.zongdiaodu import _simple_chain_missing_deliverable_paths

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "agent-tools").mkdir()
    (tmp_path / "agent-tools" / "report.md").write_text("别的项目产物", encoding="utf-8")
    missing = _simple_chain_missing_deliverable_paths(
        "全部产物放工作区 md-tools/ 目录：report.md、summary.md",
        [],
        [],
    )
    assert set(missing) == {"report.md", "summary.md"}

    (tmp_path / "md-tools").mkdir()
    (tmp_path / "md-tools" / "report.md").write_text("本任务产物", encoding="utf-8")
    missing2 = _simple_chain_missing_deliverable_paths(
        "全部产物放工作区 md-tools/ 目录：report.md、summary.md",
        [],
        [],
    )
    assert missing2 == ["summary.md"]


def test_productive_run_attempt_exempts_required_script_runs() -> None:
    """任务明确要求运行脚本时，真正的运行调用不被交付守卫当探测拦截。"""
    from v3.zongdiaodu import _simple_chain_is_productive_run_attempt

    prompt = "请运行 python mdsummary.py README.md，把真实输出写入 summary.md"
    assert _simple_chain_is_productive_run_attempt(
        "python.run",
        {"action": "python.run", "target": "md-tools/mdsummary.py", "args": {}},
        prompt,
    ) is True
    assert _simple_chain_is_productive_run_attempt(
        "python.run",
        {"action": "python.run", "target": "", "args": {}},
        prompt,
    ) is False
    assert _simple_chain_is_productive_run_attempt(
        "shell.run",
        {
            "action": "shell.run",
            "target": "md-tools",
            "args": {"command": "python mdsummary.py README.md"},
        },
        prompt,
    ) is True
    assert _simple_chain_is_productive_run_attempt(
        "shell.run",
        {
            "action": "shell.run",
            "target": "md-tools",
            "args": {"command": "dir /B md-tools"},
        },
        prompt,
    ) is False
    pytest_prompt = "全部完成后从项目根目录运行 python -m pytest tests -q，把真实输出写入《测试报告.md》"
    assert _simple_chain_is_productive_run_attempt(
        "shell.run",
        {
            "action": "shell.run",
            "target": "textutils",
            "args": {"command": "python -m pytest tests -q"},
        },
        pytest_prompt,
    ) is True
    assert _simple_chain_is_productive_run_attempt(
        "shell.run",
        {
            "action": "shell.run",
            "target": "textutils",
            "args": {"command": "python -m pytest tests -q"},
        },
        "整理工作区文件",
    ) is False
    assert _simple_chain_is_productive_run_attempt(
        "python.run",
        {"action": "python.run", "target": "md-tools/mdsummary.py", "args": {}},
        "整理工作区文件",
    ) is False


def test_post_mutation_verification_ignores_report_document_write() -> None:
    """先跑测试、再写《测试报告.md》的正确顺序不得被误判为缺验证。"""
    from v3.zongdiaodu import _simple_chain_has_post_mutation_verification

    def write_payload(target: str, content: str) -> dict:
        return {
            "ok": True,
            "tool_action": "file.write",
            "tool_args": {
                "action": "file.write",
                "target": target,
                "args": {"content": content},
            },
            "tool_result_contract": {
                "ok": True,
                "paths": [target],
                "observed_write_effect": True,
                "write_evidence": {
                    "authoritative": True,
                    "source": "tool_pre_post",
                    "changed_files": [target],
                    "post": [{"path": target, "exists": True, "is_file": True}],
                },
            },
        }

    run_payload = {
        "ok": True,
        "tool_action": "shell.run",
        "tool_args": {
            "action": "shell.run",
            "target": "textutils",
            "args": {"command": "python -m pytest tests -q"},
        },
        "tool_result_contract": {"ok": True, "paths": [], "write_effect": False},
    }
    history = [
        write_payload("textutils/src/textutils/core.py", "def reverse_words(...)"),
        run_payload,
        write_payload("textutils/测试报告.md", "pytest 运行结果：8 passed"),
    ]
    prompt = (
        "创建 Python 库项目 textutils 到工作区 textutils/ 目录："
        "全部完成后从项目根目录运行 python -m pytest tests -q，"
        "把真实测试输出写入《测试报告.md》"
    )
    assert _simple_chain_has_post_mutation_verification(history, prompt) is True
def test_platform_completion_helpers_are_removed() -> None:
    import v3.zongdiaodu as scheduler

    removed = (
        "_simple_chain_fallback_write_deliverable",
        "_simple_chain_fallback_zip_deliverable",
        "_simple_chain_try_fallback_delivery",
        "_simple_chain_platform_run_verification",
        "_simple_chain_platform_run_tests_verification",
        "_simple_chain_platform_runtime_verified",
    )
    assert all(not hasattr(scheduler, name) for name in removed)


def test_missing_deliverable_scoped_to_declared_project_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """指定项目目录后，写到别的目录的同名文件不得算已交付。"""
    from v3.zongdiaodu import _simple_chain_missing_deliverable_paths

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    prompt = (
        "创建完整 Python CLI 项目 markdown-wiki 到工作区 markdown-wiki/ 目录："
        "pyproject.toml、README.md"
    )

    def write_payload(target: str) -> dict:
        return {
            "ok": True,
            "tool_action": "file.write",
            "tool_args": {
                "action": "file.write",
                "target": target,
                "args": {"content": "x"},
            },
            "tool_result_contract": {
                "ok": True,
                "paths": [target],
                "observed_write_effect": True,
                "write_evidence": {"changed_files": [target]},
            },
        }

    (tmp_path / "CLI" / "markdown-wiki").mkdir(parents=True)
    (tmp_path / "CLI" / "markdown-wiki" / "README.md").write_text("x", encoding="utf-8")
    missing = _simple_chain_missing_deliverable_paths(
        prompt,
        [write_payload("CLI/markdown-wiki/README.md")],
        [],
    )
    assert "README.md" in missing

    (tmp_path / "markdown-wiki").mkdir()
    (tmp_path / "markdown-wiki" / "README.md").write_text("x", encoding="utf-8")
    missing2 = _simple_chain_missing_deliverable_paths(
        prompt,
        [write_payload("markdown-wiki/README.md")],
        [],
    )
    assert "README.md" not in missing2


def test_project_dir_block_confines_writes_to_declared_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """项目目录围栏：写操作写到目录外直接拦截，读操作不受限。"""
    from v3.zongdiaodu import _simple_chain_prepare_tool_call

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    prompt = (
        "创建完整 Python CLI 项目 markdown-wiki 到工作区 markdown-wiki/ 目录："
        "pyproject.toml、README.md"
    )
    _name, _args, _action, _issues, block = _simple_chain_prepare_tool_call(
        "req_x",
        prompt,
        "omni_body",
        {
            "action": "file.write",
            "target": "CLI/README.md",
            "args": {"content": "x"},
        },
    )
    assert block is not None
    assert block.get("schema") == "tiangong.v3.simple_chain.project_dir_confined.v1"

    _name2, _args2, _action2, _issues2, block2 = _simple_chain_prepare_tool_call(
        "req_x",
        prompt,
        "omni_body",
        {
            "action": "file.write",
            "target": "markdown-wiki/README.md",
            "args": {"content": "x"},
        },
    )
    assert block2 is None

    _name3, _args3, _action3, _issues3, block3 = _simple_chain_prepare_tool_call(
        "req_x",
        prompt,
        "omni_body",
        {
            "action": "file.read",
            "target": "CLI/markdown-wiki/README.md",
            "args": {},
        },
    )
    assert block3 is None


def test_repair_remaps_wrong_parent_project_paths() -> None:
    """写目标带 <错误父目录>/<项目目录>/ 时，自动改写到项目目录下。"""
    from v3.zongdiaodu import _simple_chain_repair_tool_args_before_execution

    prompt = (
        "创建完整 Python CLI 项目 markdown-wiki 到工作区 markdown-wiki/ 目录："
        "pyproject.toml、README.md"
    )
    args = {
        "action": "file.write",
        "target": "CLI/markdown-wiki/src/mdwiki/cli.py",
        "args": {"content": "x"},
    }
    repaired = _simple_chain_repair_tool_args_before_execution(prompt, "file.write", args)
    assert repaired["target"] == "markdown-wiki/src/mdwiki/cli.py"

    args2 = {
        "action": "file.write",
        "target": "markdown-wiki/README.md",
        "args": {"content": "x"},
    }
    repaired2 = _simple_chain_repair_tool_args_before_execution(prompt, "file.write", args2)
    assert repaired2["target"] == "markdown-wiki/README.md"


def test_recent_tool_failure_detects_last_failed_observation() -> None:
    """最近一次工具失败时，交付守卫应放行修复动作。"""
    from v3.zongdiaodu import _simple_chain_recent_tool_failure

    assert _simple_chain_recent_tool_failure([]) is False
    assert _simple_chain_recent_tool_failure([{"ok": True}]) is False
    assert _simple_chain_recent_tool_failure([{"ok": True}, {"ok": False}]) is True


def test_project_internal_inspection_exempts_project_reads() -> None:
    """项目目录内的列目录/读文件属于工程自检，不算无意义探测。"""
    from v3.zongdiaodu import _simple_chain_is_project_internal_inspection

    prompt = "创建完整 Python CLI 项目 markdown-wiki 到工作区 markdown-wiki/ 目录"
    assert _simple_chain_is_project_internal_inspection(
        prompt,
        "file.list",
        {"action": "file.list", "target": "markdown-wiki", "args": {}},
    ) is True
    assert _simple_chain_is_project_internal_inspection(
        prompt,
        "file.list",
        {"action": "file.list", "target": "workspace", "args": {}},
    ) is False
    assert _simple_chain_is_project_internal_inspection(
        prompt,
        "file.write",
        {"action": "file.write", "target": "markdown-wiki/README.md", "args": {}},
    ) is False


def test_replay_cached_call_only_for_successful_results() -> None:
    """失败的缓存结果不触发去重，模型修完后必须允许重跑同一条命令。"""
    from v3.zongdiaodu import _simple_chain_should_replay_cached_call

    assert _simple_chain_should_replay_cached_call(None) is False
    assert _simple_chain_should_replay_cached_call({"ok": False}) is False
    assert _simple_chain_should_replay_cached_call({"ok": True}) is True
