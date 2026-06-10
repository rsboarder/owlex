"""derive_holdout — deterministic, stratified, un-cherry-pickable held-out split."""
from __future__ import annotations

from bench.scorer import derive_holdout


def _items():
    out = []
    for src in ("seeded", "mutant", "real-fix"):
        for i in range(10):
            out.append({"id": f"{src}-{i}", "source": src})
    return out


def test_deterministic():
    items = _items()
    assert derive_holdout(items) == derive_holdout(items)


def test_stratified_proportion():
    # 10 per source at 0.2 → 2 holdout per source = 6 total
    res = derive_holdout(_items(), holdout_frac=0.2)
    held = [iid for iid, v in res.items() if v == "holdout"]
    assert len(held) == 6
    for src in ("seeded", "mutant", "real-fix"):
        assert sum(1 for h in held if h.startswith(src)) == 2


def test_membership_follows_id_not_position():
    # Reordering the input list must not change any item's assignment.
    items = _items()
    a = derive_holdout(items)
    b = derive_holdout(list(reversed(items)))
    assert a == b
