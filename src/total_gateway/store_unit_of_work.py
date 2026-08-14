from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol


class GatewayStoreWriteConnection(Protocol):
    """Minimum connection surface required by the Gateway write UoW seam."""

    def execute(self, sql: str, parameters: tuple[object, ...] = (), /) -> object:
        ...


@contextmanager
def gateway_store_write_transaction(
    connection: GatewayStoreWriteConnection,
) -> Iterator[None]:
    """Own only the existing Gateway SQLite write-transaction lifecycle.

    Locking, connection ownership, Store closed-state validation, schema, health
    checks, and domain SQL remain responsibilities of ``GatewayStateStore``.
    The control flow deliberately keeps COMMIT inside the try block so a COMMIT
    failure follows the historical ROLLBACK path.
    """

    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
