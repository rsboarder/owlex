# Audit Flywheel — persisting live solution-audit runs as a growing corpus

## Problem

`second_opinion.get_second_opinion` is ephemeral: it calls the reviewer, returns
text, and discards everything. Council deliberations ARE persisted in
`owlex.db` via `derivations` (process-wide queue, long-lived consumer). The
audit leg has no equivalent — every real reviewer run on a real diff vanishes.

Over time, real diffs reviewed in production are the highest-signal training
signal we have. Capturing them (findings + the AUDIT-1 Phase-2 verification
result as a weak label) turns live usage into a self-bootstrapping corpus.

## Schema

Separate file `OWLEX_HOME/audit_corpus.db` — never mixed with production
`owlex.db`. Schema:

```sql
CREATE TABLE IF NOT EXISTS audit_runs (
    diff_hash     TEXT PRIMARY KEY,   -- sha256(diff_text), stable dedup key
    findings_json TEXT,               -- [{file, line, description}, ...]
    verified_json TEXT,               -- AUDIT-1 Phase-2 kept subset (weak label), or NULL
    panel_verdict TEXT,               -- optional Opus-panel summary string
    outcome       TEXT,               -- free-text tag ("completed", "timed_out", ...)
    recorded_at   TEXT                -- ISO-8601 timestamp, caller-supplied
);
```

**Dedup policy**: `diff_hash` is the primary key; UPSERT on conflict replaces
the row. Identical diffs map to the same hash so repeated runs update rather
than accumulate.

## Soft-label semantics

`verified_json` stores the AUDIT-1 Phase-2 kept findings — those that survived
the citation checker's "does this finding cite a real added line?" filter. This
is a **weak label**, not ground truth:

- Phase-2 is itself an LLM judge; it has its own false-positive and false-negative
  rate (measured in AUDIT-1, not zero).
- Ground truth requires human confirmation.

Corpus items produced by `runs_as_corpus` carry `split:"iterate"` for this
reason. They MUST be kept out of any held-out or precision-critical evaluation
split. They are appropriate for qualitative triage, iteration signal, and
exploratory coverage analysis.

## Implementation

See `bench/flywheel.py`. Public API:

| Symbol | Purpose |
|---|---|
| `diff_hash_of(diff_text) -> str` | sha256 stable key |
| `record_run(diff_hash, findings, verified=None, ...) -> None` | UPSERT a run |
| `load_runs(db_path=None) -> list[dict]` | read all rows, JSON columns deserialized |
| `runs_as_corpus(db_path=None) -> list[dict]` | convert verified findings to soft-labeled corpus items |
| `_db_path() -> str` | resolves OWLEX_HOME at call time (tests' autouse fixture sets it to tmp) |
| `_connect(path=None) -> sqlite3.Connection` | creates dir + table if absent, returns open connection |

`recorded_at` is caller-supplied (not `datetime.now()` inside the module) so
tests can pass deterministic values.

## Where the future hook attaches

**NOT WIRED YET** — `owlex/second_opinion.py` is not committed. Once it is,
add roughly 5 lines after a real reviewer run completes:

```python
# In owlex/second_opinion.get_second_opinion, after run completes:
from bench.flywheel import diff_hash_of, record_run
import datetime

dh = diff_hash_of(diff_text)
record_run(
    diff_hash=dh,
    findings=parsed_findings,       # list[{file, line, description}]
    verified=verified_findings,     # AUDIT-1 Phase-2 kept subset, or None
    outcome="completed" if ok else "failed",
    recorded_at=datetime.datetime.utcnow().isoformat(),
)
```

Do NOT add this until `owlex/second_opinion.py` is reviewed and committed, and
the import dependency (`bench` imported from `owlex/`) is confirmed acceptable.

## Growth policy

- New diffs: inserted as new rows (new `diff_hash`).
- Repeated same diff: UPSERT replaces — updated Phase-2 verification or panel
  verdict overwrites the old row, no duplicate accumulation.
- Pruning: not implemented. The DB is append-only + replace; prune manually
  if the file grows beyond ~100 MB (typical row is ~5 KB).
