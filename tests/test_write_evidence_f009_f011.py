# -*- coding: utf-8 -*-
"""F-009 / F-011 回归用例（2026-07-27 源码核对）。

F-009：执行类动作（shell.run / python.run / quality.run_tests）在没有真实
changed_files/deleted_files 时不得生成权威写凭据；真写动作（file.write）行为不变。
F-011：shell 落盘判定必须基于命令级快照对比，而不是"目标 mtime 在 300 秒内"——
命令未写文件、但目标刚被外部改过时，不得判为已写入。
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest


def test_execution_actions_without_delta_have_no_write_evidence() -> None:
    """文档 F-009 隔离用例表：ok=True 的执行类动作不得自证写入。"""
    from v3.tool_result_contract import normalize_tool_result

    for action in ("shell.run", "python.run", "quality.run_tests", "command.run", "run"):
        contract = normalize_tool_result(
            "omni_body",
            {"action": action, "ok": True, "returncode": 0, "stdout": "done"},
        )
        assert contract["write_evidence"] is None, action
        assert contract["observed_write_effect"] is False, action
        assert contract["write_effect"] is False, action


def test_execution_action_with_broker_delta_stays_authoritative() -> None:
    """执行类动作带沙箱 broker 的真实变更清单时，仍应生成权威凭据。"""
    from v3.tool_result_contract import normalize_tool_result

    contract = normalize_tool_result(
        "omni_body",
        {"action": "shell.run", "ok": True, "changed_files": ["out/a.txt"], "deleted_files": []},
    )
    evidence = contract["write_evidence"]
    assert evidence is not None
    assert evidence["authoritative"] is True
    assert evidence["source"] == "sandbox_broker"
    assert evidence["changed_files"] == ["out/a.txt"]
    assert contract["observed_write_effect"] is True


def test_file_write_new_file_is_authoritative() -> None:
    """真写动作行为不变：file.write 新建文件 -> 权威凭据。"""
    from v3.tool_result_contract import normalize_tool_result

    contract = normalize_tool_result(
        "omni_body",
        {
            "action": "file.write",
            "ok": True,
            "path": r"C:\ws\t.txt",
            "snapshots": [
                {"path": r"C:\ws\t.txt", "existed": False, "kind": "other", "backup_path": None}
            ],
            "evidence": {"path": r"C:\ws\t.txt", "exists": True, "is_file": True, "size_bytes": 5, "sha256": "abc"},
        },
    )
    evidence = contract["write_evidence"]
    assert evidence is not None
    assert evidence["authoritative"] is True
    assert evidence["changed_files"] == [r"C:\ws\t.txt"]


def test_file_write_unchanged_content_has_no_write_evidence(tmp_path: Path) -> None:
    """file.write 覆盖已有文件但内容不变（备份与写后哈希一致）-> 不伪造增量凭据，
    但以 verified_unchanged_files 如实标记"目标状态已在位"（幂等重写不得误判未验证）。"""
    from v3.tool_result_contract import normalize_tool_result

    payload = b"same-content"
    backup = tmp_path / "backup.txt"
    backup.write_bytes(payload)
    contract = normalize_tool_result(
        "omni_body",
        {
            "action": "file.write",
            "ok": True,
            "path": r"C:\ws\t.txt",
            "snapshots": [{"path": r"C:\ws\t.txt", "existed": True, "backup_path": str(backup)}],
            "evidence": {
                "path": r"C:\ws\t.txt",
                "exists": True,
                "is_file": True,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        },
    )
    evidence = contract["write_evidence"]
    assert evidence is not None
    assert evidence["authoritative"] is True
    # 不冒充变更：changed_files 必须为空；幂等在位单独成键
    assert evidence["changed_files"] == []
    assert evidence["verified_unchanged_files"] == [r"C:\ws\t.txt"]
    post = evidence["post"][0]
    assert post["idempotent_unchanged"] is True
    assert post["pre_sha256"] == hashlib.sha256(payload).hexdigest()


def test_file_read_never_has_write_evidence() -> None:
    from v3.tool_result_contract import normalize_tool_result

    contract = normalize_tool_result(
        "omni_body",
        {"action": "file.read", "ok": True, "content": "x", "path": r"C:\ws\t.txt"},
    )
    assert contract["write_evidence"] is None
    assert contract["observed_write_effect"] is False


def test_contract_has_no_freshness_heuristic() -> None:
    """F-011：现役契约代码不得再含重定向正则或 300 秒新鲜度判定。"""
    import v3.tool_result_contract as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "_SHELL_REDIRECT_RE" not in source
    assert "observation_time" not in source


@pytest.mark.skipif(os.name != "nt", reason="SandboxRunner Windows 端到端")
def test_sandbox_no_write_command_does_not_claim_recent_external_file() -> None:
    """F-011 端到端：命令未写文件，但目标 5 分钟内被外部改过 -> 不得判为已写入。"""
    from omni_body_skill.tools.sandbox_runtime import SandboxRunner

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TIANGONG_SANDBOX_COMPAT", "1")
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = SandboxRunner(
                workspace=root,
                state_root=root / ".state",
                trash_root=root / ".trash",
            )
            victim = root / "victim.txt"
            victim.write_text("external-change", encoding="utf-8")
            now = time.time()
            os.utime(victim, (now, now))  # 目标在 300 秒新鲜窗口内被外部修改

            result = runner.run("cmd.exe /c echo no-write-happened", timeout_seconds=60)

            assert result.get("changed_files") == [], result.get("changed_files")
            assert (root / "victim.txt").read_text(encoding="utf-8") == "external-change"

            from v3.tool_result_contract import normalize_tool_result

            contract = normalize_tool_result("omni_body", {"action": "shell.run", **result})
            assert contract["observed_write_effect"] is False
            assert contract["write_evidence"] is None


@pytest.mark.skipif(os.name != "nt", reason="SandboxRunner Windows 端到端")
def test_sandbox_real_redirect_write_is_detected() -> None:
    """F-011 对照：真实 `>` 重定向写入必须被快照对比捕获并落盘。"""
    from omni_body_skill.tools.sandbox_runtime import SandboxRunner

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TIANGONG_SANDBOX_COMPAT", "1")
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = SandboxRunner(
                workspace=root,
                state_root=root / ".state",
                trash_root=root / ".trash",
            )
            result = runner.run("cmd.exe /c echo hello > made_by_cmd.txt", timeout_seconds=60)

            changed = [str(p).replace("\\", "/") for p in (result.get("changed_files") or [])]
            assert "made_by_cmd.txt" in changed
            assert (root / "made_by_cmd.txt").read_text(encoding="utf-8").strip() == "hello"

            from v3.tool_result_contract import normalize_tool_result

            contract = normalize_tool_result("omni_body", {"action": "shell.run", **result})
            evidence = contract["write_evidence"]
            assert evidence is not None and evidence["authoritative"] is True


def test_desktop_deliverable_format_magic_check(tmp_path: Path) -> None:
    """D-24：文本冒充 .docx 不得过桌面交付校验；真 zip 容器过。"""
    from v3.zongdiaodu import _simple_chain_paths_match_desktop

    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    fake = desktop / "我的母亲.docx"
    fake.write_text("纯文本冒充的 word", encoding="utf-8")
    real = desktop / "关于母亲的作文.docx"
    import io, zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
    real.write_bytes(buf.getvalue())

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("v3.simple_chain.kernel._path_under_desktop", lambda p: str(p).startswith(str(desktop)))
        mp.setattr("v3.simple_chain.kernel._simple_chain_expected_suffixes", lambda m: {".docx"} if "word" in m or "docx" in m else set())
        msg = "帮我在桌面上写个作文，我要word格式"
        assert _simple_chain_paths_match_desktop([str(fake)], msg) is False
        assert _simple_chain_paths_match_desktop([str(real)], msg) is True


def test_desktop_deliverable_text_suffix_skips_format_check(tmp_path: Path) -> None:
    """文本类后缀不做魔数要求。"""
    from v3.zongdiaodu import _simple_chain_desktop_file_format_ok

    note = tmp_path / "note.txt"
    note.write_text("plain", encoding="utf-8")
    assert _simple_chain_desktop_file_format_ok(str(note), ".txt") is True
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_text("not a pdf", encoding="utf-8")
    assert _simple_chain_desktop_file_format_ok(str(fake_pdf), ".pdf") is False
