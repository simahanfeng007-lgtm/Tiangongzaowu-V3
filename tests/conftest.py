"""Shared pytest collection hooks.

The ``ci_fragile`` marker is honored only on Windows CI runners
(TIANGONG_CI_ENV=1): those tests exercise real AppContainer profiles, release
archive bindings, TTS network round trips and short-path-sensitive temp
directories that shared runners cannot provide. They keep running in local
development and on the Ubuntu leg.
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("TIANGONG_CI_ENV") != "1" or os.name != "nt":
        return
    skip_ci_fragile = pytest.mark.skip(
        reason="Windows CI runner environment limitation (ci_fragile)"
    )
    for item in items:
        if item.get_closest_marker("ci_fragile"):
            item.add_marker(skip_ci_fragile)