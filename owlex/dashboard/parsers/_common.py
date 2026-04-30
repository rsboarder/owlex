"""Shared helpers for session-file parsers."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path


def find_recent_file(roots: list[Path], target_ts: str, glob: str, window_minutes: int = 10) -> Path | None:
    """Find the file under any root matching glob whose mtime is closest to target_ts."""
    try:
        target = datetime.fromisoformat(target_ts)
    except (ValueError, TypeError):
        return None
    target_epoch = target.timestamp()
    window = window_minutes * 60
    best: tuple[float, Path] | None = None
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob(glob):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            delta = abs(mtime - target_epoch)
            if delta > window:
                continue
            if best is None or delta < best[0]:
                best = (delta, p)
    return best[1] if best else None


def truncate(value, limit: int = 120) -> str:
    s = str(value)
    if len(s) <= limit:
        return s
    return s[:limit] + "…"
