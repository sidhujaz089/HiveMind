"""t104: the check history is read as a trend -- totals per day, and how long each non-passing check
has been in its state. A failing run is in the history (a gap exactly then is the defect)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_trend as T

ok = fail = 0
def check(name, cond):
    global ok, fail
    print(f"  [{'ok' if cond else 'FAIL'}]   {name}"); ok += bool(cond); fail += not cond

rs = [{"at": "2026-10-01T10:00:00Z", "failed": 1, "warnings": 2, "failing": ["A"], "warning": ["W"]},
      {"at": "2026-10-02T09:00:00Z", "failed": 0, "warnings": 2, "failing": [], "warning": ["W", "V"]},
      {"at": "2026-10-02T10:00:00Z", "failed": 0, "warnings": 2, "failing": [], "warning": ["W", "V"]}]
d = T.by_day(rs)
check("per day: runs, worst failed, last warnings", d["2026-10-01"] == (1, 1, 2) and d["2026-10-02"] == (2, 0, 2))
s = T.streaks(rs)
check("a WARN present in every run is dated from the first", s["W"] == ("WARN", "2026-10-01T10:00:00Z", 3))
check("  a newer one from when it started", s["V"] == ("WARN", "2026-10-02T09:00:00Z", 2))
check("a check that recovered is not listed", "A" not in s)
rs2 = [{"at": "a1", "failing": [], "warning": ["X (6 without)"]}, {"at": "a2", "failing": [], "warning": ["X (7 without)"]}]
check("a WARN whose count moves keeps one streak", T.streaks(rs2)["X (7 without)"] == ("WARN", "a1", 2))
check("no history: nothing to say, not a pass", T.streaks([]) == {})
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
