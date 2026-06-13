---
id: TASK-3
title: 'AUDIT-1: Verify cross-model findings in Phase 2'
status: Done
assignee: []
created_date: '2026-06-10 15:34'
updated_date: '2026-06-10 15:35'
labels:
  - audit-hardening
dependencies:
  - TASK-1
priority: high
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
**Problem:** the cross-model reviewer surfaced the TOP finding in both audit runs (subprocess leak; wd-path logging) that the 5 Opus judges missed — yet its output is labeled "lower-trust, citations NOT Phase-2-verified" and Phase 2 only citation-checks the Opus judges. The most valuable reviewer is the least verified; a cross-model hallucination passes unchecked.

**Change:** extend SKILL.md Phase 2 to run the same mechanical citation-check on the cross-model's cited findings (open each file:line, confirm the cited code matches, drop non-resolving). Update Phase 3 so the cross-model block presents *verified* findings (and can promote passing ones above "lower-trust").

**Refs:** docs/plans/owlex-audit-hardening.md; docs/handovers/audit-1-verify-cross-model-findings.md; commit 1798d69
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Phase 2 iterates cross-model findings with the same drop-on-non-resolve rule
- [ ] #2 SKILL.md updated (Phase 2 + Phase 3)
- [ ] #3 Benchmark success: verified-set precision ≥ raw-set precision; ≥1 planted decoy/hallucination dropped on a bait corpus item
<!-- AC:END -->
