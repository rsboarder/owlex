"""AUDIT-10 flywheel — persist real solution-audit runs as a soft-labeled corpus.

DESIGN INTENT
-------------
`second_opinion.get_second_opinion` is ephemeral: it calls the reviewer, returns
text, and discards everything.  Council deliberations ARE persisted in
`owlex.db` (via `derivations`).  This module extends that pattern to the audit
leg: every real reviewer run can be recorded here so live usage becomes a
growing labeled corpus over time.

STANDALONE, NOT WIRED
---------------------
The `second_opinion` feature is not yet committed.  This module is STANDALONE —
it exposes helpers that a future caller can invoke; it does NOT import or hook
into `owlex.second_opinion`.  The wiring happens once that feature is committed;
see "Future hook" below.

SOFT LABELS
-----------
`verified_json` stores the findings that survived AUDIT-1 Phase-2 citation
checking (kept vs. dropped).  These are WEAK labels — AUDIT-1 Phase-2 is itself
an LLM judge, not a human ground-truth oracle.  Items produced by
`runs_as_corpus` carry `split:"iterate"` and must be kept OUT of any
held-out or precision-critical evaluation split.

FUTURE HOOK (not wired yet)
---------------------------
In `owlex/second_opinion.py`, after a real run completes, add roughly:

    from bench.flywheel import diff_hash_of, record_run
    dh = diff_hash_of(diff_text)
    record_run(
        diff_hash=dh,
        findings=parsed_findings,       # list[{file, line, description}]
        verified=verified_findings,     # AUDIT-1 Phase-2 kept subset (or None)
        outcome="completed",
    )

Do NOT add this until `owlex/second_opinion.py` is committed and reviewed.

DB PATH
-------
Separate file `OWLEX_HOME/audit_corpus.db` — never touches production `owlex.db`.
OWLEX_HOME is resolved at call-time so the tests' autouse fixture (which sets
OWLEX_HOME to a tmp dir) isolates every write automatically.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_runs (
    diff_hash     TEXT PRIMARY KEY,
    findings_json TEXT,
    verified_json TEXT,
    panel_verdict TEXT,
    outcome       TEXT,
    recorded_at   TEXT
);
"""


def _db_path() -> str:
    """Resolve the audit corpus DB path from OWLEX_HOME at call time.

    Reads the env var on every call so the tests' autouse fixture (which sets
    OWLEX_HOME=<tmp>) takes effect without any module-level caching.
    """
    home = os.environ.get("OWLEX_HOME", os.path.expanduser("~/.owlex"))
    return os.path.join(home, "audit_corpus.db")


def _connect(path: str | None = None) -> sqlite3.Connection:
    """Open (and initialize if absent) the audit corpus DB at `path`.

    Creates the parent directory and the `audit_runs` table if they don't exist.
    Returns an open connection; caller is responsible for closing it.
    """
    if path is None:
        path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def diff_hash_of(diff_text: str) -> str:
    """Return the SHA-256 hex digest of `diff_text` — stable key for dedup."""
    return hashlib.sha256(diff_text.encode()).hexdigest()


def record_run(
    diff_hash: str,
    findings: list[dict],
    verified: list[dict] | None = None,
    panel_verdict: str | None = None,
    outcome: str | None = None,
    recorded_at: str = "",
    db_path: str | None = None,
) -> None:
    """UPSERT one audit run into the corpus DB.

    Parameters
    ----------
    diff_hash:
        Stable key produced by `diff_hash_of`.  Duplicate hashes REPLACE the
        existing row (dedup-by-diff_hash growth policy).
    findings:
        The reviewer's raw findings: list of ``{file, line, description}``.
    verified:
        The AUDIT-1 Phase-2 kept subset (weak label).  ``None`` when Phase-2
        has not run yet.
    panel_verdict:
        Optional Opus-panel summary string.
    outcome:
        Free-text outcome tag (e.g. ``"completed"``, ``"timed_out"``).
    recorded_at:
        ISO-8601 timestamp string.  Passed in by the caller (injectable /
        deterministic for tests); defaults to ``""`` when not provided.
    db_path:
        Override the DB path (tests pass a tmp path here).
    """
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO audit_runs
                (diff_hash, findings_json, verified_json, panel_verdict, outcome, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(diff_hash) DO UPDATE SET
                findings_json = excluded.findings_json,
                verified_json = excluded.verified_json,
                panel_verdict = excluded.panel_verdict,
                outcome       = excluded.outcome,
                recorded_at   = excluded.recorded_at
            """,
            (
                diff_hash,
                json.dumps(findings),
                json.dumps(verified) if verified is not None else None,
                panel_verdict,
                outcome,
                recorded_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def load_runs(db_path: str | None = None) -> list[dict]:
    """Return all recorded runs as a list of dicts, deserializing JSON columns.

    Each dict has keys: ``diff_hash``, ``findings``, ``verified``,
    ``panel_verdict``, ``outcome``, ``recorded_at``.  Returns ``[]`` when the
    DB does not yet exist.
    """
    path = db_path if db_path is not None else _db_path()
    if not os.path.exists(path):
        return []
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT diff_hash, findings_json, verified_json, panel_verdict, outcome, recorded_at "
            "FROM audit_runs"
        ).fetchall()
    finally:
        conn.close()

    result = []
    for diff_hash, findings_json, verified_json, panel_verdict, outcome, recorded_at in rows:
        result.append(
            {
                "diff_hash": diff_hash,
                "findings": json.loads(findings_json) if findings_json else [],
                "verified": json.loads(verified_json) if verified_json is not None else None,
                "panel_verdict": panel_verdict,
                "outcome": outcome,
                "recorded_at": recorded_at,
            }
        )
    return result


def runs_as_corpus(db_path: str | None = None) -> list[dict]:
    """Convert recorded runs to soft-labeled corpus items.

    Each run that has a non-empty ``verified`` list becomes one corpus item.
    Verified findings are the AUDIT-1 Phase-2 kept subset — a WEAK label, not
    ground-truth.  Items are tagged ``split:"iterate"`` and must be kept OUT of
    any held-out or precision-critical evaluation split.

    Item shape::

        {
            "id": "fly-<8-char diff_hash prefix>",
            "source": "flywheel",
            "split": "iterate",
            "bugs": [
                {"bug_type": "flywheel", "file": ..., "line": ..., "description": ...},
                ...
            ],
            "provenance": {
                "source": "audit-run",
                "diff_hash": "<full hash>",
            },
        }

    Runs with ``verified=None`` or an empty verified list are silently skipped
    (no Phase-2 label available yet).
    """
    items = []
    for run in load_runs(db_path):
        verified = run.get("verified")
        if not verified:
            continue
        bugs = [
            {
                "bug_type": "flywheel",
                "file": f.get("file", ""),
                "line": f.get("line"),
                "description": f.get("description", ""),
            }
            for f in verified
        ]
        items.append(
            {
                "id": f"fly-{run['diff_hash'][:8]}",
                "source": "flywheel",
                "split": "iterate",
                "bugs": bugs,
                "provenance": {
                    "source": "audit-run",
                    "diff_hash": run["diff_hash"],
                },
            }
        )
    return items
