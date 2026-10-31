#!/usr/bin/env python3
"""The contract for seeing the scripts that touch a model THROUGH another one.

WHY THIS EXISTS. `scripts/harness_inventory.py` reports "36 scripts touch a model" and
`scripts/scope_queue.py` is not one of them -- because it does not contain `11434`, `ollama`
or `/api/chat`. It calls `deliberate.deliberate()`, which does.

So the script that produced the 35-item measurement, the worked example in every draft of this
plan, is INVISIBLE to the audit built to map the iceberg. And because `scripts/evidence.py`
asks the inventory whether a row's producer is quarantined, every row scope_queue emits
answers `not-applicable`: **the gate is called and consults nothing.** Found on 2026-09-08 by
wiring the two together, after five straight dispatches failed against an assertion demanding
a classification that could not exist.

Two reviewers predicted the shape without seeing the code. ChatGPT: *"How do you know there
aren't 37? ... imports Ollama client, invokes HTTP endpoint, ... INDIRECTLY calls model
wrapper. Until then, call this a signature sweep, not a complete harness audit."* Z AI:
*"Inventory the CONSUMERS, not just the callers. A script that reads quarantined output and
feeds a decision is part of the iceberg."*

EXPOSURE IS INHERITED, AND THAT IS THE POINT. A script whose answers pass through a
quarantined component is not clean because its own grep came back empty. `scope_queue.py`
imports `deliberate.py`, which is quarantined for the greedy extractor, so `scope_queue.py` is
quarantined until that is fixed -- and its output stops being admissible as evidence, which is
a true statement about the measurement and a forcing function for the migration.

THE ORACLE IS WRITTEN HERE AND THE IMPLEMENTATION IS DELEGATED. coder.py is given
scripts/harness_inventory.py and never this file.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


import harness_inventory as hi                               # noqa: E402

rows = getattr(hi, "inventory", None)
check("harness_inventory.inventory() exists", callable(rows), True)
if not callable(rows):
    print("         Required: inventory() -> list of rows, one per script that touches a")
    print("                   model DIRECTLY or through another repo module. Each row keeps")
    print("                   path/status/faults and adds:")
    print("                     how     -- 'direct' or 'indirect'")
    print("                     reaches -- the model-touching paths it reaches by import")
    print(f"\n{PASSED} passed, {FAILED + 1} failed\n")
    sys.exit(1)

ALL = rows()
BY = {r["path"]: r for r in ALL}

print("\nThe direct callers are classified exactly as before -- this ADDS, it does not replace:")
# DO NOT NAME A FILE HERE. Naming exam.py pinned today's defect as the expected answer, so
# migrating exam.py broke this suite -- the identical mistake fixed for deliberate.py two
# hours earlier and left standing here. Ask the inventory which file is quarantined instead.
_q = [r for r in ALL if r["status"] == "quarantined" and r["how"] == "direct"]
check("some direct caller is still classified quarantined", bool(_q), True)
# AMENDED 2026-09-14, and the comment twelve lines above predicted this exactly: "DO NOT NAME
# A FILE HERE. Naming exam.py pinned today's defect as the expected answer, so migrating exam.py
# broke this suite." The very next assertion then named coder.py, and migrating coder.py broke
# this suite. Same mistake, same file, two lines apart.
#
# WHAT BROKE IT IS WORTH MORE THAN THE FIX. Adding telemetry to coder.py put the string
# "done_reason" into ask(), so harness_inventory reclassified it monitored -> TRUSTED. coder.py
# does not ACT on done_reason: there is no Truncated, no retry, and t238 is still open. So the
# inventory now calls a file trusted because the defect is MENTIONED, not because it is fixed.
# That weakness is filed; this suite should not encode one file's current state either way.
_m = [r for r in ALL if r["status"] == "monitored"]
check("some script is still classified monitored", bool(_m), True)
check("a clean script is still trusted",
      BY.get("projects/local-inference/settings.py", {}).get("status"), "trusted")
check("and it is marked as reached directly", (_q[0]["how"] if _q else None), "direct")

print("\nTHE SCRIPT THIS WAS ALL ABOUT is now in the inventory:")
sq = BY.get("scripts/scope_queue.py")
check("scripts/scope_queue.py appears at all", sq is not None, True)
if sq:
    check("it is marked indirect", sq["how"], "indirect")
    check("and it names what it reaches",
          any("deliberate.py" in x for x in sq["reaches"]), True)

print("\nEXPOSURE IS INHERITED -- a clean grep does not make you clean:")
# scope_queue.py's own signatures are fine: it migrated onto jsonx, it stores no fixed-width
# slice. But its answers pass through deliberate.py, which is quarantined for the greedy
# extractor. Reporting it as trusted would be the same lie the whole phase exists to stop.
# THE RULE, over every row, not the instance. An earlier version asserted flatly that
# scope_queue.py is quarantined BECAUSE deliberate.py is. That pinned the defect as the
# expected answer: when coder.py was handed deliberate.py and migrated it correctly onto
# jsonx, this suite failed it -- five times, and the model was right every time. A list of
# today's bugs asserted as expectations is a ratchet that locks them in, and the party doing
# the fix is the one who cannot see it.
RANK = {"trusted": 0, "monitored": 1, "quarantined": 2}
worst_ok = True
for r in ALL:
    reached = [BY[x]["status"] for x in r.get("reaches", []) if x in BY]
    floor = max([RANK[r["status"]]] + [RANK[x] for x in reached]) if reached else None
    if floor is not None and RANK[r["status"]] < floor:
        worst_ok = False
        print(f"         {r['path']} is {r['status']} but reaches something worse")
check("every row is at least as bad as the worst thing it reaches", worst_ok, True)

if sq:
    # And the concrete case, stated so it stays true after deliberate.py is repaired.
    d = BY.get("projects/local-inference/deliberate.py", {}).get("status")
    check("scope_queue's status tracks deliberate's rather than being hardcoded",
          RANK[sq["status"]] >= RANK.get(d, 0), True)
    check("its OWN faults are reported separately from what it inherits",
          "greedy-extract" in sq.get("own_faults", []), False)

print("\nThe count goes UP, which is the honest direction:")
check("more than the 36 direct callers", len(ALL) > 36, True)
check("every row says how it was reached",
      all(r.get("how") in ("direct", "indirect") for r in ALL), True)

print("\nA script that reaches nothing model-touching is still absent:")
check("this test file is not in the inventory", "scripts/tests/test_inventory_indirect.py"
      in BY, False)

print("\nInheritance is transitive, and a circular import does not hang it:")
# scope_queue -> deliberate is one hop. If anything reaches a model in two hops it must be
# found too, and an import cycle must terminate rather than recurse forever.
indirect = [r for r in ALL if r["how"] == "indirect"]
check("at least one indirect caller was found", len(indirect) >= 1, True)
check("every indirect row names at least one thing it reaches",
      all(r["reaches"] for r in indirect), True)

print("\nThe PRINTED headline must not undercount what inventory() knows:")
# Found 2026-09-09 after the migrations: inventory() returned 48 (38 direct + 10 indirect)
# while main() printed "38 script(s) touch a model" -- the old direct-only count, with nothing
# saying it was partial. The tool built to stop the machinery reporting confident partial
# numbers was doing exactly that in its own headline.
import subprocess                                            # noqa: E402
_out = subprocess.run([sys.executable, str(ROOT / "scripts" / "harness_inventory.py")],
                      capture_output=True, text=True, timeout=300).stdout
_head = next((l for l in _out.splitlines() if "touch a model" in l), "")
check("the headline states the FULL count", str(len(ALL)) in _head, True)
check("and it distinguishes direct from indirect",
      "indirect" in _head.lower(), True)

print("\nThe gate can now give a real verdict for a row scope_queue produced:")
import evidence                                              # noqa: E402
check("evidence.status() no longer answers 'not-applicable'",
      evidence.status("scripts/scope_queue.py") in ("trusted", "monitored", "quarantined"),
      True)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
