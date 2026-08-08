"""Mobile Body capability overlay for the authoritative Omni registry.

The overlay is compiled only when TIANGONG_MOBILE_LINK=1.  It therefore does
not advertise phone actions on installations where the mobile transport is
disabled.  Read-only sensing stays A0; actions that can affect another app are
A4 external execution and still pass through the normal Gateway grant policy.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

MOBILE_CAPABILITY_DEFINITIONS: dict[str, dict[str, Any]] = {
    "mobile.observe_ui": {
        "risk": "A0",
        "effect": "read",
        "summary": "Read the paired Android device's current accessibility UI tree; password nodes are redacted.",
    },
    "mobile.notification_list": {
        "risk": "A0",
        "effect": "read",
        "summary": "Read display text from notifications exposed by the paired Android notification listener.",
    },
    "mobile.screenshot": {
        "risk": "A0",
        "effect": "read",
        "summary": "Capture the paired Android device's current screen through the user-enabled accessibility service.",
    },
    "mobile.tap": {
        "risk": "A4",
        "effect": "execute",
        "summary": "Tap coordinates on the paired Android device; permission-controller and installer surfaces are blocked on-device.",
    },
    "mobile.tap_node": {
        "risk": "A4",
        "effect": "execute",
        "summary": "Click an accessibility node on the paired Android device; password and privilege-grant surfaces are blocked on-device.",
    },
    "mobile.swipe": {
        "risk": "A4",
        "effect": "execute",
        "summary": "Perform a bounded swipe gesture on the paired Android device.",
    },
    "mobile.input_text": {
        "risk": "A4",
        "effect": "execute",
        "summary": "Enter text into a normal editable Android accessibility node; password fields are blocked.",
    },
    "mobile.back": {
        "risk": "A4",
        "effect": "execute",
        "summary": "Invoke Android Back on the paired device.",
    },
    "mobile.home": {
        "risk": "A4",
        "effect": "execute",
        "summary": "Invoke Android Home on the paired device.",
    },
    "mobile.open_app": {
        "risk": "A4",
        "effect": "execute",
        "summary": "Launch an already-installed Android application by package name; privilege-grant packages are blocked.",
    },
}


def capability_manifest_entries() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for action_id, meta in MOBILE_CAPABILITY_DEFINITIONS.items():
        result[action_id] = {
            "alias_to": "",
            "declared_status": "core_executable",
            "effect": str(meta["effect"]),
            "executable": True,
            "handler": "mobile_body",
            "id": action_id,
            "reason": "",
            "risk": str(meta["risk"]),
            "summary": str(meta["summary"]),
        }
    return result


def omni_action_entries() -> dict[str, dict[str, Any]]:
    return {
        action_id: {
            "risk": str(meta["risk"]),
            "implemented": True,
            "effect": str(meta["effect"]),
            "summary": str(meta["summary"]),
        }
        for action_id, meta in MOBILE_CAPABILITY_DEFINITIONS.items()
    }


def augment_capability_manifest(manifest: Mapping[str, Any], *, source_hash: str) -> dict[str, Any]:
    output = deepcopy(dict(manifest))
    capabilities = output.get("capabilities")
    if not isinstance(capabilities, dict):
        raise ValueError("mobile capability overlay requires manifest capabilities")
    additions = capability_manifest_entries()
    collisions = sorted(set(capabilities) & set(additions))
    if collisions:
        raise ValueError(f"mobile capability collision: {','.join(collisions)}")
    capabilities.update(additions)
    output["total"] = len(capabilities)
    output["executable"] = sum(
        1 for value in capabilities.values()
        if isinstance(value, Mapping) and value.get("executable") is True
    )
    output["source_hash"] = source_hash
    validation = output.get("validation")
    if not isinstance(validation, dict) or validation.get("ok") is not True:
        raise ValueError("mobile capability overlay requires a healthy base manifest")
    if validation.get("executable_without_route") not in ([], None):
        raise ValueError("mobile capability overlay refuses unhealthy base routes")
    validation["executable_without_route"] = []
    return output


__all__ = [
    "MOBILE_CAPABILITY_DEFINITIONS",
    "augment_capability_manifest",
    "capability_manifest_entries",
    "omni_action_entries",
]
