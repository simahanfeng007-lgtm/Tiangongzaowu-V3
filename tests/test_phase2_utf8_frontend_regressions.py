from __future__ import annotations

import tempfile
import unicodedata
from pathlib import Path

import pytest

from communication_service.wechat_attachment import WechatAttachmentError, validate_attachment_source
from contracts.models import validate_safe_filename
from omni_body_skill.tools.omni_body_tool import BodyRuntime, BodyRuntimeConfig
from v3.zongdiaodu import _simple_chain_tool_batch_requires_order

ROOT = Path(__file__).resolve().parents[1]


def test_frontend_kernel_block_message_is_not_overwritten_by_ready_label() -> None:
    source = (ROOT / "app/frontend-v2/renderer/plugins/conversation-panel.mjs").read_text(encoding="utf-8")
    assert "chatStatus.textContent = gate.blocked" in source
    assert "if (gate.blocked) chatStatus.textContent = gate.message;\n      chatStatus.textContent =" not in source


def test_renderer_local_storage_strips_all_model_credential_aliases() -> None:
    source = (ROOT / "app/frontend-v2/renderer/runtime/http-runtime.mjs").read_text(encoding="utf-8")
    assert 'for (const key of ["modelApiKey", "api_key", "clear_api_key"]) delete next[key];' in source
    assert "if (containsCredential)" in source
    assert "return await apiJson(\"/api/v1/llm/settings\"" in source


def test_diagnostic_and_chat_upload_preload_channels_have_main_handlers() -> None:
    preload = (ROOT / "app/preload.js").read_text(encoding="utf-8")
    main = (ROOT / "app/main.js").read_text(encoding="utf-8")
    assert 'writeDiagnostic: (kind, detail = "") => ipcRenderer.send("diagnostic:write"' in preload
    assert 'uploadChatFiles: (payload) => ipcRenderer.invoke("chatFiles:upload"' in preload
    assert 'onTrusted("diagnostic:write"' in main
    assert 'handleTrusted("chatFiles:upload"' in main


def test_safe_filename_enforces_nfc_and_cross_platform_component_limits() -> None:
    assert validate_safe_filename("项目-🚀.txt") == "项目-🚀.txt"
    with pytest.raises(ValueError, match="NFC"):
        validate_safe_filename(unicodedata.normalize("NFD", "café.txt"))
    with pytest.raises(ValueError, match="component limit"):
        validate_safe_filename("界" * 86 + ".txt")


@pytest.mark.parametrize("name", ["CON.txt", "com1.LOG", "Nul.json", "LPT9.md"])
def test_safe_filename_rejects_windows_devices_case_insensitively(name: str) -> None:
    with pytest.raises(ValueError, match="reserved on Windows"):
        validate_safe_filename(name)


@pytest.mark.parametrize("name", ["a\u202Etxt.exe", "zero\u200Bwidth.txt", "join\u200Dname.txt", "bad-\ud800.txt"])
def test_safe_filename_rejects_directional_invisible_and_invalid_unicode(name: str) -> None:
    with pytest.raises(ValueError):
        validate_safe_filename(name)


def test_attachment_text_boms_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        utf8 = root / "utf8.part"
        utf8.write_bytes(b"\xef\xbb\xbfhello")
        with pytest.raises(WechatAttachmentError, match="text_utf8_bom_forbidden"):
            validate_attachment_source(utf8, filename="bom.txt", declared_mime="text/plain")
        utf16 = root / "utf16.part"
        utf16.write_bytes(b"\xff\xfeh\x00i\x00")
        with pytest.raises(WechatAttachmentError, match="text_encoding_forbidden"):
            validate_attachment_source(utf16, filename="utf16.txt", declared_mime="text/plain")


def test_omni_text_write_is_byte_stable_utf8_and_rejects_other_encodings() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(root), fact_kernel_enabled=False))
        content = "line one\r\n中文 😀\r\n"
        written = runtime.run("file.write", "stable.txt", {"content": content, "encoding": "utf-8"})
        assert written.get("success") is True
        assert (root / "stable.txt").read_bytes() == content.encode("utf-8")
        read = runtime.run("file.read", "stable.txt", {"encoding": "utf-8"})
        assert read.get("success") is True
        assert read["content"] == content
        rejected = runtime.run("file.write", "cp936.txt", {"content": "中文", "encoding": "cp936"})
        assert rejected.get("success") is False
        assert "canonical UTF-8" in rejected.get("message", "")
        assert not (root / "cp936.txt").exists()


def test_omni_file_rename_uses_relative_contract_without_absolute_self_rejection() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        (root / "before.txt").write_bytes(b"rename")
        runtime = BodyRuntime(BodyRuntimeConfig(workspace=str(root), fact_kernel_enabled=False))
        result = runtime.run("file.rename", "before.txt", {"new_name": "after.txt"})
        assert result.get("success") is True, result
        assert not (root / "before.txt").exists()
        assert (root / "after.txt").read_bytes() == b"rename"


def test_tool_batches_serialize_all_mutations_but_allow_read_only_parallelism() -> None:
    assert _simple_chain_tool_batch_requires_order([
        ("omni_body", {"action": "file.write", "target": "a.txt", "args": {"content": "x"}}, 1, "c1"),
        ("omni_body", {"action": "file.append", "target": "a.txt", "args": {"content": "y"}}, 2, "c2"),
    ]) is True
    assert _simple_chain_tool_batch_requires_order([
        ("omni_body", {"action": "file.read", "target": "a.txt", "args": {}}, 1, "c1"),
        ("omni_body", {"action": "file.hash", "target": "b.txt", "args": {}}, 2, "c2"),
    ]) is False
    assert _simple_chain_tool_batch_requires_order([("malformed",)]) is True
