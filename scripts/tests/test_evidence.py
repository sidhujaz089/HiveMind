#!/usr/bin/env python3
"""The contract for making quarantine STRUCTURAL instead of a word in a report.

WHY THIS EXISTS. `scripts/harness_inventory.py` labels 13 of 36 model-touching scripts
`quarantined`. Nothing whatsoever prevents their output from feeding a decision. Draft four
said so in its own words -- "'quarantined' is a label with nothing behind it" -- and every
one of the nine outside reviewers picked that sentence out and agreed with it. Grok put the
consequence most sharply: **detection without repair is just documentation.**

THE SHAPE, and it is the reviewers' shape rather than ours. Two were on offer:

  (a) stop quarantined scripts RUNNING -- move them to a directory, or `sys.exit()` at line 1.
  (b) stop their output being CONSUMED -- one gate every reader passes through.

Grok and ChatGPT both argued for (b), and it is right for a repository whose whole method is
running things to find out what they do: a quarantined script must stay runnable, so a human
can debug it and so the inventory can keep measuring it. What must not happen is its output
silently becoming evidence.

So the boundary is at READ time, and the unit is the ROW. A row records the script that
produced it; a reader asks whether that producer is quarantined; a quarantined producer's
rows are refused with the caller named in the refusal. Z AI's extension applies here too --
inventory the CONSUMERS, not just the callers -- and this is what makes that possible.

THE ORACLE IS WRITTEN HERE AND THE IMPLEMENTATION IS DELEGATED. coder.py is given
scripts/evidence.py and never this file.
"""
import json
import sys
import tempfile
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


GOOD = "phase0-2026-09-08"          # a collector that reconciles and keeps raw text
BROKEN = "pre-phase0-broken"        # silent truncation, greedy extraction, invisible rows


def rows_file(rows):
    """Write rows, defaulting collector_version to a GOOD one unless the case sets it.

    The default keeps the producer cases (below) about producers. A case that cares about
    provenance sets `collector_version` itself, including to None to mean 'never stamped'.
    """
    out = []
    for r in rows:
        r = dict(r)
        if "collector_version" not in r:
            r["collector_version"] = GOOD
        if r.get("collector_version") is None:
            r.pop("collector_version")
        out.append(r)
    p = Path(tempfile.mkdtemp()) / "rows.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in out), encoding="utf-8")
    return p


try:
    import evidence
except Exception as e:                                       # noqa: BLE001
    print(f"  [BAD]  scripts/evidence.py does not import: {type(e).__name__}: {e}")
    print("         Required:")
    print("           status(script) -> 'trusted' | 'monitored' | 'quarantined' | 'unknown'")
    print("           Refused -- the exception raised when a quarantined row is consumed")
    print("           read_rows(path, caller) -> list of rows, refusing quarantined producers")
    print("           audit_log() -> the refusals recorded so far, each naming the caller")
    print("\n0 passed, 1 failed\n")
    sys.exit(1)

status = getattr(evidence, "status", None)
read_rows = getattr(evidence, "read_rows", None)
Refused = getattr(evidence, "Refused", None)
check("evidence.status exists", callable(status), True)
check("evidence.read_rows exists", callable(read_rows), True)
check("evidence.Refused is an exception type",
      isinstance(Refused, type) and issubclass(Refused, Exception), True)
if not (callable(status) and callable(read_rows) and isinstance(Refused, type)):
    print(f"\n{PASSED} passed, {FAILED + 1} failed\n")
    sys.exit(1)

print("\nstatus() agrees with the inventory -- ONE source of truth, not a second list:")
# These three are what harness_inventory.py reports today. If it changes its mind, this
# module must change with it; a hand-kept copy of the answer is the bug this repo keeps
# finding (L-150), so status() must ask the inventory rather than remember it.
# Same reason as test_inventory_indirect: ask, do not name. QUAR is used below wherever a
# quarantined producer is needed, so migrating any one file cannot break this suite.
import harness_inventory as _hi                              # noqa: E402
QUAR = next((r["path"] for r in _hi.inventory() if r["status"] == "quarantined"), None)
check("the inventory still reports some quarantined script", QUAR is not None, True)
check("and status() agrees it is quarantined", status(QUAR), "quarantined")
# AMENDED 2026-09-14. This named coder.py as the monitored example -- a LIVE FILE as a fixture,
# the same shape line 101 already avoids by ASKING the inventory which script is quarantined
# rather than naming one. Adding telemetry to coder.py put "done_reason" into ask() and the
# inventory reclassified it trusted, so this assertion failed for a change that fixed nothing:
# coder.py still does not act on the stop reason (t238 is open). Ask, do not name.
_MON = next((r["path"] for r in _hi.inventory() if r["status"] == "monitored"), None)
check("the inventory still reports some monitored script", _MON is not None, True)
check("and status() agrees it is monitored", status(_MON), "monitored")
check("a clean script is trusted",
      status("projects/local-inference/settings.py"), "trusted")
