"""Council seat configuration, labeling, and quorum policy."""
import os


DEFAULT_MODEL = "gpt-5.5"
DEFAULT_REASONING = "high"
DEFAULT_TIMEOUT = 120

SEATS = ["codex", "gemini", "cursor", "claudeor", "opencode", "aichat"]


def _env(name: str, fallback: str) -> str:
    raw = os.getenv(name)
    return raw if raw is not None else fallback


def seat_model(seat: str) -> str:
    return _env(f"OWLEX_{seat.upper()}_MODEL", DEFAULT_MODEL)


def seat_reasoning(seat: str) -> str:
    return _env(f"OWLEX_{seat.upper()}_REASONING", DEFAULT_REASONING)


def enabled_seats(exclude: str) -> list[str]:
    skip = {s.strip() for s in exclude.split(",") if s.strip()}
    return [s for s in SEATS if s not in skip]


def assign_labels(seats: list[str]) -> dict[str, str]:
    labels = {}
    for i, seat in enumerate(seats):
        labels[seat] = chr(ord("A") + i)
    return labels


def retry_delays(attempts: int, base: float) -> list[float]:
    delays = []
    for k in range(attempts):
        delays.append(base * (2 ** k))
    return delays


def pick_timeout(seat: str) -> int:
    raw = _env(f"OWLEX_{seat.upper()}_TIMEOUT", str(DEFAULT_TIMEOUT))
    return int(raw)


def quorum(n_seats: int) -> int:
    return n_seats // 2


def is_majority(votes: int, n_seats: int) -> bool:
    return votes >= quorum(n_seats)
