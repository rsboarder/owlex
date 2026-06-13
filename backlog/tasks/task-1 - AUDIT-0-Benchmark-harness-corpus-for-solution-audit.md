---
id: TASK-1
title: 'AUDIT-0: Benchmark harness + corpus for solution-audit'
status: Done
assignee: []
created_date: '2026-06-10 15:34'
updated_date: '2026-06-10 15:34'
labels:
  - audit-hardening
dependencies: []
priority: high
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
**Problem:** solution-audit improvements need BEFORE/AFTER benchmarks, but the audit is non-deterministic (LLM judges) and there is no fixed corpus or measurement infra. Detection-quality items (#1/#2/#4/#6) are unmeasurable without ground-truth-labeled diffs.

**Change:** build a harness under `bench/`:
- **Corpus (a) seeded** — synthetic diffs with known injected bugs, each labeled `{bug_type, file, line, description}` in `bench/corpus/seeded/manifest.json`; include **decoys** (plausible-but-not-a-bug) to measure precision.
- **Corpus (b) real** — a handful of real owlex git-history diffs (`git show <sha>`), unlabeled, for cost/realism.
- **Runner** — executes a given audit sub-step under test (cross-model `second_opinion` call / a single Opus judge / the full panel) on each corpus item, K times (default K=5) for variance; records per run: findings (file:line + text), tokens (where exposed), wall-time, reviewer count.
- **Scorer** — vs seeded ground-truth → precision / recall / detection-rate per item + aggregate (mean ± stdev over K); cost metrics for all items.
- **Baselines** — capture the "было" snapshot for every downstream metric to `bench/baselines/*.json` (committed).

**Refs:** docs/plans/owlex-audit-hardening.md; docs/handovers/audit-0-benchmark-harness.md; commit f1deb82
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 python bench/run.py --corpus seeded --target cross_model --runs 5 emits a reproducible JSON report (precision/recall + cost)
- [ ] #2 seeded manifest has ≥10 labeled bugs across ≥3 types + ≥2 decoys
- [ ] #3 scorer unit tests (precision/recall on a hand-checked fixture) green
- [ ] #4 README documents adding a corpus item + running a before/after comparison
- [ ] #5 Benchmark success: scorer returns correct precision/recall on a fixture with known TP/FP/FN (this IS the measurement infra; N/A для стало)
<!-- AC:END -->