check("a file that touches no model is not silently 'trusted'",
      status("scripts/tests/test_evidence.py") in ("unknown", "not-applicable"), True)

print("\nrows from a TRUSTED producer are read normally:")
p = rows_file([{"id": "a", "producer": "projects/local-inference/settings.py", "v": 1},
               {"id": "b", "producer": "projects/local-inference/settings.py", "v": 2}])
got = read_rows(p, caller="test_evidence.py")
check("both rows come back", len(got), 2)
check("and they are the rows, unaltered", got[0]["v"], 1)

print("\nrows from a QUARANTINED producer are REFUSED -- this is the whole point:")
p = rows_file([{"id": "a", "producer": QUAR, "v": 1}])
refused = False
try:
    read_rows(p, caller="some_decision.py")
except Refused:
    refused = True
check("reading them raises Refused", refused, True)

print("\nthe refusal names the caller, the producer, and why:")
p = rows_file([{"id": "a", "producer": QUAR, "v": 1}])
msg = ""
try:
    read_rows(p, caller="some_decision.py")
except Refused as e:
    msg = str(e)
check("the caller is named", "some_decision.py" in msg, True)
check("the producer is named", QUAR.split("/")[-1] in msg, True)
check("the reason is given", "quarantin" in msg.lower(), True)

print("\nevery refusal is logged, so 'who tried to use quarantined output' is answerable:")
audit = getattr(evidence, "audit_log", None)
check("evidence.audit_log exists", callable(audit), True)
if callable(audit):
    entries = [e for e in audit() if e.get("caller") == "some_decision.py"]
    check("the attempt was recorded", len(entries) >= 1, True)
    check("with the producer", QUAR in str(entries[-1].get("producer", "")), True)

print("\nONE quarantined row poisons the read -- a partial answer is the failure mode here:")
p = rows_file([{"id": "a", "producer": "projects/local-inference/settings.py", "v": 1},
               {"id": "b", "producer": QUAR, "v": 2}])
refused = False
try:
    read_rows(p, caller="some_decision.py")
except Refused:
    refused = True
check("a mixed file is refused rather than silently filtered", refused, True)

print("\nMONITORED is not quarantined -- it reads, and it warns:")
p = rows_file([{"id": "a", "producer": "projects/local-inference/coder.py", "v": 1}])
got = read_rows(p, caller="test_evidence.py")
check("monitored rows are returned", len(got), 1)

print("\na row with NO producer is refused -- unprovenanced output is not trusted by default:")
p = rows_file([{"id": "a", "v": 1}])
refused = False
try:
    read_rows(p, caller="some_decision.py")
except Refused:
    refused = True
check("an unlabelled row does not get the benefit of the doubt", refused, True)

print("\nthe escape hatch exists, is explicit, and is not the default:")
p = rows_file([{"id": "a", "producer": QUAR, "v": 1}])
got = read_rows(p, caller="a_human_debugging.py", allow_quarantined=True)
check("a caller that explicitly asks may still read them", len(got), 1)

# ---------------------------------------------------------------------------------------
# THE SECOND CHECK: was the row produced by a collector that could be believed?
#
# Gate 4 of the six. It is the same decision as quarantine -- may this row be used as
# evidence -- asked about the COLLECTOR VERSION rather than the producing script, so it
# belongs in this gate rather than in a second one. Two modules that each guard reads would
# be L-150 again, and scripts/tests/test_scope_extract.py now has a test against exactly
# that shape of fork.
#
# Draft four's open item 4: "anything derived from the broken collector still sits in the
# record as though it were sound." Every reviewer wanted this. ChatGPT set the rule that
# matters: INVALID measurements must remain ACCESSIBLE but must not be CONSUMABLE as
# evidence by downstream automation. Deleting them would destroy evidence, which is the
# defect this phase is closing, so they stay readable and stop being admissible.
# ---------------------------------------------------------------------------------------
print("\nVALIDITY: a collector version is either believable or it is not:")
VALIDITY = getattr(evidence, "VALIDITY", None)
check("evidence.VALIDITY exists", VALIDITY is not None, True)
if VALIDITY is not None:
    for st in ("valid", "invalid", "superseded", "unknown"):
        check(f"VALIDITY includes {st!r}", st in VALIDITY, True)

