---
id: TASK-10
title: >-
  AUDIT-10b — Fix corpus construction bugs (pre-fix polarity + mutant
  runnability)
status: Done
assignee: []
created_date: '2026-06-10 18:06'
updated_date: '2026-06-10 18:34'
labels:
  - audit-hardening
dependencies:
  - TASK-9
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Two construction bugs surfaced by AUDIT-10's live probe (docs/handovers/audit-10-corpus-scale.md 'TWO CONSTRUCTION BUGS'):

(1) POLARITY — bench/mine_fixes.py and bench/ingest_bugsinpy.py present the FIX-commit diff (already-corrected code) to the reviewer instead of the PRE-FIX buggy code, so real-fix recall reads 0.00 as an artifact. Fix: materialize the reverse-patched pre-image as the review target and re-anchor each bug label to the removed (buggy) lines.

(2) MUTANT RUNNABILITY — bench/corpus.py load_mutants does not inline the materialized post_image (bench/check_runnable.py shows mutant has_diff=0 has_post=0), so all 22 mutants are excluded from the raw_diff live path. Fix: inline post_image in load_mutants OR have mutate.py emit a unit diff (original→mutated).

After both: re-run `python bench/run.py --corpus stratified --sources seeded,mined,mutants,dataset --per-source 2 --runs 3 --concurrency 5` and record trustworthy per-source numbers.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 a real-fix/dataset item shows buggy code; a manual run finds the seeded-equivalent bug; bounded codex re-probe shows non-zero real-fix recall
- [ ] #2 load_corpus() mutant items are runnable; stratified live probe includes mutants
- [ ] #3 pytest bench/ green throughout
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
FIXED both: (1) added reverse_unified_diff to bench/corpus.py; mine_fixes.py + ingest_bugsinpy.py now present BUGGY pre-fix code as added lines, labels re-anchored. (2) mutate.py emits a unit diff per mutant → has_diff=22, runnable in raw_diff. bench 181 tests + tests/ 303 green. Re-probe (bench/reports/stalo_probe_v2.json, 30 codex calls, K=3) validated the fix: dataset precision/recall 0.22/0.33→0.92/1.00, real-fix 0.00/0.00→0.75/1.00, mutant 1.00/1.00, seeded 0.94/1.00. Conclusion: the bad v1 numbers were the polarity artifact, NOT auditor weakness; recall≈1.00 across all labeled sources. Surviving real signal: decoy precision 0.00 (auditor over-flags benign refactors).
<!-- SECTION:NOTES:END -->
