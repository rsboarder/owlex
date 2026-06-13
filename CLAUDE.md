# Owlex — Agent Instructions

**Owlex** is an MCP server that orchestrates 6 CLI agents (codex, gemini, cursor, claudeor, opencode, aichat) into parallel "council" deliberations with R1 (independent answers) + R2 (anonymized cross-critique), a blind orchestrator-rating step, and a SQLite-backed dashboard for analytics.

**Stack**: Python 3.11 · FastMCP (stdio) · SQLite + WAL · asyncio subprocesses · FastAPI + uvicorn (dashboard) · React + Vite + Recharts (dashboard UI) · pytest + pytest-asyncio.

**Distribution**: `uv tool install --reinstall .` from repo root → installs `owlex-server` and `owlex-dashboard` consoles.

## Architecture (one-line per layer)

- `owlex/server/` — FastMCP entry. Tools: `council_ask`, `rate_council`, `second_opinion`, `start_*_session`, `resume_*_session`, etc.
- `owlex/council.py` — R1/R2 orchestration, role assignment, anonymization, outcome assembly. Calls `EnginePort` only.
- `owlex/engine.py` — subprocess lifecycle, stream readers, heartbeat, recursion fence, output cap.
- `owlex/derivations.py` — **long-lived worker** for analytics writes (pairwise, position deltas, skills). Survives request scope.
- `owlex/agreement.py` — LLM agreement judge for auto-deliberation. Probed at startup.
- `owlex/agents/*.py` — per-CLI runners (`build_exec_command` / `build_resume_command`).
- `owlex/adapters/repositories/` — SQLite write-side façade. `store.py` re-exports for back-compat.
- `owlex/dashboard/` — read-side FastAPI + React UI. `backfill.py` for derivation recovery.

## Operational Excellence

- After **every edit**, run `python -m pytest tests/ -q`. No exceptions.
- After deploying a server-side change: `uv tool install --reinstall .`, then `pkill -9 -f owlex-server`. Claude Code respawns on next council call.
- Server stderr is tee'd to `~/.owlex/logs/server-{pid}.log`. Read it when a council fails.
- Council DB: `~/.owlex/owlex.db` (SQLite, WAL). Production-only — tests have an autouse fixture isolating to a tmp dir.

## Quality Gates

- Don't assert error message *content* in tests. Assert behavior (status, side-effects).
- Don't use `asyncio.create_task(...)` for DB writes inside the awaited coroutine. Use `derivations.emit(event)`.
- Cross-layer reaches go through `owlex/ports.py` Protocols. Adding a new `self._engine.X` call site requires updating `EnginePort`.

<details>
<summary>Learned Patterns</summary>

### Derivation Writes Need a Long-Lived Consumer

**Problem**: Background analytics tasks via `asyncio.create_task(...)` get cancelled when the request-scoped event loop tears down. Fast councils (R1 consensus → R2 skipped) lose pairwise/skills writes silently.

**Solution**: Process-wide `asyncio.Queue` + `run_worker()` started in `server.main()`. Producers call `derivations.emit(event)`. Drain on shutdown with bounded timeout; backfill recovers any straggle.
See `docs/solutions/architecture/derivation-writes-need-long-lived-consumer.md`.

### Subprocess fail_pattern Must Recover stdout

**Problem**: When `fail_patterns` matches a stderr line, the runner SIGKILLs the subprocess and the post-exit handler marks `failed` — silently discarding a completed answer when the agent died on a post-completion follow-up (gemini 429, codex transient).

**Solution**: Stash matched pattern on the task at match time; post-exit handler promotes status to `completed` + records the hit in `warnings` when stdout was non-empty.
See `docs/solutions/subprocess/fail-pattern-recovery.md`.

### Tests Must Override Path.home()-Based Globals

**Problem**: `OWLEX_HOME = Path.home() / ".owlex"` resolved at import time → every test imported `store` and wrote to production DB. Result: 81 MagicMock-poisoned rows and 1143 cascaded pairwise rows in prod.

**Solution**: Resolve `OWLEX_HOME` at call time via `_owlex_home()` reading env var. Autouse fixture in `tests/conftest.py` sets `OWLEX_HOME=<tmp>` and calls `store._reset_for_tests()` / `derivations._reset_for_tests()`. Cannot be forgotten.
See `docs/solutions/testing/path-home-globals-pollute-prod.md`.

### Cross-Layer Reaches Need Protocol Contracts

**Problem**: `Council` called `self._engine._log_timing(task)`. Refactor of `TaskEngine` removed that method. `AttributeError` at runtime, in a rarely-executed timeout branch. Three full councils lost their post-R2 pipeline before anyone noticed.

