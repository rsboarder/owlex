"""Harvest objectively-labeled corpus items from owlex git history + docs.

Three miners produce items in the seeded-manifest item shape so they can be
fed straight into bench/run.py alongside the seeded corpus:

  mine_fix_commits  — genuine bug-fix commits → labeled bugs
  mine_solution_docs — docs/solutions + CLAUDE.md "Learned Patterns" → documented bugs
  mine_decoys       — refactor/chore commits → decoy items (no bugs, FP-catchers)

Run ``python bench/mine_fixes.py --out bench/corpus/mined/manifest.json`` to
build the mined corpus from the current repo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Ensure the repo root is on sys.path so ``bench.corpus`` is importable whether
# this module is run directly (``python bench/mine_fixes.py``) or as a package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bench.corpus import reverse_unified_diff, added_lines_by_file  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], repo: str = ".") -> str:
    result = subprocess.run(
        ["git"] + args,
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _short_sha(full_sha: str) -> str:
    return full_sha[:7]


def _changed_py_files(diff_text: str) -> list[str]:
    """Return list of Python files touched in a unified diff."""
    files = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/") and line.endswith(".py"):
            files.append(line[6:])
    return files


def _count_changed_lines(diff_text: str) -> int:
    """Count added + removed lines (excluding diff headers)."""
    count = 0
    for line in diff_text.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            count += 1
    return count


def _diff_size_label(changed_lines: int) -> str:
    """S <20, M <80, L ≥80."""
    if changed_lines < 20:
        return "S"
    if changed_lines < 80:
        return "M"
    return "L"


def _first_changed_line(diff_text: str, target_file: str) -> int:
    """Return the approximate pre-fix line number of the first hunk in target_file.

    Parses ``--- a/<file>`` context to find the right hunk, then reads the
    ``@@ -<line> @@`` header.  Falls back to 1 when parsing fails.
    """
    hunk_re = re.compile(r"^@@ -(\d+)")
    in_file = False
    for line in diff_text.splitlines():
        if line.startswith("--- a/"):
            in_file = line[6:].strip() == target_file
        if in_file:
            m = hunk_re.match(line)
            if m:
                return int(m.group(1))
    return 1


_SUBJECT_TO_BUG_TYPE = [
    (re.compile(r"\btimeout\b", re.I),      "resource"),
    (re.compile(r"\bleak\b", re.I),         "resource"),
    (re.compile(r"\bkill\b", re.I),         "resource"),
    (re.compile(r"\bsecurity\b|\bvuln", re.I), "security"),
    (re.compile(r"\bregress", re.I),         "regression"),
    (re.compile(r"\bjson\b|\bparse\b|\bencod", re.I), "parsing"),
    (re.compile(r"\bpath\b|\bbinary\b|\benv\b|\bmodel\b", re.I), "config"),
    (re.compile(r"\bsession\b|\bauth\b", re.I), "logic"),
    (re.compile(r"\bcrash\b|\bNameError\b|\bAttributeError\b", re.I), "none_deref"),
]


def infer_bug_type(subject: str) -> str:
    """Infer a bug_type label from a commit subject line."""
    for pattern, label in _SUBJECT_TO_BUG_TYPE:
        if pattern.search(subject):
            return label
    return "logic"


def infer_risk_domain(subject: str, file_path: str) -> str:
    """Infer a risk_domain from commit subject + primary file path."""
    combined = subject + " " + file_path
    if re.search(r"\bsubprocess\b|\bprocess\b|\bkill\b|\btimeout\b", combined, re.I):
        return "subprocess"
    if re.search(r"\bcouncil\b", combined, re.I):
        return "council"
    if re.search(r"\bagent\b|\bcursor\b|\bgemini\b|\bcodex\b|\bclaudeor\b", combined, re.I):
        return "agent"
    if re.search(r"\bserver\b|\bmcp\b", combined, re.I):
        return "server"
    if re.search(r"\bsession\b", combined, re.I):
        return "session"
    if re.search(r"\bjson\b|\bpars\b|\bencod", combined, re.I):
        return "parsing"
    if re.search(r"\benv\b|\bconfig\b|\bmodel\b", combined, re.I):
        return "config"
    return "unknown"


_FIX_COMMIT_RE = re.compile(
    r"^(fix[:\( ]|revert\b)|(\bbug\b|\bregression\b)",
    re.I,
)
_NON_FIX_PREFIX_RE = re.compile(
    r"^(feat|docs|chore|test)[:\( ]",
    re.I,
)


def _is_genuine_fix(subject: str) -> bool:
    """Return True when the commit subject describes a real bug fix.

    Keeps ``fix:``/``Fix ...`` subjects and subjects with bare ``bug`` or
    ``regression`` word.  Excludes ``feat:``/``docs:``/``chore:``/``test:``
    prefixed commits (the audit commits that contain "fix" as a substring of a
    ``feat(bench)`` title are false-positives from the raw grep).
    """
    if _NON_FIX_PREFIX_RE.match(subject):
        return False
    return bool(_FIX_COMMIT_RE.search(subject))


# ---------------------------------------------------------------------------
# Miner 1 — genuine bug-fix commits
# ---------------------------------------------------------------------------

def mine_fix_commits(repo: str = ".", max_items: int | None = None) -> list[dict]:
    """For each genuine bug-fix commit, emit a labeled item describing the bug.

    POLARITY fix: the item's ``diff`` is the *reversed* fix-commit diff so the
    reviewer sees the pre-fix (buggy) code as ``+`` (added) lines.  The bug
    label's ``file``/``line`` is anchored to the first added (buggy) line in
    the reversed diff via ``added_lines_by_file``.
    """
    raw = _git(
        ["log", "--format=%H %s", "-i",
         "--grep=fix", "--grep=bug", "--grep=regress", "--grep=revert"],
        repo,
    )
    items: list[dict] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        full_sha, _, subject = line.partition(" ")
        if not _is_genuine_fix(subject):
            continue

        fix_diff = _git(["show", full_sha], repo)
        py_files = _changed_py_files(fix_diff)
        if not py_files:
            continue  # docs-only or json-only — no code anchor

        primary_file = py_files[0]
        changed_lines = _count_changed_lines(fix_diff)

        # Reverse the diff so the buggy (pre-fix) code appears as added lines
        reversed_diff = reverse_unified_diff(fix_diff)

        # Anchor the bug label at the first added (buggy) line in the reversed diff
        added = added_lines_by_file(reversed_diff)
        if primary_file in added and added[primary_file]:
            bug_line = min(added[primary_file])
        else:
            bug_line = 1

        body_lines = _git(["show", "-s", "--format=%B", full_sha], repo).splitlines()
        first_body = body_lines[1].strip() if len(body_lines) > 1 else ""
        # Description reads as a defect in the pre-fix code, not the fix itself
        description = subject + " — buggy code before fix" + (f": {first_body}" if first_body else "")

        item = {
            "id": f"fix-{_short_sha(full_sha)}",
            "file": primary_file,
            "diff": reversed_diff,
            "bugs": [
                {
                    "bug_type": infer_bug_type(subject),
                    "file": primary_file,
                    "line": bug_line,
                    "description": description,
                }
            ],
            "source": "real-fix",
            "lang": "python",
            "diff_size": _diff_size_label(changed_lines),
            "risk_domain": infer_risk_domain(subject, primary_file),
            "difficulty": "medium",
            "split": "iterate",
            "provenance": {
                "source": "git",
                "sha": full_sha,
                "subject": subject,
            },
        }
        items.append(item)
        if max_items and len(items) >= max_items:
            break
    return items


# ---------------------------------------------------------------------------
# Miner 2 — documented bugs from docs/solutions + CLAUDE.md Learned Patterns
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    """Turn a title/path into a short identifier slug."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]


