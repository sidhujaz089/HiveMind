#!/usr/bin/env python3
"""The contract for scripts/error_scan.py.

EVERY CASE BELOW IS A REAL INCIDENT from 2026-09-08 to 2026-09-10, not an invented one. The
fixtures are the shapes those failures actually left on disk. A scanner that passes this suite
would have caught all five truncation failures the same day they happened instead of days
later when a human noticed.

Written BEFORE the implementation, and it must be RED against the stub. An oracle that passes
against an unimplemented file is hollow -- it is asserting the current broken state.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import error_scan                                            # noqa: E402

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    ok = got == want
    if ok:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def kinds(findings):
    return sorted({f["kind"] for f in findings})


d = Path(tempfile.mkdtemp())


def runs(*rows):
    """A fixture shaped like workspace/agent-runs.jsonl."""
    p = d / f"runs{len(list(d.iterdir()))}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


print("A CUT-OFF ANSWER must be found. (The preflight that spent its whole")
print("budget thinking and returned empty content, 2026-09-09.)")
f = error_scan.scan([runs({"at": "2026-09-09T20:00:00Z", "done_reason": "length",
                          "outcome": "UNPARSEABLE", "tokens": 4000})])
check("it is found", "truncated-output" in kinds(f), True)
check("and the source is named", f[0]["source"].endswith(".jsonl"), True)
check("and the row's timestamp is carried", f[0]["at"], "2026-09-09T20:00:00Z")

print("\nA TOOL LOOP THAT NEVER WROTE must be found. (Eight plan sections,")
print("220 tool calls, not one word produced, 2026-09-10.)")
f = error_scan.scan([runs({"at": "2026-09-10T02:00:00Z", "done_reason": "tool-round-cap"})])
check("it is found", kinds(f), ["tool-loop-exhausted"])

print("\nAN UNPARSEABLE ATTEMPT must be found. (Jas's t238 dispatch: four")
print("attempts, all unusable, nothing written, 2026-09-10.)")
f = error_scan.scan([runs({"at": "2026-09-10T12:25:03Z", "outcome": "UNPARSEABLE",
                           "why": "invalid JSON: Expecting property name enclosed in "
                                  "double quotes: line 1 column 2 (char 1)"})])
check("it is found", kinds(f), ["unusable-parse"])

print("\nA SIGNAL DEATH must be found in a plain text log. (git push exited 141")
print("and printed nothing after the last green test line, 2026-09-09.)")
log = d / "dispatch.log"
log.write_text("  77/83 passing; 6 failing, 6 allowed\n  No new failures.\n"
               "PUSH_EXIT=141\n")
f = error_scan.scan([log])
check("it is found", kinds(f), ["silent-signal-death"])
check("and the line number is given", isinstance(f[0]["locator"], int), True)

print("\nA REPLY THAT SAYS IT COULD NOT READ IN FULL must be found. (Q16 handed a")
print("prefix of a 41,000-char ledger, and the rows it needed were in the tail.)")
log2 = d / "escalation.md"
log2.write_text("What I established (from agent-runs.jsonl, truncated -- I could not read "
                "the full file):\n")
f = error_scan.scan([log2])
check("it is found", kinds(f), ["read-truncated"])

print("\nTOKENS SPENT AND NOTHING RETURNED must be found.")
f = error_scan.scan([runs({"at": "2026-09-09T21:00:00Z", "tokens": 5743, "content": ""})])
check("it is found", "empty-with-tokens" in kinds(f), True)

print("\n...AND A CLEAN LOG MUST PRODUCE NOTHING. A scanner that always finds")
print("something is as useless as one that never does:")
f = error_scan.scan([runs({"at": "2026-09-09T18:00:00Z", "done_reason": "stop",
                           "outcome": "ACCEPTED", "tokens": 8442, "content": "a patch"})])
check("a successful run is not a finding", f, [])
clean = d / "clean.log"
clean.write_text("  8/8 sections complete\n  assembled -> REPAIR-PLAN.md\n  PUSH_EXIT=0\n")
check("a successful log is not a finding", error_scan.scan([clean]), [])

print("\nTHE EVIDENCE MUST BE WHOLE. This scanner exists because of truncation;")
print("storing a prefix of its own findings would be the defect inside the hunter:")
long_why = "invalid JSON: " + ("x" * 3000)
f = error_scan.scan([runs({"at": "2026-09-10T00:00:00Z", "outcome": "UNPARSEABLE",
                           "why": long_why})])
check("the full text is kept, not a slice", long_why in f[0]["evidence"], True)

print("\nA MALFORMED LINE MUST NOT STOP THE SCAN. A scanner that dies on row 4")
print("of 900 has found nothing and reported nothing:")
torn = d / "torn.jsonl"
torn.write_text(json.dumps({"at": "1", "done_reason": "length"}) + "\n"
                + "{not json at all\n"
                + json.dumps({"at": "2", "done_reason": "tool-round-cap"}) + "\n")
f = error_scan.scan([torn])
check("both good rows are still found", kinds(f),
      ["tool-loop-exhausted", "truncated-output"])

print("\nA MISSING SOURCE IS NOT A PASS. 'no findings' and 'nothing was looked")
print("at' must be distinguishable (L-146):")
res = error_scan.scan([d / "does-not-exist.jsonl"])
check("it does not crash", isinstance(res, list), True)
check("and it reports the skip", error_scan.skipped(), [str(d / "does-not-exist.jsonl")])

print("\nEVERY FINDING MUST CARRY ITS EVIDENCE. The first implementation passed this")
print("suite 15/15 and returned evidence=None for 6 of 7 findings on real logs --")
print("the oracle was weak, not the work. A finding with no evidence cannot be")
print("triaged, cannot become a signature, and cannot be shown to anyone:")
mixed = error_scan.scan([runs(
    {"at": "1", "done_reason": "length"},
    {"at": "2", "done_reason": "tool-round-cap"},
    {"at": "3", "outcome": "UNPARSEABLE"},
    {"at": "4", "outcome": "UNPARSEABLE", "why": "invalid JSON"},
    {"at": "5", "tokens": 99, "content": ""},
)])
check("several findings are produced", len(mixed) >= 4, True)
check("none of them has evidence=None",
      [f["kind"] for f in mixed if f.get("evidence") is None], [])
check("and none is empty either",
      [f["kind"] for f in mixed if not str(f.get("evidence") or "").strip()], [])
check("a row with no 'why' still yields evidence -- the row itself is the evidence",
      bool(str([f for f in mixed if f["at"] == "3"][0]["evidence"]).strip()), True)

print("\nA NAMED DIRECTORY SOURCE MUST BE WALKED FOR .json TOO. workspace/plan")
print("holds its sidecars as .json and the first implementation skipped them,")
print("so the eight tool-round-cap rows that motivated this script were invisible:")
dd = Path(tempfile.mkdtemp())
(dd / "07-sec.json").write_text(json.dumps({"done_reason": "tool-round-cap", "at": "9"}))
f = error_scan.scan([dd])
check("a .json sidecar in a scanned directory is found", kinds(f), ["tool-loop-exhausted"])

print("\nAND THE BACKUP LEDGER IS A SOURCE (2026-09-11). A status outside the")
print("healthy set must surface -- including one nobody has invented yet:")
f = error_scan.scan([runs({"at": "b1", "job": "nas-backup", "status": "verify-failed",
                           "detail": "does NOT read back byte-identical"})])
check("a failed backup is a finding", len(f) >= 1, True)
f = error_scan.scan([runs({"at": "b2", "job": "nas-backup", "status": "quantum-flux",
                           "detail": "a status from the future"})])
check("an UNRECOGNISED status is a finding too", len(f) >= 1, True)
f = error_scan.scan([runs({"at": "b3", "job": "nas-backup", "status": "verified",
                           "detail": "read back cold and byte-identical"})])
check("...and a healthy backup is not", f, [])

print("\nPROSE ABOUT A DEFECT IS NOT THE DEFECT. The first full scan returned 339")
print("findings and roughly 332 were noise: 284 came from Ollama reporting")
print("truncated = 0, which means NOTHING WAS TRUNCATED, matched as truncation.")
print("This is the third time this shape has bitten -- it is why the signature")
print("gate refused jsonx.py, and why gate 2's claim was wrong:")
noise = d / "ollama-noise.log"
noise.write_text(
    "Sep 09 11:36:16 ollama: slot release: id 0 | task 2749 | stop processing: "
    "n_tokens = 22797, truncated = 0\n"
    "Sep 09 11:41:12 ollama: slot release: id 0 | task 4898 | stop processing: "
    "n_tokens = 31881, truncated = 0\n")
check("truncated = 0 is NOT a truncation finding", error_scan.scan([noise]), [])

real = d / "ollama-real.log"
real.write_text("Sep 09 12:00:00 ollama: stop processing: n_tokens = 9, truncated = 1\n")
check("...but truncated = 1 IS", kinds(error_scan.scan([real])), ["read-truncated"])

print("\nA LEDGER STATUS IS A FIELD, NOT A WORD IN A LINE. 24 backup-failed")
print("findings included rows whose status was 'collected', which is success:")
ok_rows = error_scan.scan([runs(
    {"at": "1", "job": "collect-logs", "status": "collected", "detail": "59402 lines"},
    {"at": "2", "job": "nas-backup", "status": "verified", "detail": "byte-identical"},
    {"at": "3", "job": "night-queue", "status": "built", "detail": "delivered a branch"},
    {"at": "4", "job": "night-queue", "status": "finished", "detail": "reached its end"},
)])
check("healthy ledger rows produce nothing", ok_rows, [])

bad_rows = error_scan.scan([runs(
    {"at": "5", "job": "nas-backup", "status": "unreachable", "detail": "not mounted"},
    {"at": "6", "job": "nas-backup", "status": "verify-failed", "detail": "not identical"},
    {"at": "7", "job": "night-queue", "status": "died", "detail": "exited early"},
)])
check("and every unhealthy one is found", len(bad_rows), 3)

print("\nAND AN UNRECOGNISED STATUS STILL SURFACES -- enumerating the good is")
print("what makes this survive a shape nobody has seen yet:")
check("a status from the future is a finding",
      len(error_scan.scan([runs({"at": "8", "job": "x", "status": "quantum-flux"})])) >= 1, True)

print("\nTHE SCAN MUST NOT DROWN ITS OWN SIGNAL. 7 real findings inside 339 is")
print("a backlog nobody reads -- noise is not a cosmetic problem here:")
mixed_src = d / "mixed.log"
mixed_src.write_text(("stop processing: n_tokens = 5, truncated = 0\n" * 50)
                     + "PUSH_EXIT=141\n")
mx = error_scan.scan([mixed_src])
check("50 benign lines and 1 real fault yields exactly 1 finding", len(mx), 1)
check("and it is the real one", mx[0]["kind"], "silent-signal-death")



print(f"\n  {PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
