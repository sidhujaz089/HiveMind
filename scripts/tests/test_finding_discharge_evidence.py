#!/usr/bin/env python3
"""t1046 -- the reviewer's discharge evidence is written at the finding write.

Red on main for the missing change and for it alone: scripts/session_review.py _keep() (line 1024)
writes the finding file (line 1058) and a count-only ledger row (line 1061), and writes NO per-finding
evidence row anywhere. workspace/run-logs/session-review-findings.jsonl does not exist on main
(verified with ls), so every assertion below fails on the missing writer. Nothing here imports a
name this task does not add, no signature is changed, and no assertion pins today's defect.

Why the row is what the finding proves is missing: the shipped record stamps `candidates_consulted`
as the whole open+done population, so it cannot tell "searched 900, matched none" from
"searched nothing". The row must carry what was searched, what matched, and the verdict.

Fields the endpoint CANNOT compute are asserted ABSENT on purpose: model, done_reason, exit and
seconds live at the ask() return -- t1040/t1003's endpoint. search_terms is the probe built in
scripted_items() (line 745) and is not carried on the row; the semantic hit reaches _keep()
flattened into "tid [status] title", its score gone. An empty key for any of them would read as
recorded evidence for a search that was never recorded.

No model, no live server, no live queue, no process left running, no fixed /tmp path, no write to
the repository: every path session_review writes is redirected into a temp dir.
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as sr  # noqa: E402

FAILS = []

# The real quote the finding was minted from (workspace/rca/6c1427418393.md line 20). It carries no
# identifier-shaped token, so _archive_cover() returns "" and the oracle never reads the live task archive.
QUOTE = ("I'll fold all of this into the plan, add the 4 manager candidates, and set the "
         "shortlist rule to pure coding with your family-dedup logic.")
SID = "aeef1f57-5a5c-47b9-9d13-6a9c6fc9b373"
REQ_NAME = "claude-aeef1f57-5a5c-47b9-9d13-6a9c6fc9b373.json"
SENTINEL = "not-carried-to-this-endpoint (probe built in scripted_items, scripts/session_review.py line 745)"
POPULATION = ["t900", "t901", "t902"]


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def sandbox(tmp):
    """Point every path session_review writes at into tmp. Returns (req, req_file)."""
    base = tmp / "workspace" / "session-reviews"
    sr.BASE = base
    sr.FINDINGS = base / "findings"
    sr.LEDGER = base / "ledger.jsonl"
    sr.FOR_JAS = base / "for-jas"
    # A module-level constant, read at call time, so a test can aim the writer at a temp dir.
    sr.FINDING_EVIDENCE = tmp / "workspace" / "run-logs" / "session-review-findings.jsonl"
    req_file = tmp / "requests" / REQ_NAME
    req_file.parent.mkdir(parents=True, exist_ok=True)
    req = {"id": SID, "harness": "claude", "_candidates": list(POPULATION)}
    assert str(sr.FINDING_EVIDENCE).startswith(str(tmp)), "evidence path escaped the sandbox"
    return req, req_file


def triage_row(**over):
    """A scripted-path finding row: the keys extract_scripted() builds, plus the scripted_items()
    keys a triage row carries (offered, near, commits)."""
    row = {"kind": "deferral", "quote": QUOTE, "context": "plan the shortlist",
           "where": "assistant, turn 419 of 602", "why_missed": "", "covered_by": "",
           "n": 41, "type": "DEFER", "turn": 419, "repeats": [],
           "near": ["t900 [queued] Shortlist the five by coding grade",
                    "t901 [queued] Family dedup for the model shortlist"],
           "commits": ["2d3145ba feat(local-inference): a graded paper with a real answer key"],
           "offered": ["t900", "t901", "2d3145ba"]}
    row.update(over)
    return row


def rows_of(path):
    """The JSONL rows written so far, parsed. A missing file is [] (no exception)."""
    p = Path(path)
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def t1_a_written_deferral_finding_gets_its_own_row():
    with tempfile.TemporaryDirectory() as d:
        req, req_file = sandbox(Path(d))
        new, covered = sr._keep(req_file, req, [triage_row()])
        check(new == 1 and covered == 0, "the deferral is filed as one new finding: %r" % ((new, covered),))
        rows = rows_of(sr.FINDING_EVIDENCE)
        check(len(rows) == 1, "exactly one evidence row for one finding: %d" % len(rows))
        if len(rows) != 1:
            return
        r = rows[0]
        stems = sorted(p.stem for p in Path(sr.FINDINGS).glob("*.json"))
        check(len(stems) == 1, "one finding file written: %r" % (stems,))
        check(r.get("key") == (stems[0] if stems else None),
              "the row names the finding it discharges: %r vs %r" % (r.get("key"), stems))
        check(r.get("quote") == QUOTE, "the row carries the quote's own text")
        check(r.get("quote_hash") == hashlib.sha256(QUOTE.encode("utf-8")).hexdigest(),
              "quote_hash is the quote's sha256: %r" % (r.get("quote_hash"),))
        check(r.get("session") == SID and r.get("harness") == "claude"
              and r.get("request") == REQ_NAME,
              "the row names the session, harness and request file: %r" %
              ((r.get("session"), r.get("harness"), r.get("request")),))
        check(r.get("turn") == 419, "the row carries the turn the quote came from: %r" % (r.get("turn"),))
        check(r.get("kind") == "deferral", "the row carries the finding's kind: %r" % (r.get("kind"),))
        check(r.get("disposition") == "deferred",
              "the row carries the disposition the record gets: %r" % (r.get("disposition"),))


def t2_the_row_records_the_search_not_the_population():
    with tempfile.TemporaryDirectory() as d:
        req, req_file = sandbox(Path(d))
        sr._keep(req_file, req, [triage_row()])
        rows = rows_of(sr.FINDING_EVIDENCE)
        check(len(rows) == 1, "one row written: %d" % len(rows))
        if not rows:
            return
        r = rows[0]
        s = r.get("searched")
        check(isinstance(s, dict), "searched is a dict of what the search covered: %r" % (s,))
        if not isinstance(s, dict):
            return
        check(s.get("tasks") == ["t900", "t901"],
              "searched.tasks is the offered task ids: %r" % (s.get("tasks"),))
        check(s.get("commits") == ["2d3145ba"],
              "searched.commits names the sha the pickaxe returned: %r" % (s.get("commits"),))
        check(s.get("tasks") != req["_candidates"],
              "it is the search, not the population stamp: %r vs %r" % (s.get("tasks"), req["_candidates"]))
        check(s.get("population") == len(POPULATION),
              "the population is recorded as the population: %r" % (s.get("population"),))
        check(s.get("semantic") == SENTINEL,
              "searched.semantic says it is not carried here rather than inventing a score: %r"
              % (s.get("semantic"),))
        check(r.get("search_terms") == SENTINEL,
              "search_terms says it is not carried here rather than inventing terms: %r"
              % (r.get("search_terms"),))
        check(r.get("matched") == [], "a filed finding matched nothing: %r" % (r.get("matched"),))
        check(r.get("verdict") == "no-cover-named",
              "the verdict names the path that produced the record: %r" % (r.get("verdict"),))
        for banned in ("model", "done_reason", "exit", "seconds"):
            check(banned not in r,
                  "no %s key on this endpoint's row: an empty one reads as evidence that was never recorded"
                  % banned)


def t3_a_covered_item_writes_no_evidence_row():
    with tempfile.TemporaryDirectory() as d:
        req, req_file = sandbox(Path(d))
        new, covered = sr._keep(req_file, req, [triage_row(covered_by="t900")],
                                note_task=lambda tid, text: None)
        check((new, covered) == (0, 1), "t900 is the cover, so nothing is filed: %r" % ((new, covered),))
        check(rows_of(sr.FINDING_EVIDENCE) == [], "a covered item is not a finding: no evidence row")


def t4_a_for_jas_row_writes_no_evidence_row():
    with tempfile.TemporaryDirectory() as d:
        req, req_file = sandbox(Path(d))
        new, covered = sr._keep(req_file, req, [triage_row(kind="for-jas")],
                                file_task=lambda title, body: "t9999")
        check(new == 1, "the for-jas question is filed: %r" % (new,))
        check(rows_of(sr.FINDING_EVIDENCE) == [], "a for-jas row is not a finding: no evidence row")


def t5_the_row_is_written_before_the_finding_file():
    """The task's requirement, as behaviour: the evidence row is appended BEFORE the finding file,
    so a finding whose file is skipped as an existing key still leaves its discharge evidence on disk."""
    with tempfile.TemporaryDirectory() as d:
        req, req_file = sandbox(Path(d))
        key = "abc123"
        Path(sr.FINDINGS).mkdir(parents=True, exist_ok=True)
        (Path(sr.FINDINGS) / (key + ".json")).mkdir()   # _keep sees an existing finding and skips
        fkey, sr._fkey = sr._fkey, (lambda *a: key)
        try:
            sr._keep(req_file, req, [triage_row()])
        finally:
            sr._fkey = fkey
        rows = rows_of(sr.FINDING_EVIDENCE)
        check(len(rows) == 1,
              "the evidence row exists even when the finding write is skipped: %d" % len(rows))
        check(bool(rows) and rows[0].get("key") == key,
              "the surviving row names the key: %r" % (rows and rows[0].get("key"),))


def t6_the_ledger_appends_one_json_line_per_finding():
    with tempfile.TemporaryDirectory() as d:
        req, req_file = sandbox(Path(d))
        second = triage_row(quote="yes, let's build the graded paper", turn=455, n=42)
        sr._keep(req_file, req, [triage_row(), second])
        rows = rows_of(sr.FINDING_EVIDENCE)
        check(len(rows) == 2, "two findings append two lines: %d" % len(rows))
        keys = [r.get("key") for r in rows]
        check(len(keys) == 2 and len(set(keys)) == 2, "one row per distinct finding, keyed: %r" % (keys,))
        check([r.get("turn") for r in rows] == [419, 455],
              "rows append in order, each carrying its own turn: %r" % ([r.get("turn") for r in rows],))


def main():
    for t in (t1_a_written_deferral_finding_gets_its_own_row,
              t2_the_row_records_the_search_not_the_population,
              t3_a_covered_item_writes_no_evidence_row,
              t4_a_for_jas_row_writes_no_evidence_row,
              t5_the_row_is_written_before_the_finding_file,
              t6_the_ledger_appends_one_json_line_per_finding):
        try:
            t()
        except Exception as exc:  # noqa: BLE001 -- a crash is a finding, not a traceback out of the suite
            FAILS.append("%s: %s: %s" % (t.__name__, type(exc).__name__, exc))
            print("  FAIL %s raised %s: %s" % (t.__name__, type(exc).__name__, exc))
    print("\n%d check(s) failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
