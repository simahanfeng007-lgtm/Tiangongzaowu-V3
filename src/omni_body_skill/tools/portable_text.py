"""Cross-platform text decoding and newline normalization.

The runtime never silently replaces undecodable bytes.  It accepts canonical
UTF-8 first, explicit Unicode BOM formats second, and a small allowlist of
legacy Windows encodings only for subprocess output or imported user files.
Every non-UTF-8 decode is reported to the caller so the result can be audited.
"""
from __future__ import annotations

from dataclasses import dataclass
import codecs
import locale
import os
from typing import Iterable


class PortableTextError(UnicodeError):
    pass


@dataclass(frozen=True, slots=True)
class DecodedText:
    text: str
    encoding: str
    had_bom: bool = False
    legacy_fallback: bool = False


_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def _candidate_legacy_encodings(extra: Iterable[str] = ()) -> tuple[str, ...]:
    candidates: list[str] = []
    preferred = str(locale.getpreferredencoding(False) or "").strip()
    for item in (*extra, preferred, "gb18030", "cp1252"):
        name = str(item or "").strip().lower().replace("_", "-")
        if not name or name in {"utf-8", "utf8", "ascii"} or name in candidates:
            continue
        try:
            codecs.lookup(name)
        except LookupError:
            continue
        candidates.append(name)
    return tuple(candidates)


def decode_portable_bytes(
    data: bytes,
    *,
    source: str = "text",
    allow_legacy_windows: bool = False,
    legacy_encodings: Iterable[str] = (),
) -> DecodedText:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("portable text input must be bytes")
    raw = bytes(data)
    if b"\x00" in raw and not any(raw.startswith(bom) for bom, _ in _BOMS):
        raise PortableTextError(f"{source}: NUL bytes require an explicit Unicode BOM")
    for bom, encoding in _BOMS:
        if not raw.startswith(bom):
            continue
        try:
            text = raw.decode(encoding, errors="strict")
        except UnicodeDecodeError as exc:
            raise PortableTextError(f"{source}: invalid {encoding} data") from exc
        if text.startswith("\ufeff"):
            text = text[1:]
        return DecodedText(text, encoding, had_bom=True)
    try:
        return DecodedText(raw.decode("utf-8", errors="strict"), "utf-8")
    except UnicodeDecodeError as utf8_error:
        if allow_legacy_windows:
            for encoding in _candidate_legacy_encodings(legacy_encodings):
                try:
                    text = raw.decode(encoding, errors="strict")
                except (UnicodeDecodeError, LookupError):
                    continue
                # A legacy fallback must round-trip exactly.  This prevents a
                # permissive single-byte codec from hiding corrupted bytes.
                try:
                    if text.encode(encoding, errors="strict") != raw:
                        continue
                except UnicodeEncodeError:
                    continue
                return DecodedText(text, encoding, legacy_fallback=True)
        raise PortableTextError(f"{source}: bytes are not valid UTF-8 or an allowed Windows encoding") from utf8_error


def normalize_newlines(text: str, *, newline: str = "\n") -> str:
    if newline not in {"\n", "\r\n"}:
        raise ValueError("newline must be LF or CRLF")
    return str(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)


def subprocess_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return a deterministic UTF-8 child environment on Windows and POSIX."""
    env = dict(os.environ if base is None else base)
    # Force Python child processes to emit canonical UTF-8 even when the
    # parent shell was started under a legacy Windows code page.  Locale
    # variables remain defaults because some minimal POSIX images do not ship
    # C.UTF-8, while PYTHONUTF8/PYTHONIOENCODING are platform independent.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("LC_ALL", "C.UTF-8")
    env.setdefault("LANG", "C.UTF-8")
    return env
