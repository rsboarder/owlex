---
id: TASK-7
title: 'AUDIT-5: Split the two masters (FRAME + reasoning per use)'
status: To Do
assignee: []
created_date: '2026-06-10 15:35'
labels:
  - audit-hardening
dependencies:
  - TASK-1
priority: medium
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
**Problem:** `second_opinion` serves both a generic quick gut-check AND the structured audit reviewer through one hardcoded `FRAME` ("independent second opinion… be concise…") + one reasoning/timeout default (high/120). Result: double-persona framing when the audit prompt also says "independent non-Claude reviewer," and the generic use overpays (high/120) for a quick check.

**Change:** let the caller supply the frame (or make FRAME minimal — drop the redundant persona, keep only output-shape hints), and right-size reasoning/timeout per use: generic default lower (e.g. medium/60), audit passes high explicitly. Decouple the two uses.

**NOTE:** touches the same FRAME/prompt path as AUDIT-2/AUDIT-6 — sequence after them.

**Refs:** docs/plans/owlex-audit-hardening.md
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 FRAME no longer hardcodes a persona the audit prompt duplicates
- [ ] #2 reasoning/timeout caller-controllable with use-appropriate defaults
- [ ] #3 tests updated
- [ ] #4 Benchmark success: generic-use latency/tokens drop materially; audit detection-rate unchanged
<!-- AC:END -->
