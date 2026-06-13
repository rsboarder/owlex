---
id: TASK-13
title: AUDIT-10e — Fix or demote the owlex-mined real-fix labeling
status: Done
assignee: []
created_date: '2026-06-10 20:06'
updated_date: '2026-06-10 20:16'
labels:
  - audit-hardening
dependencies:
  - TASK-10
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
bench/mine_fixes.py auto-anchors bug labels at the first changed line of the (reversed) fix diff — unreliable for real multi-line/multi-file/docs commits (proven in TASK-11/exp_lens: labels landed on imports, comments, markdown). Options: (a) DROP owlex-mined fix-commits as a LABELED stratum — keep them only as UNLABELED realism targets; rely on seeded + mutants + BugsInPy for labeled precision/recall (those are trustworthy: 0.98/1.00/0.92). (b) Re-curate labels via per-commit LLM/human localization (expensive). RECOMMENDED: (a) — demote to unlabeled, and treat BugsInPy as the real-bug labeled stratum. AC: corpus_stats no longer counts mine-fixed items as objective-labeled unless re-curated; conclusions in docs/handovers/audit-10-corpus-scale.md updated; a clean trustworthy-strata-only benchmark number recorded.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 corpus_stats.py no longer counts owlex-mined fix-commits as objective-labeled
- [ ] #2 docs/handovers/audit-10-corpus-scale.md updated with trustworthy-strata-only conclusions
- [ ] #3 benchmark numbers re-recorded with mine-fixed items demoted to unlabeled realism set
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Demoted: bench/mine_fixes.py fix-commit items now source=owlex-realism with bugs:[] (unlabeled realism targets, kept diff+note); doc items now source=documented-soft. scorer.corpus_stats _OBJECTIVE_SOURCES = {seeded,mutant,dataset} only. Manifest regenerated (15 owlex-realism / 11 documented-soft / 7 decoy). bench 182 tests green. CLEAN trustworthy-strata result (stalo_probe_v3): seeded 0.98/1.00, mutant 1.00/1.00, dataset(BugsInPy) 0.92/1.00 → auditor recall ~1.00, precision ~0.95 on well-labeled real bugs. Earlier 'real owlex bugs 0.33-0.60 recall' was confirmed a label artifact. Handover docs/handovers/audit-10-corpus-scale.md updated with corrected conclusion. Remaining open item: decoy precision instrument (TASK-12, equivalent mutants).
<!-- SECTION:NOTES:END -->
