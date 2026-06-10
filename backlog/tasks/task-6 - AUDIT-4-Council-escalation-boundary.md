---
id: TASK-6
title: 'AUDIT-4: Council-escalation boundary'
status: To Do
assignee: []
created_date: '2026-06-10 15:35'
labels:
  - audit-hardening
dependencies:
  - TASK-1
priority: medium
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
**Problem:** a single `second_opinion` call (n=1, gpt-5.5) isn't real "model diversity" — one extra sample, no variance estimate. `council_ask` (6 heterogeneous models, anonymized R1/R2 cross-critique, blind rating) is the actual diversity engine. The Phase-1↔Phase-4 boundary is fuzzy ("suggest council if ≥2 ⚠").

**Change:** SKILL.md — define an explicit escalation rule for WHEN the audit routes the cross-model step to `council_ask` instead of/in addition to `second_opinion`: e.g., diff touches high-risk domains (auth, subprocess, data) OR ≥ N changed lines OR ≥2 ⚠/❌ dimensions. Tighten Phase 4 from "suggest" to a rule.

**NOTE:** blocked on "council not script-callable".

**Refs:** docs/plans/owlex-audit-hardening.md
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 explicit, testable escalation predicate in SKILL.md
- [ ] #2 Benchmark success: threshold set where council's recall gain on high-stakes diffs justifies its cost (documented break-even); low-stakes diffs → no escalation
<!-- AC:END -->
