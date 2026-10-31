#!/usr/bin/env python3
"""Oracle for t1460: the two review ledgers, joined by ATTEMPT, not by content key.

Red on main for one reason and one only: scripts/check_workspace.py has no
function named reconcile_review_ledgers. Nothing here reads a live ledger --
every case builds its own temp directory -- so no assertion can be red for any
other reason, and no run can dirty the worktree.

The (path, sha256) pairs and the refusal reason text are copied from real ledger
rows (workspace/dispatch-loop.jsonl:7996, :8004, :24926 at f5f0a210), because a
synthetic fixture encodes what its author imagined the data looks like.

No f-strings on purpose: two earlier drafts of this file died at the parser on an
f-string that spanned a line break.
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "check_workspace.py"

SINCE = "2026-09-15T23:00:00Z"

# Real (path, sha256) pairs read off workspace/dispatch-loop.jsonl lines 7996 and
# 8004. Two DISTINCT shas on two DISTINCT paths, so a build that joins by path
# alone cannot pass the case that needs the content key to be ignored.
RUN_TESTS = "scripts/run_tests.py"
RUN_TESTS_SHA = "74a0bd30003ac10304b75e82a85d35b07d48903d61c5f6de8b3285478dfda2f4"
TRUNC = "scripts/truncation_review.py"
TRUNC_SHA = "12360ce9aa680b22621f9362de96f8decffae51a5d0793eea16afce9106bba5a"

# Verdict strings copied from real review rows in the ledger.
VERDICT_OK = "ok"
VERDICT_UNREADABLE = "unreadable (ambiguous, done_reason=stop)"

# The reason a LANE-CAP refusal carries. 15 of the 436 refusals in the real ledger
# carry this text, not the duplicate-twin text, so a build that looks for the words
# "already running" leaves them counted as reviews. Lines 24926, 25231, 25994,
# 26156, 26157, 26159.
CAP_REASON = ("swift already runs 4 job(s) of its 4 lane(s); the plan that chose "
              "this was stale")
DUP_REASON = ("already running as pid 6337 (same kind, key and brief); a second "
              "copy would race the first")

FAILS = []


def check(name, cond, msg):
    """Record, never raise: one failing assertion must not hide the other nine."""
    if not cond:
        FAILS.append(name + ": " + msg)


def load_module():
    spec = importlib.util.spec_from_file_location("cw_t1460", str(MODULE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def row(at, chunk, sha, accepted, **extra):
    """A dispatch-loop finished row in the real shape (dispatch_loop.py terminal_row
    and _refuse_launch): key equals chunk, brief_sha256 carries the sha."""
    r = {"at": at, "event": "finished", "kind": "script-review", "chunk": chunk,
         "model": "swift", "brief": "read the file", "brief_sha256": sha,
         "key": chunk, "accepted": accepted}
    r.update(extra)
    return r


def review(at, path, sha, status):
    return {"at": at, "path": path, "sha256": sha, "model": "swift",
            "seconds": 31, "status": status, "partial": False,
            "findings": 0, "done_reason": "stop"}


def run(disp_rows, review_rows):
    mod = load_module()
    fn = getattr(mod, "reconcile_review_ledgers", None)
    if not callable(fn):
        raise AssertionError(
            "scripts/check_workspace.py has no reconcile_review_ledgers -- "
            "the function this task adds, per its brief")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ws = root / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        with (ws / "dispatch-loop.jsonl").open("w", encoding="utf-8") as fh:
            for r in disp_rows:
                # A str row is written RAW: it is a torn line, and json.dumps would make it a valid
                # JSON string (Claude 2026-10-28, from the preflight objection on t1460).
                fh.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
        with (ws / "truncation-review.jsonl").open("w", encoding="utf-8") as fh:
            for r in review_rows:
                fh.write(json.dumps(r) + "\n")
        return fn(root, SINCE)


def test_function_exists():
    mod = load_module()
    fn = getattr(mod, "reconcile_review_ledgers", None)
    check("exists: callable", callable(fn),
          "module imported but has no callable reconcile_review_ledgers")
    print("ran: test_function_exists")


def test_refused_launch_is_not_a_review():
    """The shape of all 436 rows the check reports today (dispatch_loop.py:2382-2391,
    _refuse_launch: pid 0, exit null, accepted False, duplicate_of, no review_status).
    No review ran, so there is nothing to reconcile -- and a skipped row must be
    RECORDED, because a row a check skips silently is a row nobody can reconcile."""
    out = run(
        [row("2026-10-18T16:02:44Z", RUN_TESTS, RUN_TESTS_SHA, True,
             pid=333960, exit=0, seconds=31, review_status=VERDICT_OK),
         row("2026-10-18T16:03:13Z", RUN_TESTS, RUN_TESTS_SHA, False,
             pid=0, exit=None, duplicate_of=6337, reason=DUP_REASON, log="x.log"),
         row("2026-10-18T16:04:00Z", TRUNC, TRUNC_SHA, True,
             pid=333961, exit=0, seconds=44, review_status=VERDICT_OK)],
        [review("2026-10-18T16:02:00Z", RUN_TESTS, RUN_TESTS_SHA, VERDICT_OK),
         review("2026-10-18T16:04:00Z", TRUNC, TRUNC_SHA, VERDICT_OK)])
    check("refused: not counted", out["checked"] == 2,
          "checked=%r, want 2 -- a refused launch is not a review attempt, so it "
          "must not inflate the count the PASS prints" % (out["checked"],))
    check("refused: not a divergence", out["mismatches"] == [],
          "mismatches=%r, want [] -- accepted=False with no verdict of its own is a "
          "refusal, not a report of work that did not happen" % (out["mismatches"],))
    check("refused: recorded", len(out["skipped"]) == 1,
          "skipped=%r, want exactly the one refused launch" % (out["skipped"],))
    if out["skipped"]:
        s = out["skipped"][0]
        check("refused: says refused", "refus" in str(s.get("why", "")).lower(),
              "why=%r must say the launch was refused" % (s.get("why"),))
        check("refused: names the row", str(s.get("at")) == "2026-10-18T16:03:13Z"
              and s.get("chunk") == RUN_TESTS,
              "%r must name the refused row by at and chunk" % (s,))
        check("refused: names the twin", "6337" in str(s.get("why", "")),
              "why=%r must name duplicate_of, the pid that would have been raced" % (
                  s.get("why"),))
        check("refused: quotes the reason", "second copy" in str(s.get("why", "")),
              "why=%r must quote the reason the scheduler gave" % (s.get("why"),))
    print("ran: test_refused_launch_is_not_a_review")


def test_refusal_reason_that_does_not_say_already_running():
    """15 of the 436 real refusals carry the lane-cap reason, which does not contain
    the words 'already running'. A build that phrase-matches the reason leaves every
    one of them counted as a review attempt -- 15 rows today, and the shape recurs
    every time the lane fills."""
    out = run([row("2026-10-18T06:55:32Z", RUN_TESTS, RUN_TESTS_SHA, False,
                   pid=0, exit=None, duplicate_of=None, reason=CAP_REASON)],
              [review("2026-10-18T06:55:00Z", RUN_TESTS, RUN_TESTS_SHA, VERDICT_OK)])
    check("cap refusal: not counted", out["checked"] == 0,
          "checked=%r, want 0 -- the predicate must be structural (no verdict, no "
          "pid, exit null), not a match on the reason text" % (out["checked"],))
    check("cap refusal: recorded", len(out["skipped"]) == 1,
          "skipped=%r, want the one lane-cap refusal" % (out["skipped"],))
    check("cap refusal: not a divergence", out["mismatches"] == [],
          "mismatches=%r, want []" % (out["mismatches"],))
    print("ran: test_refusal_reason_that_does_not_say_already_running")


def test_accepted_true_with_unreadable_verdict_is_reported():
    """The case the task text names: a job that ran fine and a review that said
    nothing usable. This shape exists in ZERO rows of the real ledger today, so no
    build can satisfy it by reproducing today's behaviour."""
    out = run([row("2026-10-18T16:06:10Z", TRUNC, TRUNC_SHA, True,
                   pid=1479104, exit=0, seconds=316,
                   review_status=VERDICT_UNREADABLE)],
              [review("2026-10-18T16:06:10Z", TRUNC, TRUNC_SHA, VERDICT_OK)])
    check("unreadable+accepted: counted", out["checked"] == 1,
          "checked=%r, want 1 -- pid and exit are real, so this was a review" % (
              out["checked"],))
    check("unreadable+accepted: reported", len(out["mismatches"]) == 1,
          "mismatches=%r, want exactly 1 -- a build that drops every row without an "
          "'ok' verdict silences the case this check exists for" % (out["mismatches"],))
    if out["mismatches"]:
        m = out["mismatches"][0]
        check("unreadable+accepted: names the file", TRUNC in m,
              "%r must name the full path, so two files stay distinguishable" % (m,))
        check("unreadable+accepted: says accepted", "accepted=True" in m,
              "%r must quote what dispatch claimed" % (m,))
        check("unreadable+accepted: says the verdict", "unreadable" in m,
              "%r must quote what the review said" % (m,))
    print("ran: test_accepted_true_with_unreadable_verdict_is_reported")


