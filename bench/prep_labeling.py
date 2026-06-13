"""Compact council-DB targets into a small labeling payload.

The raw council prompts (``bench/corpus/db/targets.json``) wrap a small embedded
code fence in a large, near-constant boilerplate preamble (the council role
instructions + full project AGENTS.md). The labeling step only needs the code
under review plus the actual question, so this strips the preamble and keeps the
fenced blocks + a trimmed question tail — turning ~5-30KB prompts into ~1-3KB
items that are cheap to fan out and sharp to label.

Read-only over the targets file; writes a compact ``labeling_input.json``.
"""
from __future__ import annotations

import argparse
import json
import os
import re


_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def extract_code(prompt_text: str) -> str:
    """Concatenate every fenced code block in the prompt (joined by a marker)."""
    blocks = [b.strip() for b in _FENCE_RE.findall(prompt_text or "") if b.strip()]
    return "\n\n# ---- next block ----\n\n".join(blocks)


def extract_question(prompt_text: str, max_chars: int = 1200) -> str:
    """Best-effort: the prose AFTER the last fenced block, trimmed.

    Council prompts put the boilerplate + project context first, the code in the
    middle, and the actual ask last — so the tail after the final fence is the
    highest-signal question. Falls back to the prompt's tail if no fence.
    """
    text = prompt_text or ""
    last = 0
    for m in _FENCE_RE.finditer(text):
        last = m.end()
    tail = text[last:].strip() if last else text[-max_chars:].strip()
    return tail[:max_chars]


def compact(targets: list[dict], max_code_chars: int = 8000) -> list[dict]:
    out: list[dict] = []
    for it in targets:
        code = extract_code(it.get("prompt_text", ""))
        if not code:
            continue  # no embedded code → nothing localizable to label
        out.append(
            {
                "id": it["id"],
                "council_id": it.get("council_id"),
                "code": code[:max_code_chars],
                "question": extract_question(it.get("prompt_text", "")),
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default="bench/corpus/db/targets.json")
    ap.add_argument("--out", default="bench/corpus/db/labeling_input.json")
    args = ap.parse_args()
    with open(args.targets, encoding="utf-8") as f:
        targets = json.load(f)["items"]
    items = compact(targets)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"kind": "labeling_input", "items": items}, f, ensure_ascii=False, indent=2)
    total_chars = sum(len(i["code"]) + len(i["question"]) for i in items)
    print(f"Compacted {len(items)}/{len(targets)} targets (with code) → {args.out}")
    print(f"Total payload chars: {total_chars} (~{total_chars // 4} tokens)")


if __name__ == "__main__":
    main()
