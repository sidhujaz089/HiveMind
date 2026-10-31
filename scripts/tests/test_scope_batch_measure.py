#!/usr/bin/env python3
"""t1190: measure scoping-batch quality so scope_batch.BATCH_MAX can be raised on evidence.

The fixture is a progress log THIS suite writes into tempfile.mkdtemp(), built from lines
copied verbatim out of the manager queue's real progress log (START BATCH / END BATCH /
SPLIT BATCH -- the format scoping_queue.sh appends). Real format, owned data: a new line in
the live log cannot turn this suite red on main. Stamps carry no date, so a batch that
straddles midnight has no measurable duration: report it as crossed_midnight, never clamp it
to 0.0 (a 0.0 row is a plausible record with the answer removed).
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import scope_batch as sb  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    PASSED += (got == want)
    FAILED += (got != want)
    print(f"  {'PASS' if got == want else 'FAIL'}  {name}"
          + ("" if got == want else f"  (got {got!r}, want {want!r})"))
    assert got == want, f"{name}: got {got!r}, want {want!r}"


# Lines copied verbatim from the live progress log: two 3-task batches, one 2-task batch,
# one single scope. One of the 3-task batches came back with only two sections (t800 missing).
REAL = (
    "13:10:51Z START BATCH t1173 t1168 t1166 station 1\n"
    "13:20:06Z END BATCH t1173 t1168 t1166 rc=0 bytes=15797\n"
    "13:20:06Z SPLIT BATCH t1173 t1168 t1166 -> sections for: t1173 t1168 t1166\n"
    "13:20:39Z START BATCH t741 t1067 station 1\n"
    "13:36:34Z END BATCH t741 t1067 rc=0 bytes=40854\n"
    "13:36:34Z SPLIT BATCH t741 t1067 -> sections for: t741 t1067\n"
    "16:55:37Z START BATCH t1090 t1058 t800 station 1\n"
    "17:06:56Z END BATCH t1090 t1058 t800 rc=0 bytes=45846\n"
    "17:06:56Z SPLIT BATCH t1090 t1058 t800 -> sections for: t1090 t1058\n"
    "13:10:51Z START t123 (task t123) station 1 role manager\n"
    "13:14:00Z END t123 rc=0 bytes=8000\n"
)


def test_parses_a_real_batch_with_its_sections():
    recs, singles = sb.parse_progress(REAL)
    check("three batch records, one single", (len(recs), len(singles)), (3, 1))
    by = {tuple(r["ids"]): r for r in recs}
    r = by[("t1173", "t1168", "t1166")]
    check("tasks claimed on START BATCH", r["n"], 3)
    check("answer bytes from END BATCH", r["bytes"], 15797)
    check("13:10:51Z to 13:20:06Z = 9m15s", r["minutes"], 9.25)
    check("sections present on SPLIT BATCH", r["sections"], ["t1173", "t1168", "t1166"])
    check("nothing dropped here", r["missing"], [])
    check("rc kept", r["rc"], 0)


def test_a_section_the_answer_never_wrote_is_counted_as_dropped():
    recs = [r for r in sb.parse_progress(REAL)[0] if "t800" in r["ids"]]
    check("the batch that lost a task", len(recs), 1)
    check("the task left with no section", recs[0]["missing"], ["t800"])
    check("the sections that came back", recs[0]["sections"], ["t1090", "t1058"])


def test_single_scopes_are_measured_as_the_baseline():
    one = ("13:10:51Z START t123 (task t123) station 1 role manager\n"
           "13:14:00Z END t123 rc=0 bytes=8000\n")
    recs, singles = sb.parse_progress(one)
    check("a START t123 (task t123) line is not a batch", len(recs), 0)
    check("one single scope", len(singles), 1)
    check("its id", singles[0]["id"], "t123")
    check("its bytes", singles[0]["bytes"], 8000)
    check("13:10:51Z to 13:14:00Z = 3m9s", singles[0]["minutes"], 3.15)
    check("the real log has exactly one single", len(sb.parse_progress(REAL)[1]), 1)


def test_aggregates_by_batch_size_on_real_numbers():
    agg = sb.measure(REAL)
    check("two 3-task batches", agg[3]["batches"], 2)
    check("six tasks", agg[3]["tasks"], 6)
    check("(15797+45846)/6 bytes per task", agg[3]["bytes_per_task"], 10273.83)
    check("(555+679)s over 6 tasks", agg[3]["minutes_per_task"], 3.43)
    check("one section dropped among them", agg[3]["sections_missing"], 1)
    check("40854/2 bytes per task", agg[2]["bytes_per_task"], 20427.0)
    check("955s over 2 tasks", agg[2]["minutes_per_task"], 7.96)
    check("nothing dropped at n=2", agg[2]["sections_missing"], 0)
    check("singles are size 1", agg[1]["batches"], 1)
    check("its bytes per task", agg[1]["bytes_per_task"], 8000.0)
    check("its minutes per task", agg[1]["minutes_per_task"], 3.15)


def test_a_batch_over_midnight_is_reported_not_zeroed():
    text = ("23:50:00Z START BATCH tA tB station 1\n"
            "00:10:00Z END BATCH tA tB rc=0 bytes=9000\n")
    recs, singles = sb.parse_progress(text)
    check("one record", len(recs), 1)
    check("no singles", len(singles), 0)
    check("minutes is None, not a negative number", recs[0]["minutes"], None)
    check("and counted as crossing midnight", sb.measure(text)[2]["crossed_midnight"], 1)


def test_an_unpaired_start_and_a_zero_byte_answer_are_counted():
    text = ("23:59:00Z START BATCH tZ tY tX station 1\n"
            "21:00:00Z START BATCH tQ tP station 1\n"
            "21:20:00Z END BATCH tQ tP rc=1 bytes=0\n")
    recs, singles = sb.parse_progress(text)
    check("only the paired batch is a record", len(recs), 1)
    check("no singles", len(singles), 0)
    check("a crashed batch keeps its rc", recs[0]["rc"], 1)
    agg = sb.measure(text)
    check("a 0-byte answer is counted as missing bytes", agg[2]["bytes_missing"], 1)
    check("and excluded from the mean", agg[2]["bytes_per_task"], None)
    check("the START with no END is counted, not dropped", agg[3]["incomplete_start"], 1)


def test_a_log_file_on_disk_round_trips_and_loses_no_record():
    d = tempfile.mkdtemp(prefix="t1190-")
    log = Path(d) / "progress.log"
    log.write_text(REAL, encoding="utf-8")
    text = log.read_text(encoding="utf-8", errors="replace")
    recs, singles = sb.parse_progress(text)
    n_end = text.count("END BATCH ")
    assert len(recs) == n_end, "dropped records: %d of %d" % (len(recs), n_end)
    agg = sb.measure(text)
    assert sum(v["batches"] for v in agg.values()) == len(recs) + len(singles), "sizes lost records"
    assert agg[3]["bytes_per_task"] == 10273.83, agg[3]
    assert agg[3]["sections_missing"] == 1, agg[3]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            try:
                fn()
            except Exception as e:
                FAILED += 1
                print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)
