"""Fixed system-owned composition capability profiles.

These constants constrain already registered/signed authority; they never
mint it.  A model proposal cannot select a profile.  Missing profile fields
retain the original A0 contract, including its non-filesystem read actions.
This module uses only the standard library so the Body consumer can enforce
the identical policy without importing Gateway implementation code.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

WORKSPACE_WRITE_PROFILE_ID = "composition.workspace-write.v1"
_WRITE_ACTIONS = {
    "file.write": ("A3", "write"),
    "code.write": ("A3", "write"),
    "code.patch_replace": ("A3", "write"),
    "file.mkdir": ("A2", "write"),
}
WORKSPACE_WRITE_ACTIONS = frozenset(_WRITE_ACTIONS)
WORKSPACE_READ_ACTIONS = frozenset({"file.read", "file.list", "file.hash"})
_PROFILE_DOCUMENT = {
    "id": WORKSPACE_WRITE_PROFILE_ID,
    "read_actions": sorted(WORKSPACE_READ_ACTIONS),
    "write_actions": {key: list(value) for key, value in sorted(_WRITE_ACTIONS.items())},
    "path_policy": "workspace_only",
    "allow_shell": False,
    "allow_python": False,
    "max_utf8_bytes": 1048576,
    "patch": "literal-positive-count-no-noop",
    "code_write": "syntax_check_false",
    "target_snapshot": "native-path-and-content-sha256",
}
WORKSPACE_WRITE_PROFILE_SHA256 = hashlib.sha256(json.dumps(
    _PROFILE_DOCUMENT, ensure_ascii=False, sort_keys=True,
    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
WORKSPACE_PYTHON_PROFILE_ID = "composition.workspace-python.v1"
WORKSPACE_PYTHON_PROFILE_SHA256 = hashlib.sha256(json.dumps({
    "id": WORKSPACE_PYTHON_PROFILE_ID, "write_profile_sha256": WORKSPACE_WRITE_PROFILE_SHA256,
    "execution_action": "python.run", "target": "existing-workspace-py-script",
    "argv": "bounded-string-list", "allow_inline_code": False,
    "containment": "windows-appcontainer", "require_os_containment": True,
    "network": "denied", "environment": "allowlist-no-parent-secrets",
    "timeout_seconds": 60, "max_output_bytes": 4194304, "max_changed_bytes": 4194304,
    "memory_limit_bytes": 2147483648, "process_limit": 32,
    "commit": "success-only-workspace-create-update-no-delete",
}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def composition_profile_valid(profile_id=None, profile_sha256=None) -> bool:
    return (profile_id, profile_sha256) in {
        (None, None), (WORKSPACE_WRITE_PROFILE_ID, WORKSPACE_WRITE_PROFILE_SHA256),
        (WORKSPACE_PYTHON_PROFILE_ID, WORKSPACE_PYTHON_PROFILE_SHA256)}


def composition_profile_risk_ceiling(profile_id=None, profile_sha256=None) -> str:
    if not composition_profile_valid(profile_id, profile_sha256):
        raise ValueError("unknown composition profile")
    return "A0" if profile_id is None else ("A4" if profile_id == WORKSPACE_PYTHON_PROFILE_ID else "A3")


def composition_profile_fields(value) -> dict[str, Any]:
    """Extract only sealed contract fields, never infer them from arguments."""
    get = value.get if isinstance(value, Mapping) else lambda key: getattr(value, key, None)
    return {"profile_id": get("execution_profile_id"),
            "profile_sha256": get("execution_profile_sha256")}


def composition_authority_allowed(*, action_id, risk_class, allowed_side_effects,
                                  allow_shell=False, allow_python=False,
                                  profile_id=None, profile_sha256=None) -> bool:
    if (not composition_profile_valid(profile_id, profile_sha256)
            or allow_shell
            or not isinstance(allowed_side_effects, (tuple, list, set, frozenset))):
        return False
    effects = set(allowed_side_effects)
    if profile_id == WORKSPACE_PYTHON_PROFILE_ID and action_id == "python.run":
        return risk_class == "A4" and allow_python is True and effects == {"local_write", "read"}
    if allow_python:
        return False
    if profile_id is None:
        return risk_class == "A0" and effects.issubset({"none", "read"})
    if action_id in WORKSPACE_READ_ACTIONS:
        return risk_class == "A0" and effects == {"read"}
    expected = _WRITE_ACTIONS.get(action_id)
    return bool(expected and risk_class == expected[0] and effects == {"local_write", "read"})


def composition_permission_allowed(permission, *, profile_id=None, profile_sha256=None) -> bool:
    if (not permission.has_valid_sha256() or permission.requires_confirmation
            or permission.registry_risk != permission.effective_risk
            or not composition_authority_allowed(
                action_id=permission.action_id, risk_class=permission.effective_risk,
                allowed_side_effects=permission.allowed_side_effects,
                allow_shell=permission.allow_shell, allow_python=permission.allow_python,
                profile_id=profile_id, profile_sha256=profile_sha256)):
        return False
    if profile_id is None:
        return permission.effect in {"read", "verify"}
    expected_effect = "read" if permission.action_id in WORKSPACE_READ_ACTIONS else "write"
    return (permission.effect == expected_effect and permission.path_policy == "workspace_only"
            and permission.allow_absolute_paths)


def validate_composition_arguments(action_id, arguments, *, profile_id=None, profile_sha256=None) -> None:
    """Additional fixed-profile restrictions after the actual action schema."""
    if not composition_profile_valid(profile_id, profile_sha256):
        raise ValueError("unknown composition execution profile")
    if profile_id is None or action_id in WORKSPACE_READ_ACTIONS:
        return
    if profile_id == WORKSPACE_PYTHON_PROFILE_ID and action_id == "python.run":
        if (not isinstance(arguments, Mapping) or set(arguments) - {"argv", "timeout"}
                or type(arguments.get("timeout", 30)) is not int
                or not 1 <= arguments.get("timeout", 30) <= 60
                or type(arguments.get("argv", [])) is not list
                or len(arguments.get("argv", [])) > 64
                or any(type(item) is not str or len(item.encode("utf-8")) > 4096 or "\x00" in item
                       for item in arguments.get("argv", []))):
            raise ValueError("controlled Python requires bounded argv and timeout; inline code is not allowed")
        return
    if action_id not in WORKSPACE_WRITE_ACTIONS or not isinstance(arguments, Mapping):
        raise ValueError("action is outside the composition execution profile")
    allowed = {
        "file.write": {"content", "encoding", "binary"},
        "code.write": {"content", "encoding", "binary", "syntax_check", "language"},
        "code.patch_replace": {"find", "replace", "count", "regex", "allow_noop", "encoding"},
        "file.mkdir": {"exist_ok"},
    }[action_id]
    if set(arguments) - allowed:
        raise ValueError("unsupported controlled write argument")
    if action_id in {"file.write", "code.write"}:
        content = arguments.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > 1048576:
            raise ValueError("controlled write requires bounded UTF-8 text")
        if arguments.get("binary", False) is not False:
            raise ValueError("controlled write does not allow binary encoding")
    if arguments.get("encoding", "utf-8").lower().replace("_", "-") not in {"utf-8", "utf8"}:
        raise ValueError("controlled write requires UTF-8")
    if action_id == "code.write" and arguments.get("syntax_check") is not False:
        raise ValueError("controlled code.write requires syntax_check=false; execution is separate")
    if action_id == "code.patch_replace":
        if (not isinstance(arguments.get("find"), str) or not arguments["find"]
                or not isinstance(arguments.get("replace", ""), str)
                or type(arguments.get("count")) is not int or not 1 <= arguments["count"] <= 10000
                or arguments.get("regex", False) is not False
                or arguments.get("allow_noop", False) is not False):
            raise ValueError("controlled patch requires a bounded literal replacement")
        if sum(len(arguments.get(key, "").encode("utf-8")) for key in ("find", "replace")) > 1048576:
            raise ValueError("controlled patch content is too large")


def composition_completion_scope(steps) -> str:
    """Describe only terminal actions of a validated plan, never user-goal success."""
    if any(not composition_permission_allowed(step.permission, **composition_profile_fields(step)) for step in steps):
        raise ValueError("composition completion profile is invalid")
    if any(step.action_id == "python.run" for step in steps):
        return "workspace_python_terminal_execution"
    return ("workspace_file_terminal_execution" if any(
        step.action_id in WORKSPACE_WRITE_ACTIONS for step in steps)
        else "read_only_terminal_execution")
