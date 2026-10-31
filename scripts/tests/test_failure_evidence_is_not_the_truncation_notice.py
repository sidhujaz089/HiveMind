#!/usr/bin/env python3
"""A recorded failure must carry a line about the FAILURE, not the truncation notice.

`record_failure()` stores one line as the instance's evidence: the last `attempt N` line, else the LAST
line of the text it was given. dispatch_loop passes `trail + "\\n" + tail`, and `cap()` appends its
`[TRUNCATED -- ...]` marker at the END of the tail. So whenever a log tail was capped, the last line WAS
the marker, and the record stored the notice instead of what failed.

Measured 2026-10-25: 170 of the 319 "unclassified" failures carry exactly that -- evidence that is only
the notice, with the log named but nothing about the failure. Those records cannot be audited or
classified, and the fix is one line of selection, not a bigger field.

The rule this establishes: the notice may stay (it is honest about the cut), but it must never BE the
evidence when anything else is available.

Hermetic: strings only.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import failure_schema as fs  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


MARK = ("[TRUNCATED -- the end of workspace/run-logs/dispatch/x--1791508227.log: showing the first 3000 "
        "of 3007 characters; 7 characters are NOT shown. Treat everything after this point as unseen, "
        "and say so rather than assuming what it contains.]")


def evidence_of(text, d=None):
    """What record_failure() ACTUALLY stores, read back from a FRESH temporary schema each call --
    one file per case, or the next case reads the previous row (which is how this oracle first failed)."""
    import json
    import tempfile
    d = tempfile.mkdtemp()
    p = Path(d) / "failure-schema.json"
    import json as _j
    if not p.exists():
        p.write_text(_j.dumps({"failures": []}))     # append() updates an existing schema, not creates one
    fs.record_failure("test-chunk", "2026-10-25T00:00:00Z", text, path=p)
    d = json.loads(p.read_text())
    rows = d.get("failures") or []
    row = rows[-1] if isinstance(rows, list) else list(rows.values())[-1]
    return (row.get("instances") or [{}])[-1].get("evidence")


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        body = "attempt 3 ... refused (lines 10-12 cut a multi-line string)\nthe last line of the log"
        check("a capped log stores a line about the failure, not the marker",
              evidence_of(body + "\n" + MARK, d) == "attempt 3 ... refused (lines 10-12 cut a multi-line string)",
              evidence_of(body + "\n" + MARK, d))
        check("  and with no attempt line, the last real line is kept, not the marker",
              evidence_of("the last line of the log\n" + MARK, d) == "the last line of the log",
              evidence_of("the last line of the log\n" + MARK, d))
        check("an attempt line still wins when there is one",
              evidence_of("attempt 1 ... ERROR x\nlater\n" + MARK, d) == "attempt 1 ... ERROR x",
              evidence_of("attempt 1 ... ERROR x\nlater\n" + MARK, d))
        check("an uncapped log is unchanged",
              evidence_of("attempt 2 ... refused\nplain tail", d) == "attempt 2 ... refused",
              evidence_of("attempt 2 ... refused\nplain tail", d))
        check("if the marker is all there is, it is kept rather than lost",
              evidence_of(MARK, d) == MARK, evidence_of(MARK, d))

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
