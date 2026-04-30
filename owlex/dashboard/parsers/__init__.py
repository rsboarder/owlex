"""Per-agent session-file parsers that extract skill/tool invocations.

Each parser exposes ``parse(task_id, ts, session_id=None) -> list[dict]`` returning
``[{"ts": str|None, "kind": "skill"|"tool", "name": str, "args_summary": str}]``.

All parsers are best-effort. On any failure, return [].
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from . import aichat, claudeor, codex, cursor, gemini, opencode

Parser = Callable[..., list[dict]]

_REGISTRY: dict[str, Parser] = {
    "claudeor": claudeor.parse,
    "codex": codex.parse,
    "gemini": gemini.parse,
    "opencode": opencode.parse,
    "aichat": aichat.parse,
    "cursor": cursor.parse,
}


def parse_for(agent: str, task_id: str, ts: str, session_id: str | None = None) -> list[dict]:
    fn = _REGISTRY.get(agent)
    if not fn:
        return []
    try:
        return fn(task_id, ts, session_id=session_id) or []
    except Exception:
        return []


def parse_and_persist(task_id: str, agent: str, ts: str, session_id: str | None = None) -> int:
    """Run the agent's parser and write skill_invocations + skill_parse_state.

    Idempotent: if a parse_state row already exists, returns the cached count.
    Returns the number of invocations recorded.
    """
    from ... import store as _store
    conn = _store.connect()
    cached = conn.execute(
        "SELECT found FROM skill_parse_state WHERE task_id=?", (task_id,)
    ).fetchone()
    if cached:
        return int(cached["found"])
    invocations = parse_for(agent, task_id, ts, session_id=session_id)
    with _store._LOCK:  # type: ignore[attr-defined]
        conn.execute("BEGIN")
        try:
            for i, inv in enumerate(invocations):
                conn.execute(
                    """INSERT INTO skill_invocations (task_id, seq, ts, kind, name, args_summary)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (task_id, i, inv.get("ts"), inv["kind"], inv["name"], inv.get("args_summary")),
                )
            conn.execute(
                "INSERT OR REPLACE INTO skill_parse_state(task_id, parsed_at, found) VALUES (?, ?, ?)",
                (task_id, datetime.now().isoformat(), len(invocations)),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return len(invocations)


def parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
