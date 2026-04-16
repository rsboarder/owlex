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
    import random

    if critique:
        intro = DELIBERATION_INTRO_CRITIQUE
        instruction = DELIBERATION_INSTRUCTION_CRITIQUE
    else:
        intro = DELIBERATION_INTRO_REVISE
        instruction = DELIBERATION_INSTRUCTION_REVISE

    parts = [COUNCIL_SYSTEM_INSTRUCTION + intro]

    if include_original and original_prompt:
        parts.extend(["", "ORIGINAL QUESTION:", original_prompt])

    # Collect all answers, anonymize, and randomize order
    answers = []
    all_named = {
        "codex": codex_answer, "gemini": gemini_answer, "opencode": opencode_answer,
        "claudeor": claudeor_answer, "aichat": aichat_answer, "cursor": cursor_answer,
    }
    for answer in all_named.values():
        if answer:
            answers.append(answer)

    if claude_answer:
        answers.append(claude_answer)

    random.shuffle(answers)

    labels = "ABCDEFGHIJ"
    for i, answer in enumerate(answers):
        label = labels[i] if i < len(labels) else str(i + 1)
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
