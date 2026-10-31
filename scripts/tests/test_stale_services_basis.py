#!/usr/bin/env python3
"""t2669: the staleness gate compares a service with the code IT LOADS, and says so.

Measured 2026-10-25 (dsh-harness): every commit touching ANY file under scripts/ made EVERY
service whose ExecStart runs a script go stale, because the gate compared the service's start
time with the newest mtime in the file's PARENT DIRECTORY. memory-guard.service was reported
stale because check_stale_services.py changed two minutes ago, and memory-guard does not import it.

The import-closure comparison itself is already in place (commit bfd5d7e51: imported_locals and
newest_change in scripts/check_stale_services.py, pinned by scripts/tests/test_stale_services.py).
This suite pins the two halves of the fix that are NOT in the file:

  1. the printed line SAYS which basis was used, and the [ok] line stops claiming the parent
     directory as the basis when the import closure is what was compared;
  2. when the closure cannot be computed -- the entry script's source will not parse -- the gate
     FALLS BACK to that script's directory and says that it did.

Every assertion is behavioural: it reads the lines css.main() prints, with systemctl, /proc and
start times faked, and every script living in a temp directory. Nothing is written inside the
repo. There is no pytest here: the __main__ block at the bottom runs the checks and exits non-zero.
"""
import contextlib
import io
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_stale_services as css  # noqa: E402

PASSED = FAILED = 0

UNITS = ("a.service", "c.service", "e.service")
EXECS = {}


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


class _R:
    def __init__(self, out=""):
        self.returncode = 0
        self.stdout = out
        self.stderr = ""


def fake_sh(*a):
    """Only the two systemctl calls user_services() makes. Anything else is a finding."""
    j = " ".join(str(x) for x in a)
    if "list-units" in j:
        return _R("".join(f"{n} loaded active running unit {n}\n" for n in UNITS))
    if "ExecStart" in j:
        name = a[a.index("show") + 1]
        return _R(EXECS[name])
    raise AssertionError(f"fake systemctl got a call it was not written to answer: {j}")


def run_main():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        css.main([])
    return buf.getvalue()


def main():
    tmp = Path(tempfile.mkdtemp())
    old = time.time() - 7200          # 2 h ago -- OLDER than the 10-minute start time
    fresh = time.time() - 60          # 1 min ago -- NEWER than it

    d1 = tmp / "svc"
    d1.mkdir()
    (d1 / "a.py").write_text("import helper\nx = 1\n")
    (d1 / "helper.py").write_text("y = 2\n")
    (d1 / "b.py").write_text("z = 3\n")        # NOT imported by a.py
    for n in ("a.py", "helper.py"):
        os.utime(d1 / n, (old, old))
    os.utime(d1 / "b.py", (fresh, fresh))

    d2 = tmp / "svc2"
    d2.mkdir()
    (d2 / "c.py").write_text("def broken(:\n")  # source that will not parse
    (d2 / "d.py").write_text("q = 9\n")
    os.utime(d2 / "c.py", (old, old))
    os.utime(d2 / "d.py", (fresh, fresh))

    d3 = tmp / "svc3"
    d3.mkdir()
    (d3 / "e.py").write_text("import f\n")
    (d3 / "f.py").write_text("w = 7\n")
    os.utime(d3 / "e.py", (old, old))
    os.utime(d3 / "f.py", (fresh, fresh))

    EXECS.update({
        "a.service": f"/usr/bin/python3 {d1 / 'a.py'}",
        "c.service": f"/usr/bin/python3 {d2 / 'c.py'}",
        "e.service": f"/usr/bin/python3 {d3 / 'e.py'}",
    })

    real = (css.REPO, css.sh, css.started_at, css.proc_table, css.self_waiting_processes)
    css.REPO = tmp                        # the root user_services() insists ExecStart live under
    css.sh = fake_sh
    css.started_at = lambda name: 600.0   # every service started 10 minutes ago
    css.proc_table = lambda: []           # no stray processes, so /proc is never walked
    css.self_waiting_processes = lambda *a, **k: []
    try:
        out1 = run_main()
        os.utime(d1 / "a.py", (fresh, fresh))
        out2 = run_main()
    finally:
        (css.REPO, css.sh, css.started_at, css.proc_table,
         css.self_waiting_processes) = real
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("A service is NOT stale when only an unimported sibling changed:")
    report("a.service is not reported stale (b.py is not in its import closure)",
           "STALE] a.service" in out1, False)
    report("  its line does not claim the parent directory as the basis",
           "svc/" in out1, False)
    report("  the [ok] line says which basis WAS used (the import closure)",
           "import closure of a.py" in out1, True)

    print("\nA service whose entry script will not parse falls back to its directory:")
    report("c.service is reported stale (its directory's d.py changed after it started)",
           "STALE] c.service" in out1, True)
    report("  naming the sibling that made it stale", "d.py changed" in out1, True)
    report("  and saying the directory was the basis used", "basis: directory" in out1, True)
    report("  and NOT claiming a closure it could not compute",
           "import closure of c.py" in out1, False)

    print("\nThe import closure still catches the case the gate exists for:")
    report("e.service is reported stale (it imports f.py, which changed)",
           "STALE] e.service" in out1, True)
    report("  and says the closure was the basis used",
           "basis: import closure of e.py" in out1, True)

    print("\nTouching the entry point itself makes the service stale:")
    report("a.service is reported stale once a.py changes", "STALE] a.service" in out2, True)
    report("  naming a.py as the file that changed", "a.py changed" in out2, True)
    report("  and saying the closure was the basis used",
           "basis: import closure of a.py" in out2, True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
