"""
Gemini CLI agent runner.
"""

import asyncio
import hashlib
import os
import re
from pathlib import Path
from typing import Callable

from ..config import config
from .base import AgentRunner, AgentCommand

# Patterns in stderr that indicate fatal errors — kill process immediately
GEMINI_FAIL_PATTERNS = [
    "QUOTA_EXHAUSTED",
    "MODEL_CAPACITY_EXHAUSTED",
    "You have exhausted your capacity",
]


def _get_gemini_project_hash(working_directory: str) -> str:
    """
    Compute the project hash that Gemini uses for session storage.

    Gemini stores sessions in ~/.gemini/tmp/<hash>/chats/
    The hash is derived from the working directory path.

    Uses os.path.abspath() instead of Path.resolve() to match CLI behavior
    (resolve() follows symlinks which may produce different paths).
    """
    # Gemini uses SHA256 of the absolute path
    abs_path = os.path.abspath(working_directory)
    return hashlib.sha256(abs_path.encode()).hexdigest()


def _get_stable_tmpdir(working_directory: str) -> str:
    """
    Get a stable tmpdir for Gemini based on working directory.

    Uses a deterministic path so R1 and R2 share the same cwd,
    which means Gemini's session hash matches across rounds.
    """
    dir_hash = hashlib.md5(working_directory.encode()).hexdigest()[:12]
    tmpdir = Path(os.environ.get("TMPDIR", "/tmp")) / f"owlex-gemini-{dir_hash}"
    tmpdir.mkdir(parents=True, exist_ok=True)
    return str(tmpdir)


async def get_gemini_session_for_project(
    working_directory: str | None = None,
    since_mtime: float | None = None,
    max_retries: int = 3,
    retry_delay: float = 0.3,
) -> bool:
    """
    Check if a Gemini session exists for the given project.

    Gemini stores sessions in ~/.gemini/tmp/<hash>/chats/session-*.json
    We check if a session file exists that was created after since_mtime.

    Args:
        working_directory: The project directory to scope to.
        since_mtime: Only consider sessions created after this timestamp.
        max_retries: Number of retries if no session found.
        retry_delay: Delay between retries in seconds.

    Returns:
        True if a valid session exists, False otherwise
    """
    gemini_dir = Path.home() / ".gemini" / "tmp"
    if not gemini_dir.exists():
        return False

    # Require working_directory for project-scoped session discovery
    # Without it, we could accidentally resume a session from a different project
    if not working_directory:
        return False

    project_hash = _get_gemini_project_hash(working_directory)

    for attempt in range(max_retries):
        project_dirs = [gemini_dir / project_hash]

        for project_dir in project_dirs:
            if not project_dir.exists():
                continue
            chats_dir = project_dir / "chats"
            if not chats_dir.exists():
                continue
            try:
                for session_file in chats_dir.glob("session-*.json"):
                    try:
                        mtime = session_file.stat().st_mtime
                        # Check if session was created after since_mtime
                        if since_mtime is None or mtime >= since_mtime:
                            return True
                    except OSError:
                        continue
            except OSError:
                continue

        # Retry with delay if no session found
        # Uses asyncio.sleep to avoid blocking the event loop
        if attempt < max_retries - 1:
            await asyncio.sleep(retry_delay)

    return False


def clean_gemini_output(raw_output: str, original_prompt: str = "") -> str:
    """Clean Gemini CLI output by removing noise."""
    if not config.gemini.clean_output:
        return raw_output
    cleaned = raw_output
    if cleaned.startswith("YOLO mode is enabled."):
        lines = cleaned.split('\n', 2)
        if len(lines) > 2:
            cleaned = lines[2]
        elif len(lines) > 1:
            cleaned = lines[1]
    cleaned = re.sub(r'^Loaded cached credentials\.\n?', '', cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


class GeminiRunner(AgentRunner):
    """Runner for Google Gemini CLI."""

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def cli_command(self) -> str:
        return "gemini"

    @property
    def output_prefix(self) -> str:
        return "Gemini Output"

    def build_exec_command(
        self,
        prompt: str,
        working_directory: str | None = None,
        enable_search: bool = False,  # Gemini doesn't have search flag
        **kwargs,
    ) -> AgentCommand:
        """Build command for starting a new Gemini session."""
        full_command = ["gemini"]

        # Use yolo mode to prevent tool approval hangs in non-interactive mode.
        # Run from a stable temp dir so gemini can't accidentally write to the project.
        # --include-directories gives read access to the actual project.
        full_command.extend(["--approval-mode", "yolo"])

        safe_cwd = _get_stable_tmpdir(working_directory) if working_directory else None
        if working_directory:
            full_command.extend(["--include-directories", working_directory])

        return AgentCommand(
            command=full_command,
            prompt=prompt,
            cwd=safe_cwd,
            output_prefix="Gemini Output",
            not_found_hint="Please ensure Gemini CLI is installed (npm install -g @google/gemini-cli).",
            stream=True,
            fail_patterns=GEMINI_FAIL_PATTERNS,
        )

    def build_resume_command(
        self,
        session_ref: str,
        prompt: str,
        working_directory: str | None = None,
        enable_search: bool = False,
        **kwargs,
    ) -> AgentCommand:
        """Build command for resuming an existing Gemini session."""
        full_command = ["gemini"]

        full_command.extend(["--approval-mode", "yolo"])

        # Reuse the same stable tmpdir from R1 so session hash matches
        safe_cwd = _get_stable_tmpdir(working_directory) if working_directory else None
        if working_directory:
            full_command.extend(["--include-directories", working_directory])

        full_command.extend(["-r", session_ref])

        return AgentCommand(
            command=full_command,
            prompt=prompt,
            cwd=safe_cwd,
            output_prefix="Gemini Resume Output",
            not_found_hint="Please ensure Gemini CLI is installed (npm install -g @google/gemini-cli).",
            stream=False,  # Resume uses non-streaming mode
            fail_patterns=GEMINI_FAIL_PATTERNS,
        )

    def get_output_cleaner(self) -> Callable[[str, str], str]:
        return clean_gemini_output

    async def parse_session_id(
        self,
        output: str,
        since_mtime: float | None = None,
        working_directory: str | None = None,
    ) -> str | None:
        """
        Get session ID for Gemini.

        Gemini CLI uses index numbers for resume (-r 1), not UUIDs.
        We return "1" (most recent by index) only if we verify that a session
        was actually created since since_mtime.

        Gemini stores sessions under a hash of its cwd. Since we use a stable
        tmpdir derived from working_directory, we look up sessions using that
        tmpdir path (not the project directory).
        """
        if not working_directory:
            return None

        # Gemini's session hash is based on its cwd (our stable tmpdir),
        # not the project directory
        gemini_cwd = _get_stable_tmpdir(working_directory)

        if await get_gemini_session_for_project(
            working_directory=gemini_cwd,
            since_mtime=since_mtime,
        ):
            return "1"
        return None

    def validate_session_id(self, session_id: str) -> bool:
        """
        Validate a Gemini session ID.

        Gemini uses numeric indices (1-indexed) or "latest" for session references.
        Index 0 is invalid as Gemini uses 1-based indexing.
        """
        if not session_id:
            return False
        # Accept numeric indices >= 1 (Gemini uses 1-indexed sessions)
        if session_id.isdigit():
            return int(session_id) >= 1
        # Accept "latest"
        if session_id == "latest":
            return True
        # Reject anything that looks like a flag
        if session_id.startswith("-"):
            return False
        return False
