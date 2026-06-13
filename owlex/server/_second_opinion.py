"""MCP tool: second_opinion — one-call cross-model second opinion."""
from __future__ import annotations

from typing import Annotated

from pydantic import Field

from . import mcp
from ._validators import log, validate_working_directory
from ..models import ErrorCode, TaskResponse
from ..roles import frame_prompt_for_generation
from ..second_opinion import MODEL, REASONING, get_second_opinion


@mcp.tool()
async def second_opinion(
    prompt: Annotated[str, Field(description="The question or code to get a second opinion on")],
    working_directory: Annotated[
        str | None,
        Field(description="Optional dir for codex to read files from (e.g. repo root). None → ephemeral empty dir."),
    ] = None,
    timeout: Annotated[
        int | None,
        Field(description="Per-call timeout in seconds. None → module default (120)."),
    ] = None,
    role: Annotated[
        str | None,
        Field(description="Optional role id (builtin or ~/.owlex/roles.json) to focus the model, "
              "e.g. 'edge_case_adversary'. Prepends the role's round-1 framing to the prompt. "
              "None → prompt unchanged. Unknown id → error."),
    ] = None,
) -> dict:
    """Fast independent second opinion from ONE non-Claude frontier model.

    Single call, no council rounds, no rating, nothing persisted. Use for a
    quick cross-check; for full deliberation use council_ask.

    Pass ``role`` to focus the model with a builtin/user role framing (e.g.
    ``role="edge_case_adversary"`` to generate a test spec). The role's round-1
    prefix is prepended to the prompt; the council read-only-advisor framing is
    NOT applied (this is a generate call, not a read-only review).
    """
    log(
        f"[owlex.second_opinion] called model={MODEL} reasoning={REASONING} "
        f"wd={working_directory!r} timeout={timeout} role={role!r}"
    )

    # Canonical validation order: blank-prompt → role resolves → working_directory valid.
    if not prompt or not prompt.strip():
        log("[owlex.second_opinion] rejected: blank prompt")
        return TaskResponse(
            success=False, error="'prompt' parameter is required.",
            error_code=ErrorCode.INVALID_ARGS,
        ).model_dump()

    framed_prompt, role_err = frame_prompt_for_generation(prompt, role)
    if role_err is not None:
        log(f"[owlex.second_opinion] rejected: unknown role={role!r}")
        return TaskResponse(
            success=False, error=role_err, error_code=ErrorCode.INVALID_ARGS
        ).model_dump()

    wd, error = validate_working_directory(working_directory)
    if error:
        log("[owlex.second_opinion] rejected: invalid working_directory")
        return TaskResponse(
            success=False, error=error, error_code=ErrorCode.INVALID_ARGS
        ).model_dump()

    ok, text, timed_out = await get_second_opinion(framed_prompt, wd, timeout)
    if ok:
        log(f"[owlex.second_opinion] ok model={MODEL} ({len(text)} chars)")
        return {"success": True, "model": MODEL, "opinion": text}
    error_code = ErrorCode.TIMEOUT if timed_out else ErrorCode.EXECUTION_FAILED
    log(f"[owlex.second_opinion] failed code={error_code.value} timed_out={timed_out}")
    return TaskResponse(
        success=False, error=text, error_code=error_code
    ).model_dump()