def test_accepted_true_with_no_verdict_of_its_own_is_reported():
    """dispatch-loop.jsonl:7996, verbatim in shape: accepted true, pid 333960,
    exit 0, NO review_status. Its content key resolves to an 'ok' review row stamped
    seven hours earlier. Today the check reports nothing for it -- the borrowed verdict
    is exactly how work that did not happen gets an ACCEPTED."""
    out = run([row("2026-10-02T12:27:35Z", RUN_TESTS, RUN_TESTS_SHA, True,
                   pid=333960, exit=0, lost=True,
                   why="its own finished row was never written")],
              [review("2026-10-02T05:22:33Z", RUN_TESTS, RUN_TESTS_SHA, VERDICT_OK)])
    check("no verdict: counted", out["checked"] == 1,
          "checked=%r, want 1" % (out["checked"],))
    check("no verdict: reported", len(out["mismatches"]) == 1,
          "mismatches=%r, want exactly 1 -- an accepted=True attempt with no verdict "
          "of its own is what --status reporting work that did not happen means" % (
              out["mismatches"],))
    if out["mismatches"]:
        m = out["mismatches"][0]
        check("no verdict: says so", "no verdict" in m, "%r must say no verdict was "
              "recorded for the attempt" % (m,))
        check("no verdict: names the file", RUN_TESTS in m, "%r must name the path" % (m,))
        check("no verdict: the borrowed ok does not clear it", "05:22:33" not in m,
              "%r: the row's verdict is absent, not a 05:22:33Z review of the same "
              "file" % (m,))
    print("ran: test_accepted_true_with_no_verdict_of_its_own_is_reported")


