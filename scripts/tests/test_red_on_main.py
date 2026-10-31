#!/usr/bin/env python3
"""Red on main (Jas, 2026-10-14): the first sight files ONE front task and holds; still red after the
window it is declared (owner = that task), handed to Claude and released; seen green it is closed; a
suite dropping out of the refusal list while still red is NOT closed; and the chunk whose target is
the red suite (the fix) is not held by its own cause. Fakes only; nothing live is written."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import red_on_main as R  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


ledger, tasks, notes, declared, claude = [], [], [], [], []
kw = dict(file_task=lambda s, c, f, co: (tasks.append(s), f"t9{len(tasks)}")[1],
          note=lambda t, x: notes.append((t, x)), to_claude=claude.append,
          declare_fn=lambda s, t, since: declared.append((s, t)), append=ledger.append,
          facts_of=lambda c: {}, turned=lambda s: (None, None))

h = R.handle({"test_a.py": "p1-x"}, now=1000, history=list(ledger), **kw)
report("first sight: one task filed", tasks, ["test_a.py"])
report("  and the suite holds", h, ["test_a.py"])
report("  and an OPEN row is written", [r["event"] for r in ledger], ["open"])
h = R.handle({"test_a.py": "p1-x"}, now=1000 + 60, history=list(ledger), **kw)
report("inside the window: no second task, still holding", (tasks, h), (["test_a.py"], ["test_a.py"]))
h = R.handle({"test_a.py": "p1-x"}, now=1000 + R.WINDOW_S, history=list(ledger), **kw)
report("past the window: declared with its task as owner", declared, [("test_a.py", "t91")])
report("  handed to Claude", claude, ["t91"])
report("  and it no longer holds", h, [])

ledger2 = []
kw2 = {**kw, "append": ledger2.append}
R.handle({"test_b.py": "p2-y"}, now=0, history=[], **kw2)
R.handle({}, now=30, history=list(ledger2), is_red=lambda s: True, **kw2)
report("dropped from the refusals but still red: NOT closed", [r["event"] for r in ledger2], ["open"])
R.handle({}, now=40, history=list(ledger2), is_red=lambda s: False, **kw2)
report("seen green: closed", [r["event"] for r in ledger2], ["open", "closed"])

import dispatch_loop as D  # noqa: E402
D.HELD_SUITES.clear()
D.HELD_SUITES.add("test_a.py")
report("a chunk targeting the held suite is the fix, not held",
       Path("scripts/tests/test_a.py").name in D.HELD_SUITES, True)
report("the live flag is off outside the tick, so tests file nothing", D.RED_ACTIONS, False)
src = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
report("classify exempts the fix chunk", 'Path(c.get("target") or "").name not in HELD_SUITES' in src, True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
