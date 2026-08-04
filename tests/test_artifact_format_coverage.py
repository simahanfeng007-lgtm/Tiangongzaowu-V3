"""D-22：产物格式与文件名全覆盖。

- CJK/Unicode 文件名与 validate_safe_filename 同标准，不再被替换为 artifact-N.bin 误拒；
- mp4/gif/webp/mp3/wav 显式类型（magic 校验）；
- other 兜底（mime/扩展不锁定，禁可执行魔数）；
- _mime_and_format 产出的每个 format_id 在 _FORMAT_POLICIES 必有策略（完备性不变量）。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.test_artifact_gate import ArtifactGateTests, minimal_docx
from total_gateway.artifact_gate import (
    _FORMAT_POLICIES,
    ArtifactGateError,
    _validate_binary,
    _validate_gif,
    _validate_mp3,
    _validate_mp4,
    _validate_wav,
    _validate_webp,
)
from total_gateway.frozen_backend_compat import _mime_and_format


class FormatMappingCompletenessTests(unittest.TestCase):
    KNOWN_SUFFIXES = (
        ".docx", ".xlsx", ".pptx", ".pdf", ".zip", ".png", ".jpg", ".jpeg",
        ".gif", ".webp", ".mp4", ".mp3", ".wav", ".json", ".csv", ".bin", ".dat",
        ".txt", ".md", ".py", ".yaml", ".log", ".sql", ".unknownext",
    )

    def test_every_format_id_has_gate_policy(self) -> None:
        for suffix in self.KNOWN_SUFFIXES:
            with self.subTest(suffix=suffix):
                _, format_id = _mime_and_format(Path(f"anything{suffix}"))
                self.assertIn(format_id, _FORMAT_POLICIES)

    def test_media_mappings(self) -> None:
        self.assertEqual(_mime_and_format(Path("a.mp4")), ("video/mp4", "mp4"))
        self.assertEqual(_mime_and_format(Path("a.gif")), ("image/gif", "gif"))
        self.assertEqual(_mime_and_format(Path("a.webp")), ("image/webp", "webp"))
        self.assertEqual(_mime_and_format(Path("a.mp3")), ("audio/mpeg", "mp3"))
        self.assertEqual(_mime_and_format(Path("a.wav")), ("audio/wav", "wav"))


class MediaValidatorTests(unittest.TestCase):
    def test_mp4(self) -> None:
        _validate_mp4(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00" + b"\x00" * 32)
        with self.assertRaisesRegex(ArtifactGateError, "mp4_invalid"):
            _validate_mp4(b"not an mp4 file at all....")

    def test_gif(self) -> None:
        _validate_gif(b"GIF89a" + b"\x00" * 16)
        with self.assertRaisesRegex(ArtifactGateError, "gif_invalid"):
            _validate_gif(b"GIF77x" + b"\x00" * 16)

    def test_webp(self) -> None:
        _validate_webp(b"RIFF\x10\x00\x00\x00WEBP" + b"\x00" * 8)
        with self.assertRaisesRegex(ArtifactGateError, "webp_invalid"):
            _validate_webp(b"RIFF\x10\x00\x00\x00WAVE" + b"\x00" * 8)

    def test_mp3(self) -> None:
        _validate_mp3(b"ID3\x04\x00" + b"\x00" * 16)
        _validate_mp3(b"\xff\xfb" + b"\x00" * 16)
        with self.assertRaisesRegex(ArtifactGateError, "mp3_invalid"):
            _validate_mp3(b"OGGs" + b"\x00" * 16)

    def test_wav(self) -> None:
        _validate_wav(b"RIFF\x10\x00\x00\x00WAVE" + b"\x00" * 8)
        with self.assertRaisesRegex(ArtifactGateError, "wav_invalid"):
            _validate_wav(b"RIFF\x10\x00\x00\x00WEBP" + b"\x00" * 8)

    def test_other_binary_forbids_executables(self) -> None:
        _validate_binary(b"\x89random-bytes" * 4)
        with self.assertRaisesRegex(ArtifactGateError, "executable_forbidden"):
            _validate_binary(b"MZ" + b"\x00" * 64)
        with self.assertRaisesRegex(ArtifactGateError, "executable_forbidden"):
            _validate_binary(b"#!/bin/sh\necho hi\n")


class FormatCoverageGateTests(ArtifactGateTests):
    """gate.accept 层：CJK 名 / 媒体类型 / other 兜底。"""

    def test_cjk_filename_docx_accepted(self) -> None:
        reference = self.put_artifact(minimal_docx())
        self.record_producer((reference.object_id,))
        result = self.gate.accept(
            self.candidate(
                reference,
                format_id="docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                filename="关于母亲.docx",
            )
        )
        self.assertEqual(result.manifest.filename, "关于母亲.docx")

    def test_mp4_media_accepted(self) -> None:
        data = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00" + b"\x00" * 64
        reference = self.put_artifact(data)
        self.record_producer((reference.object_id,))
        result = self.gate.accept(
            self.candidate(reference, format_id="mp4", mime="video/mp4", filename="封面视频.mp4")
        )
        self.assertEqual(result.manifest.format_id, "mp4")
        self.assertEqual(result.manifest.mime, "video/mp4")

    def test_mp4_wrong_magic_rejected(self) -> None:
        reference = self.put_artifact(b"definitely not mp4 bytes")
        self.record_producer((reference.object_id,))
        with self.assertRaisesRegex(ArtifactGateError, "mp4_invalid"):
            self.gate.accept(
                self.candidate(reference, format_id="mp4", mime="video/mp4", filename="x.mp4")
            )

    def test_other_catchall_accepts_unknown_extension(self) -> None:
        reference = self.put_artifact(b"\x89some-novel-format-bytes" * 4)
        self.record_producer((reference.object_id,))
        result = self.gate.accept(
            self.candidate(
                reference,
                format_id="other",
                mime="application/x-novel-thing",
                filename="数据备份.xyz",
            )
        )
        self.assertEqual(result.manifest.mime, "application/x-novel-thing")
        self.assertEqual(result.manifest.artifact_kind, "other")

    def test_other_catchall_rejects_executable(self) -> None:
        reference = self.put_artifact(b"MZ" + b"\x00" * 128)
        self.record_producer((reference.object_id,))
        with self.assertRaisesRegex(ArtifactGateError, "executable_forbidden"):
            self.gate.accept(
                self.candidate(reference, format_id="other", mime="application/x-msdownload", filename="setup.exe")
            )

    def test_typed_format_still_enforces_extension(self) -> None:
        reference = self.put_artifact(minimal_docx())
        self.record_producer((reference.object_id,))
        with self.assertRaisesRegex(ArtifactGateError, "extension.mismatch"):
            self.gate.accept(
                self.candidate(
                    reference,
                    format_id="docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    filename="关于母亲.bin",
                )
            )


if __name__ == "__main__":
    unittest.main()
