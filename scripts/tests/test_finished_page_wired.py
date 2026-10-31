#!/usr/bin/env python3
"""t155: the Hub serves the finished-tasks list at /plan/done and the Backlog links to it.

RED on main: feedback.py has no /plan/done route and no link to it. Checked in the source, as
test_hub_layout.py does: the page reads live task files. The list itself is pinned by
test_finished_tasks.py.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    src = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    g0 = src.find("    def do_GET(")
    get = src[g0:src.find("\n    def ", g0 + 1)] if g0 >= 0 else ""
    fails = []
    if '"/plan/done"' not in get:
        fails.append("do_GET has no /plan/done route")
    if "finished_tasks" not in get or "render(" not in get:
        fails.append("/plan/done does not render through finished_tasks")
    if "review-verdicts.jsonl" not in get:
        fails.append("/plan/done does not read the verdicts that name each builder")
    p0 = src.find("def _plan_list(")
    if 'href="/plan/done"' not in src[p0:src.find("\ndef ", p0 + 1)]:
        fails.append("the Backlog (_plan_list) does not link to /plan/done")
    for f in fails:
        print("FAIL:", f)
    print("test_finished_page_wired:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
