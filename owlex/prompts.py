"""
Prompt templates for council deliberation.
Centralized prompt management for consistency and testability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .roles import RoleDefinition


# === Council System Instruction ===
# Prepended to every council prompt (R1 and R2) to enforce read-only behavior.
# Agents with yolo/auto-approve modes would otherwise create and modify files.

COUNCIL_SYSTEM_INSTRUCTION = (
    "IMPORTANT: This is a council deliberation. You are an advisor, not an implementer.\n"
    "- DO NOT create, modify, or delete any files.\n"
    "- DO NOT run commands that change state (git commit, npm install, etc.).\n"
    "- You MAY read files to inform your analysis.\n"
    "- Provide your analysis, recommendations, and code suggestions as text only.\n\n"
)


# === Round 2: Deliberation Prompts ===

DELIBERATION_INTRO_REVISE = (
    "You previously answered a question. Now review all council members' "
    "answers and provide your revised opinion."
)

DELIBERATION_INTRO_CRITIQUE = (
    "You previously answered a question. Now act as a senior code reviewer "
    "and critically analyze the other council members' answers."
)

DELIBERATION_INSTRUCTION_REVISE = (
    "Please provide your revised answer after considering the other perspectives. "
    "Note any points of agreement or disagreement."
)

DELIBERATION_INSTRUCTION_CRITIQUE = (
    "Act as a senior reviewer. Identify bugs, security vulnerabilities, "
    "architectural flaws, incorrect assumptions, or gaps in the other answers. "
    "Be specific and critical. For code suggestions, look for edge cases, "
    "error handling issues, and potential failures. Do not just agree - find problems."
)


def anonymize_round_responses(round_data, *, salt: str | None = None):
    """Shuffle a CouncilRound's per-agent responses and assign letter labels.

    Returns ``(by_label, label_to_agent)`` where:
      - ``by_label`` maps letter ('A', 'B', ...) -> the original AgentResponse object
      - ``label_to_agent`` maps letter -> agent seat name ('codex', 'gemini', ...)

    Ordering is randomized when ``salt`` is provided (deterministic per-salt so
    R1 and R2 share a mapping). When ``salt`` is None the call falls back to
    Python's global ``random.shuffle`` via the shared anonymizer helper.

    Returns (None, None) when round_data is None.
    """
    if round_data is None:
        return None, None
    from .anonymize import assign_labels
    from .models import Agent

    pairs: list[tuple[str, object]] = []
    for agent_name in Agent:
        ar = getattr(round_data, agent_name.value, None)
        if ar is not None:
            pairs.append((agent_name.value, ar))

    # Always shuffle for R2 anonymity. When no salt, generate one off process
    # randomness so the call still produces a deterministic-per-call mapping.
    effective_salt = salt if salt is not None else _random_salt()
    return assign_labels(pairs, salt=effective_salt)


def _random_salt() -> str:
    """Generate a non-deterministic salt for one-off shuffles."""
    import secrets
    return secrets.token_hex(8)


def build_deliberation_prompt(
    original_prompt: str,
    codex_answer: str | None = None,
    gemini_answer: str | None = None,
    opencode_answer: str | None = None,
    claudeor_answer: str | None = None,
    aichat_answer: str | None = None,
    cursor_answer: str | None = None,
    claude_answer: str | None = None,
    critique: bool = False,
    include_original: bool = False,
) -> str:
    """
    Build the deliberation prompt for round 2.

    Agent identities are anonymized (Response A, B, C...) and order is randomized
    to prevent self-preference bias and anchoring effects.

    By default, the original_prompt is NOT included because R2 agents resume from
    R1 sessions and already have the original question in their context.

    When include_original=True (used for exec fallback when session resume fails),
    the original prompt is included so the agent has full context.
    """
    from .anonymize import assign_labels

    if critique:
        intro = DELIBERATION_INTRO_CRITIQUE
        instruction = DELIBERATION_INSTRUCTION_CRITIQUE
    else:
        intro = DELIBERATION_INTRO_REVISE
        instruction = DELIBERATION_INSTRUCTION_REVISE

    parts = [COUNCIL_SYSTEM_INSTRUCTION + intro]

    if include_original and original_prompt:
        parts.extend(["", "ORIGINAL QUESTION:", original_prompt])

    # Collect all named answers, then anonymize via the shared helper.
    pairs: list[tuple[str, str]] = []
    for seat, answer in (
        ("codex", codex_answer),
        ("gemini", gemini_answer),
        ("opencode", opencode_answer),
        ("claudeor", claudeor_answer),
        ("aichat", aichat_answer),
        ("cursor", cursor_answer),
    ):
        if answer:
            pairs.append((seat, answer))
    if claude_answer:
        pairs.append(("claude", claude_answer))

    by_label, _ = assign_labels(pairs, salt=_random_salt())
    for label, answer in by_label.items():
        parts.extend(["", f"RESPONSE {label}:", answer])

    parts.extend(["", instruction])

    return "\n".join(parts)


# === Role Injection Functions ===

def inject_role_prefix(prompt: str, role: RoleDefinition | None, context: str | None = None) -> str:
    """
    Inject council system instruction, project context, and role prefix into a prompt for Round 1.

    Args:
        prompt: The original prompt
        role: The role definition (None or neutral role = no injection)
        context: Optional project context (CLAUDE.md, git diff) to inject

    Returns:
        Prompt with system instruction, context, and role prefix prepended
    """
    role_prefix = role.round_1_prefix if role and role.round_1_prefix else ""
    context_block = f"PROJECT CONTEXT:\n{context}\n\n" if context else ""
    return f"{COUNCIL_SYSTEM_INSTRUCTION}{context_block}{role_prefix}{prompt}"


def build_deliberation_prompt_with_role(
    original_prompt: str,
    role: RoleDefinition | None = None,
    codex_answer: str | None = None,
    gemini_answer: str | None = None,
    opencode_answer: str | None = None,
    claudeor_answer: str | None = None,
    aichat_answer: str | None = None,
    cursor_answer: str | None = None,
    claude_answer: str | None = None,
    critique: bool = False,
    include_original: bool = False,
) -> str:
    """
    Build the deliberation prompt for round 2 with role prefix.

    The role prefix is prepended to maintain the agent's perspective
    ("sticky role") during deliberation.

    Args:
        original_prompt: The original question
        role: The role definition for this agent (maintains perspective in R2)
        codex_answer: Codex's round 1 answer
        gemini_answer: Gemini's round 1 answer
        opencode_answer: OpenCode's round 1 answer
        claudeor_answer: ClaudeOR's round 1 answer
        claude_answer: Optional Claude opinion
        critique: If True, use critique mode prompts
        include_original: If True, include original_prompt (for exec fallback)

    Returns:
        Complete deliberation prompt with role prefix
    """
    # Build base deliberation prompt
    base_prompt = build_deliberation_prompt(
        original_prompt=original_prompt,
        codex_answer=codex_answer,
        gemini_answer=gemini_answer,
        opencode_answer=opencode_answer,
        claudeor_answer=claudeor_answer,
        aichat_answer=aichat_answer,
        cursor_answer=cursor_answer,
        claude_answer=claude_answer,
        critique=critique,
        include_original=include_original,
    )

    # Inject role prefix for R2 (sticky role)
    if role is None or not role.round_2_prefix:
        return base_prompt

    return f"{role.round_2_prefix}{base_prompt}"
