#!/usr/bin/env python3
"""t703: the Model-sessions page (http://100.64.0.10:8091/plan/t690/sessions) scrolls to
the right forever; Jas wants the text to wrap.

The page body is scripts/task_sessions.py::render_html (projects/app-repo/feedback.py:3748-3750).
It emits the answer text in a bare <pre> (task_sessions.py:143) and every read path in a bare
<code> (task_sessions.py:135). Neither is styled: the Hub stylesheet's only pre rule is
`.mdoc pre{white-space:pre-wrap;word-break:break-word;...}` (feedback.py:301-302), scoped to
`.mdoc`, and this page's <pre> sits in a plain `.card` (feedback.py:433-434). The browser default
for <pre> is `white-space:pre`, which does not break a long token, so one 240-char string makes
the whole document scroll sideways.

RED on main: render_html emits <pre> and <code> with no attributes, so the class the page needs
is absent. GREEN when every text-bearing element in the fragment carries `prewrap`.

A dict fixture; no live state, no server, no model, no /tmp paths.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

FAILS = []

# Two DISTINCT long values on purpose: a 240-char unbreakable token (4x60, no spaces) and a
# path-shaped token arriving through `reads`. One repeated placeholder would let a build that
# marks only one kind of element pass.
LONG_TOKEN = "ab12" * 60
LONG_PATH = "workspace/task-sessions/t690/20261006T013404Z-swift-" + "b" * 180
SENTENCE = "The answer names three files and cites one line."


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


def summary():
    return {"file": "20261006T013404Z-swift.jsonl", "model": "swift", "role": "manager",
            "started": "2026-10-06T01:34:04Z", "purpose": "manager: t690 <scoping>",
            "prompt_chars": 12345, "tool_calls": 3, "unreadable": 0, "done_reason": "stop",
            "answer": LONG_TOKEN + "\n" + SENTENCE,
            "reads": [LONG_PATH, "scripts/a.py"]}


def main():
    import task_sessions as ts
    h = ts.render_html("t690", [summary()])

    ck("the unbreakable token is rendered", LONG_TOKEN in h, True)
    ck("the ordinary sentence is rendered", SENTENCE in h, True)
    ck("the long path is rendered", LONG_PATH in h, True)

    pres = re.findall(r"<pre([^>]*)>", h)
    ck("exactly one <pre> carries the answer", len(pres), 1)
    ck("the answer <pre> is marked wrappable", "prewrap" in (pres[0] if pres else ""), True)

    codes = re.findall(r"<code([^>]*)>(.*?)</code>", h, re.S)
    ck("both read paths render as <code>", len(codes), 2)
    ck("every <code> is marked wrappable",
       all("prewrap" in attrs for attrs, _ in codes), True)

    # NEGATIVE. A build that pins the text onto one line, or puts it in a sideways scroller, is
    # the reported defect, not a fix. Presence-only checks would pass one.
    ck("no <code> is pinned to nowrap", any("nowrap" in a + t for a, t in codes), False)
    ck("no <code> gets a sideways scroller", any("overflow-x" in a + t for a, t in codes), False)
    ck("the <pre> is not pinned to nowrap", "nowrap" in (pres[0] if pres else ""), False)

    print("test_task_sessions_wrap:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
