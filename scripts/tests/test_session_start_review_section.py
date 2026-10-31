#!/usr/bin/env python3
"""session_start shows Claude's section of the Review tab, grouped by why each task waits.

Jas, 2026-10-13: "If things are getting missed on the review tab, perhaps on the session start routine,
we need to add a review of that tab for yourself." 107+ tasks sat there unmentioned. Hermetic: the task
list and the tick are passed in; no live state is read except the enrolment table, which a fake tick
need not match (an unmatched chunk falls to the 'asked in a note' group).
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_start as S  # noqa: E402

FAILED = 0


def report(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n        got {got!r}\n        want {want!r}"))


def plain(lines):
    return [re.sub(r"\x1b\[[0-9;]*m", "", l) for l in lines]


def main():
    with tempfile.TemporaryDirectory() as td:
        tick = Path(td) / "tick.json"
        tick.write_text(json.dumps({"skipped": []}))
        tasks = [({"id": "t1", "title": "a", "status": "doing", "assigned": "claude", "created": "2026-10-01"},),
                 ({"id": "t2", "title": "b", "status": "queued", "assigned": "production", "created": "2026-10-01"},),
                 ({"id": "t3", "title": "c", "status": "done", "assigned": "claude", "created": "2026-10-01"},)]
        out = plain(list(S.claude_review_section(tick=tick, tasks=tasks)))
        report("counts only OPEN tasks assigned to Claude", out[0].strip().startswith("Review tab, Claude's section: 1 task"), True)
        report("names the task", any("t1" in l for l in out), True)
        report("never lists production's or a done task", any(re.search(r"\bt[23]\b", l) for l in out), False)
        out = plain(list(S.claude_review_section(tick=tick, tasks=[tasks[1]])))
        report("an empty section says so", out, ["  Review tab, Claude's section: empty"])
    print(f"\n{'FAILED' if FAILED else 'ok'}: {FAILED} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
