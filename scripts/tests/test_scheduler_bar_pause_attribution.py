#!/usr/bin/env python3
"""t693: the hub's scheduler bar must say who paused the scheduler, and why.

The bar is function scheduler_controls() in projects/app-repo/feedback.py. It asks
scheduler_commands.is_paused() -- the flag file's EXISTENCE -- and renders a literal
summary line naming the effect ("the line is off (Swift, Manager queue)"), never the
actor, the reason, or the time.

The attribution IS recorded, by the button the bar's own Pause form posts to: the
/status/pause handler in feedback.py passes ("jas", "paused from the Hub") to
scheduler_commands.breaker_off, which calls scheduler_commands.pause(who, why) and writes
{"at", "who", "why"} into the flag file. The same record is written at boot with
who="boot", and scripts/dispatch_loop.py already puts who/why/at into its paused ledger
row. So the data the bar hides is on disk, in the format the bar's own button writes.

RED ON MAIN: with a recorded pause the bar's HTML carries the recorded reason, the recorded
actor and the recorded time. Today it carries none of them -- feedback.py has no reader.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))

import scheduler_commands as sc                          # noqa: E402
import feedback                                          # noqa: E402

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


# The two DISTINCT strings the Hub's Pause button passes today. Two values, not one
# placeholder, so a bar that prints the reason in the actor's slot -- or prints only one
# of the two -- cannot pass.
WHO = "jas"
WHY = "paused from the Hub"
AT = "2026-10-11T00:00:00Z"


def _bar(contents):
    """Render the bar against a flag file inside a temp dir. The real
    workspace/run-logs/scheduler-paused.enabled is never created, read or deleted: the
    reader resolves scheduler_commands.PAUSE_FLAG at call time, so patching the module
    attribute is enough, and the original path is restored in the finally. feedback.py
    imports the same module object from sys.modules, so it sees the patched attribute."""
    with tempfile.TemporaryDirectory() as d:
        flag = Path(d) / "scheduler-paused.enabled"
        if contents is not None:
            flag.write_text(contents, encoding="utf-8")
        old, sc.PAUSE_FLAG = sc.PAUSE_FLAG, flag
        try:
            return feedback.scheduler_controls()
        finally:
            sc.PAUSE_FLAG = old


def test_bar_names_who_paused_it_and_why():
    # The audit item, stated as the bar's own output.
    html = _bar(json.dumps({"at": AT, "who": WHO, "why": WHY}) + "\n")
    check("PAUSED" in html, "the bar must still say the scheduler is PAUSED")
    check(WHY in html, "the bar must show the recorded reason " + repr(WHY) + "; got: " + html)
    check(WHO in html, "the bar must name who paused it " + repr(WHO) + "; got: " + html)
    check(AT in html, "the bar must show when it was paused (" + AT + "); got: " + html)
    check("scheduler controls unavailable" not in html,
          "a readable recorded pause must not degrade to 'unavailable'; got: " + html)


def test_recorded_text_is_escaped_not_injected():
    # who/why come from a person's keystrokes, and the file's own esc() escapes every
    # other field the bar prints. A bar that splices them raw injects markup into the Hub.
    html = _bar(json.dumps({"at": AT, "who": "a<b>", "why": "say <b>hi</b>"}) + "\n")
    check("a&lt;b&gt;" in html,
          "the recorded actor must be escaped by the file's own esc(); got: " + html)
    check("a<b>" not in html, "raw markup from a flag must not reach the page; got: " + html)
    check("say <b>hi</b>" not in html,
          "the recorded reason must not be injected as markup; got: " + html)


def test_bar_shows_no_attribution_while_running():
    # NEGATIVE: a running scheduler must not gain attribution text it has no record of.
    html = _bar(None)
    check("running" in html, "a running scheduler must read as running; got: " + html)
    check("PAUSED" not in html, "a running scheduler must not say PAUSED; got: " + html)
    check(WHY not in html, "no pause recorded must show no reason; got: " + html)


def test_bar_invents_no_actor_for_a_hand_written_flag():
    # The flag is a FILE on purpose -- scheduler_commands.py calls it the one signal that
    # still works when everything else is wedged -- and the loop's printed recovery
    # instruction is to delete it by hand, so a touch-written flag is a normal state. It
    # records no actor: show the pause, blame no one. "marcelo" is a name no legitimate
    # bar markup can collide with.
    html = _bar("paused for the demo by marcelo\n")
    check("PAUSED" in html, "a hand-written flag is a pause; the bar must say so; got: " + html)
    check("marcelo" not in html,
          "a hand-written flag records no actor; the bar invented one: " + html)
    check("scheduler controls unavailable" not in html,
          "an unparseable flag must degrade to 'no one recorded', not to 'unavailable'; "
          "got: " + html)


def test_bar_keeps_the_time_when_the_reason_is_missing():
    # A flag that parses and records no reason: show what IS recorded, invent no cause.
    html = _bar(json.dumps({"at": AT}) + "\n")
    check("PAUSED" in html, "a flag with no attribution is still a pause; got: " + html)
    check(AT in html, "the recorded time must survive; got: " + html)
    check(WHY not in html, "a flag with no reason recorded must not gain one; got: " + html)


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    bad = 0
    for fn in tests:
        FAILS.clear()
        try:
            fn()
        except Exception as e:                                   # noqa: BLE001
            FAILS.append("raised " + type(e).__name__ + ": " + str(e))
        if FAILS:
            bad += 1
            print("FAIL " + fn.__name__)
            for m in FAILS:
                print("      " + m)
        else:
            print("ok   " + fn.__name__)
    print("\n" + str(len(tests)) + " test(s), " + str(bad) + " failing")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run())
