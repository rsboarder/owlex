"""
OpenCode CLI agent runner.

GLM-5.2 seat (opt-in): activate via COUNCIL_SUBSTITUTION_MODELS, e.g.:
  COUNCIL_SUBSTITUTION_MODELS=aichat:opencode:zai/glm-5.2
Requires ~/.owlex/glm_token. See docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md.
"""

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Callable

from ..config import config
from .base import AgentRunner, AgentCommand


# --- GLM/Z.ai seat helpers --------------------------------------------------

def _is_glm_model(model: str | None) -> bool:
    """Return True when the resolved model targets Z.ai / GLM-5.2."""
    if not model:
        return False
    lower = model.lower()
    return "glm" in lower or "zai" in lower or lower.startswith("zai/")


def _read_glm_token() -> str | None:
    """Read the GLM API token from ~/.owlex/glm_token. Returns None on failure."""
    token_path = Path.home() / ".owlex" / "glm_token"
    try:
        token = token_path.read_text().strip()
        return token or None
    except OSError:
        return None


def _build_glm_env_overrides(variant: str) -> dict[str, str]:
    """Build env_overrides for a GLM/Z.ai opencode invocation.

    - GLM_TOKEN: read from ~/.owlex/glm_token at call time (fail loudly if missing).
    - XDG_DATA_HOME: fresh per-process tmpdir (avoids stale-DB crash in opencode 1.17.7).
    - OPENCODE_CONFIG: inline Z.ai provider config pointing to api.z.ai.
    """
    overrides: dict[str, str] = {}

    token = _read_glm_token()
    if token:
        overrides["GLM_TOKEN"] = token
    # If token is missing we leave GLM_TOKEN unset — the subprocess will fail
    # loudly (auth error) rather than the council startup crashing.

    # Fresh XDG_DATA_HOME per call so opencode never re-uses a stale session DB.
    xdg_dir = tempfile.mkdtemp(prefix="owlex_glm_xdg_")
    overrides["XDG_DATA_HOME"] = xdg_dir

    # Inline Z.ai provider config so opencode knows the endpoint + model.
    cfg_dir = tempfile.mkdtemp(prefix="owlex_glm_cfg_")
    cfg_path = os.path.join(cfg_dir, "opencode.json")
    cfg = {
        "permission": {"edit": "deny", "bash": "deny", "webfetch": "deny"},
        "provider": {
            "zai": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Z.ai (GLM Coding Plan)",
                "options": {
                    "baseURL": "https://api.z.ai/api/coding/paas/v4",
                    "apiKey": "{env:GLM_TOKEN}",
                },
                "models": {
                    "glm-5.2": {
                        "name": "GLM-5.2 (Z.ai)",
                        "reasoning": True,
                        "limit": {"context": 204800, "output": 131072},
                        "options": {
                            "reasoning_effort": variant,
                            "thinking": {"type": "enabled"},
                        },
                    }
                },
            }
        },
    }
    with open(cfg_path, "w") as fh:
        json.dump(cfg, fh)
    overrides["OPENCODE_CONFIG"] = cfg_path

    return overrides


def _get_opencode_project_id(working_directory: str) -> str | None:
    """
    Look up the projectID that OpenCode uses for session storage.

    OpenCode stores sessions in ~/.local/share/opencode/storage/session/<projectID>/
    The projectID is NOT computed from the path - it's stored in project config files
    at ~/.local/share/opencode/storage/project/<projectID>.json

    Returns None if no project found for the given working directory.
    """
    project_dir = Path.home() / ".local" / "share" / "opencode" / "storage" / "project"
    if not project_dir.exists():
        return None

    abs_path = os.path.normpath(os.path.abspath(os.path.expanduser(working_directory)))

    # Scan project files to find one matching our working directory
    try:
        for project_file in project_dir.glob("*.json"):
            if project_file.name == "global.json":
                continue  # Skip global project
            try:
                with open(project_file) as f:
                    project_data = json.load(f)
                    stored_worktree = project_data.get("worktree")
                    if not isinstance(stored_worktree, str):
                        continue
                    normalized_worktree = os.path.normpath(os.path.abspath(os.path.expanduser(stored_worktree)))
                    if normalized_worktree == abs_path:
                        project_id = project_data.get("id")
                        if not isinstance(project_id, str):
                            continue
                        return project_id
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                continue
    except OSError:
        return None

    return None


