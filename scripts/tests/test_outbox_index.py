#!/usr/bin/env python3
"""Fixtures for the outbox index.

The index exists because a Code-to-Chat message was delivered only if a session happened to
search the right glob uncapped, and two went unread for days. So the property that matters is
that it never quietly loses a receipt: an unreadable line must be REPORTED, not skipped.

`L-087` is the standing reason. The feedback form silently discarded every non-image upload
with a bare `continue` and returned success, and the export it dropped was not noticed for
three days. A `continue` on a parse failure is that same bug in a different file.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import outbox_index as oi  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        good = {"file": "a.md", "read": "2026-08-25T00:00:00Z", "by": "chat"}
        receipts = tmp / "outbox-receipts.jsonl"
        receipts.write_text(
            json.dumps(good) + "\n"
            + "\n"                                  # blank: fine, skip silently
            + '{"file": "b.md", "read": ' + "\n"    # truncated JSON
            + "not json at all\n"
            + json.dumps({"read": "x"}) + "\n",     # valid JSON, no "file"
            encoding="utf-8")
        oi.RECEIPTS = receipts
        oi.MALFORMED.clear()

        print("A well-formed receipt is read:")
        seen = oi.acknowledged()
        report("the good receipt is acknowledged", seen.get("a.md"),
               "2026-08-25T00:00:00Z")
        report("and nothing else was invented", len(seen), 1)

        print("\nAn UNREADABLE line is reported, not silently dropped:")
        # This is the whole point. A receipt Chat posts with a typo currently vanishes and
        # the index says "unacknowledged" forever, with no signal that a line was unreadable.
        report("malformed lines are recorded", len(oi.MALFORMED) >= 2, True)
        report("and the bad text is kept so it can be fixed",
               any("not json at all" in m for m in oi.MALFORMED), True)

        print("\nA blank line is NOT malformed — it is just blank:")
        report("blank lines are not reported",
               any(m.strip() == "" for m in oi.MALFORMED), False)

        print("\nValid JSON with no 'file' key is malformed for this purpose:")
        report("a receipt naming nothing is reported",
               any('"read": "x"' in m or "'read': 'x'" in m for m in oi.MALFORMED), True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
