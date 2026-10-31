#!/usr/bin/env python3
"""t044: the hub's feedback box must record WHICH SECTION of the hub it came from.

Jas, 2026-09-17 (workspace/tasks/t044--design-guide.md): "Every section of the hub needs
this ... you should be aware of where this box was (where the feedback came from) so you
know what to relate it to."

The hub files every submitted note as one markdown file under workspace/app-feedback/ whose
front matter names the phone app (projects/app-repo/feedback.py:4049-4052) -- never the hub
section the box sat on. A triage session reading workspace/app-feedback/ therefore cannot
tell a bug in the Plan tab from one in Review. This pins the section as a FIELD in the record
Jas asks for, not as a rendered box.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
sys.path.insert(0, str(ROOT / "scripts"))

import feedback  # noqa: E402


class _FakeTS:
    """Stands in for taskstore: _say files a task, and a test must not touch the live queue."""

    def __init__(self):
        self.calls = []

    def add(self, title, app, body="", source=""):
        self.calls.append({"title": title, "app": app, "body": body, "source": source})
        return "t999"


def _say(tmp, fields):
    """POST the hub's feedback form with these field values; return the note file's text."""
    ts_fake = _FakeTS()
    before = set(tmp.glob("*.md"))     # CLAUDE 2026-10-28: one test calls _say twice on one tmp
    real_ts, real_dir = feedback.ts, feedback.FEEDBACK_DIR
    handler = feedback.Handler.__new__(feedback.Handler)
    handler._redirect = lambda loc: loc
    feedback.ts = ts_fake
    feedback.FEEDBACK_DIR = tmp
    try:
        def one(key, default=""):
            return fields.get(key, default)
        handler._say(one, ())
    finally:
        feedback.ts, feedback.FEEDBACK_DIR = real_ts, real_dir
    notes = sorted(set(tmp.glob("*.md")) - before)
    assert len(notes) == 1, f"expected exactly one feedback file, got {[n.name for n in notes]}"
    return notes[0].read_text(encoding="utf-8"), ts_fake


def _front_matter(text):
    lines = text.split("\n")
    assert lines[0] == "---", f"record does not open with a front-matter fence: {lines[0]!r}"
    out = {}
    for line in lines[1:]:
        if line == "---":
            return out
        assert ":" in line, f"front-matter line is not key: value -- {line!r}"
        key, val = line.split(":", 1)
        out[key.strip()] = val.strip()
    raise AssertionError("front matter is never closed")


def test_section_is_recorded_per_section_with_distinct_values():
    """THE requirement: two boxes on two sections record two DISTINCT sources, and the note is
    still filed as a task. A single repeated placeholder string cannot pass this."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        plan, ts_plan = _say(tmp, {"text": "the plan tab mis-orders the top item",
                                   "section": "Plan"})
        review, ts_review = _say(tmp, {"text": "review shows a task I already approved",
                                       "section": "Review"})

    fp, fr = _front_matter(plan), _front_matter(review)
    assert fp.get("section") == "Plan", f"Plan section recorded as {fp.get('section')!r}"
    assert fr.get("section") == "Review", f"Review section recorded as {fr.get('section')!r}"
    assert fp["section"] != fr["section"], "both sections recorded the same source"

    # The intake that already works must keep working: the note is filed as a task.
    assert ts_plan.calls, "no task was filed for the note"
    assert ts_plan.calls[0]["body"].startswith("the plan tab mis-orders")
    assert ts_review.calls[0]["title"] == "review shows a task I already approved"
    # The existing front-matter keys are unchanged in name and value.
    assert fp.get("from") == "phone", fp
    assert fp.get("app") == "unknown", fp
    assert fp.get("kind") == "note", fp
    assert "received:" in plan, plan
    assert plan.split("\n---\n")[1].strip().startswith("the plan tab mis-orders"), plan


def test_form_that_posts_no_section_writes_no_empty_key():
    """NEGATIVE guard: the Apps tab form posts no section today. It must keep producing the
    record it produces now -- an empty 'section:' key is noise that reads as a value."""
    with tempfile.TemporaryDirectory() as d:
        text, ts_fake = _say(Path(d), {"text": "the phone app will not start"})
    fm = _front_matter(text)
    assert "section" not in fm, f"section key written with no value: {fm}"
    assert fm.get("app") == "unknown", fm
    assert ts_fake.calls, "the Apps tab note must still be filed as a task"


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
