# Tests writing to production via `Path.home()` module-level globals

## Problem

`owlex/store.py` historically had:

```python
OWLEX_HOME = Path.home() / ".owlex"
DB_PATH = OWLEX_HOME / "owlex.db"
```

These are **module-level constants** resolved at import time. Any test that imported `owlex.store` (directly or transitively) and called `store.connect()` opened `~/.owlex/owlex.db` — the **production** database. The test fixtures were not aware of this.

The result: 81 rows of MagicMock-poisoned data in production `council_outcomes`, plus 1143 cascaded rows in `pairwise_agreements`:

```
| council_id | progress_log preview                                              |
|------------|-------------------------------------------------------------------|
| 005448     | "<MagicMock name='config.claudeor.model' id='4437173072'> ..."    |
| 005449     | "<MagicMock name='config.aichat.model' id='4437404048'> ..."      |
| ... (79 more) ...                                                              |
```

These polluted the dashboard's leaderboard for weeks.

## Root cause

**Defaulting persistence path to `Path.home()` at module load** makes "test pollutes prod" the default behavior. Every test author who doesn't know to override it inherits the bug.

The deeper issue: a module-level singleton singleton tied to the user's home directory. Test isolation requires fighting a global, which is exactly the wrong shape for testability.

## Solution

Resolve `OWLEX_HOME` at **call time**, with explicit env override:

```python
def _owlex_home() -> Path:
    override = os.environ.get("OWLEX_HOME")
    return Path(override) if override else Path.home() / ".owlex"

def _default_db_path() -> Path:
    return _owlex_home() / "owlex.db"

def connect(db_path: Path | None = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = _default_db_path()
    # ...
```

Module-level `OWLEX_HOME` and `DB_PATH` constants remain for backwards-compat, but anything that runs in test context goes through the dynamic helpers.

`_reset_for_tests()` closes the per-thread connection and clears the `_INIT_DONE` flag + repository container lru_cache. Tests are isolated via an **autouse fixture** in `conftest.py`:

```python
@pytest.fixture(autouse=True)
def _isolate_owlex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OWLEX_HOME", str(tmp_path / "owlex_home"))
    from owlex import derivations, store
    store._reset_for_tests()
    derivations._reset_for_tests()
    yield
    store._reset_for_tests()
    derivations._reset_for_tests()
```

Every test now writes to a per-test tmp dir. Production is never touched.

## Why this works

- **Defaults to safe, not unsafe**: the autouse fixture sets `OWLEX_HOME` before any test code runs. There is no way for a test to forget to override it.
- **Tests are not coupled to a specific override mechanism**: they just use the engine/store APIs normally; the fixture handles isolation transparently.
- **Reset hook is a real seam**: production code never imports it; it lives in the same module to share access to `_INIT_DONE` etc. but is clearly marked `_reset_for_tests`.

## Prevention

- **The autouse fixture itself is the T1 control** — it cannot be forgotten because it auto-runs for every test.
- **Detective test** (could be added): `tests/test_no_prod_db_pollution.py` that records `os.stat("~/.owlex/owlex.db").st_size` before/after running the suite. If it ever changes, something escaped isolation.
- **Migration cleanup**: the 81 + 1143 polluted rows in production were deleted via `DELETE FROM council_outcomes WHERE progress_log LIKE '%MagicMock%'`. A similar cleanup is part of the recovery runbook.

## Generalizable rule

Any module that auto-resolves persistence paths from `Path.home()` (or `~`, or hardcoded `/var/`, etc.) at import time:

1. Has a latent test-pollution bug.
2. Must offer an env override (or constructor injection).
3. Must come with an autouse fixture that uses that override in the test suite.

If 3 isn't there, the team will eventually pollute prod and not notice for weeks.

## Related

- `docs/solutions/architecture/derivation-writes-need-long-lived-consumer.md` — same project, separate concern; both touch the same singleton.
