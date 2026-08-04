from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile
from unittest import mock
import wave

import pytest


def test_atomic_json_write_retries_only_transient_permission_errors() -> None:
    from v3 import duihua_qiaojie

    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "state.json"
        real_replace = os.replace
        attempts = 0

        def transient_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise PermissionError(5, "transient lock")
            real_replace(source, destination)

        with mock.patch.object(duihua_qiaojie.os, "replace", side_effect=transient_replace), mock.patch.object(
            duihua_qiaojie.time,
            "sleep",
        ) as sleep:
            duihua_qiaojie._atomic_write_json(target, {"中文": "完整", "value": 7})

        assert json.loads(target.read_text(encoding="utf-8")) == {"中文": "完整", "value": 7}
        assert attempts == 3
        assert [call.args[0] for call in sleep.call_args_list] == [0.05, 0.1]
        assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_atomic_json_write_does_not_sleep_after_the_final_failed_attempt() -> None:
    from v3 import duihua_qiaojie

    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "state.json"
        with mock.patch.object(
            duihua_qiaojie.os,
            "replace",
            side_effect=PermissionError(5, "locked"),
        ), mock.patch.object(duihua_qiaojie.time, "sleep") as sleep:
            try:
                duihua_qiaojie._atomic_write_json(target, {"value": 1})
            except PermissionError:
                pass
            else:  # pragma: no cover - protects the fail-closed contract
                raise AssertionError("the final PermissionError must propagate")

        assert sleep.call_count == 4
        assert not target.exists()
        assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_plain_json_name_is_not_promoted_to_a_tool_call() -> None:
    from v3.gutong.gutong_ceng import GutongCeng

    assert GutongCeng._json_gongju_diaoyong({"name": "张三", "age": 30}) == ("", {})
    assert GutongCeng._json_gongju_diaoyong(
        {"name": "omni_body", "arguments": {"action": "system.health"}}
    ) == ("omni_body", {"action": "system.health"})
    assert GutongCeng._json_gongju_diaoyong(
        {
            "function": {
                "name": "omni_body",
                "arguments": json.dumps(
                    json.dumps({"action": "system.health"}, ensure_ascii=False),
                    ensure_ascii=False,
                ),
            }
        }
    ) == ("omni_body", {"action": "system.health"})


def test_execution_success_without_delta_never_becomes_write_evidence() -> None:
    from v3.tool_result_contract import normalize_tool_result

    contract = normalize_tool_result(
        "omni_body",
        {
            "ok": True,
            "action": "shell.run",
            "target": "planned.txt",
            "result": {
                "execution": {
                    "returncode": 0,
                    "changed_files": [],
                    "deleted_files": [],
                }
            },
        },
    )
    assert contract["ok"] is True
    assert contract["may_mutate"] is True
    assert contract["write_effect"] is False
    assert contract["write_evidence"] is None
    assert "planned.txt" not in contract["artifacts"]


def test_sandbox_delta_is_authoritative_write_evidence_and_is_idempotent() -> None:
    from v3.tool_result_contract import normalize_tool_result

    raw = {
        "ok": True,
        "action": "shell.run",
        "result": {
            "execution": {
                "returncode": 0,
                "changed_files": ["result.txt"],
                "deleted_files": ["old.tmp"],
            }
        },
    }
    first = normalize_tool_result("omni_body", raw)
    second = normalize_tool_result("omni_body", raw)

    assert first["write_effect"] is True
    assert first["write_evidence"]["source"] == "sandbox_broker"
    assert first["write_evidence"]["changed_files"] == ["result.txt"]
    assert first["write_evidence"]["deleted_files"] == ["old.tmp"]
    assert first["write_evidence"] == second["write_evidence"]
    assert first["artifacts"] == ["result.txt"]


def test_failed_and_missing_targets_are_not_deliverables() -> None:
    from v3.zongdiaodu import _simple_chain_collect_paths

    with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
        os.environ,
        {"TIANGONG_FORCE_WORKSPACE_ROOT": temporary},
        clear=False,
    ):
        root = Path(temporary)
        actual = root / "actual.txt"
        actual.write_text("ok", encoding="utf-8")
        history = [
            {
                "ok": False,
                "tool_action": "file.write",
                "tool_args": {"target": "failed.docx"},
                "tool_result_contract": {
                    "ok": False,
                    "artifacts": ["failed.docx"],
                    "paths": ["failed.docx"],
                },
            },
            {
                "ok": True,
                "tool_action": "file.write",
                "tool_args": {"target": "missing.docx"},
                "tool_result_contract": {
                    "ok": True,
                    "artifacts": ["missing.docx"],
                    "paths": ["missing.docx"],
                },
            },
            {
                "ok": True,
                "tool_action": "file.write",
                "tool_result_contract": {
                    "ok": True,
                    "artifacts": ["actual.txt"],
                    "paths": ["actual.txt"],
                },
            },
        ]
        paths = _simple_chain_collect_paths(history, [])

        assert paths == [str(actual.resolve())]


