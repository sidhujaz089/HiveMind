#!/usr/bin/env python3
"""2026-10-14: an oracle that reads live repository state (the role store, a run-logs ledger, the Hub
board, the live routing table) with no fixture of its own is refused at scope_lint -- three such suites
went red on main that day and each held every build. One that copies into a temp dir passes."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import scope_lint as L  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


live = {"kind": "oracle", "file": "scripts/tests/test_a.py",
        "oracle_text": "import dispatch_loop as d\norder = d.ROUTING['roles']['manager']\n"}
ledger = {"kind": "oracle", "file": "scripts/tests/test_b.py",
          "oracle_text": "rows = open('workspace/run-logs/auto-assigned.json').read()\n"}
fixture = {"kind": "oracle", "file": "scripts/tests/test_c.py",
           "oracle_text": "import tempfile, dispatch_loop as d\nd.AUTO = tempfile.mkdtemp()\nx = d.ROUTING\n"}
impl = {"kind": "implementation", "file": "scripts/x.py", "single_change": "read run-logs/ledger"}
f = L.live_state_findings([live, ledger, fixture, impl])
report("an oracle reading the live routing is refused", any(x[1] == 0 for x in f), True)
report("an oracle reading a run-logs ledger is refused", any(x[1] == 1 for x in f), True)
report("an oracle with its own temp-dir fixture passes", any(x[1] == 2 for x in f), False)
report("an implementation chunk is not judged by this rule", any(x[1] == 3 for x in f), False)
report("the finding is registered for the ledger", "oracle-live-state" in L.CHECK_NAMES, True)
report("static() runs it", any(x[0] == "oracle-live-state" for x in L.static([live], ROOT)), True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
