"""Bootstrap ownership for V3 runtime observers.

P17-M2-01 moves import/bootstrap wiring out of ``zongdiaodu.py`` while
intentionally preserving the historical import-time registration semantics.
A later lifecycle migration may move this seam to explicit startup only after
callers are proven not to depend on import-time registration.
"""
from __future__ import annotations

from .world_understanding_production import install_world_understanding_observer


def install_zongdiaodu_import_observers() -> None:
    """Install the existing production observers through one bootstrap seam."""
    install_world_understanding_observer()


__all__ = ["install_zongdiaodu_import_observers"]
