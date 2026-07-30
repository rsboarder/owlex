"""
Fast agreement scoring for auto-deliberation.
Uses Codex CLI with gpt-5.5 (reasoning_effort=low) to judge whether R1 responses agree.
"""

import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path


AGREEMENT_MODEL = os.getenv("OWLEX_AGREEMENT_MODEL", "gpt-5.5")

# Reasoning effort for the judge model. "low" is fast (~3-5s per call) and
# empirically produces identical scores to "medium" on the agreement-judge
# task (which is shallow pattern-matching, not multi-step reasoning). Bump to
# "medium" or "high" if judge scores look noisy in the dashboard.
AGREEMENT_REASONING = os.getenv("OWLEX_AGREEMENT_REASONING", "low")

# Default per-call timeout. Bumped from 30s historically after cursor-agent
# CLI buffering issues. With codex + gpt-5.5 + low reasoning the realistic
# wall-time is 3-5s, so 90s is generous headroom for a slow cold-start.
DEFAULT_JUDGE_TIMEOUT = int(os.getenv("OWLEX_AGREEMENT_TIMEOUT", "90"))

# How long a SUCCESSFUL startup probe stays valid. The probe answers "is the
# pinned model still in the codex catalog" — a fact that moves on the order of
# weeks — yet it re-ran on every MCP server start (~22/day measured), each run
# paying a full codex prompt to send 23 bytes. Failures are never cached, so a
# model disappearing is still noticed on the next start.
PROBE_CACHE_TTL = int(os.getenv("OWLEX_AGREEMENT_PROBE_TTL", "86400"))

# Basename of the empty scratch dir handed to codex as its cwd. Namespaced by
# uid so a shared /tmp cannot collide across users.
_JUDGE_CWD_NAME = f"owlex-judge-cwd-{os.getuid()}" if hasattr(os, "getuid") else "owlex-judge-cwd"


def _judge_cwd() -> str:
    """Empty scratch directory used as the judge subprocess's working dir.

    Without ``--cd``, codex inherits the MCP server process's cwd (the project
    root) and auto-loads that repo's ``AGENTS.md`` into every judge prompt —
    measured at ~27k input tokens per call for a prompt that is pure text
    classification and never reads the repo. Pointing codex at an empty dir
    outside any project removes the cost without removing capability.

    Stable rather than a per-call ``mkdtemp`` so repeated judge calls do not
    churn the filesystem. ``mkdir(exist_ok=True)`` is atomic, so concurrent
    calls cannot race on creating it.
    """
    path = Path(tempfile.gettempdir()) / _JUDGE_CWD_NAME
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return str(path)


def _build_judge_command() -> list[str]:
    """Construct the codex exec argv shared by probe and score paths.

    The judge runs in read-only sandbox — no file writes, no shell — because
    the prompt is pure text classification and any tool call would just add
    latency. Reasoning effort is configurable for quality/speed tradeoff.
    ``--cd`` pins an empty scratch dir so no repo ``AGENTS.md`` is discovered
    (same pattern as ``second_opinion._cmd``).
    """
    return [
        "codex", "exec", "--skip-git-repo-check",
        "-c", f'model_reasoning_effort="{AGREEMENT_REASONING}"',
        "--model", AGREEMENT_MODEL,
        "--sandbox", "read-only",
        "--cd", _judge_cwd(),
        "-",  # read prompt from stdin
    ]


def _probe_cache_path() -> Path:
    """Stamp file for the last successful probe (honours ``OWLEX_HOME``)."""
    home = Path(os.environ.get("OWLEX_HOME", str(Path.home() / ".owlex")))
    return home / "cache" / "agreement-probe.json"


def _read_probe_cache() -> str | None:
    """Return the cached success message when a fresh success exists for this model.

    Only successes are ever written, so a hit always means ``ok=True``. Any
    missing / corrupt / stale stamp is a miss: the caller then runs a real
    probe. Never raises — this sits on the server-startup path.
    """
    try:
        data = json.loads(_probe_cache_path().read_text(encoding="utf-8"))
        if data.get("model") != AGREEMENT_MODEL:
            return None
        age = time.time() - float(data["probed_at"])
        if age < 0 or age > PROBE_CACHE_TTL:
            return None
        return f"{data['message']} (cached, probed {age / 3600:.1f}h ago)"
    except Exception:  # noqa: BLE001 — a bad stamp must degrade to a real probe
        return None


def _write_probe_cache(message: str) -> None:
    """Stamp a successful probe. Best-effort; failures are swallowed.

    Written via a temp file + atomic rename so a concurrently starting server
    can never read a half-written stamp.
    """
    try:
        path = _probe_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "model": AGREEMENT_MODEL,
            "probed_at": time.time(),
            "message": message,
        })
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001 — caching is an optimisation, never a gate
        pass


async def _terminate(proc: asyncio.subprocess.Process | None) -> None:
    """Kill and reap a still-running child so a timeout/error can't orphan it."""
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


