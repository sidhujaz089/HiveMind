#!/usr/bin/env python3
"""The contract for ONE shared JSON extractor, so there stops being eight of them.

`re.search(r"\\{.*\\}", text, re.S)` -- first brace to last -- appears in EIGHT places in this
repository. It was fixed in scope_queue.py on 2026-09-07 after it mis-parsed three of four
realistic answer shapes; `scripts/harness_inventory.py` then found the same line in seven
more files, including exam.py (which scored the model interviews) and deliberate.py (which
scope_queue.py itself calls).

Fixing seven copies separately would produce seven implementations of one idea, which is
L-150: a lesson learned in one implementation and never carried to the other. So the fix is a
shared module, and this is its contract.

THE ORACLE IS WRITTEN HERE AND THE IMPLEMENTATION IS DELEGATED. An agent judged by a suite it
can edit will edit the suite; nine outside reviewers converged on that. coder.py is given
scripts/jsonx.py and never this file.
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


A = '{"id": "t1", "verdict": "SCOPED"}'
B = '{"id": "t2", "verdict": "NEEDS_JAS"}'
C = '{"note": "not a verdict"}'

try:
    import jsonx
except Exception as e:                                       # noqa: BLE001
    print(f"  [BAD]  scripts/jsonx.py does not import: {type(e).__name__}: {e}")
    print("         Required: objects(text) -> list of every balanced-brace JSON object,")
    print("                   in order, ignoring prose and fenced ```json blocks; and")
    print("                   one(text, keys) -> {'status': ..., 'obj': ...} where status is")
    print("                   ok | ambiguous | no-match | invalid-json | no-json and a match")
    print("                   is an object carrying every key in `keys`.")
    print("\n0 passed, 1 failed\n")
    sys.exit(1)

objects = getattr(jsonx, "objects", None)
one = getattr(jsonx, "one", None)
check("jsonx.objects exists", callable(objects), True)
check("jsonx.one exists", callable(one), True)
if not (callable(objects) and callable(one)):
    print(f"\n{PASSED} passed, {FAILED + 1} failed\n")
    sys.exit(1)

print("\nobjects() finds every complete object and ignores the prose around them:")
check("bare", [o.get("id") for o in objects(A)], ["t1"])
check("prose either side", [o.get("id") for o in objects(f"think {{x}}.\n{A}\nlater {{y}}")],
      ["t1"])
check("two objects, in order", [o.get("id") for o in objects(f"{A}\n{B}")], ["t1", "t2"])
check("a fenced block", [o.get("id") for o in objects(f"```json\n{A}\n```")], ["t1"])
check("nothing to find", objects("no braces here"), [])
check("an unterminated object yields nothing", objects('{"id": "t1", "verd'), [])

print("\none() refuses to guess, which is the whole point:")
check("exactly one match", one(A, ("id", "verdict"))["status"], "ok")
check("  and returns it", one(A, ("id", "verdict"))["obj"]["id"], "t1")

# AMBIGUITY IS ABOUT CONFLICT, NOT COUNT -- and the first version of this contract got that
# wrong. It said "two matches -> never guess", reasoning that two objects mean two answers.
# Measured on the 2026-09-07 re-run: 5 of 27 rows came back ambiguous and ALL FIVE were the
# same verdict repeated -- t084 said NOT_DELEGABLE fifteen times. The rule manufactured a
# ~19% false-failure rate in the very number the run existed to produce. A model repeating
# itself has not disagreed with itself.
A_AGAIN = '{"id": "t1", "verdict": "SCOPED", "file": "b.py"}'
check("the same verdict twice RESOLVES -- repetition is not disagreement",
      one(f"{A}\n{A_AGAIN}", ("id", "verdict"))["status"], "ok")
check("  and it is that verdict",
      one(f"{A}\n{A_AGAIN}", ("id", "verdict"))["obj"]["verdict"], "SCOPED")
check("two DIFFERENT verdicts is ambiguous, and never resolved by position",
      one(f"{A}\n{B}", ("id", "verdict"))["status"], "ambiguous")
check("one match beside a non-match resolves",
      one(f"{C}\n{A}", ("id", "verdict"))["status"], "ok")
check("parsed but nothing carries the keys", one(C, ("id", "verdict"))["status"], "no-match")
check("a candidate that will not parse", one('{"id": "t1", "verd', ("id",))["status"],
      "invalid-json")
check("no candidate at all", one("plain prose", ("id",))["status"], "no-json")
check("empty input", one("", ("id",))["status"], "no-json")

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
