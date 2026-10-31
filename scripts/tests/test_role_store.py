#!/usr/bin/env python3
"""The role store (Jas, 2026-09-29, Models/Roles/Flow plan step 1). On a COPY in a temp directory,
never the live store: the changelog records every save and revert restores it; the delete guard
names orphaned tasks; Assign to: puts Claude, then Jas, then the rest alphabetically; a hand edit to
the generated SERVING.json block is caught."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "scripts")]
import role_store as rs  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


live = rs.load()
# fk=False: this suite is the store-vs-SERVING/task/skill contract. The chart's worker foreign
# key (t1592) is specified by scripts/tests/test_role_flow_mapping.py against seeded charts, and
# run against the LIVE chart as a WARN in check_workspace until t1590 fixes it -- so it is not
# silenced there, only kept out of a suite that is about a different agreement.
report("the live store checks clean", rs.check(fk=False), [])

with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    store, serving, log = t / "ROLES.json", t / "SERVING.json", t / "ROLES-CHANGELOG.jsonl"
    shutil.copy(rs.STORE, store)
    shutil.copy(rs.SERVING, serving)
    real = (rs.STORE, rs.SERVING, rs.LOG)
    rs.STORE, rs.SERVING, rs.LOG = store, serving, log
    try:
        d = rs.load()
        M = sorted(rs.model_roles(d))[0]          # any model role -- whichever exist
        d["roles"][M]["model"] = "some-other:tag"
        e1 = rs.save(d, "test", "swap the senior model")
        report("a save is logged with who and what", (e1["n"], e1["who"], e1["what"]), (1, "test", "swap the senior model"))
        report("  and regenerates SERVING.json from the store",
               json.loads(serving.read_text())["roles"][M]["tag"], "some-other:tag")
        e2 = rs.revert(1, "test")
        report("revert restores the snapshot", rs.load()["roles"][M]["model"],
               live["roles"][M]["model"])
        report("  and is itself an entry (so it can be undone)", (e2["n"], "revert entry 1" in e2["what"]), (2, True))
        # DYNAMIC (Jas, 2026-10-01: roles and tasks change on the Models tab -- the Senior may build
        # too). The guard is pinned on a fixture, and on the live store as a property of every role.
        fx = {"roles": {"a": {"tasks": [{"task": "x"}, {"task": "y"}]}, "b": {"tasks": [{"task": "y"}]}}}
        report("delete guard: a task only the deleted role does is named; a shared one is not",
               (rs.orphaned_by_delete(fx, "a"), rs.orphaned_by_delete(fx, "b")), (["x"], []))
        live_d = rs.load()
        ok = all(set(rs.orphaned_by_delete(live_d, k)) == {t["task"] for t in r.get("tasks", [])}
                 - {t["task"] for k2, r2 in live_d["roles"].items() if k2 != k for t in r2.get("tasks", [])}
                 for k, r in live_d["roles"].items())
        report("  and on the live store it holds for every role", ok, True)
        order = rs.assign_order(rs.load())
        # THE ORG CHART (Jas, 2026-10-19) replaced "Claude, Jas, then alphabetical": rank order.
        report("Assign to: every role in org-chart rank order", order, list(rs.load()["roles"]))
        s = json.loads(serving.read_text())
        s["roles"][M]["tag"] = "hand-edited"
        serving.write_text(json.dumps(s))
        report("a hand edit to SERVING.json is caught",
               any("SERVING.json roles differ" in p for p in rs.check(serving=serving)), True)
        report("person and agent roles are never written to SERVING.json",
               sorted(set(rs.load()["roles"]) - set(rs.serving_block(rs.load()))),
               sorted(set(rs.load()["roles"]) - set(rs.model_roles(rs.load()))))
        # SIMULTANEOUS EDITS (2026-10-03): four Models-tab removals in one second corrupted
        # ROLES.json (two writers shared one ".tmp") and lost edits (each saved a stale copy).
        import threading
        def bump(i):
            for _ in range(10):
                with rs.locked():
                    x = rs.load()
                    x["roles"][M]["probe"] = x["roles"][M].get("probe", 0) + 1
                    rs.save(x, "test", f"bump {i}")
        rs.update_role(M, "test", "reset", probe=0)
        th = [threading.Thread(target=bump, args=(i,)) for i in range(6)]
        [x.start() for x in th]
        [x.join() for x in th]
        report("60 simultaneous read-edit-saves: the store parses and none is lost",
               rs.load()["roles"][M]["probe"], 60)
        report("  and no temp file is left behind", sorted(p.name for p in t.glob("*.tmp")), [])
    finally:
        rs.STORE, rs.SERVING, rs.LOG = real
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
