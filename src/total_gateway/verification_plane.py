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
# 1.47 repairs unexecuted malformed turns at most twice using the current tool schema.
# 1.48 supplies bounded page requests and explicit range feedback to the judge.
# 1.49 flushes workspace commit bytes through writable Windows handles.
# 1.50 isolates Fact runtime state from tool snapshots and media-only dependencies.
# 1.51 restores scoped system-library selectors inside Linux containment.
# 1.52 retires twelve metadata-only assessments from the executable dictionary.
# 1.53 binds real browser observations, scoped MCP connections and mandatory adversarial completion.
# 1.54 removes residual rubric authority and hidden execution from content observations.
# 1.55 preserves typed unavailability and explicit document observation failures/ranges.
# 1.56 makes novel content judgments advisory and validates recovery before writes.
# 1.57 aligns public novel preflight with the v2 observation/repair contract.
# 1.58 preserves Windows transaction lock bytes while maintaining process exclusion.
# 1.63 binds finite native numeric arguments without widening signed contracts.
# 1.64 recovers one truncated judge turn under the same authority and deadline.
# 1.65 carries the bounded recovery budget through the actual HTTP call scope.
# 1.67 binds explicit application ownership metadata in the action authority.
# 1.68 unifies connection probes with configured credentials and execution transport.
VERIFICATION_PLANE_VERSION = "1.68"

__all__ = ["VERIFICATION_PLANE_VERSION"]
