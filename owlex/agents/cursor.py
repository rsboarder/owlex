"""
Cursor Agent CLI runner.
Uses the Cursor Agent CLI (https://cursor.com/cli) for AI-powered coding assistance.
"""

import asyncio
import re
from pathlib import Path
from typing import Callable

from ..config import config
from .base import AgentRunner, AgentCommand


async def get_latest_cursor_session(
    working_directory: str | None = None,
    since_mtime: float | None = None,
    max_retries: int = 3,
    retry_delay: float = 0.3,
) -> str | None:
    """
    Find the most recent Cursor Agent session ID from filesystem.

    Cursor stores chats in ~/.cursor/chats/<project-hash>/<uuid>/
    """
    chats_dir = Path.home() / ".cursor" / "chats"
    if not chats_dir.exists():
        return None

    for attempt in range(max_retries):
        latest_dir: Path | None = None
        latest_mtime: float = 0

        try:
            for project_dir in chats_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                for session_dir in project_dir.iterdir():
                    if not session_dir.is_dir():
                        continue
                    try:
                        mtime = session_dir.stat().st_mtime
                        if since_mtime is not None and mtime < since_mtime:
                            continue
                        if mtime > latest_mtime:
                            latest_mtime = mtime
                            latest_dir = session_dir
                    except OSError:
                        continue
        except OSError:
            pass

        if latest_dir is not None:
            return latest_dir.name

        if attempt < max_retries - 1:
            await asyncio.sleep(retry_delay)

    return None


def clean_cursor_output(raw_output: str, original_prompt: str = "") -> str:
    """Clean Cursor Agent CLI output."""
    if not config.cursor.clean_output:
        return raw_output
    cleaned = raw_output
    # Remove excessive newlines
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


class CursorRunner(AgentRunner):
    """Runner for Cursor Agent CLI."""

    @property
    def name(self) -> str:
        return "cursor"

    @property
    def cli_command(self) -> str:
        return "agent"

    @property
    def output_prefix(self) -> str:
        return "Cursor Output"

    def build_exec_command(
        self,
        prompt: str,
        working_directory: str | None = None,
        enable_search: bool = False,
        model_override: str | None = None,
        **kwargs,
    ) -> AgentCommand:
        """Build command for starting a new Cursor Agent session."""
        full_command = ["agent", "--print", "--output-format", "text", "--trust"]

        # Model selection: explicit override > config > default
        model = model_override or config.cursor.model
        if model:
            full_command.extend(["--model", model])

        # Force mode (auto-approve commands)
        if config.cursor.force_mode:
            full_command.append("--force")

        # Set workspace directory
        if working_directory:
            full_command.extend(["--workspace", working_directory])

        # Prompt as positional arg
        full_command.append(prompt)

        return AgentCommand(
            command=full_command,
            prompt="",  # Prompt is in command args
            cwd=working_directory,
            output_prefix="Cursor Output",
            not_found_hint="Please install Cursor Agent CLI: curl https://cursor.com/install -fsSL | bash",
            stream=False,  # Print mode doesn't stream
            model=model,
        )

    def build_resume_command(
        self,
        session_ref: str,
        prompt: str,
        working_directory: str | None = None,
        enable_search: bool = False,
        model_override: str | None = None,
        **kwargs,
    ) -> AgentCommand:
        """Build command for resuming an existing Cursor Agent session."""
        full_command = ["agent", "--print", "--output-format", "text", "--trust"]

        # Model selection: explicit override > config > default
        model = model_override or config.cursor.model
        if model:
            full_command.extend(["--model", model])

        # Force mode
        if config.cursor.force_mode:
            full_command.append("--force")

        # Resume with session reference
        if session_ref == "--continue" or session_ref == "continue":
            full_command.append("--continue")
        else:
            if session_ref.startswith("-"):
                raise ValueError(f"Invalid session_ref: '{session_ref}' - cannot start with '-'")
            full_command.extend(["--resume", session_ref])

        # Set workspace directory
        if working_directory:
            full_command.extend(["--workspace", working_directory])

        # Prompt as positional arg
        full_command.append(prompt)

        return AgentCommand(
            command=full_command,
            prompt="",  # Prompt is in command args
            cwd=working_directory,
            output_prefix="Cursor Output",
            not_found_hint="Please install Cursor Agent CLI: curl https://cursor.com/install -fsSL | bash",
            stream=False,
            model=model,
        )

    def get_output_cleaner(self) -> Callable[[str, str], str]:
        return clean_cursor_output

    async def parse_session_id(
        self,
        output: str,
        since_mtime: float | None = None,
        working_directory: str | None = None,
    ) -> str | None:
        """
        Get session ID for Cursor Agent.

        Cursor stores chats in project-specific directories with UUID names.
        """
        return await get_latest_cursor_session(
            working_directory=working_directory,
            since_mtime=since_mtime,
        )
