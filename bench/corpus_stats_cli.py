"""Quick corpus-stats dump.  Run from the repo root:

    python bench/corpus_stats_cli.py
"""
from __future__ import annotations

import json
import os
import sys

# Support both `python bench/corpus_stats_cli.py` and `python -m bench.corpus_stats_cli`
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from bench.corpus import load_corpus  # noqa: E402
from bench.scorer import corpus_stats  # noqa: E402

stats = corpus_stats(load_corpus())
print(json.dumps(stats, indent=2, ensure_ascii=False))
