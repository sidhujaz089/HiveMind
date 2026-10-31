#!/usr/bin/env python3
"""Fail-first oracle for t1599: the error scanner must STREAM, not load a ledger whole.

Measured 2026-10-24: `scripts/error_unknowns.py` (which calls `error_scan.scan()`) held **22.7 GB
resident** on a 122 GB box whose model needs 93 GB. Fifteen minutes later the box fell to 1.42 GB
free and the memory guard stopped Swift -- the model Jas was talking to. The scanner is not the
only cause of that evening, but a 23 GB error scan removes the margin that makes everything else
survivable.

The cause, in `_scan_jsonl`: three full copies of the largest source (2.71 GB
`workspace/run-logs/slot-ledger.jsonl`) are alive at once --

    content = filepath.read_text()      # the whole file as one string
    content.splitlines()                # a list holding every line as its own string
    rows.append(json.loads(line))       # a list holding every parsed row, for a second pass

-- and the second pass only needs COUNTS per field width, which is O(distinct widths), not
O(rows).

So this asserts the property that cannot be faked by a smaller file: peak allocation while
scanning stays a small multiple of the longest LINE, not of the file. `tracemalloc` measures it
in-process; the fixture is synthetic and nothing live is read.

It also asserts the scanner still DOES its job, because a streaming rewrite that drops rows or
loses the width statistics would pass a memory test and fail the task.
"""
import json
import shutil
import sys
import tempfile
import tracemalloc
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import error_scan  # noqa: E402

PASSED = FAILED = 0
ROWS = 150_000
PAD = "x" * 400   # a ROUND width: the exact-width pass reports those (400 is in ROUND_WIDTHS)


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def peak_mb(fn):
    tracemalloc.start()
    try:
        out = fn()
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return out, peak / (1024 * 1024)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="t1599-"))
    try:
        # ~75 MB of healthy rows, plus one unknown status and one field with a round width in
        # every row (so the second pass has something to report).
        big = tmp / "slot-ledger.jsonl"
        with open(big, "w", encoding="utf-8") as f:
            for i in range(ROWS):
                f.write(json.dumps({"at": "2026-10-24T00:00:00Z", "status": "ok",
                                    "detail": PAD, "id": f"r{i}"}) + "\n")
            f.write(json.dumps({"at": "2026-10-24T00:00:01Z", "status": "never-seen-this",
                                "detail": PAD, "id": "the-one-bad-row"}) + "\n")
        size_mb = big.stat().st_size / (1024 * 1024)
        print(f"  (fixture: {size_mb:.0f} MB, {ROWS + 1} rows)")

        findings, peak = peak_mb(lambda: error_scan._scan_jsonl(big))
        # The old reader peaked at several times the file size. Streaming keeps roughly one line.
        check(f"one big .jsonl scans in under 30 MB of peak allocation (file {size_mb:.0f} MB)",
              peak < 30, True)
        print(f"  (peak was {peak:.1f} MB)")

        # ...and it still reads every row: the single unknown status is found by its locator.
        check("the unknown status in the big file is still reported",
              any("the-one-bad-row" in json.dumps(f) for f in findings), True)
        # ...and the width statistics still run, which needs a second look at the same rows.
        check("the exact-width pass still reports", any(
            f.get("kind") == "record-suspiciously-exact" for f in findings), True)

        # A whole DIRECTORY is scanned the same way (the live default is workspace/run-logs).
        d = tmp / "run-logs"
        d.mkdir()
        shutil.move(str(big), str(d / big.name))
        _found, peak_dir = peak_mb(lambda: error_scan.scan([str(d)]))
        check(f"scanning a directory over the same file stays under 30 MB ({peak_dir:.1f} MB)",
              peak_dir < 30, True)

        # ONE ENORMOUS LINE. workspace/run-logs/manager-commands/ holds a 33-line log with a
        # 255 MB line in it; "one line at a time" still allocates a quarter of a gigabyte, so the
        # reader windows long lines instead. 20 MB of single line must not cost 20 MB to scan.
        huge = tmp / "one-giant-line.log"
        with open(huge, "w", encoding="utf-8") as f:
            f.write("y" * (20 * 1024 * 1024))
        found, peak_huge = peak_mb(lambda: error_scan._scan_file(huge))
        check(f"a 20 MB single line scans in under 5 MB ({peak_huge:.1f} MB)", peak_huge < 5, True)

        # ...and a finding must not hold its input: a bounded quote, with the cut marked.
        long_line = "Traceback (most recent call last): " + "z" * 200000
        probe = tmp / "long-with-error.log"
        probe.write_text(long_line + "\n", encoding="utf-8")
        got = error_scan._scan_file(probe)
        check("a finding on a long line is produced", bool(got), True)
        check("  and its evidence is bounded", all(len(f.get("evidence") or "") <= 420 for f in got), True)
        check("  and it says the quote was cut",
              any("cut:" in (f.get("evidence") or "") for f in got), True)

        # The census reads the same files and had the identical pattern; it must stream too.
        _rows, peak_census = peak_mb(lambda: error_scan.census([str(d)]))
        check(f"the census streams as well ({peak_census:.1f} MB)", peak_census < 30, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
