#!/usr/bin/env python3
"""console_reconcile.py must catch a claimed action with no call behind it -- and only that.

Run:  python3 scripts/tests/test_console_reconcile.py
Exit 0 if all cases pass, 1 otherwise.

WHY EACH CASE EXISTS. The reconciler is a liar-detector pointed at a model, so the two ways it
can be useless are opposite and both were hit while writing it:

  * MISSING a fabrication makes it decorative.
  * CRYING WOLF gets it switched off inside a week (AGENTS.md rule 9), and its first version
    did exactly that -- it compared a reply's pids against calls in THAT TURN ONLY, so a
    legitimate status recap ("t238 -- pid 379151, running") naming a pid dispatched days
    earlier was reported as a fabrication. 379151 was real. Case 3 pins that.

The fixtures are the REAL shapes from workspace/console/threads/smoke.jsonl on 2026-09-14, not
invented ones -- writing-oracles-for-delegation: build fixtures from real artefacts, because a
synthetic one encodes what you imagined the data looks like.
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
    """Write a thread and reconcile it. Returns (exit code, findings)."""
    with tempfile.TemporaryDirectory() as td:
        threads = Path(td) / "workspace" / "console" / "threads"
        threads.mkdir(parents=True)
        (threads / "t.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        # The script resolves THREADS from its own location, so run a copy rooted in the
        # temp tree rather than monkeypatching a module global.
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


DISPATCH_RESULT = ("Dispatched. pid 379151, log workspace/console/dispatch-x.log.\n"
                   "It runs in a sandbox and must make the existing suites pass before it "
                   "delivers to a review branch.")


def main():
    if not SCRIPT.is_file():
        print(f"  [BAD]  {SCRIPT} does not exist")
        return 1

    # 1. A FABRICATED PID IS CAUGHT. The real 2026-09-14 line, verbatim.
    rc, f = run([
        {"role": "jas", "at": "2026-09-14T13:16:00Z", "text": "Make a task that just says test"},
        {"role": "qwen", "at": "2026-09-14T13:16:10Z",
         "text": "Dispatched — **pid 153120**.\n\nTask: a minimal task named test."},
    ])
    hard = [x for x in f if x["severity"] == "hard"]
    check("1. a pid no dispatch returned is caught", len(hard), 1)
    check("1b. and it names the pid", hard[0]["pid"] if hard else None, "153120")
    check("1c. and the exit code fails", rc, 1)
    check("1d. and the evidence is the WHOLE reply, not a prefix",
          "minimal task named test" in (hard[0]["evidence"] if hard else ""), True)

    # 2. A REAL PID IS SILENT -- the pid came back from an actual dispatch in the same turn.
    rc, f = run([
        {"role": "jas", "at": "2026-09-13T03:12:00Z", "text": "dispatch the escalation fixture"},
        {"role": "qwen", "at": "2026-09-13T03:12:50Z", "tool": "dispatch_coder",
         "args": {"brief": "x", "files": "scripts/x.py"}, "result": DISPATCH_RESULT},
        {"role": "qwen", "at": "2026-09-13T03:13:00Z",
         "text": "Dispatched — pid 379151 — the escalation fixture."},
    ])
    check("2. a pid a real dispatch returned is NOT reported",
          len([x for x in f if x["severity"] == "hard"]), 0)
    check("2b. and the exit code passes", rc, 0)

    # 3. THE CASE THAT CRIED WOLF. A recap in a LATER turn, naming a pid dispatched earlier.
    #    The first version reported this; 379151 was real and the finding was false.
    rc, f = run([
        {"role": "jas", "at": "2026-09-13T03:12:00Z", "text": "dispatch it"},
        {"role": "qwen", "at": "2026-09-13T03:12:50Z", "tool": "dispatch_coder",
         "args": {"brief": "x", "files": "scripts/x.py"}, "result": DISPATCH_RESULT},
        {"role": "qwen", "at": "2026-09-13T03:13:00Z", "text": "Dispatched — pid 379151."},
        {"role": "jas", "at": "2026-09-13T03:54:00Z", "text": "where do things stand?"},
        {"role": "qwen", "at": "2026-09-13T03:54:59Z",
         "text": "In flight right now:\n- t238 — pid 379151, running."},
    ])
    check("3. a later recap of an EARLIER real dispatch is not a fabrication",
          len([x for x in f if x["severity"] == "hard"]), 0)

    # 4. A FABRICATION IN A RECAP IS STILL CAUGHT -- the leniency of case 3 must not be a hole.
    rc, f = run([
        {"role": "jas", "at": "2026-09-13T03:12:00Z", "text": "dispatch it"},
        {"role": "qwen", "at": "2026-09-13T03:12:50Z", "tool": "dispatch_coder",
         "args": {"brief": "x", "files": "scripts/x.py"}, "result": DISPATCH_RESULT},
        {"role": "jas", "at": "2026-09-13T03:54:00Z", "text": "where do things stand?"},
        {"role": "qwen", "at": "2026-09-13T03:54:59Z",
         "text": "In flight:\n- pid 379151, running.\n- pid 575064, running."},
    ])
    hard = [x for x in f if x["severity"] == "hard"]
    check("4. the invented pid in a recap is still caught", len(hard), 1)
    check("4b. and it is the invented one, not the real one",
          hard[0]["pid"] if hard else None, "575064")

    # 5. A TURN THAT CLAIMS NOTHING IS SILENT. No pid, no tool name, no finding -- otherwise
    #    every ordinary answer becomes a finding and the report is unreadable.
    rc, f = run([
        {"role": "jas", "at": "2026-09-14T09:00:00Z", "text": "what is gate 3?"},
        {"role": "qwen", "at": "2026-09-14T09:00:30Z",
         "text": "Gate 3 is quarantine. It is NOT closed; evidence.py's only consumer is "
                 "phase0_numbers.py, which no service runs."},
    ])
    check("5. an ordinary answer produces no finding at all", len(f), 0)
    check("5b. and exits 0", rc, 0)

    # 6. A TORN ROW MUST NOT STOP THE SCAN. error_scan's rule: a scanner that dies on row 4 of
    #    900 has found nothing and said nothing.
    with tempfile.TemporaryDirectory() as td:
        threads = Path(td) / "workspace" / "console" / "threads"
        threads.mkdir(parents=True)
        (threads / "t.jsonl").write_text(
            '{"role": "jas", "text": "go"}\n'
            '{not json at all\n'
            '{"role": "qwen", "at": "2026-09-14T13:16:10Z", "text": "Dispatched pid 153120."}\n',
            encoding="utf-8")
        (Path(td) / "scripts").mkdir(exist_ok=True)
        (Path(td) / "scripts" / "console_reconcile.py").write_text(
            SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        r = subprocess.run([sys.executable, str(Path(td) / "scripts" / "console_reconcile.py"),
                            "--json"], capture_output=True, text=True, timeout=180,
                           stdin=subprocess.DEVNULL)
        f = json.loads(r.stdout or "[]")
        check("6. a malformed row does not stop the scan; the claim after it is still caught",
              len([x for x in f if x["severity"] == "hard"]), 1)

    # 7. THE TOOL NAMES ARE DISCOVERED, NOT LISTED. If this returns nothing, the soft signal
    #    is dead and nobody would notice, because a dead check looks exactly like a clean one.
    sys.path.insert(0, str(REPO / "scripts"))
    import console_reconcile as cr
    names = cr.tool_names()
    check("7. tool names are read from console.py", "note_task" in names and "dispatch_coder" in names, True)
    check("7b. and there are several of them", len(names) >= 5, True)

    print(f"test_console_reconcile: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
