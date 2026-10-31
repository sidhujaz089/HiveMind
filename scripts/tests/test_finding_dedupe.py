#!/usr/bin/env python3
"""t763: one sentence, one finding -- scripts/session_review.py keys a finding on
_fkey(req_file.name, raw quote) at :407-408 (sha1 of name + newline + quote, 12 hex chars), and
_keep() at :950 files a finding only when that key is new (the exists() test at :980 covers
findings/ and findings/done/). The first key argument is the PART FILE, and a session is reviewed
as several part files -- the real names in workspace/session-reviews/pending/ are
"claude-<sid>.json" plus "claude-<sid>--from-244.json", "claude-<sid>--from-289.json" -- so one
sentence quoted in two parts of ONE session mints two keys, two findings and two Senior RCA slots.
The instance this task is filed under is exactly that: findings/done/35f883ff9294.json (request
"claude-6ed72651-55a7-4a37-ab38-1a483bcb1740.json", its own verified read) carries the first-turn
sentence quoted below, and its twin 7668c6830267.json carries the same words; workspace/rca/403e76293ce0.md
names the same shape across one 6-part session (388878f77b79 23:05:06Z, 403e76293ce0 00:32:19Z).

THE FIX: strip the review-part suffix from the request name before hashing, so every part of one
session shares one finding namespace. Quote-surface normalisation (markdown, case, whitespace) is
the NEXT scope: it is the contract scripts/tests/test_session_review_duplicate_record.py pins, and
the two must move together.

RED ON MAIN for the missing change -- cases 1, 2, 5 and 7 mint two findings from one sentence
today. Cases 3, 4, 6 and 8 are the anti-vacuity guards and pass today.

Run as: python3 scripts/tests/test_finding_dedupe.py
(pytest is NOT installed; the __main__ block runs every test and exits non-zero on a failure.)
No live state: FINDINGS, LEDGER, DONE and _archive_cover are redirected into a temp dir, the
pattern scripts/tests/test_finding_anchors.py:53-59 and test_session_review_duplicate_record.py:62-65
already use. No model call, no subprocess, no network, nothing written outside the temp dir.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as sr                          # noqa: E402

SID = "6ed72651-55a7-4a37-ab38-1a483bcb1740"
SID_B = "745a17ba-a559-4e99-81f6-3f8ec745ad46"
# the real part-file naming, from ls workspace/session-reviews/pending/
BASE = "claude-6ed72651-55a7-4a37-ab38-1a483bcb1740.json"
PART1 = "claude-6ed72651-55a7-4a37-ab38-1a483bcb1740--from-244.json"
PART2 = "claude-6ed72651-55a7-4a37-ab38-1a483bcb1740--from-289.json"
BASE_B = "claude-745a17ba-a559-4e99-81f6-3f8ec745ad46.json"

SENTENCE = ("I just wanted to to get scoped work to Gemma, so can we dedicate this "
            "to preflight?")                    # findings/done/35f883ff9294.json, field "quote"
LANE = ("Committing, then switching over: restart the manager queue so it leaves station 2, "
        "and run one real tick so the Senior starts its first preflight.")   # rca/6a87ffecd80c
POP_A = "About 40 scripts read the config through the legacy path."
POP_B = "The other ~30 call sites (chat UI, probes, health checks) move later."

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("FAIL: " + msg)


class sandbox:
    """Redirect every path the module writes at a temp dir, and stub the one live-state read
    _keep reaches. No real file under the repository is opened, written or moved."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="t763-"))
        self.saved = (sr.FINDINGS, sr.LEDGER, sr.DONE, sr._archive_cover)
        sr.FINDINGS = self.dir / "findings"
        sr.LEDGER = self.dir / "ledger.jsonl"
        sr.DONE = self.dir / "done"
        sr._archive_cover = lambda quote: ""     # the live task archive is out of scope
        return self

    def __exit__(self, *exc):
        sr.FINDINGS, sr.LEDGER, sr.DONE, sr._archive_cover = self.saved
        shutil.rmtree(str(self.dir), ignore_errors=True)
        return False

    def names(self):
        return sorted(p.name for p in sr.FINDINGS.glob("*.json"))

    def records(self):
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(sr.FINDINGS.glob("*.json"))]

    def events(self):
        if not sr.LEDGER.is_file():
            return []
        return [json.loads(line) for line in
                sr.LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


def row(quote, kind="off-flow"):
    return {"kind": kind, "quote": quote, "where": "User, first turn",
            "why_missed": "a first-turn request the chart does not answer", "covered_by": ""}


def req(sid=SID):
    return {"id": sid, "harness": "claude"}


def test_one_sentence_quoted_in_two_parts_files_one_finding():
    with sandbox() as s:
        r = req()
        a, _ = sr._keep(Path(BASE), r, [row(SENTENCE)])
        b, _ = sr._keep(Path(PART1), r, [row(SENTENCE)])
        check(a == 1, "first part filed %r new finding(s); expected 1" % (a,))
        check(b == 0, "the same first-turn sentence filed again from part %s of the SAME session "
                      "added %r finding(s); expected 0 -- _fkey keys on the part file name, so "
                      "one sentence quoted in two parts of session %s buys two Senior RCA slots"
                      % (PART1, b, SID))
        check(len(s.names()) == 1, "expected 1 finding file for one sentence, found %r" % (s.names(),))


def test_three_parts_of_one_session_still_file_one_finding():
    with sandbox() as s:
        r = req()
        got = [sr._keep(Path(n), r, [row(SENTENCE)])[0] for n in (BASE, PART1, PART2)]
        check(got == [1, 0, 0], "one sentence across the three real part-file names of one session "
                                "filed %r; expected [1, 0, 0] -- a 6-part review buys six RCA slots "
                                "for one sentence" % (got,))
        check(len(s.names()) == 1, "expected 1 finding file, found %r" % (s.names(),))


def test_a_different_quote_in_a_later_part_is_still_filed():
    with sandbox() as s:
        r = req()
        sr._keep(Path(BASE), r, [row(SENTENCE)])
        n, _ = sr._keep(Path(PART1), r, [row(LANE)])
        check(n == 1, "a genuinely different quote from a later part was refused (filed %r); a key "
                      "built from the session id alone files one finding per session and hides "
                      "every finding after the first" % (n,))
        check(len(s.names()) == 2, "expected 2 finding files, found %r" % (s.names(),))


def test_two_populations_named_in_one_paragraph_are_both_kept():
    with sandbox() as s:
        r = req()
        a, _ = sr._keep(Path(BASE), r, [row(POP_A, "deferral")])
        b, _ = sr._keep(Path(PART1), r, [row(POP_B, "deferral")])
        check(a == 1 and b == 1, "the two denominators of one population were collapsed (filed "
                                 "%r, %r); expected 1, 1 -- quote-level dedupe must not merge the "
                                 "40-scripts / ~30-call-sites pair (workspace/rca/63b7af778e85.md); "
                                 "that pair is the work-signature scope, not this one" % (a, b))
        check(len(s.names()) == 2, "expected 2 finding files, found %r" % (s.names(),))


def test_an_archived_finding_refuses_the_quote_from_a_later_part():
    with sandbox() as s:
        r = req()
        sr._keep(Path(BASE), r, [row(SENTENCE)])
        done = sr.FINDINGS / "done"
        done.mkdir(parents=True, exist_ok=True)
        for p in sorted(sr.FINDINGS.glob("*.json")):
            shutil.move(str(p), str(done / p.name))
        n, _ = sr._keep(Path(PART1), r, [row(SENTENCE)])
        check(n == 0, "re-filing an ARCHIVED sentence from a later part minted %r new finding(s); "
                      "expected 0 -- _keep already refuses on findings/done/, so the shared key "
                      "must reach that check" % (n,))
        check(s.names() == [], "re-filing an archived sentence wrote into the findings dir: %r"
                               % (s.names(),))


def test_the_written_finding_keeps_its_provenance_and_a_12_char_key():
    with sandbox() as s:
        sr._keep(Path(PART1), req(), [row(SENTENCE)])
        recs = s.records()
        check(len(recs) == 1, "expected 1 finding file, found %r" % (len(recs),))
        rec = recs[0] if recs else {}
        check(rec.get("session") == SID,
              "finding record session: %r, expected %r" % (rec.get("session"), SID))
        check(rec.get("request") == PART1, "finding record request: %r, expected the part file it "
                                           "came from (%r) -- the key normalises, the provenance "
                                           "fields stay" % (rec.get("request"), PART1))
        check(rec.get("kind") == "off-flow",
              "finding record kind: %r, expected off-flow" % (rec.get("kind"),))
        check(rec.get("quote") == SENTENCE, "the model words must stay the quote verbatim: %r"
                                            % (rec.get("quote"),))
        check(rec.get("key") and len(rec["key"]) == 12,
              "finding key %r must be the 12-char id the findings dir names its files with"
              % (rec.get("key"),))
        check((sr.FINDINGS / (rec.get("key", "") + ".json")).is_file(),
              "the key is the on-disk name: no file named after the recorded key")


def test_the_refused_part_mints_no_finding_event():
    with sandbox() as s:
        r = req()
        sr._keep(Path(BASE), r, [row(SENTENCE)])
        sr._keep(Path(PART1), r, [row(SENTENCE)])
        ev = [e for e in s.events() if e.get("event") == "finding"]
        check(len(ev) == 1, "the ledger carries %r finding event(s) for one sentence filed once; "
                            "expected 1 -- the count of finding events is how the backlog is "
                            "measured, so the second event is the defect this task is filed under"
                            % (len(ev),))
        check(all(e.get("key") for e in ev), "a finding ledger event names no key: %r" % (ev,))


def test_a_second_session_mints_its_own_finding():
    with sandbox() as s:
        sr._keep(Path(BASE), req(SID), [row(SENTENCE)])
        n, _ = sr._keep(Path(BASE_B), req(SID_B), [row(SENTENCE)])
        check(n == 1, "the same sentence in a DIFFERENT session was refused (filed %r); the key "
                      "stays session-scoped, it is not global" % (n,))
        check(len(s.names()) == 2, "expected 2 finding files (one per session), found %r" % (s.names(),))


def main():
    tests = sorted((k, v) for k, v in list(globals().items())
                   if k.startswith("test_") and callable(v))
    for name, fn in tests:
        try:
            fn()
        except Exception as e:
            FAILS.append("%s raised %s: %s" % (name, type(e).__name__, e))
            print("FAIL: %s raised %s: %s" % (name, type(e).__name__, e))
    print("t763 finding dedupe: %d of %d case(s) failed" % (len(FAILS), len(tests)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
