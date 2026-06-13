---
id: TASK-12
title: AUDIT-10d — Equivalent-mutant precision set (true hallucination instrument)
status: To Do
assignee: []
created_date: '2026-06-10 18:52'
labels:
  - audit-hardening
dependencies:
  - TASK-11
priority: medium
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
AUDIT-10c proved that refactor-commit decoys do NOT measure auditor hallucination/precision — they surface real concerns in real code that get miscredited as false positives. The plan's intended precision instrument was EQUIVALENT MUTANTS: behavior-preserving AST transforms of known-good owlex modules (guaranteed no behavior change → ANY finding is a genuine false positive/hallucination). Implement: extend bench/mutate.py with behavior-preserving operators (e.g. rename a local var consistently, reorder independent statements, add redundant parens, replace a literal with an equal expression, swap x==None style only where semantically identical) tagged as decoys (bugs:[] + decoys describing the no-op change). Then re-run a decoy precision probe on equivalent mutants to get the TRUE hallucination rate. Also worth: a larger real-fix run (it's the hard stratum at 0.60 recall) to tighten that CI.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 ≥10 equivalent-mutant decoys created and integrated into bench/mutate.py
- [ ] #2 A codex probe run on equivalent-mutant decoys reports a clean false-positive rate distinct from the refactor-decoy artifact
- [ ] #3 Finding written up (precision instrument analysis, true hallucination rate, comparison to refactor-decoy results)
- [ ] #4 Larger real-fix run conducted to tighten the 0.60 recall CI on owlex-history bugs
<!-- AC:END -->
