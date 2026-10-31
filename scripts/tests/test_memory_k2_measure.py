#!/usr/bin/env python3
"""K2 (t1099): the measurement side -- recall@k, median latency, PASS/FAIL, the report.

Red on main for ONE reason: scripts/memory.py has no recall_at_k / median / score / verdict /
write_report, so every test below fails with AttributeError. `import memory` itself works on main.

No model, no server, no live file: every ranking is hand-written, the report goes to a temp dir,
and nothing here calls memory.embed -- the numbers being scored are supplied, which is the only
way a scoring rule can be tested without the embedder (the embedder shares the ThinkPad with the
Q4 and is meant to run at night).
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import memory  # the file the change goes into (exists on main)

# Two questions a person actually asks about past work, in the shape the task asks for.
CASES = [{"query": "what did we decide about routing.csv", "want": "abcdef01:4"},
         {"query": "where did L-114 come from", "want": "console-thread-1:0"}]
# keyword ranks one want inside the top 5 and the other at rank 11: @5 = 0.5, @20 = 1.0.
RANK_KEYWORD = ["x", "abcdef01:4", "y", "z", "w", "v", "u", "t", "s", "r", "console-thread-1:0"]
# vector ranks one at position 11 and never ranks the other: @5 = 0.0, @20 = 0.5.
RANK_VECTOR = ["x", "y", "z", "w", "v", "u", "t", "s", "r", "q", "p", "console-thread-1:0", "o"]
# one want at index 6: outside the first 6, inside the first 7 -- pins the off-by-one on k.
RANK_EDGE = ["a", "b", "c", "d", "e", "f", "abcdef01:4", "abcdef01:9"]

PASS_METRICS = {"recall@5": {"keyword": 0.6, "vector": 0.7, "hybrid": 0.85},
                "recall@20": {"keyword": 0.8, "vector": 0.85, "hybrid": 0.95},
                "median_latency_s": 1.2,
                "findable_seconds": 12,
                "cases": 40}


def test_recall_at_k_counts_a_hit_only_within_k():
    assert abs(memory.recall_at_k(CASES, RANK_KEYWORD, 5) - 0.5) < 1e-9
    assert abs(memory.recall_at_k(CASES, RANK_KEYWORD, 20) - 1.0) < 1e-9
    assert abs(memory.recall_at_k(CASES, RANK_VECTOR, 5) - 0.0) < 1e-9
    assert abs(memory.recall_at_k(CASES, RANK_VECTOR, 20) - 0.5) < 1e-9
    # The boundary: a want at index 6 is in the first 7 and not in the first 6.
    assert abs(memory.recall_at_k([CASES[0]], RANK_EDGE, 6) - 0.0) < 1e-9, "k is not honoured"
    assert abs(memory.recall_at_k([CASES[0]], RANK_EDGE, 7) - 1.0) < 1e-9, "k is off by one"
    # A ranking of the right LENGTH that does not contain the wanted turn is not a hit.
    assert abs(memory.recall_at_k(CASES, ["a", "b", "c", "d", "e", "f", "g"], 20) - 0.0) < 1e-9, (
        "counting any result as a hit: match on the wanted id, not on rank position")


def test_recall_at_k_on_no_cases_is_not_a_pass():
    assert abs(memory.recall_at_k([], RANK_KEYWORD, 5) - 0.0) < 1e-9, (
        "zero cases measured is not a perfect score (the vacuous-pass shape)")


def test_median_of_odd_even_single_and_empty():
    assert memory.median([3, 1, 2]) == 2
    assert memory.median([4, 1, 3, 2]) == 2.5
    assert memory.median([7]) == 7
    assert memory.median([]) is None, "an unmeasured latency must be None, not 0"


def test_score_scores_every_channel_separately():
    m = memory.score(CASES, {"keyword": RANK_KEYWORD, "vector": RANK_VECTOR, "hybrid": RANK_KEYWORD})
    assert abs(m["recall@5"]["keyword"] - 0.5) < 1e-9, m
    assert abs(m["recall@5"]["vector"] - 0.0) < 1e-9, m
    assert abs(m["recall@5"]["hybrid"] - 0.5) < 1e-9, m
    assert abs(m["recall@20"]["keyword"] - 1.0) < 1e-9, m
    assert abs(m["recall@20"]["vector"] - 0.5) < 1e-9, m
    assert m["cases"] == 2, f"a measurement must say how many cases it measured: {m.get('cases')}"


def test_verdict_passes_only_when_every_threshold_is_met():
    ok, reasons = memory.verdict(PASS_METRICS)
    assert ok is True, f"expected PASS, got {reasons}"
    assert reasons == [], f"a passing measurement has nothing to report: {reasons}"


def test_verdict_fails_a_hybrid_below_the_recall_bar():
    m = dict(PASS_METRICS, **{"recall@5": dict(PASS_METRICS["recall@5"], hybrid=0.79)})
    ok, reasons = memory.verdict(m)
    assert ok is False, "hybrid 0.79 is below the PASS bar of 0.8"
    assert any("0.8" in r for r in reasons), f"the reason must name the bar it missed: {reasons}"
    assert any("recall@5" in r and "hybrid" in r for r in reasons), reasons


def test_verdict_fails_a_hybrid_that_does_not_beat_keyword():
    m = dict(PASS_METRICS, **{"recall@5": dict(PASS_METRICS["recall@5"], hybrid=0.9, keyword=0.9)})
    ok, reasons = memory.verdict(m)
    assert ok is False, "a hybrid equal to keyword does not beat it, which is what the task asks"
    assert any("keyword" in r for r in reasons), reasons


def test_verdict_fails_an_unmeasured_or_late_findability_and_an_empty_case_set():
    ok, reasons = memory.verdict(dict(PASS_METRICS, findable_seconds=None))
    assert ok is False, "an unmeasured findability must not count as a pass"
    assert any("findable" in r for r in reasons), reasons
    ok, reasons = memory.verdict(dict(PASS_METRICS, findable_seconds=61))
    assert ok is False, "61 s is outside the 60 s PASS bar"
    assert any("60" in r for r in reasons), reasons
    ok, reasons = memory.verdict(dict(PASS_METRICS, cases=0))
    assert ok is False, "zero cases measured is never a pass (vacuous)"
    assert any("cases" in r for r in reasons), reasons


def test_write_report_records_the_numbers_whether_they_pass_or_fail():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "k2-results.md"
        memory.write_report(out, PASS_METRICS, memory.verdict(PASS_METRICS))
        text = out.read_text(encoding="utf-8")
        for word in ("keyword", "vector", "hybrid", "recall@5", "recall@20", "median", "findable"):
            assert word in text, f"the report is missing {word!r}:\n{text[:400]}"
        assert "PASS" in text, "a measurement that meets every bar must say PASS"
        assert "0.85" in text and "0.95" in text, f"the report must carry the measured numbers:\n{text[:400]}"
        bad = dict(PASS_METRICS, **{"recall@5": dict(PASS_METRICS["recall@5"], hybrid=0.4)})
        memory.write_report(out, bad, memory.verdict(bad))
        text = out.read_text(encoding="utf-8")
        assert "FAIL" in text, "a failing measurement must be reported as a failure"
        assert "0.4" in text, "the report must show the number that failed, not only the verdict"
        assert "0.6" in text, "the keyword-only number being beaten must stay in the report"


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
