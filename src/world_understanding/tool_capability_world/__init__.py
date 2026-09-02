"""Deterministic Tool Capability World projection.

This package is read-only and non-authorizing. It projects the existing
Capability Manifest / Action Registry authority into capability semantics for
World Understanding. It is not a registry, runtime, planner, or permission
source.
"""

from .compiler import (
    ToolCapabilityRelationV1,
    ToolCapabilityWorldError,
    ToolCapabilityWorldSnapshotV1,
    compile_tool_capability_world,
)

__all__ = [
    "ToolCapabilityRelationV1",
    "ToolCapabilityWorldError",
    "ToolCapabilityWorldSnapshotV1",
    "compile_tool_capability_world",
]
