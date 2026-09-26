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




def test_b1_gap_clears_after_successful_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from v3.zongdiaodu import (
        _simple_chain_evidence_check,
        _simple_chain_no_deliverable_gap,
    )

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    target = tmp_path / "设计桥可用性.md"
    target.write_text("Blender 不可用：PATH 未找到。", encoding="utf-8")
    history = [_write_contract_payload(str(target))]
    message = "生成《设计桥可用性.md》到工作区，环境不可用就说明原因。"
    assert _simple_chain_no_deliverable_gap(message, history, []) == []
    allowed, status, reasons = _simple_chain_evidence_check(
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
        _simple_chain_evidence_check,
        _simple_chain_strip_tool_markup,
    )

    assert _simple_chain_strip_tool_markup("好的，先写入。<invoke>...</invoke>") == "好的，先写入。"
    assert _simple_chain_strip_tool_markup("<tool_call>{\"name\":\"x\"}</tool_call>") == ""

    monkeypatch.setenv("TIANGONG_FORCE_WORKSPACE_ROOT", str(tmp_path))
    target = tmp_path / "设计桥可用性.md"
    target.write_text("Blender 不可用：PATH 未找到。", encoding="utf-8")
    history = [_write_contract_payload(str(target))]
    # 交付物已齐备且无 gap：这是 B2 提前收尾（不再执行下一个工具）的触发条件。
    allowed, status, reasons = _simple_chain_evidence_check(
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
    # bug-fix: correction 上限 3→1（2026-08-26，凌霜修 logic 类），超限计数钳到 1
    assert correction["attempts_used"] == 1
    assert correction["attempts_max"] == 1
    payload = _simple_chain_completion_correction_payload(
        "req_x",
        ["written content cjk_chars=983 < required 2500"],
        state,
    )
    assert payload["attempts_remaining"] == 0
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
        "_simple_chain_repair_tool_args_before_execution",
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
    assert _simple_chain_requires_command_verification("确保全部测试通过") is False
    assert _simple_chain_requires_command_verification("运行 python -m pytest tests -q") is False
    assert _simple_chain_has_post_mutation_verification(
        [payload],
        "创建项目并运行 python -m pytest tests -q，确保全部测试通过",
    ) is True
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






def test_report_name_does_not_exempt_a_write_from_factual_readback() -> None:
    """文件名和内容不能代替最后写入后的字节证据。"""
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
    assert _simple_chain_has_post_mutation_verification(history, prompt) is False
    history[-1]["tool_result_contract"]["write_evidence"]["post"][0]["size_bytes"] = 27
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






def test_intermediate_project_path_is_not_rewritten_or_quality_blocked() -> None:
    """Native permission checks own scope; task quality cannot rewrite a path."""
    from v3.zongdiaodu import _simple_chain_prepare_tool_call

    prompt = (
        "创建完整 Python CLI 项目 markdown-wiki 到工作区 markdown-wiki/ 目录："
        "pyproject.toml、README.md"
    )
    args = {
        "action": "file.write",
        "target": "CLI/markdown-wiki/src/mdwiki/cli.py",
        "args": {"content": "x"},
    }
    _name, prepared, action, _issues, block = _simple_chain_prepare_tool_call(
        "req-no-remap", prompt, "omni_body", args
    )
    assert action == "file.write"
    assert prepared == args
    assert block is None

    args2 = {
        "action": "file.write",
        "target": "markdown-wiki/README.md",
        "args": {"content": "x"},
    }
    _name2, prepared2, _action2, _issues2, block2 = _simple_chain_prepare_tool_call(
        "req-correct-path", prompt, "omni_body", args2
    )
    assert prepared2 == args2
    assert block2 is None


def test_replay_cached_call_only_for_successful_results() -> None:
    """失败的缓存结果不触发去重，模型修完后必须允许重跑同一条命令。"""
    from v3.zongdiaodu import _simple_chain_should_replay_cached_call

    assert _simple_chain_should_replay_cached_call(None) is False
    assert _simple_chain_should_replay_cached_call({"ok": False}) is False
    assert _simple_chain_should_replay_cached_call({"ok": True}) is True
