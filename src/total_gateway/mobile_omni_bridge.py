"""Bind Mobile Body actions into the existing Omni BodyRuntime.

This is intentionally an in-process adapter.  It does not create another
Runtime, agent loop, planner, or tool authority.  Gateway capability grants are
verified by the existing Omni wrapper before BodyRuntime reaches this bridge.
"""
from __future__ import annotations

import sys
import threading
from typing import Any, Mapping

try:
    from .mobile_capabilities import MOBILE_CAPABILITY_DEFINITIONS, omni_action_entries
except ImportError:
    from mobile_capabilities import MOBILE_CAPABILITY_DEFINITIONS, omni_action_entries

_INSTALL_LOCK = threading.RLock()

_MOBILE_SCHEMAS: dict[str, dict[str, Any]] = {
    "mobile.observe_ui": {"target": "empty; reads the paired Android active window", "args": {}},
    "mobile.notification_list": {"target": "empty; reads notifications from the paired Android listener", "args": {}},
    "mobile.screenshot": {"target": "empty; captures the paired Android screen", "args": {}},
    "mobile.tap": {
        "target": "empty",
        "args": {"x": "screen x coordinate (required)", "y": "screen y coordinate (required)"},
        "required": ["args.x", "args.y"],
    },
    "mobile.tap_node": {
        "target": "empty",
        "args": {
            "path": "optional UI-tree path from mobile.observe_ui",
            "view_id": "optional Android view resource id",
            "text": "optional exact visible text",
            "description": "optional exact accessibility description",
        },
        "any_of": ["args.path", "args.view_id", "args.text", "args.description"],
    },
    "mobile.swipe": {
        "target": "empty",
        "args": {
            "x1": "start x (required)", "y1": "start y (required)",
            "x2": "end x (required)", "y2": "end y (required)",
            "duration_ms": "optional 80-2000 milliseconds",
        },
        "required": ["args.x1", "args.y1", "args.x2", "args.y2"],
    },
    "mobile.input_text": {
        "target": "empty; writes to the focused or first normal editable node",
        "args": {"text": "text to enter, max 10000 characters (required)"},
        "required": ["args.text"],
    },
    "mobile.back": {"target": "empty", "args": {}},
    "mobile.home": {"target": "empty", "args": {}},
    "mobile.open_app": {
        "target": "empty",
        "args": {"package": "installed Android package name such as com.android.settings (required)"},
        "required": ["args.package"],
    },
}


def _load_omni_runtime(runtime: object) -> tuple[Any, Any]:
    backend = getattr(runtime, "backend_service", None)
    loader = getattr(backend, "_load_omni_body_module", None)
    if not callable(loader):
        raise RuntimeError("mobile_omni_bridge.backend_loader_unavailable")
    wrapper = loader()
    importer = getattr(wrapper, "_import_runtime", None)
    if not callable(importer):
        raise RuntimeError("mobile_omni_bridge.runtime_importer_unavailable")
    body_runtime, _config, error = importer()
    if body_runtime is None:
        raise RuntimeError(str(error or "mobile_omni_bridge.body_runtime_unavailable"))
    module = sys.modules.get(str(getattr(body_runtime, "__module__", "")))
    if module is None:
        raise RuntimeError("mobile_omni_bridge.body_module_unavailable")
    return body_runtime, module


def install_mobile_omni_bridge(runtime: object, broker: object) -> None:
    """Install the mobile executor into the already-loaded Omni BodyRuntime."""
    with _INSTALL_LOCK:
        body_runtime, module = _load_omni_runtime(runtime)
        actions = getattr(module, "ACTIONS", None)
        if not isinstance(actions, dict):
            raise RuntimeError("mobile_omni_bridge.actions_unavailable")
        actions.update(omni_action_entries())

        schema_fn = getattr(module, "schema_for_action", None)
        contracts_module = sys.modules.get(str(getattr(schema_fn, "__module__", "")))
        schema_table = getattr(contracts_module, "ACTION_ARGUMENT_SCHEMAS", None)
        if isinstance(schema_table, dict):
            schema_table.update(_MOBILE_SCHEMAS)

        existing_broker = getattr(body_runtime, "__tiangong_mobile_broker__", None)
        if existing_broker is broker:
            return
        original = getattr(body_runtime, "__tiangong_mobile_original_run_unlocked__", None)
        if original is None:
            original = getattr(body_runtime, "_run_unlocked", None)
            if not callable(original):
                raise RuntimeError("mobile_omni_bridge.run_unlocked_unavailable")
            setattr(body_runtime, "__tiangong_mobile_original_run_unlocked__", original)

        mobile_ids = frozenset(MOBILE_CAPABILITY_DEFINITIONS)

        def _mobile_run_unlocked(
            self: object,
            action: str,
            target: str | None = None,
            args: dict[str, Any] | None = None,
            *,
            _op_id: str | None = None,
            **kwargs: Any,
        ) -> dict[str, Any]:
            if action not in mobile_ids:
                return original(self, action, target, args, _op_id=_op_id, **kwargs)
            payload = dict(args or {})
            timeout_raw = payload.pop("_mobile_timeout_ms", 30_000)
            try:
                timeout_ms = max(1_000, min(int(timeout_raw), 60_000))
            except (TypeError, ValueError):
                timeout_ms = 30_000
            enqueue = getattr(broker, "enqueue", None)
            if not callable(enqueue):
                return {
                    "ok": False,
                    "success": False,
                    "error_type": "MOBILE_BODY_UNAVAILABLE",
                    "message": "paired Android body broker is unavailable",
                    "action": action,
                }
            try:
                result = enqueue(action, payload, timeout_ms=timeout_ms)
            except Exception as exc:
                return {
                    "ok": False,
                    "success": False,
                    "error_type": "MOBILE_BODY_ERROR",
                    "message": str(exc)[:300],
                    "action": action,
                }
            if not isinstance(result, Mapping):
                return {
                    "ok": False,
                    "success": False,
                    "error_type": "MOBILE_BODY_PROTOCOL_ERROR",
                    "message": "mobile body returned a non-object result",
                    "action": action,
                }
            output = dict(result)
            output["action"] = action
            output["success"] = bool(output.get("ok"))
            if not output["success"]:
                output["error_type"] = "MOBILE_BODY_ACTION_FAILED"
                output["message"] = str(output.get("error") or "Android action failed")[:300]
            output.setdefault("evidence", {
                "mobile_task_id": str(output.get("task_id") or ""),
                "mobile_device_id": str(output.get("device_id") or ""),
                "mobile_action": action,
            })
            return output

        setattr(body_runtime, "_run_unlocked", _mobile_run_unlocked)
        setattr(body_runtime, "__tiangong_mobile_broker__", broker)


__all__ = ["install_mobile_omni_bridge"]
