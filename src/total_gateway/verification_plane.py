"""Tiangong Verification Plane — the SINGLE machine-readable version
source (M6 §25).

P19-R2 M6 froze the Verification Plane at 1.0. Do not hardcode this
value anywhere else: import it from here. Changing it requires an
explicit Verification Plane version bump and a refresh of
docs/p19-r2/m6/VERIFICATION_PLANE_FREEZE.json (the freeze guard test
fails with VERIFICATION_PLANE_FREEZE_CHANGED otherwise).
"""

from __future__ import annotations

# 1.33 preserves task-generated composition fencing and records the reviewed
# experience-memory, native world-observation and inquiry identity boundaries.
# 1.34 versions mutable execution receipts and ordered composition feedback.
# 1.36 removes prose-derived task gates; structured evidence and authority remain.
# 1.40 adds Linux workspace OS containment and corrects image/archive execution receipts.
# 1.41 binds recoverable commits, durable reviewer evidence, v2 completion and pinned model roles.
VERIFICATION_PLANE_VERSION = "1.41"

__all__ = ["VERIFICATION_PLANE_VERSION"]
