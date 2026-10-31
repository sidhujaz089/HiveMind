#!/usr/bin/env python3
"""The Q16 supervisor: one decision per failure, acted on through artifacts only.

The old loop's defects are the assertions (all observed 2026-09-14): it re-decided the same
stale error every 15 minutes (-> one decision per failure, never re-read); it acted on missing
files and a missing heartbeat directory and did nothing (-> every action lands in an artifact
the fixture can read back); its unknown decision values were silently ignored (-> an unreadable
decision is recorded as unreadable).
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))

import supervise as SV     # noqa: E402
import dispatch_loop as DL  # noqa: E402
import taskstore as ts     # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {str(got)[:70]}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {str(got)[:70]}")


def main():
    tmp = Path(tempfile.mkdtemp())
    real = (SV.LEDGER, SV.BRIEFS, SV.REPO)
    SV.LEDGER, SV.BRIEFS, SV.REPO = tmp / "supervision.jsonl", tmp / "briefs", tmp
    SV.BRIEFS.mkdir()
    (SV.BRIEFS / "b.txt").write_text("Change X in a.py. DONE WHEN scripts/tests/test_a.py exits 0.\n")
    sha = DL.brief_sha.__wrapped__ if hasattr(DL.brief_sha, "__wrapped__") else None
    import hashlib
    sha = hashlib.sha256((SV.BRIEFS / "b.txt").read_bytes()).hexdigest()

    f1 = {"at": "T1", "event": "finished", "kind": "production", "chunk": "pA", "model": "gemma",
          "brief": "b.txt", "brief_sha256": sha, "exit": 1}
    f2 = {**f1, "at": "T2", "model": "q16"}
    refusal = {**f1, "at": "T3", "exit": 2}
    preflight = {**f1, "at": "T4", "kind": "preflight", "exit": 2}
    try:
        # ---- what needs a decision
        p = SV.pending([f1, f2, refusal, preflight], [])
        report("only the NEWEST failure per brief is pending", [x["at"] for x in p], ["T2"])
        report("refusals and preflight objections are not failures to supervise",
               SV.pending([refusal, preflight], []), [])
        report("a decided failure is never re-read (the stale-error loop)",
               SV.pending([f2], [{"failure": SV.key(f2)}]), [])

        # ---- decisions are validated
        report("a valid decision parses", SV.parse_decision('x {"decision": "SWITCH", "why": "y"} z')["decision"], "SWITCH")
        report("an unknown decision is unreadable, not guessed", SV.parse_decision('{"decision": "RESTART"}'), None)
        report("prose is unreadable", SV.parse_decision("I think switch"), None)
        report("two conflicting decisions are refused, not merged",
               SV.parse_decision('{"decision": "SWITCH"} or {"decision": "PARK"}'), None)

        # ---- REWRITE-BRIEF archives, rewrites, and changes the sha (so Q16 re-reviews it)
        new = "Change X in a.py, keeping Y. DONE WHEN scripts/tests/test_a.py exits 0."
        out = SV.execute(f2, {"decision": "REWRITE-BRIEF", "new_brief": new})
        report("a rewrite is carried out", out.startswith("brief rewritten"), True)
        report("the brief on disk is the new one", (SV.BRIEFS / "b.txt").read_text().strip(), new)
        report("the old brief is archived, not lost",
               any("Change X in a.py. DONE" in f.read_text() for f in (SV.BRIEFS / "archive").iterdir()), True)
        report("an identical rewrite is refused",
               SV.execute(f2, {"decision": "REWRITE-BRIEF", "new_brief": new}).startswith("REFUSED"), True)
        report("an empty rewrite is refused",
               SV.execute(f2, {"decision": "REWRITE-BRIEF", "new_brief": ""}).startswith("REFUSED"), True)

        # ---- HARNESS-BUG files a real task with the evidence
        tasks = tmp / "tasks"
        (tasks / "done").mkdir(parents=True)
        (tasks / "QUEUE.md").write_text("# QUEUE.md\n\n")
        saved = (ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK)
        ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK = tasks, tasks / "done", tasks / "QUEUE.md", tasks / ".lock"
        try:
            other = ts.add("other work already queued", area="workspace")
            out = SV.execute(f2, {"decision": "HARNESS-BUG", "bug_title": "health probe counts zero lanes",
                                  "why": "busy read as down", "bug_evidence": "dispatches_running = 0"}, store=ts)
            filed = list(tasks.glob("t*--harness-*.md"))
            report("a harness bug is filed as a task", len(filed), 1)
            body = filed[0].read_text() if filed else ""
            report("titled as a harness bug", "title: HARNESS: health probe counts zero lanes" in body, True)
            report("carrying its evidence", "dispatches_running = 0" in body, True)
            # Not held (Jas, 2026-09-27): a held HARNESS task was invisible to the manager, which
            # skips held tasks -- "I moved new harness tasks to the top and Q4 skipped them".
            report("left for the manager, not held for Claude", "needs: claude" in body, False)
            # Jas, 2026-10-19: a harness bug in production goes before the other tasks
            q = re.findall(r"^\d+\. (t\d+)", (tasks / "QUEUE.md").read_text(), re.M)
            hid = filed[0].name.split("--")[0] if filed else "?"
            report("filed at the TOP of the queue, above earlier work", (q[:2] == [hid, other]), True)
            ts.add("newer work put on top", area="workspace", at_top=True)
            # t564: the same chunk failing again adds a note to the open task, not a new task
            out2 = SV.execute(f2, {"decision": "HARNESS-BUG", "bug_title": "a different title",
                                   "why": "again", "bug_evidence": "second failure"}, store=ts)
            report("a second failure of the same chunk is noted, not re-filed",
                   (len(list(tasks.glob("t*--harness-*.md"))), out2.startswith("noted on")), (1, True))
            report("and the note carries the new evidence",
                   "second failure" in (list(tasks.glob("t*--harness-*.md"))[0].read_text()), True)
            q = re.findall(r"^\d+\. (t\d+)", (tasks / "QUEUE.md").read_text(), re.M)
            report("a noted harness task moves back to the top", (q[:1] == [hid]), True)
        finally:
            ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK = saved

        # ---- PARK is honoured by the scheduler, and only for that exact brief
        SV.record({"failure": SV.key(f2), "chunk": "pA", "brief_sha256": "s1", "decision": "PARK"})
        report("a supervisor PARK parks the brief in dispatch_loop", DL.parked("pA", "s1", [])[0], True)
        report("an edited brief (new sha) is not parked", DL.parked("pA", "s2", [])[0], False)

        # ---- the supervisor never kills or spawns
        src = (ROOT / "scripts" / "supervise.py").read_text()
        import ast
        names = {n.id if isinstance(n, ast.Name) else n.attr for n in ast.walk(ast.parse(src))
                 if isinstance(n, (ast.Name, ast.Attribute))}
        imports = {a.name for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.Import, ast.ImportFrom))
                   for a in n.names}
        report("the supervisor's CODE never kills, spawns or shells out",
               bool({"kill", "Popen", "system", "killpg"} & names) or "subprocess" in imports, False)

        # t398: the evidence is the FAILED run's log, matched by pid -- not the newest run of the chunk.
        (tmp / "old.log").write_text("  attempt 1 … asked for 1 file(s)\n  attempt 2 … tests fail\n")
        (tmp / "new.log").write_text("[just started]\n")
        rows = [{"event": "started", "chunk": "p9", "model": "m", "brief_sha256": sha, "pid": 1, "log": "old.log"},
                {"event": "finished", "chunk": "p9", "model": "m", "brief_sha256": sha, "pid": 1, "exit": 1},
                {"event": "started", "chunk": "p9", "model": "m", "brief_sha256": sha, "pid": 2, "log": "new.log"}]
        ev = SV.evidence(dict(rows[1], brief="b.txt"), rows, [{"chunk": "p9", "files": "a.py",
                                                                     "target": "t"}])
        report("t398: the attempt trail comes from the run that failed, not a newer one",
               "attempt 2 … tests fail" in ev and "(none recorded)" not in ev, True)
    finally:
        SV.LEDGER, SV.BRIEFS, SV.REPO = real

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
