#!/usr/bin/env python3
"""t130: each app's page on the Hub carries its timeline.

RED on main: Handler._apps (feedback.py) never calls app_timeline.timeline_html. Checked in the
source, as test_hub_layout.py does: the page reads live task files. The timeline's content is
pinned by test_app_timeline.py.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    src = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    a = src.find("    def _apps(")
    body = src[a:src.find("\n    def ", a + 1)] if a >= 0 else ""
    fails = []
    if "timeline_html(" not in body:
        fails.append("_apps does not render app_timeline.timeline_html for the app")
    if "load_all(include_done=True)" not in body:
        fails.append("_apps does not hand the timeline the finished tasks too (ts.load_all(include_done=True))")
    for f in fails:
        print("FAIL:", f)
    print("test_apps_timeline_wired:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
