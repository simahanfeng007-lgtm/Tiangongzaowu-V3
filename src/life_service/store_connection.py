"""SQLite connection lifecycle boundary for the authoritative Life shadow store.

This module owns only path safety, sqlite connection creation and connection-level
PRAGMA configuration. It does not own schema SQL, migrations, transactions,
repositories, writer leases, health policy or Life domain state.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class LifeStoreSchemaLifecycle(Protocol):
    def __call__(self, connection: sqlite3.Connection, *, now_ms: int) -> None: ...


LifeStoreErrorFactory = Callable[[str], Exception]


@dataclass(frozen=True, slots=True)
class OpenedLifeShadowSqlite:
    path: Path
    connection: sqlite3.Connection
    existed: bool


def open_life_shadow_sqlite(
    path: Path,
    *,
    create: bool,
    now_ms: int,
    error_factory: LifeStoreErrorFactory,
    initialize: LifeStoreSchemaLifecycle,
    migrate: LifeStoreSchemaLifecycle,
) -> OpenedLifeShadowSqlite:
    """Open one SQLite handle while preserving LifeShadowStore open semantics."""
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise error_factory("shadow store timestamp is invalid")
    if path.name != path.name.strip() or not path.name.endswith(".shadow.sqlite3"):
        raise error_factory("shadow store path must end with .shadow.sqlite3")
    parent = path.parent.resolve(strict=True)
    candidate = parent / path.name
    if candidate.exists():
        if candidate.is_symlink() or not candidate.is_file():
            raise error_factory("shadow store path is unsafe")
    elif not create:
        raise error_factory("shadow store does not exist")
    existed = candidate.exists()
    connection = sqlite3.connect(
        candidate,
        timeout=5.0,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA busy_timeout=5000")
        mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        if mode != "wal":
            raise error_factory("shadow store did not enter WAL mode")
        connection.execute("PRAGMA synchronous=FULL")
        if not existed:
            if not create:
                raise error_factory("shadow store creation was not authorized")
            initialize(connection, now_ms=now_ms)
        else:
            migrate(connection, now_ms=now_ms)
        return OpenedLifeShadowSqlite(candidate, connection, existed)
    except Exception:
        connection.close()
        raise


__all__ = [
    "LifeStoreErrorFactory",
    "LifeStoreSchemaLifecycle",
    "OpenedLifeShadowSqlite",
    "open_life_shadow_sqlite",
]
