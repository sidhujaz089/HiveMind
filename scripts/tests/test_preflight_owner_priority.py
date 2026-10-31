#!/usr/bin/env python3
r"""t1107 -- an owner-declared priority must be a TRACKED task, checked mechanically.

Source: workspace/rca/bbc8fa369c41.md. The deferral it records is "I'll let it finish and put
sustainment in a second focused pass" -- an item the owner named as most important lived only as
a chat sentence for ~1h10m because nothing in the dispatch path forced it to be a numbered task
with a named next actor. preflight_brief.py checks a brief's paths (the missing-paths block in
main), its oracle's self-consistency and its dependencies; it checks nothing about what a brief
PROMISES the owner.

THE CONTRACT, in the shape the code reads it:

    OWNER-PRIORITY: <tNNNN> next-actor: <who>

* marker  r"(?im)^\s*(OWNER-PRIORITY:.*)$"  -- the whole LINE is captured, so the objection
  quotes the brief's own words, marker included (never-truncate-a-record)
* task id r"\bt\d+\b"
* owner   r"next-actor\s*:\s*(\S.*)"   (case-insensitive; an empty value does not count)

A declaration naming no id, naming an id not in workspace/tasks/QUEUE.md, or naming no next-actor
makes preflight_brief.main() exit 2 WITHOUT calling the model and WITHOUT taking the lease, and
appends one ledger row. A brief with no marker behaves exactly as it does today -- that exemption
is pinned as hard as the rule: a gate that fires on ordinary prose gets --no-verify'd in a week
(planning-for-sustainment).

No live state: tempfile only, every model surface patched, nothing written into the repository.
"""
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import preflight_brief as pb
import skills

QUEUE_PATH = REPO / "workspace" / "tasks" / "QUEUE.md"
FAKE_ID = "t99999999"
LONG_TAIL = ("sustainment of the harness: every step of the thirteen-step plan needs its "
             "fixture, its carry and its decay test written into the same brief that dispatches "
             "it, because a second focused pass that is never dispatched is how the owner's "
             "stated most-important item became a sentence nobody can find")


def _queue_text():
    try:
        return QUEUE_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"  (QUEUE.md unreadable: {e}; real-artefact cases skipped)")
        return ""


def test_a_declaration_that_names_no_task_id_is_a_gap():
    decl = "OWNER-PRIORITY: the sustainment pass next-actor: frontline"
    gaps = pb.owner_priority_gaps(decl + "\n", queue_text="t1107 -- sustainment\n")
    assert len(gaps) == 1, gaps
    g = gaps[0]
    assert g.startswith(decl), f"must quote the whole line: {g!r}"
    assert "task id" in g.lower(), g


def test_a_declaration_that_names_no_next_actor_is_a_gap():
    text = _queue_text()
    ids = sorted(set(re.findall(r"\bt\d+\b", text)))
    if not ids:
        print("  (no task ids in QUEUE.md; positive id skipped)")
        return
    decl = f"OWNER-PRIORITY: {ids[0]} -- sustainment, second focused pass"
    gaps = pb.owner_priority_gaps(decl + "\n", queue_text=text)
    assert len(gaps) == 1, gaps
    assert gaps[0].startswith(decl), gaps[0]
    assert "next-actor" in gaps[0], gaps[0]


def test_an_empty_next_actor_value_is_a_gap():
    decl = "OWNER-PRIORITY: t1107 next-actor:"
    gaps = pb.owner_priority_gaps(decl + "\n", queue_text="t1107 -- x\n")
    assert len(gaps) == 1, gaps
    assert gaps[0].startswith(decl), gaps[0]
    assert "next-actor" in gaps[0], gaps[0]


def test_an_id_that_is_in_no_queue_is_a_gap():
    decl = f"OWNER-PRIORITY: {FAKE_ID} next-actor: frontline -- {LONG_TAIL}"
    gaps = pb.owner_priority_gaps(decl + "\n", queue_text="t1107 -- x\n")
    assert len(gaps) == 1, gaps
    assert gaps[0].startswith(decl), f"truncated objection: {gaps[0]!r}"
    assert FAKE_ID in gaps[0], gaps[0]
    assert "queue" in gaps[0].lower(), gaps[0]


def test_a_declaration_that_is_tracked_and_owned_is_clean():
    text = _queue_text()
    ids = sorted(set(re.findall(r"\bt\d+\b", text)))
    if not ids:
        print("  (no task ids in QUEUE.md; positive case skipped)")
        return
    brief = f"OWNER-PRIORITY: {ids[0]} next-actor: frontline -- sustainment, second focused pass\n"
    assert pb.owner_priority_gaps(brief, queue_text=text) == [], pb.owner_priority_gaps(brief, queue_text=text)


def test_a_declaration_missing_both_parts_produces_both_gaps():
    decl = "OWNER-PRIORITY: the preflight gate, then a second focused pass"
    gaps = pb.owner_priority_gaps(decl + "\n", queue_text="t1107 -- x\n")
    assert len(gaps) == 2, gaps
    assert all(g.startswith(decl) for g in gaps), gaps


