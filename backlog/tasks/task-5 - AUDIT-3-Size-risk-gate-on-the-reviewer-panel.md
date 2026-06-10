---
id: TASK-5
title: 'AUDIT-3: Size/risk gate on the reviewer panel'
status: To Do
assignee: []
created_date: '2026-06-10 15:35'
labels:
  - audit-hardening
dependencies:
  - TASK-1
priority: medium
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
**Problem:** the full 6-reviewer panel (5 Opus + 1 cross-model) runs regardless of diff size — it ran on a ~40-line delta. The audit process is itself over-engineered for small/low-risk changes (it would fail its own "over-engineered" dimension).

**Change:** SKILL.md Phase 0/1 — add a size/risk gate that scales the reviewer set: under N changed lines (and no high-risk paths) run a reduced set (static + cross-model + 1 combined judge); full panel for large or high-risk diffs. Define thresholds.

**NOTE:** blocked on "Opus panel not script-callable" (plan Open Q1).

**Refs:** docs/plans/owlex-audit-hardening.md
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 a documented gate in SKILL.md selects the reviewer set from diff size/risk
- [ ] #2 Benchmark success: material cost reduction on small diffs with detection-rate unchanged (overlapping stdev) on the labeled small set
<!-- AC:END -->
