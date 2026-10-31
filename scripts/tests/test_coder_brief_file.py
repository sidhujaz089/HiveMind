#!/usr/bin/env python3
"""coder.py's brief boundary: a path is not a task (t263).

WHY. --brief takes the task TEXT. A path is valid text, so passing one made the
path string the model's entire task, and the model did the only honest thing --
asked to read the file its task named -- until the attempts ran out. Seven
attempts on 2026-09-10. On 2026-09-14 two parallel_dispatch lanes spent 11,990s
and 13,129s this way and both ended UNPARSEABLE. Nothing downstream can tell a
path from a one-line task; only the argument boundary can.

Every case runs the REAL coder.py and stops it cheaply with a target suite that
does not exist, which coder.py refuses before any model is called. So no model
is needed, and the assertion is about what reached the task line, not about what
a parser was told.

BOTH DIRECTIONS. Refusing every --brief would pass the path assertions and break
every text caller -- probe_batch, scoped_batch, the console, the MCP dispatch --
so ordinary text must still get through.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CODER = ROOT / "projects" / "local-inference" / "coder.py"
STOP = ["--files", "scripts/error_scan.py", "--target", "scripts/tests/test_NOPE_t263.py",
        "--model", "none"]

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def run(*args):
    r = subprocess.run([sys.executable, str(CODER), *args, *STOP], capture_output=True,
                       text=True, timeout=300, cwd=str(ROOT))
    return r.returncode, r.stdout + r.stderr


def task_line(out):
    return next((l.strip() for l in out.splitlines() if l.strip().startswith("task:")), None)


def main():
    tmp = Path(tempfile.mkdtemp())
    brief = tmp / "brief.txt"
    brief.write_text("Widen the value side of the truncation signature.\nNothing else.\n")

    # ---- THE BUG: a path handed to --brief must not become the task
    rc, out = run("--brief", str(brief))
    report("a path given to --brief is refused", rc, 1)
    report("the refusal names --brief-file", "--brief-file" in out, True)
    report("no task line was produced from the path", task_line(out), None)

    # ---- the file door reads the file, whole
    rc, out = run("--brief-file", str(brief))
    report("--brief-file reaches the target check (exit 2), not a refusal", rc, 2)
    report("the task line is the file's CONTENT, not its path",
           task_line(out), "task: Widen the value side of the truncation signature.")
    report("the read is announced with its size", f"({len(brief.read_text())} chars, whole)"
           in out, True)

    # ---- the same brief through either door gets the same branch label
    label_file = next((l for l in out.splitlines() if "coder — brief-" in l), "")
    rc, out2 = run("--brief", brief.read_text())
    label_text = next((l for l in out2.splitlines() if "coder — brief-" in l), "")
    report("text and file doors hash the same content to the same label",
           label_file.split(" — ")[1] if " — " in label_file else None,
           label_text.split(" — ")[1] if " — " in label_text else "missing")

    # ---- ordinary text is untouched: every other caller passes text
    rc, out = run("--brief", "Fix the thing in error_scan.py")
    report("plain text via --brief still runs", rc, 2)
    report("plain text is the task", task_line(out), "task: Fix the thing in error_scan.py")

    # ---- edge refusals
    empty = tmp / "empty.txt"
    empty.write_text("   \n")
    rc, out = run("--brief-file", str(empty))
    report("an empty brief file is refused", rc, 1)
    rc, out = run("--brief-file", str(tmp / "missing.txt"))
    report("a missing brief file is refused", rc, 1)
    rc, out = run("--brief", "x", "--brief-file", str(brief))
    report("both doors at once is refused", rc, 1)

    # ---- ONE COPY OF A JOB AT A TIME (running_twin). A stand-in named coder.py sleeps with
    # identical arguments; the real coder.py must refuse to join it, must NOT refuse a
    # different model, and must NOT mistake its own `timeout` wrapper for a twin -- the
    # first version did exactly that and refused every wrapped dispatch with nothing else
    # running.
    import time as _t
    import os as _os
    # UNIQUE PER PROCESS. Two dispatches baselining at once ran this suite concurrently and each
    # saw the other sandbox's stand-in as a twin -- an undeclared baseline failure that made
    # coder.py refuse a real dispatch (p07, 2026-09-15T02:14Z).
    TWIN = f"twin-model-{_os.getpid()}"
    stand = tmp / "coder.py"
    stand.write_text("import time\ntime.sleep(20)\n")
    same = ["--brief-file", str(brief), "--files", "scripts/error_scan.py", "--target",
            "scripts/tests/test_NOPE_t263.py"]
    sp = subprocess.Popen([sys.executable, str(stand), *same, "--model", TWIN])
    try:
        _t.sleep(1)
        r = subprocess.run([sys.executable, str(CODER), *same, "--model", TWIN],
                           capture_output=True, text=True, timeout=300, cwd=str(ROOT))
        report("an identical live job is refused", "REFUSED: pid" in r.stdout + r.stderr, True)
        report("and it names the running pid", str(sp.pid) in r.stdout + r.stderr, True)
        r = subprocess.run([sys.executable, str(CODER), *same, "--model", "other-model"],
                           capture_output=True, text=True, timeout=300, cwd=str(ROOT))
        report("the same brief on a DIFFERENT model is not refused",
               "REFUSED: pid" in r.stdout + r.stderr, False)
    finally:
        sp.kill()
        sp.wait()
    r = subprocess.run(["timeout", "120", sys.executable, str(CODER), *same,
                        "--model", TWIN], capture_output=True, text=True, timeout=300,
                       cwd=str(ROOT))
    report("a dispatch under its own `timeout` wrapper does not refuse itself",
           "REFUSED: pid" in r.stdout + r.stderr, False)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