**Solution**: `owlex/ports.py` declares `EnginePort` (`runtime_checkable` Protocol). `Council._engine: EnginePort` typed annotation. `tests/test_engine_port.py` includes a static AST scan that fails CI if council.py reaches any method not on the Port.
See `docs/solutions/protocols/cross-layer-reaches-need-protocol-contracts.md`.

### External CLI Catalogs Rotate — Pin via Env + Probe at Startup

**Problem**: `AGREEMENT_MODEL = "gemini-2.5-flash"` hardcoded. Cursor removed it from catalog. Owlex silently fell back to overlap heuristic for weeks; every R1→R2 decision was string-overlap, not LLM judge. Repeated with `gemini-3-flash` after Google's preview deprecation — same silent failure mode.

**Solution**: Every pinned model identifier is `os.getenv("OWLEX_*_MODEL", "default")`. Startup probe in `server.main()` calls `agreement.probe_agreement_model(timeout=10s)` and logs `[ok]`/`[WARN]` line. Recovery is one env-var edit in `~/.claude/settings.json`. As of 2026-05-23 the judge runs through codex CLI with `gpt-5.5` + `reasoning_effort=low` — eliminates cursor's catalog as a moving part and gives ~4s per judge call.
See `docs/solutions/external-tools/cli-catalog-rotation-needs-health-check.md`.

### Shadow-Replay Required Before New Seat/Judge/Rater Integration

**Problem**: Ad-hoc seat additions worked when candidates were homogeneous (codex/claudeor/gemini — all general-purpose). As the LLM landscape diversifies (Grok-build, GLM, Qwen-Coder), vendor positioning ("coding-focused", "fastest") consistently diverges from measured behavior on this specific workload. Integrating a misfit seat adds noise instead of signal and is expensive to detect (rating shifts, judge drift).

**Solution**: Before integrating any new CLI agent as seat/judge/rater, run three shadow experiments against historical `~/.owlex/owlex.db` (read-only) — agreement-judge replay, seat-R1 structural shadow, blind-rater replay. Templates: `scripts/shadow_grok_judge.py`, `scripts/shadow_grok_seat.py`, `scripts/shadow_grok_rater.py`. Decision matrix + per-segment bias check in the protocol doc.
See `docs/solutions/architecture/shadow-replay-protocol-for-seat-evaluation.md`.
Worked example (Grok-build May 2026): `docs/solutions/architecture/grok-build-2026-05-shadow-eval.md`.

</details>

## Environment

Owlex reads these at MCP-server startup (from `~/.claude/settings.json` `env`):

| Var | Default | Purpose |
|---|---|---|
| `COUNCIL_EXCLUDE_AGENTS` | `""` | CSV of seats to skip (e.g. `aichat,opencode`) |
| `COUNCIL_SUBSTITUTION_MODELS` | `""` | `seat:runner:model,...` (e.g. `claudeor:codex:gpt-5.5`) |
| `CURSOR_MODEL` | (cursor default) | Pin cursor seat's model (e.g. `composer-2.5`) |
| `OWLEX_AGREEMENT_MODEL` | `gpt-5.5` | Model used by the judge (codex CLI) |
| `OWLEX_AGREEMENT_REASONING` | `low` | Reasoning effort for judge (`low`/`medium`/`high`) |
| `OWLEX_AGREEMENT_TIMEOUT` | `90` | Per-call timeout for agreement judge (s) |
| `OWLEX_SECOND_OPINION_MODEL` | `gpt-5.5` | Model for the `second_opinion` tool (codex CLI) |
| `OWLEX_SECOND_OPINION_REASONING` | `high` | Reasoning effort for `second_opinion` (`low`/`medium`/`high`) |
| `OWLEX_SECOND_OPINION_TIMEOUT` | `120` | Per-call timeout for `second_opinion` (s) |
| `OWLEX_HOME` | `~/.owlex` | Persistence root. Set by tests' autouse fixture. |
| `OWLEX_AGENT_MAX_OUTPUT_BYTES` | `25_000_000` | Per-stream output cap for runaway agents |
| `OWLEX_DISABLE_SERVER_LOG` | `""` | Set `1` to skip stderr-tee log file |

## Testing

- 266+ tests, runs in ~14s. `python -m pytest tests/ -q`
- Autouse `_isolate_owlex_home` fixture in `conftest.py` — never writes to production DB.
- Regression contracts (do not remove):
  - `tests/test_engine_port.py` — Council ↔ Engine Protocol.
  - `tests/test_derivations.py` — long-lived worker semantics.
  - `tests/test_fail_pattern_recovery.py` — stdout recovery invariant.
  - `tests/test_agreement_probe.py` — model probe behavior.
  - `tests/test_output_cap.py` — runaway-output kill.
  - `tests/test_heartbeat.py` — last_output_monotonic propagation.
  - `tests/test_recursion_fence.py` — OWLEX_COUNCIL_DEPTH inheritance.
