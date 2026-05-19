"""Default repository container wired to ``owlex.store.connect``."""
from __future__ import annotations

import threading
from functools import lru_cache

from . import (
    SqliteCallRepository,
    SqliteCouncilRepository,
    SqlitePairwiseRepository,
    SqliteScoreRepository,
    SqliteSkillRepository,
)


class RepositoryContainer:
    """Lazily constructs repositories sharing one connection getter and lock."""

    def __init__(self, connect, lock: threading.RLock) -> None:
        self.calls = SqliteCallRepository(connect, lock)
        self.councils = SqliteCouncilRepository(connect, lock)
        self.scores = SqliteScoreRepository(connect, lock)
        self.pairwise = SqlitePairwiseRepository(connect, lock)
        self.skills = SqliteSkillRepository(connect, lock)


@lru_cache(maxsize=1)
def default() -> RepositoryContainer:
    """The production container, wired to the store's per-thread connection."""
    from ... import store  # local import: store imports adapters.db.migrations
    return RepositoryContainer(store.connect, store._LOCK)
