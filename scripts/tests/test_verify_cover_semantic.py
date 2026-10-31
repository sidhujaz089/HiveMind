#!/usr/bin/env python3
"""t786 -- verify_cover() must offer the semantically closest task, not only word-matched ones.

The cover stage in scripts/session_review.py (def verify_cover, line 583) asks the judge to choose
from what CODE found: task_candidates() offers a task only when its file shares two of the quote's
_terms() (line 520) or one identifier-shaped term. A prose plan -- "free roam, no itinerary, 150
rounds" -- shares no word with the task that prevents its class, so it reaches that task by luck.
scripts/session_review_trial.py's _semantic_cover() is the meaning search; it is wired into
scripted_items (line 705-713) for the scripted path and NEVER into verify_cover.

This suite pins the wiring, with the embeddings server replaced by a scripted seam (an oracle makes
no model call and starts no process): the meaning search's answer must reach the judge's option
list, prove_cover() must keep its veto over it, a task already offered by words must not be offered
twice, a meaning search that raises must be printed and must not break the word search, and nothing
found must still cost no model call.

No model, no server, no network, no git, and no writes outside a temp dir.
"""
import contextlib
import importlib.util
import io
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


try:
    sr = _load("session_review", "scripts/session_review.py")
    _sc_mod = _load("session_candidates", "scripts/session_candidates.py")
except Exception as e:                                       # a suite bug, not the defect
    sys.exit("ORACLE BUG: cannot import the modules under test: " + f"{type(e).__name__}: {e}")

# prove_cover() reaches session_candidates through sr._sc(), which imports it by BARE name from
# REPO/scripts -- and REPO is redirected to a temp dir below, where the module is not. Hand it the
# module loaded from its real path, so the oracle does not depend on sys.path.
sr._sc = lambda: _sc_mod
# The meaning search needs an embeddings server, which no oracle may start. Pre-seed sys.modules
# with a stub module: the code under test does `import session_review_trial as _trial` (as it
# already does at line 706) and Python answers from sys.modules. This pins the wiring, not vectors.
_stub = types.ModuleType("session_review_trial")
sys.modules["session_review_trial"] = _stub
_stub._semantic_cover = lambda quote, as_of="": None
# commit_candidates runs `git log -S` in REPO; a temp dir is not a repo and an oracle starts no
# process. The lexical commit path is exercised by the existing suites.
sr.commit_candidates = lambda quote, since, limit=3: []

# Three fixture tasks with DISTINCT titles and bodies, so a build cannot satisfy every assertion by
# echoing one placeholder. The t900 text deliberately shares NO word of Q1 -- that is the defect.
T900_TITLE = "Unscripted mission envelope for the probe"
T900_BODY = ("The probe wanders unscripted with no fixed schedule, capped at one hundred and "
             "fifty laps, with the existing guardrails left in place.")
T901_TITLE = "Nightly NAS backup rotation"
T901_BODY = "Prune bundles older than seven days and report the rotation as a ledger row."
T902_TITLE = "Prune the nightly NAS backup bundles"
T902_BODY = "Rotate the nightly NAS backups, prune bundles older than seven days, keep a count."

Q1 = "Plan as agreed: free roam, no itinerary, 150 rounds, same bounds."
# WHAT1's words all appear in T900_BODY/T900_TITLE, so prove_cover's topic-word share is high for
# any stop-word list; Q1's own words (agreed, itinerary, rounds, bounds) appear nowhere in it.
WHAT1 = ("let the probe roam unscripted, with no fixed schedule, capped at one hundred and "
         "fifty laps inside the existing mission envelope")
Q3 = "Rotate the nightly NAS backups and prune the bundles older than seven days."
Q5 = "Note the xylophone quantum trellis for the quarterly review."


def _tasks_dir(root, tasks):
    d = root / "workspace" / "tasks"
    d.mkdir(parents=True)
    for tid, (title, body) in tasks.items():
        (d / (tid + "--fixture.md")).write_text(
            "---\nid: " + tid + "\ntitle: " + title + "\nstatus: open\n"
            "created: 2026-10-01T000000Z\n---\n\n" + body + "\n", encoding="utf-8")
    return d


@contextlib.contextmanager
def fixture(tasks, returns=None, raises=None):
    """A temp repo of real task files + a scripted meaning search. Yields the list of probes the
    meaning search was given. sr.REPO, sr._sc and sr.commit_candidates are restored on exit."""
    asked = []

    def _semantic(quote, as_of=""):
        asked.append(quote)
        if raises is not None:
            raise raises
        return returns

    _stub._semantic_cover = _semantic
    old = (sr.REPO, sr._sc, sr.commit_candidates)
    try:
        with tempfile.TemporaryDirectory(prefix="t786-") as td:
            root = Path(td) / "repo"
            _tasks_dir(root, tasks)
            sr.REPO = root
            sr._sc = lambda: _sc_mod
            sr.commit_candidates = lambda quote, since, limit=3: []
            yield asked
    finally:
        sr.REPO, sr._sc, sr.commit_candidates = old
        _stub._semantic_cover = lambda quote, as_of="": None


