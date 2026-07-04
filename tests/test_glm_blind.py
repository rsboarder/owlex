"""Tests for the GLM-5.2 background blind-rater (GlmBlindEvent handler).

Mirrors tests/test_derivations.py style + autouse _isolate_owlex_home fixture.
Never hits the real Z.ai API — glm_client.call_glm is mocked in every test.
Never writes to the production DB — autouse fixture points OWLEX_HOME at tmp.
"""
from __future__ import annotations

import asyncio
import json
import types

import pytest

from owlex import derivations


def _make_glm_blind_config(enabled=True, model="glm-5.2", reasoning="max", timeout=120):
    """Return a simple namespace mimicking GlmBlindConfig for monkeypatching.

    OwlexConfig is frozen=True so we cannot setattr on it directly.
    Instead we replace the module-level config object in owlex.config with a
    lightweight SimpleNamespace wrapper that satisfies config.glm_blind.*
    attribute access in the handler.
    """
    glm_blind = types.SimpleNamespace(enabled=enabled, model=model, reasoning=reasoning, timeout=timeout)
    return glm_blind


def _patch_config(monkeypatch, **glm_kwargs):
    """Patch owlex.config.config.glm_blind by replacing the whole config with a wrapper."""
    import owlex.config as _cfg_mod
    orig = _cfg_mod.config
    fake_cfg = types.SimpleNamespace(glm_blind=_make_glm_blind_config(**glm_kwargs))
    monkeypatch.setattr(_cfg_mod, "config", fake_cfg)
    return fake_cfg


# ---------------------------------------------------------------------------
# Config: OWLEX_GLM_BLIND_ENABLED defaults to False
# ---------------------------------------------------------------------------

def test_glm_blind_disabled_by_default(monkeypatch):
    """GlmBlindConfig.enabled must default False so flag-off = zero behavior change."""
    monkeypatch.delenv("OWLEX_GLM_BLIND_ENABLED", raising=False)
    from owlex.config import load_config
    cfg = load_config()
    assert cfg.glm_blind.enabled is False


def test_glm_blind_enabled_via_env(monkeypatch):
    """Setting OWLEX_GLM_BLIND_ENABLED=1 flips the flag on."""
    monkeypatch.setenv("OWLEX_GLM_BLIND_ENABLED", "1")
    from owlex.config import load_config
    cfg = load_config()
    assert cfg.glm_blind.enabled is True


def test_glm_blind_config_defaults(monkeypatch):
    """Other GlmBlindConfig fields have the right defaults."""
    monkeypatch.delenv("OWLEX_GLM_BLIND_MODEL", raising=False)
    monkeypatch.delenv("OWLEX_GLM_BLIND_REASONING", raising=False)
    monkeypatch.delenv("OWLEX_GLM_BLIND_TIMEOUT", raising=False)
    from owlex.config import load_config
    cfg = load_config()
    assert cfg.glm_blind.model == "glm-5.2"
    assert cfg.glm_blind.reasoning == "max"
    assert cfg.glm_blind.timeout == 120


# ---------------------------------------------------------------------------
# Handler: happy path — GLM returns valid ratings, store is called correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_glm_blind_persists_scores(monkeypatch):
    """Happy path: valid GLM JSON → record_agent_score called with rater='glm_blind'."""
    event = derivations.GlmBlindEvent(
        council_id="c-test",
        question="What is 2+2?",
        r1_contents={"agent_a": "four", "agent_b": "2+2=4"},
    )

    # Discover the deterministic letter→agent mapping for this council.
    from owlex.anonymize import assign_labels
    pairs = list(event.r1_contents.items())
    by_label, label_to_agent = assign_labels(pairs, salt=f"blind:{event.council_id}")
    letters = list(by_label.keys())
    assert len(letters) == 2

    glm_json = {
        letters[0]: {"score": 1, "groundedness": 4, "helpfulness": 5, "correctness": 4, "reason": "good"},
        letters[1]: {"score": -1, "groundedness": 2, "helpfulness": 2, "correctness": 2, "reason": "weak"},
    }

    async def fake_call_glm(prompt, max_tokens=2048, timeout=None, reasoning=None):
        return json.dumps(glm_json), None

    monkeypatch.setattr("owlex.glm_client.call_glm", fake_call_glm)

    recorded: list[dict] = []

    def fake_record_agent_score(council_id, agent, score, **kwargs):
        recorded.append({"council_id": council_id, "agent": agent, "score": score, **kwargs})

    monkeypatch.setattr("owlex.store.record_agent_score", fake_record_agent_score)
    _patch_config(monkeypatch)

    await derivations._handle_glm_blind(event)

    assert len(recorded) == 2
    for row in recorded:
        assert row["council_id"] == "c-test"
        assert row["rater"] == "glm_blind"
        assert row["score"] in (-1, 1)
        dims = row["dimensions"]
        assert "groundedness" in dims
        assert "helpfulness" in dims
        assert "correctness" in dims


