"""Oracle for t785 item (a): the session-close deferral ledger writer.

Task: workspace/tasks/t785--give-every-deferred-future-threshold-a-number-a.md:17
RCA:  workspace/rca/1f2b7bcf3223.md -- "That's a date to plan for, not a surprise to discover."

A deferred future threshold stated in a session's assistant prose must land in
workspace/session-reviews/deferral-ledger.jsonl as a row carrying a key, the quote, a
derived due date and a tracked_as, so the next session can say the plan is due. Nothing
writes that row today.

RED ON MAIN FOR ONE REASON: scripts/deferral_ledger.py does not exist on main, so the
import below fails -- that is the change this brief makes. No pytest (it is not installed),
no model call, no subprocess, no live state: every write this suite performs goes into a
temporary directory.
"""
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import deferral_ledger as dl

# The phrasings the task names, each quoted from an RCA this task was filed from.
QUOTES = [
    "That's a date to plan for, not a surprise to discover.",
    "a backup script that dies mid-copy on a flaky mount should probably retry rather than abort",
    "When you choose the real Senior model, change the tag there.",
    "Lowering the ceiling is optional, so I'll leave it for the handover and re-run session_end:",
    "Next session starts with batches of one, and reads that reply list first.",
    "Check that 4 of the 128 table pieces are byte-identical between Swift and the base model "
    "once the downloads finish.",
    "We will need to revisit the warm tier when the corpus crosses 1300 records.",
    "A second tier is worth keeping once the first is measured.",
]
TRIGGERS = [
    "plan for", "should probably", "when you choose", "leave it for the handover",
    "next session", "once the downloads finish", "crosses", "worth keeping",
]
ALL_PROSE = " ".join(QUOTES)


def test_module_exposes_the_named_contract():
    assert isinstance(dl.TEMPORAL_PATTERNS, list), "TEMPORAL_PATTERNS must be a list"
    assert len(dl.TEMPORAL_PATTERNS) >= 6, (
        "the task names six phrasings; the scan must carry at least that many patterns")
    for pat in dl.TEMPORAL_PATTERNS:
        assert isinstance(pat, str), "a pattern must be a string, got %r" % (pat,)
        re.compile(pat)
    assert tuple(dl.REQUIRED_FIELDS) == ("ts", "session", "key", "quote", "due", "tracked_as"), (
        "the row's fields are the six the task names, in order: %r" % (tuple(dl.REQUIRED_FIELDS),))
    assert dl.LEDGER == Path("workspace/session-reviews/deferral-ledger.jsonl"), (
        "the ledger must live where the task says: %r" % (dl.LEDGER,))


def test_every_named_phrasing_produces_a_row():
    rows = dl.find_deferred_plans(ALL_PROSE)
    assert len(rows) >= len(TRIGGERS), (
        "%d phrasings are present in the prose, the scan found %d rows" % (len(TRIGGERS), len(rows)))
    for trigger in TRIGGERS:
        matched = [r for r in rows if trigger.lower() in str(r.get("quote", "")).lower()]
        assert matched, "no deferral row was produced for the phrasing %r" % (trigger,)
        row = matched[0]
        assert str(row.get("key", "")).strip(), (
            "the row matching %r carries no key: %r" % (trigger, row))
        assert trigger.lower() in str(row["quote"]).lower(), (
            "the row for %r quotes the wrong text: %r" % (trigger, row["quote"]))


def test_quiet_prose_produces_no_rows():
    quiet = ("Fixed the timer, pushed the commit, and the suite is green. "
             "The plan file was rewritten to use months as units. "
             "Re-ran session_end: 38 suites pass.")
    rows = dl.find_deferred_plans(quiet)
    assert rows == [], (
        "prose that defers nothing produced %d row(s): %r -- a scan that fires on "
        "ordinary prose buries the deferrals it exists to find" % (len(rows), rows))


def test_keys_are_distinct_per_deferral():
    rows = dl.find_deferred_plans(ALL_PROSE)
    keys = [str(r["key"]) for r in rows]
    assert len(set(keys)) == len(keys), "two deferrals share a key: %r" % (keys,)
    for key in keys:
        assert key.strip(), "a key may not be blank: %r" % (keys,)
        assert len(key) <= 80, "key %r is longer than 80 characters" % (key,)
        assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*", key), (
            "a key must be a lowercase slug usable as a lookup key, got %r" % (key,))


