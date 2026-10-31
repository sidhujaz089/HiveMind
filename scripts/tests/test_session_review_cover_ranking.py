#!/usr/bin/env python3
"""t791: the cover search must rank the task that COVERS a finding above a task that QUOTES it.

The sibling suite (scripts/tests/test_session_review_crossref.py) asserts this against the real
workspace/tasks tree, so it is data: a task filed by a later session quotes the session's own
words, shares every term of the probe, and pushed the real cover t328 out of the top 5
(workspace/tasks/t791--...md, claude-code note 2026-10-07). This suite pins the SAME rule on a fake
tree of made-up tasks with fixed titles, so no future queue change can move an assertion.

Nothing real is read: tempfile only, and sr.REPO is redirected for these calls. Hermeticity is
asserted as a SUBSET (no id outside the fake tree may appear) and never as an exact set: _terms
keeps five words by length, and this quote's fifth word is a tie between 5-letter words whose order
Python's set iteration decides per run -- an exact-set assertion would be flaky, not a guard."""
import sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts" / "tests")]
import session_review as sr  # noqa: E402
import skills  # noqa: E402
from test_session_review import point_at  # noqa: E402

skills.LOADS = Path(tempfile.mkdtemp(prefix="skill-loads-")) / "skill-loads.jsonl"

P = F = 0


def report(name, got, want):
    global P, F
    ok = got == want
    P += ok
    F += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def rank(ids, tid):
    """Position of tid in the search's answer, one past the end when it was not returned."""
    return ids.index(tid) if tid in ids else len(ids) + 1


# The finding the task's notes use. sr._terms() gives this quote five plain words -- no
# identifier-shaped term -- because that is what session prose carries.
Q791 = ("I'll finish the parts that don't need that permission (documenting the root cause and the "
        "fix), then stop and ask you directly for the systemd edit.")
# The artefact-citation case (RCA 028b97d00e2a). Its terms are identifier-shaped, which is what
# task bodies carry: scope_queue.py, not-applicable, classified, applicable, inventory.
Q2 = ("Once that lands, `scope_queue.py` rows are classified by the inventory instead of sliding "
      "past as `not-applicable`, and the gate bites for the first time.")

tmp = Path(tempfile.mkdtemp(prefix="sr-rank-"))
point_at(tmp)
sr.REPO = tmp / "repo"                       # task_candidates reads REPO/workspace/tasks
TASKS = sr.REPO / "workspace" / "tasks"
TASKS.mkdir(parents=True)


def fake_task(tid, title, body):
    """One task in the fake tree, in the shape task_candidates parses: front-matter id/title/status
    (its regex is `^(id|title|status):\\s*(.*)$`) and notes below."""
    (TASKS / f"{tid}--fake.md").write_text(
        f"---\nid: {tid}\ntitle: {title}\nstatus: queued\n---\n\n## Notes\n\n{body}\n", encoding="utf-8")


# t328: the task that COVERS the finding. Its title is the work; it never quotes the session.
fake_task("t328",
          "Ask Jas for the systemd permission edit and document the root cause of the dry-run timeout",
          "The session said it would stop and ask for the systemd edit; documenting the root cause "
          "is the other half. Nothing was filed, so this task is that work.")
# t607: an RCA filing that QUOTES the finding verbatim. Same words, and no coverage of the work.
fake_task("t607", "Record the RCA for the dropped session-review finding",
          "The session said, verbatim: " + Q791 + " This task records that, and the missing "
          "verification that should follow it.")
# t743: the twin -- the SAME title as t607, the same words, and it does NOT quote the finding.
fake_task("t743", "Record the RCA for the dropped session-review finding",
          "The session promised to finish the permission work, document the root cause, and ask "
          "for the systemd edit afterwards.")
# t109: the plain title-word match: the words in the title, nothing in the body.
fake_task("t109", "Session review: finish the documentation of the root cause",
          "Progress notes only; no work is described.")
# t235 cites the artefacts the second finding names; t555 matches its words in its TITLE only.
fake_task("t235", "Add the inventory classification to scope_queue's rows",
          "The gate reads scope_queue.py and evidence.py; rows must be classified by the inventory "
          "and never marked not-applicable.")
fake_task("t555", "Classified inventory of the applicable queue",
          "Progress notes; the queue's rows are tracked here.")

FAKE791 = {"t328", "t607", "t743", "t109"}


def test_cover_outranks_quoter():
    found = sr.task_candidates(Q791, limit=5)
    ids = [t[0] for t in found]
    report("the search sees only the fake tree (hermetic)", set(ids) <= FAKE791, True)
    report("  it found the cover and the two RCA twins", {"t328", "t607", "t743"} <= set(ids), True)
    report("the task that COVERS the finding is ranked first", ids[:1], ["t328"])
    report("  a task that QUOTES the finding does not outrank the cover",
           rank(ids, "t328") < min(rank(ids, "t607"), rank(ids, "t743")), True)
    report("  twin titles, same words: the one quoting the finding ranks BELOW the one that does not",
           rank(ids, "t743") < rank(ids, "t607"), True)
    report("  the answer is not title-only: every hit carries the terms it matched",
           all(len(t[3]) > 0 for t in found), True)
    offered = []

    def judge(prompt, purpose):
        offered.append(prompt)
        return "t328"

    cov, how = sr.verify_cover({"quote": Q791}, "2026-09-28", judge)
    report("verify_cover offers the cover to the judge", "t328" in offered[0], True)
    report("  and the judge's pick from the offer is the cover", cov, "t328")
    report("an answer not among the options is refused",
           sr.verify_cover({"quote": Q791}, "2026-09-28", lambda prompt, purpose: "t999")[0], "")
    report("'none' leaves it a finding",
           sr.verify_cover({"quote": Q791}, "2026-09-28", lambda prompt, purpose: "none")[0], "")
    asked = []
    report("no shared terms: the model is not asked at all",
           sr.verify_cover({"quote": "zzqx wwvy"}, "2026-09-28",
                           lambda prompt, purpose: asked.append(1) or "none"),
           ("", "no task or commit shares its terms"))
    report("  (it was not asked)", bool(asked), False)


def test_artefact_citation_outranks_a_title_match():
    ids = [t[0] for t in sr.task_candidates(Q2, limit=5)]
    report("the artefact search sees only the fake tree (hermetic)",
           set(ids) <= {"t235", "t555"}, True)
    report("a body citing the finding's files outranks a title-only word match (RCA 028b97d00e2a)",
           ids[:1], ["t235"])


def test_a_probe_naming_a_task_id_still_lifts_it():
    plain = [t[0] for t in sr.task_candidates(Q791, limit=5)]
    named = [t[0] for t in sr.task_candidates(Q791 + " Record it as t607.", limit=5)]
    report("the named probe reads the fake tree too", set(named) <= FAKE791, True)
    report("a probe that NAMES t607 lifts it in the ranking (the +10 named bonus stays live)",
           rank(named, "t607") < rank(plain, "t607"), True)


if __name__ == "__main__":
    test_cover_outranks_quoter()
    test_artefact_citation_outranks_a_title_match()
    test_a_probe_naming_a_task_id_still_lifts_it()
    print(f"\n{P} passed, {F} failed")
    sys.exit(1 if F else 0)
