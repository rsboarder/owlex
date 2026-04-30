#!/usr/bin/env python3
"""
MCP Server for Codex CLI and Gemini CLI Integration
Allows Claude Code to start/resume sessions with Codex or Gemini for advice
"""

import asyncio
import json
import os
import sys
from datetime import datetime

from pydantic import Field
from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession

from .models import TaskResponse, ErrorCode, Agent
from .engine import engine, DEFAULT_TIMEOUT, codex_runner, gemini_runner, opencode_runner, claudeor_runner, aichat_runner, cursor_runner
from .council import Council
from .config import config
from .roles import get_resolver
from . import store


# Initialize FastMCP server
mcp = FastMCP("owlex-server")


# === Resources ===

async def _get_cli_version(cmd: str) -> str:
    """Get version string from a CLI tool."""
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return stdout.decode().strip().split('\n')[0]
    except Exception:
        return "unknown"


def _get_codex_model() -> str:
    """Get Codex model from config file."""
    import pathlib
    config_path = pathlib.Path.home() / ".codex" / "config.toml"
    try:
        if config_path.exists():
            content = config_path.read_text()
            for line in content.split('\n'):
                if line.startswith('model ='):
                    return line.split('=')[1].strip().strip('"\'')
    except Exception:
        pass
    return "default"


def _get_gemini_model() -> str:
    """Get Gemini model - uses CLI default."""
    # Gemini CLI uses default model based on version
    return "gemini-2.5-pro"  # Default for current CLI


def _get_opencode_model() -> str:
    """Get OpenCode model from env or default."""
    model = os.environ.get("OPENCODE_MODEL", "").strip()
    return model if model else "openrouter/anthropic/claude-sonnet-4"


def _get_aichat_model() -> str:
    """Get aichat model from env or default."""
    model = os.environ.get("AICHAT_MODEL", "").strip()
    return model if model else "default"


def _get_cursor_model() -> str:
    """Get Cursor Agent model from env or default."""
    model = os.environ.get("CURSOR_MODEL", "").strip()
    return model if model else "default"


@mcp.resource("owlex://agents")
async def get_agents() -> str:
    """List available agents and their configuration."""
    excluded = config.council.exclude_agents

    # Query CLI versions in parallel
    codex_ver, gemini_ver, opencode_ver, aichat_ver, cursor_ver = await asyncio.gather(
        _get_cli_version("codex"),
        _get_cli_version("gemini"),
        _get_cli_version("opencode"),
        _get_cli_version("aichat"),
        _get_cli_version("agent"),
    )

    agents = {
        "codex": {
            "available": "codex" not in excluded,
            "cli_version": codex_ver,
            "model": _get_codex_model(),
            "description": "Deep reasoning, code review, bug finding",
            "config": {
                "enable_search": config.codex.enable_search,
                "bypass_approvals": config.codex.bypass_approvals,
            }
        },
        "gemini": {
            "available": "gemini" not in excluded,
            "cli_version": gemini_ver,
            "model": _get_gemini_model(),
            "description": "1M context window, multimodal, large codebases",
            "config": {
                "yolo_mode": config.gemini.yolo_mode,
            }
        },
        "opencode": {
            "available": "opencode" not in excluded,
            "cli_version": opencode_ver,
            "model": _get_opencode_model(),
            "description": "Alternative perspective, configurable models",
            "config": {
                "agent_mode": config.opencode.agent,
            }
        },
        "aichat": {
            "available": "aichat" not in excluded,
            "cli_version": aichat_ver,
            "model": _get_aichat_model(),
            "description": "Multi-provider LLM CLI, bring your own model",
            "config": {
                "model": config.aichat.model,
            }
        },
        "cursor": {
            "available": "cursor" not in excluded,
            "cli_version": cursor_ver,
            "model": _get_cursor_model(),
            "description": "Cursor Agent CLI, multi-model coding assistant",
            "config": {
                "model": config.cursor.model,
                "force_mode": config.cursor.force_mode,
            }
        },
    }

    return json.dumps({
        "agents": agents,
        "excluded": list(excluded),
        "default_timeout": config.default_timeout,
    }, indent=2)


