"""System-owned, bounded admission time for an already compiled plan.

This is a deadline computed once before registration, not a renewable lease.
The short per-step ticket/grant limits and all live-generation checks remain
independent. Model arguments cannot request more time. Each controlled Python
step is budgeted at the profile's full 60-second ceiling plus host overhead.
"""
from __future__ import annotations

from contracts.composition_profile import (
    WORKSPACE_PYTHON_PROFILE_ID,
    WORKSPACE_READ_ACTIONS,
    WORKSPACE_WRITE_ACTIONS,
    composition_profile_valid,
)

LEGACY_ADMISSION_LIFETIME_MS = 60_000
PROFILE_ADMISSION_MAX_LIFETIME_MS = 15 * 60_000
_STEP_OVERHEAD_MS = 30_000
_PYTHON_RUNTIME_MS = 60_000


def composition_admission_lifetime_ms(
    action_ids, *, execution_profile_id=None, execution_profile_sha256=None,
) -> int:
    """Return the maximum sealed window for validated plan action identities."""
    if not composition_profile_valid(execution_profile_id, execution_profile_sha256):
        raise ValueError("composition admission profile is invalid")
    if execution_profile_id is None:
        return LEGACY_ADMISSION_LIFETIME_MS
    actions = tuple(action_ids)
    allowed = WORKSPACE_READ_ACTIONS | WORKSPACE_WRITE_ACTIONS
    if execution_profile_id == WORKSPACE_PYTHON_PROFILE_ID:
        allowed = allowed | {"python.run"}
    if not 1 <= len(actions) <= 128 or any(action not in allowed for action in actions):
        raise ValueError("composition admission action set is invalid")
    duration = (LEGACY_ADMISSION_LIFETIME_MS + len(actions) * _STEP_OVERHEAD_MS
                + actions.count("python.run") * _PYTHON_RUNTIME_MS)
    if duration > PROFILE_ADMISSION_MAX_LIFETIME_MS:
        raise ValueError("composition admission execution budget exceeds ceiling")
    return duration
