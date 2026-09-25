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
# 1.38 keeps generic output review from prescribing a separate file reader.
# 1.39 keeps available runtime nouns from inventing program-execution duties.
# 1.40 resolves dictionary receipt aliases and fences the ordinary-chat escape.
VERIFICATION_PLANE_VERSION = "1.40"

__all__ = ["VERIFICATION_PLANE_VERSION"]
