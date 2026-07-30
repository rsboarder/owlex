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
import time
from pathlib import Path
from typing import Callable

from ..config import config
from .base import AgentRunner, AgentCommand


# How long a SUCCESSFUL startup probe stays valid. Auth/catalog issues move
# on a longer timescale than a server respawn; failures are never cached.
PROBE_CACHE_TTL = int(os.getenv("OWLEX_GROK_PROBE_TTL", "86400"))


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
            model=model,
        )

    def get_output_cleaner(self) -> Callable[[str, str], str]:
        return clean_grok_output


# ---------------------------------------------------------------------------
# Startup health-check (auth + model + CLI present)
# ---------------------------------------------------------------------------

def _probe_cache_path() -> Path:
    home = Path(os.environ.get("OWLEX_HOME", str(Path.home() / ".owlex")))
    return home / "cache" / "grok-probe.json"


def _read_probe_cache() -> str | None:
    """Return cached success message when stamp is fresh for this model."""
    try:
        data = json.loads(_probe_cache_path().read_text(encoding="utf-8"))
        if data.get("model") != config.grok.model:
            return None
        age = time.time() - float(data["probed_at"])
        if age < 0 or age > PROBE_CACHE_TTL:
            return None
        return f"{data['message']} (cached, probed {age / 3600:.1f}h ago)"
    except Exception:  # noqa: BLE001 — bad stamp → real probe
        return None


def _write_probe_cache(message: str) -> None:
    try:
        path = _probe_cache_path()
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


async def _terminate(proc: asyncio.subprocess.Process | None) -> None:
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await proc.wait()
    except Exception:  # noqa: BLE001
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

    Mirrors ``agreement.probe_agreement_model``. Failures never cache so a
    dropped auth session is re-detected on the next MCP start. Successes stamp
    for ``OWLEX_GROK_PROBE_TTL`` (default 24h) to avoid paying a full grok
    cold-start on every server respawn.

    Returns ``(ok, message)``. Never raises.
    """
    if use_cache:
        cached = _read_probe_cache()
        if cached is not None:
            return True, cached

    if not shutil.which("grok"):
        return False, (
            "grok CLI not found on PATH; aichat→grok seat will fail. "
            "Install the Grok CLI or clear COUNCIL_SUBSTITUTION_MODELS grok entry."
        )

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *_build_probe_command(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _terminate(proc)
        return False, (
            f"grok seat probe timed out after {timeout}s "
            f"(model={config.grok.model!r})"
        )
    except FileNotFoundError:
        return False, "grok CLI not found on PATH; aichat→grok seat will fail"
    except Exception as e:  # noqa: BLE001
        await _terminate(proc)
        return False, f"grok seat probe error: {e}"

    out = (stdout or b"").decode("utf-8", errors="replace")
    err = (stderr or b"").decode("utf-8", errors="replace")
    combined = (out + "\n" + err).strip()
    low = combined.lower()

    if proc.returncode != 0:
        if any(s in low for s in ("not signed in", "unauthenticated", "login", "auth")):
            return False, (
                f"grok seat not authenticated (model={config.grok.model!r}). "
                f"Sign in via the Grok CLI. error head: {combined[:200]}"
            )
        return False, (
            f"grok seat probe exit {proc.returncode} "
            f"(model={config.grok.model!r}): {combined[:200]}"
        )

    text = parse_grok_text(out)
    if not (text or "").strip():
        # Empty completed response is still a signal the CLI ran; treat as soft ok
        # only if stdout had a JSON envelope, else fail.
        if "text" not in out and not out.strip():
            return False, (
                f"grok seat probe returned empty output "
                f"(model={config.grok.model!r}); possible auth/model issue"
            )

    message = f"grok seat {config.grok.model!r} probed ok"
    _write_probe_cache(message)
    return True, message
