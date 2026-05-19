"""Task lifecycle MCP tools: get_task_result, wait_for_task, list_tasks, cancel_task, agent_timing."""
from __future__ import annotations

import asyncio
import json as _json
from datetime import datetime

from pydantic import Field

from . import mcp
from ..engine import engine, DEFAULT_TIMEOUT
from ..models import ErrorCode, TaskResponse


@mcp.tool()
async def get_task_result(task_id: str) -> dict:
    """Get the result of a task (Codex, Gemini, or OpenCode)."""
    task = engine.get_task(task_id)
    if not task:
        return TaskResponse(success=False, error=f"Task '{task_id}' not found.", error_code=ErrorCode.NOT_FOUND).model_dump()

    if task.status == "pending":
        return TaskResponse(success=True, task_id=task_id, status=task.status, message="Task is still pending.").model_dump()
    elif task.status == "running":
        elapsed = (datetime.now() - task.start_time).total_seconds()
        return TaskResponse(success=True, task_id=task_id, status=task.status,
                            message=f"Task is still running ({elapsed:.1f}s elapsed).").model_dump()
    elif task.status == "completed":
        return TaskResponse(
            success=True, task_id=task_id, status=task.status,
            content=task.result, warnings=task.warnings,
            duration_seconds=(task.completion_time - task.start_time).total_seconds() if task.completion_time else None,
        ).model_dump()
    elif task.status == "failed":
        return TaskResponse(
            success=False, task_id=task_id, status=task.status,
            error=task.error, error_code=ErrorCode.EXECUTION_FAILED,
            duration_seconds=(task.completion_time - task.start_time).total_seconds() if task.completion_time else None,
        ).model_dump()
    elif task.status == "cancelled":
        return TaskResponse(
            success=False, task_id=task_id, status=task.status,
            error=task.error or "Task was cancelled.",
            error_code=ErrorCode.CANCELLED,
            duration_seconds=(task.completion_time - task.start_time).total_seconds() if task.completion_time else None,
        ).model_dump()
    else:
        return TaskResponse(
            success=False, task_id=task_id, status=task.status,
            error=f"Unexpected task status: {task.status}",
            error_code=ErrorCode.INTERNAL_ERROR,
        ).model_dump()


@mcp.tool()
async def wait_for_task(task_id: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Wait for a task to complete and return its result."""
    task = engine.get_task(task_id)
    if not task:
        return TaskResponse(success=False, error=f"Task '{task_id}' not found.", error_code=ErrorCode.NOT_FOUND).model_dump()

    if task.status in ["completed", "failed", "cancelled"]:
        if task.status == "completed":
            return TaskResponse(
                success=True, task_id=task_id, status=task.status,
                content=task.result, warnings=task.warnings,
                duration_seconds=(task.completion_time - task.start_time).total_seconds() if task.completion_time else None,
            ).model_dump()
        error_code = ErrorCode.EXECUTION_FAILED if task.status == "failed" else ErrorCode.CANCELLED
        return TaskResponse(success=False, task_id=task_id, status=task.status,
                            error=task.error, error_code=error_code).model_dump()

    if task.async_task:
        if task.async_task.done():
            try:
                task.async_task.result()
            except asyncio.CancelledError:
                if task.status not in ["completed", "failed", "cancelled"]:
                    task.status = "cancelled"
                    task.error = "Task was cancelled"
                    task.completion_time = datetime.now()
            except BaseException as e:
                if task.status not in ["completed", "failed", "cancelled"]:
                    task.status = "failed"
                    task.error = f"Task failed: {str(e)}"
                    task.completion_time = datetime.now()
        else:
            try:
                await asyncio.wait_for(asyncio.shield(task.async_task), timeout=timeout)
            except asyncio.TimeoutError:
                return TaskResponse(
                    success=False, task_id=task_id, status="timeout",
                    error=f"Task still running after {timeout}s. Use get_task_result to check later.",
                    error_code=ErrorCode.TIMEOUT,
                ).model_dump()
            except asyncio.CancelledError:
                return TaskResponse(
                    success=True, task_id=task_id, status=task.status,
                    message="Wait aborted. Task still running. Use get_task_result or wait_for_task later.",
                ).model_dump()
            except Exception as e:
                task.status = "failed"
                task.error = f"Task failed: {str(e)}"
                task.completion_time = datetime.now()
                return TaskResponse(
                    success=False, task_id=task_id, status=task.status,
                    error=task.error, error_code=ErrorCode.INTERNAL_ERROR,
                ).model_dump()
    else:
        return TaskResponse(
            success=False, task_id=task_id, status=task.status,
            error=task.error or f"Task has no async handler (status: {task.status})",
            error_code=ErrorCode.INTERNAL_ERROR,
        ).model_dump()

    if task.status == "completed":
        return TaskResponse(
            success=True, task_id=task_id, status=task.status,
            content=task.result, warnings=task.warnings,
            duration_seconds=(task.completion_time - task.start_time).total_seconds() if task.completion_time else None,
        ).model_dump()

    error_code = ErrorCode.EXECUTION_FAILED if task.status == "failed" else ErrorCode.CANCELLED
    return TaskResponse(success=False, task_id=task_id, status=task.status,
                        error=task.error, error_code=error_code).model_dump()


@mcp.tool()
async def list_tasks(
    status_filter: str | None = Field(default=None, description="Filter by status: pending, running, completed, failed, cancelled"),
    limit: int = Field(default=20, description="Maximum number of tasks to return"),
) -> dict:
    """List all tracked tasks with their current status."""
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
    return {"success": True, "count": len(tasks_list), "tasks": tasks_list}


@mcp.tool()
async def cancel_task(task_id: str) -> dict:
    """Cancel a running task and kill its subprocess."""
    task = engine.get_task(task_id)
    if not task:
        return TaskResponse(success=False, error=f"Task '{task_id}' not found.", error_code=ErrorCode.NOT_FOUND).model_dump()
    if task.status in ["completed", "failed", "cancelled"]:
        return TaskResponse(
            success=False, task_id=task_id, status=task.status,
            error=f"Task already {task.status}, cannot cancel.",
            error_code=ErrorCode.INVALID_ARGS,
        ).model_dump()

    await engine.kill_task_subprocess(task)
    task.status = "cancelled"
    task.error = "Cancelled by user"
    task.completion_time = datetime.now()

    return TaskResponse(success=True, task_id=task_id, status=task.status,
                        message="Task cancelled successfully.").model_dump()


@mcp.tool()
async def agent_timing(
    last_n: int = Field(default=20, description="Number of recent entries to return"),
    agent_filter: str | None = Field(default=None, description="Filter by agent name (e.g. 'codex', 'gemini')"),
) -> str:
    """Show recent agent execution timing from the canonical store at ~/.owlex/owlex.db."""
    from .. import store as _store
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
