#!/usr/bin/env python3
"""t401: the Hub serves a task's model sessions at /plan/<tid>/sessions and the task page links there.

RED on main: feedback.py has no such route and plan_detail() no such link. Checked in feedback.py's
source, as test_hub_layout.py does: the page reads live session files, which an oracle must not.
The page's content is task_sessions.render_html's, pinned by test_task_sessions_page.py.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    src = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    fails = []
    g0 = src.find("def do_GET(")
    get = src[g0:src.find("\n    def ", g0 + 1)] if g0 >= 0 else ""
    if not ('"/sessions"' in get or "'/sessions'" in get):
        fails.append("do_GET has no /sessions route")
    if "task_sessions" not in get or "render_html" not in get:
        fails.append("the /sessions route does not render through task_sessions.render_html")
    if "summarize" not in get:
        fails.append("the /sessions route does not read the sessions through task_sessions.summarize")
    p0 = src.find("def plan_detail(")
    if "/sessions" not in src[p0:src.find("\ndef ", p0 + 1)]:
        fails.append("plan_detail does not link the task to /plan/<tid>/sessions")
    for f in fails:
        print("FAIL:", f)
    print("test_hub_sessions_route:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
