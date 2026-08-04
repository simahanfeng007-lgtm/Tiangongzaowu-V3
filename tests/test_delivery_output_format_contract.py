from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend" / "tiangong-backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _write_valid_format(path: Path, suffix: str) -> None:
    if suffix in {".docx", ".xlsx", ".pptx", ".zip"}:
        member = {
            ".docx": "word/document.xml",
            ".xlsx": "xl/workbook.xml",
            ".pptx": "ppt/presentation.xml",
            ".zip": "payload.bin",
        }[suffix]
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(member, b"content")
        return
    payloads = {
        ".pdf": b"%PDF-1.7\n1 0 obj\nendobj\n%%EOF\n",
        ".png": b"\x89PNG\r\n\x1a\nopaque",
        ".jpg": b"\xff\xd8opaque\xff\xd9",
        ".jpeg": b"\xff\xd8opaque\xff\xd9",
        ".webp": b"RIFF\x04\x00\x00\x00WEBP",
        ".mp4": b"\x00\x00\x00\x18ftypisom",
        ".mp3": b"ID3\x04\x00\x00",
        ".txt": "纯文本".encode(),
        ".md": "# Markdown".encode(),
        ".csv": "a,b\n1,2\n".encode(),
        ".json": b'{"ok":true}',
        ".html": b"<!doctype html><html></html>",
    }
    path.write_bytes(payloads[suffix])


class DeliveryOutputFormatContractTests(unittest.TestCase):
    FORMAT_CASES = (
        ("Word文档", ".docx", ".md"),
        ("Excel表格", ".xlsx", ".docx"),
        ("PowerPoint", ".pptx", ".xlsx"),
        ("PDF", ".pdf", ".pptx"),
        ("Markdown", ".md", ".pdf"),
        ("纯文本", ".txt", ".md"),
        ("CSV", ".csv", ".txt"),
        ("JSON", ".json", ".csv"),
        ("HTML", ".html", ".json"),
        ("ZIP压缩包", ".zip", ".html"),
        ("PNG", ".png", ".zip"),
        ("JPG", ".jpg", ".png"),
        ("JPEG", ".jpeg", ".jpg"),
        ("WEBP", ".webp", ".jpeg"),
        ("MP3", ".mp3", ".webp"),
        ("MP4", ".mp4", ".mp3"),
    )

    def test_conversion_contract_uses_requested_output_not_input_format(self) -> None:
        from v3.zongdiaodu import (
            _simple_chain_expected_suffixes,
            _simple_chain_requested_target_paths,
        )

        for product_name, expected_suffix, input_suffix in self.FORMAT_CASES:
            prompt = f"桌面上的输入文件{input_suffix}请转换成{product_name}"
            with self.subTest(product_name=product_name):
                self.assertEqual(
                    _simple_chain_expected_suffixes(prompt),
                    {expected_suffix},
                )
                self.assertEqual(_simple_chain_requested_target_paths(prompt), [])

    def test_each_supported_output_format_validates_its_own_bytes(self) -> None:
        from v3.zongdiaodu import _simple_chain_paths_match_requested_formats

        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            for _, expected_suffix, input_suffix in self.FORMAT_CASES:
                with self.subTest(expected_suffix=expected_suffix):
                    expected = root / f"output{expected_suffix}"
                    wrong = root / f"input{input_suffix}"
                    _write_valid_format(expected, expected_suffix)
                    _write_valid_format(wrong, input_suffix)
                    self.assertTrue(
                        _simple_chain_paths_match_requested_formats(
                            [str(expected)],
                            {expected_suffix},
                        )
                    )
                    self.assertFalse(
                        _simple_chain_paths_match_requested_formats(
                            [str(wrong)],
                            {expected_suffix},
                        )
                    )

    def test_renaming_bytes_to_requested_suffix_does_not_pass(self) -> None:
        from v3.zongdiaodu import _simple_chain_paths_match_requested_formats

        with tempfile.TemporaryDirectory() as temp_root:
            fake = Path(temp_root) / "renamed.docx"
            fake.write_text("this is still plain text", encoding="utf-8")
            self.assertFalse(
                _simple_chain_paths_match_requested_formats(
                    [str(fake)],
                    {".docx"},
                )
            )


if __name__ == "__main__":
    unittest.main()