def _extract_code_file_from_doc(text: str) -> str:
    """Find the first owlex/*.py reference in the document text."""
    m = re.search(r"owlex/[\w/]+\.py", text)
    return m.group(0) if m else "owlex/unknown.py"


def _problem_statement(doc_text: str) -> str:
    """Extract the first non-empty line after a '## Problem' heading."""
    in_problem = False
    for line in doc_text.splitlines():
        if re.match(r"^##\s+Problem", line):
            in_problem = True
            continue
        if in_problem and line.strip():
            return line.strip()[:200]
    return ""


def mine_solution_docs(repo: str = ".") -> list[dict]:
    """Emit labeled items from docs/solutions/**/*.md + CLAUDE.md Learned Patterns."""
    items: list[dict] = []
    repo_path = Path(repo).resolve()

    # ---- docs/solutions/**/*.md ----
    solutions_root = repo_path / "docs" / "solutions"
    if solutions_root.is_dir():
        for md_path in sorted(solutions_root.rglob("*.md")):
            # Skip shadow-eval/architecture survey docs — they're not bug docs
            if "shadow" in md_path.name:
                continue
            text = md_path.read_text(encoding="utf-8")
            title_m = re.match(r"^#\s+(.+)", text)
            title = title_m.group(1).strip() if title_m else md_path.stem
            code_file = _extract_code_file_from_doc(text)
            problem = _problem_statement(text) or title
            slug = _slug(md_path.stem[:30])
            item = {
                "id": f"doc-{slug}",
                "file": code_file,
                "diff": "",
                "bugs": [
                    {
                        "bug_type": _infer_bug_type_from_doc(title + " " + problem),
                        "file": code_file,
                        "line": 1,
                        "description": f"{title}: {problem}",
                    }
                ],
                "source": "real-fix",
                "lang": "python",
                "diff_size": "M",
                "risk_domain": infer_risk_domain(title, code_file),
                "difficulty": "hard",
                "split": "iterate",
                "label_kind": "documented",
                "provenance": {
                    "source": "docs/solutions",
                    "path": str(md_path.relative_to(repo_path)),
                },
            }
            items.append(item)

    # ---- CLAUDE.md Learned Patterns ----
    claude_md = repo_path / "CLAUDE.md"
    if claude_md.exists():
        text = claude_md.read_text(encoding="utf-8")
        items.extend(_mine_claude_md_patterns(text, str(claude_md.relative_to(repo_path))))

    return items