def test_accepted_false_with_ok_verdict_is_reported():
    """The mirror: the review ledger says ok, the dispatch ledger says nothing was
    accepted. Zero rows today; it is the other direction of a report that cannot
    be verified, and the rule is symmetric in the arithmetic."""
    out = run([row("2026-10-18T17:00:00Z", TRUNC, TRUNC_SHA, False,
                   pid=999, exit=0, review_status=VERDICT_OK)],
              [review("2026-10-18T17:00:00Z", TRUNC, TRUNC_SHA, VERDICT_OK)])
    check("accepted False + ok: reported", len(out["mismatches"]) == 1,
          "mismatches=%r, want exactly 1" % (out["mismatches"],))
    if out["mismatches"]:
        m = out["mismatches"][0]
        check("accepted False + ok: quotes both", "accepted=False" in m and "ok" in m,
              "%r must quote accepted and the verdict" % (m,))
    print("ran: test_accepted_false_with_ok_verdict_is_reported")


def test_accepted_false_with_unreadable_verdict_is_not_a_divergence():
    """The over-reporting guard, and the assertion the last two drafts got backwards.
    Both ledgers agree nothing was accepted, so this is NOT a divergence. A build that
    flags every accepted=False row fails here, and on the real ledger it would add
    176 findings to a check a person has to read every hour."""
    out = run([row("2026-10-18T16:06:10Z", TRUNC, TRUNC_SHA, False,
                   pid=1479104, exit=0, seconds=316,
                   review_status=VERDICT_UNREADABLE)],
              [review("2026-10-18T16:06:10Z", TRUNC, TRUNC_SHA, VERDICT_OK)])
    check("agreement on nothing accepted: counted", out["checked"] == 1,
          "checked=%r, want 1 -- it is a real attempt; agreeing is not the same as "
          "being invisible" % (out["checked"],))
    check("agreement on nothing accepted: quiet", out["mismatches"] == [],
          "mismatches=%r, want [] -- accepted=False with an unreadable verdict is "
          "agreement between the ledgers" % (out["mismatches"],))
    print("ran: test_accepted_false_with_unreadable_verdict_is_not_a_divergence")


