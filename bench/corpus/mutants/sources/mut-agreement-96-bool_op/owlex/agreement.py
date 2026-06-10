"""
Fast agreement scoring for auto-deliberation.
Uses Codex CLI with gpt-5.5 (reasoning_effort=low) to judge whether R1 responses agree.
"""

import asyncio
import json
import os
import re


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


def _build_judge_command() -> list[str]:
    """Construct the codex exec argv shared by probe and score paths.

    The judge runs in read-only sandbox — no file writes, no shell — because
    the prompt is pure text classification and any tool call would just add
    latency. Reasoning effort is configurable for quality/speed tradeoff.
    """
    return [
        "codex", "exec", "--skip-git-repo-check",
        "-c", f'model_reasoning_effort="{AGREEMENT_REASONING}"',
        "--model", AGREEMENT_MODEL,
        "--sandbox", "read-only",
        "-",  # read prompt from stdin
    ]


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


async def probe_agreement_model(timeout: float = 10.0) -> tuple[bool, str]:
    """Startup health-check: verify the configured AGREEMENT_MODEL is reachable.

    External CLI catalogs rotate. A model name that worked last week may
    return 400 today, and owlex would silently fall back to overlap-heuristic
    for weeks before anyone noticed. This probe runs once at server start and
    prints a clear warning to stderr (teed to the log file) if the model is
    gone.

    Returns ``(ok, message)``. Never raises — health-check failure must not
    block server startup; the judge will fall back to overlap-heuristic at
    council time.
    """
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
        if "is not supported" in combined and "invalid_request_error" in combined:
            return False, (
                f"agreement model {AGREEMENT_MODEL!r} not in codex catalog. "
                f"Override via OWLEX_AGREEMENT_MODEL. codex error head: {combined[:200]}"
            )
        return False, f"agreement model probe exit {proc.returncode}: {combined[:200]}"

    return True, f"agreement model {AGREEMENT_MODEL!r} probed ok"


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
