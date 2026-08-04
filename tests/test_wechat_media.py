import base64
import io
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from communication_service.wechat_media import (
    WechatMediaDownloader,
    WechatMediaError,
    WechatMediaLimits,
    WechatMediaReference,
    build_wechat_download_url,
    parse_wechat_aes_key,
    validate_wechat_cdn_url,
)


KEY = b"0123456789abcdef"


def encrypt(value: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(value) + padder.finalize()
    cipher = Cipher(algorithms.AES(KEY), modes.ECB()).encryptor()
    return cipher.update(padded) + cipher.finalize()


class _Response:
    def __init__(self, body: bytes, *, status=200, headers=None, max_read=None):
        self.status = status
        self.headers = headers or {"content-length": str(len(body))}
        self._stream = io.BytesIO(body)
        self.max_read = max_read
        self.closed = False
        self.read_sizes = []

    def read(self, amount):
        self.read_sizes.append(amount)
        actual = min(amount, self.max_read) if self.max_read else amount
        return self._stream.read(actual)

    def close(self):
        self.closed = True


class _Transport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, url, *, timeout_seconds):
        self.calls.append((url, timeout_seconds))
        return self.response


class WechatMediaTests(unittest.TestCase):
    def test_url_policy_is_exact_https_allowlist_without_credentials_or_redirect_origin(self):
        url = build_wechat_download_url("a+b/中文")
        self.assertTrue(url.startswith("https://novac2c.cdn.weixin.qq.com/c2c/download?"))
        self.assertIn("%2B", url)
        self.assertEqual(
            validate_wechat_cdn_url(
                url,
                allowed_hosts=("novac2c.cdn.weixin.qq.com",),
            ),
            url,
        )
        for bad in (
            "http://novac2c.cdn.weixin.qq.com/c2c/download",
            "https://127.0.0.1/c2c/download",
            "https://novac2c.cdn.weixin.qq.com.evil.test/c2c/download",
            "https://user@novac2c.cdn.weixin.qq.com/c2c/download",
            "https://novac2c.cdn.weixin.qq.com:444/c2c/download",
        ):
            with self.subTest(url=bad), self.assertRaises(WechatMediaError):
                validate_wechat_cdn_url(
                    bad,
                    allowed_hosts=("novac2c.cdn.weixin.qq.com",),
                )

    def test_aes_key_accepts_protocol_forms_and_rejects_ambiguous_lengths(self):
        self.assertEqual(parse_wechat_aes_key(base64.b64encode(KEY).decode()), KEY)
        self.assertEqual(parse_wechat_aes_key(KEY.hex()), KEY)
        encoded_hex = base64.b64encode(KEY.hex().encode()).decode()
        self.assertEqual(parse_wechat_aes_key(encoded_hex), KEY)
        for bad in ("", "not-base64", base64.b64encode(b"short").decode()):
            with self.subTest(value=bad), self.assertRaises(WechatMediaError):
                parse_wechat_aes_key(bad)

    def test_streamed_ciphertext_is_decrypted_with_separate_size_and_hash_evidence(self):
        plaintext = ("产品化文件" * 5_000).encode("utf-8")
        ciphertext = encrypt(plaintext)
        response = _Response(ciphertext, max_read=7_777)
        transport = _Transport(response)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "stage"
            downloader = WechatMediaDownloader(
                root,
                transport=transport,
                limits=WechatMediaLimits(
                    max_cipher_bytes=len(ciphertext),
                    max_plain_bytes=len(plaintext),
                    chunk_bytes=8_192,
                    timeout_seconds=10,
                ),
            )
            result = downloader.download(
                WechatMediaReference(
                    encrypted_query_param="query-token",
                    aes_key=base64.b64encode(KEY).decode(),
                    declared_cipher_bytes=len(ciphertext),
                    declared_plain_bytes=len(plaintext),
                )
            )
            self.assertEqual(result.plaintext_path.read_bytes(), plaintext)
            self.assertEqual(result.plaintext_size_bytes, len(plaintext))
            self.assertEqual(result.ciphertext_size_bytes, len(ciphertext))
            self.assertTrue(response.closed)
            self.assertGreater(len(response.read_sizes), 2)
            self.assertFalse(any(path.name.endswith(".cipher.part") for path in root.iterdir()))
            result.cleanup()
            self.assertEqual(list(root.iterdir()), [])

    def test_redirect_truncation_declared_mismatch_and_both_size_limits_fail_cleanly(self):
        plaintext = b"hello secure media"
        ciphertext = encrypt(plaintext)
        cases = (
            (_Response(ciphertext, status=302, headers={"location": "https://evil.test"}), None, "wechat.media.redirect.forbidden"),
            (_Response(ciphertext[:-1]), None, "wechat.media.ciphertext.block_alignment_invalid"),
            (_Response(ciphertext), len(ciphertext) + 16, "wechat.media.ciphertext.declared_length_mismatch"),
        )
        for index, (response, declared, code) in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                downloader = WechatMediaDownloader(Path(temporary) / "stage", transport=_Transport(response))
                with self.assertRaises(WechatMediaError) as caught:
                    downloader.download(
                        WechatMediaReference(
                            encrypted_query_param="query",
                            aes_key=KEY.hex(),
                            declared_cipher_bytes=declared,
                        )
                    )
                self.assertEqual(caught.exception.code, code)
                stage = Path(temporary) / "stage"
                if stage.exists():
                    self.assertEqual(list(stage.iterdir()), [])

        with tempfile.TemporaryDirectory() as temporary:
            response = _Response(ciphertext)
            downloader = WechatMediaDownloader(
                Path(temporary) / "stage",
                transport=_Transport(response),
                limits=WechatMediaLimits(
                    max_cipher_bytes=len(ciphertext) - 1,
                    max_plain_bytes=100,
                    chunk_bytes=4_096,
                ),
            )
            with self.assertRaises(WechatMediaError) as caught:
                downloader.download(
                    WechatMediaReference(encrypted_query_param="query", aes_key=KEY.hex())
                )
            self.assertEqual(caught.exception.code, "wechat.media.ciphertext.too_large")

        with tempfile.TemporaryDirectory() as temporary:
            response = _Response(ciphertext)
            downloader = WechatMediaDownloader(
                Path(temporary) / "stage",
                transport=_Transport(response),
                limits=WechatMediaLimits(
                    max_cipher_bytes=len(ciphertext),
                    max_plain_bytes=len(plaintext) - 1,
                    chunk_bytes=4_096,
                ),
            )
            with self.assertRaises(WechatMediaError) as caught:
                downloader.download(
                    WechatMediaReference(encrypted_query_param="query", aes_key=KEY.hex())
                )
            self.assertEqual(caught.exception.code, "wechat.media.plaintext.too_large")


if __name__ == "__main__":
    unittest.main()
