#!/usr/bin/env python3
"""K2 (t1099) step 3: the recall@k metric over a channel ranking.

The task's PASS bar (hybrid recall@5 >= 0.8, beats keyword-only) is a number computed as:
for each of the 40 ground-truth questions, is the answer turn within the top-k of a channel's
ranking? This suite pins that metric -- recall_at (one question) and recall_score (all 40) --
over hand-written rankings, so it is hermetic: no model, no server, no 39 MB index, no /tmp.

Red on main for ONE reason: scripts/memory.py has no recall_at / recall_score, so every test
below fails with AttributeError. The module imports fine -- the existing channel functions
keyword_rank (:180), vector_rank (:338), rrf (:353, RRF_K=60 at :144) and recall (:365) are all
present; only the recall metric they rank against is missing.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import memory as M   # the file the change goes into (exists on main)


def test_recall_at_is_a_top_k_hit_test():
    ranked = ["a", "b", "c", "d", "e", "f", "g"]   # ranks 1..7
    assert M.recall_at("a", ranked, 5) is True,    "rank 1 must be a top-5 hit"
    assert M.recall_at("e", ranked, 5) is True,    "rank 5 is the boundary: included in top-5"
    assert M.recall_at("f", ranked, 5) is False,   "rank 6 must miss top-5"
    assert M.recall_at("f", ranked, 6) is True,    "rank 6 is a top-6 hit"
    assert M.recall_at("g", ranked, 5) is False,   "rank 7 must miss top-5"
    assert M.recall_at("zzz", ranked, 5) is False, "an id not in the ranking must miss"
    assert M.recall_at("b", ranked, 1) is False,   "only the exact top id is a top-1 hit"


def test_recall_at_degenerate_k_is_never_a_hit():
    assert M.recall_at("x", ["x"], 0) is False,    "top-0 is empty: nothing is in it"
    assert M.recall_at("x", ["x"], -1) is False,   "negative k must not be a hit"
    assert M.recall_at("x", [], 5) is False,       "an empty ranking has no top-k"


def test_recall_score_is_the_fraction_in_top_k():
    ranked = (["p", "q", "r", "s", "t"] +
              ["u", "v", "w", "x", "y"] +
              ["z", "a", "b", "c", "d"] +
              ["e", "f", "g", "h", "i"] +
              ["j"])
    correct = ["t", "v", "i", "j"]                 # ranks 5, 6, 20, 21
    assert M.recall_score(correct, ranked, 5) == 0.25,  "only 't' (rank 5) is in top-5: 1/4"
    assert M.recall_score(correct, ranked, 20) == 0.75, "t,v,i are in top-20: 3/4; 'j' (rank 21) is out"
    assert M.recall_score(correct, ranked, 1) == 0.0,   "none of the four is at rank 1"


def test_recall_score_empty_questions_is_no_measurement():
    assert M.recall_score([], ["a", "b", "c"], 5) is None
    assert M.recall_score([], ["a", "b", "c"], 20) is None


def test_recall_score_all_and_none_hit():
    ranked = ["a", "b", "c"]
    assert M.recall_score(["a", "b", "c"], ranked, 3) == 1.0, "every answer in the ranking: 3/3"
    assert M.recall_score(["z", "w"], ranked, 3) == 0.0,      "no answer in the ranking: 0/2"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except Exception as e:                                        # noqa: BLE001
            failed += 1
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n  {len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
