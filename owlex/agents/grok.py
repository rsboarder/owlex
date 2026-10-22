"""
Grok Agent CLI runner.
Uses the Grok CLI (https://x.ai) for AI-powered analysis and coding assistance.

Phase-0 harness (A/B ship 2026-07-30, N=5 rated, win_rate_B=0.8):
  - optional output-contract suffix on prompts (default on)
  - strip leading tool-narration from final answers (default on)
See docs/design/ab-grok-harness-benchmark.md.

Startup probe (``probe_grok_seat``) catches auth / missing-CLI failures that
previously surfaced only as a silent aichat seat score of −1 mid-council.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import time
from pathlib import Path
from typing import Callable

from ..config import config
from .base import AgentRunner, AgentCommand


# Probe-stamp lifetimes, validated in GrokConfig (see owlex/config.py).
# Module-level aliases keep the historical names readable at the use-sites.
PROBE_CACHE_TTL = config.grok.probe_ttl
PROBE_FAIL_TTL = config.grok.probe_fail_ttl


# Match the A/B arm ``contract_strip`` wording (do not drift without re-running A/B).
OUTPUT_CONTRACT_SUFFIX = """

## Output contract (mandatory)
- Start with: **Verdict:** (1–2 sentences)
- Then: **Evidence** with file:line citations where possible
- Then: **Residual risks** (bullet list, max 5)
- Then: **Recommendation** (ship / ship with N / block) and why
- Do NOT include tool plans, "I'll read…", "Pulling…", or intermediate narration in the final answer
- Prefer concise findings over exhaustive dumps unless the question demands depth
"""

_TOOLISH_START = re.compile(
    r"(?is)^\s*(I'll|I will|Let me|Reading|Pulling|Starting|Next I'll|"
    r"Looking|Checking|Digging|I need to|First,?\s+I)\b"
)
_TOOLISH_LINE = re.compile(
    r"(?im)^(I'll |I will |Next I'll |Pulling |Reading |Checking |"
    r"Looking at |Digging |Starting with |Let me )\b"
)
_HEADING_OR_VERDICT = re.compile(
    r"(?im)^(?:#{1,3}\s|\*\*Verdict\*\*:?|\*\*Verdict:\*\*|Verdict:|##\s*Executive summary)"
)
# Live Grok sometimes glues narration to the verdict on one line, and uses either
# ``**Verdict:**`` (colon inside bold) or ``**Verdict**:`` (colon outside).
# Bare ``Verdict:`` only when not already inside bold markers.
_INLINE_STRUCT = re.compile(
    r"(?:\*\*Verdict:\*\*|\*\*Verdict\*\*:|(?<!\*)Verdict:)",
    re.IGNORECASE,
)


def parse_grok_text(raw_output: str) -> str:
    """Extract the ``text`` field from Grok's JSON output.

    ``grok --output-format json`` prints a single JSON object like
    ``{"text": "...", "stopReason": "EndTurn", "sessionId": "019f...", ...}``.
    Falls back to slicing at the last ``}`` for truncated/noisy output, and
    returns the raw string as a last resort.
    """
    if not raw_output:
        return ""
    text = raw_output.strip()
    if text.startswith("Grok Output"):
        text = text.split("\n", 1)[-1].strip() if "\n" in text else text
    try:
        i = text.find("{")
        if i >= 0:
            outer = json.loads(text[i:])
            if isinstance(outer, dict) and "text" in outer:
                return str(outer.get("text") or "")
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    try:
        outer = json.loads(text)
        if isinstance(outer, dict) and "text" in outer:
            return str(outer.get("text") or "")
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    last = text.rfind("}")
    if last > 0:
        try:
            outer = json.loads(text[: last + 1])
            if isinstance(outer, dict) and "text" in outer:
                return str(outer.get("text") or "")
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return text


def _first_structure_offset(text: str) -> int | None:
    """Earliest offset of a structural start (heading / Verdict). None if absent."""
    offsets: list[int] = []
    for rx in (_HEADING_OR_VERDICT, _INLINE_STRUCT):
        m = rx.search(text)
        if m:
            offsets.append(m.start())
    return min(offsets) if offsets else None


def strip_tool_narration(text: str) -> str:
    """Drop leading tool-monologue until first structural heading/verdict.

    Also drops isolated mid-document toolish one-liners (agent status chatter).
    Mirrors the A/B harness cleaner that won the Phase-0 ship gates.

    Handles a live edge case where Grok glues narration to ``**Verdict:**``
    without a preceding newline (so line-anchored heading match fails).
    """
    if not text:
        return ""
    cut = _first_structure_offset(text)
    if cut is not None and cut > 0:
        prefix = text[:cut]
        if _TOOLISH_START.match(text) or _TOOLISH_START.match(prefix) or len(prefix) < 800:
            text = text[cut:]
    kept = []
    for ln in text.splitlines():
        # Drop pure status one-liners only — never a line that already contains
        # a structural verdict/heading (toolish+verdict often share one line).
        if (
            _TOOLISH_LINE.match(ln)
            and len(ln) < 200
            and _first_structure_offset(ln) is None
        ):
            continue
        kept.append(ln)
    return "\n".join(kept).strip()


def apply_output_contract(prompt: str) -> str:
    """Append the council output contract when enabled."""
    if not prompt:
        return OUTPUT_CONTRACT_SUFFIX.lstrip("\n")
    if "Output contract (mandatory)" in prompt:
        return prompt  # idempotent — avoid double-append on resume
    return prompt.rstrip() + OUTPUT_CONTRACT_SUFFIX


def clean_grok_output(raw_output: str, original_prompt: str = "") -> str:
    """Clean Grok CLI output: parse JSON envelope, strip tool narration, normalize."""
    text = parse_grok_text(raw_output)
    if not config.grok.clean_output:
        return text
    if config.grok.strip_tool_narration:
        text = strip_tool_narration(text)
    cleaned = re.sub(r"\n{3,}", "\n\n", text)
    return cleaned.strip()

# ---------------------------------------------------------------------------
# Grok MCP isolation — shared by council seat runs AND the startup probe
# ---------------------------------------------------------------------------

def _owlex_cache_dir() -> Path:
    home = Path(os.environ.get("OWLEX_HOME", str(Path.home() / ".owlex")))
    return home / "cache"


def _isolated_grok_home() -> Path:
    """Empty ``HOME`` that makes grok discover no MCP servers.

    Grok resolves MCP servers from Claude Code's config: the global
    ``mcpServers`` block in ``$HOME/.claude.json`` plus the entry keyed by the
    process cwd. Running grok under owlex-server's own HOME therefore boots
    grok's entire MCP stack — measured 25 descendant processes, one of which is
    ``owlex-server`` itself, which then probes grok again. Pointing HOME at an
    empty directory makes both lookups miss. ``cwd`` is left alone for seat
    runs so the repo's AGENTS.md and grok's built-in file tools still work.
    """
    return _owlex_cache_dir() / "grok-isolated-home"


def _isolation_env_overrides(in_probe: bool = False) -> dict[str, str]:
    """HOME/GROK_HOME overrides that strip grok's MCP stack.

    ``GROK_HOME`` keeps grok's real credential store (``~/.grok/auth.json``)
    reachable while ``HOME`` points at the empty isolation dir, so grok still
    authenticates.

    Two more vars stop the empty HOME leaking where it shouldn't.
    ``XDG_CONFIG_HOME`` is *redirected* rather than unset, because
    ``AgentCommand.env_overrides`` can only add keys (``engine.py`` does
    ``env.update``) — popping it would isolate the probe but not the seat.
    ``GIT_CONFIG_GLOBAL`` keeps the real ``~/.gitconfig`` reachable: a seat can
    call grok's built-in ``run_terminal_command``, and those children inherit
    the empty HOME, which otherwise drops user identity and ``safe.directory``.

    In-probe only: ``OWLEX_IN_PROBE`` fences a nested probe, and
    ``OWLEX_COUNCIL_DEPTH`` reuses the canonical fence so a nested owlex-server
    also refuses ``council_ask`` (``server/_validators.council_recursion_block``
    reads only that var).

    ``Path.home()`` is read directly rather than threaded through a bespoke
    ``OWLEX_REAL_HOME`` var: owlex-server always runs with the real HOME (the
    only route that could invert that is a nested owlex under grok, which both
    fences already refuse), and the extra var was an unvalidated trust root
    whose two writers disagreed.
    """
    real_home = Path.home()
    isolated = _isolated_grok_home()
    return {
        "GROK_HOME": os.environ.get("GROK_HOME") or str(real_home / ".grok"),
        "HOME": str(isolated),
        "XDG_CONFIG_HOME": str(isolated / ".config"),
        "GIT_CONFIG_GLOBAL": str(real_home / ".gitconfig"),
        **({"OWLEX_IN_PROBE": "1", "OWLEX_COUNCIL_DEPTH": "1"} if in_probe else {}),
    }


def _ensure_isolated_home() -> Path | None:
    """Best-effort create the isolation dir; ``None`` when it could not be made.

    A ``None`` return never relaxes isolation — callers still force ``HOME`` at
    the path. It only means the dir is unusable as a ``cwd``. Grok runs fine
    with a nonexistent ``HOME`` (verified: exit 0, no dir created), and failing
    OPEN here would silently restore the MCP stack this module exists to strip.
    """
    path = _isolated_grok_home()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 — isolation still applies via env
        return None
    return path


class GrokRunner(AgentRunner):
    """Runner for Grok CLI (xAI)."""

    @property
    def name(self) -> str:
        return "grok"

    @property
    def cli_command(self) -> str:
        return "grok"

    @property
    def output_prefix(self) -> str:
        return "Grok Output"

    @property
    def auto_loads_project_instructions(self) -> bool:
        """Verified from grok's own history under ``~/.grok/sessions``.

        A bookmatcher session's ``chat_history.jsonl`` opens with a synthetic
        ``project_instructions`` user turn listing ``## From:`` blocks that
        include the repo's AGENTS.md, before owlex's council prompt arrives.
        """
        return True

    def _prepare_prompt(self, prompt: str) -> str:
        if config.grok.output_contract:
            return apply_output_contract(prompt)
        return prompt

    def _seat_env_overrides(self) -> dict[str, str] | None:
        """Strip grok's MCP stack from seat runs unless explicitly re-enabled.

        A council seat is an advisor answering from a prompt; across 20 sampled
        council sessions grok called only its built-in grep / read_file /
        run_terminal_command and never an ``mcp__*`` tool, while the stack cost
        ~17 npm servers per run and re-entered owlex. ``cwd`` is untouched, so
        the repo's own AGENTS.md still loads; the user-global instructions under
        ``$HOME`` do not. Opt out with ``OWLEX_GROK_ISOLATE_MCP=false``.
        """
        if not config.grok.isolate_mcp:
            return None
        _ensure_isolated_home()  # best-effort; isolation does not depend on it
        return _isolation_env_overrides()

    def build_exec_command(
        self,
        prompt: str,
        working_directory: str | None = None,
        enable_search: bool = False,
        model_override: str | None = None,
        **kwargs,
    ) -> AgentCommand:
        """Build command for starting a new Grok session."""
        model = model_override or config.grok.model
        prepared = self._prepare_prompt(prompt)
        full_command = [
            "grok", "-p", prepared,
            "--output-format", "json",
            "--always-approve",
            "--model", model,
            "--effort", config.grok.effort,
            "--disable-web-search",
        ]

        return AgentCommand(
            command=full_command,
            prompt="",  # Prompt is in command args
            cwd=working_directory,
            output_prefix="Grok Output",
            not_found_hint="Please install the Grok CLI (binary: grok). See: https://x.ai",
            stream=False,  # JSON output mode doesn't stream
            env_overrides=self._seat_env_overrides(),
            model=model,
        )

    def build_resume_command(
        self,
        session_ref: str,
        prompt: str,
        working_directory: str | None = None,
        enable_search: bool = False,
        model_override: str | None = None,
        **kwargs,
    ) -> AgentCommand:
        """Build command for resuming an existing Grok session."""
        if session_ref.startswith("-"):
            raise ValueError(f"Invalid session_ref: '{session_ref}' - cannot start with '-'")

        model = model_override or config.grok.model
        prepared = self._prepare_prompt(prompt)
        full_command = [
            "grok", "-r", session_ref, "-p", prepared,
            "--output-format", "json",
            "--always-approve",
            "--model", model,
            "--effort", config.grok.effort,
            "--disable-web-search",
        ]

        return AgentCommand(
            command=full_command,
            prompt="",  # Prompt is in command args
            cwd=working_directory,
            output_prefix="Grok Output",
            not_found_hint="Please install the Grok CLI (binary: grok). See: https://x.ai",
            stream=False,
            env_overrides=self._seat_env_overrides(),
            model=model,
        )

    def get_output_cleaner(self) -> Callable[[str, str], str]:
        return clean_grok_output

# ---------------------------------------------------------------------------
# Startup health-check (auth + model + CLI present)
# ---------------------------------------------------------------------------

# Bounded wait between escalating signals when reaping the probe's tree.
REAP_GRACE_SECONDS = 5.0


def _probe_cache_path() -> Path:
    """Stamp for the last SUCCESSFUL probe.

    Failures live in a separate file (``_probe_fail_path``) rather than gaining
    an ``ok`` flag here, so an older owlex reading this stamp can never mistake
    a short-TTL failure for a 24h success — the shape it expects is unchanged.
    """
    return _owlex_cache_dir() / "grok-probe.json"


def _probe_fail_path() -> Path:
    """Stamp for the last FAILED probe (short TTL). Invisible to older readers."""
    return _owlex_cache_dir() / "grok-probe-fail.json"


def _build_probe_env() -> dict[str, str]:
    """Full environment for the probe subprocess: no MCP stack, no recursion."""
    env = os.environ.copy()
    env.update(_isolation_env_overrides(in_probe=True))
    return env


def _probe_should_skip() -> str | None:
    """Reason to skip the probe entirely, or ``None`` to run it.

    Two nesting routes reach this code from inside a grok process, and both
    would otherwise spawn another grok: the probe's own subprocess
    (``OWLEX_IN_PROBE``) and a grok council seat whose MCP stack booted an
    owlex-server (``OWLEX_COUNCIL_DEPTH``, set by ``TaskEngine`` for every
    agent subprocess).

    The marker is matched against the same falsy tokens ``config._get_bool``
    accepts, so ``OWLEX_IN_PROBE=0`` / ``no`` / ``n`` — the conventional ways to
    turn a flag off — do not disable the health-check forever while startup
    keeps logging ``[ok]``.
    """
    marker = (os.environ.get("OWLEX_IN_PROBE") or "").strip().lower()
    if marker not in ("", "0", "false", "no", "n", "off"):
        return "skipped: nested inside a grok probe (OWLEX_IN_PROBE=1)"
    try:
        depth = int(os.environ.get("OWLEX_COUNCIL_DEPTH", "0") or 0)
    except ValueError:
        depth = 0
    if depth > 0:
        return f"skipped: nested inside a council agent (OWLEX_COUNCIL_DEPTH={depth})"
    return None


def _read_stamp(path: Path, ttl: int) -> tuple[float, str] | None:
    """Return ``(probed_at, message)`` when the stamp is fresh for this model."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("model") != config.grok.model:
            return None
        if data.get("ok") is False:
            # A transitional stamp from the single-file layout. Successes never
            # carried this key, so treating it as a miss retires it safely.
            return None
        probed_at = float(data["probed_at"])
        age = time.time() - probed_at
        if age < 0 or age > ttl:
            return None
        return probed_at, f"{data['message']} (cached, probed {age / 3600:.1f}h ago)"
    except Exception:  # noqa: BLE001 — bad stamp → real probe
        return None


