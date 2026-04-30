"""Parse Claude Code transcripts (~/.claude/projects/*/<session>.jsonl).

Claude transcripts contain assistant messages with ``content`` blocks of type
``tool_use``. We surface every tool call, tagging Skill invocations specifically.
We also infer skill activations from Read tool calls that target a
``<name>/SKILL.md`` path (matching how codex's dashboard does it).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ._common import find_recent_file, truncate

ROOTS = [Path.home() / ".claude" / "projects"]

_SKILL_MD_RE = re.compile(r"([A-Za-z0-9_\-]+)/SKILL\.md", re.IGNORECASE)


def parse(task_id: str, ts: str, session_id: str | None = None) -> list[dict]:
    transcript = find_recent_file(ROOTS, ts, "*.jsonl", window_minutes=15)
    if transcript is None:
        return []
    out: list[dict] = []
    with open(transcript, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = entry.get("message") or entry
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name") or "?"
                inp = block.get("input") or {}
                if name == "Skill":
                    out.append({
                        "ts": entry.get("timestamp"),
                        "kind": "skill",
                        "name": str(inp.get("skill") or inp.get("name") or "?"),
                        "args_summary": truncate(inp.get("args") or ""),
                    })
                else:
                    args_dump = json.dumps(inp, ensure_ascii=False)
                    out.append({
                        "ts": entry.get("timestamp"),
                        "kind": "tool",
                        "name": str(name),
                        "args_summary": truncate(args_dump),
                    })
                    # Infer skill activation from <name>/SKILL.md reads.
                    if name in ("Read", "Bash", "Grep", "Glob"):
                        seen: set[str] = set()
                        for m in _SKILL_MD_RE.finditer(args_dump):
                            skill_name = m.group(1)
                            if skill_name in seen:
                                continue
                            seen.add(skill_name)
                            out.append({
                                "ts": entry.get("timestamp"),
                                "kind": "skill",
                                "name": skill_name,
                                "args_summary": truncate(args_dump),
                            })
    return out
