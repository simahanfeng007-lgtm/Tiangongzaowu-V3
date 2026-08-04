"""Omni Body App Adapter interface.

This module defines the tool-facing adapter contract only. It is not an agent
planner and does not decide tasks. Each adapter exposes deterministic actions
that omni_body can route to after the adapter is implemented by the host.
"""
from __future__ import annotations
from typing import Any, Protocol

class AppAdapter(Protocol):
    app_id: str
    adapter_name: str

    def health(self) -> dict[str, Any]: ...
    def describe_actions(self) -> dict[str, Any]: ...
    def execute(self, action: str, target: str | None, args: dict[str, Any]) -> dict[str, Any]: ...
    def verify(self, action: str, result: dict[str, Any], expected: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def rollback(self, op_id: str) -> dict[str, Any]: ...

def adapter_required(adapter_name: str, action: str, target: str | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "implemented": False,
        "requires_adapter": adapter_name,
        "action": action,
        "target": target,
        "message": "This action is mounted in Omni Body App Bus but requires a host adapter backend.",
        "evidence": {"adapter": adapter_name, "action": action, "available": False},
    }
