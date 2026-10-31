#!/usr/bin/env python3
"""t659: a session-review finding whose quote does not anchor must be REFUSED.

The defect on main: extract() asks _anchor() for the sentence a quote sits in and, when it is
not found, sets r["quote_unverified"] = True (scripts/session_review.py:465) -- an annotation.
_keep() (scripts/session_review.py:1024) ignores the flag: it writes FINDINGS/<key>.json and
logs event=finding for the row anyway, so a paraphrase the session never said becomes a
first-class finding, enters pending_findings(), and spends an RCA model call on words nobody
said. That is how the finding "Review probe-work in slices by subsystem rather than as one
1,525-line diff" (workspace/rca/602a0c62ce9d.md) was filed.

The contract pinned here: a row carrying quote_unverified is TERMINAL. _keep() must write NO
finding file for it, log one ledger row {"event": "refused", "reason": "quote_unverified"}
naming that quote, and NOT count it in `new`.

This file is the live twin of scripts/tests/test_session_review_anchor_refusal.py, which is red
on main for its OWN bug: its _run() returns Path objects collected inside a
TemporaryDirectory and its test reads them after that context manager deleted the directory, so
it dies FileNotFoundError at its line 77 and can never prove the gate. The bridge will not let a
scope overwrite that file, so the gate's fixture lives here, reading finding files INSIDE the
temp dir.

Run as: python3 scripts/tests/test_session_review_anchor_gate.py
(pytest is NOT installed; the __main__ block runs the tests and exits non-zero on any failure.)
No live state: FINDINGS, _log and _archive_cover are rebound into a temp dir / a recorder for the
duration of the call and restored in `finally`; no model call, no subprocess, no network, no
fixed /tmp path (tempfile). The last test counts the live findings dir before and after to prove
nothing was written outside the temp dir.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as sr                          # noqa: E402

ANCHORED = "Now proving the gate actually refuses is the thing we have been fixing all day."
UNANCHORED = "Implementing recommendations 1 and 2 now: ... The gate and the ledger."


def _keep_once():
    """One _keep() call over an anchored row and a paraphrase row.

    Returns (records, log_rows, new, covered): `records` is [(filename, parsed row)] read while
    the temp dir still exists -- paths handed back from a deleted directory cannot be read, which
    is the bug in the t707 file this one replaces. `log_rows` is every ledger row _keep emitted.
    """
    records, log_rows = [], []
    with tempfile.TemporaryDirectory(prefix="sr-anchor-gate-") as td:
        td = Path(td)
        findings = td / "findings"
        req = {"harness": "claude", "id": "6991f526-test"}
        req_file = td / "req.json"
        req_file.write_text(json.dumps(req), encoding="utf-8")
        orig = (sr.FINDINGS, sr._log, sr._archive_cover)
        try:
            sr.FINDINGS = findings
            sr._log = log_rows.append                 # capture, never write the live ledger.jsonl
            sr._archive_cover = lambda quote: ""      # keep the cover search off the live task store

            # The session text we simulate: the anchored sentence is in it, the paraphrase is not
            # -- the same signal extract() derives quote_unverified from.
            text = "We changed the settings and then committed.\n" + ANCHORED + "\n"
            rows = [{"kind": "unresolved", "covered_by": "", "quote": ANCHORED},
                    {"kind": "unresolved", "covered_by": "", "quote": UNANCHORED}]
            for r in rows:                            # mirror extract(), session_review.py:465
                _sentence, found = sr._anchor(r["quote"], text)
                if not found:
                    r["quote_unverified"] = True
            assert rows[0].get("quote_unverified") is None, \
                "the fixture's own signal is wrong: the real sentence was flagged unverified"
            assert rows[1].get("quote_unverified") is True, \
                "the fixture's own signal is wrong: the paraphrase was not flagged"

            new, covered = sr._keep(req_file, req, rows, None, None)
            for f in sorted(findings.rglob("*.json")):   # read INSIDE the temp dir
                try:
                    records.append((f.name, json.loads(f.read_text(encoding="utf-8"))))
                except (OSError, ValueError) as e:
                    records.append((f.name, {"__unreadable__": str(e)}))
        finally:
            sr.FINDINGS, sr._log, sr._archive_cover = orig
    return records, log_rows, new, covered


def test_anchor_flags_the_paraphrase_not_the_real_sentence():
    # The seam the gate acts on must be real evidence, not the oracle's assumption: _anchor()
    # finds the sentence that was said and rejects the paraphrase that was never said.
    text = ANCHORED + "\n"
    assert sr._anchor(ANCHORED, text)[1] is True, "_anchor must find the sentence it appears in"
    assert sr._anchor(UNANCHORED, text)[1] is False, "_anchor must reject a sentence never said"


def test_refuses_unanchored_and_admits_anchored():
    records, log_rows, new, covered = _keep_once()

    # (a) the unanchored quote must NOT be persisted as a finding, by its exact value
    un = [n for n, rec in records if rec.get("quote") == UNANCHORED]
    assert not un, "unanchored quote was written to findings/: %s" % (un,)

    # (b) the gate must not over-refuse: the anchored quote still becomes exactly one finding,
    #     carrying its own quote and a valid disposition (a file that says nothing is not a finding)
    an = [(n, rec) for n, rec in records if rec.get("quote") == ANCHORED]
    assert len(an) == 1, "anchored quote should yield exactly one finding, got %d" % len(an)
    assert an[0][1].get("disposition") in sr.DISPOSITIONS, \
        "finding must carry a valid disposition: %s" % (an[0][1],)

    # (c) the refusal is recorded on the ledger, with the reason, naming the quote it refused
    refused = [r for r in log_rows if r.get("event") == "refused"
               and r.get("reason") == "quote_unverified"]
    assert len(refused) == 1, \
        "expected exactly one refused row (reason=quote_unverified), got %s" % (log_rows,)
    assert refused[0].get("quote") == UNANCHORED, \
        "the refused row must name the unanchored quote: %s" % (refused[0],)

    # (d) downstream consumption: the anchored row is logged as a finding, the refused one is not
    found = [r for r in log_rows if r.get("event") == "finding"]
    assert any(r.get("quote") == ANCHORED for r in found), \
        "anchored row not logged as a finding: %s" % (log_rows,)
    assert not any(r.get("quote") == UNANCHORED for r in found), \
        "unanchored row was logged as a finding: %s" % (log_rows,)

    # (e) a refused row is not counted, and a bare refusal invents no coverage
    assert new == 1, "a refused row must not count as a new finding; new=%d" % new
    assert covered == 0, "neither row is covered; covered=%d" % covered


def test_no_live_state_is_written():
    # The fixture must leave the live queue untouched: the set of live finding files is the same
    # before and after. (t659 was refused on 2026-10-05 because its draft oracle wrote into the
    # live workspace/session-reviews/findings/.)
    live = sr.FINDINGS
    before = sorted(p.name for p in live.glob("*.json")) if live.is_dir() else []
    _keep_once()
    after = sorted(p.name for p in live.glob("*.json")) if live.is_dir() else []
    assert before == after, \
        "the fixture wrote into the live findings dir: %s" % (set(after) ^ set(before),)


if __name__ == "__main__":
    import traceback
    tests = [test_anchor_flags_the_paraphrase_not_the_real_sentence,
             test_refuses_unanchored_and_admits_anchored,
             test_no_live_state_is_written]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (fn.__name__, e))
        except Exception:
            failed += 1
            traceback.print_exc()
            print("FAIL %s: exception" % fn.__name__)
    print("%d test(s) failed" % failed)
    sys.exit(1 if failed else 0)
