"""
Fast agreement scoring for auto-deliberation.
Uses Cursor CLI with gemini-2.5-flash to judge whether R1 responses agree.
"""

import asyncio
import json
import re
import sys


AGREEMENT_MODEL = __import__("os").getenv("OWLEX_AGREEMENT_MODEL", "gemini-3-flash")

# Default per-call timeout for the cursor-agent judge subprocess. Bumped from
# the historical 30s after cursor-agent CLI v2026.05.16 started buffering
# output for 30-60s before emitting any tokens on council-sized prompts. A
# 30s wall was firing on the prompt-think phase rather than on real failure.
# Override via env when Cursor performance changes again.
DEFAULT_JUDGE_TIMEOUT = int(__import__("os").getenv("OWLEX_AGREEMENT_TIMEOUT", "90"))


async def probe_agreement_model(timeout: float = 10.0) -> tuple[bool, str]:
    """Startup health-check: verify the configured AGREEMENT_MODEL is reachable.

    External CLI catalogs (cursor-agent's catalog in particular) rotate
    frequently — a model name that worked last week may be 404 today, and
    owlex silently fell back to overlap-heuristic for weeks before anyone
    noticed. This probe runs once at server start and prints a clear warning
    to stderr (teed to the log file) if the model is gone.

    Returns ``(ok, message)``. Never raises — health-check failure must not
    block server startup; the judge will fall back to overlap-heuristic at
    council time.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "agent", "--print", "--output-format", "text", "--trust",
            "--model", AGREEMENT_MODEL,
            "Reply with one word: OK",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return False, f"agreement model probe timed out after {timeout}s (model={AGREEMENT_MODEL!r})"
    except FileNotFoundError:
        return False, "cursor-agent CLI not found on PATH; agreement judge will fallback to heuristic"
    except Exception as e:
        return False, f"agreement model probe error: {e}"

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        # Common shape: "Cannot use this model: X. Available models: ..."
        if "Cannot use this model" in err:
            return False, (
                f"agreement model {AGREEMENT_MODEL!r} not in cursor-agent catalog. "
                f"Override via OWLEX_AGREEMENT_MODEL. cursor stderr head: {err[:200]}"
            )
        return False, f"agreement model probe exit {proc.returncode}: {err[:200]}"

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
    Score agreement between agent responses using Cursor CLI + gemini-2.5-flash.

    Returns (score, reason) where score is 1.0-5.0.
    Falls back to term-overlap heuristic if the judge fails.
    """
    if len(responses) < 2:
        return 5.0, "Single response"

    # Resolve timeout from explicit arg → env-configurable default. Read at
    # call time, not module load, so tests can monkeypatch via env.
    if timeout is None:
        timeout = DEFAULT_JUDGE_TIMEOUT

    # Build the prompt with anonymous labels via the shared helper.
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

    try:
        proc = await asyncio.create_subprocess_exec(
            "agent", "--print", "--output-format", "text", "--trust",
            "--model", AGREEMENT_MODEL,
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )

        if proc.returncode != 0:
            return _fallback_score(responses), "judge failed"

        text = stdout.decode("utf-8", errors="replace").strip()
        return _parse_score(text)

    except asyncio.TimeoutError:
        return _fallback_score(responses), "judge timeout"
    except FileNotFoundError:
        return _fallback_score(responses), "cursor CLI not found"
    except Exception as e:
        return _fallback_score(responses), f"judge error: {e}"


def _parse_score(text: str) -> tuple[float, str]:
    """Extract score and reason from judge output."""
    # Try JSON parse
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("{") and "score" in line:
            try:
                data = json.loads(line)
                score = float(data.get("score", 3))
                reason = data.get("reason", "")
                return max(1.0, min(5.0, score)), reason
            except (json.JSONDecodeError, ValueError):
                continue

    # Try to find JSON in markdown blocks
    if "```" in text:
        try:
            block = text.split("```")[1]
            if block.startswith("json"):
                block = block[4:]
            data = json.loads(block.strip())
            return max(1.0, min(5.0, float(data["score"]))), data.get("reason", "")
        except (json.JSONDecodeError, ValueError, IndexError, KeyError):
            pass

    # Try regex for just the score number
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
