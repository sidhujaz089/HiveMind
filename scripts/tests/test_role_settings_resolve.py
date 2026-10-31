#!/usr/bin/env python3
"""t3955 -- a per-task setting is alive only if the reader names a role the store has.

WHAT THIS PINS. The Models tab gives a task a Series limit, and the dispatcher honours it in
task_routing.apply_series (scripts/task_routing.py:362-382), which asks task_routing.entry(store,
role, kind) for the entry that carries the number. entry (task_routing.py:252-254) looks the role
up through tasks_of (:217-218) and returns None when the role key is not in store["roles"]. One
site in the dispatcher passes a role key the store does not have:

    scripts/dispatch_loop.py:2841
        n = int((task_routing.entry(ROUTING, "senior", "scoping") or {}).get("series") or 0)

"senior" is not a role the store has -- its keys are jas, claude, manager, auditor, coder,
embedder, dsh. So entry() answers None, n is 0, and 0 is documented as NO LIMIT
(task_routing.py:369; the comment at dispatch_loop.py:2839-2840). Today the Senior station's
yield-after-scoping never fires, and the Series box on the Scoping row reaches nobody.

SHAPE OF THIS SUITE. Five tests pass today and pin the behaviour the fix must not break: the
series value is read PER ROLE (the same task answers two numbers), it MOVES the queue through
apply_series (not just returned), 0 leaves the queue alone, and max_lanes is read off the owner's
entry with three distinct values. One test is RED today for the missing change and only that: the
last scans the dispatcher's own source for the literal (role, task) pairs it asks the store for
and fails on a pair the store cannot answer. Its first assertion is that the scan found at least
one literal call, so deleting the read cannot pass.

NO LIVE STATE. The store used here is built in this file: a literal dict, deepcopy()d, written
into a tempfile.mkdtemp() directory and read back with json.loads, so it is exercised in the shape
the dispatcher's loader produces. No repository configuration is opened, nothing is written
outside the temp directory, no model is called, no subprocess is started. The dispatcher's source
text is read read-only: it is the code the fix edits, not configuration that can drift under the
suite.
"""
import copy
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import task_routing  # noqa: E402

DISPATCHER = ROOT / "scripts" / "dispatch_loop.py"

# entry(d, role, kind) maps a kind to a task id through task_routing.KIND_TASK (task_routing.py:45-47),
# so the kinds this suite passes come from the module's own inverse table, TASK_KIND (task_routing.py:47):
# a rename in the code is followed by the fixture instead of breaking the suite.
KIND_SCOPING = task_routing.TASK_KIND.get("scoping", "scoping")
KIND_RCA = task_routing.TASK_KIND.get("rca", "rca")

BASE = {
    "roles": {
        # Real role keys, real task ids, real settings values taken from the store the Models tab
        # writes (manager: Building max_lanes 1 series 1, Scoping series 1, RCA max_lanes 1).
        "manager": {"kind": "model", "model": "swift", "lanes": 4, "tasks": [
            {"task": "building", "interrupt": False, "series": 1, "max_lanes": 1},
            {"task": "scoping", "interrupt": False, "series": 1, "max_lanes": 1},
            {"task": "rca", "interrupt": False, "series": 1, "max_lanes": 1},
        ]},
        # auditor's Scoping series 3 is NOT in today's store: it exists so a reader that answers one
        # fixed number for every role, or that ignores the role argument, cannot pass. Its rca series
        # 0 is the store's real value for a limit that means "no limit".
        "auditor": {"kind": "model", "model": "swift-junior:latest", "lanes": 1, "tasks": [
            {"task": "scoping", "interrupt": False, "series": 3, "max_lanes": 2},
            {"task": "rca", "interrupt": False, "series": 0, "max_lanes": 0},
        ]},
        # A role that lists no Scoping at all, so entry() can be shown to answer None for a task the
        # role does not carry: a reader that ignores the task argument fails this.
        "dsh": {"kind": "agent", "tasks": [
            {"task": "merge_review", "interrupt": False},
            {"task": "direct_commit", "interrupt": False},
        ]},
    },
}


def _fixture_store():
    """A store this suite owns: deepcopy of the literal above, written to a fresh temp directory and
    read back through json.loads -- the shape the dispatcher's loader hands back."""
    tmp = Path(tempfile.mkdtemp(prefix="t3955-"))
    (tmp / "store.json").write_text(json.dumps(copy.deepcopy(BASE)), encoding="utf-8")
    return json.loads((tmp / "store.json").read_text(encoding="utf-8"))


