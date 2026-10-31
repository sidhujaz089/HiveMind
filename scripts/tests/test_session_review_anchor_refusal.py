#!/usr/bin/env python3
"""t707 oracle: a session-review finding whose quote does not anchor must be REFUSED.

RCA 87928c9c6afc (rule 6a): "A finding is not a finding unless its quote anchors to the
transcript." extract() already flags such a row quote_unverified (session_review.py:454) but
_keep() still writes findings/<key>.json and logs event=finding, so a paraphrase becomes a
first-class finding and an RCA. The fix: _keep() must refuse quote_unverified rows -- write a
ledger row event=refused,reason=quote_unverified and NO finding file -- so rca() has nothing to
consume.

Run as: python3 scripts/tests/test_session_review_anchor_refusal.py
(pytest is NOT installed; the __main__ block below runs the tests and exits non-zero on any
failure.) No live state: FINDINGS, _log and _archive_cover are redirected to a temp dir / a
recorder; no model call, no subprocess, no network.
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


def _run():
    """One _keep() call over an anchored row and a paraphrase row.

    Returns (finding_files, log_rows, new, covered).
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        findings = td / "findings"
        log_rows = []
        orig = (sr.FINDINGS, sr._log, sr._archive_cover)
        try:
            sr.FINDINGS = findings
            sr._log = log_rows.append                       # capture, do not write live ledger.jsonl
            sr._archive_cover = lambda quote: ""           # keep the cover search off the live task store

            # The only line of the session the anchored quote appears on, so _anchor() finds it
            # and the paraphrase (never said) does not -- the same signal extract() sets from.
            text = "We changed the settings and then committed.\n" + ANCHORED + "\n"
            rows = [
                {"kind": "unresolved", "covered_by": "", "quote": ANCHORED},
                {"kind": "unresolved", "covered_by": "", "quote": UNANCHORED},
            ]
            for r in rows:                                  # mirror session_review.py:452-454
                _sentence, found = sr._anchor(r["quote"], text)
                if not found:
                    r["quote_unverified"] = True

            req_file = td / "req.json"
            req_file.write_text(json.dumps({"harness": "claude", "id": "6991f526-test"}), encoding="utf-8")
            new, covered = sr._keep(req_file, {"harness": "claude", "id": "6991f526-test"}, rows, None, None)
        finally:
            sr.FINDINGS, sr._log, sr._archive_cover = orig
        files = sorted(findings.rglob("*.json")) if findings.exists() else []
        return files, log_rows, new, covered


def test_anchor_flags_the_paraphrase_not_the_real_sentence():
    # Guard the seam the gate relies on: _anchor() must be True for the real sentence and False
    # for the paraphrase, so the gate has a true signal to act on (not a signal of its own bug).
    text = ANCHORED + "\n"
    assert sr._anchor(ANCHORED, text)[1] is True
    assert sr._anchor(UNANCHORED, text)[1] is False


def test_refuses_unanchored_and_admits_anchored():
    files, log_rows, new, covered = _run()

    # (a) behaviour, not shape: the unanchored quote must NOT be persisted as a finding file
    with_unanchored = [f for f in files if UNANCHORED in f.read_text(encoding="utf-8", errors="replace")]
    assert not with_unanchored, "unanchored quote was written to findings/: %s" % [str(f) for f in with_unanchored]

    # (b) the gate must not over-refuse: the anchored quote MUST still become a finding
    with_anchored = [f for f in files if ANCHORED in f.read_text(encoding="utf-8", errors="replace")]
    assert len(with_anchored) == 1, "anchored quote should yield exactly one finding, got %d" % len(with_anchored)
    # ...and that finding records the anchored quote with a real disposition
    rec = json.loads(with_anchored[0].read_text(encoding="utf-8"))
    assert rec.get("quote") == ANCHORED
    assert rec.get("disposition") in sr.DISPOSITIONS, "finding must carry a disposition: %s" % rec

    # (c) the refusal is recorded on the ledger with the RCA's reason, naming the unanchored quote
    refused = [r for r in log_rows if r.get("event") == "refused" and r.get("reason") == "quote_unverified"]
    assert len(refused) == 1, "expected exactly one refused row (reason=quote_unverified), got %s" % log_rows
    assert UNANCHORED in refused[0].get("quote", ""), "refused row must name the unanchored quote: %s" % refused[0]

    # (d) the anchored row is logged as a finding; the unanchored row is NOT
    found_rows = [r for r in log_rows if r.get("event") == "finding"]
    assert any(ANCHORED in (r.get("quote") or "") for r in found_rows), "anchored row not logged as finding: %s" % log_rows
    assert not any(UNANCHORED in (r.get("quote") or "") for r in found_rows), "unanchored row was logged as a finding"

    # (e) a refused row must not count as a new finding
    assert new == 1, "a refused row must not count as a new finding; new=%d" % new
    assert covered == 0, "neither row is covered; covered=%d" % covered


if __name__ == "__main__":
    import traceback
    tests = [test_anchor_flags_the_paraphrase_not_the_real_sentence,
             test_refuses_unanchored_and_admits_anchored]
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
