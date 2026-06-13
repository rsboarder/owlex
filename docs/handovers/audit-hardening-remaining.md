# Owlex Audit Hardening — Remaining Tasks (program handover, 2026-06-10)

Program-level handover covering **all remaining** audit-hardening tickets. Per-ticket full specs live in the plan; a **dedicated per-ticket handover is written when each ticket starts**. AUDIT-0/1/2 are done (their own handovers: `audit-0-benchmark-harness.md`, `audit-1-verify-cross-model-findings.md`).

**Canonical spec (read first):** [`docs/plans/owlex-audit-hardening.md`](../plans/owlex-audit-hardening.md) — every ticket's Problem/Change/Acceptance/Benchmark + the AUDIT→TASK mapping.
**Tracker:** local **Backlog.md** (label `audit-hardening`), tasks `TASK-1..TASK-9`. All backlog ops go through the `backlog-tasker` subagent — never edit `backlog/*.md` by hand.

## Goal
Finish the audit-hardening program: implement the 6 remaining tickets, each shipped only with a BEFORE/AFTER benchmark run through the `bench/` harness.

## Status (done)
- **AUDIT-0** (`TASK-1`) ✅ committed `f1deb82` — `bench/` harness (scorer, corpus loader, async runner), 8-item seeded corpus + 3 real diffs, committed baseline, 44 tests.
- **AUDIT-2** (`TASK-2`) ✅ — cross-model reviewer gets the **raw `git diff`**, not the orchestrator's prose. Shipped as a **COST win (~2.1× faster), recall-parity** — NOT a recall win (the "prose loses bugs" premise was an empty-sandbox artifact; do not resurrect). Lives in `~/.claude/skills/solution-audit/SKILL.md` Phase 1 (external file).
- **AUDIT-1** (`TASK-3`) ✅ committed `1798d69` — Phase-2 citation-check of cross-model findings + a "bait" corpus item.
- Tests green: `bench/` 44, `tests/` 303.

## What's left (pick any UNBLOCKED; recommended order below)
Each ticket's full spec is in the plan doc section of the same name. Only the non-obvious delta is here.

1. **AUDIT-10 — Scale & stratify corpus + flywheel** (`TASK-9`, dep AUDIT-0 ✅ → **unblocked**). **Recommended FIRST** — fixes risk **P0 (corpus over-fit)**, which makes every other detection number fragile until done. Sources: read-only DB extractor (the **~89 diff-bearing council prompts** = realistic targets ONLY — DB ratings/agreement are NOT labels, see Decisions), bug-fix mining (esp. `docs/solutions/` + CLAUDE.md Learned Patterns), BugsInPy, mutation (`cosmic-ray`/`mutmut`), decoys from refactor commits, + the persist-flywheel.
2. **AUDIT-6 — Structured cross-model output** (`TASK-4`, dep AUDIT-0 ✅ → **unblocked, scriptable**). Make `second_opinion` emit per-dimension JSON so convergence vs the Opus panel is matched programmatically, not by eye.
3. **AUDIT-9 — Extract `owlex/_codex.py`** (`TASK-8`, **no dep, no corpus needed**). DRY the 2×2 duplication (`_cmd`≈`_build_judge_command`, `_terminate`×2). Pure refactor; benchmark = duplicate-LOC + argv-byte-identical + suite green. Sequence after AUDIT-5 if both touch `_cmd`.
4. **AUDIT-5 — Split FRAME/reasoning** (`TASK-7`, soft dep AUDIT-0 for the quality-guard → **unblocked**). Decouple generic gut-check from the audit reviewer; right-size reasoning/timeout. Sequence after AUDIT-2/AUDIT-6 (same FRAME/prompt path).
5. **AUDIT-3 — Size/risk gate** (`TASK-5`, dep AUDIT-0 ✅ but **⛔ BLOCKED** by Open-Q1). The cost half is scriptable; the **quality-guard needs the Opus panel, which is not script-callable** (see Open Questions).
6. **AUDIT-4 — Council-escalation boundary** (`TASK-6`, dep AUDIT-0 ✅ but **⛔ BLOCKED** by Open-Q1). The detection-delta benchmark needs **council**, not script-callable. Same blocker as AUDIT-3.

**Recommended order:** `AUDIT-10` → `AUDIT-6` → `AUDIT-9` → `AUDIT-5` → (unblock Open-Q1) → `AUDIT-3` → `AUDIT-4`.

