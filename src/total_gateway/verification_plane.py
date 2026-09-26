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
# 1.45 gives reasoning judges a bounded 60-second call and reserves closeout time.
# 1.44 adds one protocol correction by the same judge; invalid verdicts never approve.
# 1.43 additionally bounds generated FFmpeg thread pools inside unchanged OS limits.
# 1.42 binds recoverable commits, durable reviewer evidence, v2 completion and pinned model roles.
# 1.46 scales bounded judge reasoning for large evidence and preserves failed-call usage.
VERIFICATION_PLANE_VERSION = "1.46"

__all__ = ["VERIFICATION_PLANE_VERSION"]