def _read_probe_cache() -> tuple[bool, str] | None:
    """Return ``(ok, message)`` for whichever fresh stamp is NEWER.

    Recency, not file precedence: a success stamp still inside its 24h TTL must
    not outrank a failure recorded minutes ago, or a seat that is broken right
    now keeps reporting ``[ok]`` for the rest of the day.
    """
    ok_hit = _read_stamp(_probe_cache_path(), PROBE_CACHE_TTL)
    fail_hit = _read_stamp(_probe_fail_path(), PROBE_FAIL_TTL)
    if ok_hit and fail_hit:
        return (True, ok_hit[1]) if ok_hit[0] >= fail_hit[0] else (False, fail_hit[1])
    if ok_hit:
        return True, ok_hit[1]
    if fail_hit:
        return False, fail_hit[1]
    return None


def _write_stamp(path: Path, message: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "model": config.grok.model,
            "probed_at": time.time(),
            "message": message,
        })
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001 — caching is optional
        pass


def _discard_stamp(path: Path) -> None:
    """Drop a stamp that the newer outcome contradicts. Never raises.

    Guarded because ``probe_grok_seat`` promises never to raise: an unremovable
    path (wrong owner, read-only volume, a directory in the file's place) would
    otherwise turn a SUCCESSFUL probe into an exception at server startup.
    """
    try:
        path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 — a stale stamp is bounded by its TTL
        pass


