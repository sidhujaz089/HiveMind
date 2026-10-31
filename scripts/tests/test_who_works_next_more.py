"""t694: Frontline tile is capped at three next items -- the excess must be reported, not dropped.

scripts/who_works.py record() caps every role's 'next' list at three and silently discards
the rest (the [:3] slice at who_works.py:97). This pins the data half: when a role's
waiting queue is longer than three, the who-works document reports how many are hidden
(next_more), so the tile can display '+N more'. Written 2026-10-06; red on main until
record() carries the count.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))

import who_works  # noqa: E402


def _write_snapshot(frontline_waiting, manager_waiting=(), senior_waiting=(), path=None):
    who_works.record(running=[], frontline_waiting=frontline_waiting,
                     manager_waiting=manager_waiting, senior_waiting=senior_waiting, path=path)
    return json.loads(path.read_text(encoding="utf-8"))


def test_overflow_is_reported_not_dropped():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "who-works.json"
        waiting = [("production", "brief-p1-t1%02d.txt" % i) for i in range(5)]
        doc = _write_snapshot(waiting, path=path)
        fr = doc["roles"]["frontline"]
        assert len(fr["next"]) == 3, fr
        assert fr["next_more"] == 2, fr


def test_no_overflow_reports_zero():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "who-works.json"
        waiting = [("production", "brief-p1-t201.txt"), ("production", "brief-p1-t202.txt")]
        doc = _write_snapshot(waiting, path=path)
        fr = doc["roles"]["frontline"]
        assert len(fr["next"]) == 2, fr
        assert fr["next_more"] == 0, fr


def test_other_roles_and_one_overflow_pinned():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "who-works.json"
        waiting = [("production", "brief-p1-t30%d.txt" % i) for i in range(4)]
        doc = _write_snapshot(waiting, path=path)
        assert doc["roles"]["frontline"]["next_more"] == 1, doc["roles"]["frontline"]
        assert doc["roles"]["senior"]["next_more"] == 0, doc["roles"]["senior"]
        assert doc["roles"]["manager"]["next_more"] == 0, doc["roles"]["manager"]


if __name__ == "__main__":
    failures = 0
    for name in ("test_overflow_is_reported_not_dropped",
                 "test_no_overflow_reports_zero",
                 "test_other_roles_and_one_overflow_pinned"):
        try:
            globals()[name]()
        except Exception as e:  # a test failure must be reported, not raised
            failures += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
        else:
            print(f"ok {name}")
    if failures:
        sys.exit(f"{failures} test(s) failed")
    print("all t694 oracles passed")
