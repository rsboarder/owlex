"""Parse Codex CLI rollout files (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl).

Two kinds of invocations are emitted:
- ``kind='tool'``  — every ``function_call`` (mostly ``exec_command``).
- ``kind='skill'`` — inferred when an ``exec_command`` reads ``<name>/SKILL.md``.
  Codex's own skill-usage dashboard derives counts the same way.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ._common import find_recent_file, truncate

ROOTS = [Path.home() / ".codex" / "sessions"]

_SKILL_MD_RE = re.compile(r"([A-Za-z0-9_\-]+)/SKILL\.md", re.IGNORECASE)


def parse(task_id: str, ts: str, session_id: str | None = None) -> list[dict]:
    rollout = find_recent_file(ROOTS, ts, "rollout-*.jsonl", window_minutes=15)
    if rollout is None:
        return []
    out: list[dict] = []
    with open(rollout, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else None
            ts = entry.get("timestamp") or entry.get("ts")
            # Newer codex schema: response_item -> payload.type == 'function_call'
            if payload and payload.get("type") == "function_call":
                name = payload.get("name") or "?"
                args = payload.get("arguments") or payload.get("input")
                args_str = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
                out.append({
                    "ts": ts,
                    "kind": "tool",
                    "name": str(name),
                    "args_summary": truncate(args_str),
                })
                # Promote SKILL.md reads to a kind='skill' invocation so the dashboard
                # can show real skill usage independently of the underlying tool calls.
                if name == "exec_command" and isinstance(args_str, str):
                    seen: set[str] = set()
                    for m in _SKILL_MD_RE.finditer(args_str):
                        skill_name = m.group(1)
                        if skill_name in seen:
                            continue
                        seen.add(skill_name)
                        out.append({
                            "ts": ts,
                            "kind": "skill",
                            "name": skill_name,
                            "args_summary": truncate(args_str),
                        })
                continue
            # Legacy / variant shapes still in the wild
            etype = entry.get("type") or entry.get("event_type")
            if etype in ("function_call", "tool_call", "shell_command"):
                p = payload or entry
                name = p.get("name") or p.get("tool") or etype
                args = p.get("arguments") or p.get("command") or p.get("input")
                out.append({
                    "ts": ts,
                    "kind": "tool",
                    "name": str(name),
                    "args_summary": truncate(args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)),
                })
    return out