@pytest.mark.asyncio
async def test_handle_glm_blind_maps_letters_back_to_agents(monkeypatch):
    """Agent identity is correctly recovered from the letter→agent mapping."""
    event = derivations.GlmBlindEvent(
        council_id="c-map",
        question="Q?",
        r1_contents={"codex": "codex answer", "gemini": "gemini answer"},
    )

    from owlex.anonymize import assign_labels
    pairs = list(event.r1_contents.items())
    by_label, label_to_agent = assign_labels(pairs, salt=f"blind:{event.council_id}")
    letters = list(by_label.keys())

    glm_json = {
        letters[0]: {"score": 1, "groundedness": 5, "helpfulness": 5, "correctness": 5, "reason": "best"},
        letters[1]: {"score": -1, "groundedness": 1, "helpfulness": 1, "correctness": 1, "reason": "worst"},
    }

    async def fake_call_glm(prompt, max_tokens=2048, timeout=None, reasoning=None):
        return json.dumps(glm_json), None

    monkeypatch.setattr("owlex.glm_client.call_glm", fake_call_glm)

    recorded: dict[str, int] = {}

    def fake_record_agent_score(council_id, agent, score, **kwargs):
        recorded[agent] = score

    monkeypatch.setattr("owlex.store.record_agent_score", fake_record_agent_score)
    _patch_config(monkeypatch)

    await derivations._handle_glm_blind(event)

    assert set(recorded.keys()) == {"codex", "gemini"}
    expected_winner = label_to_agent[letters[0]]
    expected_loser = label_to_agent[letters[1]]
    assert recorded[expected_winner] == 1
    assert recorded[expected_loser] == -1


# ---------------------------------------------------------------------------
# Handler: error paths — GLM errors / parse failures must not crash the worker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_glm_blind_glm_error_does_not_crash(monkeypatch):
    """A GLM API error must be logged and skipped — never crash the worker."""
    async def fake_call_glm(prompt, max_tokens=2048, timeout=None, reasoning=None):
        return "", "timeout after 120s"

    monkeypatch.setattr("owlex.glm_client.call_glm", fake_call_glm)

    recorded: list[dict] = []
    monkeypatch.setattr("owlex.store.record_agent_score", lambda *a, **kw: recorded.append(kw))
    _patch_config(monkeypatch)

    event = derivations.GlmBlindEvent(
        council_id="c-err",
        question="Q?",
        r1_contents={"a": "answer a", "b": "answer b"},
    )
    await derivations._handle_glm_blind(event)
    assert len(recorded) == 0


@pytest.mark.asyncio
async def test_handle_glm_blind_parse_failure_does_not_crash(monkeypatch):
    """Garbage GLM output must be logged and skipped — never crash the worker."""
    async def fake_call_glm(prompt, max_tokens=2048, timeout=None, reasoning=None):
        return "not valid json at all !@#", None

    monkeypatch.setattr("owlex.glm_client.call_glm", fake_call_glm)

    recorded: list[dict] = []
    monkeypatch.setattr("owlex.store.record_agent_score", lambda *a, **kw: recorded.append(kw))
    _patch_config(monkeypatch)

    event = derivations.GlmBlindEvent(
        council_id="c-parse",
        question="Q?",
        r1_contents={"a": "answer a", "b": "answer b"},
    )
    await derivations._handle_glm_blind(event)
    assert len(recorded) == 0


