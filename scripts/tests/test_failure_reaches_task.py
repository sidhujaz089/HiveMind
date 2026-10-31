#!/usr/bin/env python3
"""A production failure is written onto the task, and one attempt is enough.

Jas, 2026-09-16, two halves of one idea:

  "can all the error messages and any data related to the production of a specific task be
   recorded within the task itself? That way, the next time the task comes by, we can look at
   the failures and learn."

  "we shouldn't have three attempts at a task before failure. It should just be one: note the
   reason, put it back at the beginning of production, and let it go through analysis again."

The second only makes sense because of the first. coder.py is invoked IDENTICALLY on every
attempt -- the switch history goes to the ledger and is never shown to the model -- so attempts
two and three could not discover anything attempt one did not, and cost a second and third
half-hour of GPU to prove it. Writing the failure onto the task is what makes the NEXT attempt
different from the last, and it is the only thing that does.

Nothing here writes to a real task: taskstore is replaced.
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def main():
    import dispatch_loop as DL
    fails = []

    def ck(label, got, want):
        ok = got == want
        print(f"  [{'ok' if ok else 'BAD'}]    {label} -> {got}")
        if not ok:
            fails.append(f"{label}: expected {want}, got {got}")

    # --- ONE ATTEMPT -----------------------------------------------------------------
    ck("one dead attempt is enough to hold a task", DL.FUTILE_AFTER, 1)
    SHA = "a" * 64

    def fin(ex, accepted=False, at="T"):
        return {"at": at, "event": "finished", "kind": "production", "chunk": "p99",
                "brief_sha256": SHA, "model": "gemma4:26b", "exit": ex, "accepted": accepted,
                "seconds": 10}

    ck("a single failure holds it", DL.futile("p99", SHA, [fin(1)])[0], True)
    # A REFUSAL IS NOT AN ATTEMPT, so it does not hold -- see test_futile_task_held for why.
    ck("a refusal does not hold it", DL.futile("p99", SHA, [fin(2)] * 5)[0], False)
    ck("a delivery fault does hold it", DL.futile("p99", SHA, [fin(3)])[0], True)
    ck("a yield still does not", DL.futile("p99", SHA, [fin(DL.YIELD_EXITS[0])])[0], False)
    ck("an acceptance still clears it",
       DL.futile("p99", SHA, [fin(1), fin(0, accepted=True)])[0], False)

    # --- WHICH TASK A BRIEF BELONGS TO ------------------------------------------------
    real_repo = DL.REPO
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "workspace" / "briefs").mkdir(parents=True)
        DL.REPO = tmp

        def brief(name, body):
            (tmp / "workspace" / "briefs" / name).write_text(body, encoding="utf-8")
            return name

        ck("a declared TASK: line wins",
           DL.task_of(brief("a.txt", "TASK: t401\n\nchange one thing. t999 is mentioned later.")),
           "t401")
        ck("a task mentioned in the body is found",
           DL.task_of(brief("b.txt", "task: t402 -- do the thing\nDONE WHEN x exits 0")), "t402")
        ck("a task in the FILENAME is found when the body has none",
           DL.task_of(brief("brief-t403-console-pin.txt", "no id in here at all")), "t403")
        ck("a brief with no task anywhere resolves to nothing",
           DL.task_of(brief("c.txt", "fix the thing. DONE WHEN y exits 0")), None)
        ck("a brief that does not exist resolves to nothing", DL.task_of("gone.txt"), None)
        ck("no brief name at all resolves to nothing", DL.task_of(None), None)

        # --- THE NOTE ----------------------------------------------------------------
        notes, moves = [], []

        class FakeStore:
            @staticmethod
            def add_note(tid, text, who="x"):
                notes.append((tid, text, who))

            @staticmethod
            def move(tid, where, target=None):
                moves.append((tid, where))

        import supervise
        real_ts = sys.modules.get("taskstore")
        sys.modules["taskstore"] = FakeStore
        try:
            log = tmp / "job.log"
            log.write_text("header\n  attempt 1 … asked for 2 file(s)\n"
                           "  attempt 2 … gave 0 new\n  REFUSING TO START -- baseline\n",
                           encoding="utf-8")
            job = {"chunk": "p99", "brief": "b.txt", "brief_sha256": SHA,
                   "model": "gemma4:26b", "target": "tests/t.py", "files": "x.py"}
            tid = DL.note_failure_on_task(job, 2, 83, "job.log")
            ck("the note goes to the task the brief names", tid, "t402")
            ck("exactly one note was written", len(notes), 1)
            body = notes[0][1]
            ck("it names the failure KIND, not just a number", "refused-to-start" in body, True)
            ck("it explains what that exit code means",
               "precondition failed" in body, True)
            ck("it carries the model and the duration",
               "gemma4:26b" in body and "83s" in body, True)
            ck("it carries the attempt trail", "attempt 1" in body and "attempt 2" in body, True)
            ck("it carries the end of the log", "REFUSING TO START" in body, True)
            ck("it points at the log file", "job.log" in body, True)
            ck("it is attributed to the scheduler", notes[0][2], "dispatch-loop")
            ck("the task goes to the front for re-scoping (Jas, 2026-09-20)", moves, [("t402", "top")])
            ck("it tells the reader a blind retry is pointless",
               "cannot discover" in body, True)

            # A CHUNK WITH NO TASK MUST NOT RAISE -- 23 of 27 enrolled chunks are in this
            # state, so this is the common path, not the edge case.
            notes.clear()
            tid = DL.note_failure_on_task({**job, "brief": "c.txt"}, 1, 10, None)
            ck("a brief with no task writes no note and does not raise", (tid, len(notes)),
               (None, 0))

            # AND A BROKEN TASKSTORE MUST NOT TAKE THE DISPATCH DOWN. The failure is already
            # in the ledger; losing the note is not worth losing the run record.
            class Broken:
                @staticmethod
                def add_note(*a, **k):
                    raise RuntimeError("disk full")
            sys.modules["taskstore"] = Broken
            tid = DL.note_failure_on_task(job, 1, 10, "job.log")
            ck("a taskstore that raises is survived", tid, None)
            _ = supervise
        finally:
            if real_ts is not None:
                sys.modules["taskstore"] = real_ts
            else:
                sys.modules.pop("taskstore", None)
            DL.REPO = real_repo

    for x in fails:
        print("FAIL:", x)
    print("test_failure_reaches_task:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