@mcp.resource("owlex://council/status")
def get_council_status() -> str:
    """Get status of running council deliberations."""
    council_tasks = []

    for task_id, task in engine.tasks.items():
        if task.command == "council_ask":
            elapsed = (datetime.now() - task.start_time).total_seconds()
            council_tasks.append({
                "task_id": task_id,
                "status": task.status,
                "elapsed_seconds": round(elapsed, 1),
                "prompt": task.args.get("prompt", "")[:100] + "..." if len(task.args.get("prompt", "")) > 100 else task.args.get("prompt", ""),
                "deliberate": task.args.get("deliberate", True),
                "critique": task.args.get("critique", False),
            })

    # Sort by most recent first
    council_tasks.sort(key=lambda x: x["elapsed_seconds"])

    running = [t for t in council_tasks if t["status"] == "running"]
    pending = [t for t in council_tasks if t["status"] == "pending"]

    return json.dumps({
        "running_count": len(running),
        "pending_count": len(pending),
        "total_count": len(council_tasks),
        "running": running,
        "pending": pending,
        "recent": council_tasks[:5],
    }, indent=2)


def _log(msg: str):
    """Log progress to stderr for CLI visibility."""
    print(msg, file=sys.stderr, flush=True)


def _validate_working_directory(working_directory: str | None) -> tuple[str | None, str | None]:
    """Validate and expand working directory. Returns (expanded_path, error_message)."""
    if not working_directory:
        return None, None
    expanded = os.path.expanduser(working_directory)
    if not os.path.isdir(expanded):
        return None, f"working_directory '{working_directory}' does not exist or is not a directory."
    return expanded, None


def _council_recursion_block(tool_name: str) -> dict | None:
    """Return an error dict when this owlex-server is running inside another council.

    The engine sets ``OWLEX_COUNCIL_DEPTH`` on every spawned agent subprocess. When
    an agent's own owlex-server is invoked, it inherits the env and refuses
    council_ask / rate_council so a council can't recursively spawn sub-councils.
    """
    try:
        depth = int(os.environ.get("OWLEX_COUNCIL_DEPTH", "0") or 0)
    except ValueError:
        depth = 0
    if depth > 0:
        return TaskResponse(
            success=False,
            error=(
                f"Recursive {tool_name} is not allowed (OWLEX_COUNCIL_DEPTH={depth}). "
                f"This owlex-server is running inside an active council; a participant "
                f"cannot spawn another council."
            ),
            error_code=ErrorCode.INVALID_ARGS,
        ).model_dump()
    return None


# === Codex Tools ===

@mcp.tool()
async def start_codex_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send"),
    working_directory: str | None = Field(default=None, description="Working directory for Codex (--cd flag)"),
    enable_search: bool = Field(default=True, description="Enable web search (--search flag)")
) -> dict:
    """Start a new Codex session (no prior context)."""
    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    task = engine.create_task(
        command=f"{Agent.CODEX.value}_exec",
        args={"prompt": prompt.strip(), "working_directory": working_directory, "enable_search": enable_search},
        context=ctx,
    )

    task.async_task = asyncio.create_task(engine.run_agent(
        task, codex_runner, mode="exec",
        prompt=prompt.strip(), working_directory=working_directory, enable_search=enable_search
    ))

    return TaskResponse(
        success=True,
        task_id=task.task_id,
        status=task.status,
        message="Codex session started. Use wait_for_task to get result.",
    ).model_dump()


@mcp.tool()
async def resume_codex_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send to the resumed session"),
    session_id: str | None = Field(default=None, description="Session ID to resume (uses --last if not provided)"),
    working_directory: str | None = Field(default=None, description="Working directory for Codex (--cd flag)"),
    enable_search: bool = Field(default=True, description="Enable web search (--search flag)")
) -> dict:
    """Resume an existing Codex session and ask for advice."""
    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    use_last = not session_id or not session_id.strip()
    session_ref = "--last" if use_last else session_id.strip()

    # Validate session ID if provided (not using --last)
    if not use_last and not codex_runner.validate_session_id(session_ref):
        return TaskResponse(
            success=False,
            error=f"Invalid session_id: '{session_id}' - contains disallowed characters",
            error_code=ErrorCode.INVALID_ARGS
        ).model_dump()

    task = engine.create_task(
        command=f"{Agent.CODEX.value}_resume",
        args={"session_id": session_ref, "prompt": prompt.strip(), "working_directory": working_directory, "enable_search": enable_search},
        context=ctx,
    )

    task.async_task = asyncio.create_task(engine.run_agent(
        task, codex_runner, mode="resume",
        prompt=prompt.strip(), session_ref=session_ref, working_directory=working_directory, enable_search=enable_search
    ))

    return TaskResponse(
        success=True,
        task_id=task.task_id,
        status=task.status,
        message=f"Codex resume started{' (last session)' if use_last else f' for session {session_id}'}. Use wait_for_task to get result.",
    ).model_dump()


# === Gemini Tools ===

