"""Parse aichat session YAML (~/.config/aichat/sessions/*.yaml).

Since PR #994 ("feat: save function calls in the session") aichat persists tool
results inside ``messages[].content.tool_results[]`` with each entry shaped as
``{call: {name, arguments}, output, ...}``. Older sessions silently lack the
``tool_results`` key — return [] in that case.
"""
from __future__ import annotations

import json
from pathlib import Path

from ._common import find_recent_file, truncate

ROOTS = [
    Path.home() / ".config" / "aichat" / "sessions",
    Path.home() / ".local" / "share" / "aichat" / "sessions",
]


def parse(task_id: str, ts: str, session_id: str | None = None) -> list[dict]:
    candidate = find_recent_file(ROOTS, ts, "*.yaml", window_minutes=15)
    if candidate is None:
        return []
    try:
        import yaml  # type: ignore
    except ImportError:
        return []
    try:
        data = yaml.safe_load(candidate.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for msg in data.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        # MessageContent is `#[serde(untagged)]` — look for the structural marker.
        results = None
        if isinstance(content, dict) and "tool_results" in content:
            results = content.get("tool_results")
        if not isinstance(results, list):
            continue
        for tr in results:
            if not isinstance(tr, dict):
                continue
            call = tr.get("call") if isinstance(tr.get("call"), dict) else {}
            out.append({
                "ts": None,
                "kind": "tool",
                "name": str(call.get("name") or "?"),
                "args_summary": truncate(
                    json.dumps(call.get("arguments") or {}, ensure_ascii=False)
                ),
            })
    return out
