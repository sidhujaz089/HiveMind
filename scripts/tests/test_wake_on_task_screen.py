#!/usr/bin/env python3
"""The wake date is editable on the task screen, with a date picker (I2, t8656).

Jas, 2026-10-26: *"For the wake command I want to confirm you will add that to the task screen so I can
edit it as well. I imagine for me it will be a calendar pop up that lets me choose the date."*

The CLI landed in 14d249e9f (`taskstore.py wake <id> <date> | --clear`), so this is the surface, not the
mechanism. A browser gives the calendar for free from `<input type="date">` -- no JavaScript, no library,
and the value it posts is exactly the `YYYY-MM-DD` the store refuses anything else for.

What this refuses:

  * a field with no current value shown -- a date you cannot see is a date you cannot trust;
  * a set with no way to CLEAR: `wake` is an optional key, so the control has to be able to say "no";
  * a route that lets a bad date become a 500 -- taskstore raises ValueError and the route must turn that
    into a message, not a stack trace;
  * and the page's own help text going stale, which C3's guard now enforces for real.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
import feedback  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def main():
    f = feedback.wake_form

    # 1. THE PICKER ITSELF. `<input type="date">` is the browser's calendar.
    html = f("t1234", {"id": "t1234", "wake": "2026-11-01", "status": "queued"})
    check("the control is a date input (the browser supplies the calendar)",
          'type="date"' in html, html[:200])
    check("it posts to the wake route", 'action="/plan/wake"' in html, html[:200])
    check("it carries the task id", 'name="id" value="t1234"' in html, html[:300])
    check("the current date is shown as the field's value", 'value="2026-11-01"' in html, html[:300])

    # 2. NO WAKE YET: the field is empty, not absent -- the control is always there.
    html = f("t1234", {"id": "t1234", "status": "queued"})
    check("an undeferred task still gets the control", 'type="date"' in html, html[:200])
    check("with an empty value", 'value=""' in html, html[:300])
    check("and says it is not deferred", "not deferred" in html.lower(), html[-200:])

    # 3. CLEARING IS ITS OWN ACTION, because `wake` is an optional key.
    html = f("t1234", {"id": "t1234", "wake": "2026-11-01", "status": "queued"})
    check("a deferred task offers a way to clear it", "plan/wake" in html and "Clear" in html, html[:400])
    check("the clear posts an empty date rather than navigating elsewhere",
          html.count('action="/plan/wake"') == 2, html.count('action="/plan/wake"'))

    # 4. THE ROUTE EXISTS AND HANDLES A BAD DATE. Source-level, because the handler runs in the server;
    #    what matters is that taskstore's ValueError cannot become a 500.
    src = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    check("feedback.py has a /plan/wake route", 'path == "/plan/wake"' in src)
    seg = src[src.find('path == "/plan/wake"'):src.find('path == "/plan/wake"') + 1400]
    check("it calls the store's own set_wake", "set_wake(" in seg, seg[:200])
    check("it calls clear_wake for an empty date", "clear_wake(" in seg, seg[:200])
    check("and it turns a refused date into a message, not a stack trace",
          "ValueError" in seg and "str(e)" in seg, seg[:300])

    # 5. THE TASK PAGE ACTUALLY RENDERS IT -- a function nothing calls is not a surface.
    check("the task page includes the control", "wake_form(" in src.replace("def wake_form(", ""),
          "wake_form is defined but never used")

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