@mcp.tool()
async def start_gemini_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send"),
    working_directory: str | None = Field(default=None, description="Working directory for Gemini context"),
) -> dict:
    """Start a new Gemini CLI session (no prior context)."""
    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    task = engine.create_task(
        command=f"{Agent.GEMINI.value}_exec",
        args={"prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )

    task.async_task = asyncio.create_task(engine.run_agent(
        task, gemini_runner, mode="exec",
        prompt=prompt.strip(), working_directory=working_directory
    ))

    return TaskResponse(
        success=True,
        task_id=task.task_id,
        status=task.status,
        message="Gemini session started. Use wait_for_task to get result.",
    ).model_dump()


@mcp.tool()
async def resume_gemini_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send to the resumed session"),
    session_ref: str = Field(default="latest", description="Session to resume: 'latest' for most recent, or index number"),
    working_directory: str | None = Field(default=None, description="Working directory for Gemini context"),
) -> dict:
    """Resume an existing Gemini CLI session with full conversation history."""
    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    # Validate session reference (must be numeric index or "latest")
    if not gemini_runner.validate_session_id(session_ref):
        return TaskResponse(
            success=False,
            error=f"Invalid session_ref: '{session_ref}' - must be 'latest' or a numeric index",
            error_code=ErrorCode.INVALID_ARGS
        ).model_dump()

    task = engine.create_task(
        command=f"{Agent.GEMINI.value}_resume",
        args={"session_ref": session_ref, "prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )

    task.async_task = asyncio.create_task(engine.run_agent(
        task, gemini_runner, mode="resume",
        prompt=prompt.strip(), session_ref=session_ref, working_directory=working_directory
    ))

    return TaskResponse(
        success=True,
        task_id=task.task_id,
        status=task.status,
        message=f"Gemini resume started (session: {session_ref}). Use wait_for_task to get result.",
    ).model_dump()


# === OpenCode Tools ===

@mcp.tool()
async def start_opencode_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send"),
    working_directory: str | None = Field(default=None, description="Working directory for OpenCode context"),
) -> dict:
    """Start a new OpenCode session (no prior context)."""
    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    task = engine.create_task(
        command=f"{Agent.OPENCODE.value}_exec",
        args={"prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )

    task.async_task = asyncio.create_task(engine.run_agent(
        task, opencode_runner, mode="exec",
        prompt=prompt.strip(), working_directory=working_directory
    ))

    return TaskResponse(
        success=True,
        task_id=task.task_id,
        status=task.status,
        message="OpenCode session started. Use wait_for_task to get result.",
    ).model_dump()


@mcp.tool()
async def resume_opencode_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send to the resumed session"),
    session_id: str | None = Field(default=None, description="Session ID to resume (uses --continue if not provided)"),
    working_directory: str | None = Field(default=None, description="Working directory for OpenCode context"),
) -> dict:
    """Resume an existing OpenCode session with full conversation history."""
    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    use_continue = not session_id or not session_id.strip()
    session_ref = "--continue" if use_continue else session_id.strip()

    # Validate session ID if provided (not using --continue)
    if not use_continue and not opencode_runner.validate_session_id(session_ref):
        return TaskResponse(
            success=False,
            error=f"Invalid session_id: '{session_id}' - contains disallowed characters",
            error_code=ErrorCode.INVALID_ARGS
        ).model_dump()

    task = engine.create_task(
        command=f"{Agent.OPENCODE.value}_resume",
        args={"session_id": session_ref, "prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )

    task.async_task = asyncio.create_task(engine.run_agent(
        task, opencode_runner, mode="resume",
        prompt=prompt.strip(), session_ref=session_ref, working_directory=working_directory
    ))

    return TaskResponse(
        success=True,
        task_id=task.task_id,
        status=task.status,
        message=f"OpenCode resume started{' (continuing last session)' if use_continue else f' for session {session_id}'}. Use wait_for_task to get result.",
    ).model_dump()


# === Claude OpenRouter Session Tools ===

@mcp.tool()
async def start_claudeor_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send"),
    working_directory: str | None = Field(default=None, description="Working directory for Claude context"),
) -> dict:
    """
    Start a new Claude Code session via OpenRouter.

    Uses Claude CLI with OpenRouter backend, allowing alternative models
    like DeepSeek, GPT-4o, Gemini, etc. Configure model via CLAUDEOR_MODEL env var.
    """
    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    # Check if API key is configured
    if not config.claudeor.api_key:
        return TaskResponse(
            success=False,
            error="OPENROUTER_API_KEY or CLAUDEOR_API_KEY environment variable not set",
            error_code=ErrorCode.INVALID_ARGS
        ).model_dump()

    task = engine.create_task(
        command=f"{Agent.CLAUDEOR.value}_exec",
        args={"prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )

    task.async_task = asyncio.create_task(engine.run_agent(
        task, claudeor_runner, mode="exec",
        prompt=prompt.strip(), working_directory=working_directory
    ))

    model_info = f" ({config.claudeor.model})" if config.claudeor.model else ""
    return TaskResponse(
        success=True,
        task_id=task.task_id,
        status=task.status,
        message=f"Claude OpenRouter{model_info} session started. Use wait_for_task to get result.",
    ).model_dump()


@mcp.tool()
async def resume_claudeor_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send to the resumed session"),
    session_id: str | None = Field(default=None, description="Session ID to resume (uses --continue if not provided)"),
    working_directory: str | None = Field(default=None, description="Working directory for Claude context"),
) -> dict:
    """Resume an existing Claude OpenRouter session with full conversation history."""
    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    # Check if API key is configured
    if not config.claudeor.api_key:
        return TaskResponse(
            success=False,
            error="OPENROUTER_API_KEY or CLAUDEOR_API_KEY environment variable not set",
            error_code=ErrorCode.INVALID_ARGS
        ).model_dump()

    use_continue = not session_id or not session_id.strip()
    session_ref = "continue" if use_continue else session_id.strip()

    # Validate session ID if provided
    if not use_continue and not claudeor_runner.validate_session_id(session_ref):
        return TaskResponse(
            success=False,
            error=f"Invalid session_id: '{session_id}' - contains disallowed characters",
            error_code=ErrorCode.INVALID_ARGS
        ).model_dump()

    task = engine.create_task(
        command=f"{Agent.CLAUDEOR.value}_resume",
        args={"session_id": session_ref, "prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )

    task.async_task = asyncio.create_task(engine.run_agent(
        task, claudeor_runner, mode="resume",
        prompt=prompt.strip(), session_ref=session_ref, working_directory=working_directory
    ))

    model_info = f" ({config.claudeor.model})" if config.claudeor.model else ""
    return TaskResponse(
        success=True,
        task_id=task.task_id,
        status=task.status,
        message=f"Claude OpenRouter{model_info} resume started{' (continuing last session)' if use_continue else f' for session {session_id}'}. Use wait_for_task to get result.",
    ).model_dump()


# === AiChat Tools ===

@mcp.tool()
async def start_aichat_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send"),
    working_directory: str | None = Field(default=None, description="Working directory for aichat context"),
) -> dict:
    """
    Start a new aichat session.

    Uses the aichat CLI for multi-provider LLM access. Configure model via
    AICHAT_MODEL env var or aichat's own config file (~/.config/aichat/config.yaml).
    """
    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    task = engine.create_task(
        command=f"{Agent.AICHAT.value}_exec",
        args={"prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )

    task.async_task = asyncio.create_task(engine.run_agent(
        task, aichat_runner, mode="exec",
        prompt=prompt.strip(), working_directory=working_directory
    ))

    model_info = f" ({config.aichat.model})" if config.aichat.model else ""
    return TaskResponse(
        success=True,
        task_id=task.task_id,
        status=task.status,
        message=f"AiChat{model_info} session started. Use wait_for_task to get result.",
    ).model_dump()


@mcp.tool()
async def resume_aichat_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send to the resumed session"),
    session_id: str = Field(description="Session name to resume"),
    working_directory: str | None = Field(default=None, description="Working directory for aichat context"),
) -> dict:
    """Resume an existing aichat session with full conversation history."""
    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    if not session_id or not session_id.strip():
        return TaskResponse(success=False, error="'session_id' parameter is required for aichat resume.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    session_ref = session_id.strip()

    # Validate session ID
    if not aichat_runner.validate_session_id(session_ref):
        return TaskResponse(
            success=False,
            error=f"Invalid session_id: '{session_id}' - contains disallowed characters",
            error_code=ErrorCode.INVALID_ARGS
        ).model_dump()

    task = engine.create_task(
        command=f"{Agent.AICHAT.value}_resume",
        args={"session_id": session_ref, "prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )

    task.async_task = asyncio.create_task(engine.run_agent(
        task, aichat_runner, mode="resume",
        prompt=prompt.strip(), session_ref=session_ref, working_directory=working_directory
    ))

    model_info = f" ({config.aichat.model})" if config.aichat.model else ""
    return TaskResponse(
        success=True,
        task_id=task.task_id,
        status=task.status,
        message=f"AiChat{model_info} resume started for session {session_id}. Use wait_for_task to get result.",
    ).model_dump()


# === Cursor Agent Tools ===

@mcp.tool()
async def start_cursor_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send"),
    working_directory: str | None = Field(default=None, description="Working directory for Cursor Agent (--workspace flag)"),
) -> dict:
    """
    Start a new Cursor Agent CLI session.

    Uses the Cursor Agent CLI for AI-powered coding assistance. Configure model via
    CURSOR_MODEL env var. Requires a Cursor subscription.
    """
    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    task = engine.create_task(
        command=f"{Agent.CURSOR.value}_exec",
        args={"prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )

    task.async_task = asyncio.create_task(engine.run_agent(
        task, cursor_runner, mode="exec",
        prompt=prompt.strip(), working_directory=working_directory
    ))

    model_info = f" ({config.cursor.model})" if config.cursor.model else ""
    return TaskResponse(
        success=True,
        task_id=task.task_id,
        status=task.status,
        message=f"Cursor{model_info} session started. Use wait_for_task to get result.",
    ).model_dump()


@mcp.tool()
async def resume_cursor_session(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or request to send to the resumed session"),
    session_id: str | None = Field(default=None, description="Chat ID to resume (uses --continue if not provided)"),
    working_directory: str | None = Field(default=None, description="Working directory for Cursor Agent (--workspace flag)"),
) -> dict:
    """Resume an existing Cursor Agent session with full conversation history."""
    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    use_continue = not session_id or not session_id.strip()
    session_ref = "--continue" if use_continue else session_id.strip()

    if not use_continue and not cursor_runner.validate_session_id(session_ref):
        return TaskResponse(
            success=False,
            error=f"Invalid session_id: '{session_id}' - contains disallowed characters",
            error_code=ErrorCode.INVALID_ARGS
        ).model_dump()

    task = engine.create_task(
        command=f"{Agent.CURSOR.value}_resume",
        args={"session_id": session_ref, "prompt": prompt.strip(), "working_directory": working_directory},
        context=ctx,
    )

    task.async_task = asyncio.create_task(engine.run_agent(
        task, cursor_runner, mode="resume",
        prompt=prompt.strip(), session_ref=session_ref, working_directory=working_directory
    ))

    model_info = f" ({config.cursor.model})" if config.cursor.model else ""
    return TaskResponse(
        success=True,
        task_id=task.task_id,
        status=task.status,
        message=f"Cursor{model_info} resume started{' (continue)' if use_continue else f' for session {session_id}'}. Use wait_for_task to get result.",
    ).model_dump()


# === Task Management Tools ===

@mcp.tool()
async def get_task_result(task_id: str) -> dict:
    """
    Get the result of a task (Codex, Gemini, or OpenCode).

    Args:
        task_id: The task ID returned by start/resume session
    """
    task = engine.get_task(task_id)
    if not task:
        return TaskResponse(success=False, error=f"Task '{task_id}' not found.", error_code=ErrorCode.NOT_FOUND).model_dump()

    if task.status == "pending":
        return TaskResponse(
            success=True,
            task_id=task_id,
            status=task.status,
            message="Task is still pending.",
        ).model_dump()
    elif task.status == "running":
        elapsed = (datetime.now() - task.start_time).total_seconds()
        return TaskResponse(
            success=True,
            task_id=task_id,
            status=task.status,
            message=f"Task is still running ({elapsed:.1f}s elapsed).",
        ).model_dump()
    elif task.status == "completed":
        return TaskResponse(
            success=True,
            task_id=task_id,
            status=task.status,
            content=task.result,
            warnings=task.warnings,
            duration_seconds=(task.completion_time - task.start_time).total_seconds() if task.completion_time else None,
        ).model_dump()
    elif task.status == "failed":
        return TaskResponse(
            success=False,
            task_id=task_id,
            status=task.status,
            error=task.error,
            error_code=ErrorCode.EXECUTION_FAILED,
            duration_seconds=(task.completion_time - task.start_time).total_seconds() if task.completion_time else None,
        ).model_dump()
    elif task.status == "cancelled":
        return TaskResponse(
            success=False,
            task_id=task_id,
            status=task.status,
            error=task.error or "Task was cancelled.",
            error_code=ErrorCode.CANCELLED,
            duration_seconds=(task.completion_time - task.start_time).total_seconds() if task.completion_time else None,
        ).model_dump()
    else:
        # Unknown status - should not happen but handle gracefully
        return TaskResponse(
            success=False,
            task_id=task_id,
            status=task.status,
            error=f"Unexpected task status: {task.status}",
            error_code=ErrorCode.INTERNAL_ERROR,
        ).model_dump()


@mcp.tool()
async def wait_for_task(task_id: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """
    Wait for a task to complete and return its result.

    Args:
        task_id: The task ID to wait for
        timeout: Maximum seconds to wait (default: 300)
    """
    task = engine.get_task(task_id)
    if not task:
        return TaskResponse(success=False, error=f"Task '{task_id}' not found.", error_code=ErrorCode.NOT_FOUND).model_dump()

    if task.status in ["completed", "failed", "cancelled"]:
        if task.status == "completed":
            return TaskResponse(
                success=True,
                task_id=task_id,
                status=task.status,
                content=task.result,
                warnings=task.warnings,
                duration_seconds=(task.completion_time - task.start_time).total_seconds() if task.completion_time else None,
            ).model_dump()
        error_code = ErrorCode.EXECUTION_FAILED if task.status == "failed" else ErrorCode.CANCELLED
        return TaskResponse(
            success=False,
            task_id=task_id,
            status=task.status,
            error=task.error,
            error_code=error_code,
        ).model_dump()

    if task.async_task:
        # Check if task already completed (e.g., between abort and re-wait)
        if task.async_task.done():
            try:
                task.async_task.result()  # Re-raise any exception from the task
            except asyncio.CancelledError:
                # Task was cancelled - mark appropriately
                if task.status not in ["completed", "failed", "cancelled"]:
                    task.status = "cancelled"
                    task.error = "Task was cancelled"
                    task.completion_time = datetime.now()
            except BaseException as e:
                # Catch BaseException to handle all exceptions including SystemExit, KeyboardInterrupt
                if task.status not in ["completed", "failed", "cancelled"]:
                    task.status = "failed"
                    task.error = f"Task failed: {str(e)}"
                    task.completion_time = datetime.now()
            # Fall through to return result below
        else:
            try:
                await asyncio.wait_for(asyncio.shield(task.async_task), timeout=timeout)
            except asyncio.TimeoutError:
                return TaskResponse(
                    success=False,
                    task_id=task_id,
                    status="timeout",
                    error=f"Task still running after {timeout}s. Use get_task_result to check later.",
                    error_code=ErrorCode.TIMEOUT,
                ).model_dump()
            except asyncio.CancelledError:
                # User aborted the wait (e.g., pressed ESC) - task keeps running
                return TaskResponse(
                    success=True,
                    task_id=task_id,
                    status=task.status,
                    message="Wait aborted. Task still running. Use get_task_result or wait_for_task later.",
                ).model_dump()
            except Exception as e:
                # Bug fix: Set task.status and task.error so subsequent calls are consistent
                task.status = "failed"
                task.error = f"Task failed: {str(e)}"
                task.completion_time = datetime.now()
                return TaskResponse(
                    success=False,
                    task_id=task_id,
                    status=task.status,
                    error=task.error,
                    error_code=ErrorCode.INTERNAL_ERROR,
                ).model_dump()
    else:
        # Bug fix: No async_task means task may have failed before launch or is in unexpected state
        # Return actual status instead of assuming CANCELLED
        return TaskResponse(
            success=False,
            task_id=task_id,
            status=task.status,
            error=task.error or f"Task has no async handler (status: {task.status})",
            error_code=ErrorCode.INTERNAL_ERROR,
        ).model_dump()

    if task.status == "completed":
        return TaskResponse(
            success=True,
            task_id=task_id,
            status=task.status,
            content=task.result,
            warnings=task.warnings,
            duration_seconds=(task.completion_time - task.start_time).total_seconds() if task.completion_time else None,
        ).model_dump()

    error_code = ErrorCode.EXECUTION_FAILED if task.status == "failed" else ErrorCode.CANCELLED
    return TaskResponse(
        success=False,
        task_id=task_id,
        status=task.status,
        error=task.error,
        error_code=error_code,
    ).model_dump()


@mcp.tool()
async def list_tasks(
    status_filter: str | None = Field(default=None, description="Filter by status: pending, running, completed, failed, cancelled"),
    limit: int = Field(default=20, description="Maximum number of tasks to return"),
) -> dict:
    """
    List all tracked tasks with their current status.

    Args:
        status_filter: Optional filter by task status
        limit: Maximum number of tasks to return (default: 20)
    """
    tasks_list = []
    for task_id, task in list(engine.tasks.items())[-limit:]:
        if status_filter and task.status != status_filter:
            continue
        elapsed = (datetime.now() - task.start_time).total_seconds()
        tasks_list.append({
            "task_id": task_id,
            "command": task.command,
            "status": task.status,
            "elapsed_seconds": round(elapsed, 1),
            "has_result": task.result is not None,
            "has_error": task.error is not None,
        })

    return {
        "success": True,
        "count": len(tasks_list),
        "tasks": tasks_list,
    }


@mcp.tool()
async def cancel_task(task_id: str) -> dict:
    """
    Cancel a running task and kill its subprocess.

    Args:
        task_id: The task ID to cancel
    """
    task = engine.get_task(task_id)
    if not task:
        return TaskResponse(success=False, error=f"Task '{task_id}' not found.", error_code=ErrorCode.NOT_FOUND).model_dump()

    if task.status in ["completed", "failed", "cancelled"]:
        return TaskResponse(
            success=False,
            task_id=task_id,
            status=task.status,
            error=f"Task already {task.status}, cannot cancel.",
            error_code=ErrorCode.INVALID_ARGS,
        ).model_dump()

    # Kill the subprocess and cancel the async task
    await engine.kill_task_subprocess(task)
    task.status = "cancelled"
    task.error = "Cancelled by user"
    task.completion_time = datetime.now()

    return TaskResponse(
        success=True,
        task_id=task_id,
        status=task.status,
        message="Task cancelled successfully.",
    ).model_dump()


# === Council Tool ===

@mcp.tool()
async def agent_timing(
    last_n: int = Field(default=20, description="Number of recent entries to return"),
    agent_filter: str | None = Field(default=None, description="Filter by agent name (e.g. 'codex', 'gemini')"),
) -> str:
    """Show recent agent execution timing from the canonical store at ~/.owlex/owlex.db.
    Use this to diagnose which CLI agent is slow."""
    import json as _json
    from . import store as _store

    conn = _store.connect()

    where, args = "WHERE status != 'running'", []
    if agent_filter:
        where += " AND command LIKE ?"
        args.append(f"%{agent_filter.lower()}%")

    recent_rows = conn.execute(
        f"""SELECT task_id, command, status, duration_s, completed_at, council_id, error
              FROM calls {where}
             ORDER BY completed_at DESC
             LIMIT ?""",
        [*args, last_n],
    ).fetchall()

    recent = [
        {
            "ts": r["completed_at"],
            "task_id": (r["task_id"] or "")[:8],
            "command": r["command"],
            "status": r["status"],
            "duration_s": round(r["duration_s"] or 0.0, 1),
            **({"council_id": r["council_id"]} if r["council_id"] else {}),
            **({"error": (r["error"] or "")[:200]} if r["status"] == "failed" and r["error"] else {}),
        }
        for r in recent_rows
    ]

    summary_rows = conn.execute(
        """SELECT command,
                  COUNT(*)        AS count,
                  AVG(duration_s) AS avg_s,
                  MAX(duration_s) AS max_s,
                  MIN(duration_s) AS min_s
             FROM calls
            WHERE status != 'running' AND duration_s > 0
            GROUP BY command
            ORDER BY command"""
    ).fetchall()

    summary = {
        r["command"]: {
            "count": r["count"],
            "avg_s": round(r["avg_s"] or 0.0, 1),
            "max_s": round(r["max_s"] or 0.0, 1),
            "min_s": round(r["min_s"] or 0.0, 1),
        }
        for r in summary_rows
    }

    if not recent and not summary:
        return _json.dumps({"message": "No timing data yet. Run a council or agent task first.", "entries": []})

    return _json.dumps({"summary": summary, "recent": recent}, indent=2)


@mcp.tool()
async def council_ask(
    ctx: Context[ServerSession, None],
    prompt: str = Field(description="The question or task to send to the council"),
    claude_opinion: str | None = Field(default=None, description="Claude's initial opinion (optional)"),
    working_directory: str | None = Field(default=None, description="Working directory for context"),
    deliberate: bool | str = Field(default="auto", description="If true, always run R2; 'auto' skips on consensus; false = R1 only"),
    critique: bool = Field(default=False, description="If true, R2 critiques instead of revising"),
    timeout: int = Field(default=DEFAULT_TIMEOUT, description="Timeout per agent in seconds"),
    roles: dict[str, str] | list[str] | None = Field(default=None, description="Role assignments (same shape as council_ask)"),
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

    Workflow:
      1. Call this tool with your prompt.
      2. Read each Response A/B/C/... and form your synthesis.
      3. Call rate_council with one entry per letter:
         {"A": {"score": -1|+1, "groundedness"?: 1-5, "helpfulness"?: 1-5,
                "correctness"?: 1-5, "reason"?: "one sentence"}}
      4. THEN present the synthesized answer to the user.
    """
    import random as _random
    from .prompts import anonymize_round_responses

    blocked = _council_recursion_block("council_ask")
    if blocked is not None:
        return blocked

    if not prompt or not prompt.strip():
        return TaskResponse(success=False, error="'prompt' parameter is required.", error_code=ErrorCode.INVALID_ARGS).model_dump()

    working_directory, error = _validate_working_directory(working_directory)
    if error:
        return TaskResponse(success=False, error=error, error_code=ErrorCode.INVALID_ARGS).model_dump()

    if isinstance(roles, str):
        try:
            roles = json.loads(roles)
        except (json.JSONDecodeError, TypeError):
            return TaskResponse(
                success=False,
                error=f"Invalid roles format: expected a JSON list or dict, got string: {roles[:100]}",
                error_code=ErrorCode.INVALID_ARGS
            ).model_dump()

    if roles is not None and team is not None:
        return TaskResponse(
            success=False,
            error="Cannot specify both 'roles' and 'team' parameters. Use one or the other.",
            error_code=ErrorCode.INVALID_ARGS
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
        return TaskResponse(
            success=False,
            error=f"Council deliberation failed: {e}",
            error_code=ErrorCode.EXECUTION_FAILED,
        ).model_dump()

    # Anonymize using a council-id-salted RNG so R1 and R2 share the same mapping.
    council_id = council.council_id
    salt = f"blind:{council_id}"
    r1_by_label, mapping = anonymize_round_responses(response.round_1, salt=salt)
    r2_by_label, _ = anonymize_round_responses(response.round_2, salt=salt) if response.round_2 else (None, None)

    if mapping:
        store.record_council_anonymization(council_id, mapping)

    def _to_anon(ar):
        if ar is None:
            return None
        d = ar.model_dump()
        # Strip every field that could leak identity (agent name, session id).
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

    payload = {
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
    return payload


@mcp.tool()
async def rate_council(
    council_id: str = Field(description="The council_id returned by council_ask"),
    ratings: dict = Field(description="Map of letter → rating dict, e.g. {'A': {'score': 1, 'groundedness': 4, 'reason': '...'}, 'B': {...}}"),
) -> dict:
    """Submit per-letter blind ratings for a council. The server resolves
    letters to agents and writes one row per agent into ``agent_scores``.
    Required follow-up after every council_ask call.
    """
    blocked = _council_recursion_block("rate_council")
    if blocked is not None:
        return blocked

    if isinstance(ratings, str):
        try:
            ratings = json.loads(ratings)
        except (json.JSONDecodeError, TypeError):
            return TaskResponse(
                success=False,
                error=f"Invalid ratings format: expected a JSON dict",
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
                council_id,
                agent,
                int(score),
                rater="claude_blind",
                dimensions=dimensions or None,
                reason=raw.get("reason"),
            )
            rated.append(agent)
        except Exception as e:
            errors.append(f"{label} ({agent}): {e}")

    return {
        "ok": len(errors) == 0,
        "agents_rated": rated,
        "errors": errors,
    }


def main():
    """Entry point for owlex-server command."""
    import argparse
    import signal

    from . import __version__

    parser = argparse.ArgumentParser(
        prog="owlex-server",
        description="MCP server for multi-agent CLI orchestration"
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"owlex {__version__}"
    )
    parser.parse_args()

    async def run_with_cleanup():
        loop = asyncio.get_running_loop()
        shutdown_event = asyncio.Event()

        def signal_handler(sig):
            _log(f"Received signal {sig}, shutting down...")
            shutdown_event.set()

        # Register signal handlers for graceful shutdown
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        engine.start_cleanup_loop()
        try:
            # Run MCP server with shutdown monitoring
            server_task = asyncio.create_task(mcp.run_stdio_async())
            shutdown_task = asyncio.create_task(shutdown_event.wait())

            done, pending = await asyncio.wait(
                [server_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            # If shutdown was triggered, cancel the server
            if shutdown_task in done:
                server_task.cancel()
                try:
                    await server_task
                except asyncio.CancelledError:
                    pass
            # If server task completed (possibly due to client disconnect), log it
            if server_task in done:
                try:
                    server_task.result()  # Re-raise any exception
                except Exception as e:
                    _log(f"Server task ended: {e}")
        except asyncio.CancelledError:
            _log("Server cancelled")
        except Exception as e:
            _log(f"Server error: {e}")
        finally:
            # Kill all running tasks before exit
            await engine.kill_all_tasks()
            engine.stop_cleanup_loop()
            _log("Server shutdown complete.")

    asyncio.run(run_with_cleanup())


if __name__ == "__main__":
    main()
