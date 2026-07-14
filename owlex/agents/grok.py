"""
Grok Agent CLI runner.
Uses the Grok CLI (https://x.ai) for AI-powered analysis and coding assistance.
"""

import json
import re
from typing import Callable

from ..config import config
from .base import AgentRunner, AgentCommand


def parse_grok_text(raw_output: str) -> str:
    """Extract the ``text`` field from Grok's JSON output.

    ``grok --output-format json`` prints a single JSON object like
    ``{"text": "...", "stopReason": "EndTurn", "sessionId": "019f...", ...}``.
    Falls back to slicing at the last ``}`` for truncated/noisy output, and
    returns the raw string as a last resort.
    """
    try:
        outer = json.loads(raw_output)
        return str(outer.get("text", "") or "")
    except json.JSONDecodeError:
        last = raw_output.rfind("}")
        if last > 0:
            try:
                return str(json.loads(raw_output[: last + 1]).get("text", ""))
            except (json.JSONDecodeError, ValueError):
                pass
        return raw_output


def clean_grok_output(raw_output: str, original_prompt: str = "") -> str:
    """Clean Grok CLI output: parse the JSON envelope, then normalize whitespace."""
    text = parse_grok_text(raw_output)
    if not config.grok.clean_output:
        return text
    cleaned = re.sub(r'\n{3,}', '\n\n', text)
    return cleaned.strip()


class GrokRunner(AgentRunner):
    """Runner for Grok CLI (xAI)."""

    @property
    def name(self) -> str:
        return "grok"

    @property
    def cli_command(self) -> str:
        return "grok"

    @property
    def output_prefix(self) -> str:
        return "Grok Output"

    def build_exec_command(
        self,
        prompt: str,
        working_directory: str | None = None,
        enable_search: bool = False,
        model_override: str | None = None,
        **kwargs,
    ) -> AgentCommand:
        """Build command for starting a new Grok session."""
        model = model_override or config.grok.model
        full_command = [
            "grok", "-p", prompt,
            "--output-format", "json",
            "--always-approve",
            "--model", model,
            "--effort", config.grok.effort,
            "--disable-web-search",
        ]

        return AgentCommand(
            command=full_command,
            prompt="",  # Prompt is in command args
            cwd=working_directory,
            output_prefix="Grok Output",
            not_found_hint="Please install the Grok CLI (binary: grok). See: https://x.ai",
            stream=False,  # JSON output mode doesn't stream
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
        """Build command for resuming an existing Grok session."""
        if session_ref.startswith("-"):
            raise ValueError(f"Invalid session_ref: '{session_ref}' - cannot start with '-'")

        model = model_override or config.grok.model
        full_command = [
            "grok", "-r", session_ref, "-p", prompt,
            "--output-format", "json",
            "--always-approve",
            "--model", model,
            "--effort", config.grok.effort,
            "--disable-web-search",
        ]

        return AgentCommand(
            command=full_command,
            prompt="",  # Prompt is in command args
            cwd=working_directory,
            output_prefix="Grok Output",
            not_found_hint="Please install the Grok CLI (binary: grok). See: https://x.ai",
            stream=False,
            model=model,
        )

    def get_output_cleaner(self) -> Callable[[str, str], str]:
        return clean_grok_output
