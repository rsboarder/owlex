"""Shared async client for GLM-5.2 via Z.ai's Anthropic-compatible endpoint.

Used by the shadow-replay scripts (shadow_glm_judge/seat/rater.py) so the GLM
candidate is evaluated through the SAME endpoint family the `claudeor` seat would
use in production (ANTHROPIC_BASE_URL -> https://api.z.ai/api/anthropic).

We call the REST endpoint directly instead of spawning the full `claude` CLI:
the CLI reloads the entire owlex project context (~30k tokens, ~$0.45, ~10s) on
every invocation, which is slow, costly, and pollutes the candidate's view with
owlex's CLAUDE.md / MCP context. Direct /v1/messages isolates the model.

Config (per the External-CLI-rotation pattern — pin via env, safe defaults):
  OWLEX_GLM_BASE_URL  default https://api.z.ai/api/anthropic
  OWLEX_GLM_TOKEN     GLM Coding Plan key (required; falls back to ANTHROPIC_AUTH_TOKEN)
  OWLEX_GLM_MODEL     default glm-5.2
  OWLEX_GLM_TIMEOUT   default 120 (seconds)
"""
from __future__ import annotations

import os

import httpx

GLM_BASE_URL = os.getenv("OWLEX_GLM_BASE_URL", "https://api.z.ai/api/anthropic").rstrip("/")
GLM_TOKEN = os.getenv("OWLEX_GLM_TOKEN") or os.getenv("ANTHROPIC_AUTH_TOKEN") or ""
GLM_MODEL = os.getenv("OWLEX_GLM_MODEL", "glm-5.2")
GLM_TIMEOUT = int(os.getenv("OWLEX_GLM_TIMEOUT", "120"))
# "", "high", or "max". When set, requests enable extended thinking at that effort.
# Verified param shape against api.z.ai/api/anthropic: thinking{enabled} + reasoning_effort.
GLM_REASONING = os.getenv("OWLEX_GLM_REASONING", "").strip().lower()


def _headers() -> dict[str, str]:
    # Z.ai's Anthropic-compatible endpoint accepts the Coding Plan key either as
    # x-api-key (Anthropic standard) or Authorization: Bearer (what Claude Code sends).
    # Send both so it works regardless of which the gateway honors.
    return {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
        "x-api-key": GLM_TOKEN,
        "authorization": f"Bearer {GLM_TOKEN}",
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

    reasoning: "high"/"max" enables extended thinking at that effort (falls back
    to the OWLEX_GLM_REASONING env default). Thinking tokens count toward
    max_tokens, so a floor is enforced when reasoning is on.
    """
    if not GLM_TOKEN:
        return "", "OWLEX_GLM_TOKEN not set"
    effort = (reasoning or GLM_REASONING or "").strip().lower()
    body = {
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
        async with httpx.AsyncClient(timeout=timeout or GLM_TIMEOUT) as client:
            resp = await client.post(url, headers=_headers(), json=body)
    except httpx.TimeoutException:
        return "", f"timeout after {timeout or GLM_TIMEOUT}s"
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


async def smoke_test() -> None:
    """Verify endpoint/auth/model before a full sweep. Run: python scripts/_glm_client.py"""
    print(f"[smoke] base={GLM_BASE_URL} model={GLM_MODEL} token={'set' if GLM_TOKEN else 'MISSING'}")
    text, err = await call_glm("Reply with exactly: OK", max_tokens=16)
    if err:
        print(f"[smoke] FAIL: {err}")
    else:
        print(f"[smoke] OK -> {text!r}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(smoke_test())
