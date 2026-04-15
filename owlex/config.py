"""
Centralized configuration for owlex.
All settings are loaded from environment variables with sensible defaults.
"""

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class CodexConfig:
    """Configuration for Codex CLI integration."""
    bypass_approvals: bool = False
    clean_output: bool = True
    enable_search: bool = True


@dataclass(frozen=True)
class GeminiConfig:
    """Configuration for Gemini CLI integration."""
    yolo_mode: bool = False
    clean_output: bool = True
    fallback_runner: str = "cursor"  # Which runner to use for fallback
    fallback_model: str | None = None  # Model override for fallback runner


@dataclass(frozen=True)
class OpenCodeConfig:
    """Configuration for OpenCode CLI integration."""
    model: str | None = None  # Model as provider/model (e.g., anthropic/claude-sonnet-4)
    agent: str = "plan"  # Agent to use - "plan" (read-only) or "build" (full access)
    json_output: bool = False  # Output in JSON format
    clean_output: bool = True


@dataclass(frozen=True)
class ClaudeORConfig:
    """Configuration for Claude Code via OpenRouter integration."""
    api_key: str | None = None  # OpenRouter API key
    model: str | None = None  # OpenRouter model (e.g., deepseek/deepseek-v3.2)
    clean_output: bool = True


@dataclass(frozen=True)
class AiChatConfig:
    """Configuration for aichat CLI integration."""
    model: str | None = None  # Model name (e.g., openai:gpt-4o, claude:claude-sonnet-4)
    clean_output: bool = True


@dataclass(frozen=True)
class CursorConfig:
    """Configuration for Cursor Agent CLI integration."""
    model: str | None = None  # Model name (e.g., gpt-5, sonnet-4)
    force_mode: bool = False  # Auto-approve commands (--force)
    clean_output: bool = True


@dataclass(frozen=True)
class CouncilConfig:
    """Configuration for council orchestration."""
    exclude_agents: frozenset[str] = frozenset()  # Agents to exclude from council
    default_team: str | None = None  # Default team preset when no roles/team specified
    include_claude_opinion: bool = False  # Whether Claude should share its opinion by default
    substitution_donors: tuple[str, ...] = ("codex", "cursor")  # Preferred donors for unavailable agents
    # Per-seat model overrides for substituted agents (seat:model pairs)
    # When set, forces substitution through cursor runner (which supports --model)
    substitution_models: dict[str, str] | None = None


@dataclass(frozen=True)
class OwlexConfig:
    """Main configuration container."""
    codex: CodexConfig
    gemini: GeminiConfig
    opencode: OpenCodeConfig
    claudeor: ClaudeORConfig
    aichat: AiChatConfig
    cursor: CursorConfig
    council: CouncilConfig
    default_timeout: int = 300

    def print_warnings(self):
        """Print security warnings for dangerous configurations."""
        if self.codex.bypass_approvals:
            print(
                "[SECURITY WARNING] CODEX_BYPASS_APPROVALS is enabled!\n"
                "This uses --dangerously-bypass-approvals-and-sandbox which allows\n"
                "arbitrary command execution without sandboxing. Only use this in\n"
                "trusted, isolated environments. Never expose this server to untrusted clients.",
                file=sys.stderr,
                flush=True
            )


