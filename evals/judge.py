"""
LLM-as-judge scoring for council eval.
Uses claude CLI in print mode for scoring — no API key needed.
"""

import asyncio
import json
import sys

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

JSON_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "relevance": {"type": "integer", "minimum": 1, "maximum": 5},
        "specificity": {"type": "integer", "minimum": 1, "maximum": 5},
        "actionability": {"type": "integer", "minimum": 1, "maximum": 5},
        "depth": {"type": "integer", "minimum": 1, "maximum": 5},
        "accuracy": {"type": "integer", "minimum": 1, "maximum": 5},
        "reasoning": {"type": "string"},
    },
    "required": ["relevance", "specificity", "actionability", "depth", "accuracy", "reasoning"],
})


async def score_response(
    question: str,
    response: str,
    expected_topics: list[str],
    timeout: int = 60,
) -> dict:
    """Score a single agent response using claude CLI as judge."""
    prompt = JUDGE_PROMPT.format(
        question=question,
        response=response[:8000],  # Cap to avoid overwhelming the judge
        expected_topics=", ".join(expected_topics),
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p",
            "--output-format", "json",
            "--json-schema", JSON_SCHEMA,
            "--no-session-persistence",
            "--tools", "",  # No tools needed for judging
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode()),
            timeout=timeout,
        )

        if proc.returncode != 0:
            print(f"  Judge failed: {stderr.decode()[:200]}", file=sys.stderr)
            return _default_scores("judge process failed")

        output = stdout.decode().strip()

        # claude --output-format json wraps result in {"type":"result","result":"..."}
        try:
            wrapper = json.loads(output)
            if isinstance(wrapper, dict) and "result" in wrapper:
                result_text = wrapper["result"]
                # The result might be a JSON string or already parsed
                if isinstance(result_text, str):
                    return json.loads(result_text)
                return result_text
            return wrapper
        except (json.JSONDecodeError, TypeError):
            # Try to extract JSON from the raw output
            for line in output.split("\n"):
                line = line.strip()
                if line.startswith("{") and "relevance" in line:
                    return json.loads(line)
            print(f"  Judge output not parseable: {output[:200]}", file=sys.stderr)
            return _default_scores("unparseable output")

    except asyncio.TimeoutError:
        return _default_scores("judge timeout")
    except FileNotFoundError:
        return _default_scores("claude CLI not found")
    except Exception as e:
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

    # Extract key terms from each response (words > 5 chars, lowered)
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

    # Compute pairwise Jaccard similarity
    similarities = []
    for i in range(len(term_sets)):
        for j in range(i + 1, len(term_sets)):
            intersection = len(term_sets[i] & term_sets[j])
            union = len(term_sets[i] | term_sets[j])
            if union > 0:
                similarities.append(intersection / union)

    avg_similarity = sum(similarities) / len(similarities) if similarities else 0
    # Map 0-1 similarity to 1-5 scale
    return round(1 + avg_similarity * 4, 1)
