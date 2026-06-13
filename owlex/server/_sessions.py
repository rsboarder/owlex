"""Per-agent start_/resume_ MCP tools."""
from __future__ import annotations

import asyncio

from pydantic import Field
from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from . import mcp
from ._validators import validate_working_directory
from ..config import config
from ..engine import (
    engine, codex_runner, gemini_runner, opencode_runner,
    claudeor_runner, aichat_runner, cursor_runner,
)
from ..models import Agent, ErrorCode, TaskResponse
from ..roles import frame_prompt_for_generation


_ROLE_FIELD_DESC = (
    "Optional role id (builtin or ~/.owlex/roles.json) to focus the model, e.g. "
    "'edge_case_adversary'. Prepends the role's round-1 framing to the prompt. "
    "None → prompt unchanged. Unknown id → error."
)


def _err(msg: str, code: ErrorCode = ErrorCode.INVALID_ARGS) -> dict:
    return TaskResponse(success=False, error=msg, error_code=code).model_dump()


def _success(task, message: str) -> dict:
    return TaskResponse(
        success=True, task_id=task.task_id, status=task.status, message=message,
    ).model_dump()


def _frame_with_role(prompt: str, role: str | None) -> tuple[str | None, dict | None]:
    """Prepend the role's round-1 prefix to ``prompt`` (generate framing, no
    council read-only-advisor instruction). Returns ``(framed, None)`` or, for an
    unknown role id, ``(None, error_response)``. ``role=None`` → prompt unchanged.

    Canonical validation order enforced at every start_*_session call site:
      1. blank-prompt reject
      2. role resolves  (this helper — called with the ORIGINAL, unstripped prompt)
      3. working_directory valid
      4. api_key / other config preconditions  (claudeor only)

    Delegates resolution to roles.frame_prompt_for_generation (shared with
    second_opinion) and adapts its error message to this module's _err dict.
    """
    framed, error_message = frame_prompt_for_generation(prompt, role)
    if error_message is not None:
        return None, _err(error_message)
    return framed, None


# === Codex ===

@mcp.tool()
async def start_codex_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send"),
    working_directory: str | None = Field(default=None, description="Working directory for Codex (--cd flag)"),
    enable_search: bool = Field(default=True, description="Enable web search (--search flag)"),
    role: str | None = Field(default=None, description=_ROLE_FIELD_DESC),
) -> dict:
    """Start a new Codex session (no prior context).

    Pass ``role`` (e.g. "edge_case_adversary") to focus the model with a
    builtin/user role framing — its round-1 prefix is prepended to the prompt.
    ``role=None`` leaves the prompt byte-identical.
    """
    if not prompt or not prompt.strip():
        return _err("'prompt' parameter is required.")
    prompt, role_err = _frame_with_role(prompt, role)
    if role_err:
        return role_err
    working_directory, error = validate_working_directory(working_directory)
    if error:
        return _err(error)

    task = engine.create_task(
        command=f"{Agent.CODEX.value}_exec",
        args={"prompt": prompt, "working_directory": working_directory, "enable_search": enable_search},
        context=ctx,
    )
    task.async_task = asyncio.create_task(engine.run_agent(
        task, codex_runner, mode="exec",
        prompt=prompt, working_directory=working_directory, enable_search=enable_search,
    ))
    return _success(task, "Codex session started. Use wait_for_task to get result.")


@mcp.tool()
async def resume_codex_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send to the resumed session"),
    session_id: str | None = Field(default=None, description="Session ID to resume (uses --last if not provided)"),
    working_directory: str | None = Field(default=None, description="Working directory for Codex (--cd flag)"),
    enable_search: bool = Field(default=True, description="Enable web search (--search flag)"),
) -> dict:
    """Resume an existing Codex session and ask for advice."""
    if not prompt or not prompt.strip():
        return _err("'prompt' parameter is required.")
    working_directory, error = validate_working_directory(working_directory)
    if error:
        return _err(error)

    use_last = not session_id or not session_id.strip()
    session_ref = "--last" if use_last else session_id.strip()
    if not use_last and not codex_runner.validate_session_id(session_ref):
        return _err(f"Invalid session_id: '{session_id}' - contains disallowed characters")

    task = engine.create_task(
        command=f"{Agent.CODEX.value}_resume",
        args={"session_id": session_ref, "prompt": prompt.strip(), "working_directory": working_directory, "enable_search": enable_search},
        context=ctx,
    )
    task.async_task = asyncio.create_task(engine.run_agent(
        task, codex_runner, mode="resume",
        prompt=prompt.strip(), session_ref=session_ref,
        working_directory=working_directory, enable_search=enable_search,
    ))
    return _success(task, f"Codex resume started{' (last session)' if use_last else f' for session {session_id}'}. Use wait_for_task to get result.")