def load_config() -> OwlexConfig:
    """Load configuration from environment variables."""
    codex = CodexConfig(
        bypass_approvals=os.environ.get("CODEX_BYPASS_APPROVALS", "false").lower() == "true",
        clean_output=os.environ.get("CODEX_CLEAN_OUTPUT", "true").lower() == "true",
        enable_search=os.environ.get("CODEX_ENABLE_SEARCH", "true").lower() == "true",
    )

    gemini = GeminiConfig(
        yolo_mode=os.environ.get("GEMINI_YOLO_MODE", "false").lower() == "true",
        clean_output=os.environ.get("GEMINI_CLEAN_OUTPUT", "true").lower() == "true",
        fallback_runner=os.environ.get("GEMINI_FALLBACK_RUNNER", "cursor"),
        fallback_model=os.environ.get("GEMINI_FALLBACK_MODEL") or None,
    )

    opencode = OpenCodeConfig(
        model=os.environ.get("OPENCODE_MODEL") or None,
        agent=os.environ.get("OPENCODE_AGENT", "plan"),  # Default to read-only plan agent
        json_output=os.environ.get("OPENCODE_JSON_OUTPUT", "false").lower() == "true",
        clean_output=os.environ.get("OPENCODE_CLEAN_OUTPUT", "true").lower() == "true",
    )

    claudeor = ClaudeORConfig(
        api_key=os.environ.get("OPENROUTER_API_KEY") or os.environ.get("CLAUDEOR_API_KEY") or None,
        model=os.environ.get("CLAUDEOR_MODEL") or None,
        clean_output=os.environ.get("CLAUDEOR_CLEAN_OUTPUT", "true").lower() == "true",
    )

    aichat = AiChatConfig(
        model=os.environ.get("AICHAT_MODEL") or None,
        clean_output=os.environ.get("AICHAT_CLEAN_OUTPUT", "true").lower() == "true",
    )

    cursor = CursorConfig(
        model=os.environ.get("CURSOR_MODEL") or None,
        force_mode=os.environ.get("CURSOR_FORCE_MODE", "false").lower() == "true",
        clean_output=os.environ.get("CURSOR_CLEAN_OUTPUT", "true").lower() == "true",
    )

    # Parse council exclude agents (comma-separated list)
    exclude_raw = os.environ.get("COUNCIL_EXCLUDE_AGENTS", "")
    exclude_agents = frozenset(
        agent.strip().lower()
        for agent in exclude_raw.split(",")
        if agent.strip()
    )
    # Parse default team (None if not set or empty)
    default_team = os.environ.get("COUNCIL_DEFAULT_TEAM", "").strip() or None
    # Parse Claude opinion setting
    include_claude_opinion = os.environ.get("COUNCIL_CLAUDE_OPINION", "false").lower() == "true"
    donors_raw = os.environ.get("COUNCIL_SUBSTITUTION_DONORS", "codex,cursor")
    substitution_donors = tuple(d.strip().lower() for d in donors_raw.split(",") if d.strip())

    # Parse per-seat substitution overrides: "seat:runner:model" or "seat:model" (uses default donor)
    # Examples: "opencode:cursor:grok-4-20,claudeor:codex:gpt-5.3-codex"
    #           "opencode:grok-4-20" (uses first donor from substitution_donors)
    sub_models_raw = os.environ.get("COUNCIL_SUBSTITUTION_MODELS", "")
    substitution_models = None
    if sub_models_raw.strip():
        substitution_models = {}
        for entry in sub_models_raw.split(","):
            parts = [p.strip() for p in entry.strip().split(":")]
            if len(parts) == 3:
                seat, runner, model = parts
                substitution_models[seat.lower()] = (runner.lower(), model)
            elif len(parts) == 2:
                seat, model = parts
                substitution_models[seat.lower()] = (None, model)  # None = use default donor

    council = CouncilConfig(
        exclude_agents=exclude_agents,
        default_team=default_team,
        include_claude_opinion=include_claude_opinion,
        substitution_donors=substitution_donors,
        substitution_models=substitution_models,
    )

    try:
        timeout = int(os.environ.get("OWLEX_DEFAULT_TIMEOUT", "300"))
        if timeout <= 0:
            print(f"[WARNING] OWLEX_DEFAULT_TIMEOUT must be positive, using default 300", file=sys.stderr)
            timeout = 300
    except ValueError:
        print(f"[WARNING] Invalid OWLEX_DEFAULT_TIMEOUT value, using default 300", file=sys.stderr)
        timeout = 300

    return OwlexConfig(
        codex=codex,
        gemini=gemini,
        opencode=opencode,
        claudeor=claudeor,
        aichat=aichat,
        cursor=cursor,
        council=council,
        default_timeout=timeout,
    )


# Global config instance - loaded once at import time
config = load_config()
