"""manager_note: a scoper's '(a) ALREADY DONE' is closed automatically only when the task's OWN tests prove it
on main, and never when the answer qualifies itself (Jas, 2026-10-20)."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import manager_note as MN  # noqa: E402
import taskstore as ts  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


tmp = Path(tempfile.mkdtemp())
briefs = tmp / "briefs"; briefs.mkdir()
(briefs / "brief-p1-t42.txt").write_text("TASK: t42\nDONE WHEN python3 scripts/tests/test_a.py exits 0\n")
(briefs / "brief-p2-t42.txt").write_text("TASK: t42\nDONE WHEN python3 scripts/tests/test_b.py exits 0\n")
(briefs / "brief-p3-t420.txt").write_text("TASK: t420\nDONE WHEN python3 scripts/tests/test_other.py exits 0\n")
report("the task's tests come from every brief filed for it, and only its own",
       MN.task_tests("t42", "", briefs), ["scripts/tests/test_a.py", "scripts/tests/test_b.py"])

repo = tmp / "repo"; (repo / "scripts" / "tests").mkdir(parents=True)
for n in ("test_a.py", "test_b.py"):
    (repo / "scripts" / "tests" / n).write_text("pass\n")
real_tt = MN.task_tests
MN.task_tests = lambda tid, body="", briefs=None: real_tt(tid, body, tmp / "briefs")
try:
    ans = "## (a) ALREADY DONE -- verified\n"
    ok, why = MN.verify_done("t42", ans, "", repo, run=lambda t: 0)
    report("all the task's tests green on main: proven", ok, True)
    ok, why = MN.verify_done("t42", ans, "", repo, run=lambda t: 1 if t.endswith("test_b.py") else 0)
    report("one red: not proven, and it says which", (ok, "test_b.py" in why), (False, True))
    ok, why = MN.verify_done("t42", "## (a) ALREADY DONE -- for the recorder. Not the whole task.\n", "", repo,
                             run=lambda t: 0)
    report("a hedged answer is never closed by a machine (t1078's own words)", ok, False)
    ok, why = MN.verify_done("t999", ans, "", repo, run=lambda t: 0)
    report("no test defines done: not proven", (ok, "no test" in why), (False, True))
finally:
    MN.task_tests = real_tt

# apply(): through a real task store in a temp dir
tasks = tmp / "tasks"; (tasks / "done").mkdir(parents=True)
(tasks / "QUEUE.md").write_text("# QUEUE.md\n\n")
saved = (ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK)
ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK = tasks, tasks / "done", tasks / "QUEUE.md", tasks / ".lock"
real_to = MN.to
MN.to = lambda cls: "claude"
try:
    a = ts.add("a finished thing", area="workspace")
    d = MN.apply(a, "## (a) ALREADY DONE -- it landed\n", verify=lambda *x: (True, "verified on main: test_a.py exit 0"))
    m = (ts.find(a)[0] or {})
    report("proven: closed, assigned to nobody", (d[0], m.get("status")), ("done", "done"))
    b = ts.add("a half-finished thing", area="workspace")
    d = MN.apply(b, "## (a) ALREADY DONE -- it landed\n", verify=lambda *x: (False, "its test(s) are not green on main: x"))
    m = (ts.find(b)[0] or {})
    report("not proven: it escalates as before", (m.get("status"), ts.assignee(m.get("needs"))), ("queued", "technical")) # needs: is a tier (2026-10-29)
    report("  and the note says why it was not closed", "Not closed automatically" in (ts.find(b)[1] or ""), True)
finally:
    ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK = saved
    MN.to = real_to
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
