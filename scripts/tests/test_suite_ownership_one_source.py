#!/usr/bin/env python3
"""t1886 -- the sweep and the ceiling check must use ONE definition of an owned red suite.

Measured 2026-10-25 (dsh-harness/deepseek-v4.1-flash) during a sweep of main: 855 suites run,
173 failing, and the sweep printed UNOWNED for 100 DISTINCT suites ("is RED and has no owner --
name one in workspace/suite-owners.json"). workspace/suite-owners.json held 182 entries. Yet
scripts/tests/test_ceiling_owned.py said "ok -- every allowed failure has an open owner", because
it reads the CEILING's reason text, not the owners file. Both were internally consistent and about
different files, so 100 red suites could be "owned" by the rule and unowned by the report.

The rule this suite pins: an owned suite names a TASK ID, in suite-owners.json or in the ceiling's
reason, and that task is OPEN. One lookup serves both mechanisms, so "owned" means one thing.

The live repository is checked first, because that is where the defect lives: on the old code the
gap below is every allowed suite that suite-owners.json does not name (282 when measured), and that
is exactly what the sweep then calls UNOWNED. The fixtures after it pin the rule itself.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import run_tests as rt  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  (got {got!r}, want {want!r})"))


live_ceiling = json.loads((ROOT / "workspace" / "test-failures-ceiling.json").read_text(encoding="utf-8"))
live_allowed = list(live_ceiling.get("allowed_to_fail") or [])
live_owners = json.loads((ROOT / "workspace" / "suite-owners.json").read_text(encoding="utf-8"))
live_reasons = {}
for v in live_ceiling.values():
    if isinstance(v, dict):
        live_reasons.update(v)
live_open = {f.name.split("--")[0] for f in (ROOT / "workspace" / "tasks").glob("t*.md")}

resolved = {s: rt.suite_owner(s) for s in live_allowed}
gap = sorted(s for s, o in resolved.items() if not o)
report("every suite the ceiling allows to fail names an OPEN owner, in one of the two files",
       len(gap), 0)
if gap:
    print(f"        {len(gap)} would still print UNOWNED; the first few: {gap[:6]}")
report("  the ceiling supplies owners too, not only suite-owners.json",
       sum(1 for s, o in resolved.items() if o and s not in live_owners) > 0, True)

# ---- the rule, on fixtures: both sources count, and only an OPEN task is an owner ---------------
owners = {"from_file.py": "t1", "closed_in_file.py": "t9"}
reasons = {"from_ceiling.py": "t2 (2026-10-25): red since a measured run.",
           "closed_in_ceiling.py": "t9: closed"}
open_ids = {"t1", "t2"}
report("a suite named in suite-owners.json resolves to its open task",
       rt.suite_owner("from_file.py", open_ids, owners, reasons), "t1")
report("a suite named only in the ceiling's reason resolves too -- the 282 the sweep called UNOWNED",
       rt.suite_owner("from_ceiling.py", open_ids, owners, reasons), "t2")
report("a CLOSED task owns nothing, from either file",
       (rt.suite_owner("closed_in_file.py", open_ids, owners, reasons),
        rt.suite_owner("closed_in_ceiling.py", open_ids, owners, reasons)), ("", ""))
report("a suite named nowhere owns nothing", rt.suite_owner("nobody.py", open_ids, owners, reasons), "")
# THE PREMISE WAS A LIVE SAMPLE, AND LIVE THINGS CLOSE (2026-10-25). This asserted that t1592 or
# t1888 was open -- and both were closed by the very work that added the assertion, so the suite went
# red on main for a reason that had nothing to do with what it proves. The property is "the set comes
# from the store", not "these two ids happen to be in it": assert the shape and that it agrees with the
# store's own answer.
import taskstore as _ts
_store_open = {m.get("id") for m, _b in _ts.load_all().values()
               if m.get("status") not in ("done", "cancelled", "resolved")}
report("the open-task set is read from the store, not guessed",
       (isinstance(live_open, set) and bool(live_open) and live_open == _store_open), True)

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
