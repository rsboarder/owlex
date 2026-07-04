"""Event-sourced derivation pipeline.

Owlex separates two classes of write at the persistence layer:

  1. **Canonical facts** (block the MCP response):
     - `calls`, `council_anonymization`, `council_outcomes`

  2. **Derived analytics** (DO NOT block the MCP response):
     - `pairwise_agreements`, `position_deltas` (council.py)
     - `skill_invocations`              (engine.py)

Before this module existed, derivations were either inline-awaited (adding
30-90s to user-visible latency on a 4-agent council) or fire-and-forget via
``asyncio.create_task`` (subject to event-loop shutdown losing the write
silently when the council finished fast — e.g. R1 consensus skips R2).

This module owns the queue + long-lived consumer that decouples those two
lifecycles:

  Council/Engine emits a ``DerivationEvent`` → put on a process-wide queue.
  The worker (started in ``server.main()``) pops events forever and runs the
  matching handler, persisting results when ready. On server shutdown the
  worker drains remaining events with a bounded timeout, so a graceful
  ``SIGTERM`` does not lose work.

The same derivation handlers are reused by ``dashboard/backfill.py`` for
historical recovery, so derivation logic has one canonical implementation.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import CouncilRound, Task


# === Event types ===

@dataclass(frozen=True)
class PairwiseEvent:
    """Compute and persist C(N,2) pairwise agreement scores for a council's R1."""
    council_id: str
    prompt: str
    r1_contents: dict[str, str]


@dataclass(frozen=True)
class PositionDeltaEvent:
    """Compute and persist R1→R2 Jaccard distance per agent that participated in both."""
    round_1: "CouncilRound"
    round_2: "CouncilRound"


@dataclass(frozen=True)
class SkillsEvent:
    """Parse a completed task's agent transcript and persist skill_invocations."""
    task_id: str
    seat: str
    runner: str
    ts: str


@dataclass(frozen=True)
class GlmBlindEvent:
    """Background GLM-5.2 blind-rating of R1 responses.

    When OWLEX_GLM_BLIND_ENABLED=1, the council emits this after R1 completes.
    The handler calls GLM-5.2 via Z.ai's Anthropic-compatible endpoint,
    applies the same deterministic anonymization as claude_blind (salt='blind:{council_id}'),
    parses per-letter ratings, and stores per-agent scores under rater='glm_blind'.

    Additive only — never modifies or replaces claude_blind scores.
    See docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md.
    Patterns ported from scripts/shadow_glm_rater.py.
    """
    council_id: str
    question: str        # Original prompt text shown to agents
    r1_contents: dict[str, str]  # agent -> response text from R1


DerivationEvent = PairwiseEvent | PositionDeltaEvent | SkillsEvent | GlmBlindEvent


# === Handlers (single source of truth for derivation logic) ===

async def _handle_pairwise(event: PairwiseEvent) -> None:
    from . import store
    from .agreement import score_agreement

    agents = sorted(event.r1_contents.keys())
    if len(agents) < 2:
        return
    pairs = [(agents[i], agents[j]) for i in range(len(agents)) for j in range(i + 1, len(agents))]
    sem = asyncio.Semaphore(5)

    async def _one(a: str, b: str) -> tuple[str, str, float, str]:
        async with sem:
            score, reason = await score_agreement(
                event.prompt, {a: event.r1_contents[a], b: event.r1_contents[b]},
            )
            return a, b, float(score), reason

    results = await asyncio.gather(*[_one(a, b) for a, b in pairs], return_exceptions=True)
    rows: list[tuple[str, str, float, str | None]] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        rows.append(r)  # type: ignore[arg-type]
    if rows:
        store.record_pairwise_agreements(event.council_id, rows, source="judge")
        _log(f"pairwise: persisted {len(rows)} pairs for {event.council_id}")


