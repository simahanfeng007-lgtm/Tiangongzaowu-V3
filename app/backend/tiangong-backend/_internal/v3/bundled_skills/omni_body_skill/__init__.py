"""Canonical Tiangong Omni Body package with side-effect-free submodules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .tools import BodyRuntime, BodyRuntimeConfig

__all__ = ["BodyRuntime", "BodyRuntimeConfig"]


def __getattr__(name: str) -> Any:
    """Preserve the public runtime exports without eager runtime imports.

    Gateway schema validation imports ``omni_body_skill.tool_contracts`` and
    must not construct or import the Body runtime as a side effect.  PEP 562
    keeps the historical package-level exports lazy for callers that actually
    request them.
    """

    if name in __all__:
        from .tools import BodyRuntime, BodyRuntimeConfig

        return {
            "BodyRuntime": BodyRuntime,
            "BodyRuntimeConfig": BodyRuntimeConfig,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
