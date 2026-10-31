#!/usr/bin/env python3
"""A NEEDS JAS answer stops placing its task in Jas's column once he releases the hold.

2026-09-25: 11 tasks Jas had released stayed in the Status board's needs-jas column, because
the stage came from the manager's answer alone. Built in a temporary repo; no model runs.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import task_board  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


ANSWER = "# t{n}\n\n**(b) NEEDS JAS** -- a question for him.\n" + "x" * 300 + "\n"


def task(repo, n, needs, sub=""):
    d = repo / "workspace" / "tasks" / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / f"t{n}--x.md").write_text(f"---\nid: t{n}\ntitle: x\nstatus: queued\nneeds: {needs}\n---\n")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        out = repo / "workspace" / "run-logs" / "scoping-queue" / "out"
        out.mkdir(parents=True)
        for n in (101, 102, 103, 104):
            (out / f"t{n}.md").write_text(ANSWER.format(n=n))
        task(repo, 101, "jas")
        task(repo, 102, "")
        task(repo, 103, "claude")
        task(repo, 104, "", sub="done")
        st = {k: v[0] for k, v in task_board.manager_stages(repo).items()}
        report("still held for Jas: in his column", st.get("t101"), "needs-jas")
        report("hold released: out of his column", st.get("t102"), None)
        report("handed to Claude: out of his column", st.get("t103"), None)
        report("finished task: out of his column", st.get("t104"), None)
        (out / "t105.md").write_text(ANSWER.format(n=105))
        st = {k: v[0] for k, v in task_board.manager_stages(repo).items()}
        report("no task file anywhere: the answer still stands", st.get("t105"), "needs-jas")
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        task(repo, 201, "jas")                # held, the manager never answered it
        task(repo, 202, "")
        cols = task_board.board(repo)
        got = [t for t, _ in cols["needs-jas"]]
        report("a hold with no answer is in his column (same rule as Review)", got, ["t201"])
        report("the held set is exactly needs: jas", task_board.held_for_jas(repo), {"t201"})
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