async def _handle_position_delta(event: PositionDeltaEvent) -> None:
    from . import store
    from .models import Agent

    def _word_set(s: str) -> set[str]:
        return {w.lower().strip(".,;:!?()\"'`") for w in s.split() if len(w) > 5 and w.isalpha()}

    for agent in Agent:
        r1 = getattr(event.round_1, agent.value, None)
        r2 = getattr(event.round_2, agent.value, None)
        if not (r1 and r2 and r1.content and r2.content and r2.task_id):
            continue
        t1 = _word_set(r1.content)
        t2 = _word_set(r2.content)
        if not t1 and not t2:
            continue
        inter = len(t1 & t2)
        union = len(t1 | t2) or 1
        delta = 1.0 - (inter / union)
        label = "unchanged" if delta < 0.845 else "minor" if delta < 0.906 else "major"
        store.record_position_delta(r2.task_id, position_delta=delta, position_label=label)


async def _handle_skills(event: SkillsEvent) -> None:
    from .dashboard.parsers import parse_and_persist
    await asyncio.to_thread(
        parse_and_persist,
        event.task_id, event.seat, event.ts, None,
        runner=event.runner,
    )


# === GLM-5.2 blind-rater helpers (ported from scripts/shadow_glm_rater.py) ===

_BLIND_RATE_PROMPT = """\
You are a senior software engineering reviewer evaluating multiple AI advisors' answers to the same question.
The advisors are anonymized — you only see letter labels (Response A, B, C, ...).
Rate each response based on its content alone — be strict and discriminating.

ORIGINAL QUESTION:
{question}

{responses}

For EACH letter present above, return a rating with these fields:
- score: -1 (rejected/poor) or +1 (accepted/good)
- groundedness: 1-5 (does it reference real facts, code, sound reasoning?)
- helpfulness: 1-5 (does it actually answer the question with actionable detail?)
- correctness: 1-5 (is the analysis technically right?)
- reason: one sentence

Respond with ONLY a JSON object mapping letters to ratings, e.g.:
{{"A": {{"score": 1, "groundedness": 4, "helpfulness": 5, "correctness": 4, "reason": "..."}}, "B": {{"score": -1, "groundedness": 1, "helpfulness": 1, "correctness": 1, "reason": "Empty response"}}}}

Rate only these letters: {letters}
"""


def _parse_glm_ratings(raw: str, expected_letters: list[str]) -> tuple[dict[str, dict] | None, str | None]:
    """Parse a JSON ratings response from GLM. Returns (ratings_dict, error_or_None).

    Ported from scripts/shadow_glm_rater.py:parse_glm_ratings.
    Tries multiple extraction strategies to handle markdown fences and partial JSON.
    """
    candidates: list[str] = []

    for match in re.finditer(r"\{[^{}]*\"[A-P]\"[^{}]*\{[^{}]*\}.*?\}", raw, re.DOTALL):
        candidates.append(match.group(0))
    if "```" in raw:
        for chunk in raw.split("```"):
            if chunk.strip().startswith("json"):
                candidates.append(chunk.strip()[4:].strip())
            elif chunk.strip().startswith("{"):
                candidates.append(chunk.strip())
    candidates.append(raw.strip())
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and any(k in parsed for k in expected_letters):
                cleaned = {}
                for k, v in parsed.items():
                    if k in expected_letters and isinstance(v, dict):
                        cleaned[k] = v
                if cleaned:
                    return cleaned, None
        except (json.JSONDecodeError, ValueError):
            continue

    return None, f"could not parse ratings; head: {raw[:300]}"


