"""t1349: a deferral's cover can be a task finished MORE than two weeks before the review.

RED ON MAIN FOR EXACTLY ONE REASON, at the assertion that fails: scripts/session_review.py:438
defines `def done_tasks(days=14):`; its body builds a cutoff from `days` and skips any file whose
mtime is older (lines 441 and 444). All three callers -- extract(), extract_scripted() and
script_audit.py:216 -- call it with NO argument, so the reviewer's candidate pool
`_ids(open_tasks()) + _ids(done_tasks())` and the prompt's {done_tasks} block list only tasks
finished in the last fortnight. RCA workspace/rca/4ba0aeec607e.md cause C2 is this class -- the
coverage matcher resolved covered_by against a pool that excluded the done tasks that implement
the join tool. The 14-day window re-narrows that pool by age: a cover finished 15+ days before
the review reads as 'no covering task found', and reviews run on sessions idle at least 24 h,
on topics that run for months.

Nothing live is read or touched: every task file is a fixture built by tempfile.mkdtemp(),
sr.REPO is pointed at it for the duration of each test and restored in a finally, taskstore is
never imported, and no subprocess, model call, server or process is started.

Run: python3 scripts/tests/test_session_review_cover_done_window.py
"""
import os
import pathlib
import re
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

sr = None
IMPORT_ERROR = ""
try:
    import session_review as sr
except Exception as e:  # noqa: BLE001 -- a failed import is reported by every test, not hidden
    IMPORT_ERROR = f"cannot import the module under test: {type(e).__name__}: {e}"

# The two finished tasks RCA 4ba0aeec607e names as the finding's real covers, at two ages.
OLD_ID, OLD_TITLE, OLD_DAYS = "t898", "Judge cover on the task's full body and ledger", 40
NEW_ID, NEW_TITLE, NEW_DAYS = "t663", "Gemma does session reviews to the quality standard", 2
OPEN_ID, OPEN_TITLE = "t1092", "Refuse a deferral with no covering task"   # OPEN: the other half


def _write(path, tid, title, status, age_days):
    path.write_text("---\n"
                    f"id: {tid}\n"
                    f"title: {title}\n"
                    "area: workspace\n"
                    f"status: {status}\n"
                    "---\n\n"
                    "## Notes\n\n"
                    f"Fixture for t1349: the deferral's cover is the finished task {tid}.\n"
                    "The join tool that computes it is prove_cover.\n", encoding="utf-8")
    t = time.time() - age_days * 86400
    os.utime(path, (t, t))
    return path


def _corpus():
    """A throwaway repo root: workspace/tasks/ with one queued task and two DONE ones, aged."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    tasks = tmp / "workspace" / "tasks"
    (tasks / "done").mkdir(parents=True)
    _write(tasks / "done" / f"{OLD_ID}--judge-cover.md", OLD_ID, OLD_TITLE, "done", OLD_DAYS)
    _write(tasks / "done" / f"{NEW_ID}--gemma-reviews.md", NEW_ID, NEW_TITLE, "done", NEW_DAYS)
    _write(tasks / f"{OPEN_ID}--refuse-ownerless-deferral.md", OPEN_ID, OPEN_TITLE, "queued", NEW_DAYS)
    return tmp


def _listing(**kw):
    """done_tasks() against the fixture corpus, with sr.REPO restored on every path."""
    saved = sr.REPO
    sr.REPO = _corpus()
    try:
        return sr.done_tasks(**kw)
    finally:
        sr.REPO = saved


def test_a_done_task_finished_40_days_ago_is_in_the_default_pool():
    """THE RED ASSERTION: age must not hide a finished cover from the default pool."""
    listing = _listing()
    ids = sr._ids(listing)
    check(OLD_ID in ids, f"a task finished {OLD_DAYS} days ago must be a candidate cover; got {ids}")
    check(f"{OLD_ID} [done] {OLD_TITLE}" in listing,
          f"its line must carry id, status and title for the reviewer; got {listing!r}")


def test_the_recent_done_task_is_listed_with_id_status_and_title():
    """Control: the corpus is read at all, so the red test is red for its AGE, not because the
    listing is empty. The two finished tasks keep DISTINCT titles a reviewer can tell apart."""
    listing = _listing()
    ids = sr._ids(listing)
    check(NEW_ID in ids, f"a recently finished task must be listed; got {ids}")
    check(f"{NEW_ID} [done] {NEW_TITLE}" in listing,
          f"its line must read 'id [status] title'; got {listing!r}")
    check(ids.count(OLD_ID) == 1 and ids.count(NEW_ID) == 1,
          f"each finished task appears exactly once; got {ids}")


def test_an_open_task_is_never_listed_by_done_tasks():
    """NEGATIVE. open_tasks() owns the open half of the pool; a done_tasks() that widened its glob
    to the parent directory would list queued work as a finished cover."""
    ids = sr._ids(_listing())
    check(OPEN_ID not in ids,
          f"the queued task {OPEN_ID} must not appear as a finished cover; got {ids}")
    check(all(re.fullmatch(r"t\d{3,} \[[a-z]+\] .+", line) for line in _listing().splitlines()),
          "every entry is one line with the id first -- _ids() parses the listing with a ^ anchor")


def test_an_explicit_window_is_still_honoured():
    """NEGATIVE on the fix: `days` stays a knob, not a deleted feature. days=7 excludes the 40-day
    task and keeps the 2-day one. A build that removes the window outright fails here."""
    ids = sr._ids(_listing(days=7))
    check(ids == [NEW_ID], f"days=7 must list only the recent task; got {ids}")


def test_a_repo_with_no_done_directory_says_none():
    """The documented `or \"(none)\"` fallback survives the change."""
    saved = sr.REPO
    sr.REPO = pathlib.Path(tempfile.mkdtemp())
    try:
        listing = sr.done_tasks()
    finally:
        sr.REPO = saved
    check(listing == "(none)", f"a corpus with no done/ directory must say '(none)'; got {listing!r}")


FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def main():
    if IMPORT_ERROR:
        FAILS.append(IMPORT_ERROR)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001 -- a crash is a finding, reported by name
            FAILS.append(f"{t.__name__} raised {type(e).__name__}: {e}")
    for m in FAILS:
        print("FAIL:", m)
    print(f"{len(tests) - len(FAILS)}/{len(tests)} assertions pass" if not FAILS
          else f"{len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
