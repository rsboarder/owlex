"""Per-seat resolution: which runner is actually answering, with which model,
and where its transcript lives.

This is the single source of truth for "who is running this task" — every
downstream consumer (logs, the calls table's ``model`` column, the skill
parser's transcript dispatch, the dashboard's role display) must read from
``Resolution``, never from ad-hoc string fields.

Without this, three orthogonal facts (seat, runner, model) get conflated:
- the skill parser was scanning ``~/.claude/projects/`` for substituted
  ``claudeor`` and mis-attributing the parent Claude Code session's tool
  calls to the council's claudeor task;
- the calls table's ``model`` column was populated only when substitution
  carried an explicit model — native runners had NULL;
- log lines kept calling a substituted agent "Claude/OpenRouter (via X)"
  even though it was actually codex with X.

Construct one ``Resolution`` per seat at council start; pass it through.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


TranscriptSource = Literal["claude-projects", "codex-rollouts", "gemini-tmp", "none"]


# Mapping from (native) runner name to where that runner persists its
# session transcript. Used by the skill parser to decide where to look.
# Substituted agents inherit the *runner's* transcript source, not the seat's.
_RUNNER_TRANSCRIPT_SOURCE: dict[str, TranscriptSource] = {
    "codex": "codex-rollouts",
    "claudeor": "claude-projects",
    "gemini": "gemini-tmp",
    "cursor": "none",     # protobuf store, no public schema
    "opencode": "none",   # `opencode run` doesn't persist
    "aichat": "none",     # YAML sessions don't record tool calls
}


@dataclass(frozen=True)
class Resolution:
    """How a single council seat is being answered, end-to-end.

    Construct once per seat at the start of a council and propagate.
    """

    seat: str
    """The role label in the council ('codex', 'claudeor', ...)."""

    runner: str
    """The CLI binary actually launched. Equals ``seat`` when not substituted."""

    model: str | None
    """Concrete LLM model identifier, or None if the runner uses its default."""

    is_substituted: bool
    """True iff ``runner != seat``."""

    @property
    def transcript_source(self) -> TranscriptSource:
        """Where to look for this task's tool/skill transcript.

        Substituted seats follow the runner's source — a substituted claudeor
        running through codex writes to ``codex-rollouts``, not to
        ``claude-projects``. This is what the skill parser must dispatch on.
        """
        return _RUNNER_TRANSCRIPT_SOURCE.get(self.runner, "none")

    def display_name(self) -> str:
        """Human-readable identity for log lines and the dashboard.

        Honest about substitution — never claims a substituted agent is
        running its native runner.
        """
        if self.is_substituted:
            model_part = f"({self.model})" if self.model else ""
            return f"{self.seat}->{self.runner}{model_part}"
        return self.seat if not self.model else f"{self.seat}({self.model})"


def resolve_seat(
    seat: str,
    runner: str | None = None,
    model: str | None = None,
) -> Resolution:
    """Build a Resolution from raw fields. ``runner=None`` means no substitution."""
    effective_runner = runner or seat
    return Resolution(
        seat=seat,
        runner=effective_runner,
        model=model,
        is_substituted=(effective_runner != seat),
    )
