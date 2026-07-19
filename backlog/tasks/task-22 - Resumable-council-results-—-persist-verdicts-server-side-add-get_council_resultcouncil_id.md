---
id: TASK-22
title: >-
  Resumable council results — persist verdicts server-side, add
  get_council_result(council_id)
status: To Do
assignee: []
created_date: '2026-07-19 12:38'
labels:
  - feature
  - reliability
dependencies: []
ordinal: 22000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The owlex MCP connection drops repeatedly during long-running `council_ask` calls (observed ≥4 disconnects in one Claude Code session, 2026-07-18/19, bookmatcher TASK-254 work). When the MCP connection dies mid-council, the entire 5-model verdict is LOST — ~10 minutes of multi-model work gone, non-recoverable, because results only travel over the live MCP response. The client-side workaround (420s timeout in .mcp.json) bounds the hang but does not preserve results.

Fix: make council runs durable server-side.
1. Persist each council run's state + per-agent responses + final verdict on disk, keyed by `council_id`, as the run progresses (not only at completion).
2. Add an MCP tool `get_council_result(council_id)` that returns the current state: `running | partial (N/M agents done) | complete`, with whatever responses exist so far.
3. On MCP reconnect, a client can re-fetch by `council_id` instead of re-running the whole council. `council_ask` should return the `council_id` EARLY (first response chunk) so the client has the key even if the connection dies later.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A council whose MCP connection dropped mid-run can be fully recovered via `get_council_result(council_id)` after reconnect — no re-run
- [ ] #2 `council_id` is available to the client before the first agent completes
- [ ] #3 Partial results (some agents done, some pending/failed) are retrievable with per-agent status
- [ ] #4 Existing `council_ask` behavior unchanged for the happy path
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Source — Claude Code harness retro of session d2fae700 (2026-07-19): one full council verdict lost to a mid-run `Connection closed`; user had to /mcp reconnect twice in one session.
<!-- SECTION:NOTES:END -->
