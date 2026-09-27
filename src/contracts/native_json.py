"""Bind native JSON data without widening the signed-contract JSON subset."""
from __future__ import annotations

import math
import hashlib
from .canonical import MAX_SAFE_INTEGER, canonical_json_bytes, canonical_sha256


def native_json_sha256(value):
    """Keep historical hashes; type-tag all data when finite floats occur.

    The tagged form is hash input only, never substituted for tool arguments.
    Signed envelopes still contain integer/string fields and the resulting hash.
    Each node is tagged so user dictionaries cannot impersonate a float marker.
    The standalone Omni verifier implements this wire rule independently.
    """
    try:
        return canonical_sha256(value)
    except TypeError:
        pass

    def tagged(item):
        if item is None: return ["null"]
        if isinstance(item, bool): return ["bool", item]
        if isinstance(item, int):
            if not -MAX_SAFE_INTEGER <= item <= MAX_SAFE_INTEGER:
                raise ValueError("integer is outside the interoperable JSON range")
            return ["int", item]
        if isinstance(item, float):
            if not math.isfinite(item): raise ValueError("non-finite native JSON number")
            return ["float64", item.hex()]
        if isinstance(item, str):
            item.encode("utf-16-be")
            return ["str", item]
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item): raise TypeError("native JSON keys must be strings")
            return ["object", [[key, tagged(item[key])] for key in sorted(item, key=lambda key: key.encode("utf-16-be"))]]
        if isinstance(item, (list, tuple)): return ["array", [tagged(child) for child in item]]
        raise TypeError("unsupported native JSON value")

    # The prefix is outside JSON, so no old float-free JSON value can imitate it.
    return hashlib.sha256(b"tiangong.native-json-float-binding.v1\x00" + canonical_json_bytes(tagged(value))).hexdigest()
