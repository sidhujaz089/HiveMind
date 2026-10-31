"""t1239: cover_check left a finding deferred although the OPEN task that owns its substance
(t740, status: queued) was named by the meaning search and then vetoed.

Red on main for exactly one reason, stated at the assertion: verify_cover runs every semantic pick
through prove_cover, whose word-share floor (COVER_SHARE = 0.2) was built for FINISHED tasks. An
open task's scope is forward-looking, so it shares no words with a user's sentence, prove_cover
answers "missed", and verify_cover returns covered_by="" -- which is what left the finding "I
wonder if our coding logs need to be backed up in a similar way" (RCA d315e3535513) disposed
deferred while its owner, the open task t740, sat in the same directory as the finding's report.

Nothing live is read or touched: every task file is a fixture under a temp dir, the meaning search
is stubbed (no embeddings server, no model call, deterministic), and no subprocess is started.

Run: python3 scripts/tests/test_session_review_cover_open_scope.py
"""
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

sr = None
IMPORT_ERROR = ""
try:
    import session_review as sr                     # noqa: E402
    import session_review_trial                     # noqa: E402
except Exception as e:  # noqa: BLE001 -- a failed import is reported by every test, not hidden
    IMPORT_ERROR = f"cannot import the module under test: {type(e).__name__}: {e}"

_sem_answer = {}


def _fake_semantic(quote, as_of=""):
    """Stand-in for session_review_trial._semantic_cover: (score, tid) for the task under test,
    None for everyone else. A stub, not the real meaning search -- this suite pins what
    verify_cover DOES with its answer, not how the answer is found."""
    return _sem_answer.get("value")


if sr is not None and "session_review_trial" in sys.modules:
    session_review_trial._semantic_cover = _fake_semantic

# The real finding's words (workspace/rca/d315e3535513.md). _terms keeps the >=5-letter words;
# these are the candidate long words: coding, backed, similar, wonder, history, agents.
QUOTE = ("I wonder if our coding logs need to be backed up in a similar way, so the history of "
         "what the agents did is not lost with this box")
FINDING = {"key": "d315e3535513", "kind": "deferral", "quote": QUOTE,
           "context": "I wonder if our coding logs need to be backed up in a similar way."}

HEAD = """---
id: {tid}
title: {title}
area: workspace
kind: unfiled
status: queued
---

## Notes

{body}
"""

# The five tasks the session's cover_check actually consulted. Each text carries at least four of
# the six candidate long words, so every one clears task_candidates' two-shared-terms gate and all
# five are offered (five is also the limit, so none can be cut off the end of the list).
FILLERS = [
    ("t060", "Back up the coding logs so the agents' history survives",
     "Records that the coding logs on disk are never backed up, and wonders what a similar loss "
     "would cost."),
    ("t065", "Agent transcripts are indexed, not backed up, so history needs a copy",
     "Adds the session transcripts to the corpora so the agents' coding history is copied; "
     "wondered why not the logs as well."),
    ("t072", "Copy the coding logs to the corpus every week",
     "Copies the coding logs weekly; the agents' history is out of scope and nobody has "
     "wondered about a similar backup."),
    ("t079", "Index the agent session transcripts so a past session can be found",
     "Indexes transcripts; the agents' coding history is indexed, never backed up, which is "
     "a similar gap."),
    ("t107", "The corpus copies transcripts; nothing preserves the coding logs",
     "Notes that transcripts are taken and the coding logs are left out, so agents history "
     "stays thin; a wonder."),
]

# t740's title and body are the real open task's words, and they share NOT ONE of the six
# candidate long words above. That is the whole reason the word scan missed it, so the meaning
# search's answer is the only route to it -- a build that only widens task_candidates does not
# reach it at all.
T740 = """---
id: t740
title: Record what the backup covers: scope writer plus a check that fails on an unverified scope
area: workspace
kind: unfiled
status: queued
assigned: jas
---

## Notes

scripts/backup_corpora.py appends a machine-readable row to workspace/run-logs/backup-scope.jsonl
on every run: ts, bundle, scope_roots, excluded_roots, files, bytes, verified, verification_cmd.
Nothing today records the scope set, so \"the irreplaceable things are already covered\" is a
coverage claim with no recorder behind it.
"""

# A FINISHED task, also sharing none of the finding's words. Its acceptance must stay vetoed.
T088 = """---
id: t088
title: Close out the audit session's transcript indexing
area: workspace
kind: unfiled
status: done
---

## Notes

Transcript indexing shipped and the audit's reference lists were committed.
"""


