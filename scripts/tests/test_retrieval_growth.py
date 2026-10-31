"""Oracle for t785 item (c): the retrieval growth writer.

Task: workspace/tasks/t785--give-every-deferred-future-threshold-a-number-a.md:17
RCA:  workspace/rca/1f2b7bcf3223.md -- a plan date stated in prose with no number under it.

Nothing on the box measures the warm-tier population, so a projected threshold date can be
neither recorded nor re-derived. This suite pins the module that does it: the rate is a
measured number, the date is COMPUTED from it, and a row carrying a placeholder is refused.
Refusing the placeholder is the point of the task, so the suite pins the refusals too.

RED ON MAIN FOR ONE REASON: scripts/retrieval_growth.py does not exist on main, so the
import below fails -- that is the change this brief makes. No pytest (it is not installed),
no model call, no subprocess, no live state: every write goes into a temporary directory.
"""
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import retrieval_growth as rg


def test_module_exposes_the_named_contract():
    assert rg.TIER == "warm", "the tier the task names is 'warm', not %r" % (rg.TIER,)
    assert tuple(rg.REQUIRED_FIELDS) == (
        "ts", "tier", "count", "records_per_day", "projected_threshold", "method"), (
        "the row's fields are the six the task names, in order: %r" % (tuple(rg.REQUIRED_FIELDS),))
    assert rg.DEFAULT_LOG == Path("workspace/run-logs/retrieval-growth.jsonl"), (
        "the growth row must land where the task pointed it: %r" % (rg.DEFAULT_LOG,))


def test_project_threshold_date_computes_whole_days_from_the_rate():
    cases = [
        (1200, 7.9, 1300, date(2026, 10, 8), date(2026, 10, 21)),
        (40, 2.0, 100, date(2026, 1, 1), date(2026, 1, 31)),
        (0, 2.5, 10, date(2026, 10, 8), date(2026, 10, 12)),
        (1300, 7.9, 1300, date(2026, 10, 8), date(2026, 10, 8)),
        (1400, 7.9, 1300, date(2026, 10, 8), date(2026, 10, 8)),
    ]
    for count, rate, threshold, start, want in cases:
        got = rg.project_threshold_date(count=count, records_per_day=rate,
                                        threshold=threshold, start=start)
        assert got == want, (
            "project_threshold_date(count=%s, rate=%s, threshold=%s, start=%s) returned %r, "
            "expected %r" % (count, rate, threshold, start, got, want))


def test_a_zero_rate_is_refused_naming_the_field():
    raised = None
    try:
        rg.project_threshold_date(count=1200, records_per_day=0,
                                  threshold=1300, start=date(2026, 10, 8))
    except ValueError as exc:
        raised = str(exc)
    assert raised is not None, (
        "a rate of 0 produced a date -- an unmeasured population would project a plan date")
    assert "records_per_day" in raised, "the refusal must name the field: %r" % (raised,)


def test_a_negative_rate_is_refused_naming_the_field():
    raised = None
    try:
        rg.project_threshold_date(count=1200, records_per_day=-7.9,
                                  threshold=1300, start=date(2026, 10, 8))
    except ValueError as exc:
        raised = str(exc)
    assert raised is not None, "a negative rate produced a date: a shrinking count is not growth"
    assert "records_per_day" in raised, "the refusal must name the field: %r" % (raised,)


def test_a_zero_threshold_is_refused_naming_the_field():
    raised = None
    try:
        rg.project_threshold_date(count=1200, records_per_day=7.9,
                                  threshold=0, start=date(2026, 10, 8))
    except ValueError as exc:
        raised = str(exc)
    assert raised is not None, "a threshold of 0 produced a date -- an unnumbered threshold is the defect"
    assert "threshold" in raised, "the refusal must name the field: %r" % (raised,)


def test_growth_row_carries_the_six_fields_with_real_values():
    row = rg.growth_row(ts="2026-10-08T02:00:00Z", count=1200, records_per_day=7.9,
                        threshold=1300, start=date(2026, 10, 8),
                        method="warm-tier census, 7-day mean")
    for field in rg.REQUIRED_FIELDS:
        assert field in row, "the row is missing the required field %r: %r" % (field, sorted(row))
    assert row["ts"] == "2026-10-08T02:00:00Z", "ts was not carried through: %r" % (row["ts"],)
    assert row["tier"] == "warm", "the tier must be the module's TIER: %r" % (row["tier"],)
    assert row["count"] == 1200, "count was not carried through: %r" % (row["count"],)
    assert row["records_per_day"] == 7.9, "records_per_day was not carried through: %r" % (row["records_per_day"],)
    assert row["method"] == "warm-tier census, 7-day mean", (
        "method names where the number came from; it must be stored whole: %r" % (row["method"],))
    assert row["projected_threshold"] == "2026-10-21", (
        "the date must be computed from (1200, 7.9, 1300, 2026-10-08) and serialised as ISO: %r"
        % (row["projected_threshold"],))
    json.dumps(row)


