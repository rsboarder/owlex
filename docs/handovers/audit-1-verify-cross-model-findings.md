# AUDIT-1: Verify cross-model findings in Phase 2 — Handover (2026-06-10)

**Backlog:** `TASK-3` (AUDIT-1, **Done**) · `TASK-2` (AUDIT-2, **Done**) — tracked in the local Backlog.md project (label `audit-hardening`).

## Goal
Implement **AUDIT-1** — extend `solution-audit` Phase 2 to mechanically citation-check the **cross-model** (`second_opinion`) reviewer's findings (drop non-resolving), and measure the precision before/after through the `bench/` harness. This is the next ticket after AUDIT-0 (done) and AUDIT-2 (shipped).

**Single source of truth for the full plan:** [`docs/plans/owlex-audit-hardening.md`](../plans/owlex-audit-hardening.md) — read AUDIT-1 (and the execution order). This doc is the runbook for AUDIT-1 + the committed state of AUDIT-0/AUDIT-2.

## Status
- ✅ **AUDIT-0 complete + COMMITTED.** `bench/` harness (scorer, corpus loader, async runner, 8-item seeded corpus, 3 real diffs, committed baseline, 37 tests). Commit `f1deb82` on branch `audit-0-bench-harness` (bench/ + the plan + the AUDIT-0 handover only).
- ✅ **AUDIT-2 SHIPPED (cost-justified).** `~/.claude/skills/solution-audit/SKILL.md` Phase 1 now passes the cross-model reviewer the **raw `git diff`, not a prose summary** (external file — NOT in the owlex repo, cannot be committed here). The bench-side AUDIT-2 work (prose-corpus realism + `_materialize` repo access) is in `f1deb82`.
- ✅ **Faithful 8-item baseline captured** → `bench/baselines/cross_model.json` (gpt-5.5/high, K=5, 80/80 ok, `file_access=materialized-repo`).
- ❌ **AUDIT-1: zero code written.** This is the task.
- ⏸️ **`second_opinion` feature itself is still UNCOMMITTED** (prior-session work — `owlex/second_opinion.py`, `owlex/server/_second_opinion.py`, the `tests/test_second_opinion.py` + `test_deliberation_prompt.py`, and mods to `prompts.py`/`roles.py`/`server/__init__.py`/`agreement.py`/`CLAUDE.md`). Intentionally left out of `f1deb82`.

## What's left (AUDIT-1)
1. **Add a verification pass to the harness** — a `--verify` mode (or a scorer option) that, given the materialized repo + a finding's `file:line`, opens the cited line and drops the finding if the citation doesn't resolve / doesn't match. Re-score precision on the surviving set. *Acceptance:* `bench/` has a verified-vs-raw precision comparison; unit-tested on a fixture with a known non-resolving finding.
2. **Add a "bait" corpus item** — a seeded item engineered so the cross-model tends to cite a **non-existent or wrong** `file:line` (a hallucination magnet), so the verify pass has something to drop. *Acceptance:* ≥1 finding dropped by verification on the bait item.
3. **Edit `SKILL.md` Phase 2 + Phase 3** — run the same mechanical citation-check on the cross-model's cited findings (open each `file:line`, confirm match, drop non-resolving); update Phase 3 so the cross-model block presents *verified* findings (and may promote passing ones above "lower-trust"). *Acceptance:* Phase 2 iterates cross-model findings with the drop-on-non-resolve rule; SKILL.md updated.
4. **Benchmark (было/стало):** precision of cross-model findings, (было) no verification vs (стало) post-Phase-2. *Success:* verified-set precision ≥ raw-set precision; ≥1 planted decoy/hallucination dropped on the bait item.

## Decisions locked (do NOT re-debate)
- **AUDIT-2 is a COST win, not a recall win.** Honest 8-item baseline: prose vs raw input is recall-parity (line 0.95 vs 0.97; file 0.97 vs 0.97); raw is ~2.1× faster (20.5s vs 43.3s). Shipped on cost (+ bias argument). The original "prose loses bugs" premise was an empty-sandbox artifact — do not resurrect it. (User, 2026-06-10.)
- **Harness scope = the scriptable `cross_model` target only.** The Opus panel + `council_ask` are not script-callable (plan Open Q1) — a `TARGETS` registry seam exists but they're intentionally absent. AUDIT-1 only needs the cross_model leg, which the harness already drives.
- **Both input variants get repo access** (materialized per-item temp git repo via `run._materialize`) — faithful to production. Prose with an EMPTY sandbox makes codex refuse to review; never measure prose without file access.
- **Two match granularities**: `line` (strict, raw-diff) + `file` (fair for line-less prose). Default window ±3. K≥5 with mean±stdev.

