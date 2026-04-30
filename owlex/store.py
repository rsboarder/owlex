"""Canonical persistence for owlex task execution and council outcomes.

This is the single source of truth — the engine writes here directly. Older
deployments wrote a JSONL log at ``~/.owlex/logs/timing.jsonl``; on first init
we import that file once so historical data is available in the new schema.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .models import Task

OWLEX_HOME = Path.home() / ".owlex"
DB_PATH = OWLEX_HOME / "owlex.db"
LEGACY_JSONL = OWLEX_HOME / "logs" / "timing.jsonl"

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    task_id       TEXT PRIMARY KEY,
    agent         TEXT NOT NULL,
    round         INTEGER NOT NULL,
    command       TEXT NOT NULL,
    council_id    TEXT,
    status        TEXT NOT NULL,            -- running | completed | failed | cancelled
    started_at    TEXT NOT NULL,
    completed_at  TEXT,
    duration_s    REAL,
    prompt_text   TEXT,
    result_text   TEXT,
    output_chars  INTEGER,
    error         TEXT,
    last_lines    TEXT,
    session_id    TEXT,
    legacy        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_calls_started_at ON calls(started_at);
CREATE INDEX IF NOT EXISTS idx_calls_agent      ON calls(agent);
CREATE INDEX IF NOT EXISTS idx_calls_council    ON calls(council_id);
CREATE INDEX IF NOT EXISTS idx_calls_status     ON calls(status);

CREATE TABLE IF NOT EXISTS council_rounds (
    council_id  TEXT NOT NULL,
    round       INTEGER NOT NULL,
    ts          TEXT NOT NULL,
    fastest     TEXT,
    slowest     TEXT,
    spread_s    REAL,
    agent_order TEXT,
    PRIMARY KEY (council_id, round)
);

CREATE TABLE IF NOT EXISTS council_outcomes (
    council_id        TEXT PRIMARY KEY,
    completed_at      TEXT NOT NULL,
    total_duration_s  REAL,
    agreement_score   REAL,
    agreement_reason  TEXT,
    progress_log      TEXT,
    claude_opinion    TEXT,
    deliberation      INTEGER NOT NULL DEFAULT 0,
    critique          INTEGER NOT NULL DEFAULT 0,
    rounds            INTEGER,
    backfilled        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS skill_invocations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    ts           TEXT,
    kind         TEXT NOT NULL,
    name         TEXT NOT NULL,
    args_summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_skill_task ON skill_invocations(task_id);

CREATE TABLE IF NOT EXISTS skill_parse_state (
    task_id    TEXT PRIMARY KEY,
    parsed_at  TEXT NOT NULL,
    found      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS session_scores (
    council_id TEXT NOT NULL,
    rater      TEXT NOT NULL DEFAULT 'human',
    score      INTEGER NOT NULL,            -- -1 (👎) or +1 (👍)
    label      TEXT,
    comment    TEXT,
    ts         TEXT NOT NULL,
    PRIMARY KEY (council_id, rater, ts)
);
CREATE INDEX IF NOT EXISTS idx_scores_council ON session_scores(council_id);

CREATE TABLE IF NOT EXISTS pairwise_agreements (
    council_id  TEXT NOT NULL,
    agent_a     TEXT NOT NULL,
    agent_b     TEXT NOT NULL,
    score       REAL NOT NULL,              -- 1..5
    reason      TEXT,
    source      TEXT NOT NULL,              -- 'judge' | 'overlap'
    computed_at TEXT NOT NULL,
    PRIMARY KEY (council_id, agent_a, agent_b)
);
CREATE INDEX IF NOT EXISTS idx_pa_council ON pairwise_agreements(council_id);

CREATE TABLE IF NOT EXISTS agent_scores (
    council_id  TEXT NOT NULL,
    agent       TEXT NOT NULL,              -- mapped server-side from letter label
    rater       TEXT NOT NULL,              -- 'claude_blind' for now
    score       INTEGER NOT NULL,           -- -1 or +1
    dimensions  TEXT,                       -- JSON: {groundedness, helpfulness, correctness} 1..5
    reason      TEXT,
    ts          TEXT NOT NULL,
    PRIMARY KEY (council_id, agent, rater, ts)
);
CREATE INDEX IF NOT EXISTS idx_agent_scores_council ON agent_scores(council_id);
CREATE INDEX IF NOT EXISTS idx_agent_scores_agent   ON agent_scores(agent);

CREATE TABLE IF NOT EXISTS council_anonymization (
    council_id TEXT NOT NULL,
    label      TEXT NOT NULL,               -- 'A', 'B', 'C', ...
    agent      TEXT NOT NULL,               -- 'codex', 'gemini', ...
    created_at TEXT NOT NULL,
    PRIMARY KEY (council_id, label)
);
CREATE INDEX IF NOT EXISTS idx_anon_council ON council_anonymization(council_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


_LOCK = threading.RLock()
_TLS = threading.local()
_INIT_DONE = False


def _agent_and_round(command: str) -> tuple[str, int]:
    """Derive (base_agent, round) from a council/agent command name."""
    name = command or ""
    if name.startswith("council_"):
        name = name[len("council_"):]
    round_n = 1
    if name.endswith("_delib"):
        name = name[: -len("_delib")]
        round_n = 2
    if "_" in name:
        name = name.split("_", 1)[0]
    return name, round_n


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Return a per-thread connection. Schema + legacy import run once globally."""
    global _INIT_DONE
    conn: sqlite3.Connection | None = getattr(_TLS, "conn", None)
    if conn is not None:
        return conn
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    with _LOCK:
        if not _INIT_DONE:
            conn.executescript(SCHEMA)
            _ensure_columns(conn)
            _migrate_legacy_jsonl(conn)
            _INIT_DONE = True
    _TLS.conn = conn
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the original schema. ALTER TABLE on missing only."""
    outcomes_cols = {r[1] for r in conn.execute("PRAGMA table_info(council_outcomes)").fetchall()}
    if "backfilled" not in outcomes_cols:
        conn.execute("ALTER TABLE council_outcomes ADD COLUMN backfilled INTEGER NOT NULL DEFAULT 0")

    # OpenLLMetry / OTel gen_ai.* attributes (Feature 10)
    # Mapping: model -> gen_ai.request.model; input_tokens -> gen_ai.usage.input_tokens;
    # output_tokens -> gen_ai.usage.output_tokens; finish_reason -> gen_ai.response.finish_reason.
    # Position tracking for vote-flip timeline (Feature 5):
    # position_delta (0..1 Jaccard distance R1→R2 of same agent) and position_label.
    calls_cols = {r[1] for r in conn.execute("PRAGMA table_info(calls)").fetchall()}
    for name, ddl in (
        ("model",          "ALTER TABLE calls ADD COLUMN model TEXT"),
        ("input_tokens",   "ALTER TABLE calls ADD COLUMN input_tokens INTEGER"),
        ("output_tokens",  "ALTER TABLE calls ADD COLUMN output_tokens INTEGER"),
        ("finish_reason",  "ALTER TABLE calls ADD COLUMN finish_reason TEXT"),
        ("position_delta", "ALTER TABLE calls ADD COLUMN position_delta REAL"),
        ("position_label", "ALTER TABLE calls ADD COLUMN position_label TEXT"),
    ):
        if name not in calls_cols:
            conn.execute(ddl)


def _migrate_legacy_jsonl(conn: sqlite3.Connection, jsonl_path: Path = LEGACY_JSONL) -> None:
    if not jsonl_path.exists():
        return
    row = conn.execute("SELECT value FROM meta WHERE key='jsonl_imported'").fetchone()
    if row:
        return
    imported = 0
    with _LOCK:
        conn.execute("BEGIN")
        try:
            for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "council_round_summary":
                    _insert_round_summary(conn, entry)
                    imported += 1
                elif entry.get("task_id"):
                    _insert_legacy_call(conn, entry)
                    imported += 1
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('jsonl_imported', ?)",
                (datetime.now().isoformat() + f" rows={imported}",),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _insert_legacy_call(conn: sqlite3.Connection, entry: dict) -> None:
    command = entry.get("command", "")
    agent, round_n = _agent_and_round(command)
    last_lines = entry.get("last_lines")
    conn.execute(
        """INSERT OR IGNORE INTO calls
           (task_id, agent, round, command, council_id, status,
            started_at, completed_at, duration_s,
            prompt_text, result_text, output_chars, error, last_lines, legacy)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 1)""",
        (
            entry.get("task_id"),
            agent,
            round_n,
            command,
            entry.get("council_id"),
            entry.get("status", "unknown"),
            entry.get("ts"),  # legacy: only completion ts known
            entry.get("ts"),
            float(entry.get("duration_s") or 0.0),
            entry.get("preview"),  # 500-char preview is the best legacy result we have
            entry.get("output_chars"),
            entry.get("error"),
            json.dumps(last_lines) if last_lines else None,
        ),
    )


def _insert_round_summary(conn: sqlite3.Connection, entry: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO council_rounds
           (council_id, round, ts, fastest, slowest, spread_s, agent_order)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            entry.get("council_id"),
            int(entry.get("round") or 0),
            entry.get("ts"),
            entry.get("fastest"),
            entry.get("slowest"),
            entry.get("spread_s"),
            json.dumps(entry.get("agent_order") or []),
        ),
    )


# --- Live writers used by engine.py / council.py ---

def record_task_running(task: Task, prompt: str | None = None) -> None:
    """Insert/refresh a row at task start. Idempotent; safe to call multiple times."""
    try:
        conn = connect()
        agent, round_n = _agent_and_round(task.command)
        model = getattr(task, "model", None)
        with _LOCK:
            conn.execute(
                """INSERT INTO calls
                   (task_id, agent, round, command, council_id, status,
                    started_at, prompt_text, model)
                   VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)
                   ON CONFLICT(task_id) DO UPDATE SET
                     status      = excluded.status,
                     prompt_text = COALESCE(excluded.prompt_text, calls.prompt_text),
                     model       = COALESCE(excluded.model,       calls.model)""",
                (
                    task.task_id,
                    agent,
                    round_n,
                    task.command,
                    task.council_id,
                    task.start_time.isoformat(),
                    prompt,
                    model,
                ),
            )
    except Exception as e:
        _log_failure("record_task_running", e)


def record_task_complete(task: Task, session_id: str | None = None) -> None:
    """Update the row with terminal status, result, error, duration."""
    try:
        conn = connect()
        completed_at = task.completion_time or datetime.now()
        duration = round((completed_at - task.start_time).total_seconds(), 3)
        last_lines = task.output_lines[-10:] if task.output_lines else None
        agent, round_n = _agent_and_round(task.command)
        model = getattr(task, "model", None)
        with _LOCK:
            conn.execute(
                """INSERT INTO calls
                   (task_id, agent, round, command, council_id, status,
                    started_at, completed_at, duration_s,
                    prompt_text, result_text, output_chars, error, last_lines, session_id, model)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(task_id) DO UPDATE SET
                     status       = excluded.status,
                     completed_at = excluded.completed_at,
                     duration_s   = excluded.duration_s,
                     result_text  = excluded.result_text,
                     output_chars = excluded.output_chars,
                     error        = excluded.error,
                     last_lines   = excluded.last_lines,
                     session_id   = COALESCE(excluded.session_id, calls.session_id),
                     model        = COALESCE(excluded.model,       calls.model)""",
                (
                    task.task_id,
                    agent,
                    round_n,
                    task.command,
                    task.council_id,
                    task.status,
                    task.start_time.isoformat(),
                    completed_at.isoformat(),
                    duration,
                    task.result,
                    len(task.result) if task.result else None,
                    task.error,
                    json.dumps(last_lines) if last_lines else None,
                    session_id,
                    model,
                ),
            )
    except Exception as e:
        _log_failure("record_task_complete", e)


def record_council_round(council_id: str, round_num: int,
                         agent_timings: list[tuple[str, float, str]]) -> None:
    if not agent_timings:
        return
    try:
        conn = connect()
        ranking = [f"{name}={dur:.1f}s({st})" for name, dur, st in agent_timings]
        with _LOCK:
            conn.execute(
                """INSERT OR REPLACE INTO council_rounds
                   (council_id, round, ts, fastest, slowest, spread_s, agent_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    council_id,
                    round_num,
                    datetime.now().isoformat(),
                    agent_timings[0][0],
                    agent_timings[-1][0],
                    round(agent_timings[-1][1] - agent_timings[0][1], 1) if len(agent_timings) >= 2 else 0,
                    json.dumps(ranking),
                ),
            )
    except Exception as e:
        _log_failure("record_council_round", e)


