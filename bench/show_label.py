"""Show a corpus item's bug label + the diff around it — to judge label quality."""
from __future__ import annotations

import sys

from bench.corpus import load_corpus

ids = set(sys.argv[1:])
by_id = {it["id"]: it for it in load_corpus()}
for iid in sys.argv[1:]:
    it = by_id.get(iid)
    if not it:
        print(f"{iid}: NOT FOUND")
        continue
    bug = (it.get("bugs") or [{}])[0]
    print(f"## {iid}  label-> {bug.get('file')}:{bug.get('line')}  type={bug.get('bug_type')}")
    print(f"   desc: {bug.get('description','')[:140]}")
    diff = it.get("diff", "")
    # show the added (buggy) lines only
    added = [ln for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    print(f"   buggy(+) lines ({len(added)}): " + " | ".join(a[1:].strip()[:50] for a in added[:6]))
    print()