async def _handle_glm_blind(event: GlmBlindEvent) -> None:
    """Background handler: call GLM-5.2 to blind-rate R1 responses.

    Uses assign_labels with salt='blind:{council_id}' — same deterministic
    anonymization as claude_blind so per-agent scores are directly comparable.
    Scores are stored under rater='glm_blind'; claude_blind is untouched.
    Errors (network, parse, missing token) are logged and skipped — the worker
    must never crash because of a single GLM failure.
    """
    from . import glm_client, store
    from .anonymize import assign_labels
    from .config import config

    pairs = list(event.r1_contents.items())
    if len(pairs) < 2:
        _log(f"glm_blind: {event.council_id} — fewer than 2 R1 responses, skipping")
        return

    salt = f"blind:{event.council_id}"
    by_label, label_to_agent = assign_labels(pairs, salt=salt)

    parts = []
    for letter, body in by_label.items():
        truncated = body[:3000] if len(body) > 3000 else body
        parts.append(f"RESPONSE {letter}:\n{truncated}")
    letters_str = ", ".join(by_label.keys())

    prompt = _BLIND_RATE_PROMPT.format(
        question=event.question[:1500],
        responses="\n\n".join(parts),
        letters=letters_str,
    )

    text, err = await glm_client.call_glm(
        prompt,
        max_tokens=2048,
        timeout=config.glm_blind.timeout,
        reasoning=config.glm_blind.reasoning,
    )
    if err:
        _log(f"glm_blind: {event.council_id} — GLM error: {err}")
        return

    expected_letters = list(by_label.keys())
    ratings, parse_err = _parse_glm_ratings(text, expected_letters)
    if not ratings:
        _log(f"glm_blind: {event.council_id} — parse failed: {parse_err}")
        return

    persisted = 0
    for letter, rating in ratings.items():
        agent = label_to_agent.get(letter)
        if not agent:
            continue
        try:
            score = int(rating.get("score", 0))
            if score not in (-1, 1):
                score = 1 if score > 0 else -1
        except (TypeError, ValueError):
            score = -1
        dims = {k: rating.get(k) for k in ("groundedness", "helpfulness", "correctness")}
        reason = rating.get("reason", "")
        store.record_agent_score(
            council_id=event.council_id,
            agent=agent,
            score=score,
            rater="glm_blind",
            dimensions=dims,
            reason=reason,
        )
        persisted += 1

    _log(f"glm_blind: {event.council_id} — persisted {persisted} agent scores")


_HANDLERS: dict[type, callable] = {  # type: ignore[type-arg]
    PairwiseEvent: _handle_pairwise,
    PositionDeltaEvent: _handle_position_delta,
    SkillsEvent: _handle_skills,
}

# GlmBlindEvent is handled exclusively by the dedicated glm_blind worker.
_GLM_BLIND_HANDLER = _handle_glm_blind


# === Queues + workers ===
#
# Two isolated lanes:
#   1. _queue          — fast events (pairwise, position-delta, skills); handler
#                        latency is bounded by the LLM judge (~4s per pair).
#   2. _glm_blind_queue — slow events (GLM-5.2 blind-rating, up to ~120s per
#                        call). Kept on a separate queue + worker so a slow GLM
#                        call never delays a subsequent PairwiseEvent or SkillsEvent.

_queue: "asyncio.Queue[DerivationEvent | None] | None" = None
_glm_blind_queue: "asyncio.Queue[GlmBlindEvent | None] | None" = None


def get_queue() -> "asyncio.Queue[DerivationEvent | None]":
    """Process-wide singleton queue for fast derivation events. Lazily created on first access."""
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


def get_glm_blind_queue() -> "asyncio.Queue[GlmBlindEvent | None]":
    """Process-wide singleton queue for GlmBlindEvent. Lazily created on first access."""
    global _glm_blind_queue
    if _glm_blind_queue is None:
        _glm_blind_queue = asyncio.Queue()
    return _glm_blind_queue


def emit(event: DerivationEvent) -> None:
    """Non-blocking enqueue. Routes GlmBlindEvent to the dedicated slow lane;
    all other events go to the fast-lane queue.

    If no event loop is running (e.g. tests outside asyncio), the event is
    silently dropped — derivations are best-effort and must never raise into
    the caller.
    """
    try:
        if isinstance(event, GlmBlindEvent):
            get_glm_blind_queue().put_nowait(event)
        else:
            get_queue().put_nowait(event)
    except RuntimeError:
        # No running event loop. Acceptable: backfill will recompute later.
        pass
    except Exception as e:
        _log(f"emit failed: {e}")


