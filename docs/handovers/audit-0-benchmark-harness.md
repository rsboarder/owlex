# AUDIT-0: Benchmark Harness + Corpus — Handover (2026-06-09)

**Backlog:** `TASK-1` (AUDIT-0, **Done**) — tracked in the local Backlog.md project (label `audit-hardening`).

## Goal
Build the `bench/` harness + seeded&real corpus + scorer + committed baselines for `solution-audit`. This is **step 1** of the Owlex audit-hardening plan and the **dependency that unblocks AUDIT-1/2/3/4/5/6** — every downstream ticket's BEFORE/AFTER benchmark runs through this harness.

**Single source of truth for the full plan:** [`docs/plans/owlex-audit-hardening.md`](../plans/owlex-audit-hardening.md). Read it first — it has all 8 tickets, the dependency graph, and the per-ticket benchmark specs. This handover is the runbook for AUDIT-0 only.

## Status
- ✅ **Plan designed + persisted, NOT deployed to Plane.** 8 tickets with full bodies + было/стало benchmark specs in the plan doc. Plane deploy blocked — see Risks P1.
- ✅ **The code under audit is implemented + green but UNCOMMITTED.** The `second_opinion` feature (`owlex/second_opinion.py`, `owlex/server/_second_opinion.py`), its P1/P2/P3 fixes (reap-on-timeout, `(ok,text,timed_out)` 3-tuple + `ErrorCode.TIMEOUT` mapping, dead-param removal in `agreement.py`), and server-side `[owlex.second_opinion]` logging are all in the working tree. `python -m pytest tests/ -q` → **303 passed**. `owlex==0.1.13` reinstalled.
- ✅ **solution-audit skill** (`~/.claude/skills/solution-audit/SKILL.md`, EXTERNAL to repo) already wires the cross-model `second_opinion` reviewer into Phase 1 + a mandatory Phase-3 receipt.
- ❌ **AUDIT-0 harness: zero code written.** This is the task.

## What's left (AUDIT-0)
1. **Create `bench/` layout** — `bench/run.py` (runner+CLI), `bench/scorer.py`, `bench/corpus/seeded/manifest.json`, `bench/corpus/real/`, `bench/baselines/`, `bench/README.md`. *Acceptance:* `python bench/run.py --help` works.
2. **Seeded corpus** — synthetic diffs with known injected bugs, each labeled `{bug_type, file, line, description}` in the manifest; **≥10 bugs across ≥3 types + ≥2 decoys** (plausible-but-not-a-bug, to measure precision). *Acceptance:* manifest validates against a schema; decoys flagged.
3. **Real corpus** — a handful of real owlex git-history diffs (`git show <sha> > bench/corpus/real/<sha>.diff`), unlabeled, for cost/realism. *Acceptance:* ≥3 real diffs of varied size.
4. **Runner** — executes a target audit sub-step on each corpus item, **K times (default K=5)**; records per run: findings (file:line + text), tokens (if available), wall-time. Start with the **`cross_model` target = direct call to `owlex.second_opinion.get_second_opinion()`** (see Risks P0 — only this target is cleanly scriptable). *Acceptance:* `python bench/run.py --corpus seeded --target cross_model --runs 5` emits a JSON report.
5. **Scorer** — vs seeded ground-truth → precision / recall / detection-rate per item + aggregate (mean ± stdev over K); cost metrics for all. *Acceptance:* `bench/scorer.py` + unit tests on a hand-checked fixture (known TP/FP/FN) green.
6. **Baselines** — capture the "было" snapshot for the cross_model target to `bench/baselines/cross_model.json` (committed). *Acceptance:* baseline file exists + is reproducible across runs (within stdev).

## Decisions locked (do NOT re-debate)
- **Corpus = seeded + real.** Seeded gives ground-truth for precision/recall (+decoys for precision); real gives cost/realism. (User, 2026-06-09.)
- **Benchmark gate is mandatory** — every downstream ticket needs было/стало; AUDIT-0 is the foundation, built first and deliberately (a flaky corpus poisons every later delta).
- **#7 (enforcement) and #8 (circularity) were DROPPED** as tickets — they don't fit numeric было/стало. Keep as documented known-limitations, not work items.
- **K≥5 runs + mean±stdev** — the audit is non-deterministic (LLM judges); a single before/after run is not a benchmark.

