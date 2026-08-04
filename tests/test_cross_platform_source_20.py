from __future__ import annotations

import codecs
import os
from pathlib import Path
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_cross_platform_source import (  # noqa: E402
    _validate_line_endings,
    strict_json_loads,
    validate_relative_path,
    validate_zip_member_name,
    verify_tree,
)
from omni_body_skill.tools.portable_text import (  # noqa: E402
    PortableTextError,
    decode_portable_bytes,
    normalize_newlines,
    subprocess_environment,
)


def test_p01_canonical_utf8_decodes_without_fallback() -> None:
    decoded = decode_portable_bytes("English 中文 😀".encode("utf-8"), source="p01")
    assert decoded.text == "English 中文 😀"
    assert decoded.encoding == "utf-8"
    assert decoded.legacy_fallback is False


def test_p02_utf8_bom_is_decoded_and_reported() -> None:
    decoded = decode_portable_bytes(codecs.BOM_UTF8 + "hello".encode("utf-8"), source="p02")
    assert decoded.text == "hello"
    assert decoded.had_bom is True


def test_p03_utf16le_bom_is_decoded_strictly() -> None:
    decoded = decode_portable_bytes(codecs.BOM_UTF16_LE + "中文".encode("utf-16-le"), source="p03")
    assert decoded.text == "中文"
    assert decoded.encoding == "utf-16-le"


def test_p04_utf16be_bom_is_decoded_strictly() -> None:
    decoded = decode_portable_bytes(codecs.BOM_UTF16_BE + "text".encode("utf-16-be"), source="p04")
    assert decoded.text == "text"
    assert decoded.encoding == "utf-16-be"


def test_p05_utf32_bom_is_decoded_without_nul_false_positive() -> None:
    decoded = decode_portable_bytes(codecs.BOM_UTF32_LE + "A界".encode("utf-32-le"), source="p05")
    assert decoded.text == "A界"
    assert decoded.had_bom is True


def test_p06_windows_gb18030_output_round_trips_without_replacement() -> None:
    raw = "Windows 控制台输出".encode("gb18030")
    decoded = decode_portable_bytes(raw, source="p06", allow_legacy_windows=True, legacy_encodings=("gb18030",))
    assert decoded.text == "Windows 控制台输出"
    assert decoded.encoding == "gb18030"
    assert "�" not in decoded.text


def test_p07_windows_cp1252_output_round_trips_without_replacement() -> None:
    raw = "café – résumé".encode("cp1252")
    decoded = decode_portable_bytes(raw, source="p07", allow_legacy_windows=True, legacy_encodings=("cp1252",))
    assert decoded.text == "café – résumé"
    assert decoded.legacy_fallback is True


def test_p08_invalid_bytes_fail_closed_when_legacy_is_not_explicit() -> None:
    with pytest.raises(PortableTextError):
        decode_portable_bytes(b"\xff\xfeX", source="p08")


def test_p09_nul_bytes_without_unicode_bom_fail_closed() -> None:
    with pytest.raises(PortableTextError, match="NUL"):
        decode_portable_bytes(b"a\x00b", source="p09")


def test_p10_lf_crlf_and_lone_cr_normalize_deterministically() -> None:
    text = "a\r\nb\rc\n"
    assert normalize_newlines(text, newline="\n") == "a\nb\nc\n"
    assert normalize_newlines(text, newline="\r\n") == "a\r\nb\r\nc\r\n"


def test_p11_python_child_encoding_overrides_legacy_parent_codepage() -> None:
    env = subprocess_environment({"PYTHONIOENCODING": "cp936", "PYTHONUTF8": "0", "PATH": "x"})
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["PATH"] == "x"


def test_p12_linux_source_line_endings_reject_crlf() -> None:
    failures = _validate_line_endings(Path("module.py"), b"x=1\r\n")
    assert "must use LF" in failures


def test_p13_windows_script_line_endings_reject_lone_lf() -> None:
    failures = _validate_line_endings(Path("start.ps1"), b"Write-Host ok\n")
    assert "must use CRLF" in failures


def test_p14_nfc_paths_pass_and_nfd_paths_fail() -> None:
    assert validate_relative_path("src/café.py") == []
    assert any("NFC" in item for item in validate_relative_path("src/cafe\u0301.py"))


def test_p15_windows_reserved_and_trailing_dot_names_fail_on_linux_too() -> None:
    assert any("reserved" in item.lower() for item in validate_relative_path("src/CON.txt"))
    assert any("trailing" in item.lower() for item in validate_relative_path("src/file.txt."))


def test_p16_case_insensitive_collision_is_detected_before_windows_checkout() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / ".gitattributes").write_text(
            "* text=auto eol=lf\n*.ps1 text eol=crlf\n*.bat text eol=crlf\n*.cmd text eol=crlf\n",
            encoding="utf-8",
        )
        # Windows 大小写不敏感 FS 上两个名字会落到同一物理文件，
        # 用 source_files 单测注入合成清单，验证工具的碰撞判定本身。
        (root / "Case.py").write_text("x=1\n", encoding="utf-8")
        import verify_cross_platform_source as verifier

        real_source_files = verifier.source_files

        def synthetic_source_files(_root):
            return [root / "Case.py", root / "case.py"]

        verifier.source_files = synthetic_source_files
        try:
            result = verify_tree(root)
        finally:
            verifier.source_files = real_source_files
        assert result.ok is False
        assert any("case-insensitive collision" in item for item in result.failures)


def test_p17_relative_path_budget_leaves_room_for_windows_install_root() -> None:
    assert validate_relative_path("a" * 110 + "/" + "b" * 110 + ".txt")
    assert validate_relative_path("app/backend/runtime.py") == []


def test_p18_json_duplicate_keys_and_nonfinite_numbers_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        strict_json_loads('{"a":1,"a":2}', source="p18")
    with pytest.raises(ValueError, match="non-finite"):
        strict_json_loads('{"a":NaN}', source="p18")


def test_p19_zip_member_names_are_cross_platform_and_traversal_safe() -> None:
    assert validate_zip_member_name("folder/项目.txt") == []
    assert validate_zip_member_name("../escape.txt")
    assert validate_zip_member_name(r"C:\\temp\\file.txt")
    assert validate_zip_member_name("folder/COM1.txt")


def test_p20_entire_repository_passes_cross_platform_release_gate() -> None:
    result = verify_tree(ROOT)
    assert result.ok, "\n".join(result.failures[:50])
    assert result.text_file_count > 1_000
    assert result.max_relative_utf16_units <= 220