async def probe_agreement_model(timeout: float = 10.0, use_cache: bool = True) -> tuple[bool, str]:
    """Startup health-check: verify the configured AGREEMENT_MODEL is reachable.

    External CLI catalogs rotate. A model name that worked last week may
    return 400 today, and owlex would silently fall back to overlap-heuristic
    for weeks before anyone noticed. This probe runs once at server start and
    prints a clear warning to stderr (teed to the log file) if the model is
    gone.

    A successful result is stamped to disk and reused for ``PROBE_CACHE_TTL``
    seconds, so respawning the MCP server does not spawn a codex process each
    time. A cache hit says so in its message. Failures are never cached: a
    model going away is still caught on the next start. Pass
    ``use_cache=False`` to force a real probe.

    Returns ``(ok, message)``. Never raises — health-check failure must not
    block server startup; the judge will fall back to overlap-heuristic at
    council time.
    """
    if use_cache:
        cached = _read_probe_cache()
        if cached is not None:
            return True, cached

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *_build_judge_command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=b"Reply with one word: OK"),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        await _terminate(proc)
        return False, f"agreement model probe timed out after {timeout}s (model={AGREEMENT_MODEL!r})"
    except FileNotFoundError:
        return False, "codex CLI not found on PATH; agreement judge will fallback to heuristic"
    except Exception as e:
        await _terminate(proc)
        return False, f"agreement model probe error: {e}"

    if proc.returncode != 0:
        # codex emits errors on stdout (not stderr) — check both.
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        combined = (out + err).strip()
        # Common shape: '"message":"The \'X\' model is not supported when using Codex with a ChatGPT account."'
        if "is not supported" in combined or "invalid_request_error" in combined:
            return False, (
                f"agreement model {AGREEMENT_MODEL!r} not in codex catalog. "
                f"Override via OWLEX_AGREEMENT_MODEL. codex error head: {combined[:200]}"
            )
        return False, f"agreement model probe exit {proc.returncode}: {combined[:200]}"

    message = f"agreement model {AGREEMENT_MODEL!r} probed ok"
    _write_probe_cache(message)
    return True, message


AGREEMENT_PROMPT = """\
You are judging whether multiple AI advisors agree on a software engineering question.

QUESTION:
{question}

{responses}

Score the overall agreement between these responses on a scale of 1-5:
1 = Fundamental disagreement (contradictory recommendations)
2 = Significant differences (different approaches, some conflict)
3 = Partial agreement (same direction but different emphasis/details)
4 = Strong agreement (same recommendations with minor variation)
5 = Full consensus (essentially the same answer)

Respond with ONLY a JSON object:
{{"score": <1-5>, "reason": "<one sentence>"}}
"""


async def score_agreement(
    question: str,
    responses: dict[str, str],
    timeout: int | None = None,
) -> tuple[float, str]:
    """
    Score agreement between agent responses using Codex CLI + gpt-5.5.

    Returns (score, reason) where score is 1.0-5.0.
    Falls back to term-overlap heuristic if the judge fails.
    """
    if len(responses) < 2:
        return 5.0, "Single response"

    if timeout is None:
        timeout = DEFAULT_JUDGE_TIMEOUT

    from .anonymize import assign_labels

    by_label, _ = assign_labels(list(responses.items()))
    response_parts = []
    for label, content in by_label.items():
        truncated = content[:2000] if len(content) > 2000 else content
        response_parts.append(f"RESPONSE {label}:\n{truncated}")

    prompt = AGREEMENT_PROMPT.format(
        question=question[:500],
        responses="\n\n".join(response_parts),
    )

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *_build_judge_command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode()),
            timeout=timeout,
        )

        if proc.returncode != 0:
            return _fallback_score(responses), "judge failed"

        text = stdout.decode("utf-8", errors="replace").strip()
        return _parse_score(text)

    except asyncio.TimeoutError:
        await _terminate(proc)
        return _fallback_score(responses), "judge timeout"
    except FileNotFoundError:
        return _fallback_score(responses), "codex CLI not found"
    except Exception as e:
        await _terminate(proc)
        return _fallback_score(responses), f"judge error: {e}"


def _parse_score(text: str) -> tuple[float, str]:
    """Extract score and reason from judge output.

    codex emits a verbose preamble (workdir, model, session id, the echoed
    user prompt) before the actual model response. The response itself
    appears after a 'codex' marker line. We search the full text for a JSON
    line containing 'score' — that catches the response regardless of where
    in the output it lands. The echoed prompt template `{"score": <1-5>, ...}`
    is filtered out because it has placeholder brackets, not a real number.
    """
    for line in text.split("\n"):
        line = line.strip()
        if not (line.startswith("{") and "score" in line):
            continue
        # Skip the echoed prompt template line — it contains literal '<1-5>'.
        if "<" in line and ">" in line:
            continue
        try:
            data = json.loads(line)
            score = float(data.get("score", 3))
            reason = data.get("reason", "")
            return max(1.0, min(5.0, score)), reason
        except (json.JSONDecodeError, ValueError):
            continue

    if "```" in text:
        try:
            block = text.split("```")[1]
            if block.startswith("json"):
                block = block[4:]
            data = json.loads(block.strip())
            return max(1.0, min(5.0, float(data["score"]))), data.get("reason", "")
        except (json.JSONDecodeError, ValueError, IndexError, KeyError):
            pass

    match = re.search(r'"score"\s*:\s*(\d)', text)
    if match:
        return float(match.group(1)), "parsed from partial output"

    return 3.0, "unparseable judge output"


def _fallback_score(responses: dict[str, str]) -> float:
    """Term-overlap heuristic when judge is unavailable."""
    term_sets = []
    for content in responses.values():
        words = set()
        for word in content.lower().split():
            cleaned = word.strip(".,;:!?()\"'`")
            if len(cleaned) > 5 and cleaned.isalpha():
                words.add(cleaned)
        term_sets.append(words)

    if len(term_sets) < 2:
        return 3.0

    similarities = []
    for i in range(len(term_sets)):
        for j in range(i + 1, len(term_sets)):
            intersection = len(term_sets[i] & term_sets[j])
            union = len(term_sets[i] | term_sets[j])
            if union > 0:
                similarities.append(intersection / union)

    avg = sum(similarities) / len(similarities) if similarities else 0
    return round(1 + avg * 4, 1)