def record_council_outcome(
    council_id: str,
    *,
    total_duration_s: float,
    rounds: int,
    deliberation: bool,
    critique: bool,
    agreement_score: float | None = None,
    agreement_reason: str | None = None,
    progress_log: list[str] | None = None,
    claude_opinion: str | None = None,
    backfilled: bool = False,
    completed_at: str | None = None,
) -> None:
    try:
        conn = connect()
        with _LOCK:
            conn.execute(
                """INSERT OR REPLACE INTO council_outcomes
                   (council_id, completed_at, total_duration_s,
                    agreement_score, agreement_reason, progress_log,
                    claude_opinion, deliberation, critique, rounds, backfilled)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    council_id,
                    completed_at or datetime.now().isoformat(),
                    round(total_duration_s, 3),
                    agreement_score,
                    agreement_reason,
                    json.dumps(progress_log or []),
                    claude_opinion,
                    1 if deliberation else 0,
                    1 if critique else 0,
                    rounds,
                    1 if backfilled else 0,
                ),
            )
    except Exception as e:
        _log_failure("record_council_outcome", e)


def record_session_score(
    council_id: str,
    score: int,
    *,
    rater: str = "human",
    label: str | None = None,
    comment: str | None = None,
) -> None:
    """Persist a thumbs-up / thumbs-down on a council session."""
    if score not in (-1, 1):
        raise ValueError("score must be -1 or +1")
    try:
        conn = connect()
        with _LOCK:
            conn.execute(
                """INSERT OR REPLACE INTO session_scores
                   (council_id, rater, score, label, comment, ts)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (council_id, rater, int(score), label, comment, datetime.now().isoformat()),
            )
    except Exception as e:
        _log_failure("record_session_score", e)


