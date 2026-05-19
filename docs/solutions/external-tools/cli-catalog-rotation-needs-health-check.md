# External CLI catalogs rotate — pinned model strings need env-override + startup health-check

## Problem

`owlex/agreement.py` hardcoded the model used by the agreement judge:

```python
AGREEMENT_MODEL = "gemini-2.5-flash"
```

The judge runs through Cursor's `agent` CLI. **Cursor rotates its model catalog frequently** — `gemini-2.5-flash` was eventually removed. After the rotation:

```
$ agent --model gemini-2.5-flash ...
Cannot use this model: gemini-2.5-flash. Available models: gemini-3-flash, ...
```

Owlex's agreement code caught the non-zero exit and **silently fell back to a term-overlap heuristic**. The fallback reason was `"judge failed"` — generic, not actionable. The dashboard's agreement-score chart kept rendering numbers (heuristic numbers), so nothing screamed.

This degraded the council's auto-deliberation decision quality for weeks. Every R1 → R2 trigger decision was being made by a string-overlap heuristic instead of an LLM judge.

Same pattern reappeared a few weeks later: a different stale name (`gemini-3-flash` was a Cursor namespace alias not present in Google's API) caused a 404 when pinned in `~/.gemini/settings.json`, and the gemini council seat started failing at 11s.

## Root cause

External CLI catalogs (Cursor's `agent`, Google's `gemini`, OpenAI's `codex`) are **independent inventories that rotate without coordination**. Three failure modes:

1. **Removal**: name was valid, now returns "Cannot use this model". Owlex silently falls back.
2. **Namespace confusion**: name exists in tool A's catalog but not tool B's; pinning the name in tool B returns 404 (e.g., `gemini-3-flash` works in cursor-agent's `--model` but Google's API doesn't know it).
3. **Capacity throttling**: name exists, but `auto`-routing picks a preview-tier model that returns 429 under load — invisible until you read the JSON error.

In all three, **the failure was silenced by fallback logic**. There was no startup probe that would have caught the rotation immediately.

## Solution

Three layers, in increasing strength:

### 1. Make every model name env-configurable

```python
AGREEMENT_MODEL = os.getenv("OWLEX_AGREEMENT_MODEL", "gemini-3-flash")
DEFAULT_JUDGE_TIMEOUT = int(os.getenv("OWLEX_AGREEMENT_TIMEOUT", "90"))
```

Recovery is now one env-var change in `~/.claude/settings.json`, no code edit. Default is updated when we choose a known-good model; users override when Cursor rotates next.

### 2. Startup health-check (probe at server boot)

```python
# owlex/agreement.py
async def probe_agreement_model(timeout: float = 10.0) -> tuple[bool, str]:
    """Returns (ok, message). Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "agent", "--print", "--output-format", "text", "--trust",
            "--model", AGREEMENT_MODEL,
            "Reply with one word: OK",
            stdout=PIPE, stderr=PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return False, f"agreement model probe timed out (model={AGREEMENT_MODEL!r})"
    except FileNotFoundError:
        return False, "cursor-agent CLI not found on PATH"

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")
        if "Cannot use this model" in err:
            return False, (
                f"agreement model {AGREEMENT_MODEL!r} not in cursor-agent catalog. "
                f"Override via OWLEX_AGREEMENT_MODEL. cursor stderr head: {err[:200]}"
            )
        return False, f"agreement model probe exit {proc.returncode}: {err[:200]}"
    return True, f"agreement model {AGREEMENT_MODEL!r} probed ok"
```

`server/main()` fires this as a background task at startup; result is logged loudly:

```
[ok]   agreement health-check: agreement model 'gemini-3-flash' probed ok
[WARN] agreement health-check: agreement model 'gemini-2.5-flash' not in cursor-agent catalog.
       Override via OWLEX_AGREEMENT_MODEL. cursor stderr head: Cannot use this model: ...
```

This makes silent degradation **noisy** — the operator sees the problem at boot, not weeks later.

### 3. Fallback never silences without flagging

When `score_agreement` returns a heuristic fallback, the `reason` field must contain the literal substring telling us this was a fallback (`judge timeout`, `judge failed`, `cursor CLI not found`). Dashboard analytics can count fallback rate over time and alert if it crosses a threshold.

## Why this works

- **Boot-time probe is dirt cheap** (~2s for a "say OK" round-trip) and runs once per server lifetime.
- **Env override is the recovery action**: when the probe fails, the operator gets a one-line fix in the log.
- **Fallback path still works** — health-check failure does NOT block server startup. Heuristic still runs at council time. We trade silent degradation for **noisy degradation**.

## Prevention

- **T1 (implemented)**: `tests/test_agreement_probe.py` — 4 tests:
  - `test_probe_ok` — happy path.
  - `test_probe_detects_missing_model` — cursor's exact "Cannot use this model" stderr shape.
  - `test_probe_handles_timeout` — slow subprocess gets bounded.
  - `test_probe_handles_missing_cli` — `FileNotFoundError` is graceful.
- **T2 (future)**: dashboard widget counting `agreement_reason LIKE '%judge %'` rate per day. Alarm when above 5%.

## Generalizable rule

For every external CLI dependency we name a specific model/version of:

1. **Pin via env** — never hardcode model strings.
2. **Probe at startup** — fail noisily, not silently.
3. **Make fallback observable** — log the reason, count the rate.

Without all three, the next vendor catalog rotation will degrade the system silently again.

## Related

- `owlex/agreement.py` — the probe + model resolution.
- `owlex/server/__init__.py` — wires probe into startup.
- `~/.claude/settings.json` env section — current overrides (`OWLEX_AGREEMENT_MODEL`, etc.).