def test_due_date_is_derived_from_the_phrasing():
    cases = [
        ("We will need to revisit this in ~3 months.", date(2026, 10, 8), date(2027, 1, 8)),
        ("Revisit this in 2 months.", date(2026, 10, 8), date(2026, 12, 8)),
        ("Revisit this in 1 month.", date(2027, 1, 31), date(2027, 2, 28)),
        ("Revisit this in 6 months.", date(2026, 10, 8), date(2027, 4, 8)),
        ("Next session starts with batches of one.", date(2026, 10, 8), date(2026, 10, 9)),
        ("A backup script should probably retry rather than abort.",
         date(2026, 10, 8), date(2027, 1, 6)),
    ]
    for quote, start, want in cases:
        got = dl.derive_due(quote, start)
        assert got == want, "derive_due(%r, %s) returned %r, expected %r" % (quote, start, got, want)


def test_the_default_horizon_is_declared():
    assert dl.DEFAULT_HORIZON_DAYS == 90, (
        "a deferral with no stated horizon gets the declared one, not a magic number: %r"
        % (dl.DEFAULT_HORIZON_DAYS,))


def test_row_carries_the_six_fields_with_real_values():
    row = dl.row(ts="2026-10-08T02:00:00Z", session="6cc34e3b", key="date-to-plan-for",
                 quote="That's a date to plan for, not a surprise to discover.",
                 due=date(2027, 1, 8), tracked_as="t785")
    for field in dl.REQUIRED_FIELDS:
        assert field in row, "the row is missing the required field %r: %r" % (field, sorted(row))
    assert row["ts"] == "2026-10-08T02:00:00Z", "ts was not carried through: %r" % (row["ts"],)
    assert row["session"] == "6cc34e3b", "session was not carried through: %r" % (row["session"],)
    assert row["key"] == "date-to-plan-for", "key was not carried through: %r" % (row["key"],)
    assert row["tracked_as"] == "t785", "tracked_as was not carried through: %r" % (row["tracked_as"],)
    assert row["due"] == "2027-01-08", (
        "a date must serialise to ISO YYYY-MM-DD, not a date object: %r" % (row["due"],))
    assert row["quote"] == "That's a date to plan for, not a surprise to discover.", (
        "the quote is the record of what was deferred; it must be stored whole: %r" % (row["quote"],))
    json.dumps(row)


def test_row_refuses_a_blank_tracked_as():
    for blank in ("", "   ", None):
        raised = None
        try:
            dl.row(ts="2026-10-08T02:00:00Z", session="s", key="k",
                   quote="That's a date to plan for.", due=date(2027, 1, 8), tracked_as=blank)
        except ValueError as exc:
            raised = str(exc)
        assert raised is not None, "a row with tracked_as=%r was accepted" % (blank,)
        assert "tracked_as" in raised, "the refusal must name the field: %r" % (raised,)


def test_row_refuses_a_non_string_tracked_as():
    raised = None
    try:
        dl.row(ts="2026-10-08T02:00:00Z", session="s", key="k",
               quote="That's a date to plan for.", due=date(2027, 1, 8), tracked_as=785)
    except ValueError as exc:
        raised = str(exc)
    assert raised is not None, "tracked_as=785 was accepted as a tracker reference"
    assert "tracked_as" in raised, "the refusal must name the field: %r" % (raised,)


def test_row_refuses_a_blank_quote():
    raised = None
    try:
        dl.row(ts="2026-10-08T02:00:00Z", session="s", key="k", quote="   ",
               due=date(2027, 1, 8), tracked_as="t785")
    except ValueError as exc:
        raised = str(exc)
    assert raised is not None, "a row with a blank quote was accepted"
    assert "quote" in raised, "the refusal must name the field: %r" % (raised,)