def _write_probe_cache(message: str, ok: bool = True) -> None:
    """Stamp the probe outcome, retiring the stamp it contradicts."""
    if ok:
        _write_stamp(_probe_cache_path(), message)
        _discard_stamp(_probe_fail_path())
    else:
        _write_stamp(_probe_fail_path(), message)
        _discard_stamp(_probe_cache_path())


def _probe_failure(message: str) -> tuple[bool, str]:
    """Stamp a failure with the short TTL and return it."""
    _write_probe_cache(message, ok=False)
    return False, message


async def _wait_briefly(proc: asyncio.subprocess.Process) -> None:
    """Bounded wait for the leader. Swallows ordinary errors, not cancellation.

    ``CancelledError`` is deliberately allowed through: ``_terminate``'s
    ``finally`` still runs and delivers the guaranteed SIGKILL.
    """
    try:
        await asyncio.wait_for(proc.wait(), timeout=REAP_GRACE_SECONDS)
    except Exception:  # noqa: BLE001 — timed out, or the pid is already gone
        pass


async def _terminate(
    proc: asyncio.subprocess.Process | None,
    pgid: int | None = None,
) -> None:
    """Kill the probe's whole process group, not just the grok process.

    ``proc.kill()`` alone leaves grok's children reparented to launchd, where
    they spin unreaped — the observed failure was 130 orphans holding 533% CPU
    and ~13.5 GB RSS.

    Deliberately does NOT bail out when ``proc.returncode`` is already set. The
    leader's returncode is published as soon as grok itself is reaped, while
    ``communicate()`` stays blocked until every fd-inheriting descendant closes
    the pipes — so "leader already exited, children still holding stdout" IS
    the timeout path this function exists for. Gating the group reap on the
    leader made it a no-op precisely there (verified: leader returncode 0,
    communicate timed out, process group still alive).

    ``pgid`` MUST be captured by the caller right after spawn, while the pid is
    guaranteed live. Resolving it here would race child reaping: the watcher
    frees the pid on another thread before ``returncode`` is published, so
    ``os.getpgid(proc.pid)`` could read a recycled pid's group and signal an
    unrelated process tree. Never raises.
    """
    if proc is None:
        return

    try:
        own_pgid = os.getpgid(0)
    except OSError:
        own_pgid = None

    group = pgid if (pgid is not None and pgid > 0 and pgid != own_pgid) else None

    def _signal_group(sig: int) -> None:
        if group is None:
            return
        try:
            os.killpg(group, sig)
        except OSError:  # ProcessLookupError/PermissionError are both OSError
            pass

    try:
        _signal_group(signal.SIGTERM)
        await _wait_briefly(proc)
        _signal_group(signal.SIGKILL)
        await _wait_briefly(proc)
    finally:
        # A `finally` still runs when the caller is cancelled mid-reap (verified),
        # and killpg is synchronous and non-blocking — so the guaranteed SIGKILL
        # lands even on a shutdown that cancels us at the first await. Without
        # it, the group would have received only SIGTERM: grok dies promptly,
        # the spinning members this function exists to reap do not.
        _signal_group(signal.SIGKILL)
        if proc.returncode is None:
            try:
                proc.kill()
            except OSError:
                pass


