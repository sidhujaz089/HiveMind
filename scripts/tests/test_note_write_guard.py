#!/usr/bin/env python3
"""Fail-first oracle for t1581 + t1588: the note WRITER is where these two end.

WHY BOTH HERE. On 2026-10-24 two records could not be committed at all because they QUOTED a
test key (the pre-commit hook scans the diff, and a record that quotes a credential is
indistinguishable from one that contains it), and a Manager note written at 22:0xZ named a
script it proposed -- which dangled from the moment it was written. Both are the same endpoint:
a note is written with a key shape in it, or with a path that resolves to nothing, and the
repair happens days later by hand.

So: redact at the writer (t1588), and declare SCRATCH paths at the writer (t1581) -- but only
the class that is provably a record. A note naming a probe under /tmp or ~/.cache is recording
where it worked; a note naming a repository path that does not exist is making a claim the
check exists to catch, and that is deliberately still left to the check.

Isolation: every path is a temp one. The live store, the live registry and the live private
ledger are never touched.
"""
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "app-repo"))
sys.path.insert(0, str(REPO / "scripts"))

import check_workspace as cw  # noqa: E402  -- its pattern is the independent oracle
import taskstore as ts  # noqa: E402

PASSED = FAILED = 0
FAKE_KEY = "sk-ant-" + "A" * 30


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="note-guard-"))
    try:
        ts.TASKS = tmp / "tasks"
        ts.TASKS.mkdir()
        ts.QUEUE = ts.TASKS / "QUEUE.md"
        ts.LOCK = ts.TASKS / ".lock"
        ts.REGISTRY = tmp / "EXTERNAL-POINTERS.md"
        ts.SECRET_HITS = tmp / "private" / "secret-hits.jsonl"
        ts.REGISTRY.write_text("# pointers\n", encoding="utf-8")

        ts.add("a task for the note guard", "workspace", "t-guard")
        task_file = [p for p in ts.TASKS.glob("t*--*.md")][0]
        tid = task_file.name.split("--")[0]
        print(f"  (signal) task under test: {tid}")

        # 0. The pattern this writer uses is the checker's. A fallback exists for trees with no
        #    scripts/ (test_task_cancel.py's fixture copies only projects/app-repo), and drift
        #    between the two is exactly how a fix in one place leaves the other red (t1563).
        check("the writer's pattern is the checker's", ts._secret_pattern().pattern, cw.SECRET_PATTERN)
        check("  and the fallback is identical to it", ts.SECRET_PATTERN_FALLBACK, cw.SECRET_PATTERN)

        # 1. t1588: a note carrying a key shape is redacted BEFORE it lands.
        ts.add_note(tid, f"the probe key was {FAKE_KEY} and the run said so", who="dsh-harness")
        body = task_file.read_text(encoding="utf-8")
        check("the note on disk carries no key shape", bool(__import__("re").search(cw.SECRET_PATTERN, body)), False)
        check("  and it carries the writer's marker", "[redacted:secret-pattern]" in body, True)
        hits = [l for l in ts.SECRET_HITS.read_text(encoding="utf-8").splitlines() if l.strip()]
        check("  and the raw value is preserved once", len(hits), 1)
        check("  in the private ledger, tagged", '"sensitivity": "secret-shaped"' in hits[0], True)

        # 2. t1581: a scratch path the note names is declared, so the check stays green.
        ts.add_note(tid, "wrote the implementation to `/tmp/t9999probe/impl_block.py` (gone now)",
                    who="dsh-harness")
        reg = ts.REGISTRY.read_text(encoding="utf-8")
        check("a /tmp path in a note is declared", "/tmp/t9999probe/impl_block.py" in reg, True)
        check("  and the row names the note it came from", task_file.name in reg, True)
        check("  and says a machine wrote it", "when the note was written" in reg.lower(), True)

        # 3. Idempotent: the same path in a second note is not declared twice.
        ts.add_note(tid, "again: `/tmp/t9999probe/impl_block.py`", who="dsh-harness")
        reg2 = ts.REGISTRY.read_text(encoding="utf-8")
        check("the same path is declared once", reg2.count("/tmp/t9999probe/impl_block.py"), 1)

        # 4. A repository path that does not exist is NOT auto-declared: that is the check's job,
        #    and an auto-declaration there would silence a real claim.
        before = ts.REGISTRY.read_text(encoding="utf-8")
        ts.add_note(tid, "the oracle is `scripts/tests/test_this_does_not_exist.py`", who="dsh-harness")
        check("a missing REPO path is left for the check to catch",
              ts.REGISTRY.read_text(encoding="utf-8"), before)

        # 5. A path that exists is not declared either.
        ts.add_note(tid, "see `scripts/check_workspace.py`", who="dsh-harness")
        check("an existing path is not declared",
              ts.REGISTRY.read_text(encoding="utf-8"), before)

        # 6. The forgery guard is untouched: this endpoint speaks for the harness, never for Jas.
        try:
            ts.add_note(tid, "x", who="jas")
            check("a note attributed to jas is still refused", "no exception", "Forged")
        except ts.Forged:
            check("a note attributed to jas is still refused", "Forged", "Forged")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