# === Gemini ===

@mcp.tool()
async def start_gemini_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send"),
    working_directory: str | None = Field(default=None, description="Working directory for Gemini context"),
    role: str | None = Field(default=None, description=_ROLE_FIELD_DESC),
) -> dict:
    """Start a new Gemini CLI session (no prior context).

    Pass ``role`` (e.g. "edge_case_adversary") to focus the model with a
    builtin/user role framing — its round-1 prefix is prepended to the prompt.
    ``role=None`` leaves the prompt byte-identical.
    """
    if not prompt or not prompt.strip():
        return _err("'prompt' parameter is required.")
    prompt, role_err = _frame_with_role(prompt, role)
    if role_err:
        return role_err
    working_directory, error = validate_working_directory(working_directory)
    if error:
        return _err(error)

    task = engine.create_task(
        command=f"{Agent.GEMINI.value}_exec",
        args={"prompt": prompt, "working_directory": working_directory},
        context=ctx,
    )
    task.async_task = asyncio.create_task(engine.run_agent(
        task, gemini_runner, mode="exec",
        prompt=prompt, working_directory=working_directory,
    ))
    return _success(task, "Gemini session started. Use wait_for_task to get result.")


@mcp.tool()
async def resume_gemini_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send to the resumed session"),
    session_ref: str = Field(default="latest", description="Session to resume: 'latest' for most recent, or index number"),
    working_directory: str | None = Field(default=None, description="Working directory for Gemini context"),
) -> dict:
    """Resume an existing Gemini CLI session with full conversation history."""
    if not prompt or not prompt.strip():
        return _err("'prompt' parameter is required.")
    working_directory, error = validate_working_directory(working_directory)
    if error:
        return _err(error)
    if not gemini_runner.validate_session_id(session_ref):
        return _err(f"Invalid session_ref: '{session_ref}' - must be 'latest' or a numeric index")

    task = engine.create_task(
        command=f"{Agent.GEMINI.value}_resume",
        args={"session_ref": session_ref, "prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )
    task.async_task = asyncio.create_task(engine.run_agent(
        task, gemini_runner, mode="resume",
        prompt=prompt.strip(), session_ref=session_ref, working_directory=working_directory,
    ))
    return _success(task, f"Gemini resume started (session: {session_ref}). Use wait_for_task to get result.")


# === OpenCode ===

@mcp.tool()
async def start_opencode_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send"),
    working_directory: str | None = Field(default=None, description="Working directory for OpenCode context"),
    role: str | None = Field(default=None, description=_ROLE_FIELD_DESC),
) -> dict:
    """Start a new OpenCode session (no prior context).

    Pass ``role`` (e.g. "edge_case_adversary") to focus the model with a
    builtin/user role framing — its round-1 prefix is prepended to the prompt.
    ``role=None`` leaves the prompt byte-identical.
    """
    if not prompt or not prompt.strip():
        return _err("'prompt' parameter is required.")
    prompt, role_err = _frame_with_role(prompt, role)
    if role_err:
        return role_err
    working_directory, error = validate_working_directory(working_directory)
    if error:
        return _err(error)

    task = engine.create_task(
        command=f"{Agent.OPENCODE.value}_exec",
        args={"prompt": prompt, "working_directory": working_directory},
        context=ctx,
    )
    task.async_task = asyncio.create_task(engine.run_agent(
        task, opencode_runner, mode="exec",
        prompt=prompt, working_directory=working_directory,
    ))
    return _success(task, "OpenCode session started. Use wait_for_task to get result.")


@mcp.tool()
async def resume_opencode_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send to the resumed session"),
    session_id: str | None = Field(default=None, description="Session ID to resume (uses --continue if not provided)"),
    working_directory: str | None = Field(default=None, description="Working directory for OpenCode context"),
) -> dict:
    """Resume an existing OpenCode session with full conversation history."""
    if not prompt or not prompt.strip():
        return _err("'prompt' parameter is required.")
    working_directory, error = validate_working_directory(working_directory)
    if error:
        return _err(error)

    use_continue = not session_id or not session_id.strip()
    session_ref = "--continue" if use_continue else session_id.strip()
    if not use_continue and not opencode_runner.validate_session_id(session_ref):
        return _err(f"Invalid session_id: '{session_id}' - contains disallowed characters")

    task = engine.create_task(
        command=f"{Agent.OPENCODE.value}_resume",
        args={"session_id": session_ref, "prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )
    task.async_task = asyncio.create_task(engine.run_agent(
        task, opencode_runner, mode="resume",
        prompt=prompt.strip(), session_ref=session_ref, working_directory=working_directory,
    ))
    return _success(task, f"OpenCode resume started{' (continuing last session)' if use_continue else f' for session {session_id}'}. Use wait_for_task to get result.")


# === Claude OpenRouter ===

@mcp.tool()
async def start_claudeor_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send"),
    working_directory: str | None = Field(default=None, description="Working directory for Claude context"),
    role: str | None = Field(default=None, description=_ROLE_FIELD_DESC),
) -> dict:
    """Start a new Claude Code session via OpenRouter.

    Pass ``role`` (e.g. "edge_case_adversary") to focus the model with a
    builtin/user role framing — its round-1 prefix is prepended to the prompt.
    ``role=None`` leaves the prompt byte-identical.
    """
    if not prompt or not prompt.strip():
        return _err("'prompt' parameter is required.")
    prompt, role_err = _frame_with_role(prompt, role)
    if role_err:
        return role_err
    working_directory, error = validate_working_directory(working_directory)
    if error:
        return _err(error)
    if not config.claudeor.api_key:
        return _err("OPENROUTER_API_KEY or CLAUDEOR_API_KEY environment variable not set")

    task = engine.create_task(
        command=f"{Agent.CLAUDEOR.value}_exec",
        args={"prompt": prompt, "working_directory": working_directory},
        context=ctx,
    )
    task.async_task = asyncio.create_task(engine.run_agent(
        task, claudeor_runner, mode="exec",
        prompt=prompt, working_directory=working_directory,
    ))
    model_info = f" ({config.claudeor.model})" if config.claudeor.model else ""
    return _success(task, f"Claude OpenRouter{model_info} session started. Use wait_for_task to get result.")


@mcp.tool()
async def resume_claudeor_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send to the resumed session"),
    session_id: str | None = Field(default=None, description="Session ID to resume (uses --continue if not provided)"),
    working_directory: str | None = Field(default=None, description="Working directory for Claude context"),
) -> dict:
    """Resume an existing Claude OpenRouter session with full conversation history."""
    if not prompt or not prompt.strip():
        return _err("'prompt' parameter is required.")
    working_directory, error = validate_working_directory(working_directory)
    if error:
        return _err(error)
    if not config.claudeor.api_key:
        return _err("OPENROUTER_API_KEY or CLAUDEOR_API_KEY environment variable not set")

    use_continue = not session_id or not session_id.strip()
    session_ref = "continue" if use_continue else session_id.strip()
    if not use_continue and not claudeor_runner.validate_session_id(session_ref):
        return _err(f"Invalid session_id: '{session_id}' - contains disallowed characters")

    task = engine.create_task(
        command=f"{Agent.CLAUDEOR.value}_resume",
        args={"session_id": session_ref, "prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )
    task.async_task = asyncio.create_task(engine.run_agent(
        task, claudeor_runner, mode="resume",
        prompt=prompt.strip(), session_ref=session_ref, working_directory=working_directory,
    ))
    model_info = f" ({config.claudeor.model})" if config.claudeor.model else ""
    return _success(task, f"Claude OpenRouter{model_info} resume started{' (continuing last session)' if use_continue else f' for session {session_id}'}. Use wait_for_task to get result.")


# === AiChat ===

@mcp.tool()
async def start_aichat_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send"),
    working_directory: str | None = Field(default=None, description="Working directory for aichat context"),
    role: str | None = Field(default=None, description=_ROLE_FIELD_DESC),
) -> dict:
    """Start a new aichat session.

    Pass ``role`` (e.g. "edge_case_adversary") to focus the model with a
    builtin/user role framing — its round-1 prefix is prepended to the prompt.
    ``role=None`` leaves the prompt byte-identical.
    """
    if not prompt or not prompt.strip():
        return _err("'prompt' parameter is required.")
    prompt, role_err = _frame_with_role(prompt, role)
    if role_err:
        return role_err
    working_directory, error = validate_working_directory(working_directory)
    if error:
        return _err(error)

    task = engine.create_task(
        command=f"{Agent.AICHAT.value}_exec",
        args={"prompt": prompt, "working_directory": working_directory},
        context=ctx,
    )
    task.async_task = asyncio.create_task(engine.run_agent(
        task, aichat_runner, mode="exec",
        prompt=prompt, working_directory=working_directory,
    ))
    model_info = f" ({config.aichat.model})" if config.aichat.model else ""
    return _success(task, f"AiChat{model_info} session started. Use wait_for_task to get result.")


@mcp.tool()
async def resume_aichat_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send to the resumed session"),
    session_id: str = Field(description="Session name to resume"),
    working_directory: str | None = Field(default=None, description="Working directory for aichat context"),
) -> dict:
    """Resume an existing aichat session with full conversation history."""
    if not prompt or not prompt.strip():
        return _err("'prompt' parameter is required.")
    if not session_id or not session_id.strip():
        return _err("'session_id' parameter is required for aichat resume.")
    working_directory, error = validate_working_directory(working_directory)
    if error:
        return _err(error)

    session_ref = session_id.strip()
    if not aichat_runner.validate_session_id(session_ref):
        return _err(f"Invalid session_id: '{session_id}' - contains disallowed characters")

    task = engine.create_task(
        command=f"{Agent.AICHAT.value}_resume",
        args={"session_id": session_ref, "prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )
    task.async_task = asyncio.create_task(engine.run_agent(
        task, aichat_runner, mode="resume",
        prompt=prompt.strip(), session_ref=session_ref, working_directory=working_directory,
    ))
    model_info = f" ({config.aichat.model})" if config.aichat.model else ""
    return _success(task, f"AiChat{model_info} resume started for session {session_id}. Use wait_for_task to get result.")


# === Cursor Agent ===

@mcp.tool()
async def start_cursor_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send"),
    working_directory: str | None = Field(default=None, description="Working directory for Cursor Agent (--workspace flag)"),
    role: str | None = Field(default=None, description=_ROLE_FIELD_DESC),
) -> dict:
    """Start a new Cursor Agent CLI session.

    Pass ``role`` (e.g. "edge_case_adversary") to focus the model with a
    builtin/user role framing — its round-1 prefix is prepended to the prompt.
    ``role=None`` leaves the prompt byte-identical.
    """
    if not prompt or not prompt.strip():
        return _err("'prompt' parameter is required.")
    prompt, role_err = _frame_with_role(prompt, role)
    if role_err:
        return role_err
    working_directory, error = validate_working_directory(working_directory)
    if error:
        return _err(error)

    task = engine.create_task(
        command=f"{Agent.CURSOR.value}_exec",
        args={"prompt": prompt, "working_directory": working_directory},
        context=ctx,
    )
    task.async_task = asyncio.create_task(engine.run_agent(
        task, cursor_runner, mode="exec",
        prompt=prompt, working_directory=working_directory,
    ))
    model_info = f" ({config.cursor.model})" if config.cursor.model else ""
    return _success(task, f"Cursor{model_info} session started. Use wait_for_task to get result.")


@mcp.tool()
async def resume_cursor_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send to the resumed session"),
    session_id: str | None = Field(default=None, description="Chat ID to resume (uses --continue if not provided)"),
    working_directory: str | None = Field(default=None, description="Working directory for Cursor Agent (--workspace flag)"),
) -> dict:
    """Resume an existing Cursor Agent session with full conversation history."""
    if not prompt or not prompt.strip():
        return _err("'prompt' parameter is required.")
    working_directory, error = validate_working_directory(working_directory)
    if error:
        return _err(error)

    use_continue = not session_id or not session_id.strip()
    session_ref = "--continue" if use_continue else session_id.strip()
    if not use_continue and not cursor_runner.validate_session_id(session_ref):
        return _err(f"Invalid session_id: '{session_id}' - contains disallowed characters")

    task = engine.create_task(
        command=f"{Agent.CURSOR.value}_resume",
        args={"session_id": session_ref, "prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )
    task.async_task = asyncio.create_task(engine.run_agent(
        task, cursor_runner, mode="resume",
        prompt=prompt.strip(), session_ref=session_ref, working_directory=working_directory,
    ))
    model_info = f" ({config.cursor.model})" if config.cursor.model else ""
    return _success(task, f"Cursor{model_info} resume started{' (continue)' if use_continue else f' for session {session_id}'}. Use wait_for_task to get result.")
