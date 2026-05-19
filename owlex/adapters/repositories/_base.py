"""Shared base for SQLite repositories."""
from __future__ import annotations

import sqlite3
import sys
import threading
from typing import Callable


class SqliteRepository:
    """Holds the connection getter and a lock shared by all repos.

    The caller passes in a ``connect`` callable so tests can substitute an
    in-memory connection. The lock is shared (typically with the legacy
    ``owlex.store._LOCK``) so concurrent writers stay coordinated while the
    façade is in place.
    """

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        lock: threading.RLock | None = None,
    ) -> None:
        self._connect = connect
        self._lock = lock or threading.RLock()

    def _conn(self) -> sqlite3.Connection:
        return self._connect()

    @staticmethod
    def _log_failure(where: str, exc: Exception) -> None:
        print(f"[owlex.repo] {where} failed: {exc}", file=sys.stderr, flush=True)
