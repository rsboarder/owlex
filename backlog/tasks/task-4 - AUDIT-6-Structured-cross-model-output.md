---
id: TASK-4
title: 'AUDIT-6: Structured cross-model output'
status: To Do
assignee: []
created_date: '2026-06-10 15:35'
labels:
  - audit-hardening
dependencies:
  - TASK-1
priority: medium
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
**Problem:** `second_opinion` returns free prose (`{"opinion": <text>}`); the orchestrator eyeball-parses it into per-dimension verdicts + convergence vs the Opus judges. Manual, error-prone, non-reproducible.

**Change:** have the cross-model emit a fixed per-dimension structure (JSON: `[{dimension, verdict, findings:[{file,line,issue}]}]`) — via a prompt contract in SKILL.md or a structured-output mode on the tool. Orchestrator matches convergence programmatically.

**Refs:** docs/plans/owlex-audit-hardening.md (dedicated handover TBD when started)
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 cross-model output reliably parseable per-dimension
- [ ] #2 SKILL.md (and/or tool) updated
- [ ] #3 orchestrator does a programmatic convergence match
- [ ] #4 Benchmark success: structured parse-error rate ≈ 0; convergence-match accuracy ≥ manual baseline
<!-- AC:END -->