def test_windows_utf8_shell_wrapper_keeps_command_data_out_of_outer_cmd_parsing() -> None:
    from omni_body_skill.tools.sandbox_runtime import (
        WINDOWS_UTF8_SHELL_MARKER,
        _prepare_windows_utf8_shell_command,
    )

    command = 'echo "中文&A|B>C<D" && python -c "print(\'中文&|\')"'
    prepared = _prepare_windows_utf8_shell_command([WINDOWS_UTF8_SHELL_MARKER, command])

    assert isinstance(prepared, list)
    assert prepared[0].lower().endswith(("powershell.exe", "pwsh.exe"))
    assert "-EncodedCommand" in prepared
    encoded = prepared[prepared.index("-EncodedCommand") + 1]
    script = base64.b64decode(encoded).decode("utf-16-le")
    assert command not in script
    assert base64.b64encode(command.encode("utf-16-le")).decode("ascii") in script
    assert "[Console]::OutputEncoding" in script


def test_windows_powershell_wrapper_uses_private_drive_after_path_rewrite() -> None:
    from omni_body_skill.tools.sandbox_runtime import (
        WINDOWS_POWERSHELL_SHELL_MARKER,
        _prepare_windows_utf8_shell_command,
    )

    command = "Set-Content -NoNewline -LiteralPath '结果.txt' -Value '完整'"
    prepared = _prepare_windows_utf8_shell_command(
        [WINDOWS_POWERSHELL_SHELL_MARKER, command],
        cwd=Path("C:/sandbox/workspace"),
    )

    assert isinstance(prepared, list)
    encoded = prepared[prepared.index("-EncodedCommand") + 1]
    script = base64.b64decode(encoded).decode("utf-16-le")
    assert command not in script
    assert base64.b64encode(command.encode("utf-16-le")).decode("ascii") in script
    assert "New-PSDrive -Name TiangongWorkspace" in script
    assert "[Environment]::CurrentDirectory=$cwd" in script
    assert "$ProgressPreference='SilentlyContinue'" in script


@pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer integration")
def test_windows_gateway_a5_shell_preserves_utf8_and_cmd_metacharacters() -> None:
    from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        runtime = BodyRuntime(
            BodyRuntimeConfig(
                workspace=str(root),
                fact_kernel_enabled=False,
                require_confirmation_for_a4=False,
                allow_shell=True,
            )
        )
        command = (
            'echo \u4e2d\u6587>utf8.txt'
            ' && echo "A&B">>utf8.txt'
            ' && echo "A|B">>utf8.txt'
            ' && echo "A>B">>utf8.txt'
            ' && echo "A<B">>utf8.txt'
        )
        result = runtime.run("shell.run", "", {"command": command, "timeout": 30})

        assert result["success"] is True, result
        execution = result["execution"]
        assert execution["containment"] == "gateway_a5_host_execution"
        assert execution["timeout_disabled"] is True
        assert execution["changed_files"] == ["utf8.txt"]
        text = (root / "utf8.txt").read_bytes().decode("utf-8").replace("\r", "")
        assert text.strip().split("\n") == [
            "中文 ",
            '"A&B" ',
            '"A|B" ',
            '"A>B" ',
            '"A<B"',
        ]


def _body_runtime(root: Path):
    from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig

    return BodyRuntime(
        BodyRuntimeConfig(
            workspace=str(root),
            fact_kernel_enabled=False,
            require_confirmation_for_a4=False,
        )
    )


def test_mindmap_html_target_cannot_overwrite_mermaid_or_opml() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        runtime = _body_runtime(root)
        requested = root / "城市韧性.html"
        result = runtime._action_mindmap_create(
            "mindmap-collision",
            str(requested),
            {
                "title": "城市韧性 <安全>",
                "tree": [{"预防": ["监测"]}, {"响应": ["恢复"]}],
                "opml": True,
            },
        )

        markdown = root / "城市韧性.md"
        opml = root / "城市韧性.opml"
        assert markdown.is_file() and requested.is_file() and opml.is_file()
        assert "```mermaid" in markdown.read_text(encoding="utf-8")
        html = requested.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html
        assert 'class="mermaid"' in html
        assert "<安全>" not in html
        assert result["output"]["path"] == str(markdown)
        assert result["html"]["path"] == str(requested)
        assert result["opml"]["path"] == str(opml)
        assert len(result["snapshots"]) == 3


