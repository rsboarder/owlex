"""
Context gathering for council deliberation.
Extracts relevant project knowledge (learned patterns, conventions, architecture)
from CLAUDE.md and injects it into agent prompts as shared context.
"""

import re
from pathlib import Path

# Max context size in characters (~4K tokens at 3.5 chars/token)
MAX_CONTEXT_CHARS = 14000
MAX_RELEVANT_PATTERNS = 15

CONTEXT_FILES = [
    "CLAUDE.md",
    ".claude/CLAUDE.md",
]


def _read_file(working_dir: str, filename: str) -> str | None:
    path = Path(working_dir) / filename
    if path.exists():
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    return None


def _extract_project_basics(content: str) -> str:
    """Extract the project header, tech stack, and key conventions from CLAUDE.md."""
    lines = content.split("\n")
    basics = []
    in_section = False
    in_details = False
    sections_to_capture = {
        "# ", "**Package Manager**", "**Technology Stack**",
        "## Essential Commands", "### Directory Structure", "### Key Conventions",
    }

    for line in lines:
        # Skip <details> sections (learned patterns, logging, etc.)
        if "<details>" in line:
            in_details = True
            continue
        if "</details>" in line:
            in_details = False
            continue
        if in_details:
            continue

        # Capture project basics
        if any(line.strip().startswith(s) for s in sections_to_capture):
            in_section = True
        elif line.startswith("## ") and in_section:
            # New top-level section — stop unless it's one we want
            if not any(s in line for s in ["Essential Commands", "Git Workflow"]):
                in_section = False

        if in_section:
            basics.append(line)

    return "\n".join(basics).strip()


def _extract_patterns(content: str) -> list[dict]:
    """Extract learned patterns from CLAUDE.md's pattern tables."""
    patterns = []
    current_category = None

    for line in content.split("\n"):
        cat_match = re.match(r"### (.+?) \(\d+\)", line)
        if cat_match:
            current_category = cat_match.group(1)
            continue

        row_match = re.match(r"\| (\d+) \| (.+?) \| (.+?) \|", line)
        if row_match and current_category:
            num = row_match.group(1).strip()
            name = row_match.group(2).strip()
            rule = row_match.group(3).strip()
            if num.isdigit():
                patterns.append({
                    "id": num,
                    "category": current_category,
                    "name": name,
                    "rule": rule,
                    # Build searchable text for relevance matching
                    "keywords": f"{name} {rule} {current_category}".lower(),
                })

    return patterns


def _score_relevance(pattern: dict, question_lower: str) -> int:
    """Score how relevant a pattern is to a question (higher = more relevant)."""
    score = 0
    keywords = pattern["keywords"]

    # Split question into significant words
    question_words = set()
    for word in question_lower.split():
        cleaned = word.strip(".,;:!?()\"'`")
        if len(cleaned) > 3:
            question_words.add(cleaned)

    # Count keyword matches
    for word in question_words:
        if word in keywords:
            score += 2
        # Partial matches (e.g., "redis" matches "redis-based")
        elif any(word in kw for kw in keywords.split()):
            score += 1

    # Boost for category-level matches
    category = pattern["category"].lower()
    category_keywords = {
        "database": ["prisma", "migration", "query", "schema", "sql", "neon", "database", "db"],
        "ui / css": ["css", "component", "ui", "layout", "style", "button", "modal", "visual"],
        "logic / state": ["state", "zustand", "store", "cache", "redis", "api", "route", "filter"],
        "testing": ["test", "e2e", "playwright", "mock", "jest", "assert"],
        "integrations": ["telegram", "qstash", "webhook", "notification", "push", "bot"],
        "architecture / design": ["architecture", "pattern", "service", "domain", "monorepo", "shared"],
    }

    for cat_name, cat_words in category_keywords.items():
        if cat_name in category:
            if any(w in question_lower for w in cat_words):
                score += 3
            break

    return score


def _select_relevant_patterns(
    patterns: list[dict],
    question: str,
    max_patterns: int = MAX_RELEVANT_PATTERNS,
) -> list[dict]:
    """Select the most relevant patterns for a given question."""
    question_lower = question.lower()

    scored = [(p, _score_relevance(p, question_lower)) for p in patterns]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Only include patterns with some relevance
    relevant = [(p, s) for p, s in scored if s > 0]
    return [p for p, _ in relevant[:max_patterns]]


async def gather_context(working_dir: str, question: str | None = None) -> str | None:
    """
    Gather targeted project context for council prompts.

    Instead of dumping the full CLAUDE.md, extracts:
    1. Project basics (tech stack, directory structure, key conventions)
    2. Learned patterns relevant to the question (keyword-matched)

    Args:
        working_dir: Project directory to read CLAUDE.md from
        question: The council question (used to select relevant patterns)

    Returns:
        Formatted context string, or None if no context available.
    """
    # Read CLAUDE.md
    content = None
    for filename in CONTEXT_FILES:
        content = _read_file(working_dir, filename)
        if content:
            break

    if not content:
        return None

    parts = []

    # 1. Project basics (compact)
    basics = _extract_project_basics(content)
    if basics:
        parts.append(basics)

    # 2. Relevant learned patterns
    all_patterns = _extract_patterns(content)
    if all_patterns and question:
        relevant = _select_relevant_patterns(all_patterns, question)
        if relevant:
            pattern_lines = ["RELEVANT PROJECT LEARNINGS:"]
            for p in relevant:
                pattern_lines.append(f"- [{p['category']}] {p['name']}: {p['rule']}")
            parts.append("\n".join(pattern_lines))

    if not parts:
        return None

    context = "\n\n".join(parts)

    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n[... context truncated]"

    return context
