#!/usr/bin/env python3
"""t1373 -- the review gate resolves a missed item's covered_by from the open queue.

Evidence: workspace/rca/7c90ff9c5cd6.md. The trial found the miss (right=true), the RCA's own
answer carried covered_by="", and the finding-filer accepted that empty answer, so it filed a
brand-new task while the open task t696 already covered the item: the finding record kept an
empty covered_by and sat in limbo.

Driven with a fake asker, a fake file_task and a fake note_task over a TemporaryDirectory: rca()
writes its report and its finding record inside that temp tree (every repo path the module
exposes is redirected into it) and skills.with_skills is stubbed, so nothing under the
repository is written and no model is called.

Asserted is behaviour and the record on disk, not a return shape:
  * an item whose fix names a file an OPEN task owns becomes a NOTE on that task, files NO new
    task, and the finding record ends covered_by="t9001" with disposition "covered";
  * items nothing owns still produce exactly ONE new task, and an owned item must not appear in
    it -- so "file nothing" and "file everything" both fail;
  * with no owner at all the one task is still filed and the record carries the NEW id, so the
    change cannot be satisfied by suppressing the filing.
pytest is not installed: running this file runs the tests. Non-zero exit on any failure.
"""
import json
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review  # noqa: E402

try:
    import skills  # noqa: E402
except ImportError:  # not importable here: hand rca() a stub module
    skills = types.ModuleType("skills")
    sys.modules["skills"] = skills

FIXED = "t9001"          # the open task in the fixture queue that owns OWNED_FILE
NEW = "t9002"            # the id the fake file_task hands back
OWNED_FILE = "scripts/session_review.py"
UNOWNED_FILE = "scripts/merge_gate.py"
OWNED_TITLE = "Review gate: resolve covered_by for every missed item, then record the match"
UNOWNED_TITLE = "Verify the cordis.patch.yml block was applied, then record it"

OWNED_ITEM = {
    "title": OWNED_TITLE,
    "kind": "solution",
    "why": "the trial found the miss (right=true) and the gate never matched it to the open task",
    "change": ("In the finding-filer " + OWNED_FILE + ": when an item has said='missed' and "
               "cover='', search the open queue for the task that owns the file this fix edits; "
               "if found set covered_by to that task ID and disposition='covered'."),
    "covered_by": "",
}
UNOWNED_ITEM = {
    "title": UNOWNED_TITLE,
    "kind": "data-collection",
    "why": "no evidence the block was applied; turn 7 still hit the 30s timeout",
    "change": "Add a load-time check to " + UNOWNED_FILE + " that fails on the hard defaults.",
    "covered_by": "",
}

# The marker is minted by the module's OWN helper, so the fixture cannot disagree with the format
# _open_group_task searches for; _group_marker is the producer of that marker today.
GROUP_MARK = session_review._group_marker("file", OWNED_FILE)

TASKS = [
    ({"id": FIXED, "status": "queued"},
     "Record the dsh-local-ai lesson: regenerating the patch layer can silently re-drop settings."
     "\n\n" + GROUP_MARK + "\n"),
]

FINDING = {
    "key": "7c90ff9c5cd6",
    "title": "Review gate: resolve covered_by for every missed item",
    "request": "req-46211cfc",
    "kind": "solution",
    "quote": "I'll also record a LESSONS.md entry with the evidence",
    "session": "session-46211cfc-1bb5-4fb9-b7a7-594e8547d45f",
    "source": "session-rca",
    "area": "workspace",
    "turn": 8,
    "gold": "yes",
    "found": "2026-10-16T16:19:25Z",
    "found_at": "2026-10-16T16:19:25Z",
}

KEEP = ("REPO", "ROOT", "SKILL", "SKILLS", "SKILL_DIR", "SKILL_PATH")
DIRS = ("RCA_DIR", "FINDINGS", "BASE", "TRIALS")


def _isolate(base):
    """Point every module-level path under the repository into the temp tree.

    rca() writes the RCA report and moves the finding record; redirecting every repo Path the
    module exposes keeps the whole run inside `base` (this is what makes the suite safe to run
    unattended -- it must not leave a file behind in the worktree). Each redirected path keeps a
    copy of the original file's bytes, so a constant that is READ as a template still reads. REPO
    and ROOT stay put (identity), and the skill-document paths stay put (read-only, and their
    only reader, with_skills, is stubbed out by the caller).

    Returns {name: original}; the caller restores them in a finally.
    """
    saved = {}
    for name, value in list(vars(session_review).items()):
        if name in KEEP or not isinstance(value, Path):
            continue
        try:
            inside = value.is_relative_to(REPO)
        except AttributeError:  # Python without Path.is_relative_to
            inside = str(value).startswith(str(REPO) + "/")
        if not inside:
            continue
        saved[name] = value
        target = base / name.lower()
        target.parent.mkdir(parents=True, exist_ok=True)
        if name in DIRS:
            target.mkdir(parents=True, exist_ok=True)
        try:
            if value.is_file():
                shutil.copy2(str(value), str(target))
        except OSError:
            pass
        setattr(session_review, name, target)
    return saved


