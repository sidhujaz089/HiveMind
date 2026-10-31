#!/usr/bin/env python3
"""t1371 -- finding 79867ab6c673 carries its in-session discharge evidence.

The finding quoted turn 211 of session be2121ac ("I'll make them all start
collapsed; the counts in the headers still show what's in each:") and shipped as a
deferral with an empty covered_by. The promise was paid IN-SESSION: feedback.py's
_stage default flipped open_=True -> open_=False, verified as
"stages: 11 | open on load: 0", and committed as 37fceee
(workspace/rca/79867ab6c673.md, sections 1 and 3).

This suite pins the record-side half of closing it: the finding record on disk must
say the deferral is covered, must name the discharge commit, and must carry the
in-session verification line -- so a later reader can see the promise was paid
without re-reading a 4125-line transcript.

RED ON MAIN: the record has no discharge_evidence key and its covered_by is
"t1139" (workspace/session-reviews/findings/done/79867ab6c673.json, lines 7-8,
read in full at 913 lines).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FINDINGS = ROOT / "workspace" / "session-reviews" / "findings"
KEY = "79867ab6c673"
COMMIT = "37fceee"
VERIFICATION = "stages: 11 | open on load: 0"
SESSION = "be2121ac-5236-4268-a5fa-56bda26324e3"


def _record():
    """The finding record, found in open findings/ OR in findings/done/.

    Both locations are searched so the suite is red for the MISSING FIELDS the task
    asks for, never for a path that moved when the finding was closed. Exactly one
    hit is required: the repo's own copy of this key is unique (search_repo finds the
    key in one findings JSON only).
    """
    hits = [p for p in (FINDINGS / f"{KEY}.json", FINDINGS / "done" / f"{KEY}.json")
            if p.is_file()]
    assert len(hits) == 1, (
        f"t1371: expected exactly one finding record for {KEY} under {FINDINGS} "
        f"(open) or its done/ subdir -- found {hits}. The record is the finding's "
        "only on-disk account of itself; it must be present and unique.")
    text = hits[0].read_text(encoding="utf-8")
    try:
        return hits[0], json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"t1371: {hits[0]} is not valid JSON ({exc}). A closing action that "
            "corrupts the record leaves a finding with no record at all.") from exc


def test_the_finding_is_dispositioned_covered():
    _path, rec = _record()
    assert rec.get("disposition") == "covered", (
        f"t1371: disposition is {rec.get('disposition')!r}, expected 'covered' -- the "
        "deferral was discharged in-session (commit 37fceee), so it must not stay "
        "deferred.")


def test_covered_by_names_the_in_session_discharge_commit():
    _path, rec = _record()
    cov = str(rec.get("covered_by") or "").strip()
    assert COMMIT in cov, (
        f"t1371: covered_by is {cov!r}; it must name the discharge commit {COMMIT} "
        "(the repo's form for a commit discharge is 'fixed:<hash>') so the record "
        "shows the promise was paid by an action, not left at 'no covering task "
        "found'.")


def test_the_record_carries_the_in_session_verification_line():
    _path, rec = _record()
    ev = rec.get("discharge_evidence")
    text = str(ev or "")
    assert COMMIT in text and VERIFICATION in text, (
        "t1371: discharge_evidence is " + repr(ev) + " -- it must carry BOTH the "
        "commit " + COMMIT + " and the in-session verification line '" + VERIFICATION
        + "'. Naming only the hash says a change shipped; naming only the counts says "
        "something was measured. The pair is the discharge: this change, verified.")


def test_closing_the_finding_keeps_its_identity_intact():
    """NEGATIVE guard: closing a finding must not rewrite it.

    These keys are what every ledger and RCA in the repo join on. A build that
    replaces the record with a one-line summary satisfies the field assertions above
    and passes here -- and a closed finding nobody can resolve back to its session
    and report is the defect this task exists to stop.
    """
    path, rec = _record()
    assert rec.get("key") == KEY, (
        f"t1371: {path.name} carries key {rec.get('key')!r}, expected {KEY!r}.")
    assert rec.get("session") == SESSION, (
        f"t1371: session is {rec.get('session')!r}; the record must keep pointing at "
        "the session whose transcript carries the discharge.")
    assert rec.get("rca_report") == "workspace/rca/79867ab6c673.md", (
        f"t1371: rca_report is {rec.get('rca_report')!r}; it is the only place the "
        "evidence is traced to transcript lines (workspace/rca/79867ab6c673.md:15).")
    assert str(rec.get("quote", "")).startswith("I'll make them all start collapsed"), (
        f"t1371: quote is {rec.get('quote')!r} -- the quoted promise IS this finding.")


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
