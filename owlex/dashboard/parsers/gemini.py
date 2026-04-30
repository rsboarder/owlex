"""Parse Gemini CLI chat sessions (~/.gemini/tmp/<hash>/chats/session-*.json).

The CLI persists tool calls inside each gemini-role message under ``toolCalls`` —
a list of ``{id, name, args, result, status, timestamp, ...}`` records.

Note: in headless ``-p`` mode the CLI may skip writing tool calls (gemini-cli
issue #10927). For interactive / council-style runs they are present.
"""
from __future__ import annotations

import json
from pathlib import Path

from ._common import find_recent_file, truncate

ROOTS = [Path.home() / ".gemini" / "tmp"]


def parse(task_id: str, ts: str, session_id: str | None = None) -> list[dict]:
    chat = find_recent_file(ROOTS, ts, "session-*.json", window_minutes=15)
    if chat is None:
        return []
    try:
        data = json.loads(chat.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    out: list[dict] = []

    # Newer schema: { messages: [{ toolCalls: [...] }] }
    messages = data.get("messages") if isinstance(data, dict) else None
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            calls = msg.get("toolCalls")
            if not isinstance(calls, list):
                continue
            for tc in calls:
                if not isinstance(tc, dict):
                    continue
                out.append({
                    "ts": tc.get("timestamp") or msg.get("timestamp"),
                    "kind": "tool",
                    "name": str(tc.get("name") or tc.get("displayName") or "?"),
                    "args_summary": truncate(
                        json.dumps(tc.get("args") or {}, ensure_ascii=False)
                    ),
                })
        if out:
            return out

    # Legacy / chat-history schema fallback: { history: [{ parts: [{ functionCall }] }] }
    history = data.get("history") if isinstance(data, dict) else data
    if isinstance(history, list):
        for msg in history:
            parts = msg.get("parts") if isinstance(msg, dict) else None
            if not isinstance(parts, list):
                continue
            for p in parts:
                if not isinstance(p, dict):
                    continue
                fc = p.get("functionCall") or p.get("function_call")
                if fc:
                    out.append({
                        "ts": None,
                        "kind": "tool",
                        "name": str(fc.get("name") or "?"),
                        "args_summary": truncate(json.dumps(fc.get("args") or {}, ensure_ascii=False)),
                    })
    return out
