#!/usr/bin/env python3
"""t4641 (2026-10-26): the sweeps on session start must be READ-ONLY, and must say when they cannot look.

stale_assignments.py and error_unknowns.py were on no protocol: correct, tested, run by nobody. Jas's
disposition was "one session_start.py line each (cheap, read-only, no new unit)". Both of them WRITE
when run for real -- stale_assignments `--apply` reassigns tasks, error_unknowns files one Manager item
per new error shape -- so the property this suite pins is that session start never runs the writing form:

  1. the section runs each script in its dry mode, and `--apply` never appears in an argv;
  2. every script it runs is one of the two that were wired, so a later edit cannot quietly add a third;
  3. a script that raises OSError is REPORTED and does not take session start down, and is never printed
     as a clean result (the empty-output-is-never-evidence rule).

Hermetic: subprocess.run is stubbed, so no task is read, moved or filed by this suite.
"""
import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_start as ss  # noqa: E402

FAILS = []
WIRED = {"stale_assignments.py", "error_unknowns.py"}
# The flags each script MUST carry when session start runs it. error_unknowns files a Manager item
# per new shape unless it is given --dry-run, so the flag is the whole safety property.
REQUIRED_FLAGS = {"stale_assignments.py": [], "error_unknowns.py": ["--dry-run"]}


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


class FakeProc:
    def __init__(self, rc=0, out="  0 stale assignment(s) (dry run)\n", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def runner(seen, rc=0, out=None, err="", raises=None):
    def run(argv, **kw):
        seen.append(list(argv))
        if raises:
            raise raises
        return FakeProc(rc, out if out is not None else "  0 stale assignment(s) (dry run)\n", err)
    return run


def section(seen, **kw):
    """Call the section with subprocess.run stubbed. section()/print are captured by the caller."""
    real = ss.subprocess
    try:
        ss.subprocess = type("S", (), {"run": staticmethod(runner(seen, **kw)),
                                       "TimeoutExpired": TimeoutError,
                                       "CalledProcessError": Exception})()
        ss.section = lambda title: None
        ss._work_drift_section()
    finally:
        ss.subprocess = real


def main():
    seen = []
    with contextlib.redirect_stdout(io.StringIO()):
        section(seen)
    called = [Path(a[1]).name for a in seen]
    check("the section runs the wired sweeps", set(called) == WIRED, called)
    check("no writing flag is ever passed (--apply would reassign tasks)",
          not any("--apply" in a for a in seen), seen)
    for name in sorted(WIRED):
        need = REQUIRED_FLAGS[name]
        check(f"{name} is run with {need or 'no writing flag'}",
              any(Path(a[1]).name == name and all(f in a for f in need) for a in seen), seen)

    # 3. A script that cannot run must be reported, not read as clean, and must not crash session start.
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            section([], raises=OSError("boom"))
        check("a script that cannot run does not raise out of session start", True)
    except Exception as e:                                         # noqa: BLE001 -- that is the test
        check("a script that cannot run does not raise out of session start", False, repr(e))
    printed = buf.getvalue().splitlines()
    check("  and it is reported as COULD NOT RUN, not as a clean result",
          any("COULD NOT RUN" in l for l in printed), printed)

    # 4. A zero exit with NO output is not evidence of anything.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        section([], out="", rc=0)
    printed = buf.getvalue().splitlines()
    check("empty output is reported, never printed as a result",
          any("not read as clean" in l for l in printed), printed)

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