def test_search_result_parser_returns_real_links_and_deduplicates() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        runtime = _body_runtime(Path(temporary))
        html = """
        <ul>
          <li class="b_algo"><h2><a href="https://example.com/a">天工造物 功能说明</a></h2>
          <p>第一条中文摘要 &amp; evidence</p></li>
          <li class="b_algo"><h2><a href="https://example.com/a#again">重复结果</a></h2></li>
          <li class="b_algo"><h2><a href="https://docs.example.org/b">第二条说明</a></h2></li>
        </ul>
        """
        results = runtime._extract_search_results(html)

        assert [item["url"] for item in results] == [
            "https://example.com/a",
            "https://docs.example.org/b",
        ]
        assert results[0]["snippet"] == "第一条中文摘要 & evidence"


def test_file_browser_fetch_is_confined_to_workspace() -> None:
    from omni_body_skill.tools.omni_body_tool import OmniBodyError

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "workspace"
        root.mkdir()
        inside = root / "页面.html"
        inside.write_text("<title>内部</title><p>正文</p>", encoding="utf-8")
        outside = Path(temporary) / "outside.html"
        outside.write_text("secret", encoding="utf-8")
        runtime = _body_runtime(root)

        fetched = runtime._browser_fetch(inside.as_uri(), {})
        assert fetched["status"] == 200
        assert "正文" in fetched["text"]
        with pytest.raises(OmniBodyError):
            runtime._browser_fetch(outside.as_uri(), {})


@pytest.mark.skipif(os.name != "nt", reason="Windows CJK font integration")
def test_image_add_text_uses_scalable_cjk_font_and_writes_visible_pixels() -> None:
    from PIL import Image, ImageChops

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "blank.png"
        Image.new("RGB", (720, 240), "white").save(source)
        runtime = _body_runtime(root)
        result = runtime._action_image_add_text(
            "cjk-image",
            str(source),
            {
                "output": "中文标题.png",
                "text": "天工造物 中文完整",
                "x": 30,
                "y": 40,
                "font_size": 56,
                "color": "#101010",
            },
        )

        output = root / "中文标题.png"
        with Image.open(output).convert("RGB") as rendered, Image.open(source).convert("RGB") as blank:
            changed = ImageChops.difference(rendered, blank)
            assert changed.getbbox() is not None
            assert sum(1 for pixel in changed.getdata() if pixel != (0, 0, 0)) > 500
        assert result["font"]["scalable"] is True
        assert Path(result["font"]["path"]).name.lower() in {
            "msyh.ttc",
            "msyhbd.ttc",
            "simhei.ttf",
            "simsun.ttc",
            "deng.ttf",
        }


@pytest.mark.skipif(os.name != "nt", reason="Windows CJK PDF integration")
def test_pdf_create_from_text_embeds_cjk_font() -> None:
    from pypdf import PdfReader

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        runtime = _body_runtime(root)
        result = runtime._action_pdf_create_from_text(
            "cjk-pdf",
            "中文报告.pdf",
            {"title": "天工造物报告", "text": "中文正文完整可读\n第二行证据"},
        )

        output = root / "中文报告.pdf"
        assert output.read_bytes().startswith(b"%PDF-")
        assert output.stat().st_size > 10_000
        assert result["font_embedded"] is True
        assert Path(result["font_path"]).is_file()
        extracted = "\n".join(page.extract_text() or "" for page in PdfReader(str(output)).pages)
        assert "中文正文完整可读" in extracted


@pytest.mark.skipif(os.name != "nt", reason="Windows SAPI integration")
def test_audio_tts_creates_valid_chinese_wav_inside_appcontainer() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        runtime = _body_runtime(root)
        result = runtime._action_audio_tts(
            "sapi-tts",
            None,
            {"text": "天工造物中文语音实测", "output": "中文语音.wav", "timeout": 45},
        )

        output = root / "中文语音.wav"
        with wave.open(str(output), "rb") as reader:
            assert reader.getnchannels() >= 1
            assert reader.getframerate() > 0
            assert reader.getnframes() > reader.getframerate() // 4
        assert result["engine"] == "windows-sapi"
        assert result["subprocess"]["containment"] == "trusted-windows-sapi-broker"
