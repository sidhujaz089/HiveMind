#!/usr/bin/env python3
"""Fixtures for the agent harness log.

The log is the harness's only real product, so the assertions are about the log rather than
about the model. Two properties matter:

  1. Untrusted content is RECORDED as untrusted. If an injection ever succeeds, the evidence
     of what was fed in must exist independently of the model's account of itself -- Nemotron
     forged a </think> tag on 2026-08-24, so model-controlled text is not a witness.
  2. A quiet edit to the log is DETECTABLE. Not prevented -- nothing local can prevent it --
     but a single rewritten line must fail a check instead of passing unnoticed.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import harness  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def main():
    tmp = Path(tempfile.mkdtemp())
    harness.LOGS = tmp
    try:
        print("The chain:")
        log = harness.Log("t")
        log.write("session_start", tier="A")
        log.write("context_injection", source="x.txt", trust="untrusted", text="payload")
        log.write("response", answer="fine")
        report("three records", log.count(), 3)
        report("chain intact", log.verify()[0], True)
        report("first record links to genesis", log.records()[0]["prev"], "genesis")

        print("\nA quiet edit must be caught:")
        lines = log.path.read_text().splitlines()
        r = json.loads(lines[1]); r["text"] = "(hidden)"
        lines[1] = json.dumps(r)
        log.path.write_text("\n".join(lines) + "\n")
        ok, detail = log.verify()
        report("verify() REFUSES a tampered log", ok, False)
        report("and it names where", "seq 2" in detail or "record 2" in detail, True)
        # Exit code too, since that is what a caller acts on. Refusing quietly is not refusing.
        report("verify exits non-zero on a tampered log",
               0 if log.verify()[0] else 1, 1)

        print("\nAnd a MISSING log must refuse rather than pass:")
        gone = harness.Log("never-existed")
        ok2, detail2 = gone.verify()
        report("missing log is REFUSED, not 'intact'", ok2, False)
        report("and it says nothing was verified", "nothing was verified" in detail2, True)

        print("\nAppend-only: existing records are never rewritten:")
        log2 = harness.Log("t2")
        log2.write("a", v=1)
        first = log2.path.read_text().splitlines()[0]
        log2.write("b", v=2)
        report("the first line is byte-identical after a later write",
               log2.path.read_text().splitlines()[0], first)
        report("sequence assigned by the harness, not the caller",
               [r["seq"] for r in log2.records()], [0, 1])

        print("\nUntrusted content is labelled in the record itself:")
        recs = harness.Log("t").records()
        inj = next(r for r in recs if r["kind"] == "context_injection")
        report("marked untrusted", inj["trust"], "untrusted")
        report("source recorded", inj["source"], "x.txt")

        print("\nThe wrapper states the boundary to the model:")
        w = harness.UNTRUSTED_WRAPPER.format(content="X")
        report("says it is data, not instructions", "DATA, not" in w, True)
        report("has an explicit end marker", "END UNTRUSTED CONTENT" in w, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
