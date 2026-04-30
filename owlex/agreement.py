"""
Fast agreement scoring for auto-deliberation.
Uses Cursor CLI with gemini-2.5-flash to judge whether R1 responses agree.
"""

import asyncio
import json
import re
import sys


AGREEMENT_MODEL = "gemini-2.5-flash"

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
    timeout: int = 30,
) -> tuple[float, str]:
    """
    Score agreement between agent responses using Cursor CLI + gemini-2.5-flash.

    Returns (score, reason) where score is 1.0-5.0.
    Falls back to term-overlap heuristic if the judge fails.
    """
    if len(responses) < 2:
        return 5.0, "Single response"

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
