#!/usr/bin/env python3
"""t1153: a failing check files exactly ONE task at the front of the queue and ONE RCA request at the
front of the RCA queue; a second run with the same failure files nothing new; a passing run notes the
task once; a check that did not run is never called green. Temp dirs; taskstore.add is faked."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import hourly_checks as H  # noqa: E402
import session_review as SR  # noqa: E402
import taskstore  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


OUT = """Git state
  [PASS] The live checkout is on main
  [FAIL] The history is backed up
         the NAS bundle is 3 days old
         run backup_corpora.py
  [WARN] Working tree is clean
         Uncommitted changes present.
  [FAIL] Every project has a STATUS.md
OK -- 2 passed, 1 warning(s), 2 failed
"""
f = H.fails(OUT)
report("each [FAIL] block is a finding", sorted(f), ["Every project has a STATUS.md", "The history is backed up"])
report("its indented detail comes with it", f["The history is backed up"],
       "the NAS bundle is 3 days old\nrun backup_corpora.py")
report("a WARN is not a finding", "Working tree is clean" in f, False)
report("new failures parsed from run_tests",
       H.new_failures("  NEW FAILURES -- these are not in the ceiling: ['test_a.py', 'test_b.py']"),
       ["test_a.py", "test_b.py"])

# reconcile: the filing / dedupe / green-note rules
tasks, notes = {}, []
def open_task(m):
    return next((t for t, mk in tasks.items() if mk == m), "")
def file(name, detail, head, ts):
    tid = f"t{900 + len(tasks)}"
    tasks[tid] = H.marker(name)
    return tid
note = lambda tid, text: notes.append((tid, text))

s = H.reconcile({"Backup": "old"}, {"Backup"}, {}, "abc", "T1", open_task, file, note)
report("a failing check files exactly one task", list(tasks), ["t900"])
s = H.reconcile({"Backup": "old"}, {"Backup"}, s, "abc", "T2", open_task, file, note)
report("the same failure again files nothing new", list(tasks), ["t900"])
s2 = H.reconcile({}, set(), s, "def", "T3", open_task, file, note)
report("a check that did not run is not called green", (notes, s2["Backup"]["red"]), ([], True))
s = H.reconcile({}, {"Backup"}, s, "def", "T3", open_task, file, note)
report("a passing run notes the task", [t for t, _ in notes], ["t900"])
report("the note names the time and commit", "T3, def" in notes[0][1], True)
H.reconcile({}, {"Backup"}, s, "def", "T4", open_task, file, note)
report("a second passing run does not note again", len(notes), 1)

# 2026-10-19: "The two review ledgers agree (422 of 4472 disagree)" carries live counts, so every run
# made a new name, a new marker and a new task -- 6 tasks for one finding (t1460-t1486).
tasks.clear(); notes.clear()
s = H.reconcile({"Ledgers agree (422 of 4472 disagree)": "x"}, {"Ledgers agree (422 of 4472 disagree)"},
                {}, "abc", "T1", open_task, file, note)
s = H.reconcile({"Ledgers agree (565 of 5066 disagree)": "y"}, {"Ledgers agree (565 of 5066 disagree)"},
                s, "abc", "T2", open_task, file, note)
report("a finding whose counts change files ONE task", len(tasks), 1)
# t1885: the SECOND run's content is appended to that one task. It used to be computed, built into a
# body and thrown away because the task already existed -- a finding nobody could act on.
report("  and the changed content is appended to it", (len(notes), "y" in notes[-1][1]), (1, True))
s = H.reconcile({}, {"Ledgers agree"}, s, "def", "T3", open_task, file, note)
report("and a green run of it notes that task as green", [t for t, _ in notes], list(tasks) * 2)
report("  the last note is the green one", "green" in notes[-1][1].lower(), True)

# file_finding: the task goes to the FRONT, the RCA request to the front of the RCA queue
SR.FINDINGS = Path(tempfile.mkdtemp()) / "findings"
SR.FINDINGS.mkdir()
(SR.FINDINGS / "0050360a93af.json").write_text("{}")
added = []
taskstore.add = lambda title, **k: added.append((title, k)) or "t999"
H.rca_waits = lambda: ""
tid = H.file_finding("The history is backed up", "old", "abc", "T1")
report("one task is added", len(added), 1)
report("at the front of the queue", added[0][1].get("at_top"), True)
report("its body carries the dedupe marker", H.marker("The history is backed up") in added[0][1]["body"], True)
q = SR.pending_findings()
report("the RCA request is first in the RCA queue", q[0].name.startswith("check-"), True)
report("and it names the task it is covered by", json.loads(q[0].read_text())["covered_by"], tid)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
