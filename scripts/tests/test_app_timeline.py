#!/usr/bin/env python3
"""t130: a scrollable timeline per app -- where it started, what shipped, what is next.

Jas: "a project timeline area in each app that I can scroll. It'll show me where it started and
where is going. Tasks can be linked in the timeline as they are completed and scheduled."
RED on main: scripts/app_timeline.py is a stub with no timeline_html(). GREEN when
timeline_html(area, tasks, releases) renders, oldest first: a Started line, each release with its
date, each finished task linked with its completion date, then a Next section of the area's queued
tasks in queue order. Pure: fixture task metas and releases, nothing live.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import app_timeline as at  # noqa: E402

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


TASKS = [  # in queue order, done ones anywhere: the function sorts what needs sorting
    {"id": "t10", "title": "Queued first <b>", "area": "app-x", "status": "queued", "created": "2026-09-01T000000Z"},
    {"id": "t11", "title": "Queued second", "area": "app-x", "status": "doing", "created": "2026-09-02T000000Z"},
    {"id": "t03", "title": "Finished later", "area": "app-x", "status": "done",
     "created": "2026-08-10T000000Z", "completed": "2026-09-20T120000Z"},
    {"id": "t02", "title": "Finished early", "area": "app-x", "status": "done",
     "created": "2026-08-05T000000Z", "completed": "2026-08-20T120000Z"},
    {"id": "t04", "title": "Dropped", "area": "app-x", "status": "cancelled",
     "created": "2026-08-06T000000Z", "completed": "2026-08-07T000000Z"},
    {"id": "t99", "title": "Another app", "area": "app-y", "status": "done",
     "created": "2026-07-01T000000Z", "completed": "2026-07-02T000000Z"},
]
RELEASES = [("2026-08-19", "0.2-dictation"), ("2026-08-15", "0.1")]


def main():
    if not hasattr(at, "timeline_html"):
        print("  [BAD]  app_timeline has no timeline_html(): the missing change")
        return 1
    h = at.timeline_html("app-x", TASKS, RELEASES)
    ck("starts at the area's earliest date", "Started" in h and "2026-08-05" in h, True)
    order = [h.find(x) for x in ("Started", "0.1", "0.2-dictation", "Finished early", "Finished later")]
    ck("oldest first: start, releases and finished tasks interleaved by date", order == sorted(order) and -1 not in order, True)
    ck("finished tasks are linked to their task pages", ('href="/plan/t02"' in h, 'href="/plan/t03"' in h), (True, True))
    ck("each finished task carries its completion date", ("2026-08-20" in h, "2026-09-20" in h), (True, True))
    nxt = h.find("Next")
    ck("a Next section follows, the queued tasks in queue order",
       nxt != -1 and nxt < h.find('href="/plan/t10"') < h.find('href="/plan/t11"'), True)
    ck("cancelled tasks and other apps' tasks are left out", ("Dropped" in h, "Another app" in h), (False, False))
    ck("titles are HTML-escaped", ("&lt;b&gt;" in h, "Queued first <b>" in h), (True, False))
    ck("it scrolls rather than growing the page", bool(re.search(r"overflow(-y)?\s*:\s*auto", h)), True)
    e = at.timeline_html("app-z", TASKS, [])
    ck("an app with no history says so plainly", "Nothing yet" in e, True)
    print("test_app_timeline:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
