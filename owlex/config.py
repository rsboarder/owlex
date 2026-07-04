"""
Centralized configuration for owlex.
All settings are loaded from environment variables with sensible defaults.

Env-var parsing happens in one place (``_env``), giving every field uniform
truthy / numeric / list parsing and one place to add new conversions.
"""

import os
import sys
from dataclasses import dataclass


# === Env-var parsing helpers ============================================
# Centralizing these collapses ~30 ad-hoc parsing call sites into 4 helpers
# and gives every config field consistent semantics.

_TRUTHY = {"true", "1", "yes", "on", "y"}
_FALSY = {"false", "0", "no", "off", "n", ""}


def _get_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean env var. Accepts true/false, 1/0, yes/no, on/off."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in _TRUTHY:
        return True
    if val in _FALSY:
        return default if val == "" else False
    print(
        f"[WARNING] {name}={raw!r} is not a valid boolean; using default {default}",
        file=sys.stderr,
        flush=True,
    )
    return default


def _get_int(name: str, default: int, *, min_value: int | None = None) -> int:
    """Parse an int env var; warn and fall back on invalid or out-of-range values."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        val = int(raw)
    except ValueError:
        print(
            f"[WARNING] Invalid {name} value {raw!r}, using default {default}",
            file=sys.stderr,
            flush=True,
        )
        return default
    if min_value is not None and val < min_value:
        print(
            f"[WARNING] {name} must be >= {min_value}, got {val}; using default {default}",
            file=sys.stderr,
            flush=True,
        )
        return default
    return val


def _get_str_or_none(name: str) -> str | None:
    """Return env var stripped, or None if unset/empty."""
    raw = os.environ.get(name, "").strip()
    return raw or None


def _get_csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Parse comma-separated list; lowercased; empty/missing → default."""
    raw = os.environ.get(name, "")
    items = tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    return items or default


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
class GlmBlindConfig:
    """Configuration for the optional GLM-5.2 background blind-rater.

    When enabled, after each council's R1 round, GLM-5.2 blind-rates the
    anonymized R1 responses and persists per-agent scores under rater='glm_blind'
    alongside the existing 'claude_blind' rater. Off by default — zero latency
    impact when disabled.

    See docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md.
    """
    enabled: bool = False       # OWLEX_GLM_BLIND_ENABLED (default off)
    model: str = "glm-5.2"     # OWLEX_GLM_BLIND_MODEL
    reasoning: str = "max"     # OWLEX_GLM_BLIND_REASONING (max uses extended thinking)
    timeout: int = 120         # OWLEX_GLM_BLIND_TIMEOUT (seconds; GLM@max is ~100s/call)


@dataclass(frozen=True)
class GlmEscalationConfig:
    """Configuration for the optional GLM-5.2 tie-breaker on R1 disagreement.

    When enabled, if auto-mode R1 agreement falls below AUTO_DELIBERATION_THRESHOLD
    (i.e. R2 will fire), GLM-5.2 is invoked ONCE as an additional opinion and
    attached to CouncilResponse.glm_escalation_response. Off by default — zero
    latency impact on consensus or when the flag is unset.

    Reasoning effort for the GLM-via-opencode escalation is controlled by
    OWLEX_GLM_OC_VARIANT (shared with the GLM seat), not a separate escalation var.

    See docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md.
    """
    enabled: bool = False        # OWLEX_GLM_ESCALATION_ENABLED (default off)
    model: str = "zai/glm-5.2"  # OWLEX_GLM_ESCALATION_MODEL
    timeout: int = 120           # OWLEX_GLM_ESCALATION_TIMEOUT (seconds)


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
    glm_blind: GlmBlindConfig
    glm_escalation: GlmEscalationConfig
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


# Known runner identifiers (matches owlex.agents Agent enum). Used to
# validate seat:runner:model entries up front instead of letting them
# silently 600s-timeout when the runner doesn't recognize the model.
_KNOWN_RUNNERS = frozenset({"codex", "gemini", "opencode", "claudeor", "aichat", "cursor"})


