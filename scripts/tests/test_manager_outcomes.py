#!/usr/bin/env python3
"""Every manager answer takes its task out of the manager's queue -- except no usable answer.

Jas, 2026-09-26: "If it's scoped why would it review it again? Then it should be in the Status
tab. If it's blocked it needs to be blocked for me or you. All of these should not warrant
another review." Model-free: decide() is pure; the release rule runs on a temp task file.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import manager_note  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


IMPL = {"kind": "implementation", "file": "scripts/widget.py", "target": "scripts/tests/test_w.py",
        "single_change": "x", "what_is_wrong": "y", "done_when": "test_w.py exits 0",
        "related_entries": ["z"]}
ORACLE = {"kind": "oracle", "file": "scripts/tests/test_w.py", "oracle_text": "pass\n"}


def ans(step, block=None):
    t = f"# t999\n\n**{step}** -- the answer.\n\nBody.\n"
    if block is not None:
        t += "\n```chunks\n" + json.dumps(block) + "\n```\n"
    return t


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        (repo / "scripts" / "tests").mkdir(parents=True)
        d = lambda t, r: (lambda x: x and x[:2])(manager_note.decide(t, r))
        why = lambda t: (manager_note.decide(t, repo) or ("", "", ""))[2]
        report("(a) already done -> held for Claude to verify",
               d(ans("(a) ALREADY DONE"), repo), ("queued", "claude"))
        # (b) is a decision above the Manager -> the technical seat, which asks Jas only about what he
        # wants (Jas, 2026-10-19)
        report("(b) needs a decision -> the technical seat", d(ans("(b) NEEDS JAS"), repo), ("queued", "claude"))
        report("(d) blocked -> blocked, held for Claude", d(ans("(d) BLOCKED"), repo),
               ("blocked", "claude"))
        report("(c) scoped with a valid block -> doing (the Status board)",
               d(ans("(c) SCOPE", [ORACLE, IMPL]), repo), ("doing", ""))
        # UPDATED 2026-10-08 (t790): an unbuildable scope goes back to scoping, not to Jas.
        report("(c) scoped in prose only -> back to scoping",
               d(ans("(c) SCOPE"), repo), ("queued", ""))
        report("  with a note saying why", "chunks" in why(ans("(c) SCOPE")), True)
        report("(c) scoped with a block bridge refuses -> back to scoping",
               d(ans("(c) SCOPE", [dict(IMPL, done_when="")]), repo), ("queued", ""))
        report("  and the note names the error",
               "done_when" in why(ans("(c) SCOPE", [dict(IMPL, done_when="")])), True)
        report("no answer at all -> stays queued (retry)", d("", repo), None)
        report("no declared step -> back to scoping, not Jas (2026-10-19)", d("# t999\n\nsome musing\n", repo),
               ("queued", ""))

    import taskstore
    with tempfile.TemporaryDirectory() as tmp:
        tasks = Path(tmp)
        taskstore.TASKS = tasks
        taskstore.QUEUE = tasks / "QUEUE.md"
        taskstore.LOCK = tasks / ".lock"
        (tasks / "QUEUE.md").write_text("1. t999 — x\n")
        f = tasks / "t999--x.md"
        f.write_text("---\nid: t999\ntitle: x\narea: a\nkind: k\nstatus: blocked\nneeds: claude\n"
                     "---\n\n## Notes\n")
        taskstore.set_meta("t999", needs="")
        head = f.read_text().split("\n---", 1)[0]
        report("releasing the hold on a blocked task makes it queued again",
               "status: queued" in head, True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
