"""MCP resources: owlex://agents and owlex://council/status."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from . import mcp
from ._validators import (
    get_aichat_model, get_codex_model, get_cursor_model,
    get_gemini_model, get_opencode_model, get_cli_version,
)
from ..config import config
from ..engine import engine


@mcp.resource("owlex://agents")
async def get_agents() -> str:
    """List available agents and their configuration."""
    excluded = config.council.exclude_agents

    codex_ver, gemini_ver, opencode_ver, aichat_ver, cursor_ver = await asyncio.gather(
        get_cli_version("codex"),
        get_cli_version("gemini"),
        get_cli_version("opencode"),
        get_cli_version("aichat"),
        get_cli_version("cursor-agent"),
    )

    agents = {
        "codex": {
            "available": "codex" not in excluded,
            "cli_version": codex_ver,
            "model": get_codex_model(),
            "description": "Deep reasoning, code review, bug finding",
            "config": {
                "enable_search": config.codex.enable_search,
                "bypass_approvals": config.codex.bypass_approvals,
            }
        },
        "gemini": {
            "available": "gemini" not in excluded,
            "cli_version": gemini_ver,
            "model": get_gemini_model(),
            "description": "1M context window, multimodal, large codebases",
            "config": {"yolo_mode": config.gemini.yolo_mode}
        },
        "opencode": {
            "available": "opencode" not in excluded,
            "cli_version": opencode_ver,
            "model": get_opencode_model(),
            "description": "Alternative perspective, configurable models",
            "config": {"agent_mode": config.opencode.agent}
        },
        "aichat": {
            "available": "aichat" not in excluded,
            "cli_version": aichat_ver,
            "model": get_aichat_model(),
            "description": "Multi-provider LLM CLI, bring your own model",
            "config": {"model": config.aichat.model}
        },
        "cursor": {
            "available": "cursor" not in excluded,
            "cli_version": cursor_ver,
            "model": get_cursor_model(),
            "description": "Cursor Agent CLI, multi-model coding assistant",
            "config": {"model": config.cursor.model, "force_mode": config.cursor.force_mode}
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
            prompt = task.args.get("prompt", "")
            council_tasks.append({
                "task_id": task_id,
                "status": task.status,
                "elapsed_seconds": round(elapsed, 1),
                "prompt": prompt[:100] + "..." if len(prompt) > 100 else prompt,
                "deliberate": task.args.get("deliberate", True),
                "critique": task.args.get("critique", False),
            })

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
