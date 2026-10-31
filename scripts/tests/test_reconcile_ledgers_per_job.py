#!/usr/bin/env python3
"""t1476: the two review ledgers must be reconciled per JOB, not per file.

check_workspace.py reconciles the dispatch ledger (what the dispatch job recorded, field
`accepted`) with the review ledger (what the reviewer said, field `status`). It resolves a
job's verdict by looking the job up by (chunk, brief_sha256) -- for a script-review that is
(file path, file content hash). That key is NOT per dispatch: every re-dispatch of a file
whose content has not changed inherits the verdict of an EARLIER review of that content. A
dispatch that ends non-zero after the file's review has been recorded 'ok' is therefore
counted as the two ledgers disagreeing, forever. The hourly finding on 2026-10-18 is 1009 rows
of exactly that message: "dispatch says accepted=False, the review says 'ok'".

Counted on the ledgers as they stand: every one of those rows carries NO `review_status` field
of its own, and ZERO rows carry `review_status` == "ok" with `accepted=False`. So the 'ok'
every one of them is quoted with belongs to a different dispatch of the same file content.

This suite pins the behaviour that makes the reconciliation honest:

  * a row that records its own verdict (`review_status`) is compared with IT, and its own
    verdict only -- never with a verdict reached by (path, content-hash);
  * a row that records no verdict of its own is NOT reported as a ledger disagreement: it is
    reported in its own bucket, because the dispatch ledger cannot vouch for what the reviewer
    told that job. It is never counted as agreement, and never dropped;
  * a row that records no verdict AND has no review row for its (path, content-hash) at all
    keeps today's "no row written" divergence -- a job accepted with nothing reviewed is the
    case this check was built to catch, and the fix must not silence it;
  * the row filter (finished / script-review / trusted-since) is preserved.

Pure: dicts in, dict out. No files, no subprocess, no network, no model, no ledger read from
disk, no tempdir. Runs under `python3 <this file>`; pytest is not installed.

Red on main for the one reason this change introduces: check_workspace has no function named
`reconcile_review_ledgers`.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

TRUSTED = "2026-09-15T23:00:00Z"


def _reconcile():
    import check_workspace
    fn = getattr(check_workspace, "reconcile_review_ledgers", None)
    assert fn is not None, (
        "scripts/check_workspace.py defines no reconcile_review_ledgers(disp_rows, "
        "review_rows, trusted_since): the reconciliation is inline in the ledger-check method, "
        "which is why a job's verdict is resolved by (path, content-hash) and can be borrowed "
        "from a different dispatch of the same file.")
    return fn


def _disp(path, sha, accepted, at="2026-10-18T20:50:40Z", **extra):
    row = {"at": at, "event": "finished", "kind": "script-review",
           "chunk": path, "brief_sha256": sha, "accepted": accepted}
    row.update(extra)
    return row


def _out(fn, disp, reviews=()):
    out = fn(list(disp), list(reviews), TRUSTED)
    for k in ("checked", "disagree", "no_verdict"):
        assert k in out, f"missing bucket {k!r} in {out!r}"
    assert isinstance(out["checked"], int), f"checked must be an int, got {out['checked']!r}"
    assert isinstance(out["disagree"], list), f"disagree must be a list, got {out['disagree']!r}"
    assert isinstance(out["no_verdict"], list), f"no_verdict must be a list, got {out['no_verdict']!r}"
    return out


def test_agreeing_job_is_reported_as_agreement():
    """The common good case: the job recorded its own verdict and it matches accepted."""
    fn = _reconcile()
    out = _out(fn, [_disp("scripts/intake.py", "sha-alpha", True, review_status="ok")])
    assert out["checked"] == 1, out
    assert out["disagree"] == [], f"a job whose own verdict agrees must not be reported: {out}"
    assert out["no_verdict"] == [], f"a job that recorded a verdict is not 'no verdict': {out}"


def test_unreadable_verdict_and_not_accepted_agree():
    """The pair the ledgers were built to agree on: the reply was unusable, so the job was not
    accepted. accepted=False with its own verdict 'unreadable (...)' is AGREEMENT, not
    divergence -- the comparison is accepted vs `said == "ok"`. The fix must not report it."""
    fn = _reconcile()
    out = _out(fn, [_disp("scripts/dispatch_loop.py", "sha-beta", False,
                          review_status="unreadable (invalid-json, done_reason=stop)")])
    assert out["checked"] == 1, out
    assert out["disagree"] == [], out
    assert out["no_verdict"] == [], out


def test_own_verdict_ok_and_not_accepted_is_a_divergence():
    """The invariant the check's own message states -- 'a job that ran fine and a review that
    said nothing usable are not the same outcome'. A job that recorded its own verdict 'ok' and
    is marked accepted=False (rc != 0: killed, preempted, timed out) IS a divergence, and the
    fix must not silence it by dropping the comparison."""
    fn = _reconcile()
    out = _out(fn, [_disp("scripts/lane_status.py", "sha-gamma", False, review_status="ok")])
    assert out["checked"] == 1, out
    assert len(out["disagree"]) == 1, (
        "accepted=False against the row's own recorded verdict 'ok' must stay a finding, so "
        f"--status keeps showing it: {out}")
    text = str(out["disagree"][0])
    assert "scripts/lane_status.py" in text, f"the finding must name the file: {text!r}"
    assert "2026-10-18T20:50:40Z" in text, f"the finding must name when: {text!r}"
    assert "accepted=False" in text, f"the finding must state the dispatch side: {text!r}"
    assert "ok" in text, f"the finding must state the review side: {text!r}"


def test_missing_own_verdict_is_bucketed_not_inherited():
    """THE DEFECT (t1476's 1009 rows). A finished script-review row that carries no verdict of
    its own, at a content hash whose review row says 'ok'. Today the check resolves that 'ok' by
    (path, sha) -- a key every dispatch of that content shares -- and prints 'dispatch says
    accepted=False, the review says ok'. That 'ok' belongs to a different dispatch, so it must
    not be attributed to this row, and the row must NOT be reported as a ledger disagreement.
    It goes in its own bucket, because the dispatch ledger cannot vouch for it."""
    fn = _reconcile()
    out = _out(fn,
               [_disp("scripts/intake.py", "sha-alpha", False)],
               [{"path": "scripts/intake.py", "sha256": "sha-alpha", "status": "ok"}])
    assert out["checked"] == 1, out
    assert out["disagree"] == [], (
        "a verdict recorded for a DIFFERENT dispatch of the same file content must not be "
        f"attributed to this row -- that is the 1009-row defect: {out['disagree']}")
    assert len(out["no_verdict"]) == 1, (
        f"a row the dispatch ledger recorded no verdict for must still be reported: {out}")
    text = str(out["no_verdict"][0])
    assert "scripts/intake.py" in text, f"must name the file: {text!r}"
    assert "2026-10-18T20:50:40Z" in text, f"must name the time: {text!r}"
    assert "accepted=False" in text, f"must state the dispatch side: {text!r}"
    assert "no verdict" in text.lower(), (
        f"the finding must say the dispatch ledger recorded no verdict: {text!r}")


def test_no_review_row_at_all_still_diverges():
    """Negative assertion on the fix: 'no row written' must not become a pass, and must not be
    folded into no_verdict. A job accepted while nothing was reviewed for its content is the
    case the ledgers were built to catch (t324's shape: detection that goes quiet)."""
    fn = _reconcile()
    out = _out(fn, [_disp("scripts/lane_status.py", "sha-delta", True)])
    assert out["checked"] == 1, out
    assert len(out["disagree"]) == 1, (
        f"accepted=True with no review recorded must stay a finding: {out}")
    text = str(out["disagree"][0])
    assert "no row written" in text, (
        f"the finding must say the review recorded nothing, not go quiet: {text!r}")
    assert out["no_verdict"] == [], (
        f"'no row written' is a divergence, not a missing-verdict report: {out['no_verdict']}")


def test_two_distinct_files_sharing_a_content_hash_are_not_confused():
    """Two finished rows share one content hash and disagree in OPPOSITE directions, with a
    review row recorded for only ONE of them. An implementation that keys by content hash and
    drops the path serves that review row to the other row too -- and passes every other test
    in this file. Both rows recorded no verdict of their own.

      scripts/intake.py         accepted=True,  NO review row for (intake.py, sha-shared)
      scripts/dispatch_loop.py  accepted=False, review row for (dispatch_loop.py, sha-shared)='ok'

    Correct outcome: intake.py keeps the 'no row written' divergence; dispatch_loop.py is
    reported as a row with no verdict of its own. Neither may borrow the other's verdict.
    """
    fn = _reconcile()
    out = _out(fn,
               [_disp("scripts/intake.py", "sha-shared", True),
                _disp("scripts/dispatch_loop.py", "sha-shared", False)],
               [{"path": "scripts/dispatch_loop.py", "sha256": "sha-shared", "status": "ok"}])
    assert out["checked"] == 2, out
    assert len(out["disagree"]) == 1, (
        f"only the row with no review recorded at its content is a divergence: {out['disagree']}")
    text = str(out["disagree"][0])
    assert "scripts/intake.py" in text, (
        f"the divergence must be intake.py's, not a verdict borrowed across paths: {text!r}")
    assert "no row written" in text, f"and it must say why: {text!r}"
    assert len(out["no_verdict"]) == 1, (
        f"only dispatch_loop.py has a review row at that content: {out['no_verdict']}")
    nv = str(out["no_verdict"][0])
    assert "scripts/dispatch_loop.py" in nv, f"must name the row that has the review row: {nv!r}"
    assert "scripts/intake.py" not in nv, (
        f"intake.py must not be reported through dispatch_loop.py's review row: {nv!r}")


def test_rows_the_check_does_not_own_are_not_counted():
    """The row filter must survive the extraction: only finished, kind == 'script-review', at >=
    trusted_since. A preflight row, a started row and a pre-cutoff row are not this check's."""
    fn = _reconcile()
    out = _out(fn, [
        _disp("scripts/intake.py", "sha-alpha", False, at="2026-09-15T06:00:00Z",
              review_status="ok"),
        {"at": "2026-10-18T20:50:40Z", "event": "started", "kind": "script-review",
         "chunk": "scripts/intake.py", "brief_sha256": "sha-alpha", "accepted": True},
        {"at": "2026-10-18T20:50:40Z", "event": "finished", "kind": "preflight",
         "chunk": "scripts/intake.py", "brief_sha256": "sha-alpha", "accepted": True},
        {"at": "2026-10-18T20:50:40Z", "event": "finished", "kind": "script-review",
         "chunk": "scripts/dispatch_loop.py", "brief_sha256": "sha-beta", "accepted": True,
         "review_status": "ok"},
    ])
    assert out["checked"] == 1, f"only the in-scope finished script-review row counts: {out}"
    assert out["disagree"] == [], out
    assert out["no_verdict"] == [], out


if __name__ == "__main__":
    import traceback
    tests = sorted((n, f) for n, f in list(globals().items())
                   if n.startswith("test_") and callable(f))
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception:                                            # noqa: BLE001
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
