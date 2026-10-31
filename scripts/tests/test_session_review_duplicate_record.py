#!/usr/bin/env python3
"""t795: the dedupe endpoint in scripts/session_review.py records the refusal.

_keep() (:808) refuses a finding whose key file already exists (:836-839) and writes
nothing anywhere. workspace/rca/403e76293ce0.md section 5.3: without a row at that
point, "we dedupe by work" cannot be shown in force at the next incident and the
duplicate class cannot be counted down.

RED ON MAIN: step 2 (no duplicates row at the refusal point) and step 5 (no
finding-duplicate event in the ledger). Temp dirs and fakes only; no model, no repo write.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as sr                          # noqa: E402

REQ = "claude-6cc34e3b-0c16-4f3b-8f78-98893304830f.json"
SID = "6cc34e3b-0c16-4f3b-8f78-98893304830f"
Q1 = "probe env3  ->  findings  ->  patch env2  ->  re-clone env3  ->  probe again"
Q2 = "Probe env3 -> findings -> patch env2 -> re-clone env3 -> probe again"

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("FAIL: " + msg)


def sig(q):
    """The normalisation scripts/session_review.py _anchor() already uses (:480), case-folded
    as it is at :485/:494: collapse whitespace, strip the quotes, lower-case."""
    return re.sub(r"\s+", " ", str(q)).strip().strip('"').strip().lower()


def row(q):
    return {"kind": "off-flow", "quote": q,
            "where": "assistant, turn 12 of 839",
            "why_missed": "a named multi-step procedure with no node on the chart"}


def dups(tmp):
    # Where the suite TELLS the implementation to write (sr.DUPLICATES = sr.BASE / ..., line 64),
    # not tmp / "duplicates.jsonl" -- the two differed and no implementation could satisfy both
    # (the Senior's preflight objection, 2026-10-08; fixed by Claude 2026-10-09).
    p = tmp / "workspace" / "session-reviews" / "duplicates.jsonl"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def main():
    tmp = Path(tempfile.mkdtemp(prefix="t795-"))
    sr.BASE = tmp / "workspace" / "session-reviews"
    sr.FINDINGS = sr.BASE / "findings"
    sr.LEDGER = sr.BASE / "ledger.jsonl"
    sr.DUPLICATES = sr.BASE / "duplicates.jsonl"
    sr._archive_cover = lambda q: ""          # the live task archive is out of scope
    req = {"id": SID, "harness": "claude"}
    reqf = tmp / REQ                          # _keep uses only .name
    noop = lambda a, b: None

    key1, key2 = sr._fkey(REQ, Q1), sr._fkey(REQ, Q2)
    check(key1 != key2, "fixture broken: the two wordings must hash apart today, else the "
                        "class this record exists to count has no instances")

    # 1 FIRST occurrence: the finding is written; the dedupe endpoint records NOTHING.
    got = sr._keep(reqf, req, [row(Q1)], note_task=noop, file_task=noop)
    check(got == (1, 0), "first occurrence: _keep returned %r, expected (1, 0)" % (got,))
    check(not dups(tmp), "a duplicates row was written for a finding that was NOT refused; "
                         "the record belongs at the refusal point (:838-839)")

    # 2 THE REFUSAL, one part later: a row is written, and nothing else moves.
    req["parts_done"] = 1
    got = sr._keep(reqf, req, [row(Q1)], note_task=noop, file_task=noop)
    check(got == (0, 0), "refusal: _keep returned %r, expected (0, 0) -- recording must not "
                         "change what _keep reports" % (got,))
    rows = dups(tmp)
    check(len(rows) == 1, "no finding-duplicate row at the refusal point: %d row(s) in %s"
                          % (len(rows), sr.DUPLICATES))
    d = rows[0] if rows else {}
    check(d.get("request") == REQ, "duplicates row request: %r, expected %r" % (d.get("request"), REQ))
    check(d.get("session") == SID, "duplicates row session: %r, expected %r" % (d.get("session"), SID))
    check(d.get("key") == key1, "duplicates row key: %r, expected the refused key %r" % (d.get("key"), key1))
    check(d.get("duplicate_of") == key1,
          "duplicates row duplicate_of: %r, expected the key of the finding already on disk (%r)"
          % (d.get("duplicate_of"), key1))
    check((sr.FINDINGS / ("%s.json" % d.get("duplicate_of"))).is_file(),
          "duplicate_of %r names no finding file on disk: the field must point at the record "
          "it duplicates" % (d.get("duplicate_of"),))
    check(d.get("part") == 2, "duplicates row part: %r, expected 2 (parts_done is 1; part = "
                             "parts_done + 1, per scripts/session_review.py:424,464)" % (d.get("part"),))
    check(d.get("examined") == 1, "duplicates row examined: %r, expected 1 (the rows this call "
                                 "looked at, the t658 count)" % (d.get("examined"),))
    check(isinstance(d.get("terms"), list) and "findings" in d.get("terms", []),
          "duplicates row terms %r must be the quote's own searchable terms, not empty"
          % (d.get("terms"),))
    check(d.get("terms") == sr._terms(Q1), "duplicates row terms: %r, expected _terms(quote) %r"
                                           % (d.get("terms"), sr._terms(Q1)))
    check(d.get("signature") == sig(Q1), "duplicates row signature: %r, expected the quote normalised "
                                         "by _anchor's recipe (%r)" % (d.get("signature"), sig(Q1)))
    check(d.get("signature") != Q1, "duplicates row signature is the raw quote; it must be the "
                                    "normalised form, so two wordings of one procedure count as one class")
    ts = d.get("ts")
    check(isinstance(ts, str) and ts.strip() == ts and bool(ts.strip()),
          "duplicates row ts: %r, expected a non-empty timestamp string" % (ts,))

    # 3 A SECOND WORDING is a new finding today (identity is the quote): not a refusal.
    req["parts_done"] = 1
    got = sr._keep(reqf, req, [row(Q2)], note_task=noop, file_task=noop)
    check(got == (1, 0), "second wording: _keep returned %r, expected (1, 0)" % (got,))
    check(len(dups(tmp)) == 1, "a duplicates row was written for a finding that was written, not refused")

    # 4 BOTH wordings refused in one later part: two rows, two keys, ONE signature, examined 2.
    req["parts_done"] = 2
    got = sr._keep(reqf, req, [row(Q1), row(Q2)], note_task=noop, file_task=noop)
    check(got == (0, 0), "both-refused call: _keep returned %r, expected (0, 0)" % (got,))
    rows = dups(tmp)
    check(len(rows) == 3, "expected 3 duplicates rows after two refusals in one call, found %d: %r"
                          % (len(rows), [r.get("key") for r in rows]))
    last = rows[-2:]
    check(sorted([r.get("key") for r in last]) == sorted([key1, key2]),
          "the two rows of one examined call must name the two refused keys, found %r"
          % ([r.get("key") for r in last],))
    check(len({r.get("signature") for r in last}) == 1,
          "two wordings of one procedure must share ONE signature, else the class cannot be "
          "counted down: %r" % ([r.get("signature") for r in last],))
    check(all(r.get("examined") == 2 for r in last), "rows of a two-row call must record examined=2: %r"
                                                     % ([r.get("examined") for r in last],))
    check(all(r.get("part") == 3 for r in last), "rows of the parts_done=2 call must record part 3: %r"
                                                 % ([r.get("part") for r in last],))

    # 5 THE LEDGER carries the event, and the finding count never moved.
    led = []
    if sr.LEDGER.is_file():
        for line in sr.LEDGER.read_text(encoding="utf-8").splitlines():
            if line.strip():
                led.append(json.loads(line))
    ev = [r for r in led if r.get("event") == "finding-duplicate"]
    check(len(ev) == 3, "ledger: %d finding-duplicate event(s), expected 3 (one per refusal)" % len(ev))
    check(all(r.get("key") for r in ev), "a finding-duplicate ledger event names no key: %r" % (ev,))
    check(all(r.get("request") == REQ for r in ev),
          "finding-duplicate ledger events must name the request: %r" % ([r.get("request") for r in ev],))
    n_find = sum(1 for r in led if r.get("event") == "finding")
    check(n_find == 2, "the record changed what _keep files: expected 2 'finding' ledger events, got %d" % n_find)
    check(len(list(sr.FINDINGS.glob("*.json"))) == 2,
          "the record changed what _keep writes: expected 2 finding files, got %d"
          % len(list(sr.FINDINGS.glob("*.json"))))

    print("t795 finding-duplicate record: %d assertion(s) failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