def test_the_marker_is_case_insensitive_and_keeps_the_briefs_own_words():
    decl = "owner-priority: t1107 sustainment follow-on"
    gaps = pb.owner_priority_gaps(decl + "\n", queue_text="t1107 -- x\n")
    assert len(gaps) == 1, gaps
    assert gaps[0].startswith(decl), f"record the brief's own line, not a normalisation: {gaps[0]!r}"
    assert "next-actor" in gaps[0], gaps[0]


def test_leading_whitespace_is_not_recorded():
    gaps = pb.owner_priority_gaps("    OWNER-PRIORITY: t1107 sustainment\n", queue_text="t1107 -- x\n")
    assert len(gaps) == 1, gaps
    assert gaps[0].startswith("OWNER-PRIORITY:"), repr(gaps[0])


def test_no_marker_in_prose_changes_nothing():
    plain = ("The most important part of this change is the gate. I will do the rest in a "
             "second focused pass later. Priorities: the oracle first, then the docs.")
    assert pb.owner_priority_gaps(plain, queue_text="t1107 -- x\n") == []
    assert pb.owner_priority_gaps("", queue_text="") == []
    assert pb.owner_priority_gaps(None, queue_text="") == []


def test_tracked_means_the_queue_as_it_actually_is():
    text = _queue_text()
    ids = sorted(set(re.findall(r"\bt\d+\b", text)))
    tracked = pb.tracked_task_ids(text)
    for tid in ids[:8]:
        assert tid in tracked, f"{tid} is in QUEUE.md but tracked_task_ids rejects it"
    assert FAKE_ID not in tracked, "an id in no queue was read as tracked"
    if ids:
        assert len(tracked) == len(ids), (sorted(tracked)[:8], ids[:8])
    assert pb.tracked_task_ids("") == set()
    assert pb.tracked_task_ids(None) == set()


def _run_main(brief_text):
    rows, calls, leases = [], [], []

    def fake_record(*a, **kw):
        rows.append((a, kw))

    def fake_ask(prompt, model, timeout=None, num_predict=12000):
        calls.append(prompt)
        return ("VERDICT: SATISFIABLE\nWHY: the brief names real files and the oracle is "
                "self-consistent\nMISSING: nothing", "stop")

    saved = (pb.record_review, pb.ask, pb.modellease.acquire, pb.modellease.release,
             skills.with_skills, sys.argv)
    pb.record_review = fake_record
    pb.ask = fake_ask
    pb.modellease.acquire = lambda *a, **kw: (leases.append(1), "sentinel")[1]
    pb.modellease.release = lambda *a, **kw: None
    skills.with_skills = lambda prompt, kind: (prompt, [])
    sys.argv = ["preflight_brief.py", "--brief", "BRIEF", "--files", "scripts/preflight_brief.py"]
    try:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "brief.txt"
            f.write_text(brief_text, encoding="utf-8")
            sys.argv[2] = str(f)
            rc = pb.main()
    finally:
        (pb.record_review, pb.ask, pb.modellease.acquire, pb.modellease.release,
         skills.with_skills, sys.argv) = saved
    return rc, rows, calls, leases


def _field(row, index, name):
    a, kw = row
    if name in kw:
        return kw[name]
    return a[index] if len(a) > index else ""


def test_main_refuses_an_untracked_priority_before_spending_the_model():
    rc, rows, calls, leases = _run_main(
        f"OWNER-PRIORITY: {FAKE_ID} next-actor: frontline -- {LONG_TAIL}\n")
    assert rc == 2, f"exit {rc}: an untracked owner priority must stop the dispatch"
    assert calls == [], f"the gate must stop BEFORE the model is called ({len(calls)} calls)"
    assert leases == [], f"the gate must stop BEFORE the lease is taken ({len(leases)} taken)"
    assert len(rows) == 1, rows
    assert _field(rows[0], 6, "verdict") == "NOT SATISFIABLE", rows[0]
    note = _field(rows[0], 11, "note")
    assert FAKE_ID in note, f"the objection does not name the task: {note!r}"
    assert "OWNER-PRIORITY" in note, f"the objection does not name the marker: {note!r}"
    assert LONG_TAIL in note, f"objection recorded truncated: {note!r}"


def test_main_allows_a_tracked_declaration_and_records_the_pass():
    text = _queue_text()
    ids = sorted(set(re.findall(r"\bt\d+\b", text)))
    if not ids:
        print("  (no task ids in QUEUE.md; positive path skipped)")
        return
    brief = f"OWNER-PRIORITY: {ids[0]} next-actor: frontline -- the tracked follow-on\n"
    rc, rows, calls, leases = _run_main(brief)
    assert rc == 0, f"exit {rc}: a tracked, owned priority must not be blocked"
    assert len(calls) == 1, calls
    assert len(leases) == 1, leases
    assert len(rows) == 1, rows
    assert _field(rows[0], 6, "verdict") == "SATISFIABLE", rows[0]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
