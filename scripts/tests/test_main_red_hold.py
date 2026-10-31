#!/usr/bin/env python3
"""A suite red on MAIN holds every build until it is green (t650).

Every coder sandbox copies main, so a suite that is red there is in every baseline and refuses
every build. On 2026-10-01/02 that cost 20+ starts on one chunk: three stale suites, then
test_senior_station, then test_slot_events after systemd 259. Each retry spent ~5 min of
baseline to learn the same thing, because "refused on this exact code" was keyed on HEAD, and
log-only commits moved HEAD.

  * the suite a refusal names is read from THAT run's log (by pid), not the chunk's latest log
  * while it is red on main, main_red_hold() returns a reason and classify() starts nothing
  * once it is green -- or a later run of the chunk was accepted -- nothing is held
  * a suite that cannot be run (None) does not hold
  * code_state() is keyed on the scripts/ and projects/ trees, not on HEAD

Temporary log files only; red_of is a stub, so no suite is run.
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import dispatch_loop as D  # noqa: E402

fails = []


def ck(label, got, want):
    print(f"  [{'ok' if got == want else 'FAIL'}]    {label} -> {got!r}")
    if got != want:
        fails.append(f"{label}: expected {want!r}, got {got!r}")


def main():
    with tempfile.TemporaryDirectory(dir=REPO / "workspace") as d:
        d = Path(d)
        (d / "refused.log").write_text("  REFUSING TO START -- the sandbox baseline is not what is declared:\n"
                                       "    undeclared failure: test_slot_events.py\n")
        (d / "other.log").write_text("attempt 1 ... ACCEPTED\n")
        rel = lambda n: str((d / n).relative_to(REPO))
        st = lambda pid, log, chunk="p1": {"event": "started", "chunk": chunk, "pid": pid, "log": rel(log)}
        fin = lambda pid, ex, chunk="p1": {"event": "finished", "chunk": chunk, "pid": pid, "exit": ex,
                                           "kind": "production"}
        refused = [st(1, "refused.log"), fin(1, 2)]

        ck("the refusal's own log names the suite",
           D.refusal_blockers(refused), {"test_slot_events.py": "p1"})
        ck("a later run's log is not read for an earlier refusal",
           D.refusal_blockers([st(1, "refused.log"), fin(1, 2), st(2, "other.log", "p2")]),
           {"test_slot_events.py": "p1"})
        held = D.main_red_hold(refused, lambda t: True)
        ck("red on main: every build is held", bool(held and "test_slot_events.py" in held), True)
        ck("green on main: nothing is held", D.main_red_hold(refused, lambda t: False), None)
        ck("cannot be run: nothing is held", D.main_red_hold(refused, lambda t: None), None)
        ck("a later accepted run clears the refusal",
           D.main_red_hold(refused + [st(3, "other.log"), fin(3, 0)], lambda t: True), None)
        # Declared in the ceiling since the refusal: every baseline now allows it, so it holds nothing.
        import json as _j
        allowed = _j.loads((REPO / "workspace" / "test-failures-ceiling.json").read_text())["allowed_to_fail"]
        (d / "declared.log").write_text(f"    undeclared failure: {allowed[0]}\n")
        ck("a suite declared in the ceiling since its refusal holds nothing",
           D.main_red_hold([st(4, "declared.log", "p4"), fin(4, 2, "p4")], lambda t: True), None)

    # NEEDS GIT: coder.py's sandbox has no .git, and this suite red there refused every build.
    if (REPO / ".git").exists():
        ck("code_state is keyed on code trees, not HEAD",
           (D.code_state() or "").split(":")[0].count("+"), 1)
    else:
        print("  [skip]  code_state: no .git here (the coder sandbox) -- checked on main")

    for x in fails:
        print("FAIL:", x)
    print("test_main_red_hold:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
