# Subprocess fail-pattern must recover stdout when answer is already written

## Problem

`TaskEngine._read_stream_lines` matches each stderr line against `fail_patterns`. When one fires, the runner kills the subprocess and the post-exit handler marks the task `failed`. This pattern made sense for early-fail signals (quota exhausted before any answer).

It silently throws away **successful answers** when the agent dies after writing them:

| seat | duration | stdout | stderr | result |
|---|---|---|---|---|
| gemini | 118s | full 4KB RFC review | `MODEL_CAPACITY_EXHAUSTED` (line 50, after answer) | **task.status = failed**, answer discarded |

The 429 from gemini's internal follow-up API call (post-completion housekeeping) landed in stderr after the user-facing answer was already in stdout. fail_pattern matched, runner SIGKILL'd, post-exit handler ran the `else` branch and lost the answer.

## Root cause

`fail_pattern` semantics conflate two things:

1. **"Fatal before output"** — the agent crashed without producing anything. status=failed is right; nothing to recover.
2. **"Fatal after output"** — agent produced a complete answer, then an internal subsequent call errored. status=failed is **wrong**; the answer is recoverable.

The matcher had no concept of "did stdout have content when this pattern fired."

## Solution

Stash the matching pattern on the task at match time. The post-exit handler then inspects whether stdout was populated and, if so, **promotes the result to completed** with the pattern hit recorded in `task.warnings`:

```python
# In _read_stream_lines, when fail_pattern matches:
if fail_patterns and any(p in decoded for p in fail_patterns):
    task._fail_pattern_matched = decoded   # NEW — remember which pattern
    if task.process and task.process.returncode is None:
        task.process.kill()
    break

# In run_agent_command, after process exits with non-zero:
fail_pattern_hit = getattr(task, '_fail_pattern_matched', None)
if fail_pattern_hit and stdout_text.strip():
    # Agent produced a complete answer BEFORE the pattern fired.
    # Promote to completed, stash error context as a warning.
    task.result = f"{output_prefix}:\n\n{output_cleaner(stdout_text, prompt)}"
    task.warnings = (
        f"recovered after fail_pattern hit: {fail_pattern_hit[:200]}\n"
        f"(stderr was non-empty; full text in last_lines)"
    )
    task.status = TaskStatus.COMPLETED.value
    return

# Else fall through to status=failed as before.
```

## Why this works

- **Early-fail behavior unchanged**: when stdout is empty, the same code path marks failed.
- **Late-fail behavior fixed**: when stdout has content, the work the user paid for is preserved.
- **Diagnostic context preserved**: `task.warnings` records the exact pattern hit + a pointer to `last_lines` for full stderr context. Dashboard can surface "completed with warning" distinctly from clean "completed".
- **Generic across agents**: not gemini-specific; codex/claudeor/cursor all benefit if they have any post-completion stderr error pattern.

## Prevention

- **Regression test** (`tests/test_fail_pattern_recovery.py`):
  - `test_recover_completed_answer_when_fail_pattern_fires_after_stdout` — locks in the recovery invariant.
  - `test_fail_pattern_without_stdout_still_marked_failed` — proves we did not regress the early-fail path.

Both tests run real subprocesses (no mocks) — they spawn Python that writes a known answer then trips the fail_pattern in stderr.

## Why we cannot detect this in the matcher itself

Stdout and stderr are read by separate coroutines in parallel. At match time, the stderr reader has no view of stdout state. The cleanest place to apply context is the post-exit handler, where both stream texts and the matched pattern are all available.

## Related

- See `docs/solutions/architecture/derivation-writes-need-long-lived-consumer.md` — the broader theme: don't discard work that's already done because of a downstream signal.
