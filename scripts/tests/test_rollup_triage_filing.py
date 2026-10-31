"""t1064: ONE dated roll-up triage task for the ownerless `deferred` findings.

workspace/tasks/t1064--file-the-121-ownerless-deferred-findings-as-one.md:17 asks for the
roll-up shape: when a sweep finds N>10 uncovered deferrals, file ONE dated task (source
session-rca, top of QUEUE.md) whose body lists all N keys with quote/turn/date, and stamp
each finding's covered_by with that single id; disposition "deferred" gains an owner and an
expiry. The records it reads look like workspace/session-reviews/findings/86cf8f0ab420.json:
key, kind, quote, where ("assistant, turn 215 of 266"), found (the date), disposition,
covered_by. Filing one task per finding mints 121+ tasks in one pass against Jas's 2026-10-01
rule that the backlog must shrink, so this suite pins the ROLL-UP, not the per-finding shape.

Run with plain python3 (pytest is not installed):
    python3 scripts/tests/test_rollup_triage_filing.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import session_review as sr
except Exception as exc:
    print("FATAL: cannot import scripts/session_review.py: %s" % exc)
    sys.exit(1)

TODAY = "2026-10-12"
DUE = "2026-10-26"                      # TODAY + horizon_days=14
QUEUE_FIX = ("# Task queue\n"
             "The top of this file is the top of the queue.\n"
             "t900  Do the other thing  [workspace]  queued\n")

_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)
        print("FAIL: " + msg)


def finding(key, i, disposition="deferred", covered_by=""):
    """Every record carries its OWN quote, turn and date, in the field names the real
    records use (where, found). A body that repeats one placeholder string for all of
    them cannot satisfy the assertions below."""
    return {"key": key, "kind": "deferral",
            "quote": "quote number %d: a distinct sentence for record %s" % (i, key),
            "where": "assistant, turn %d of 602" % (100 + i),
            "found": "2026-09-%02d" % (10 + i),
            "disposition": disposition, "covered_by": covered_by,
            "why_missed": "never mentioned again; no covering task found"}


def uncovered(n):
    return [finding("k%02d" % i, i) for i in range(n)]


def sandbox(tmp, queue_text=QUEUE_FIX):
    tasks = Path(tmp) / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    queue = Path(tmp) / "queue.md"
    queue.write_text(queue_text, encoding="utf-8")
    return tasks, queue


def test_the_roll_up_filer_exists():
    check(callable(getattr(sr, "roll_up_triage", None)),
          "scripts/session_review.py defines roll_up_triage(...) -- the roll-up filer "
          "this brief adds; it does not exist on main")


def test_at_ten_uncovered_deferrals_nothing_is_filed():
    with tempfile.TemporaryDirectory() as tmp:
        tasks, queue = sandbox(tmp)
        f = uncovered(10)
        res = sr.roll_up_triage(f, task_id="t1100", tasks_dir=tasks,
                                queue_path=queue, today=TODAY)
        check(isinstance(res, dict), "returns a dict, got %r" % (res,))
        check(res.get("filed") is False,
              "10 is not >10: filed must be False, got %r" % (res.get("filed"),))
        check(res.get("count") == 10,
              "count reports the 10 uncovered deferrals it found, got %r" % (res.get("count"),))
        check([p.name for p in tasks.glob("*.md")] == [],
              "no task file is written at N=10, wrote %r" % ([p.name for p in tasks.glob("*.md")],))
        check(queue.read_text(encoding="utf-8") == QUEUE_FIX,
              "QUEUE.md is untouched at N=10")
        check(all(x["covered_by"] == "" for x in f), "no record is stamped at N=10")


def test_at_eleven_it_files_exactly_one_dated_task_listing_all_eleven():
    with tempfile.TemporaryDirectory() as tmp:
        tasks, queue = sandbox(tmp)
        f = uncovered(11)
        res = sr.roll_up_triage(f, task_id="t1100", tasks_dir=tasks,
                                queue_path=queue, today=TODAY)
        check(res.get("filed") is True, "N=11 files a roll-up, got %r" % (res.get("filed"),))
        check(res.get("count") == 11,
              "count is the number of uncovered deferrals, got %r" % (res.get("count"),))
        check(res.get("task_id") == "t1100",
              "the return carries the single id it used, got %r" % (res.get("task_id"),))
        names = sorted(p.name for p in tasks.glob("*.md"))
        check(len(names) == 1, "exactly ONE task file for 11 findings, got %r" % (names,))
        if not names:
            return
        path = Path(str(res.get("path") or ""))
        check(path.is_file() and path.name == names[0],
              "the returned path is the file that was written, got %r" % (res.get("path"),))
        check(names[0].startswith("t1100") and names[0].endswith(".md"),
              "the task file is named for its id, got %r" % (names[0],))
        check(TODAY in names[0], "the task file name carries the date, got %r" % (names[0],))
        body = path.read_text(encoding="utf-8")
        check(body.startswith("---\n"),
              "the task file opens with front-matter, got %r" % body[:12])
        for want in ("id: t1100", "status: queued", "source: session-rca", TODAY):
            check(want in body, "the body carries %r" % want)
        for i, rec in enumerate(f):
            check(rec["key"] in body, "the body lists finding key %s" % rec["key"])
            check(rec["quote"] in body, "the body quotes finding %s verbatim" % rec["key"])
            check("turn %d of 602" % (100 + i) in body,
                  "the body gives %s its OWN turn number" % rec["key"])
            check("2026-09-%02d" % (10 + i) in body,
                  "the body gives %s its OWN date, read from its 'found' field" % rec["key"])
        check("t9999" not in body, "the body lists only the findings it was given")


def test_each_stamped_record_gets_the_id_an_owner_and_an_expiry():
    with tempfile.TemporaryDirectory() as tmp:
        tasks, queue = sandbox(tmp)
        f = uncovered(11)
        sr.roll_up_triage(f, task_id="t1100", tasks_dir=tasks, queue_path=queue, today=TODAY)
        for rec in f:
            check(rec["covered_by"] == "t1100",
                  "%s covered_by is the single roll-up id, got %r" % (rec["key"], rec["covered_by"]))
            check(rec.get("owner") == "manager",
                  "%s gains an owner, got %r" % (rec["key"], rec.get("owner")))
            check(rec.get("due") == DUE,
                  "%s gains an expiry 14 days out, got %r" % (rec["key"], rec.get("due")))
            check(rec.get("kind") == "deferral" and rec.get("disposition") == "deferred",
                  "%s keeps the kind and disposition it came with" % rec["key"])


def test_findings_that_are_not_uncovered_deferrals_are_left_alone():
    with tempfile.TemporaryDirectory() as tmp:
        tasks, queue = sandbox(tmp)
        f = uncovered(11)
        covered = finding("done1", 20, covered_by="t900")
        other = finding("open1", 19, disposition="unresolved")
        res = sr.roll_up_triage(f + [covered, other], task_id="t1100", tasks_dir=tasks,
                                queue_path=queue, today=TODAY)
        check(res.get("count") == 11,
              "only uncovered deferrals count, got %r" % (res.get("count"),))
        body = Path(str(res.get("path") or "")).read_text(encoding="utf-8")
        check("done1" not in body, "a finding that already has a covering task is not listed")
        check("open1" not in body, "a finding whose disposition is not deferred is not listed")
        check(covered["covered_by"] == "t900", "a finding already covered keeps its own covered_by")
        check(covered.get("owner") is None, "a finding already covered gains no owner")
        check(other.get("owner") is None, "an unresolved finding gains no owner")


def test_the_queue_row_lands_at_the_top_and_nothing_else_moves():
    with tempfile.TemporaryDirectory() as tmp:
        tasks, queue = sandbox(tmp)
        before = QUEUE_FIX.splitlines()
        sr.roll_up_triage(uncovered(11), task_id="t1100", tasks_dir=tasks,
                          queue_path=queue, today=TODAY)
        after = queue.read_text(encoding="utf-8").splitlines()
        check(len(after) == len(before) + 1, "QUEUE.md gains exactly one row, got %r" % (after,))
        check(after[0] == before[0], "the queue header is untouched, got %r" % (after[:3],))
        check(after[1].startswith("t1100"),
              "the new row is the FIRST row of the queue, got %r" % (after[1:3],))
        check("roll-up" in after[1], "the new row says it is the roll-up, got %r" % (after[1:3],))
        check("t1100" in after[1], "the new row names the id, got %r" % (after[1:3],))
        check(after[2:] == before[1:], "existing rows keep their order and text, got %r" % (after[2:],))


def test_a_second_sweep_the_same_day_mints_no_second_task():
    with tempfile.TemporaryDirectory() as tmp:
        tasks, queue = sandbox(tmp)
        first = sr.roll_up_triage(uncovered(11), task_id="t1100", tasks_dir=tasks,
                                  queue_path=queue, today=TODAY)
        second = sr.roll_up_triage(uncovered(11), task_id="t1100", tasks_dir=tasks,
                                   queue_path=queue, today=TODAY)
        files = sorted(p.name for p in tasks.glob("*.md"))
        check(len(files) == 1, "the second sweep writes no second task, wrote %r" % (files,))
        check(second.get("filed") is False,
              "the second sweep reports it did not file again, got %r" % (second.get("filed"),))
        check(second.get("count") == 11, "the second sweep still counts 11, got %r" % (second.get("count"),))
        check(second.get("path") == first.get("path"),
              "the second sweep points at the existing roll-up, got %r" % (second.get("path"),))
        check(len(queue.read_text(encoding="utf-8").splitlines()) == len(QUEUE_FIX.splitlines()) + 1,
              "the queue gains one row across BOTH sweeps")


def _main():
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            print("running %s" % name)
            try:
                fn()
            except Exception as exc:
                msg = "%s raised %s: %s" % (name, type(exc).__name__, exc)
                _failures.append(msg)
                print("FAIL: " + msg)
    if _failures:
        print("\n%d FAILURE(S)" % len(_failures))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
