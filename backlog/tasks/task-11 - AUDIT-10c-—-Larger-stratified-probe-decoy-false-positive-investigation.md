---
id: TASK-11
title: AUDIT-10c — Larger stratified probe + decoy false-positive investigation
status: Done
assignee: []
created_date: '2026-06-10 18:34'
updated_date: '2026-06-10 18:52'
labels:
  - audit-hardening
dependencies:
  - TASK-10
ordinal: 11000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Construction is now sound (TASK-10). Two things need a bigger, rate-limited run: (a) tighter CIs — re-run the stratified probe with more items per source (e.g. --per-source 5-8, --runs 5) across seeded,mined,mutants,dataset, foreground --concurrency 5, mindful of codex ceiling ~60-80/run (may need batching). (b) The real weak spot surfaced by the small probe: decoy precision 0.00 — the auditor invents findings on benign refactor/chore diffs. Quantify the false-positive rate on a larger decoy set and characterize what it over-flags (style nits vs hallucinated bugs).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 per-source precision/recall with K=5 + a decoy-FP analysis written to docs/handovers or bench/README
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
K=5 probe (75 codex calls, bench/reports/stalo_probe_v3.json): seeded 0.98/1.00, mutant 1.00/1.00, dataset 0.92/1.00, real-fix 0.58/0.60±0.51, decoy precision 0.00. HONEST REVISION: synthetic/mutant/public bugs caught ~100%; REAL owlex-history bugs are the hard stratum (~0.60 recall, high variance) — that's the true over-fit signal. DECOY FINDING (key, via bench/analyze_decoys.py): the decoy 'false positives' are mostly LEGITIMATE-looking concerns about real refactored code (e.g. subprocess child-reaping, bare except swallowing cancellation), NOT hallucinations; merge-commit decoy got 0 findings. So refactor-commit decoys do NOT measure hallucination — they miscredit real findings on real code as FP. A true precision instrument needs guaranteed-clean equivalent mutants. See docs/handovers/audit-10-corpus-scale.md AUDIT-10c section. New tooling: bench/analyze_decoys.py, bench/summarize_probe.py.
<!-- SECTION:NOTES:END -->