def record_pairwise_agreements(
    council_id: str,
    rows: list[tuple[str, str, float, str | None]],
    *,
    source: str = "judge",
) -> None:
    """Persist a batch of pairwise agreement scores for a council.

    rows: list of (agent_a, agent_b, score, reason). Pairs are stored sorted so
    (a,b) and (b,a) always end up in the same row.
    """
    if not rows:
        return
    try:
        conn = connect()
        ts = datetime.now().isoformat()
        with _LOCK:
            conn.execute("BEGIN")
            try:
                for a, b, score, reason in rows:
                    a_, b_ = sorted([a, b])
                    conn.execute(
                        """INSERT OR REPLACE INTO pairwise_agreements
                           (council_id, agent_a, agent_b, score, reason, source, computed_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (council_id, a_, b_, float(score), reason, source, ts),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    except Exception as e:
        _log_failure("record_pairwise_agreements", e)


def record_position_delta(
    task_id: str,
    *,
    position_delta: float,
    position_label: str,
) -> None:
    """Update an R2 call row with its R1↔R2 Jaccard position delta."""
    try:
        conn = connect()
        with _LOCK:
            conn.execute(
                "UPDATE calls SET position_delta=?, position_label=? WHERE task_id=?",
                (round(float(position_delta), 4), position_label, task_id),
            )
    except Exception as e:
        _log_failure("record_position_delta", e)


def record_council_anonymization(council_id: str, mapping: dict[str, str]) -> None:
    """Persist the letter→agent mapping for a blind council. Idempotent per (council_id, label)."""
    if not mapping:
        return
    try:
        conn = connect()
        ts = datetime.now().isoformat()
        with _LOCK:
            conn.execute("BEGIN")
            try:
                for label, agent in mapping.items():
                    conn.execute(
                        """INSERT OR REPLACE INTO council_anonymization
                           (council_id, label, agent, created_at)
                           VALUES (?, ?, ?, ?)""",
                        (council_id, label, agent, ts),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    except Exception as e:
        _log_failure("record_council_anonymization", e)


def get_council_anonymization(council_id: str) -> dict[str, str]:
    """Return {label: agent} mapping for a blind council, or {} if none."""
    try:
        conn = connect()
        rows = conn.execute(
            "SELECT label, agent FROM council_anonymization WHERE council_id=?",
            (council_id,),
        ).fetchall()
        return {r["label"]: r["agent"] for r in rows}
    except Exception as e:
        _log_failure("get_council_anonymization", e)
        return {}


def record_agent_score(
    council_id: str,
    agent: str,
    score: int,
    *,
    rater: str = "claude_blind",
    dimensions: dict | None = None,
    reason: str | None = None,
) -> None:
    """Persist a per-agent rating row. Score must be -1 or +1."""
    if score not in (-1, 1):
        raise ValueError("score must be -1 or +1")
    try:
        conn = connect()
        with _LOCK:
            conn.execute(
                """INSERT OR REPLACE INTO agent_scores
                   (council_id, agent, rater, score, dimensions, reason, ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    council_id,
                    agent,
                    rater,
                    int(score),
                    json.dumps(dimensions) if dimensions else None,
                    reason,
                    datetime.now().isoformat(),
                ),
            )
    except Exception as e:
        _log_failure("record_agent_score", e)


def _log_failure(where: str, exc: Exception) -> None:
    import sys
    print(f"[owlex.store] {where} failed: {exc}", file=sys.stderr, flush=True)
