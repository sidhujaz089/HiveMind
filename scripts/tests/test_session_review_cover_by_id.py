"""t1314 -- the session-review cover scorer must resolve coverage by task id, not word overlap.

RED ON MAIN, for the missing change: prove_cover() (scripts/session_review.py:758) accepts a task
cover only when the item shares COVER_SHARE (0.2, line 745) of its topic words with the task's
title. Finding 1002504f96bd (workspace/rca/1002504f96bd.md:23) is a deferral whose own context
names t234 by id -- "t234 — \"Golden set v0, labelled by Jas, with a date on it\" It's the parent
task; its fate decides the other." -- and shares no content word with that title, so it scores
0.00, is downgraded to `missed`, and _keep (scripts/session_review.py:1250-1258) mints it as a
live finding for a task closed on 2026-10-05 (git 1853bafb). Task identity is an id; the test
compares prose.

Every value below is real: the quote, the context and the title are the ones on that record.
No live state: task_text is stubbed, FINDINGS/_log/DEFERRAL_LEDGER are redirected into a temp dir.
"""

import json
import re
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as sr  # noqa: E402

# The REAL title of t234 and a real title from the same RCA family (t777), so no synthetic
# phrasing can be gamed. Distinct strings on purpose: a stub that answers every id the same
# way passes tests 2 and 3 by accident.
TITLES = {
    "t234": "Golden set v0, labelled by Jas, with a date on it",
    "t777": "self-contaminated-measurement: a model must not evaluate its own harness",
}
# The quote and context of finding 1002504f96bd, verbatim from workspace/rca/1002504f96bd.md:23,35.
QUOTE = ("The labelling work is *done* — all 35 labels are on the file — "
         "but the task is still queued.")
CONTEXT = ('t234 — "Golden set v0, labelled by Jas, with a date on it" It\'s the parent task; '
           "its fate decides the other.")

_real_task_text = sr.task_text


def _stubbed():
    sr.task_text = lambda tid: TITLES.get(tid, "")


def _restore():
    sr.task_text = _real_task_text


def test_id_cited_in_context_is_proven_cover():
    """The finding cites its covering task by id in its own context: that is proven cover."""
    _stubbed()
    try:
        row = {"verdict": "covered", "cover": "t234", "quote": QUOTE, "context": CONTEXT,
               "kind": "deferral", "what": "close out the golden set labelling task"}
        out = sr.prove_cover(row)
    finally:
        _restore()
    assert out.get("verdict") == "covered", (
        f"an id the finding cites by number must be proven cover, got verdict "
        f"{out.get('verdict')!r} with overruled {out.get('overruled')!r}")
    assert out.get("cover") == "t234", f"the cover must survive as the id cited, got {out.get('cover')!r}"
    assert not out.get("overruled"), f"a proven cover is not overruled: {out.get('overruled')!r}"
    reason = str(out.get("cover_reason") or "")
    assert "by id" in reason and "t234" in reason, (
        f"the record must say the cover was resolved by id (RCA 1002504f96bd section 5 greps for "
        f"'cover t[0-9]* by id'), got {reason!r}")
    assert "shares" not in reason, f"the id reason must not be the word-overlap veto: {reason!r}"


def test_id_not_cited_by_the_finding_gets_the_lexical_test():
    """A cover the finding's own words never name is not proven by id: the 0.2 floor still applies."""
    _stubbed()
    try:
        row = {"verdict": "covered", "cover": "t777",
               "quote": "rotate the nightly backup copies before the retention window closes",
               "context": "we talked about offsite copies of the workspace",
               "kind": "deferral", "what": "rotate backups offsite"}
        out = sr.prove_cover(row)
    finally:
        _restore()
    assert out.get("verdict") == "missed", (
        f"a task id the item never cites must not become cover for free: {out.get('verdict')!r}")
    assert not out.get("cover"), f"a refused cover is cleared, got {out.get('cover')!r}"
    assert out.get("model_cover") == "t777", f"the model's pick is kept for the record: {out!r}"
    assert "shares" in str(out.get("overruled") or ""), (
        f"the word-overlap veto must still be the stated reason: {out.get('overruled')!r}")


