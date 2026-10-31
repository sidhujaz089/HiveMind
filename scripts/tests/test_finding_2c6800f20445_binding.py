"""Oracle for t1026 -- bind the 2026-09-11 'Monday' promise (finding 2c6800f20445).

Task:  workspace/tasks/t1026--bind-the-2026-09-11-monday-promise-to-durable-ar.md (QUEUE.md:50)
RCA:   workspace/rca/2c6800f20445.md  (D-3 at :52, D-4 at :53, the rule at :59, solution row :85)

The finding is the deferral record on disk:
workspace/session-reviews/findings/done/2c6800f20445.json. Today it carries kind "deferral",
the verbatim promise quote, disposition "deferred" and covered_by "" -- no owner, no due date,
no tracker. That is the loss the RCA names: the field existed, the cover search ran (the record's
own candidates_consulted list, 858 entries), and the record was committed empty because the shape
permits it, so when the promised Monday (2026-09-14) arrived there was no subject to act on
(workspace/rca/2c6800f20445.md:59). The record also leaves the RCA's two open verifications
unanswered: D-4, does workspace/historical-findings.jsonl exist (2c6800f20445.md:53), and D-3,
which task produced the closure-standard oracle (2c6800f20445.md:52).

What this suite pins is the RECORD: owner, due 2026-09-14, a non-empty covered_by, the five
fields of the t607 deferral contract that scripts/session_review.py enforces (DEFERRAL_FIELDS
at scripts/session_review.py:975, deferral_violations at :980), and closed answers to D-4 and D-3.

RED ON MAIN FOR ONE REASON: the record carries none of the nine binding fields, so every test
that reads one fails on a missing key -- not on this suite's own logic. No pytest (not installed),
no model call, no live server, no process left running, no fixed /tmp path, no subprocess, no git,
and no writes at all: this suite reads one JSON file and stats files.

WHY NO HASH IS ASSERTED: an earlier draft asked `git log --diff-filter=A` which commit added the
closure artefact. The builder's sandbox is not a git repository, so that assertion can never be
satisfied by any edit to the record -- the brief-unsatisfiable shape. D-3 is therefore pinned by
what a gitless checkout can prove: the record names the artefact, a dated closure, and task ids
whose task files exist on disk.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import session_review as sr

KEY = "2c6800f20445"
FINDING = ROOT / "workspace" / "session-reviews" / "findings" / "done" / (KEY + ".json")
TASKS = ROOT / "workspace" / "tasks"
TASK_DIRS = (TASKS, TASKS / "done")
DUE = "2026-09-14"
SCAN_ARTEFACT = "workspace/historical-findings.jsonl"
CLOSURE_ARTEFACT = "workspace/gate-closures.json"
BINDING_FIELDS = ("owner", "due", "covered_by", "tracked_as", "verify_command",
                  "decay_test", "intended_state", "d4_scan_findings",
                  "d3_closure_oracle_provenance")
QUOTE = ("Tonight runs by itself; Monday I'll have the scan findings, "
         "the root-cause plan, and that oracle.")
WORDS = ("fill", "tbd", "todo", "unverified", "confirm", "placeholder", "n/a")


def record():
    """The REAL finding record, read from disk on every run. No fixture, no copy."""
    return json.loads(FINDING.read_text(encoding="utf-8"))


def task_files(task_id):
    """The task files the repo carries for this id -- live tasks and archived ones, because
    t274 and t395 sit under workspace/tasks/done/ and a glob on workspace/tasks alone misses
    every completed tracker (verified 2026-10-11)."""
    found = []
    for directory in TASK_DIRS:
        found.extend(sorted(p.name for p in directory.glob(task_id + "--*.md")))
    return found


def test_finding_record_is_the_deferral_under_test():
    """Anti-vacuity: the file this suite binds is this record, and the four fields its current
    readers key on keep their exact values. A build that rewrites the record goes red here."""
    rec = record()
    assert rec.get("key") == KEY, "not the record this task binds: %r" % (rec.get("key"),)
    assert rec.get("kind") == "deferral", "kind must stay deferral: %r" % (rec.get("kind"),)
    assert rec.get("quote") == QUOTE, (
        "the promised sentence must survive the edit verbatim, found %r" % (rec.get("quote"),))
    assert rec.get("disposition") == "deferred", (
        "the promised act has not been shown to land in git, so disposition must stay "
        "'deferred', found %r" % (rec.get("disposition"),))


def test_binding_fields_are_all_present_and_non_empty():
    rec = record()
    missing = [f for f in BINDING_FIELDS if not str(rec.get(f) or "").strip()]
    assert missing == [], (
        "the deferral is still unbound: no value for %r. A deferral with no owner, no due date "
        "and no cover is invisible -- the rule at workspace/rca/2c6800f20445.md:59" % (missing,))


def test_deferral_satisfies_the_t607_contract_as_the_repo_computes_it():
    """Checked by the validator the repository already ships (deferral_violations,
    scripts/session_review.py:980), not by a shape check invented here: a record that passes is
    one the next session's code accepts, and the field list cannot drift from the contract
    without scripts/tests/test_deferral_contract.py noticing."""
    problems = sr.deferral_violations(record())
    assert problems == [], "the repo's own deferral contract refuses this record: %r" % (problems,)


def test_due_is_the_monday_the_promise_named():
    due = str(record().get("due") or "").strip()
    assert due == DUE, (
        "the solution row at workspace/rca/2c6800f20445.md:85 names due 2026-09-14 "
        "(2026-09-11 was a Friday; the promised Monday was 2026-09-14), found %r" % (due,))


def test_tracker_ids_resolve_to_task_files_that_exist():
    """An id naming no task is a pointer to nothing: assert the lookup succeeds, not that an
    id appears. A field may bind one tracker, not two promises, so every id it names must land."""
    rec = record()
    for field in ("covered_by", "tracked_as"):
        value = str(rec.get(field) or "").strip()
        ids = re.findall(r"\bt\d+\b", value)
        assert ids, ("%s=%r names no task id; the field exists to bind the promise to a tracker"
                     % (field, value))
        for tid in ids:
            assert task_files(tid), (
                "%s=%r names %s, which has no task file under workspace/tasks/ "
                "or workspace/tasks/done/" % (field, value, tid))


def test_the_binding_carries_no_unresolved_word():
    rec = record()
    blob = json.dumps({f: rec.get(f) for f in BINDING_FIELDS}).lower()
    hits = [w for w in WORDS if w in blob]
    assert hits == [], "unresolved word left in the binding: %r" % (hits,)


def test_the_nine_binding_values_are_pairwise_distinct():
    """One placeholder string pasted into every field cannot carry nine answers."""
    rec = record()
    values = [str(rec.get(f) or "").strip() for f in BINDING_FIELDS]
    assert len(set(values)) == len(values), "the binding reuses a value: %r" % (values,)


def test_verify_command_names_a_test_that_actually_exists():
    cmd = str(record().get("verify_command") or "").strip()
    assert cmd.startswith("python3 "), "verify_command must be runnable as named, found %r" % (cmd,)
    parts = cmd.split()
    assert len(parts) == 2, "verify_command must be a single command, found %r" % (cmd,)
    target = parts[1]
    assert target.startswith("scripts/tests/"), "verify_command must name a test, found %r" % (cmd,)
    assert (ROOT / target).is_file(), (
        "verify_command points at %r, which is not a file on disk" % (target,))


def test_decay_test_names_a_file_that_exists():
    decay = str(record().get("decay_test") or "").strip()
    assert decay.startswith("scripts/"), "decay_test must name a repo file, found %r" % (decay,)
    assert (ROOT / decay).is_file(), "decay_test %r is not a file on disk" % (decay,)


def test_intended_state_names_the_key_and_the_three_distinct_deliverables():
    """The promise named three deliverables. One string covering all three is not three
    answers: each must be named, and the state must say which finding it is."""
    state = str(record().get("intended_state") or "")
    assert KEY in state, "intended_state must name the finding it binds: %r" % (state,)
    named = [d for d in ("scan findings", "root-cause plan", "oracle") if d in state]
    assert len(named) == 3, (
        "the promise named three deliverables (scan findings, root-cause plan, oracle); "
        "intended_state accounts for %r" % (named,))


def test_the_d4_and_d3_answers_are_two_distinct_statements():
    rec = record()
    d4 = str(rec.get("d4_scan_findings") or "").strip()
    d3 = str(rec.get("d3_closure_oracle_provenance") or "").strip()
    assert d4 and d3, "both answer fields must carry text"
    assert d4 != d3, "the two answers must be two answers, not one string pasted twice"


def test_d4_answer_agrees_with_the_file_on_disk():
    """D-4 is a fact about the filesystem, so its expectation is COMPUTED here from the file.
    An answer that contradicts the disk fails; a fabricated one cannot pass; and if the artefact
    is ever deleted the record goes red on its own -- the self-decaying assertion in this suite."""
    d4 = str(record().get("d4_scan_findings") or "")
    assert SCAN_ARTEFACT in d4, (
        "D-4's answer must name the artefact it checked (%s): %r" % (SCAN_ARTEFACT, d4))
    exists = (ROOT / SCAN_ARTEFACT).is_file()
    says_present = bool(re.search(r"\bpresent\b|\bexists\b", d4, re.I))
    says_absent = bool(re.search(r"\babsent\b|\bdoes not exist\b", d4, re.I))
    assert says_present != says_absent, (
        "D-4's answer states both present and absent, so it settles nothing: %r" % (d4,))
    assert says_present == exists, (
        "the record says present=%s; the file on disk says present=%s -- D-4 closes only when "
        "the recorded answer matches the filesystem" % (says_present, exists))


def test_d3_answer_names_the_artefact_a_date_and_a_task_that_exists():
    """D-3 asks which task produced the closure-standard oracle. Without git (see the module
    docstring) the record is pinned to the checks that DO resolve: the artefact file, a dated
    closure, and task ids whose task files exist on disk."""
    d3 = str(record().get("d3_closure_oracle_provenance") or "")
    assert CLOSURE_ARTEFACT in d3, (
        "D-3's answer must name the artefact it traced (%s): %r" % (CLOSURE_ARTEFACT, d3))
    assert re.search(r"\b20\d\d-\d\d-\d\d\b", d3), (
        "D-3's answer must carry the date the verification was closed: %r" % (d3,))
    ids = re.findall(r"\bt\d+\b", d3)
    assert ids, "D-3 asks which TASK produced the closure-standard oracle; name a tNNN: %r" % (d3,)
    for tid in ids:
        assert task_files(tid), (
            "D-3 names %s, which has no task file under workspace/tasks/ or workspace/tasks/done/"
            % (tid,))


def _main():
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print("PASS", name)
        except Exception as exc:
            failed.append(name)
            print("FAIL", name, "->", type(exc).__name__, exc)
    print("%d/%d passed" % (len(tests) - len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
