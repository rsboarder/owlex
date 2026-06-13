"""
Lightweight single-call second opinion from one non-Claude frontier model.

Mirrors the lean subprocess pattern in ``agreement.py`` (codex exec + stdin +
wait_for) rather than the heavy ``engine.run_agent`` path — no Task lifecycle,
heartbeat, output-cap, fail-patterns, or recursion fence, none of which a
one-shot read-only call needs.

codex ``--json`` emits a JSONL event stream; the answer is the
``agent_message`` item(s). Extraction fails closed: if the stream cannot be
parsed into a non-empty message (e.g. codex rotates its --json schema), the
caller gets ``success=False`` rather than a silent empty opinion.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile


MODEL = os.getenv("OWLEX_SECOND_OPINION_MODEL", "gpt-5.5")

# Quality-first: a real review, not the agreement judge's shallow "low".
REASONING = os.getenv("OWLEX_SECOND_OPINION_REASONING", "high")

# High reasoning runs longer than the judge; generous headroom for cold-start.
TIMEOUT = int(os.getenv("OWLEX_SECOND_OPINION_TIMEOUT", "120"))

FRAME = (
    "You are an independent second opinion for another AI engineer. Be concise. "
    "Give your own take, name the top risks, end with a clear recommendation.\n\n"
)


def _cmd(cwd: str) -> list[str]:
    """codex exec argv: read-only sandbox, JSON event stream, model pinned.

    ``cwd`` is always passed (an ephemeral tmp dir for the generic case, or the
    caller's repo root when it wants codex to read changed files) so codex does
    not silently inherit the server process's working directory.
    """
    return [
        "codex", "exec", "--skip-git-repo-check", "--json",
        "-c", f'model_reasoning_effort="{REASONING}"',
        "--model", MODEL,
        "--sandbox", "read-only",
        "--cd", cwd,
        "-",  # read prompt from stdin
    ]


def _extract_final_message(stdout: str) -> str:
    """Join the agent_message item(s) from codex --json JSONL output.

    Control events (thread.started/turn.started/turn.completed) and codex's own
    skill-load ERROR lines are skipped: they are not item.completed/agent_message
    or are not valid JSON. Empty join → caller fails closed.
    """
    out: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "item.completed":
            item = ev.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                out.append(item["text"])
    return "\n\n".join(out).strip()


async def _terminate(proc: asyncio.subprocess.Process | None) -> None:
    """Kill and reap a still-running child so a timeout/error can't orphan it.

    No-op if the process never started or already exited. Best-effort: a
    ProcessLookupError (already gone) or a reap failure is swallowed — the
    point is only to not leave a runaway codex behind.
    """
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await proc.wait()
    except Exception:  # noqa: BLE001 — best-effort reap, never raise from cleanup
        pass


async def get_second_opinion(
    prompt: str,
    working_directory: str | None = None,
    timeout: int | None = None,
) -> tuple[bool, str, bool]:
    """Run one codex-exec second opinion. Returns ``(ok, text, timed_out)``.

    ``working_directory=None`` → run in an ephemeral empty tmp dir (clean,
    deterministic, cannot read the repo). A caller that wants repo context
    (e.g. solution-audit) passes the repo root explicitly.

    Fails closed: returncode 0 with an empty/unparseable extraction returns
    ``ok=False`` so a rotated codex --json schema cannot yield a silent empty
    success. ``timed_out`` lets the caller distinguish a timeout (→ retry /
    TIMEOUT code) from a generic failure. The spawned child is always
    killed+reaped on timeout or error so a slow codex run can't be orphaned.
    """
    if timeout is None:
        timeout = TIMEOUT

    async def _run(cwd: str) -> tuple[bool, str, bool]:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *_cmd(cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=(FRAME + prompt).encode()),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await _terminate(proc)
            return False, f"second opinion timed out after {timeout}s (model={MODEL!r})", True
        except FileNotFoundError:
            return False, "codex CLI not found on PATH", False
        except Exception as e:  # noqa: BLE001 - surface any spawn failure to caller
            await _terminate(proc)
            return False, f"second opinion error: {e}", False

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            combined = (out + err).strip()
            return False, f"codex exited {proc.returncode}: {combined[:300]}", False

        text = _extract_final_message(out)
        if not text:
            return False, "empty/unparseable codex --json output", False
        return True, text, False

    if working_directory:
        return await _run(working_directory)

    with tempfile.TemporaryDirectory(prefix="owlex-second-opinion-") as tmp:
        return await _run(tmp)