async def get_latest_opencode_session(
    working_directory: str | None = None,
    since_mtime: float | None = None,
    max_retries: int = 3,
    retry_delay: float = 0.3,
) -> str | None:
    """
    Find the most recent OpenCode session ID from filesystem.

    OpenCode stores sessions in ~/.local/share/opencode/storage/session/<project>/ses_*.json
    The session ID is extracted from the filename (without .json extension).

    Args:
        working_directory: Project directory to scope session search.
        since_mtime: Only consider sessions created after this timestamp.
        max_retries: Number of retries if no session found.
        retry_delay: Delay between retries in seconds.

    Returns:
        Session ID (e.g., ses_49b5d1b81ffeZfa2uTg3NVmKrH) if found, None otherwise
    """
    opencode_dir = Path.home() / ".local" / "share" / "opencode" / "storage" / "session"
    if not opencode_dir.exists():
        return None

    # Require working_directory for project-scoped session discovery
    # Without it, we could accidentally resume a session from a different project
    if not working_directory:
        return None

    # Look up the projectID from OpenCode's config files
    project_id = _get_opencode_project_id(working_directory)
    if not project_id:
        return None  # Project not found in OpenCode's registry

    for attempt in range(max_retries):
        latest_file: Path | None = None
        latest_mtime: float = 0

        project_dirs = [opencode_dir / project_id]

        for project_dir in project_dirs:
            if not project_dir.exists():
                continue
            try:
                for session_file in project_dir.glob("ses_*.json"):
                    try:
                        mtime = session_file.stat().st_mtime
                        # Skip files older than since_mtime if specified
                        if since_mtime is not None and mtime < since_mtime:
                            continue
                        if mtime > latest_mtime:
                            latest_mtime = mtime
                            latest_file = session_file
                    except OSError:
                        continue
            except OSError:
                continue

        if latest_file is not None:
            # Session ID is the filename without .json extension
            return latest_file.stem

        # Retry with delay if no session found
        # Uses asyncio.sleep to avoid blocking the event loop
        if attempt < max_retries - 1:
            await asyncio.sleep(retry_delay)

    return None


def clean_opencode_output(raw_output: str, original_prompt: str = "") -> str:
    """Clean OpenCode CLI output by removing noise."""
    if not config.opencode.clean_output:
        return raw_output
    cleaned = raw_output
    # Remove ANSI escape codes
    cleaned = re.sub(r'\x1b\[[0-9;]*m', '', cleaned)
    # Collapse multiple newlines
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


