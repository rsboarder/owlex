"""
LLM-as-judge scoring for council eval.
Uses anthropic SDK if ANTHROPIC_API_KEY is set, otherwise falls back to claude CLI.
The claude CLI approach only works when run from a terminal (not from within Claude Code).
"""

import asyncio
import json
import os
import sys

JUDGE_MODEL = "claude-sonnet-4-20250514"

JUDGE_PROMPT = """\
You are evaluating an AI agent's response to a software engineering question about a real codebase.

QUESTION:
{question}

AGENT'S RESPONSE:
{response}

EXPECTED TOPICS (the response should ideally cover these):
{expected_topics}

Score the response on each criterion from 1 to 5:

1. **relevance**: Does the answer address the actual question asked? (1=off-topic, 5=directly addresses every aspect)
2. **specificity**: Does it reference actual project files, patterns, or conventions? (1=completely generic, 5=references specific files/functions/patterns from the project)
3. **actionability**: Are recommendations concrete enough to implement? (1=vague platitudes, 5=specific steps with code examples)
4. **depth**: Does it show understanding beyond surface-level? (1=superficial, 5=considers edge cases, tradeoffs, and second-order effects)
5. **accuracy**: Are factual claims about software engineering correct? (1=contains errors, 5=technically sound)

Respond with ONLY valid JSON, no other text:
{{"relevance": <1-5>, "specificity": <1-5>, "actionability": <1-5>, "depth": <1-5>, "accuracy": <1-5>, "reasoning": "<one sentence explaining your scores>"}}
"""


def _parse_json_scores(text: str) -> dict | None:
    """Try to extract JSON scores from LLM output."""
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("{") and "relevance" in line:
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    if "```" in text:
        try:
            block = text.split("```")[1]
            if block.startswith("json"):
                block = block[4:]
            return json.loads(block.strip())
        except (json.JSONDecodeError, IndexError):
            pass

    return None


async def _score_via_sdk(prompt: str) -> dict:
    """Score using anthropic SDK (requires ANTHROPIC_API_KEY)."""
    import anthropic
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text
    result = _parse_json_scores(text)
    if result:
        return result
    return _default_scores(f"unparseable SDK output: {text[:100]}")


async def _score_via_cli(prompt: str, timeout: int = 60) -> dict:
    """Score using claude CLI (requires OAuth login, won't work inside Claude Code)."""
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", "--no-session-persistence",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        proc.communicate(input=prompt.encode()),
        timeout=timeout,
    )

    if proc.returncode != 0:
        err = stderr.decode()[:200]
        return _default_scores(f"CLI failed: {err}")

    text = stdout.decode()
    result = _parse_json_scores(text)
    if result:
        return result
    return _default_scores(f"unparseable CLI output: {text[:100]}")


async def score_response(
    question: str,
    response: str,
    expected_topics: list[str],
    timeout: int = 60,
) -> dict:
    """Score a single agent response. Uses SDK if API key available, else CLI."""
    prompt = JUDGE_PROMPT.format(
        question=question,
        response=response[:8000],
        expected_topics=", ".join(expected_topics),
    )

    try:
        if os.environ.get("ANTHROPIC_API_KEY"):
            return await _score_via_sdk(prompt)
        else:
            return await _score_via_cli(prompt, timeout)
    except Exception as e:
        print(f"  Judge error: {e}", file=sys.stderr)
        return _default_scores(str(e))


def _default_scores(reason: str) -> dict:
    """Return neutral scores when judging fails."""
    return {
        "relevance": 0, "specificity": 0, "actionability": 0,
        "depth": 0, "accuracy": 0, "reasoning": f"Scoring failed: {reason}",
    }


def compute_consistency(agent_responses: dict[str, str]) -> float:
    """
    Compute cross-agent consistency score (1-5).
    Simple heuristic: check how many agents mention the same key concepts.
    """
    if len(agent_responses) < 2:
        return 5.0

    term_sets = []
    for content in agent_responses.values():
        if not content:
            continue
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

    avg_similarity = sum(similarities) / len(similarities) if similarities else 0
    return round(1 + avg_similarity * 4, 1)
