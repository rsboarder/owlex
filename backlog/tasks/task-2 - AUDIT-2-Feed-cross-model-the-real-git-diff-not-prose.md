---
id: TASK-2
title: 'AUDIT-2: Feed cross-model the real git diff (not prose)'
status: Done
assignee: []
created_date: '2026-06-10 15:34'
updated_date: '2026-06-10 15:34'
labels:
  - audit-hardening
dependencies:
  - TASK-1
priority: high
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
**Problem:** the Phase-1 cross-model reviewer is sold as "independent, diff-anchored, blind," but the orchestrator hands `second_opinion` a PROSE SUMMARY of the changes (its own editorialized description) + repo read access — reintroducing the orchestrator's framing/bias, the exact thing model-diversity should remove. Evidence: both 2026-06 audit runs led with a hand-written "1. REAP… 2. RETURN SIGNATURE…" summary, not raw hunks.

**Change:** SKILL.md Phase 1 — pass the actual `git diff <base>..HEAD` (or staged+unstaged) text to `second_opinion`, OR instruct codex to run `git diff` itself (read-only sandbox allows it) with `working_directory`=repo root. Remove the prose-summary step from the cross-model path; keep the 5-dimension lens as the only framing.

**NOTE:** shipped as a COST win (~2.1× faster); recall-parity not a recall win.

**Refs:** docs/plans/owlex-audit-hardening.md; docs/handovers/audit-1-verify-cross-model-findings.md; commit f1deb82
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 the cross-model prompt contains raw diff hunks (or an explicit "run git diff yourself" instruction), no editorialized prose
- [ ] #2 SKILL.md Phase-1 block updated
- [ ] #3 a manual run shows codex receiving the real diff
- [ ] #4 Benchmark success: raw-diff recall ≥ prose-summary recall (hypothesis: strictly higher); no recall regression on any bug type
<!-- AC:END -->
