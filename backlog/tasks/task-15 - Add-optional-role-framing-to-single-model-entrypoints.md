---
id: TASK-15
title: Add optional role framing to single-model entrypoints
status: Done
assignee: []
created_date: '2026-06-13 14:50'
updated_date: '2026-06-13 15:15'
labels:
  - enhancement
  - roles
  - council
dependencies: []
priority: medium
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Role/team framing currently reaches only council_ask. The single-model MCP tools — second_opinion, start_gemini_session, start_codex_session, start_aichat_session and the other start_*_session tools — accept only prompt (+ working_directory, and enable_search for codex). So there is no way to focus a single non-Claude model on a task with a non-default framing without manually baking it into the prompt text. This is the one genuine gap blocking use of owlex for focused single-model test-spec generation.

Add an optional role parameter (a role id) to second_opinion and every start_*_session tool. When provided, resolve it against the merged builtin + ~/.owlex/roles.json registry (get_merged_roles_and_teams / RoleResolver) and prepend the resolved role's round_1_prefix to the prompt via the existing inject_role_prefix path. When omitted, behavior is byte-identical to today (backward compatible). Decide and document whether COUNCIL_SYSTEM_INSTRUCTION's read-only framing should apply to a single-model *generate* call (it likely should NOT for a generation task — document the chosen behavior).

role="edge-case-adversary" (from Task 1) is the natural first consumer.

Code anchors: the MCP tool registration layer (owlex/server/ — wherever second_opinion / start_*_session are defined); owlex/roles.py (RoleResolver, get_merged_roles_and_teams); owlex/prompts.py (inject_role_prefix); owlex/second_opinion.py.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 second_opinion and each start_*_session tool accept an optional role param defaulting to None; with None the prompt sent is byte-identical to current behavior.
- [x] #2 With role set, the resolved round_1_prefix is prepended to the prompt.
- [x] #3 Unknown role id → clear error surfaced to the caller (not silently ignored).
- [x] #4 Resolution uses the same merged builtin + user-config registry as council.
- [x] #5 Tests cover: role omitted → prompt unchanged; role set → prefix present; unknown role → error. Assert behavior/structure, not exact wording.
- [x] #6 MCP tool docstrings updated to mention the role param and its semantics.
- [x] #7 Documented decision on whether read-only system framing applies to single-model generate calls.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented on branch feat/test-spec-role; 318 tests pass. test-spec maps all 6 seats (superset of DEFAULT_AGENT_ORDER).
<!-- SECTION:NOTES:END -->
