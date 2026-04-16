"""
Context gathering for council deliberation.
Collects project context (CLAUDE.md, git diff) to inject into agent prompts,
ensuring all agents reason over the same shared baseline.
"""

import os
import asyncio
import sys
from pathlib import Path


# Max context size in characters (~8K tokens at 3.5 chars/token)
MAX_CONTEXT_CHARS = 28000

# Files to look for as project context (checked in order)
CONTEXT_FILES = [
    "CLAUDE.md",
    ".claude/CLAUDE.md",
]


async def _run_command(cmd: list[str], cwd: str | None = None, timeout: int = 10) -> str | None:
    """Run a command and return stdout, or None on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0:
            return stdout.decode("utf-8", errors="replace").strip()
    except (asyncio.TimeoutError, FileNotFoundError, Exception):
        pass
    return None


def _read_claude_md(working_dir: str) -> str | None:
    """Read CLAUDE.md from the working directory, extracting key sections."""
    for filename in CONTEXT_FILES:
        path = Path(working_dir) / filename
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                # Truncate if too long — keep the top (most important sections)
                if len(content) > MAX_CONTEXT_CHARS // 2:
                    content = content[: MAX_CONTEXT_CHARS // 2] + "\n\n[... truncated]"
                return content
            except Exception:
                continue
    return None


async def _get_git_diff(working_dir: str) -> str | None:
    """Get staged + unstaged git diff, if in a git repo."""
    diff = await _run_command(
        ["git", "diff", "--stat", "--no-color", "HEAD"],
        cwd=working_dir,
        timeout=5,
    )
    if not diff:
        return None
    # Also get the actual diff, but limited
    full_diff = await _run_command(
        ["git", "diff", "--no-color", "HEAD", "--", ".", ":(exclude)*.lock", ":(exclude)*.json"],
        cwd=working_dir,
        timeout=10,
    )
    if full_diff and len(full_diff) > MAX_CONTEXT_CHARS // 3:
        full_diff = full_diff[: MAX_CONTEXT_CHARS // 3] + "\n\n[... diff truncated]"
    return full_diff or diff


async def _get_git_branch(working_dir: str) -> str | None:
    """Get current git branch name."""
    return await _run_command(
        ["git", "branch", "--show-current"],
        cwd=working_dir,
        timeout=3,
    )


async def gather_context(working_dir: str) -> str | None:
    """
    Gather project context for council prompts.

    Returns a formatted context string to inject into agent prompts,
    or None if no context is available.

    Context is capped at MAX_CONTEXT_CHARS to prevent context rot.
    """
    parts = []

    # Gather in parallel
    claude_md = _read_claude_md(working_dir)
    branch, diff = await asyncio.gather(
        _get_git_branch(working_dir),
        _get_git_diff(working_dir),
    )

    if branch:
        parts.append(f"Current branch: {branch}")

    if claude_md:
        parts.append(f"PROJECT INSTRUCTIONS (CLAUDE.md):\n{claude_md}")

    if diff:
        parts.append(f"CURRENT CHANGES (git diff):\n{diff}")

    if not parts:
        return None

    context = "\n\n".join(parts)

    # Final truncation safety
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n[... context truncated]"

    return context