## Decisions locked (do NOT re-debate)
- **Harness scope = the scriptable `cross_model` target only.** `get_second_opinion()` is a plain coroutine `bench/run.py` calls directly; the **Opus panel + `council_ask` are NOT script-callable** (Open-Q1).
- **DB is for targets/realism, NOT labels.** `agent_scores` ratings are Claude-blind → circularity (dropped #8); `agreement_score` is contaminated by judge-fallback (332/391 "low" = overlap-heuristic artifact). `calls.input_tokens` are NULL.
- **AUDIT-2 = cost win, not recall** — never re-litigate.
- **Corpus = seeded + real**; **#7/#8 dropped** (documented known-limitations, not tickets).
- **Tracker = Backlog.md** (Plane is dead — 403; owlex routes new tasks to Backlog.md). Backlog ops via `backlog-tasker` only.
- **Methodology:** K≥5 runs, mean±stdev; freeze+version the corpus; held-out split; add items for coverage, never to flip an A/B.

## Open questions / blockers
1. **(BLOCKS AUDIT-3 & AUDIT-4) The Opus panel + `council_ask` are not callable from a Python script.** `bench/run.py` can drive `get_second_opinion` but not the 5 `Agent`-spawn judges nor council. Needs a headless-Claude driver or a model-API approximation before #3/#4 benchmarks are real. A `TARGETS` registry seam exists in the harness for this.
2. **(non-blocking) codex rate-limit ceiling (~60-80 calls).** Full re-baselines hit it. Prefer targeted probes (1 item × K); run live baselines **foreground at `--concurrency 5`** (~5-7 min) — background tasks get reaped over long idle.
3. **(cosmetic) Plane 403** — irrelevant now (moved to Backlog). Ignore unless Plane mirroring is wanted.

## Risks / pitfalls
- **P0** — corpus over-fit (the 8+3 set); AUDIT-10 exists to fix this. Until then treat all detection numbers as directional.
- **P1** — codex usage limit; background-task reaping over idle → run foreground.
- **P2** — damage-control hook blocks Bash containing `.keys`/`.dump`/`rm -f`/`rm -rf` substrings (e.g. `dict.keys()`, `json.dumps` in inline `python -c`) — put such code in a file.
- **P2** — don't p-hack the corpus (don't tune a seed item until raw "beats" prose).
- The `second_opinion` **feature itself is still uncommitted** (intentional; bench commits excluded it).

## Where things are
- **Harness (committed):** `bench/run.py`, `bench/scorer.py`, `bench/corpus.py`, `bench/corpus/{seeded,real}/`, `bench/baselines/cross_model.json`, `bench/tests/`, `bench/README.md`.
- **Cross-model entry point (the scriptable benchmark target):** `owlex.second_opinion.get_second_opinion(prompt, working_directory, timeout) -> (ok, text, timed_out)` — imported lazily in `run._acall_cross_model`, so `pytest bench/` does NOT need the uncommitted feature file.
- **The skill under improvement (external, NOT in repo):** `~/.claude/skills/solution-audit/SKILL.md` — AUDIT-1/2 edits live here; AUDIT-3/4/6 will also edit it (so they can't be "committed" to owlex — note in each PR).
- **DB (read-only corpus source):** `~/.owlex/owlex.db` — `calls.prompt_text/result_text` (~89 diff-bearing councils), `agent_scores`, `council_outcomes`.
- **env:** `OWLEX_SECOND_OPINION_MODEL/REASONING/TIMEOUT` (gpt-5.5/high/120).

## How to verify (current green state)
- `python -m pytest bench/ -q` → 44 passed.
- `python -m pytest tests/ -q` → 303 passed.
- `python bench/run.py --corpus seeded --runs 1 --dry` → JSON report, no codex.
- Live (costs codex): `python bench/run.py --corpus seeded --runs 5 --input-variant both --concurrency 5 --baseline` (~7 min, ~80 calls).

## Branch state
- Branch: **`audit-0-bench-harness`** (off `main`; main protected — PRs via `gh pr create -R rsboarder/owlex`). Commits `f1deb82`, `1798d69`, `50804fe` present, **NOT pushed, no PR**.
- **Uncommitted:** `backlog/tasks/` (the 9 Backlog tasks), `docs/plans/owlex-audit-hardening.md` (AUDIT-10 added + mapping), `docs/handovers/*` (this doc + the audit-1 doc + audit-0 edits), the `second_opinion` feature files, `docs/design/`, and 4 pre-existing unrelated dirty files (`owlex/agents/gemini.py`, `owlex/council.py`, `tests/test_agreement_probe.py`, `tests/test_sessions.py`). Decide isolation before committing.

## Resume command
> `read that handover docs/handovers/audit-hardening-remaining.md, analyze it, challenge if needed, then start execution — pick the next unblocked ticket (recommend AUDIT-10 / TASK-9 first), write its per-ticket handover, set the Backlog task In Progress via backlog-tasker, implement with a было/стало benchmark, run python -m pytest bench/ after each edit`