async def run_worker() -> None:
    """Long-lived consumer for fast derivation events (pairwise/position-delta/skills).

    Pops events forever; ``None`` is the shutdown sentinel. Exceptions in
    handlers are logged but never propagate — one bad event must not stop the
    worker. Started by ``server.main()`` alongside the MCP server.
    """
    queue = get_queue()
    _log("derivation worker started")
    while True:
        event = await queue.get()
        if event is None:
            queue.task_done()
            _log("derivation worker received shutdown sentinel")
            return
        try:
            handler = _HANDLERS.get(type(event))
            if handler is None:
                _log(f"no handler for {type(event).__name__}; dropping")
            else:
                await handler(event)
        except Exception as e:
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            _log(f"handler {type(event).__name__} failed: {e}\n{tb}")
        finally:
            queue.task_done()


async def run_glm_blind_worker() -> None:
    """Long-lived consumer dedicated to GlmBlindEvent (slow lane, up to ~120s/call).

    Isolated from the fast-lane worker so GLM latency never delays
    pairwise/position-delta/skills writes. Shutdown sentinel and error
    isolation mirror run_worker() exactly.
    Started by ``server.main()`` alongside run_worker().
    """
    queue = get_glm_blind_queue()
    _log("glm_blind worker started")
    while True:
        event = await queue.get()
        if event is None:
            queue.task_done()
            _log("glm_blind worker received shutdown sentinel")
            return
        try:
            await _GLM_BLIND_HANDLER(event)
        except Exception as e:
            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            _log(f"handler GlmBlindEvent failed: {e}\n{tb}")
        finally:
            queue.task_done()


async def drain(timeout: float = 30.0) -> int:
    """Wait for both queues to empty, with a bounded deadline.

    Called from ``server.main()`` on shutdown. Returns the total number of
    events that did not complete in time across both lanes (zero on clean drain).

    The glm_blind lane gets the same timeout budget as the fast lane — slow
    in-flight GLM calls that exceed it are logged; backfill recovers them.
    """
    fast_queue = get_queue()
    glm_queue = get_glm_blind_queue()

    fast_pending = fast_queue.qsize()
    glm_pending = glm_queue.qsize()
    total_pending = fast_pending + glm_pending

    if total_pending == 0:
        return 0

    _log(
        f"draining {fast_pending} fast + {glm_pending} glm_blind derivation events "
        f"(timeout {timeout}s each)"
    )

    remaining = 0

    async def _drain_one(queue: "asyncio.Queue", label: str) -> int:
        if queue.qsize() == 0:
            return 0
        try:
            await asyncio.wait_for(queue.join(), timeout=timeout)
            _log(f"{label} queue drained cleanly")
            return 0
        except asyncio.TimeoutError:
            r = queue.qsize()
            _log(f"{label} drain timed out with {r} events pending (run backfill to recover)")
            return r

    fast_remaining, glm_remaining = await asyncio.gather(
        _drain_one(fast_queue, "derivation"),
        _drain_one(glm_queue, "glm_blind"),
    )
    return fast_remaining + glm_remaining


async def shutdown(timeout: float = 30.0) -> None:
    """Signal both workers to stop and wait for drain.

    Puts the shutdown sentinel on both queues after the drain to ensure all
    enqueued events are processed first. Idempotent — safe to call multiple times.
    """
    await drain(timeout=timeout)
    await get_queue().put(None)
    await get_glm_blind_queue().put(None)


def _log(msg: str) -> None:
    print(f"[owlex.derivations] {msg}", file=sys.stderr, flush=True)


def _reset_for_tests() -> None:
    """Reset both singleton queues. Tests use this to isolate between cases."""
    global _queue, _glm_blind_queue
    _queue = None
    _glm_blind_queue = None
