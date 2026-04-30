"""Parse Cursor agent session storage.

cursor-agent persists per-thread SQLite at
``~/.cursor/chats/<workspace_hash>/<thread_uuid>/store.db`` with two tables:
``meta(key, value)`` and ``blobs(id, data BLOB)``. Apart from the first
system-prompt blob (plain JSON), the payloads are protobuf-encoded with the
schema only available in minified JS — there is no public .proto. Tool-call
extraction from disk would require reverse-engineering and would be fragile
across cursor versions.

The supported way to capture tool calls is at runtime: invoke
``cursor-agent --output-format stream-json`` and tee stdout. owlex doesn't do
that today, so this parser intentionally returns [].
"""
from __future__ import annotations


def parse(task_id: str, ts: str, session_id: str | None = None) -> list[dict]:
    return []
