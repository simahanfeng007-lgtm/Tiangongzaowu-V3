from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from total_gateway.desktop_attachment_ingress import (
    DesktopAttachmentIngress,
    DesktopAttachmentIngressError,
    encode_desktop_attachment_metadata,
)
from total_gateway.object_store import ContentAddressedObjectStore


class DesktopAttachmentSizeOnlyTests(unittest.TestCase):
    def _accept(
        self,
        ingress: DesktopAttachmentIngress,
        *,
        filename: str,
        body: bytes,
        mime: str = "application/octet-stream",
    ):
        metadata = encode_desktop_attachment_metadata(
            {
                "content_sha256": hashlib.sha256(body).hexdigest(),
                "created_at_ms": 1,
                "filename": filename,
                "mime": mime,
                "session_id": "session-size-only",
                "size_bytes": len(body),
            }
        )
        return ingress.accept(metadata, io.BytesIO(body), content_length=len(body))

    def test_unknown_extensions_and_contents_are_accepted_as_opaque_bytes(self) -> None:
        cases = (
            ("母亲的灯.自定义格式", b"\x00\xffnot-a-known-format", "application/x-custom"),
            ("program.exe", b"MZ\x00\x01opaque", "application/x-msdownload"),
            ("README", b"extensionless content", "application/octet-stream"),
            ("fake.pdf", b"this is deliberately not a PDF", "application/pdf"),
        )
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            store = ContentAddressedObjectStore.open(root / "objects", now_ms=1)
            try:
                ingress = DesktopAttachmentIngress(
                    store,
                    root / "staging",
                    max_attachment_bytes=1024,
                )
                for filename, body, mime in cases:
                    with self.subTest(filename=filename):
                        reference = self._accept(
                            ingress,
                            filename=filename,
                            body=body,
                            mime=mime,
                        )
                        self.assertEqual(reference.filename, filename)
                        self.assertEqual(reference.size_bytes, len(body))
                        self.assertEqual(reference.sha256, hashlib.sha256(body).hexdigest())
                        self.assertEqual(reference.mime, mime)
            finally:
                store.close()

    def test_declared_mime_is_only_a_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            store = ContentAddressedObjectStore.open(root / "objects", now_ms=1)
            try:
                ingress = DesktopAttachmentIngress(store, root / "staging")
                reference = self._accept(
                    ingress,
                    filename="任意.bin",
                    body=b"opaque",
                    mime="not a mime",
                )
                self.assertEqual(reference.mime, "application/octet-stream")
            finally:
                store.close()

    def test_size_limit_remains_the_admission_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            store = ContentAddressedObjectStore.open(root / "objects", now_ms=1)
            try:
                ingress = DesktopAttachmentIngress(
                    store,
                    root / "staging",
                    max_attachment_bytes=4,
                )
                with self.assertRaisesRegex(
                    DesktopAttachmentIngressError,
                    "desktop_attachment.metadata.values_invalid",
                ):
                    self._accept(ingress, filename="too-large.anything", body=b"12345")
            finally:
                store.close()

    def test_file_picker_and_mime_fallback_have_no_extension_allowlist(self) -> None:
        main = (
            Path(__file__).resolve().parents[1] / "app" / "main.js"
        ).read_text(encoding="utf-8")
        picker_start = main.index("function chatFileFilters()")
        picker_end = main.index("function chatAttachmentMime", picker_start)
        picker = main[picker_start:picker_end]
        mime_end = main.index("function dataUrlForFile", picker_end)
        mime_helper = main[picker_end:mime_end]
        self.assertIn('{ name: "All Files", extensions: ["*"] }', picker)
        self.assertNotIn("CHAT_SAFE_EXTENSIONS", picker)
        self.assertNotIn("extension_forbidden", mime_helper)
        self.assertIn('return "application/octet-stream";', mime_helper)


if __name__ == "__main__":
    unittest.main()
