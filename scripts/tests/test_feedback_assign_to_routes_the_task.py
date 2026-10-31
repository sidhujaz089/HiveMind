#!/usr/bin/env python3
"""t033: the phone/app feedback form's "Assign to:" role must route the TASK it creates.

Jas, 2026-09-17, in this task: a phone note he reviews that needs Claude's input is switched
over to Claude, and the point of the Assign-to dropdown (feedback.py's hold_select / hold_value)
is that the role chosen on the form is the role the work goes to. It is not today: the save
handler computes the role, writes it into the feedback NOTE's front matter (assigned: claude)
and then creates the task without passing it, so taskstore's default needs="" lands the task on
production -- out of Review, the queue Jas reads, and off Claude's board.

This suite asks the one question a person would check by hand: post the form with a role chosen,
read the task that was created, look at the role it carries. Three cases:
  claude     -> the created task is needs: claude  (the case Jas named)
  jas        -> the created task is needs: jas     (the case that files it in Review)
  no choice  -> the created task is needs: production -- the NEGATIVE guard, so a build that
                routed every note to a model role passes the two cases above and fails here.

Hermetic by construction: notes are written under a tempfile.TemporaryDirectory, the task store
is a fake that files nothing, the role store is a fixture this file owns (feedback._assignable
is replaced for the duration of the call), no model is called, no server is started, and no
subprocess is spawned. A Models-tab edit cannot turn this suite red.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "projects" / "app-repo", ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import feedback  # noqa: E402

# The role store this test owns. feedback._assignable() returns a LIST OF (key, name) PAIRS
# (feedback.py:1085-1090) and hold_value() tests membership in those keys (feedback.py:1097-1100),
# so the fixture is a list of pairs, not a dict. Replacing the function is what keeps the suite
# off the live ROLES.json: the assertions depend on this fixture, never on who the Models tab
# happens to list.
FIXED_ROLES = [("claude", "Claude"), ("jas", "Jas"), ("production", "Production")]


class _FakeTS:
    """Stands in for taskstore so the save handler files no live task.

    add() mirrors taskstore.add's signature (taskstore.py:399-400) and records every call, so a
    test can read the assignment the form delivered to the task it created -- not merely that a
    task appeared. PRODUCTION mirrors taskstore.PRODUCTION: feedback.hold_value() returns
    ts.PRODUCTION for a value that is not an assignable role, and feedback.ts is this object
    while a test runs, so the fake has to supply it.
    """

    PRODUCTION = "production"

    def __init__(self):
        self.calls = []

    def add(self, title, area="unfiled", body="", source="", at_top=False,
            kind="unfiled", needs="", author=""):
        self.calls.append({"title": title, "area": area, "body": body, "source": source,
                           "needs": needs})
        return "t999"


def _front_matter(text):
    fm = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return fm
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def _say(tmp, fields):
    """Post the hub's feedback form with these field values; return (note text, fake store).

    The note is returned because the role reaches it today; the store is returned because what
    the created task carries is the thing under test. feedback.ts, feedback.FEEDBACK_DIR and
    feedback._assignable are restored on the way out.
    """
    ts_fake = _FakeTS()
    real = (feedback.ts, feedback.FEEDBACK_DIR, feedback._assignable)
    handler = feedback.Handler.__new__(feedback.Handler)
    handler._redirect = lambda loc: loc          # a POST ends in a redirect; no server here
    feedback.ts = ts_fake
    feedback.FEEDBACK_DIR = tmp
    feedback._assignable = lambda: list(FIXED_ROLES)
    try:
        def one(key, default=""):
            return fields.get(key, default)
        handler._say(one, ())
    finally:
        feedback.ts, feedback.FEEDBACK_DIR, feedback._assignable = real
    notes = sorted(tmp.glob("*.md"))
    assert len(notes) == 1, f"expected exactly one feedback note, got {[n.name for n in notes]}"
    return notes[0].read_text(encoding="utf-8"), ts_fake


def test_assign_to_claude_routes_the_created_task_to_claude():
    """The case Jas named on 2026-09-17: a phone note handed to Claude reaches Claude."""
    with tempfile.TemporaryDirectory() as d:
        text, ts_fake = _say(Path(d), {"text": "claude should look at this intake path",
                                       "hold": "claude"})
    assert ts_fake.calls, "Assign to: Claude filed no task at all"
    assert feedback.hold_value("claude") == "claude", (
        "the role fixture is not in effect -- _assignable is not being consulted")
    fm = _front_matter(text)
    assert fm.get("assigned") == "claude", (
        f"the form recorded the Assign-to role in the note as {fm.get('assigned')!r}; expected "
        "'claude' -- the fixture's role store is not in effect")
    assert ts_fake.calls[0]["needs"] == "claude", (
        "Assign to: Claude must create a task assigned to claude; the created task carries "
        f"needs={ts_fake.calls[0].get('needs')!r} -- the role reaches the note "
        "(assigned: claude) and is dropped between the note and ts.add")


def test_assign_to_jas_routes_the_created_task_to_jas():
    """Assign to: jas must create a needs: jas task -- that assignment is what puts a phone
    note in Review, the queue Jas reads (his 2026-09-17 'switch it over')."""
    with tempfile.TemporaryDirectory() as d:
        text, ts_fake = _say(Path(d), {"text": "park this one for Jas", "hold": "jas"})
    assert ts_fake.calls, "Assign to: jas filed no task at all"
    assert _front_matter(text).get("assigned") == "jas", (
        f"note recorded assigned={_front_matter(text).get('assigned')!r}; expected 'jas'")
    assert ts_fake.calls[0]["needs"] == "jas", (
        "Assign to: jas must create a task assigned to jas (that is what files it in Review); "
        f"the created task carries needs={ts_fake.calls[0].get('needs')!r}")


def test_no_assign_to_choice_keeps_the_task_in_production():
    """NEGATIVE guard: a note with no Assign-to must keep landing in production. A fix that
    routed every note to a model role would pass the two tests above and fail this one."""
    with tempfile.TemporaryDirectory() as d:
        text, ts_fake = _say(Path(d), {"text": "an ordinary note, no Assign to chosen"})
    assert ts_fake.calls, "a note with no Assign-to must still be filed as a task"
    assert _front_matter(text).get("assigned") == "production", (
        f"a note with no Assign-to must record assigned: production, got "
        f"{_front_matter(text).get('assigned')!r}")
    assert ts_fake.calls[0]["needs"] == "production", (
        "a note with no Assign-to must create a production task; the created task carries "
        f"needs={ts_fake.calls[0].get('needs')!r}")


if __name__ == "__main__":
    failed = 0
    for name in sorted(k for k in globals() if k.startswith("test_")):
        fn = globals()[name]
        if not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
    print(f"{failed} failed")
    sys.exit(1 if failed else 0)
