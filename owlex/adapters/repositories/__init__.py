"""SQLite-backed repositories over the canonical owlex schema.

These wrap the same SQL the original ``owlex.store`` writers performed; the
store module is kept as a thin façade that delegates here. Each repo accepts a
connection getter so tests can inject an in-memory ``sqlite3.Connection``.
"""
from .call_repository import SqliteCallRepository
from .council_repository import SqliteCouncilRepository
from .score_repository import SqliteScoreRepository
from .pairwise_repository import SqlitePairwiseRepository
from .skill_repository import SqliteSkillRepository

__all__ = [
    "SqliteCallRepository",
    "SqliteCouncilRepository",
    "SqliteScoreRepository",
    "SqlitePairwiseRepository",
    "SqliteSkillRepository",
]