@pytest.mark.asyncio
async def test_handle_glm_blind_skips_fewer_than_two_responses(monkeypatch):
    """Handler must skip gracefully when fewer than 2 R1 responses exist."""
    called: list[int] = []

    async def fake_call_glm(prompt, max_tokens=2048, timeout=None, reasoning=None):
        called.append(1)
        return "{}", None

    monkeypatch.setattr("owlex.glm_client.call_glm", fake_call_glm)
    _patch_config(monkeypatch)

    event = derivations.GlmBlindEvent(
        council_id="c-single",
        question="Q?",
        r1_contents={"only_one": "solo answer"},
    )
    await derivations._handle_glm_blind(event)
    assert len(called) == 0


# ---------------------------------------------------------------------------
# Integration: GlmBlindEvent goes through the worker queue end-to-end
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_glm_blind_event_goes_through_dedicated_worker(monkeypatch):
    """GlmBlindEvent is routed to the dedicated glm_blind worker, not the main worker."""
    handled: list[derivations.GlmBlindEvent] = []

    async def fake_handler(event):
        handled.append(event)

    # Patch the dedicated handler used by run_glm_blind_worker.
    monkeypatch.setattr(derivations, "_GLM_BLIND_HANDLER", fake_handler)

    worker = asyncio.create_task(derivations.run_glm_blind_worker())
    try:
        derivations.emit(derivations.GlmBlindEvent(
            council_id="c-worker",
            question="Q?",
            r1_contents={"a": "x", "b": "y"},
        ))
        await derivations.drain(timeout=2.0)
    finally:
        await derivations.shutdown(timeout=1.0)
        await asyncio.wait_for(worker, timeout=2.0)

    assert len(handled) == 1
    assert handled[0].council_id == "c-worker"


@pytest.mark.asyncio
async def test_glm_blind_event_does_not_go_to_main_worker(monkeypatch):
    """GlmBlindEvent emitted via emit() lands on the glm_blind queue, not the main queue."""
    derivations.emit(derivations.GlmBlindEvent(
        council_id="c-lane",
        question="Q?",
        r1_contents={"a": "x", "b": "y"},
    ))

    fast_queue = derivations.get_queue()
    glm_queue = derivations.get_glm_blind_queue()

    assert fast_queue.qsize() == 0, "GlmBlindEvent must NOT be on the fast-lane queue"
    assert glm_queue.qsize() == 1, "GlmBlindEvent must be on the glm_blind queue"

    # Clean up — drain the glm_blind queue so other tests start clean.
    glm_queue.get_nowait()
    glm_queue.task_done()


@pytest.mark.asyncio
async def test_non_glm_events_do_not_go_to_glm_blind_queue(monkeypatch):
    """PairwiseEvent / SkillsEvent land on the main fast-lane queue, not glm_blind."""
    derivations.emit(derivations.PairwiseEvent(
        council_id="c-fast",
        prompt="p",
        r1_contents={"a": "x", "b": "y"},
    ))

    fast_queue = derivations.get_queue()
    glm_queue = derivations.get_glm_blind_queue()

    assert fast_queue.qsize() == 1, "PairwiseEvent must be on the fast-lane queue"
    assert glm_queue.qsize() == 0, "PairwiseEvent must NOT be on the glm_blind queue"

    # Clean up.
    fast_queue.get_nowait()
    fast_queue.task_done()


@pytest.mark.asyncio
async def test_glm_blind_worker_error_isolation(monkeypatch):
    """A GLM handler error must not kill the glm_blind worker — next event still processes."""
    calls: list[int] = []
    processed_after_error: list[str] = []

    async def flaky_handler(event):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("GLM exploded on first call")
        processed_after_error.append(event.council_id)

    monkeypatch.setattr(derivations, "_GLM_BLIND_HANDLER", flaky_handler)

    worker = asyncio.create_task(derivations.run_glm_blind_worker())
    try:
        derivations.emit(derivations.GlmBlindEvent(
            council_id="bad", question="Q?", r1_contents={"a": "x", "b": "y"},
        ))
        derivations.emit(derivations.GlmBlindEvent(
            council_id="good", question="Q?", r1_contents={"a": "x", "b": "y"},
        ))
        await derivations.drain(timeout=2.0)
    finally:
        await derivations.shutdown(timeout=1.0)
        await asyncio.wait_for(worker, timeout=2.0)

    assert processed_after_error == ["good"]
