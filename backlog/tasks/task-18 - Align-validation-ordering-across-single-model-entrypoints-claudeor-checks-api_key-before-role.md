---
id: TASK-18
title: >-
  Align validation ordering across single-model entrypoints (claudeor checks
  api_key before role)
status: Done
assignee: []
created_date: '2026-06-13 15:56'
updated_date: '2026-06-13 16:09'
labels:
  - bug
dependencies: []
priority: low
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Error-validation order is inconsistent across the single-model tools. `start_claudeor_session` checks `config.claudeor.api_key` BEFORE role validation (`owlex/server/_sessions.py`), so an unknown-role call to claudeor with a missing/unset API key returns an API-KEY error instead of the INVALID_ARGS role error that every other tool returns. The other `start_*` tools validate working_directory then role; `second_opinion` validates prompt then role. This surfaced during testing: `tests/test_role_framing.py` had to inject a fake api_key (`_FakeClaudeORConfig`) just to reach claudeor's role path.

Recommended fix: pick one canonical order and apply it to all 7 surfaces — validate cheap request-shape args first (prompt non-empty → role resolves) BEFORE environment/config preconditions (api_key, working_directory existence), so a bad role fails fast and identically everywhere, before any credential/IO check.

Related to TASK-15 (role framing feature whose validation surfaced this inconsistency).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Unknown role returns INVALID_ARGS on all 7 surfaces regardless of whether api_key / working_directory are valid (role validation precedes api_key and working_directory checks; document the chosen canonical order).
- [x] #2 A test asserts unknown-role → INVALID_ARGS for `start_claudeor_session` WITHOUT injecting an api_key (role error wins over missing-key error).
- [x] #3 The validation order is documented in the tool layer.
- [x] #4 Assertions are behavior/structure, not exact wording.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented on branch feat/test-spec-role; full suite 352 passed.
<!-- SECTION:NOTES:END -->
