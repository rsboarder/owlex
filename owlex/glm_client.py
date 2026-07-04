"""Async client for GLM-5.2 via Z.ai's Anthropic-compatible endpoint.

Ported from scripts/_glm_client.py for production use by the glm_blind
blind-rater derivation handler.

We call the REST endpoint directly instead of spawning the full `claude` CLI:
the CLI reloads the entire owlex project context on every invocation, adding
~$0.45 and ~10s of overhead. Direct /v1/messages isolates the model from
owlex's CLAUDE.md / MCP context.

Token resolution (in order):
  1. OWLEX_GLM_TOKEN env var
  2. ~/.owlex/glm_token file

Config (per external-CLI-rotation pattern — pin via env, safe defaults):
  OWLEX_GLM_BASE_URL  default https://api.z.ai/api/anthropic
  OWLEX_GLM_MODEL     default glm-5.2
  OWLEX_GLM_TIMEOUT   default 120 (seconds)

See docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md for context.
Patterns ported from scripts/_glm_client.py and scripts/shadow_glm_rater.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

GLM_BASE_URL = os.getenv("OWLEX_GLM_BASE_URL", "https://api.z.ai/api/anthropic").rstrip("/")
GLM_MODEL = os.getenv("OWLEX_GLM_MODEL", "glm-5.2")
GLM_TIMEOUT = int(os.getenv("OWLEX_GLM_TIMEOUT", "120"))


def _read_token() -> str:
    """Read GLM token at call time.

    Order: OWLEX_GLM_TOKEN env → ~/.owlex/glm_token file.
    Token is never logged to prevent accidental exposure.
    """
    token = os.environ.get("OWLEX_GLM_TOKEN", "").strip()
    if token:
        return token
    token_path = Path(os.environ.get("OWLEX_HOME", str(Path.home() / ".owlex"))) / "glm_token"
    if token_path.exists():
        return token_path.read_text().strip()
    return ""


def _headers(token: str) -> dict[str, str]:
    """Build Anthropic-compatible auth headers for Z.ai's endpoint.

    Z.ai accepts the key as x-api-key (Anthropic standard) or Authorization:
    Bearer (what Claude Code sends). Send both so it works regardless of which
    the gateway honors.
    """
    return {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
        "x-api-key": token,
        "authorization": f"Bearer {token}",
    }


def _extract_text(payload: dict) -> str:
    """Pull assistant text out of an Anthropic Messages response."""
    blocks = payload.get("content") or []
    parts = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
    return "".join(parts).strip()


async def call_glm(
    prompt: str,
    max_tokens: int = 2048,
    timeout: int | None = None,
    reasoning: str | None = None,
) -> tuple[str, str | None]:
    """POST a single user message, return (assistant_text, error_or_None).

    reasoning: "high"/"max" enables extended thinking at that effort. Thinking
    tokens count toward max_tokens, so a floor of 4096 is enforced when
    reasoning is on.

    Returns (text, None) on success; ("", error_message) on any failure.
    Callers must treat a non-None error as a soft failure and log + skip.
    """
    token = _read_token()
    if not token:
        return "", "OWLEX_GLM_TOKEN not set and ~/.owlex/glm_token not found"

    effective_timeout = timeout if timeout is not None else GLM_TIMEOUT
    effort = (reasoning or "").strip().lower()

    body: dict = {
        "model": GLM_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if effort in ("high", "max"):
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = effort
        if body["max_tokens"] < 4096:
            body["max_tokens"] = 4096

    url = f"{GLM_BASE_URL}/v1/messages"
    try:
        async with httpx.AsyncClient(timeout=effective_timeout) as client:
            resp = await client.post(url, headers=_headers(token), json=body)
    except httpx.TimeoutException:
        return "", f"timeout after {effective_timeout}s"
    except httpx.HTTPError as e:
        return "", f"http error: {type(e).__name__}: {str(e)[:200]}"

    if resp.status_code != 200:
        return "", f"glm http {resp.status_code}: {resp.text[:300]}"

    try:
        payload = resp.json()
    except ValueError as e:
        return "", f"non-JSON response: {e}"

    text = _extract_text(payload)
    if not text:
        stop = payload.get("stop_reason")
        return "", f"empty content (stop_reason={stop}): {str(payload)[:200]}"

    return text, None


async def probe(timeout: float = 30.0) -> tuple[bool, str]:
    """Lightweight health-check for startup logging.

    Sends a minimal prompt; returns (ok, message) — never raises.
    Mirrors the pattern from owlex/agreement.py:probe_agreement_model.
    """
    try:
        text, err = await call_glm("Reply with exactly: OK", max_tokens=16, timeout=int(timeout))
        if err:
            return False, f"glm probe failed: {err}"
        return True, f"glm_blind reachable; model={GLM_MODEL}"
    except Exception as e:
        return False, f"glm probe exception: {e}"


def _log(msg: str) -> None:
    print(f"[owlex.glm_client] {msg}", file=sys.stderr, flush=True)
