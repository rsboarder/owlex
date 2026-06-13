"""owlex MCP server — entry point and FastMCP instance.

Tool definitions live in sibling modules and register via decorators on the
shared ``mcp`` instance below. Importing those modules at the bottom of this
file is what makes the registration take effect; the imports are intentional.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import traceback
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .. import __version__
from ..engine import engine
from ..store import _owlex_home
from ._validators import (
    council_recursion_block as _council_recursion_block,  # back-compat
    log,
    validate_working_directory as _validate_working_directory,  # back-compat
)


class _StderrTee:
    """Mirror writes to original stderr and a log file so failures are diagnosable post-mortem."""

    def __init__(self, original, log_file):
        self._original = original
        self._log_file = log_file

    def write(self, data):
        try:
            self._original.write(data)
        except Exception:
            pass
        try:
            self._log_file.write(data)
        except Exception:
            pass
        return len(data) if isinstance(data, str) else 0

    def flush(self):
        for s in (self._original, self._log_file):
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        return getattr(self._original, "isatty", lambda: False)()

    def fileno(self):
        return self._original.fileno()


def _install_stderr_logging() -> Path | None:
    """Tee stderr to ~/.owlex/logs/server-{pid}.log; install crash hook."""
    if os.environ.get("OWLEX_DISABLE_SERVER_LOG") == "1":
        return None
    log_dir = _owlex_home() / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[owlex] could not create log dir {log_dir}: {e}", file=sys.stderr, flush=True)
        return None

    log_path = log_dir / f"server-{os.getpid()}.log"
    try:
        fh = open(log_path, "a", buffering=1, encoding="utf-8")
    except OSError as e:
        print(f"[owlex] could not open {log_path}: {e}", file=sys.stderr, flush=True)
        return None

    fh.write(
        f"\n--- owlex-server {__version__} pid={os.getpid()} "
        f"started {datetime.now().isoformat()} ---\n"
    )
    sys.stderr = _StderrTee(sys.stderr, fh)

    def _on_uncaught(exc_type, exc, tb):
        msg = "".join(traceback.format_exception(exc_type, exc, tb))
        print(f"[owlex] UNCAUGHT EXCEPTION:\n{msg}", file=sys.stderr, flush=True)

    sys.excepthook = _on_uncaught
    return log_path


def _install_asyncio_exception_handler() -> None:
    """Log unhandled task exceptions instead of letting asyncio drop them silently."""
    def handler(loop, context):
        exc = context.get("exception")
        if exc is not None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            print(
                f"[owlex] asyncio task error: {context.get('message','')}\n{tb}",
                file=sys.stderr, flush=True,
            )
        else:
            print(f"[owlex] asyncio: {context.get('message','')}", file=sys.stderr, flush=True)
    asyncio.get_running_loop().set_exception_handler(handler)

mcp = FastMCP("owlex-server")

# Register resources and tools by importing their modules. Order matters only
# in that ``mcp`` must exist first.
from . import _resources  # noqa: E402,F401
from . import _sessions   # noqa: E402,F401
from . import _tasks      # noqa: E402,F401
from . import _council    # noqa: E402,F401
from . import _second_opinion  # noqa: E402,F401

# Back-compat: tests and external callers may import the tool functions
# directly off the package. Re-export the FastMCP-wrapped callables.
from ._sessions import (  # noqa: E402,F401
    start_codex_session, resume_codex_session,
    start_gemini_session, resume_gemini_session,
    start_opencode_session, resume_opencode_session,
    start_claudeor_session, resume_claudeor_session,
    start_aichat_session, resume_aichat_session,
    start_cursor_session, resume_cursor_session,
)
from ._tasks import (  # noqa: E402,F401
    get_task_result, wait_for_task, list_tasks, cancel_task, agent_timing,
)
from ._council import council_ask, rate_council  # noqa: E402,F401
from ._second_opinion import second_opinion  # noqa: E402,F401
from ._resources import get_agents, get_council_status  # noqa: E402,F401


def main():
    """Entry point for the ``owlex-server`` console script."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="owlex-server",
        description="MCP server for multi-agent CLI orchestration",
    )
    parser.add_argument("-v", "--version", action="version", version=f"owlex {__version__}")
    parser.parse_args()

    log_path = _install_stderr_logging()
    if log_path:
        log(f"owlex-server logging stderr to {log_path}")

    async def run_with_cleanup():
        loop = asyncio.get_running_loop()
        _install_asyncio_exception_handler()
        shutdown_event = asyncio.Event()

        def signal_handler(sig):
            log(f"Received signal {sig}, shutting down...")
            shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))
            except NotImplementedError:
                pass

        engine.start_cleanup_loop()
        # Start the long-lived derivation worker. It owns the queue Council
        # and Engine emit into; on shutdown we drain it with a bounded timeout
        # so pairwise/skills/position-deltas don't get silently lost when the
        # MCP server stops mid-flight.
        from .. import derivations as _derivations
        derivation_worker = asyncio.create_task(_derivations.run_worker())

        # Probe the agreement-judge model. External CLI catalogs (codex's
        # ChatGPT-account allowlist) rotate, and a stale pinned model name
        # silently degrades the judge to overlap-heuristic without surfacing
        # the failure. Non-blocking: if the probe fails, log loudly but let
        # the server start anyway. 30s accommodates codex's cold-start
        # latency (~12-15s on first invocation per process for config load
        # + auth handshake); 10s produced spurious [WARN] alarms on freshly
        # respawned MCP servers.
        async def _probe_agreement():
            try:
                from .. import agreement as _agreement
                ok, msg = await _agreement.probe_agreement_model(timeout=30.0)
                tag = "[ok]" if ok else "[WARN]"
                log(f"{tag} agreement health-check: {msg}")
            except Exception as _e:
                log(f"[WARN] agreement health-check failed: {_e}")
        asyncio.create_task(_probe_agreement())
        try:
            server_task = asyncio.create_task(mcp.run_stdio_async())
            shutdown_task = asyncio.create_task(shutdown_event.wait())
            done, pending = await asyncio.wait(
                [server_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if shutdown_task in done:
                server_task.cancel()
                try:
                    await server_task
                except asyncio.CancelledError:
                    pass
            if server_task in done:
                try:
                    server_task.result()
                except Exception as e:
                    log(f"Server task ended: {e}")
        except asyncio.CancelledError:
            log("Server cancelled")
        except Exception as e:
            log(f"Server error: {e}")
        finally:
            await engine.kill_all_tasks()
            engine.stop_cleanup_loop()
            await _derivations.shutdown(timeout=30.0)
            try:
                await asyncio.wait_for(derivation_worker, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                derivation_worker.cancel()
            log("Server shutdown complete.")

    asyncio.run(run_with_cleanup())


if __name__ == "__main__":
    main()