STORE = _fixture_store()
FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        print(f"      got:  {got!r}")
        print(f"      want: {want!r}")
        FAILURES.append(label)


def test_the_series_value_is_read_per_role():
    """The setting belongs to the (role, task) pair: the same task on two roles answers two
    numbers, and a role that does not list the task answers None."""
    check("entry(STORE, manager, scoping).series",
          (task_routing.entry(STORE, "manager", KIND_SCOPING) or {}).get("series"), 1)
    check("entry(STORE, auditor, scoping).series",
          (task_routing.entry(STORE, "auditor", KIND_SCOPING) or {}).get("series"), 3)
    check("entry(STORE, dsh, scoping) -- dsh lists no scoping",
          task_routing.entry(STORE, "dsh", KIND_SCOPING), None)


def test_the_series_value_moves_the_queue():
    """Not the return shape: series 1 with a streak of 1 scoping item must put the next item of a
    DIFFERENT kind in front (task_routing.apply_series, task_routing.py:362-382)."""
    items = [{"role": "manager", "kind": KIND_SCOPING, "key": "a"},
             {"role": "manager", "kind": KIND_RCA, "key": "b"}]
    got = task_routing.apply_series(STORE, items, {"manager": (KIND_SCOPING, 1)})
    check("apply_series yields after 1 scoping in a row (manager series 1)",
          [i["key"] for i in got], ["b", "a"])


def test_series_0_leaves_the_queue_alone():
    """The value is READ, not treated as a flag: 0 is documented as no limit (task_routing.py:369),
    so a streak against a task whose series is 0 must not reorder the queue."""
    items = [{"role": "auditor", "kind": KIND_RCA, "key": "a"},
             {"role": "auditor", "kind": KIND_SCOPING, "key": "b"}]
    got = task_routing.apply_series(STORE, items, {"auditor": (KIND_RCA, 9)})
    check("apply_series leaves series 0 alone", [i["key"] for i in got], ["a", "b"])


def test_max_lanes_is_read_off_the_owner_entry():
    """max_lanes (task_routing.py:453-470) is read from the entry for (kind, role); three distinct
    numbers, so a reader that hard-codes one value fails."""
    check("max_lanes(STORE, scoping, role=auditor)",
          task_routing.max_lanes(STORE, KIND_SCOPING, role="auditor"), 2)
    check("max_lanes(STORE, rca, role=manager)",
          task_routing.max_lanes(STORE, KIND_RCA, role="manager"), 1)
    check("max_lanes(STORE, rca, role=auditor)  (0 = no limit)",
          task_routing.max_lanes(STORE, KIND_RCA, role="auditor"), 0)


ENTRY_CALL = re.compile(
    r'\bentry\(\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*"([a-z_][a-z0-9_]*)"\s*,\s*"([a-z_][a-z0-9_]*)"\s*\)')


def test_the_dispatcher_names_no_role_the_store_lacks():
    """THE FIX (red today). Every literal (role, task) pair scripts/dispatch_loop.py asks
    task_routing.entry() for must resolve in the store: the role must be a key of its roles, and
    that role must list that task. dispatch_loop.py:2841 passes a key the store has no entry for,
    so the series number on that row is read by nobody."""
    lines = DISPATCHER.read_text(encoding="utf-8").splitlines()
    scanned = 0
    offenders = []
    for lineno, line in enumerate(lines, 1):
        for role, task in ENTRY_CALL.findall(line):
            scanned += 1
            listed = [e.get("task") for e in (STORE["roles"].get(role, {}).get("tasks") or [])]
            if role not in STORE["roles"]:
                offenders.append(f"dispatch_loop.py:{lineno}: entry(..., {role!r}, {task!r}) --"
                                 f" {role!r} is not a role the store has, so this setting is"
                                 " read by nobody")
            elif task not in listed:
                offenders.append(f"dispatch_loop.py:{lineno}: entry(..., {role!r}, {task!r}) --"
                                 f" {role!r} does not list {task!r}, so entry() returns None")
    check(f"the scan sees the dispatcher's literal entry() calls at all ({scanned} found)",
          scanned >= 1, True)
    check("every literal entry(role, task) in the dispatcher resolves in the store", offenders, [])


def main():
    tests = sorted((v for k, v in globals().items()
                    if k.startswith("test_") and callable(v)), key=lambda f: f.__name__)
    for f in tests:
        f()
    print(f"\n{len(FAILURES)} of {len(tests)} test(s) failed")
    for label in FAILURES:
        print(f"  FAILED: {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