def _build(root):
    """A task tree the test owns: five open fillers, t740 open, t088 in the finished directory."""
    tasks = pathlib.Path(root) / "workspace" / "tasks"
    (tasks / "done").mkdir(parents=True)
    (tasks / "t740--record-what-the-backup-covers-scope-writer-plus.md").write_text(
        T740, encoding="utf-8")
    for tid, title, body in FILLERS:
        (tasks / f"{tid}--cover-filler.md").write_text(
            HEAD.format(tid=tid, title=title, body=body), encoding="utf-8")
    (tasks / "done" / "t088--close-out-the-audit-session-transcript-indexing.md").write_text(
        T088, encoding="utf-8")


def _run(sem, answer):
    """verify_cover against a temp task tree with a recording fake judge. Returns
    (covered_by, how, prompts)."""
    assert not IMPORT_ERROR, IMPORT_ERROR
    prompts = []

    def judge(prompt, purpose, **kw):
        prompts.append(prompt)
        return answer

    with tempfile.TemporaryDirectory() as td:
        sr.REPO = pathlib.Path(td)
        _build(td)
        _sem_answer["value"] = sem
        try:
            covered_by, how = sr.verify_cover(dict(FINDING), "2026-08-27", judge)
        finally:
            _sem_answer.pop("value", None)
    return covered_by, how, prompts


def test_open_task_named_by_the_meaning_search_is_recorded_as_the_cover():
    """The defect. An OPEN task named by the meaning search is the cover; a word-share floor
    must not veto it. On main verify_cover returns covered_by="" -- disposition deferred."""
    covered_by, how, prompts = _run((0.61, "t740"), "t740")
    assert prompts, "verify_cover must ask the judge, not decide for it"
    assert "t740" in prompts[0], (
        "the covering open task must be ON the list the model is asked to pick from; the "
        f"options offered were:\n{prompts[0]}")
    assert "[queued]" in prompts[0], (
        "the option lines must carry each task's status, so the judge can see that t740 is "
        f"open and not finished; options were:\n{prompts[0]}")
    assert "Record what the backup covers" in prompts[0], (
        "the option lines must carry the covering task's title, not just its id")
    assert covered_by == "t740", (
        "t740 is OPEN (status: queued) and the meaning search named it, so the finding is "
        f"covered by it -- not deferred. verify_cover returned covered_by={covered_by!r} "
        f"how={how!r}")
    assert "open" in (how or "").lower(), (
        "the record the finding keeps must say WHY an unproven cover was accepted, so a later "
        f"reader can tell it from a proven one; got how={how!r}")
    assert "0.61" in (how or ""), (
        "the record must carry the meaning score the cover was accepted on, so a later reader "
        f"can see how close the match was; got how={how!r}")


def test_the_consulted_tasks_are_kept_when_the_covering_task_is_added():
    """The RCA's fix is 'keep the consulted set and ADD the covering task'. A build that narrows
    the candidate limit to make room, or drops a consulted task to fit, fails here: the list the
    model judges is the evidence, so it grows by one. Nothing may be invented into it."""
    covered_by, how, prompts = _run((0.61, "t740"), "t740")
    assert prompts, "verify_cover must ask the judge"
    for tid, _title, _body in FILLERS:
        assert f"{tid} [" in prompts[0], (
            f"the consulted task {tid} was dropped from the options; the covering task is added "
            f"to the set, not traded against it. Options were:\n{prompts[0]}")
    assert "t740" in prompts[0], f"t740 is missing from the options:\n{prompts[0]}"
    assert "t900" not in prompts[0], "the option list may not offer a task no search found"
    assert covered_by == "t740", f"covered_by={covered_by!r} how={how!r}"


def test_a_finished_task_named_by_the_meaning_search_is_still_vetoed():
    """The word-share floor stays for FINISHED tasks: a task the item's own words do not share is
    coincidence, not coverage (t663). Deleting prove_cover is not the fix, and this test is the
    guard that stops a build from buying green by deleting it."""
    covered_by, how, prompts = _run((0.55, "t088"), "t088")
    assert prompts, "verify_cover must ask the judge"
    assert "t088" in prompts[0], "a finished task is still OFFERED; only its acceptance is vetoed"
    assert covered_by == "", (
        "t088 is finished and shares none of the finding's words; accepting it as the cover is "
        f"the false COVERED the floor exists to stop. covered_by={covered_by!r} how={how!r}")
    assert "unproven" in (how or ""), (
        f"the record must name the vetoed cover so the finding shows why; got how={how!r}")


def test_a_pick_that_is_not_on_the_option_list_is_still_refused():
    """The allowlist is pre-existing behaviour (the block after the judge's answer): an answer
    naming a task no search found is refused. The fix must not widen it."""
    covered_by, how, _prompts = _run(None, "t900")
    assert covered_by == "", (
        "t900 has no task file in the tree, so it cannot be a cover; got covered_by="
        f"{covered_by!r} how={how!r}")
    assert "not among the options" in (how or ""), f"got how={how!r}"


def main():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001 -- a crash is reported, never silent
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