class OpenCodeRunner(AgentRunner):
    """Runner for OpenCode AI coding agent CLI."""

    @property
    def name(self) -> str:
        return "opencode"

    @property
    def cli_command(self) -> str:
        return "opencode"

    @property
    def output_prefix(self) -> str:
        return "OpenCode Output"

    def build_exec_command(
        self,
        prompt: str,
        working_directory: str | None = None,
        enable_search: bool = False,  # OpenCode doesn't have web search flag
        model_override: str | None = None,
        **kwargs,
    ) -> AgentCommand:
        """Build command for running OpenCode with a prompt.

        When model_override resolves to a GLM/Z.ai model, injects the Z.ai
        provider config and GLM_TOKEN env override instead of using --format json
        (which hangs on this provider). See docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md.
        """
        resolved_model = model_override or config.opencode.model
        full_command = ["opencode", "run"]

        if _is_glm_model(resolved_model):
            # GLM/Z.ai path: --model + --variant; no --format json (hangs on Z.ai).
            variant = os.environ.get("OWLEX_GLM_OC_VARIANT", "high")
            full_command.extend(["--model", resolved_model])
            full_command.extend(["--variant", variant])
            env_overrides = _build_glm_env_overrides(variant)
            full_command.append("--")
            full_command.append(prompt)
            return AgentCommand(
                command=full_command,
                prompt="",
                cwd=working_directory,
                output_prefix="OpenCode Output",
                not_found_hint="Please ensure OpenCode is installed (curl -fsSL https://opencode.ai/install | bash).",
                stream=True,
                env_overrides=env_overrides,
                model=resolved_model,
            )

        # Standard opencode path (non-GLM models).

        # Model configuration (format: provider/model)
        if resolved_model:
            full_command.extend(["--model", resolved_model])

        # Agent selection (e.g., "build", "plan")
        if config.opencode.agent:
            full_command.extend(["--agent", config.opencode.agent])

        # Output format
        if config.opencode.json_output:
            full_command.extend(["--format", "json"])

        # Use -- to signal end of options, preventing prompt-as-flag injection
        # This ensures prompts starting with - aren't parsed as CLI flags
        # Note: OpenCode doesn't support stdin input, so we must use positional args
        full_command.append("--")
        full_command.append(prompt)

        return AgentCommand(
            command=full_command,
            prompt="",  # Prompt is in command as positional arg
            cwd=working_directory,
            output_prefix="OpenCode Output",
            not_found_hint="Please ensure OpenCode is installed (curl -fsSL https://opencode.ai/install | bash).",
            stream=True,
            model=resolved_model,
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
        """Build command for resuming an existing OpenCode session."""
        resolved_model = model_override or config.opencode.model
        full_command = ["opencode", "run"]

        # Model configuration
        if resolved_model:
            full_command.extend(["--model", resolved_model])

        env_overrides: dict[str, str] | None = None
        if _is_glm_model(resolved_model):
            # GLM/Z.ai resume: inject provider env, no --format json.
            variant = os.environ.get("OWLEX_GLM_OC_VARIANT", "high")
            full_command.extend(["--variant", variant])
            env_overrides = _build_glm_env_overrides(variant)
        else:
            # Agent selection (non-GLM only; GLM config sets this via OPENCODE_CONFIG)
            if config.opencode.agent:
                full_command.extend(["--agent", config.opencode.agent])

            # Output format
            if config.opencode.json_output:
                full_command.extend(["--format", "json"])

        # Session resume
        if session_ref == "--continue" or session_ref == "latest":
            full_command.append("--continue")
        else:
            # Validate session_ref to prevent flag injection
            if session_ref.startswith("-"):
                raise ValueError(f"Invalid session_ref: '{session_ref}' - cannot start with '-'")
            full_command.extend(["--session", session_ref])

        # Use -- to signal end of options, preventing prompt-as-flag injection
        # Note: OpenCode doesn't support stdin input, so we must use positional args
        full_command.append("--")
        full_command.append(prompt)

        return AgentCommand(
            command=full_command,
            prompt="",  # Prompt is in command as positional arg
            cwd=working_directory,
            output_prefix="OpenCode Resume Output",
            not_found_hint="Please ensure OpenCode is installed (curl -fsSL https://opencode.ai/install | bash).",
            stream=False,  # Resume uses non-streaming mode
            env_overrides=env_overrides,
            model=resolved_model,
        )

    def get_output_cleaner(self) -> Callable[[str, str], str]:
        return clean_opencode_output

    async def parse_session_id(
        self,
        output: str,
        since_mtime: float | None = None,
        working_directory: str | None = None,
    ) -> str | None:
        """
        Get session ID for OpenCode.

        OpenCode doesn't output session ID in stdout, so we check the filesystem
        for the most recently created session file.

        Args:
            output: Ignored (OpenCode doesn't output session IDs)
            since_mtime: Only consider sessions created after this timestamp
            working_directory: Project directory to scope session search
        """
        return await get_latest_opencode_session(
            working_directory=working_directory,
            since_mtime=since_mtime,
        )
