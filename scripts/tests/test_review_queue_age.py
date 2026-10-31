#!/usr/bin/env python3
"""t1020 -- the review-queue age ledger over workspace/session-reviews/ledger.jsonl.

RED ON MAIN FOR ONE REASON: the four functions this file calls do not exist in
scripts/session_end.py, so every test below fails with AttributeError
("module 'session_end' has no attribute 'record_queue_age'"). The import itself succeeds on
main: session_end.py is a module like any other script in scripts/ (it runs its work under
`if __name__ == "__main__":`), so this suite is red for the missing change and for nothing
else.

WHY THE JOIN GOES THROUGH `request`, NOT `key`. A census of the 1158 rows in
workspace/session-reviews/ledger.jsonl (all 1158 decode as JSON):

  * 76 rows have event="queued" and the field set (at, event, from_turn, request, to_turn).
    Not one of them carries a `key`.
  * 481 rows have event="finding" and the field set (at, event, key, kind, quote, request).
  * Every finding row carries a `request` value that some queued row also carries, so
    queued -> finding is joinable through `request` and through nothing else.

A queued row therefore cannot be matched to a finding by key; it is matched by the request
file both rows name. Inventing a `key` on the queued side is not available.

`covered_by`: the ledger has NO per-finding cover id. The 170 rows with event="covered"
carry (at, event, quote, request, task) and no `key`; the 41 rows carrying a field literally
named `covered` are event="reviewed" rows where `covered` and `findings` are INTEGER COUNTS
(verbatim: {"at": "2026-09-28T19:19:14Z", "event": "reviewed", "request":
"claude-7735c494-...json", "findings": 9, "covered": 0}). Joining those quotes to the 481
finding quotes matched 0 of 170. So this module reads a cover id from a row's own
`covered_by` field when one is present and writes "" when it is not -- it does NOT transpose
an integer count into a field that must hold a task id. On today's ledger the column is ""
for every finding: that is the gap, recorded rather than papered over.

`disposition` IS joinable: 41 rows carry it (all event="rca") and all 41 carry a `key` that
is one of the finding keys. Values seen: CLOSED, JAS, TASK, FIXED.

`age_hours` is measured queued->found, which is all the ledger can measure. The RCA's "22
days" (workspace/rca/2604faf648ae.md:96) is decision->finding, and no ledger row records a
decision instant, so the ledger structurally cannot produce that number.

Every expectation below is COMPUTED from the file at run time (count the finding rows, do the
datetime arithmetic). Nothing here stores today's counts, so the suite does not go red when
the ledger legitimately grows.
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import session_end

REPO = Path(__file__).resolve().parents[2]
REAL_LEDGER = REPO / "workspace" / "session-reviews" / "ledger.jsonl"

# kind is in the row set because the staleness rule is defined on it, and the ledger's
# finding rows carry it (deferral / unresolved / off-flow).
REQUIRED_KEYS = {"key", "queued", "found", "age_hours", "disposition", "covered_by", "kind"}


def _iso(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _q(request, at, **extra):
    row = {"at": at, "event": "queued", "request": request, "from_turn": 0, "to_turn": 10}
    row.update(extra)
    return row


def _f(key, request, at, kind="deferral", **extra):
    row = {"at": at, "event": "finding", "key": key, "kind": kind,
           "quote": "I'll update it to the new decision", "request": request}
    row.update(extra)
    return row


def _ages(rows):
    return session_end.join_queue_ages(rows)


def _by_key(rows, key):
    hits = [r for r in rows if r["key"] == key]
    assert len(hits) == 1, "expected exactly one age row for " + repr(key) + ", got " + str(len(hits)) + ": " + str(hits)
    return hits[0]


def test_the_rca_case_age_is_computed_from_the_timestamps():
    """The flag that minted this task, as arithmetic: 2026-09-28T18:02:41Z queued ->
    2026-10-08T13:04:22Z found. The expected value is DERIVED by datetime arithmetic in this
    test, never copied from a constant the implementation could hard-code."""
    queued, found = "2026-09-28T18:02:41Z", "2026-10-08T13:04:22Z"
    expected = (_iso(found) - _iso(queued)).total_seconds() / 3600.0
    r = _by_key(_ages([_q("req-a.json", queued), _f("2604faf648ae", "req-a.json", found)]), "2604faf648ae")
    assert r["queued"] == queued, "queued not echoed from the row: " + str(r)
    assert r["found"] == found, "found not echoed from the row: " + str(r)
    assert isinstance(r["age_hours"], float), "age_hours must be a float, got " + type(r["age_hours"]).__name__
    assert abs(r["age_hours"] - expected) < 0.02, "age_hours " + str(r["age_hours"]) + " != computed " + str(expected)
    assert abs(r["age_hours"] - 235.03) < 0.02, "those two stamps are 235.03h apart, got " + str(r["age_hours"])
    assert r["age_hours"] == round(expected, 2), "age_hours must be rounded to 2dp, got " + str(r["age_hours"])
    assert r["kind"] == "deferral", "kind must come off the finding row itself: " + str(r)


def test_the_age_is_rounded_to_two_decimal_places():
    """A placeholder that stores raw seconds, or an int, fails here: 3630s is 1.0083h -> 1.01."""
    rows = [_q("r1", "2026-10-01T00:00:00Z"), _f("k1", "r1", "2026-10-01T01:00:30Z")]
    got = _by_key(_ages(rows), "k1")["age_hours"]
    assert abs(got - 1.01) < 1e-9, "3630s is 1.00833h, rounded to 2dp = 1.01, got " + str(got)


def test_distinct_findings_get_distinct_ages():
    """Two findings on the same request must not share one age. A stub that fills every row
    with the same number passes a single-finding suite and fails here (oracle-weak shape 2)."""
    rows = [_q("req-a.json", "2026-10-01T00:00:00Z"),
            _f("key-a", "req-a.json", "2026-10-01T06:00:00Z"),
            _q("req-b.json", "2026-10-01T00:00:00Z"),
            _f("key-b", "req-b.json", "2026-10-03T06:00:00Z")]
    out = _ages(rows)
    assert sorted(r["key"] for r in out) == ["key-a", "key-b"], "both findings must appear: " + str(out)
    a = _by_key(out, "key-a")["age_hours"]
    b = _by_key(out, "key-b")["age_hours"]
    assert abs(a - 6.0) < 0.02, "key-a expected 6.0h, got " + str(a)
    assert abs(b - 54.0) < 0.02, "key-b expected 54.0h, got " + str(b)


def test_earliest_queued_row_is_the_one_used():
    """A request re-queued later must not shorten its age: 30h, not 6h."""
    rows = [_q("req-a.json", "2026-10-01T00:00:00Z"),
            _q("req-a.json", "2026-10-02T00:00:00Z"),
            _f("key-a", "req-a.json", "2026-10-02T06:00:00Z")]
    r = _by_key(_ages(rows), "key-a")
    assert r["queued"] == "2026-10-01T00:00:00Z", "must use the EARLIEST queued row: " + str(r)
    assert abs(r["age_hours"] - 30.0) < 0.02, "expected 30.0h from the earliest queue, got " + str(r["age_hours"])


def test_finding_with_no_queued_row_is_reported_not_dropped():
    """A finding whose request was never queued is a visible gap, not a silent loss --
    silent loss is exactly how the 22-day-late flag stayed invisible."""
    r = _by_key(_ages([_f("key-ghost", "req-missing.json", "2026-10-08T13:04:22Z")]), "key-ghost")
    assert r["queued"] == "", "an unjoined row must show an empty queued, not a guess: " + str(r)
    assert r["age_hours"] is None, "an unjoined row must have NO age, not a zero: " + str(r)
    assert r["found"] == "2026-10-08T13:04:22Z", "found must survive the join: " + str(r)


def test_disposition_and_cover_are_carried_onto_the_age_row():
    """Both are read off the ledger's own fields and land on the finding's row; a finding
    with neither reads as empty, not as inherited from a sibling."""
    rows = [_q("req-a.json", "2026-10-01T00:00:00Z"),
            _f("key-a", "req-a.json", "2026-10-02T00:00:00Z"),
            {"at": "2026-10-02T09:00:00Z", "event": "rca", "key": "key-a",
             "disposition": "TASK", "report": "workspace/rca/key-a.md", "tasks": ["t1020"]},
            {"at": "2026-10-02T10:00:00Z", "event": "covered", "key": "key-a",
             "covered_by": "t778", "request": "req-a.json"},
            _f("key-b", "req-b.json", "2026-10-02T00:00:00Z")]
    out = _ages(rows)
    a = _by_key(out, "key-a")
    assert a["disposition"] == "TASK", "disposition not carried onto the age row: " + str(a)
    assert a["covered_by"] == "t778", "covered_by not carried onto the age row: " + str(a)
    b = _by_key(out, "key-b")
    assert b["disposition"] == "", "an undispositioned finding must read as empty, not inherited: " + str(b)
    assert b["covered_by"] == "", "an uncovered finding must read as empty, not inherited: " + str(b)


def test_the_latest_disposition_wins():
    """A finding dispositioned, then re-dispositioned, reports the LAST one chronologically."""
    rows = [_q("req-a.json", "2026-10-01T00:00:00Z"),
            _f("key-a", "req-a.json", "2026-10-02T00:00:00Z"),
            {"at": "2026-10-03T00:00:00Z", "event": "rca", "key": "key-a",
             "disposition": "TASK", "report": "r1.md", "tasks": []},
            {"at": "2026-10-05T00:00:00Z", "event": "rca", "key": "key-a",
             "disposition": "CLOSED", "report": "r2.md", "tasks": []}]
    out = _ages(rows)
    assert _by_key(out, "key-a")["disposition"] == "CLOSED", "the latest disposition must win: " + str(out)


def test_stale_deferrals_are_exactly_over_bound_and_undispositioned():
    """THE DECAY FIXTURE. Each of the four findings differs from the others on exactly one
    criterion, so an implementation that filters on any single criterion -- or on none --
    fails here."""
    rows = [
        _q("r1", "2026-10-01T00:00:00Z"), _f("old-undisposed", "r1", "2026-10-10T00:00:00Z", kind="deferral"),
        _q("r2", "2026-10-01T00:00:00Z"), _f("old-disposed", "r2", "2026-10-10T00:00:00Z", kind="deferral"),
        _q("r3", "2026-10-01T00:00:00Z"), _f("old-other-kind", "r3", "2026-10-10T00:00:00Z", kind="off-flow"),
        _q("r4", "2026-10-01T00:00:00Z"), _f("young-undisposed", "r4", "2026-10-01T02:00:00Z", kind="deferral"),
        {"at": "2026-10-02T00:00:00Z", "event": "rca", "key": "old-disposed",
         "disposition": "TASK", "report": "r.md", "tasks": []},
    ]
    got = sorted(r["key"] for r in session_end.stale_review_deferrals(_ages(rows), max_age_hours=24.0))
    assert got == ["old-undisposed"], (
        "stale_review_deferrals must return exactly the >24h deferrals with no disposition, got " + str(got))


def test_the_bound_is_strict():
    """Exactly 24.0h is NOT stale: age_hours > max_age_hours, not >=. A >= makes a row that
    is exactly one day old a finding, and the rule is 'ages past 24h'."""
    rows = [_q("r1", "2026-10-01T00:00:00Z"), _f("at-bound", "r1", "2026-10-02T00:00:00Z", kind="deferral")]
    ages = _ages(rows)
    assert abs(_by_key(ages, "at-bound")["age_hours"] - 24.0) < 0.02, "fixture must sit on the bound"
    assert session_end.stale_review_deferrals(ages, max_age_hours=24.0) == [], (
        "a finding exactly at the bound is not over it: " + str(session_end.stale_review_deferrals(ages)))


def test_unjoined_deferral_is_not_reported_as_stale():
    """age_hours is None for a finding with no queued row -- it has no measurable age, so a
    stub that treats None as 0 (or as truthy) is caught here."""
    ages = _ages([_f("key-ghost", "req-missing.json", "2026-10-08T13:04:22Z", kind="deferral")])
    assert session_end.stale_review_deferrals(ages, 24.0) == [], (
        "an unmeasurable age must not be reported as stale: " + str(session_end.stale_review_deferrals(ages)))


def test_write_appends_one_row_per_finding_and_never_touches_the_live_ledger():
    """The age ledger is append-only, one row per finding, and the real ledger is not a
    fixture. The last assertion is the oracle-dirty guard: the writer takes the output path
    as an argument and must never open workspace/session-reviews/ledger.jsonl."""
    rows = [_q("r1", "2026-10-01T00:00:00Z"), _f("k1", "r1", "2026-10-10T00:00:00Z"),
            _q("r2", "2026-10-01T00:00:00Z"), _f("k2", "r2", "2026-10-02T00:00:00Z")]
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "queue-age.jsonl"
        out.write_text('{"existing": "row"}\n', encoding="utf-8")
        before = (sum(1 for _ in REAL_LEDGER.open(encoding="utf-8", errors="replace"))
                  if REAL_LEDGER.exists() else None)
        n = session_end.record_queue_age(_ages(rows), out)
        lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert n == 2, "record_queue_age must return the number of rows written, got " + str(n)
        assert lines[0] == {"existing": "row"}, "the writer must APPEND, not rewrite: " + str(lines[0])
        assert len(lines) == 3, "expected 2 appended rows (one per finding), got " + str(len(lines) - 1) + ": " + str(lines)
        for r in lines[1:]:
            assert set(r) == REQUIRED_KEYS, (
                "an age row must carry exactly " + str(sorted(REQUIRED_KEYS)) + ", got " + str(sorted(r)))
            assert isinstance(r["age_hours"], (float, type(None))), "age_hours must be float or None: " + str(r)
            assert isinstance(r["covered_by"], str), "covered_by must be a string, never a count: " + str(r)
        if before is not None:
            after = sum(1 for _ in REAL_LEDGER.open(encoding="utf-8", errors="replace"))
            assert after == before, (
                "the live ledger changed (" + str(before) + " -> " + str(after) + "): the writer touched live state")


def test_the_real_ledger_joins_one_row_per_finding_and_stays_readable():
    """Real artefact, not a synthetic one. Every expectation is COMPUTED from the file, so
    this survives the ledger growing. It also fixes the join's vocabulary: if a queued row
    gains a `key`, `finding` is renamed, or `covered` turns into an id, the suite goes red on
    real data -- the decay test for this fixture."""
    if not REAL_LEDGER.exists():
        print("SKIP: " + str(REAL_LEDGER) + " is not in this checkout")
        return
    rows = session_end.load_review_rows(REAL_LEDGER)
    n_findings = sum(1 for r in rows if r.get("event") == "finding")
    assert n_findings > 0, "the ledger is the fixture; it must contain finding rows"
    ages = session_end.join_queue_ages(rows)
    assert len(ages) == n_findings, (
        "one age row per finding expected (" + str(n_findings) + "), got " + str(len(ages))
        + " -- collapsing by request is a bug")
    for r in ages:
        assert set(r) == REQUIRED_KEYS, (
            "an age row must carry exactly " + str(sorted(REQUIRED_KEYS)) + ", got " + str(sorted(r)))
        assert r["key"], "age row without a key: " + str(r)
        assert isinstance(r["covered_by"], str), "covered_by must be a string, never a count: " + str(r)
        if r["age_hours"] is not None:
            assert r["age_hours"] >= 0, "finding " + str(r["key"]) + " found before it was queued: " + str(r["age_hours"])
    assert session_end.stale_review_deferrals(ages), (
        "on today's ledger most findings are far over 24h; an empty staleness result means the rule is dead")


def test_a_torn_final_line_is_recorded_not_swallowed():
    """The one scenario this module was built for: a writer cut off mid-line. The reader
    must keep the torn record, WHOLE, as visible evidence -- never-truncate-a-record. A
    loader that skips the line, slices it, or raises, fails here."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ledger.jsonl"
        good = '{"at": "2026-10-01T00:00:00Z", "event": "finding", "key": "k1", "kind": "deferral", "quote": "q", "request": "r1.json"}'
        torn = '{"at": "2026-10-02T00:00:00Z", "event": "finding", "key": "k2", "kin'
        p.write_text(good + "\n" + torn + "\n", encoding="utf-8")
        rows = session_end.load_review_rows(p)
        assert len(rows) == 2, "a torn record must be kept, not dropped: " + str(len(rows)) + " rows: " + str(rows)
        bad = [r for r in rows if r.get("event") == "unparseable"]
        assert len(bad) == 1, "exactly one unparseable row expected: " + str(rows)
        assert bad[0]["detail"] == torn, "the raw line must be kept WHOLE, not sliced: " + repr(bad[0].get("detail"))


if __name__ == "__main__":
    fails = []
    for name in sorted(globals()):
        fn = globals()[name]
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS " + name)
            except Exception as exc:
                fails.append((name, exc))
                print("FAIL " + name + ": " + type(exc).__name__ + ": " + str(exc))
    print("")
    print(str(len(fails)) + " failed")
    sys.exit(1 if fails else 0)