def _infer_bug_type_from_doc(text: str) -> str:
    """Infer bug_type from a docs title/problem statement."""
    if re.search(r"\btask\b.*\bcancel\b|\bfire-and-forget\b|\bcreate_task\b", text, re.I):
        return "race_condition"
    if re.search(r"\bPath\.home\b|\bpollut\b|\bprod\b.*\btest\b", text, re.I):
        return "config"
    if re.search(r"\bProtocol\b|\bcross.layer\b|\brefactor\b.*\battribute\b", text, re.I):
        return "none_deref"
    if re.search(r"\bcatalog\b|\bmodel.rotat\b|\bhardcod\b", text, re.I):
        return "config"
    if re.search(r"\bfail.pattern\b|\bstdout\b.*\bdiscard\b|\bkill\b.*\banswer\b", text, re.I):
        return "logic"
    return "logic"


def _mine_claude_md_patterns(text: str, source_path: str) -> list[dict]:
    """Extract individual Learned Patterns entries from CLAUDE.md."""
    items: list[dict] = []
    # The Learned Patterns block is inside a <details> section; each entry has
    # a ### heading followed by **Problem**: prose.
    pattern_blocks = re.split(r"###\s+", text)
    for block in pattern_blocks[1:]:  # skip pre-first-heading content
        lines = block.splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        if not title:
            continue
        # Extract first non-empty line after "**Problem**"
        problem = ""
        in_problem = False
        for line in lines[1:]:
            if re.match(r"\*\*Problem\*\*", line):
                in_problem = True
                continue
            if in_problem and line.strip() and not line.startswith("**"):
                problem = line.strip()[:200]
                break
        if not problem:
            problem = title

        code_file = _extract_code_file_from_doc(block) or "owlex/unknown.py"
        slug = _slug(title[:30])
        item = {
            "id": f"doc-claude-{slug}",
            "file": code_file,
            "diff": "",
            "bugs": [
                {
                    "bug_type": _infer_bug_type_from_doc(title + " " + problem),
                    "file": code_file,
                    "line": 1,
                    "description": f"{title}: {problem}",
                }
            ],
            "source": "real-fix",
            "lang": "python",
            "diff_size": "M",
            "risk_domain": infer_risk_domain(title, code_file),
            "difficulty": "hard",
            "split": "iterate",
            "label_kind": "documented",
            "provenance": {
                "source": "CLAUDE.md",
                "path": source_path,
            },
        }
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Miner 3 — refactor/chore decoys
# ---------------------------------------------------------------------------

