"""Helper functions used by tool modules — no MCP decorators here."""
from __future__ import annotations

import asyncio
import os
import sys

from ..models import TaskResponse, ErrorCode


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def validate_working_directory(working_directory: str | None) -> tuple[str | None, str | None]:
    """Returns (expanded_path, error_message)."""
    if not working_directory:
        return None, None
    expanded = os.path.expanduser(working_directory)
    if not os.path.isdir(expanded):
        return None, f"working_directory '{working_directory}' does not exist or is not a directory."
    return expanded, None


def council_recursion_block(tool_name: str) -> dict | None:
    """Refuse council_ask / rate_council when running inside another council."""
    try:
        depth = int(os.environ.get("OWLEX_COUNCIL_DEPTH", "0") or 0)
    except ValueError:
        depth = 0
    if depth > 0:
        return TaskResponse(
            success=False,
            error=(
                f"Recursive {tool_name} is not allowed (OWLEX_COUNCIL_DEPTH={depth}). "
                f"This owlex-server is running inside an active council; a participant "
                f"cannot spawn another council."
            ),
            error_code=ErrorCode.INVALID_ARGS,
        ).model_dump()
    return None


async def get_cli_version(cmd: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return stdout.decode().strip().split("\n")[0]
    except Exception:
        return "unknown"


def get_codex_model() -> str:
    import pathlib
    config_path = pathlib.Path.home() / ".codex" / "config.toml"
    try:
        if config_path.exists():
            content = config_path.read_text()
            for line in content.split("\n"):
                if line.startswith("model ="):
                    return line.split("=")[1].strip().strip("\"'")
    except Exception:
        pass
    return "default"


def get_gemini_model() -> str:
    return "gemini-2.5-pro"


def get_opencode_model() -> str:
    model = os.environ.get("OPENCODE_MODEL", "").strip()
    return model if model else "openrouter/anthropic/claude-sonnet-4"


def get_aichat_model() -> str:
    model = os.environ.get("AICHAT_MODEL", "").strip()
    return model if model else "default"


def get_cursor_model() -> str:
    model = os.environ.get("CURSOR_MODEL", "").strip()
    return model if model else "default"
