"""Parse OpenCode session storage.

Modern OpenCode (≥ ~0.2x) writes one JSON file per message-part at
``~/.local/share/opencode/storage/part/<message_id>/<part_id>.json``. Tool
calls are parts where ``type == 'tool'``, with fields ``callID``, ``tool``
(function name), ``state.input`` (arguments), ``state.output``, and
``state.time.{start,end}`` in epoch ms.

Older OpenCode versions used a single SQLite DB at
``~/.local/share/opencode/opencode.db``. We try the JSON tree first because
the DB stops being written once the migration has happened.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ._common import truncate

JSON_ROOT = Path.home() / ".local" / "share" / "opencode" / "storage" / "part"
DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
WINDOW_MINUTES = 15


def _parse_json_storage(target_ts: float) -> list[dict]:
    if not JSON_ROOT.exists():
        return []
    window = WINDOW_MINUTES * 60
    out: list[dict] = []
    # Walk msg directories; check dir mtime as a fast filter, then per-file mtime.
    try:
        msg_dirs = list(JSON_ROOT.iterdir())
    except OSError:
        return []
    for msg_dir in msg_dirs:
        try:
            if abs(msg_dir.stat().st_mtime - target_ts) > window:
                continue
        except OSError:
            continue
        try:
            files = list(msg_dir.iterdir())
        except OSError:
            continue
        for f in files:
            try:
                if abs(f.stat().st_mtime - target_ts) > window:
                    continue
            except OSError:
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            if d.get("type") != "tool":
                continue
            state = d.get("state") if isinstance(d.get("state"), dict) else {}
            t_ms = (state.get("time") or {}).get("start")
            out.append({
                "ts": datetime.fromtimestamp(t_ms / 1000).isoformat() if t_ms else None,
                "kind": "tool",
                "name": str(d.get("tool") or "?"),
                "args_summary": truncate(json.dumps(state.get("input") or {}, ensure_ascii=False)),
            })
    out.sort(key=lambda x: x.get("ts") or "")
    return out


def _parse_sqlite(target_ms: int) -> list[dict]:
    if not DB_PATH.exists():
        return []
    window_ms = WINDOW_MINUTES * 60 * 1000
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT data, time_created
                 FROM part
                WHERE json_extract(data, '$.type') = 'tool'
                  AND time_created BETWEEN ? AND ?
                ORDER BY time_created ASC""",
            (target_ms - window_ms, target_ms + window_ms),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        try:
            d = json.loads(r["data"])
        except (TypeError, json.JSONDecodeError):
            continue
        state = d.get("state") if isinstance(d.get("state"), dict) else {}
        t_ms = (state.get("time") or {}).get("start") or r["time_created"]
        out.append({
            "ts": datetime.fromtimestamp(t_ms / 1000).isoformat() if t_ms else None,
            "kind": "tool",
            "name": str(d.get("tool") or "?"),
            "args_summary": truncate(json.dumps(state.get("input") or {}, ensure_ascii=False)),
        })
    return out


def parse(task_id: str, ts: str, session_id: str | None = None) -> list[dict]:
    try:
        target = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return []
    target_ts = target.timestamp()
    out = _parse_json_storage(target_ts)
    if out:
        return out
    return _parse_sqlite(int(target_ts * 1000))