def test_append_ledger_appends_and_round_trips():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "deferral-ledger.jsonl"
        first = dl.row(ts="2026-10-08T02:00:00Z", session="6cc34e3b", key="date-to-plan-for",
                       quote="That's a date to plan for, not a surprise to discover.",
                       due=date(2027, 1, 8), tracked_as="t785")
        second = dl.row(ts="2026-10-08T03:00:00Z", session="6cc34e3b", key="retry-the-backup",
                        quote="a backup script should probably retry rather than abort",
                        due=date(2027, 1, 6), tracked_as="t785")
        dl.append_ledger(path, [first])
        dl.append_ledger(path, [second])
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 2, "two calls appended %d line(s): %r" % (len(lines), lines)
        assert json.loads(lines[0]) == first, "the first row did not survive the write"
        assert json.loads(lines[1]) == second, "the second row overwrote the first"
        assert json.loads(lines[0])["due"] == "2027-01-08", "due must round-trip as ISO text"
        assert json.loads(lines[1])["key"] == "retry-the-backup", "key must round-trip unchanged"


def test_append_ledger_creates_a_missing_parent_dir():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "deferral-ledger.jsonl"
        dl.append_ledger(path, [dl.row(ts="2026-10-08T02:00:00Z", session="s", key="k",
                                       quote="a date to plan for", due=date(2027, 1, 8),
                                       tracked_as="t785")])
        assert path.is_file(), "the ledger was not written at %r" % (path,)
        assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["key"] == "k"


def test_unnumbered_reports_the_quote_of_a_row_without_a_tracked_as():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "deferral-ledger.jsonl"
        numbered = dl.row(ts="2026-10-08T02:00:00Z", session="s", key="numbered",
                          quote="a date to plan for", due=date(2027, 1, 8), tracked_as="t785")
        unnumbered = dl.row(ts="2026-10-08T02:00:00Z", session="s", key="unnumbered",
                            quote="worth keeping once the tier is measured",
                            due=date(2027, 1, 6), tracked_as="t785")
        # t728 repair (2026-10-11 Manager scope, applied 2026-10-12): test_row_refuses_a_blank_tracked_as
        # asserts row(tracked_as="") RAISES, and this call asked the same row() to RETURN a dict --
        # no implementation satisfies both. The unnumbered state a reader must catch is a row ON
        # DISK whose tracker was blanked, so build it numbered, then blank the field.
        unnumbered["tracked_as"] = ""
        dl.append_ledger(path, [numbered])
        dl.append_ledger(path, [unnumbered])
        found = dl.unnumbered(path)
        assert len(found) == 1, "expected exactly one untracked row, got %r" % (found,)
        assert found[0]["key"] == "unnumbered", "the report names the wrong row: %r" % (found[0],)
        assert "worth keeping" in found[0]["quote"], (
            "the report must carry the quote so a reader can find the deferral: %r" % (found[0],))


def test_unnumbered_on_a_missing_file_is_empty_not_a_crash():
    with tempfile.TemporaryDirectory() as tmp:
        assert dl.unnumbered(Path(tmp) / "deferral-ledger.jsonl") == [], (
            "a first run with no ledger must report nothing, not raise")


def test_a_torn_line_is_reported_naming_the_line_number():
    """never-truncate-a-record: a row cut mid-write must not read as absent.
    Name the file and the line; never skip silently."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "deferral-ledger.jsonl"
        dl.append_ledger(path, [dl.row(ts="2026-10-08T02:00:00Z", session="s", key="k",
                                       quote="a date to plan for", due=date(2027, 1, 8),
                                       tracked_as="t785")])
        path.write_text(path.read_text(encoding="utf-8") + '{"key": "torn-ro', encoding="utf-8")
        raised = None
        try:
            dl.unnumbered(path)
        except ValueError as exc:
            raised = str(exc)
        assert raised is not None, (
            "a torn final line was skipped -- the deferral row vanished quietly")
        assert "2" in raised, "the report must name the line number: %r" % (raised,)
        assert path.name in raised, "the report must name the file: %r" % (raised,)


def _run():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except AssertionError as exc:
            failed.append(name)
            print("FAIL %s: %s" % (name, exc))
        except Exception as exc:
            failed.append(name)
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
    print("%d/%d passed" % (len(tests) - len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