_DECOY_COMMIT_RE = re.compile(r"\b(refactor|chore|cleanup|clean.up)\b", re.I)


def mine_decoys(repo: str = ".", max_items: int | None = None) -> list[dict]:
    """For each refactor/chore/cleanup commit emit a decoy item (no bugs, FP-catcher)."""
    raw = _git(
        ["log", "--format=%H %s", "-i",
         "--grep=refactor", "--grep=chore", "--grep=cleanup"],
        repo,
    )
    items: list[dict] = []
    seen_ids: set[str] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        full_sha, _, subject = line.partition(" ")
        if not _DECOY_COMMIT_RE.search(subject):
            continue

        diff_text = _git(["show", full_sha], repo)
        py_files = _changed_py_files(diff_text)
        if not py_files:
            continue  # docs/json-only commit — skip

        primary_file = py_files[0]
        changed_lines = _count_changed_lines(diff_text)
        first_line = _first_changed_line(diff_text, primary_file)
        item_id = f"decoy-{_short_sha(full_sha)}"
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        item = {
            "id": item_id,
            "file": primary_file,
            "diff": diff_text,
            "bugs": [],
            "decoys": [
                {
                    "file": primary_file,
                    "line": first_line,
                    "description": "refactor/chore — behavior-preserving, no defect: " + subject,
                }
            ],
            "source": "decoy",
            "lang": "python",
            "diff_size": _diff_size_label(changed_lines),
            "risk_domain": infer_risk_domain(subject, primary_file),
            "difficulty": "medium",
            "split": "iterate",
            "provenance": {
                "source": "git",
                "sha": full_sha,
                "subject": subject,
            },
        }
        items.append(item)
        if max_items and len(items) >= max_items:
            break
    return items


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mine objectively-labeled corpus items from owlex git history + docs."
    )
    parser.add_argument(
        "--out",
        default="bench/corpus/mined/manifest.json",
        help="Output manifest path (default: bench/corpus/mined/manifest.json)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=None,
        metavar="N",
        help="Cap on items per miner (useful for quick smoke-tests)",
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Repo root (default: current directory)",
    )
    args = parser.parse_args()

    fix_items = mine_fix_commits(repo=args.repo, max_items=args.max)
    doc_items = mine_solution_docs(repo=args.repo)
    decoy_items = mine_decoys(repo=args.repo, max_items=args.max)

    all_items = fix_items + doc_items + decoy_items

    manifest = {
        "schema_version": 1,
        "kind": "mined",
        "description": (
            "Mined corpus: real bug-fix commits (source=real-fix) + "
            "documented bugs from docs/solutions + CLAUDE.md (label_kind=documented) + "
            "refactor/chore decoys (source=decoy). "
            "Generated by bench/mine_fixes.py from owlex git history."
        ),
        "items": all_items,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    n_fix = sum(1 for i in all_items if i["source"] == "real-fix" and i.get("label_kind") != "documented")
    n_doc = sum(1 for i in all_items if i.get("label_kind") == "documented")
    n_decoy = sum(1 for i in all_items if i["source"] == "decoy")
    print(f"Mined corpus written to {out_path}")
    print(f"  fix commits : {n_fix}")
    print(f"  doc items   : {n_doc}")
    print(f"  decoys      : {n_decoy}")
    print(f"  total       : {len(all_items)}")


if __name__ == "__main__":
    main()
