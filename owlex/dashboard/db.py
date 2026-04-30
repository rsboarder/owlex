"""Read-only handle on the canonical owlex store.

The dashboard never writes call data; that comes from ``owlex/store.py`` (which
is also responsible for one-shot import of legacy ``timing.jsonl``). This module
exists only to (a) open a connection and (b) host the small skill-invocation
write API used by the dashboard's parser pipeline.
"""
from __future__ import annotations

import sqlite3

from .. import store


def connect() -> sqlite3.Connection:
    return store.connect()