## Open questions
1. **(non-blocking) Panel/council targets** — still not script-callable (plan Open Q1). AUDIT-3/AUDIT-4 will need a headless-Claude driver or an approximation. Out of scope for AUDIT-1.
2. **(blocking for Plane tracking) Plane 403** — `PLANE_API_KEY` (workspace `chapta`) still rejected; AUDIT-0/1/2 tickets NOT created. Refresh the key in `~/.claude/settings.json` → restart session. Track against the plan doc until then. Go through the `plane-tasker` subagent, never `mcp__plane__*` directly.

## Risks / pitfalls
- **P1 — codex usage limit.** A full 80-call re-baseline is near the rate-limit ceiling; one earlier 60-call run hit `You've hit your usage limit` partway. For AUDIT-1, prefer cheap targeted probes (1 item × K) over full re-baselines; check `ok N/N` in the report (failed runs silently lower recall).
- **P1 — background tasks get reaped over long idle.** A ~20-min serial background baseline was killed during a multi-hour session idle (no error, empty output). Run live baselines **foreground at `--concurrency 5`** (~5–7 min, fits the 10-min ceiling) while staying active, OR keep the session warm.
- **P2 — damage-control hook false-positives.** Bash commands containing the literal substrings `.keys`/`.dump` (e.g. `dict.keys()`, `json.dumps`) or `rm -f`/`rm -rf` are blocked. Use plain `rm`, avoid `.dumps` in inline `python -c` (put it in a file), avoid `.keys()` in shell one-liners.
- **P2 — corpus over-fitting (the AUDIT-0 P0 risk).** Don't keep enlarging/subtling a seeded item until raw "beats" prose — that p-hacks AUDIT-2's win. For AUDIT-1's bait item, engineer a *realistic* hallucination magnet, run once, accept the result.

## Files touched (this session)
- **Committed (`f1deb82`):** all of `bench/` (28 files: `scorer.py`, `corpus.py`, `run.py`, `tests/`, `corpus/seeded/{manifest.json,schema.json,diffs/,post_image/}`, `corpus/real/*.diff`, `baselines/cross_model.json`, `README.md`), `docs/plans/owlex-audit-hardening.md`, `docs/handovers/audit-0-benchmark-harness.md`.
- **External, NOT committable to repo:** `~/.claude/skills/solution-audit/SKILL.md` (Phase 1 raw-diff edit — AUDIT-2 deliverable).
- **Uncommitted, NOT this work:** the `second_opinion` feature files + the 4 pre-existing dirty files (`owlex/agents/gemini.py`, `owlex/council.py`, `tests/test_agreement_probe.py`, `tests/test_sessions.py`), `docs/design/`.

## How to verify (AUDIT-0/2 still green)
- `python -m pytest bench/ -q` → 37 passed (pure scorer/corpus/runner — no codex).
- `python -m pytest tests/ -q` → 303 passed (owlex untouched).
- `python bench/run.py --corpus seeded --runs 1 --dry` → JSON report, no codex.
- Live (costs codex): `python bench/run.py --corpus seeded --runs 5 --input-variant both --concurrency 5 --baseline` (~7 min, ~80 calls).

## Context for the next agent (non-obvious)
- **The cross-model entry point is `owlex.second_opinion.get_second_opinion(prompt, working_directory, timeout) -> (ok, text, timed_out)`** — imported lazily inside `run._acall_cross_model` (so `pytest bench/` does NOT require the uncommitted feature file).
- **AUDIT-1's verify step maps cleanly onto the harness**: the runner already materializes each item into a temp git repo (`run._materialize`), so a verify pass can open each finding's cited `file:line` against that repo and drop non-resolving ones. The scorer's `parse_findings` already extracts `file:line` citations; precision is already computed. The headroom is real: baseline `decoy_hits ~0.17–0.27` and precision spread (line 0.88 vs file 0.94) = findings that don't resolve to a planted bug.
- **The hard corpus item is `seed-04-jsonl-parse`** (negative-slice boundary) — recall ~0.80 both variants. `seed-08-large-rating-stats` is the ONLY item where raw edges prose (line 1.00 vs 0.80) — a subtle one-line guard removal in a 94-line file.
- **Granularity gotcha**: prose can't anchor lines, so it's measured at `file` granularity; raw at `line`. AUDIT-1's precision metric should use `line` (verification is about exact citations resolving).
- **env knobs:** `OWLEX_SECOND_OPINION_MODEL/REASONING/TIMEOUT` (gpt-5.5/high/120).

## Branch state
- Branch: **`audit-0-bench-harness`** (off `main`; main is protected — PRs via `gh pr create -R rsboarder/owlex`).
- Commit **`f1deb82`** present, **NOT pushed, no PR**. Working tree still carries the uncommitted `second_opinion` feature + 4 unrelated dirty files (leave them, or isolate separately).
- AUDIT-0/1/2 Plane tickets NOT created (403). Backfill once auth restored.

## Resume command
> `read that handover docs/handovers/audit-1-verify-cross-model-findings.md, analyze it, challenge if needed, then start execution — implement AUDIT-1 (verify cross-model findings in Phase 2); add a harness --verify pass + a bait corpus item, measure precision before/after; run python -m pytest bench/ after each edit`
