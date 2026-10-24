"""Name matching for watchlist screening. Standard library only."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_TITLES = {"mr", "mrs", "ms", "dr", "sir", "llc", "ltd", "inc", "co"}


def normalize(name: str) -> str:
    tokens = re.sub(r"[^a-z\s]", " ", name.lower()).split()
    return " ".join(t for t in tokens if t not in _TITLES)


def name_score(a: str, b: str) -> float:
    """Similarity in [0, 1]: the better of direct and token-sorted comparison."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    direct = SequenceMatcher(None, na, nb).ratio()
    sa, sb = " ".join(sorted(na.split())), " ".join(sorted(nb.split()))
    return max(direct, SequenceMatcher(None, sa, sb).ratio())
