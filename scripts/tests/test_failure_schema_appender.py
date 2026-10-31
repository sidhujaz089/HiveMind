#!/usr/bin/env python3
"""The failure-schema appender keeps t366's contract (2026-09-22): one instance per failure, under
the right class; a known class gains an instance, never a twin row; the same failure twice appends
once; it never writes `defense`; an unknown shape is 'unclassified', not forced into a class."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import failure_schema as F  # noqa: E402

FAILED = 0


def report(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


def main():
    d = Path(tempfile.mkdtemp())
    try:
        p = d / "schema.json"
        p.write_text(json.dumps({"failures": [{"class": "window-full", "cause_category": "content",
                                               "instances": [], "defense": "hand-written"}]}))
        syntax = "  attempt 1 … python syntax (16000 tok, 700s)"
        report("a syntax failure is harness-edit-application", F.classify(syntax)[0], "harness-edit-application")
        report("the LAST attempt decides",
               F.classify("  attempt 1 … tests fail (2: a, 10s)\n  attempt 2 … python syntax (1 tok, 1s)")[0],
               "harness-edit-application")
        report("a window refusal is window-full", F.classify("  NEEDS-SCOPE -- refusing to start: x")[0], "window-full")
        report("an unknown shape is unclassified", F.classify("something new happened")[0], "unclassified")
        cls, how = F.record_failure("p1", "2026-10-01T00:00:00Z", syntax, path=p)
        report("a new class is added", (cls, how), ("harness-edit-application", "new-class"))
        report("a repeat is a duplicate, not a second instance",
               F.record_failure("p1", "2026-10-01T00:00:00Z", syntax, path=p)[1], "duplicate")
        F.record_failure("p2", "2026-10-01T01:00:00Z", syntax, path=p)
        F.record_failure("p3", "2026-10-01T02:00:00Z", "  NEEDS-SCOPE -- refusing", path=p)
        rows = json.loads(p.read_text())["failures"]
        report("one row per class", sorted(r["class"] for r in rows), ["harness-edit-application", "window-full"])
        report("two sightings of the edit class", len([r for r in rows if r["class"] == "harness-edit-application"][0]["instances"]), 2)
        report("the existing class gained its instance", len(rows[0]["instances"]), 1)
        report("defense is never invented",
               [r.get("defense") for r in rows], ["hand-written", None])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("test_failure_schema_appender:", "FAILED" if FAILED else "ok")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