validity = getattr(evidence, "validity", None)
check("evidence.validity(collector_version) exists", callable(validity), True)
if callable(validity):
    check("the repaired collector is valid", validity(GOOD), "valid")
    check("the pre-Phase-0 collector is invalid", validity(BROKEN), "invalid")
    check("a version nobody has ruled on is 'unknown', not 'valid'",
          validity("some-collector-nobody-stamped"), "unknown")

print("\nrows from the BROKEN collector are refused -- old numbers stop being evidence:")
p = rows_file([{"id": "a", "producer": "projects/local-inference/settings.py",
                "collector_version": BROKEN, "v": 1}])
refused = False
msg = ""
try:
    read_rows(p, caller="some_decision.py")
except Refused as e:
    refused, msg = True, str(e)
check("reading them raises Refused", refused, True)
check("and the refusal names the collector", BROKEN in msg, True)

print("\n...but they stay READABLE, because deleting evidence is the defect we are closing:")
p = rows_file([{"id": "a", "producer": "projects/local-inference/settings.py",
                "collector_version": BROKEN, "v": 1}])
got = read_rows(p, caller="a_human_debugging.py", allow_invalid=True)
check("an explicit reader still gets them", len(got), 1)
check("and the row is intact", got[0]["v"], 1)

print("\nan UNSTAMPED row is not given the benefit of the doubt:")
p = rows_file([{"id": "a", "producer": "projects/local-inference/settings.py",
                "collector_version": None, "v": 1}])
refused = False
try:
    read_rows(p, caller="some_decision.py")
except Refused:
    refused = True
check("a row with no collector_version is refused", refused, True)

print("\nan UNRECOGNISED collector version is refused too -- 'unknown' is not 'fine':")
# The gap that let scope_queue's own rows through the gate on 2026-09-08. validity() answered
# "unknown" for a version nobody had ruled on, read_rows only refused "invalid", and the row
# was admitted. This suite asked about a MISSING version and never about a present one nobody
# recognises, which is the more likely case: every new collector starts unrecognised.
p = rows_file([{"id": "a", "producer": "projects/local-inference/settings.py",
                "collector_version": "some-collector-nobody-stamped", "v": 1}])
refused = False
try:
    read_rows(p, caller="some_decision.py")
except Refused:
    refused = True
check("an unknown collector version is refused", refused, True)
check("and validity() still calls it 'unknown', not 'invalid'",
      validity("some-collector-nobody-stamped"), "unknown")

print("\nA PRODUCER THE INVENTORY CANNOT CLASSIFY is refused -- the other half of the gap:")
# Closed the collector-version branch on 2026-09-08 and left this one open, which Jas caught
# while reviewing: status() answers "not-applicable" for a producer that resolves to no script,
# and read_rows only acted on "quarantined". So a row naming a file that does not exist, or one
# the inventory has no opinion about, sailed straight through a gate that had consulted nothing.
# Same asymmetry as the unknown collector version, opposite branch.
p = rows_file([{"id": "a", "producer": "scripts/not_a_real_file.py", "v": 1}])
refused = False
try:
    read_rows(p, caller="some_decision.py")
except Refused:
    refused = True
check("a producer that resolves to nothing is refused", refused, True)

p = rows_file([{"id": "a", "producer": "scripts/tests/test_evidence.py", "v": 1}])
refused = False
try:
    read_rows(p, caller="some_decision.py")
except Refused:
    refused = True
check("a real file the inventory has no opinion about is refused too", refused, True)

print("\nthe two checks are independent -- passing one does not excuse the other:")
p = rows_file([{"id": "a", "producer": QUAR,
                "collector_version": GOOD, "v": 1}])
refused = False
try:
    read_rows(p, caller="some_decision.py")
except Refused:
    refused = True
check("a good collector does not rescue a quarantined producer", refused, True)

p = rows_file([{"id": "a", "producer": "projects/local-inference/settings.py",
                "collector_version": BROKEN, "v": 1}])
refused = False
try:
    read_rows(p, caller="some_decision.py", allow_quarantined=True)
except Refused:
    refused = True
check("allow_quarantined does not silently also allow an invalid collector", refused, True)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