def test_lexical_cover_still_proven_when_no_id_is_cited():
    """A cover proven by shared subject words is still accepted: the lexical route is kept."""
    _stubbed()
    try:
        row = {"verdict": "covered", "cover": "t777",
               "quote": "a model must not evaluate its own harness",
               "context": "the measurement is self-contaminated when the same model grades it",
               "kind": "deferral", "what": "separate the grader from the graded"}
        out = sr.prove_cover(row)
    finally:
        _restore()
    assert out.get("verdict") == "covered", f"word overlap must still prove cover: {out!r}"
    assert out.get("cover") == "t777", f"the cover must be kept: {out.get('cover')!r}"
    assert not out.get("overruled"), f"a proven cover is not overruled: {out.get('overruled')!r}"


def test_commit_cover_must_not_be_a_session_closeout():
    """The commit branch of prove_cover is untouched: a commit that does not exist fixes nothing."""
    row = {"verdict": "covered", "cover": "0000000", "quote": QUOTE, "context": CONTEXT,
           "kind": "deferral", "what": "close out the golden set labelling task"}
    out = sr.prove_cover(row)
    assert out.get("verdict") == "missed", f"an unresolvable commit hash is not a cover: {out!r}"
    assert "no such commit" in str(out.get("overruled") or ""), (
        f"the stated reason must name the cause: {out.get('overruled')!r}")


def test_id_cover_is_recorded_on_the_task_not_minted_as_a_finding():
    """The value is USED downstream: the id-matched cover reaches _keep as covered_by, the covering
    task gets the note, the cover reason is logged, and no finding is minted. Today the row arrives
    with covered_by \"\" (prove_cover cleared it) and _keep mints workspace/.../findings/<key>.json."""
    _stubbed()
    saved_findings, saved_log, saved_ledger = sr.FINDINGS, sr._log, sr.DEFERRAL_LEDGER
    notes, logged = [], []
    sr._log = lambda row: logged.append(row)
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sr.FINDINGS = tmp / "findings"
            sr.DEFERRAL_LEDGER = tmp / "deferrals.jsonl"
            req_file = tmp / "dsh-session-f1776b49.json"
            req = {"id": "session-f1776b49-c813-4194-a141-9f158bea671a", "harness": "dsh"}
            req_file.write_text(json.dumps(req), encoding="utf-8")
            row = {"verdict": "covered", "cover": "t234", "quote": QUOTE, "context": CONTEXT,
                   "kind": "deferral", "what": "close out the golden set labelling task"}
            settled = sr.prove_cover(row)
            # built the way extract_scripted() builds `found` (scripts/session_review.py:916-920)
            found = [{"kind": settled["kind"], "quote": settled["quote"],
                      "context": settled["context"], "where": "assistant, turn 12 of 13",
                      "why_missed": settled["what"], "covered_by": settled["cover"]}]
            new, covered = sr._keep(req_file, req, found, note_task=lambda t, text: notes.append((t, text)))
            assert new == 0, f"a finding whose target is covered by id must not be minted: {new} minted"
            assert not (sr.FINDINGS / "done").exists() or not list((sr.FINDINGS).glob("*.json")), (
                "no finding file may be written for an id-covered item")
            assert covered == 1, f"the covered count must register the id cover, got {covered}"
            assert notes and notes[0][0] == "t234", (
                f"the covering task must be noted with its id, got {notes!r}")
            assert QUOTE in notes[0][1], f"the note must carry the quote it covers: {notes!r}"
            ev = [r for r in logged if r.get("event") == "covered"]
            assert ev and ev[0].get("task") == "t234", (
                f"the ledger row must name the covering task, got {logged!r}")
            assert "by id" in str(ev[0].get("cover_reason") or ""), (
                f"the covered row must record that cover was resolved by id, got {ev!r}")
    finally:
        sr._log, sr.DEFERRAL_LEDGER = saved_log, saved_ledger
        sr.FINDINGS = saved_findings
        _restore()


if __name__ == "__main__":
    _tests = sorted((n, f) for n, f in list(globals().items())
                    if n.startswith("test_") and callable(f))
    _failed = 0
    for _name, _fn in _tests:
        try:
            _fn()
            print(f"ok   {_name}")
        except Exception:  # noqa: BLE001 -- a failing suite must say which assertion and why
            _failed += 1
            print(f"FAIL {_name}")
            traceback.print_exc()
    print(f"{len(_tests) - _failed}/{len(_tests)} passed")
    sys.exit(1 if _failed else 0)