## Open questions
1. **(BLOCKING for #3/#4 metrics) How does the harness drive the Opus judge panel?** `get_second_opinion()` is a plain Python coroutine → directly callable from `bench/run.py`. But the 5 Opus dimension judges run as **Claude Code `Agent` subagent spawns**, which a standalone Python script **cannot** invoke. So the harness can cleanly benchmark `second_opinion`-centric metrics (AUDIT-2 recall, AUDIT-1 precision, AUDIT-6 parse) but NOT panel-involving ones (AUDIT-3 quality-guard, AUDIT-4 council-delta) the same way. Decide for AUDIT-0: scope the harness to the scriptable `cross_model` target first, and design the panel/council targets separately (options: headless Claude Code driver, or approximate the panel with direct model API calls, or semi-manual capture). Flag back if this reshapes AUDIT-3/AUDIT-4.
2. **(non-blocking) Token capture.** codex `--json` event stream may not expose token counts; `get_second_opinion` doesn't return them. If per-call tokens aren't programmatically available, fall back to **wall-time + reviewer-count** as the cost proxy and note tokens as "not captured".

## Risks / pitfalls
- **P0 — AUDIT-0 is the load-bearing risk of the whole plan.** A non-representative or over-fitted corpus makes every downstream "было/стало" meaningless. Invest in corpus realism (base seeded diffs on real owlex code shapes), variance (K runs), and decoys. Don't rush it.
- **P0 — the Opus panel is not script-callable** (see Open Q1). Scope AUDIT-0 to the `cross_model`/`get_second_opinion` target, which IS callable, before promising panel-level benchmarks.
- **P1 — Plane deploy is blocked (HTTP 403).** `PLANE_API_KEY` (workspace `chapta`, `api.plane.so`) is loaded but rejected → expired/revoked or no `chapta` access. Fix: refresh the key in `~/.claude/settings.json` → restart session (MCP reads env at spawn). Until then, track AUDIT-0 against the plan doc; backfill the Plane ticket ID once auth is back. Do NOT call `mcp__plane__*` directly — go through the `plane-tasker` subagent.
- **P2 — `second_opinion` calls real codex** (~15-40s at reasoning=high, +~14k tokens from codex auto-loading `~/.claude/skills`). K=5 × corpus-size × variants is real wall-time/cost — budget for it, or add a `--runs 1 --dry` smoke mode.
- **P2 — the live owlex-server runs pre-logging code** (respawned before the logging edit; `pkill -9` is hook-blocked). The harness calling `get_second_opinion()` in-process is unaffected (it imports the current code), but if it goes through the MCP tool, the running server is stale until a restart.

## Files touched (this session — all UNCOMMITTED)
- NEW: `docs/plans/owlex-audit-hardening.md` (the plan), `docs/handovers/audit-0-benchmark-harness.md` (this doc), `docs/design/` (earlier design doc).
- NEW (feature, green): `owlex/second_opinion.py`, `owlex/server/_second_opinion.py`, `tests/test_second_opinion.py`, `tests/test_deliberation_prompt.py`.
- MODIFIED (mine): `owlex/prompts.py`, `owlex/roles.py`, `owlex/server/__init__.py`, `owlex/agreement.py`, `CLAUDE.md`.
- MODIFIED (pre-existing, NOT mine — from before this work): `owlex/agents/gemini.py`, `owlex/council.py`, `tests/test_agreement_probe.py`, `tests/test_sessions.py`.
- EXTERNAL (not in repo): `~/.claude/skills/solution-audit/SKILL.md`.

## How to verify (AUDIT-0)
- Harness self-test: `python -m pytest bench/ -q` (scorer precision/recall on a known fixture) green.
- `python bench/run.py --corpus seeded --target cross_model --runs 5` → reproducible JSON report with precision/recall + cost.
- Existing suite stays green: `python -m pytest tests/ -q` → 303 passed (AUDIT-0 adds files, shouldn't touch existing tests).

## Context for the next agent (non-obvious)
- **The cross-model reviewer is `owlex.second_opinion.get_second_opinion(prompt, working_directory, timeout) -> (ok, text, timed_out)`** — a plain async coroutine, directly importable in `bench/run.py`. This is your scriptable benchmark entry point.
- **It currently gets a PROSE SUMMARY of the diff** (the orchestrator's words), not the raw diff — that's exactly what AUDIT-2 fixes. For AUDIT-0's baseline you'll want to capture BOTH input variants (prose vs raw-diff) so AUDIT-2's before/after is ready.
- **codex `--json` output** = JSONL; `second_opinion._extract_final_message` pulls `agent_message` items. The harness scores against the returned `opinion` text (parse file:line citations out of it).
- **The audit is run by Claude (the orchestrator), not a script** — that's the core tension in Open Q1. The Opus judges are `Agent` spawns. Only the `second_opinion` leg is a Python API.
- **env knobs:** `OWLEX_SECOND_OPINION_MODEL/REASONING/TIMEOUT` (default `gpt-5.5`/`high`/`120`).

## Branch state
- Branch: **`main`** (protected per memory — PRs via `gh pr create -R rsboarder/owlex`). Base: `main`.
- **Working tree is dirty**: the `second_opinion` feature + fixes + plan/design/handover docs are uncommitted (see Files touched), alongside 4 pre-existing dirty files that are NOT this work. Nothing committed/pushed/PR'd. Decide how to isolate AUDIT-0 (a `bench/`-only branch is clean since `bench/` is all-new) vs the entangled feature work.
- Plane: AUDIT-0 ticket NOT yet created (403). Backfill once auth restored.

## Resume command
> `read that handover docs/handovers/audit-0-benchmark-harness.md, analyze it, challenge if needed, then start execution — build the AUDIT-0 bench harness; scope to the scriptable cross_model target first per Open Q1; run python -m pytest after each edit`
