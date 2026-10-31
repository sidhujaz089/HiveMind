#!/usr/bin/env python3
"""A claim of in-flight work is pinned to a trace, or it is labelled UNVERIFIED -- cbc5a3e, t357.

Run:  python3 scripts/tests/test_claim_trace.py
Exit 0 if all cases pass, 1 otherwise.

WHAT THIS ADDS OVER test_console_reconcile. That fixture already pins the hard signal (a pid no
dispatch returned is caught; a pid a real dispatch returned is silent; a later recap is not a
fabrication). What it does NOT cover is the claim-pinning rule's ESCAPE, added by t357:

    a claim of in-flight work needs a verifiable trace AT THE CLAIM, or the label UNVERIFIED.

So the non-redundant half of this file is the escape, and the half that matters most is that the
escape is PER SENTENCE: a model honestly marks the one claim it cannot back ("UNVERIFIED: pid N,
no row"), and the escape must not become a hole that launders an unlabelled fabrication sitting
in a neighbouring sentence of the same reply.

DECAY TEST, the half that matters (docs/CHECKS.md rule 1, AGENTS.md rule 9). Case 3 is the
command that FAILS unless the escape is implemented: remove the UNVERIFIED handling from
console_reconcile.py and case 3 flips from a reported (soft, exit 0) finding to an unreported
(hard, exit 1) one. Adding the trace, or the label, is what turns the finding the right way.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "console_reconcile.py"

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: got {got!r}, want {want!r}")


def run(rows):
    """Write a thread and reconcile it in a temp tree. Returns (exit code, findings)."""
    with tempfile.TemporaryDirectory() as td:
        threads = Path(td) / "workspace" / "console" / "threads"
        threads.mkdir(parents=True)
        (threads / "t.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        # The script resolves THREADS from its own location, so run a copy rooted in the temp
        # tree rather than monkeypatching a module global (same seam as test_console_reconcile).
        (Path(td) / "scripts").mkdir(exist_ok=True)
        (Path(td) / "scripts" / "console_reconcile.py").write_text(
            SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        r = subprocess.run([sys.executable, str(Path(td) / "scripts" / "console_reconcile.py"),
                            "--json"], capture_output=True, text=True, timeout=180,
                           stdin=subprocess.DEVNULL)
        try:
            findings = json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            findings = []
        return r.returncode, findings


DISPATCH_RESULT = ("Dispatched. pid 777777, log workspace/console/dispatch-t357.log.\n"
                   "It runs in a sandbox and must make the existing suites pass.")


def main():
    if not SCRIPT.is_file():
        print(f"  [BAD]  {SCRIPT} does not exist")
        return 1

    # 1. A CLAIM WITH NO TRACE IS FLAGGED (the hard signal, as a claim/trace rule).
    rc, f = run([
        {"role": "jas", "at": "2026-09-18T00:00:00Z", "text": "dispatch the fixture"},
        {"role": "qwen", "at": "2026-09-18T00:00:10Z", "text": "Dispatched — pid 888888."},
    ])
    hard = [x for x in f if x["severity"] == "hard"]
    check("1. a claim with no trace is caught", len(hard), 1)
    check("1b. and it names the pid", hard[0]["pid"] if hard else None, "888888")
    check("1c. and the exit code fails", rc, 1)

    # 2. THE SAME CLAIM WITH A TRACE IS SILENT -- the pid came back from a real dispatch.
    rc, f = run([
        {"role": "jas", "at": "2026-09-18T00:00:00Z", "text": "dispatch the fixture"},
        {"role": "qwen", "at": "2026-09-18T00:00:10Z", "tool": "dispatch_coder",
         "args": {"brief": "x"}, "result": DISPATCH_RESULT},
        {"role": "qwen", "at": "2026-09-18T00:00:20Z", "text": "Dispatched — pid 777777."},
    ])
    check("2. the same claim with a verifiable trace is silent",
          len([x for x in f if x["severity"] == "hard"]), 0)
    check("2b. and the exit code passes", rc, 0)

    # 3. THE ESCAPE (t357): an unbacked claim LABELLED UNVERIFIED is an honest gap -- reported,
    #    not failed. This is the case that FAILS if the escape is not implemented.
    rc, f = run([
        {"role": "jas", "at": "2026-09-18T00:00:00Z", "text": "dispatch the fixture"},
        {"role": "qwen", "at": "2026-09-18T00:00:10Z",
         "text": "UNVERIFIED: pid 888888, dispatched, no row in the ledger."},
    ])
    soft = [x for x in f if x["severity"] == "soft" and x["pid"] == "888888"]
    check("3. an unbacked claim labelled UNVERIFIED is reported as an honest gap",
          len(soft), 1)
    check("3b. and it is NOT treated as a fabrication",
          len([x for x in f if x["severity"] == "hard"]), 0)
    check("3c. and the exit code passes (an honest gap is not a violation)", rc, 0)

    # 4. THE ESCAPE IS PER SENTENCE -- a label in one sentence must not launder an unlabelled
    #    fabrication in a neighbouring sentence of the same reply.
    rc, f = run([
        {"role": "jas", "at": "2026-09-18T00:00:00Z", "text": "where do things stand?"},
        {"role": "qwen", "at": "2026-09-18T00:00:10Z",
         "text": ("In flight: pid 555555 running. "
                  "UNVERIFIED: pid 666666 may have been dispatched, no row.")},
    ])
    hard = [x for x in f if x["severity"] == "hard"]
    check("4. the unlabelled fabrication is still caught", len(hard), 1)
    check("4b. and it is the unlabelled pid, not the labelled one",
          hard[0]["pid"] if hard else None, "555555")
    check("4c. the labelled one is the honest gap",
          len([x for x in f if x["severity"] == "soft" and x["pid"] == "666666"]), 1)
    check("4d. and the exit code fails (a real fabrication is present)", rc, 1)

    # 5. A BACKED PID AND A LABELLED UNBACKED PID TOGETHER: the backed one is silent, the
    #    labelled one is an honest gap, and nothing is a fabrication.
    rc, f = run([
        {"role": "jas", "at": "2026-09-18T00:00:00Z", "text": "dispatch the fixture"},
        {"role": "qwen", "at": "2026-09-18T00:00:10Z", "tool": "dispatch_coder",
         "args": {"brief": "x"}, "result": DISPATCH_RESULT},
        {"role": "qwen", "at": "2026-09-18T00:00:20Z",
         "text": "pid 777777 is in flight. UNVERIFIED: pid 888888 unconfirmed."},
    ])
    check("5. a backed pid and a labelled unbacked pid: no fabrication is reported",
          len([x for x in f if x["severity"] == "hard"]), 0)
    check("5b. the backed pid is not even an honest gap",
          len([x for x in f if x["pid"] == "777777"]), 0)
    check("5c. the labelled pid is the honest gap",
          len([x for x in f if x["severity"] == "soft" and x["pid"] == "888888"]), 1)
    check("5d. and the exit code passes", rc, 0)

    print(f"test_claim_trace: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
