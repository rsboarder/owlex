"""Council MCP tools: council_ask (blind) and rate_council."""
from __future__ import annotations

import json

from pydantic import Field
from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from . import mcp
from ._validators import council_recursion_block, validate_working_directory
from .. import store
from ..config import config
from ..council import Council
from ..engine import DEFAULT_TIMEOUT
from ..models import ErrorCode, TaskResponse
from ..prompts import anonymize_round_responses


@mcp.tool()
async def council_ask(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or task to send to the council"),
    claude_opinion: str | None = Field(default=None, description="Claude's initial opinion (optional)"),
    working_directory: str | None = Field(default=None, description="Working directory for context"),
    deliberate: bool | str = Field(default="auto", description="If true, always run R2; 'auto' skips on consensus; false = R1 only"),
    critique: bool = Field(default=False, description="If true, R2 critiques instead of revising"),
    timeout: int = Field(default=DEFAULT_TIMEOUT, description="Timeout per agent in seconds"),
    roles: dict[str, str] | list[str] | None = Field(default=None, description="Role assignments"),
    team: str | None = Field(default=None, description="Team preset name (alternative to roles)"),
) -> dict:
    """REQUIRED FOLLOW-UP: After this tool returns, you MUST call
    rate_council(council_id=..., ratings={...}) BEFORE you present the
    synthesized answer to the user. Skipping the rating defeats the entire
    purpose of this tool — it exists to collect blind per-agent quality data
    that the dashboard depends on. Rate every letter that appears in round_1.

    Asks the council with anonymized responses. Returns letter-keyed responses
    (Response A, B, C, ...) and persists the letter→agent mapping server-side.
    The mapping is never returned to the orchestrator — ratings are committed
    against letters and resolved to agent names server-side.
    """
    blocked = council_recursion_block("council_ask")
    if blocked is not None:
        return blocked

    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    if isinstance(roles, str):
        try:
            roles = json.loads(roles)
        except (json.JSONDecodeError, TypeError):
            return TaskResponse(
                success=False,
                error=f"Invalid roles format: expected a JSON list or dict, got string: {roles[:100]}",
                error_code=ErrorCode.INVALID_ARGS,
            ).model_dump()

    if roles is not None and team is not None:
        return TaskResponse(
            success=False,
            error="Cannot specify both 'roles' and 'team' parameters. Use one or the other.",
            error_code=ErrorCode.INVALID_ARGS,
        ).model_dump()

    effective_team = team if team is not None else config.council.default_team
    role_spec = roles if roles is not None else effective_team

    try:
        council = Council(context=ctx)
        response = await council.deliberate(
            prompt=prompt.strip(),
            working_directory=working_directory,
            claude_opinion=claude_opinion,
            deliberate=deliberate,
            critique=critique,
            timeout=timeout,
            roles=role_spec,
        )
    except ValueError as e:
        return TaskResponse(success=False, error=str(e), error_code=ErrorCode.INVALID_ARGS).model_dump()
    except Exception as e:
        import sys as _sys, traceback as _tb
        _tb.print_exc(file=_sys.stderr); _sys.stderr.flush()
        return TaskResponse(
            success=False,
            error=f"Council deliberation failed: {e}",
            error_code=ErrorCode.EXECUTION_FAILED,
        ).model_dump()

    council_id = council.council_id
    salt = f"blind:{council_id}"
    r1_by_label, mapping = anonymize_round_responses(response.round_1, salt=salt)
    r2_by_label, _ = anonymize_round_responses(response.round_2, salt=salt) if response.round_2 else (None, None)

    if mapping:
        try:
            store.record_council_anonymization(council_id, mapping)
        except Exception as _e:
            import sys as _sys, traceback as _tb
            _tb.print_exc(file=_sys.stderr); _sys.stderr.flush()

    def _to_anon(ar):
        if ar is None:
            return None
        d = ar.model_dump()
        d["agent"] = "anon"
        d["session_id"] = None
        return d

    def _round_payload(by_label):
        if not by_label:
            return None
        return {label: _to_anon(ar) for label, ar in by_label.items()}

    r1_payload = _round_payload(r1_by_label)
    r2_payload = _round_payload(r2_by_label)
    letters_present = sorted((r1_payload or {}).keys())

    return {
        "_required_next_tool_call": (
            f"REQUIRED: Before responding to the user, you MUST call "
            f"rate_council(council_id='{council_id}', ratings={{...}}). "
            f"Rate each of these letters: {letters_present}. "
            f"Each rating is {{score: -1 or +1, groundedness?: 1-5, helpfulness?: 1-5, "
            f"correctness?: 1-5, reason?: str}}. DO NOT respond to the user until "
            f"this call has been dispatched. The letter→agent mapping is hidden — "
            f"rate based on response content alone."
        ),
        "council_id": council_id,
        "prompt": response.prompt,
        "working_directory": response.working_directory,
        "deliberation": response.deliberation,
        "critique": response.critique,
        "claude_opinion": response.claude_opinion.model_dump() if response.claude_opinion else None,
        "round_1": r1_payload,
        "round_2": r2_payload,
        "metadata": response.metadata.model_dump(),
    }


@mcp.tool()
async def rate_council(
    council_id: str = Field(description="The council_id returned by council_ask"),
    ratings: dict = Field(description="Map of letter → rating dict, e.g. {'A': {'score': 1, 'groundedness': 4, 'reason': '...'}, 'B': {...}}"),
) -> dict:
    """Submit per-letter blind ratings for a council. Required follow-up after every council_ask call."""
    blocked = council_recursion_block("rate_council")
    if blocked is not None:
        return blocked

    if isinstance(ratings, str):
        try:
            ratings = json.loads(ratings)
        except (json.JSONDecodeError, TypeError):
            return TaskResponse(
                success=False,
                error="Invalid ratings format: expected a JSON dict",
                error_code=ErrorCode.INVALID_ARGS,
            ).model_dump()

    if not isinstance(ratings, dict) or not ratings:
        return TaskResponse(
            success=False,
            error="'ratings' must be a non-empty dict mapping letter → rating",
            error_code=ErrorCode.INVALID_ARGS,
        ).model_dump()

    mapping = store.get_council_anonymization(council_id)
    if not mapping:
        return TaskResponse(
            success=False,
            error=f"No blind anonymization found for council_id={council_id}. Was it created via council_ask?",
            error_code=ErrorCode.NOT_FOUND,
        ).model_dump()

    rated: list[str] = []
    errors: list[str] = []
    for label, raw in ratings.items():
        if not isinstance(raw, dict):
            errors.append(f"{label}: rating must be a dict")
            continue
        score = raw.get("score")
        if score not in (-1, 1):
            errors.append(f"{label}: score must be -1 or +1, got {score!r}")
            continue
        agent = mapping.get(label)
        if not agent:
            errors.append(f"{label}: no agent mapped for this label in council {council_id}")
            continue
        dim_keys = ("groundedness", "helpfulness", "correctness")
        dimensions = {k: raw[k] for k in dim_keys if k in raw and raw[k] is not None}
        try:
            store.record_agent_score(
                council_id, agent, int(score),
                rater="claude_blind",
                dimensions=dimensions or None,
                reason=raw.get("reason"),
            )
            rated.append(agent)
        except Exception as e:
            errors.append(f"{label} ({agent}): {e}")

    return {"ok": len(errors) == 0, "agents_rated": rated, "errors": errors}