def _build_probe_command() -> list[str]:
    """Minimal headless argv — no output-contract suffix (keep probe tiny)."""
    return [
        "grok",
        "-p", "Reply with exactly: ok",
        "--output-format", "json",
        "--always-approve",
        "--model", config.grok.model,
        "--effort", config.grok.effort,
        "--disable-web-search",
    ]


async def probe_grok_seat(timeout: float = 30.0, use_cache: bool = True) -> tuple[bool, str]:
    """Startup health-check: grok CLI present, signed in, model responds.

    Shares its shape with ``agreement.probe_agreement_model`` but diverges on
    caching: successes stamp for ``OWLEX_GROK_PROBE_TTL`` (default 24h) while
    failures stamp for the much shorter ``OWLEX_GROK_PROBE_FAIL_TTL`` (default
    10 min). Failures used to skip the cache entirely so a dropped auth session
    was re-detected on the next MCP start — but that turned a broken seat into
    a probe storm, since every owlex-server respawn paid a full grok cold
    start. A short TTL keeps re-detection quick while bounding the spawn rate;
    each outcome retires the stamp it contradicts.

    Returns ``(ok, message)``. Never raises.
    """
    skip = _probe_should_skip()
    if skip is not None:
        return True, skip

    if use_cache:
        cached = _read_probe_cache()
        if cached is not None:
            return cached

    if not shutil.which("grok"):
        return _probe_failure(
            "grok CLI not found on PATH; aichat→grok seat will fail. "
            "Install the Grok CLI or clear COUNCIL_SUBSTITUTION_MODELS grok entry."
        )

    isolation_dir = _ensure_isolated_home()

    proc = None
    pgid = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *_build_probe_command(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(isolation_dir) if isolation_dir is not None else None,
            env=_build_probe_env(),
            start_new_session=True,
        )
        # Capture the group while the pid is still guaranteed live. On a fast
        # exit the child watcher can free the pid first; start_new_session makes
        # the child its own group leader, so its pid IS the pgid — fall back to
        # that rather than silently degrading to a leader-only kill.
        try:
            pgid = os.getpgid(proc.pid)
        except OSError:
            pgid = proc.pid
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _terminate(proc, pgid)
        return _probe_failure(
            f"grok seat probe timed out after {timeout}s "
            f"(model={config.grok.model!r})"
        )
    except FileNotFoundError:
        return _probe_failure(
            "grok probe could not start: the grok CLI or its working "
            f"directory ({isolation_dir}) is missing"
        )
    except Exception as e:  # noqa: BLE001
        await _terminate(proc, pgid)
        return _probe_failure(f"grok seat probe error: {e}")
    except BaseException:
        # Cancellation (server shutdown mid-probe) is a BaseException, so the
        # handlers above miss it. start_new_session put grok in its own session,
        # meaning nothing else will ever signal it — without this the whole tree
        # leaks permanently. Last clause, so the spawn body is unaffected.
        await _terminate(proc, pgid)
        raise

    out = (stdout or b"").decode("utf-8", errors="replace")
    err = (stderr or b"").decode("utf-8", errors="replace")
    combined = (out + "\n" + err).strip()
    low = combined.lower()

    if proc.returncode != 0:
        if any(s in low for s in ("not signed in", "unauthenticated", "login", "auth")):
            return _probe_failure(
                f"grok seat not authenticated (model={config.grok.model!r}). "
                f"Sign in via the Grok CLI. error head: {combined[:200]}"
            )
        return _probe_failure(
            f"grok seat probe exit {proc.returncode} "
            f"(model={config.grok.model!r}): {combined[:200]}"
        )

    if not (parse_grok_text(out) or "").strip():
        # An empty answer is not a healthy seat — stamping it as a 24h success
        # would hide a mute model for a day.
        return _probe_failure(
            f"grok seat probe returned empty output "
            f"(model={config.grok.model!r}); possible auth/model issue"
        )

    message = f"grok seat {config.grok.model!r} probed ok"
    _write_probe_cache(message, ok=True)
    return True, message
