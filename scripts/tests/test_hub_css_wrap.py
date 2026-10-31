#!/usr/bin/env python3
"""t703: the Model-sessions page scrolls sideways because the text blocks have no wrapping rule.

scripts/task_sessions.py::render_html marks the text it emits with class="prewrap"; the browser
only wraps it if the Hub's stylesheet says so. feedback.py builds every page through shell()
(line 1008), whose style block is `{CSS}{appnav.CSS}` (line 1014), so the rule has to live in this
file. Today the stylesheet's only pre rule is
`.mdoc pre{white-space:pre-wrap;word-break:break-word;...}` (lines 301-302) -- scoped to .mdoc,
which the sessions page is not.

RED on main: no .prewrap rule exists in projects/app-repo/feedback.py.
GREEN when a .prewrap rule breaks text: a wrapping white-space value, an overflow-break property,
and neither nowrap nor a sideways scroller in its declaration block.

Reads the source as text: no import (feedback.py pulls in the whole Hub), no server, no model,
no writes.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "projects" / "app-repo" / "feedback.py"

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


def declarations(src, name):
    """The body of the first rule whose selector list includes `.name`."""
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", src):
        if "." + name in re.findall(r"[.#]?[\w-]+", sel):
            return body
    return None


def main():
    src = TARGET.read_text(encoding="utf-8")
    rule = declarations(src, "prewrap")
    ck("a .prewrap rule exists in feedback.py", rule is not None, True)
    body = rule or ""
    ck("it asks for a wrapping white-space, not pre/nowrap",
       bool(re.search(r"white-space\s*:\s*(pre-wrap|normal)", body)), True)
    ck("it breaks long tokens (overflow-wrap / word-break)",
       bool(re.search(r"(overflow-wrap|word-break)\s*:\s*(anywhere|break-word|break-all)", body)),
       True)
    # NEGATIVE. A rule that keeps the text on one line, or scrolls it sideways, is the reported
    # defect wearing the class name. Presence-only checks pass those.
    ck("the rule does not set nowrap", "nowrap" in body, False)
    ck("the rule does not add a sideways scroller", "overflow-x" in body, False)

    print("test_hub_css_wrap:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
