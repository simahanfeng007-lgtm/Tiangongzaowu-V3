"""Single authoritative Windows CurrentUser DPAPI implementation."""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from typing import Protocol


class DataProtector(Protocol):
    def protect(self, plaintext: bytes | bytearray, entropy: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes, entropy: bytes) -> bytearray: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob_from_bytes(data: bytes | bytearray) -> tuple[_DataBlob, object]:
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(bytes(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


class WindowsDpapiProtector:
    """CurrentUser DPAPI with UI disabled and mandatory additional entropy."""

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows DPAPI is unavailable on this platform")
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def protect(self, plaintext: bytes | bytearray, entropy: bytes) -> bytes:
        return self._call("CryptProtectData", plaintext, entropy)

    def unprotect(self, ciphertext: bytes, entropy: bytes) -> bytearray:
        return bytearray(self._call("CryptUnprotectData", ciphertext, entropy))

    def _call(self, function_name: str, data: bytes | bytearray, entropy: bytes) -> bytes:
        input_blob, input_buffer = _blob_from_bytes(data)
        entropy_blob, entropy_buffer = _blob_from_bytes(entropy)
        output_blob = _DataBlob()
        description = wintypes.LPWSTR()
        function = getattr(self._crypt32, function_name)
        try:
            if function_name == "CryptProtectData":
                ok = function(
                    ctypes.byref(input_blob),
                    "Tiangong v3 protected runtime data",
                    ctypes.byref(entropy_blob),
                    None,
                    None,
                    self._CRYPTPROTECT_UI_FORBIDDEN,
                    ctypes.byref(output_blob),
                )
            else:
                ok = function(
                    ctypes.byref(input_blob),
                    ctypes.byref(description),
                    ctypes.byref(entropy_blob),
                    None,
                    None,
                    self._CRYPTPROTECT_UI_FORBIDDEN,
                    ctypes.byref(output_blob),
                )
            _ = (input_buffer, entropy_buffer)
            if not ok:
                raise OSError(ctypes.get_last_error(), f"{function_name} failed")
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            if output_blob.pbData:
                self._kernel32.LocalFree(output_blob.pbData)
            if description:
                self._kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))


class EphemeralTestProtector:
    """In-memory AEAD protector used only by explicit test runtimes.

    Production and development continue to require Windows CurrentUser DPAPI.
    The random key is never persisted, so this class cannot silently become a
    durable credential store.
    """

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self._cipher = AESGCM(os.urandom(32))

    def protect(self, plaintext: bytes | bytearray, entropy: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._cipher.encrypt(nonce, bytes(plaintext), bytes(entropy))

    def unprotect(self, ciphertext: bytes, entropy: bytes) -> bytearray:
        if len(ciphertext) < 29:
            raise ValueError("test ciphertext is truncated")
        return bytearray(self._cipher.decrypt(ciphertext[:12], ciphertext[12:], bytes(entropy)))


_EPHEMERAL_SCOPE_LOCK = threading.Lock()
_EPHEMERAL_SCOPE_PROTECTORS: dict[str, EphemeralTestProtector] = {}


def ephemeral_test_protector_for_scope(scope: str) -> EphemeralTestProtector:
    """Return one process-local test protector for an explicit state scope.

    The key is still never persisted and therefore cannot become a durable
    credential store.  Reusing it only inside the current Python process makes
    orderly stop/start lifecycle tests representative without weakening the
    Windows DPAPI production path.
    """

    clean = str(scope or "").strip()
    if not clean:
        raise ValueError("test protector scope is required")
    with _EPHEMERAL_SCOPE_LOCK:
        protector = _EPHEMERAL_SCOPE_PROTECTORS.get(clean)
        if protector is None:
            protector = EphemeralTestProtector()
            _EPHEMERAL_SCOPE_PROTECTORS[clean] = protector
        return protector


__all__ = [
    "DataProtector",
    "EphemeralTestProtector",
    "WindowsDpapiProtector",
    "ephemeral_test_protector_for_scope",
]