def test_growth_row_records_the_threshold_it_projected_against():
    row = rg.growth_row(ts="2026-10-08T02:00:00Z", count=1200, records_per_day=7.9,
                        threshold=1300, start=date(2026, 10, 8),
                        method="warm-tier census, 7-day mean")
    assert row.get("threshold") == 1300, (
        "the row must carry the threshold number it projected against, so a later reader "
        "can re-derive the date instead of trusting it: %r" % (row.get("threshold"),))


def test_growth_row_refuses_a_placeholder_count():
    for bad in (None, 0, "1200", "   "):
        raised = None
        try:
            rg.growth_row(ts="2026-10-08T02:00:00Z", count=bad, records_per_day=7.9,
                          threshold=1300, start=date(2026, 10, 8),
                          method="warm-tier census, 7-day mean")
        except ValueError as exc:
            raised = str(exc)
        assert raised is not None, "a count of %r was accepted as a measurement" % (bad,)
        assert "count" in raised, "the refusal must name the field: %r" % (raised,)


def test_growth_row_refuses_a_blank_method():
    raised = None
    try:
        rg.growth_row(ts="2026-10-08T02:00:00Z", count=1200, records_per_day=7.9,
                      threshold=1300, start=date(2026, 10, 8), method="   ")
    except ValueError as exc:
        raised = str(exc)
    assert raised is not None, "a row with a blank method was accepted"
    assert "method" in raised, "the refusal must name the field: %r" % (raised,)


def test_append_growth_writes_one_json_line_and_reads_back():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "retrieval-growth.jsonl"
        first = rg.growth_row(ts="2026-10-08T02:00:00Z", count=1200, records_per_day=7.9,
                              threshold=1300, start=date(2026, 10, 8),
                              method="warm-tier census, 7-day mean")
        second = rg.growth_row(ts="2026-10-09T02:00:00Z", count=1208, records_per_day=7.9,
                               threshold=1300, start=date(2026, 10, 9),
                               method="warm-tier census, 7-day mean")
        rg.append_growth(path, first)
        rg.append_growth(path, second)
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 2, "two appends produced %d line(s): %r" % (len(lines), lines)
        rows = rg.read_growth(path)
        assert len(rows) == 2, "read_growth returned %d row(s) from a 2-line ledger" % (len(rows),)
        assert rows[0] == first, "the first row did not survive the write"
        assert rows[1] == second, "the second row overwrote the first"
        assert rows[0]["projected_threshold"] == "2026-10-21", (
            "the computed date must round-trip: %r" % (rows[0]["projected_threshold"],))
        assert rows[1]["count"] == 1208, "the count must round-trip: %r" % (rows[1]["count"],)
        assert rows[1]["threshold"] == 1300, "the threshold must round-trip: %r" % (rows[1]["threshold"],)


def test_append_growth_refuses_an_incomplete_row_and_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "retrieval-growth.jsonl"
        row = rg.growth_row(ts="2026-10-08T02:00:00Z", count=1200, records_per_day=7.9,
                            threshold=1300, start=date(2026, 10, 8),
                            method="warm-tier census, 7-day mean")
        del row["method"]
        raised = None
        try:
            rg.append_growth(path, row)
        except ValueError as exc:
            raised = str(exc)
        assert raised is not None, "an incomplete row was appended to the growth log"
        assert "method" in raised, "the refusal must name the field: %r" % (raised,)
        assert (not path.exists()) or path.read_text(encoding="utf-8") == "", (
            "a refused row must leave no trace in the ledger: %r" % (str(path),))


def test_read_growth_on_a_missing_file_is_empty_not_a_crash():
    with tempfile.TemporaryDirectory() as tmp:
        assert rg.read_growth(Path(tmp) / "retrieval-growth.jsonl") == []


def test_a_torn_line_is_reported_naming_the_line_number():
    """never-truncate-a-record: a row cut mid-write must not read as absent.
    Name the file and the line; never skip silently."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "retrieval-growth.jsonl"
        rg.append_growth(path, rg.growth_row(ts="2026-10-08T02:00:00Z", count=1200,
                                             records_per_day=7.9, threshold=1300,
                                             start=date(2026, 10, 8),
                                             method="warm-tier census, 7-day mean"))
        text = path.read_text(encoding="utf-8")
        path.write_text(text + '{"count": 120', encoding="utf-8")
        raised = None
        try:
            rg.read_growth(path)
        except ValueError as exc:
            raised = str(exc)
        assert raised is not None, (
            "a torn final line was skipped -- the growth row vanished quietly")
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