def test_a_semantic_answer_reaches_the_judge_and_becomes_the_cover():
    """THE DEFECT. A quote sharing no word with its covering task must still reach the judge."""
    seen = []

    def judge(prompt, purpose, **kw):
        seen.append(prompt)
        return "t900"

    with fixture({"t900": (T900_TITLE, T900_BODY)}, returns=(0.83, "t900")) as asked:
        row = {"quote": Q1, "context": "the plan for the next session", "what": WHAT1}
        got = sr.verify_cover(row, "2026-10-08", judge)
    assert asked, "the meaning search was never consulted (verify_cover has no semantic path)"
    assert Q1 in asked[0], f"the meaning search was not given the quote: {asked[0]!r}"
    assert seen, "the judge was never asked: nothing was offered to it"
    assert "t900" in seen[0], f"t900 was not put in front of the judge: {seen[0]!r}"
    assert T900_TITLE in seen[0], f"the option showed the id but no title: {seen[0]!r}"
    assert got[0] == "t900", f"the judge's answer was not returned as the cover: {got!r}"


def test_b_meaning_alone_does_not_beat_prove_cover():
    """NEGATIVE. The closest task by meaning, sharing no topic word, must NOT be reported as covered."""
    seen = []

    def judge(prompt, purpose, **kw):
        seen.append(prompt)
        return "t901"

    with fixture({"t901": (T901_TITLE, T901_BODY)}, returns=(0.90, "t901")) as asked:
        row = {"quote": "The sandbox is stale, so refresh it from the current tree before launching.",
               "context": "the environment is stale", "what": "re-clone the environment when it is stale"}
        got = sr.verify_cover(row, "2026-10-08", judge)
    assert asked, "the meaning search was never consulted"
    assert seen and "t901" in seen[0], f"the meaning search's answer must be offered to the judge: {seen!r}"
    assert got[0] == "", f"a cover prove_cover() cannot support must not be returned: {got!r}"
    assert "t901" in (got[1] or ""), f"the reason must name the cover it vetoed: {got!r}"


def test_c_a_task_offered_by_words_is_not_offered_twice():
    """NEGATIVE. Q3 shares five terms with t902, so t902 is offered lexically; the meaning search
    naming the same id must not list it a second time (a build that appends unconditionally fails)."""
    seen = []

    def judge(prompt, purpose, **kw):
        seen.append(prompt)
        return "t902"

    with fixture({"t902": (T902_TITLE, T902_BODY)}, returns=(0.70, "t902")) as asked:
        got = sr.verify_cover({"quote": Q3, "context": "", "what": ""}, "2026-10-08", judge)
    assert got[0] == "t902", f"a lexically-found cover must still be honoured: {got!r}"
    assert seen, "the judge was never asked"
    assert seen[0].count("t902") == 1, f"t902 was listed more than once: {seen[0]!r}"


def test_d_a_failing_meaning_search_is_recorded_not_swallowed():
    """The seam is best-effort: it must not break the word search, and it must SAY it failed."""
    seen = []

    def judge(prompt, purpose, **kw):
        seen.append(prompt)
        return "t902"

    err = io.StringIO()
    with fixture({"t902": (T902_TITLE, T902_BODY)}, raises=RuntimeError("no embeddings server")) as asked:
        with contextlib.redirect_stderr(err):
            got = sr.verify_cover({"quote": Q3, "context": "", "what": ""}, "2026-10-08", judge)
    assert asked, "the meaning search was never consulted"
    assert got[0] == "t902", f"a failing meaning search must not break the word search: {got!r}"
    text = err.getvalue()
    assert "RuntimeError" in text, f"the exception must be printed, not swallowed silently: {text!r}"
    assert "meaning" in text.lower() or "semantic" in text.lower(), f"say which search failed: {text!r}"


def test_e_nothing_found_still_costs_no_model_call():
    """PRESERVE: no lexical hit, no meaning hit -> no judge call, and the reason is unchanged."""
    seen = []

    def judge(prompt, purpose, **kw):
        seen.append(prompt)
        return "none"

    with fixture({"t900": (T900_TITLE, T900_BODY)}, returns=None) as asked:
        got = sr.verify_cover({"quote": Q5, "context": "", "what": ""}, "2026-10-08", judge)
    assert asked, "the meaning search must be consulted for every quote, not only when words fail"
    assert seen == [], f"nothing was offered, so the judge must not be asked: {seen!r}"
    assert got[0] == "", f"expected no cover: {got!r}"
    assert "no task or commit" in (got[1] or ""), f"the no-cover reason must not change: {got!r}"


def main():
    tests = [test_a_semantic_answer_reaches_the_judge_and_becomes_the_cover,
             test_b_meaning_alone_does_not_beat_prove_cover,
             test_c_a_task_offered_by_words_is_not_offered_twice,
             test_d_a_failing_meaning_search_is_recorded_not_swallowed,
             test_e_nothing_found_still_costs_no_model_call]
    bad = []
    for fn in tests:
        try:
            fn()
            print("PASS  " + fn.__name__)
        except AssertionError as e:
            bad.append(fn.__name__)
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:                                # noqa: BLE001
            bad.append(fn.__name__)
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"{len(tests) - len(bad)}/{len(tests)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