def _load_substitution_models() -> dict[str, tuple[str | None, str]] | None:
    """Parse COUNCIL_SUBSTITUTION_MODELS = ``seat:runner:model,seat:model,...``.

    Two forms per entry:
      - ``seat:runner:model``   → run the named seat through ``runner`` with ``model``
      - ``seat:model``          → run the named seat through the default donor with ``model``

    Invalid entries (malformed shape, unknown runner, empty model) are
    dropped with a warning; an empty/missing var returns None. Catching
    typos here prevents the 600s-timeout failure mode where an unknown
    model string gets shipped to a runner that silently rejects it.
    """
    raw = os.environ.get("COUNCIL_SUBSTITUTION_MODELS", "")
    if not raw.strip():
        return None
    out: dict[str, tuple[str | None, str]] = {}
    for entry in raw.split(","):
        parts = [p.strip() for p in entry.strip().split(":")]
        if len(parts) == 3 and all(parts):
            seat, runner, model = parts
            if runner.lower() not in _KNOWN_RUNNERS:
                print(
                    f"[WARNING] COUNCIL_SUBSTITUTION_MODELS: runner {runner!r} "
                    f"is not one of {sorted(_KNOWN_RUNNERS)}; entry {entry!r} skipped",
                    file=sys.stderr, flush=True,
                )
                continue
            out[seat.lower()] = (runner.lower(), model)
        elif len(parts) == 2 and all(parts):
            seat, model = parts
            out[seat.lower()] = (None, model)
        elif entry.strip():  # malformed but non-empty → warn
            print(
                f"[WARNING] COUNCIL_SUBSTITUTION_MODELS entry {entry!r} is not "
                f"'seat:runner:model' or 'seat:model'; skipped",
                file=sys.stderr,
                flush=True,
            )
    return out or None


def load_config() -> OwlexConfig:
    """Load configuration from environment variables."""
    codex = CodexConfig(
        bypass_approvals=_get_bool("CODEX_BYPASS_APPROVALS", False),
        clean_output=_get_bool("CODEX_CLEAN_OUTPUT", True),
        enable_search=_get_bool("CODEX_ENABLE_SEARCH", True),
    )

    gemini = GeminiConfig(
        yolo_mode=_get_bool("GEMINI_YOLO_MODE", False),
        clean_output=_get_bool("GEMINI_CLEAN_OUTPUT", True),
        fallback_runner=os.environ.get("GEMINI_FALLBACK_RUNNER", "cursor"),
        fallback_model=_get_str_or_none("GEMINI_FALLBACK_MODEL"),
    )

    opencode = OpenCodeConfig(
        model=_get_str_or_none("OPENCODE_MODEL"),
        agent=os.environ.get("OPENCODE_AGENT", "plan"),  # Default to read-only plan agent
        json_output=_get_bool("OPENCODE_JSON_OUTPUT", False),
        clean_output=_get_bool("OPENCODE_CLEAN_OUTPUT", True),
    )

    claudeor = ClaudeORConfig(
        api_key=_get_str_or_none("OPENROUTER_API_KEY") or _get_str_or_none("CLAUDEOR_API_KEY"),
        model=_get_str_or_none("CLAUDEOR_MODEL"),
        clean_output=_get_bool("CLAUDEOR_CLEAN_OUTPUT", True),
    )

    aichat = AiChatConfig(
        model=_get_str_or_none("AICHAT_MODEL"),
        clean_output=_get_bool("AICHAT_CLEAN_OUTPUT", True),
    )

    cursor = CursorConfig(
        model=_get_str_or_none("CURSOR_MODEL"),
        force_mode=_get_bool("CURSOR_FORCE_MODE", False),
        clean_output=_get_bool("CURSOR_CLEAN_OUTPUT", True),
    )

    council = CouncilConfig(
        exclude_agents=frozenset(_get_csv("COUNCIL_EXCLUDE_AGENTS")),
        default_team=_get_str_or_none("COUNCIL_DEFAULT_TEAM"),
        include_claude_opinion=_get_bool("COUNCIL_CLAUDE_OPINION", False),
        substitution_donors=_get_csv("COUNCIL_SUBSTITUTION_DONORS", default=("codex", "cursor")),
        substitution_models=_load_substitution_models(),
    )

    glm_blind = GlmBlindConfig(
        enabled=_get_bool("OWLEX_GLM_BLIND_ENABLED", False),
        model=os.environ.get("OWLEX_GLM_BLIND_MODEL", "glm-5.2"),
        reasoning=os.environ.get("OWLEX_GLM_BLIND_REASONING", "max"),
        timeout=_get_int("OWLEX_GLM_BLIND_TIMEOUT", 120, min_value=1),
    )

    glm_escalation = GlmEscalationConfig(
        enabled=_get_bool("OWLEX_GLM_ESCALATION_ENABLED", False),
        model=os.environ.get("OWLEX_GLM_ESCALATION_MODEL", "zai/glm-5.2"),
        timeout=_get_int("OWLEX_GLM_ESCALATION_TIMEOUT", 120, min_value=1),
    )

    return OwlexConfig(
        codex=codex,
        gemini=gemini,
        opencode=opencode,
        claudeor=claudeor,
        aichat=aichat,
        cursor=cursor,
        council=council,
        glm_blind=glm_blind,
        glm_escalation=glm_escalation,
        default_timeout=_get_int("OWLEX_DEFAULT_TIMEOUT", 300, min_value=1),
    )


# Global config instance - loaded once at import time
config = load_config()