def _run(items, tasks):
    """Drive rca() over a temp tree. Returns (ids, filed, notes, record read back from disk).

    filed/notes collect the calls made on the fake task surfaces, so the tests see what the gate
    DID. The record is read back from the file rca() moved the finding to -- that JSON is the
    account the dispatcher reads, and it is the thing the RCA found to be wrong.
    """
    tmp = tempfile.TemporaryDirectory()
    base = Path(tmp.name)
    finding = base / "7c90ff9c5cd6.json"
    finding.write_text(json.dumps(FINDING, indent=1) + "\n", encoding="utf-8")
    tasks_dir = base / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / (FIXED + "--record-the-dsh-local-ai-lesson.md")).write_text(
        "---\nid: " + FIXED + "\nstatus: queued\n---\n" + GROUP_MARK + "\n", encoding="utf-8")

    filed, notes = [], []

    def fake_asker(prompt, purpose, budget, **kw):
        return json.dumps(items)

    def fake_file(title, body):
        filed.append((title, body))
        return NEW

    def fake_note(tid, text):
        notes.append((tid, text))

    saved = _isolate(base)
    saved_with_skills = getattr(skills, "with_skills", None)
    skills.with_skills = lambda text, *a, **k: (text, "")
    try:
        ids = session_review.rca(finding, asker=fake_asker, file_task=fake_file,
                                 note_task=fake_note, tasks=tasks, tasks_dir=tasks_dir)
        rec = json.loads((base / "findings" / "done" / "7c90ff9c5cd6.json")
                         .read_text(encoding="utf-8"))
        return ids, filed, notes, rec
    finally:
        for name, value in saved.items():
            setattr(session_review, name, value)
        if saved_with_skills is not None:
            skills.with_skills = saved_with_skills
        tmp.cleanup()


def test_owned_item_becomes_a_note_and_files_no_new_task():
    """The defect: an empty model cover made an item an open task owns file a SECOND task."""
    ids, filed, notes, rec = _run([OWNED_ITEM], TASKS)
    assert filed == [], (
        "an item whose fix names a file an open task owns must not file a second task: %r" % (filed,))
    assert notes and notes[0][0] == FIXED, ("expected a note on " + FIXED + ": %r" % (notes,))
    assert OWNED_TITLE in notes[0][1], notes[0][1]
    assert ids == [FIXED], ids
    assert rec.get("covered_by") == FIXED, rec
    assert rec.get("disposition") == "covered", rec


def test_only_unowned_items_join_the_new_task():
    """One finding, one task: it carries the items nothing owns, and never an owned one."""
    ids, filed, notes, rec = _run([OWNED_ITEM, UNOWNED_ITEM], TASKS)
    assert len(filed) == 1, "expected exactly one new task: %r" % ([t for t, _b in filed],)
    assert filed[0][0].startswith(UNOWNED_TITLE), filed[0][0]
    assert OWNED_TITLE not in filed[0][1], (
        "an item " + FIXED + " owns is being filed a second time:\n" + filed[0][1])
    assert UNOWNED_TITLE in filed[0][1], filed[0][1]
    assert any(tid == FIXED and OWNED_TITLE in text for tid, text in notes), notes
    assert rec.get("covered_by") == FIXED, rec
    assert rec.get("disposition") == "covered", rec


def test_a_finding_nothing_owns_still_files_its_one_task():
    """Anti-vacuity: the change must not suppress filing. No owner -> one task, NEW id recorded."""
    ids, filed, notes, rec = _run([UNOWNED_ITEM], TASKS)
    assert len(filed) == 1, filed
    assert filed[0][0].startswith(UNOWNED_TITLE), filed[0][0]
    assert notes == [], notes
    assert ids == [NEW], ids
    assert rec.get("covered_by") == NEW, rec


TESTS = [
    test_owned_item_becomes_a_note_and_files_no_new_task,
    test_only_unowned_items_join_the_new_task,
    test_a_finding_nothing_owns_still_files_its_one_task,
]

if __name__ == "__main__":
    failed = []
    for fn in TESTS:
        try:
            fn()
            print("ok   %s" % fn.__name__)
        except Exception as exc:  # a crash is a finding, never a pass
            failed.append(fn.__name__)
            print("FAIL %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
    print("%d/%d passed" % (len(TESTS) - len(failed), len(TESTS)))
    sys.exit(1 if failed else 0)
