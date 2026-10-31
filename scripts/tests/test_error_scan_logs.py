#!/usr/bin/env python3
"""t275 fix 3, the decay test: a .log holding a known failure line produces a finding that QUOTES it.

On 2026-09-11 the scanner ran over nine real dispatch logs full of failures and returned zero --
a source that is scanned and cannot produce a finding reads as coverage. The lines here are the
fixed strings coder.py and Python print when a run ends badly.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import error_scan as E  # noqa: E402

FAILED = 0


def report(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


def main():
    d = Path(tempfile.mkdtemp(prefix="t275-"))
    lines = ["  NOT ACCEPTED after 6 attempt(s). Nothing was written.",
             "  REFUSING TO START -- the sandbox baseline is not what is declared:",
             "Traceback (most recent call last):"]
    (d / "p99--production--m.log").write_text("attempt 1 … ok\n" + "\n".join(lines) + "\n")
    (d / "quiet.log").write_text("attempt 1 … ACCEPTED (1 suite green)\n")
    f = E.scan([str(d)])
    quoted = sorted(x["evidence"] for x in f if x["kind"] == "run-failed")
    report("every terminal failure line is a finding, quoted", quoted, sorted(l.strip() for l in lines))
    report("each carries a time (the log's last write)", all(x.get("at") for x in f), True)
    report("a clean log produces none", [x for x in f if "quiet" in x["source"]], [])
    print("test_error_scan_logs:", "FAILED" if FAILED else "ok")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