def test_honest_agreement_is_not_reported():
    """The green case, on a real (path, sha) pair, with its own verdict."""
    out = run([row("2026-10-18T18:00:00Z", RUN_TESTS, RUN_TESTS_SHA, True,
                   pid=1, exit=0, review_status=VERDICT_OK)],
              [review("2026-10-18T18:00:00Z", RUN_TESTS, RUN_TESTS_SHA, VERDICT_OK)])
    check("agreement: counted", out["checked"] == 1, "checked=%r, want 1" % (out["checked"],))
    check("agreement: quiet", out["mismatches"] == [],
          "mismatches=%r, want []" % (out["mismatches"],))
    check("agreement: nothing skipped", out["skipped"] == [],
          "skipped=%r, want [] -- this attempt ran" % (out["skipped"],))
    check("agreement: not a non-pass", "not a pass" not in str(out["summary"]),
          "summary=%r: one attempt was checked, so it must not read as nothing checked"
          % (out["summary"],))
    print("ran: test_honest_agreement_is_not_reported")


def test_counts_and_torn_are_arithmetic():
    """checked counts ATTEMPTS -- not lines. A tick row must not count, two distinct
    files must count twice, and the torn line is named by file and line, not swallowed."""
    out = run(
        [row("2026-10-18T18:00:00Z", RUN_TESTS, RUN_TESTS_SHA, True,
             pid=1, exit=0, review_status=VERDICT_OK),
         row("2026-10-18T18:00:05Z", TRUNC, TRUNC_SHA, True,
             pid=2, exit=0, review_status=VERDICT_OK),
         {"at": "2026-10-18T18:00:10Z", "event": "tick", "say": "NO ROOM 21G"},
         "{\"at\": \"2026-10-18T18:00:15Z\", \"event\": "],
        [review("2026-10-18T18:00:00Z", RUN_TESTS, RUN_TESTS_SHA, VERDICT_OK),
         review("2026-10-18T18:00:05Z", TRUNC, TRUNC_SHA, VERDICT_OK)])
    check("arithmetic: attempts not lines", out["checked"] == 2,
          "checked=%r, want 2 -- a tick row is not a review and a refusal is not "
          "either" % (out["checked"],))
    check("arithmetic: no phantom divergence", out["mismatches"] == [],
          "mismatches=%r, want [] -- the tick must not be reconciled" % (out["mismatches"],))
    check("arithmetic: torn is one", len(out["torn"]) == 1,
          "torn=%r, want 1 -- an unreadable ledger row is the finding this section "
          "exists for" % (out["torn"],))
    if out["torn"]:
        check("torn: names file and line", "dispatch-loop.jsonl" in out["torn"][0]
              and "line 4" in out["torn"][0],
              "%r must name the file and line 4 -- the fourth row written" % (out["torn"],))
        check("torn: not the good rows", not any(f"line {n}:" in out["torn"][0] for n in (1, 2, 3)),
              "%r must point at the torn line, not a clean one" % (out["torn"],))
    print("ran: test_counts_and_torn_are_arithmetic")


def test_nothing_checked_says_not_a_pass():
    """The existing SKIP wording (check_workspace.py:1540-1543) is a record the next
    reader acts on, so an empty ledger must never read as a pass."""
    out = run([], [])
    check("empty: zero checked", out["checked"] == 0,
          "checked=%r, want 0" % (out["checked"],))
    check("empty: not a pass", "not a pass" in str(out["summary"]),
          "summary=%r must say this is not a pass" % (out["summary"],))
    check("empty: nothing invented", out["mismatches"] == [],
          "%r, want []" % (out["mismatches"],))
    print("ran: test_nothing_checked_says_not_a_pass")


TESTS = [test_function_exists, test_refused_launch_is_not_a_review,
         test_refusal_reason_that_does_not_say_already_running,
         test_accepted_true_with_unreadable_verdict_is_reported,
         test_accepted_true_with_no_verdict_of_its_own_is_reported,
         test_accepted_false_with_ok_verdict_is_reported,
         test_accepted_false_with_unreadable_verdict_is_not_a_divergence,
         test_honest_agreement_is_not_reported, test_counts_and_torn_are_arithmetic,
         test_nothing_checked_says_not_a_pass]

if __name__ == "__main__":
    for t in TESTS:
        try:
            t()
        except Exception as e:                                          # noqa: BLE001
            FAILS.append(t.__name__ + " raised " + type(e).__name__ + ": " + str(e))
    for f in FAILS:
        print("FAIL: " + f)
    print("%d/%d test(s) passed, %d failure(s)"
          % (len(TESTS) - len(FAILS), len(TESTS), len(FAILS)))
    sys.exit(1 if FAILS else 0)
