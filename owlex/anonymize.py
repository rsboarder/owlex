"""Single source of truth for anonymized labeling of agent responses.

Both R2 deliberation prompts and the agreement judge anonymize agent identities
behind letters (Response A, B, C, ...). This module centralizes the labeling
scheme and shuffle strategy so the two call sites can't drift.

Future home: ``owlex/domain/services/anonymizer.py`` once the domain layer
extraction (Refactor #1) lands.
"""
from __future__ import annotations

import random
from typing import Iterable, TypeVar

LABELS: str = "ABCDEFGHIJKLMNOP"
"""Letter labels used when masking agent identity. Up to 16 participants."""


T = TypeVar("T")


def label_for(index: int) -> str:
    """Return the canonical label for a 0-indexed position."""
    if 0 <= index < len(LABELS):
        return LABELS[index]
    return str(index + 1)


def assign_labels(
    items: Iterable[tuple[str, T]],
    *,
    salt: str | None = None,
) -> tuple[dict[str, T], dict[str, str]]:
    """Assign letter labels to ``(key, value)`` pairs.

    Args:
        items: iterable of ``(agent_seat, payload)`` pairs. Iteration order is
            preserved when ``salt is None``; otherwise the order is shuffled
            with a deterministic RNG seeded on ``salt`` (so the same salt yields
            the same mapping across processes — useful for blind-rating where
            R1 and R2 must share a mapping).
        salt: optional deterministic seed. ``None`` → preserve input order.

    Returns:
        ``(by_label, label_to_key)``:
          * ``by_label[letter] -> payload``
          * ``label_to_key[letter] -> agent_seat``
    """
    pairs = list(items)
    if salt is not None:
        rng = random.Random(salt)
        rng.shuffle(pairs)

    by_label: dict[str, T] = {}
    label_to_key: dict[str, str] = {}
    for i, (key, payload) in enumerate(pairs):
        lbl = label_for(i)
        by_label[lbl] = payload
        label_to_key[lbl] = key
    return by_label, label_to_key
