#!/usr/bin/env python3
"""t767: the cover census is built AFTER the review pass mints its own tasks, and every
finding carries the count of what was examined.

Red on main for the defect and for it alone:
  * _keep() stamps the census it was handed -- req["_candidates"], which extract() and
    extract_scripted() fill in BEFORE any minting -- so a task minted by this very pass (the
    "Decide:" task a for-jas row files) cannot be its finding's carrier, and a deferral quoting
    the same work is written with covered_by empty. That is the recorded shape: a census topping
    out at t751 while the same pass created t758/t759.
  * the finding record carries no examined_count / open_count / population_size / cover_verdict /
    cover_ts / queue_high_water, so "no covering task found" cannot be audited: was the queue
    searched, over how many tasks, against which queue head?
  * the minted id is absent from candidates_consulted, so the record is a snapshot of a world
    its own author had not finished writing.

No model, no live queue, no server, no subprocess, no fixed /tmp path. Every module global that
touches the tree is redirected into a tempfile, and open_tasks/done_tasks/_archive_cover are
patched so nothing reads workspace/tasks/.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as sr  # noqa: E402

FAILS = []

# The real quotes of the session this task came from (workspace/rca/3bc852b26950.md). Q_DEFER is
# the same sentence as Q_APPROVE on purpose: the work was said twice -- the user approving it, the
# assistant deferring it -- and the deferral's carrier is the Decide task minted from that quote.
Q_APPROVE = ("I think the biggest value thing is having the model look at the harness, so this "
             "probe idea of yours seems like the best thing to do.")
Q_DEFER = Q_APPROVE
Q_STALE = ("Env 3 is stale again, so the re-clone of the eval sandbox is deferred to a later "
           "session, once today's five fixes are in.")
Q_ROUND4 = ("Round 4 has no plan at all: the mission, the sandbox size, 150 rounds, and "
            "permission to patch under the existing gate.")

# The queue as the module would list it: open_tasks() -> "id title" lines, done_tasks() likewise.
OPENED = "t001 alpha build the census after minting\nt011 beta stamp examined-count on findings"
CLOSED = "t002 gamma finished the probe"
# What extract()/extract_scripted() hand _keep() today: the queue BEFORE this pass mints.
PRE_MINT = ["t001", "t011", "t002"]
MINT = "t900"


def check(cond, msg):
    if cond:
        print("  ok   " + msg)
    else:
        FAILS.append(msg)
        print("  FAIL " + msg)


def setup(tmp):
    """Redirect every module global that touches the tree into tmp. Returns the undo tuple."""
    repo = Path(tmp)
    keep = (sr.REPO, sr.FINDINGS, sr.LEDGER, sr.FOR_JAS,
            sr.open_tasks, sr.done_tasks, sr._archive_cover)
    base = repo / "workspace" / "session-reviews"
    sr.REPO = repo
    sr.FINDINGS = base / "findings"
    sr.LEDGER = base / "ledger.jsonl"
    sr.FOR_JAS = base / "for-jas"
    # open_tasks() imports taskstore from REPO/projects/app-repo and reads the LIVE queue, so it
    # is replaced by name, not steered by REPO: the population below is the test's, not the box's.
    sr.open_tasks = lambda: OPENED
    sr.done_tasks = lambda days=14: CLOSED
    sr._archive_cover = lambda quote: ""      # no live taskstore.DONE read
    return keep


def restore(keep):
    (sr.REPO, sr.FINDINGS, sr.LEDGER, sr.FOR_JAS,
     sr.open_tasks, sr.done_tasks, sr._archive_cover) = keep


def ledger_rows():
    p = Path(sr.LEDGER)
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def run_keep(tmp, name, found):
    """One _keep() call through the positional signature its four caller-suites use.

    req["_candidates"] is pre-populated with the pre-mint snapshot exactly as today's callers do,
    so a RED here is the missing minted id, not an empty list.
    """
    notes, minted = [], []
    ids = iter([MINT, "t901", "t902", "t903"])

    def note_task(tid, text):
        notes.append((tid, text))

    def file_task(title, body):
        minted.append((title, body))
        return next(ids)

    rf = Path(tmp) / name
    req = {"id": "sess1", "harness": "claude", "parts_done": 0, "_candidates": list(PRE_MINT)}
    rf.write_text(json.dumps(req) + "\n", encoding="utf-8")
    new, covered = sr._keep(rf, req, found, note_task=note_task, file_task=file_task)
    d = Path(sr.FINDINGS)
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    rec = json.loads(files[0].read_text(encoding="utf-8")) if len(files) == 1 else None
    return {"new": new, "covered": covered, "notes": notes, "minted": minted,
            "files": files, "rec": rec}


def test_the_carrier_a_pass_mints_is_credited(tmp):
    """RCA case (i): Jas approves work, this pass mints a Decide task for it -- the finding of
    the same quote must be credited to that id, not written as uncovered."""
    keep = setup(tmp)
    try:
        found = [{"kind": "for-jas", "quote": Q_APPROVE, "where": "user, turn 4", "why_missed": ""},
                 {"kind": "deferral", "quote": Q_DEFER, "where": "assistant, turn 9",
                  "why_missed": "no task number given"}]
        r = run_keep(tmp, "req-1.json", found)
        check(bool(r["minted"]) and str(r["minted"][0][0]).startswith("Decide:"),
              "the pass minted the Decide task for the approval: %r" % (r["minted"],))
        check(r["new"] == 1,
              "exactly ONE new finding -- the for-jas item; the deferral of the same quote is not "
              "filed as uncovered. new=%r" % (r["new"],))
        check(r["covered"] == 1,
              "the deferral is counted COVERED, by the task this pass minted. covered=%r" % (r["covered"],))
        check(r["files"] == [],
              "no finding file is written with covered_by empty: %r"
              % ([p.name for p in r["files"]],))
        cov = [x for x in ledger_rows() if x.get("event") == "covered"]
        check(any(x.get("task") == MINT and x.get("quote") == Q_APPROVE for x in cov),
              "the ledger names the minted task id as the carrier: %r" % (cov,))
        check(bool(r["notes"]) and r["notes"][0][0] == MINT,
              "the minted task carries the note that this finding is covered here: %r" % (r["notes"],))
    finally:
        restore(keep)


def test_the_finding_stamps_examined_count(tmp):
    """RCA case (ii): a genuinely uncovered finding is still written -- and its record says how
    many tasks the cover search could have seen. Stamping must not invent a cover."""
    keep = setup(tmp)
    try:
        found = [{"kind": "deferral", "quote": Q_STALE, "where": "assistant, turn 40",
                  "why_missed": "nothing scheduled it"}]
        r = run_keep(tmp, "req-2.json", found)
        check(r["new"] == 1 and r["rec"] is not None,
              "the uncovered deferral is STILL filed as a finding (stamping must not hide it): "
              "new=%r files=%r" % (r["new"], [p.name for p in r["files"]]))
        rec = r["rec"] or {}
        for field in ("examined_count", "open_count", "population_size",
                      "cover_verdict", "cover_ts", "queue_high_water", "candidates_consulted"):
            check(field in rec, "the finding record carries %s" % field)
        check(rec.get("covered_by") == "",
              "the finding is STILL reported uncovered -- the stamp must not invent a cover: "
              "%r" % (rec.get("covered_by"),))
        ec, oc, ps = rec.get("examined_count"), rec.get("open_count"), rec.get("population_size")
        cands = rec.get("candidates_consulted")
        check(isinstance(ec, int) and not isinstance(ec, bool),
              "examined_count is an int, not a string: %r" % (ec,))
        check(isinstance(oc, int) and isinstance(ps, int),
              "open_count and population_size are ints: %r %r" % (oc, ps))
        check(isinstance(cands, list) and set(cands) >= {"t001", "t011", "t002"},
              "the census is the queue on disk, not a partial sample: %r" % (cands,))
        if isinstance(ec, int) and isinstance(cands, list):
            check(ec == len(cands),
                  "examined_count %r equals the census it stamps (%r)" % (ec, len(cands)))
        if isinstance(ec, int) and isinstance(oc, int):
            check(ec >= oc,
                  "examined_count %r covers every OPEN task in the queue (open_count %r) -- the "
                  "RCA's case (ii)" % (ec, oc))
        if isinstance(ec, int) and isinstance(ps, int):
            check(ec <= ps,
                  "examined_count %r never exceeds population_size %r" % (ec, ps))
        check(rec.get("cover_verdict") == "none",
              "an uncovered finding says verdict 'none': %r" % (rec.get("cover_verdict"),))
        check(rec.get("queue_high_water") == "t011",
              "queue_high_water is the highest id in the census: %r" % (rec.get("queue_high_water"),))
        check(isinstance(rec.get("cover_ts"), str) and bool(rec.get("cover_ts")),
              "cover_ts is stamped, so staleness is dated: %r" % (rec.get("cover_ts"),))
        fr = [x for x in ledger_rows() if x.get("event") == "finding"]
        check(len(fr) == 1 and fr[0].get("examined_count") == ec,
              "the ledger row for the finding carries the SAME examined_count as the record: %r" % (fr,))
    finally:
        restore(keep)


def test_the_census_includes_tasks_minted_by_this_pass(tmp):
    """The census is built AFTER minting, so a later row of the SAME call can be credited and the
    record's own list reaches the ids this pass created."""
    keep = setup(tmp)
    try:
        found = [{"kind": "for-jas", "quote": Q_APPROVE, "where": "user, turn 4", "why_missed": ""},
                 {"kind": "deferral", "quote": Q_ROUND4, "where": "assistant, turn 22",
                  "why_missed": "no plan exists"}]
        r = run_keep(tmp, "req-3.json", found)
        check(bool(r["minted"]) and r["minted"][0][0].startswith("Decide:"),
              "the for-jas row minted its task: %r" % (r["minted"],))
        rec = r["rec"]
        check(rec is not None,
              "Round 4 is disjoint work, so it is still a finding: files=%r"
              % ([p.name for p in r["files"]],))
        rec = rec or {}
        check(MINT in (rec.get("candidates_consulted") or []),
              "the census contains the task this pass just minted (%s): %r" % (MINT, rec.get("candidates_consulted")))
        check(rec.get("queue_high_water") == MINT,
              "queue_high_water moved to %s, not the pre-mint t011: %r" % (MINT, rec.get("queue_high_water")))
        check(isinstance(rec.get("examined_count"), int) and rec["examined_count"] >= 4,
              "the population grew by the mint: examined_count=%r, expected >= 4" % (rec.get("examined_count"),))
        check(rec.get("covered_by") == "",
              "a minted task covering the SAME quote must not swallow a different finding: %r"
              % (rec.get("covered_by"),))
    finally:
        restore(keep)


def main():
    tests = [test_the_carrier_a_pass_mints_is_credited,
             test_the_finding_stamps_examined_count,
             test_the_census_includes_tasks_minted_by_this_pass]
    for t in tests:
        print(t.__name__)
        with tempfile.TemporaryDirectory() as tmp:
            t(tmp)
    print("test_deferral_cover: %d FAILURES" % len(FAILS))
    for m in FAILS:
        print("  - " + m)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
