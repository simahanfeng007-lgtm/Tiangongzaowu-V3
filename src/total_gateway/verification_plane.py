"""Tiangong Verification Plane — the SINGLE machine-readable version
source (M6 §25).

P19-R2 M6 froze the Verification Plane at 1.0. Do not hardcode this
value anywhere else: import it from here. Changing it requires an
explicit Verification Plane version bump and a refresh of
docs/p19-r2/m6/VERIFICATION_PLANE_FREEZE.json (the freeze guard test
fails with VERIFICATION_PLANE_FREEZE_CHANGED otherwise).
"""

from __future__ import annotations

VERIFICATION_PLANE_VERSION = "1.1"

__all__ = ["VERIFICATION_PLANE_VERSION"]
