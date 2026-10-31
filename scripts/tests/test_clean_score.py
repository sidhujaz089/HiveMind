#!/usr/bin/env python3
"""t116 oracle -- the scorer every three-stage-vs-two-stage number is computed from.

Red on main for one reason: projects/good-dictator-app/cleanup/clean.py has no
score_corrections. Running this file today fails at every test that needs it with the
AssertionError named below, and that message is the missing change, not a bug here.

Why the rule is pinned as a test and not in prose: clean.py:114 scores repairs with
`t.lower() in out.lower()` (clean.py:117). On the repository's own ground truth that
reports a repair that never happened. clean.py:82 gives the second sample
traps ["release notes", "passed"] and the raw dictation at clean.py:78-79 already contains
the substring "release notes", so today's score() returns (1, 2) on text no model ever
touched -- verified by running it. And "the" is a substring of "they'll", so the task's own
real pair ("they'll", "the") scores as repaired in a sentence no stage edited. A substring
scorer makes the local cleanup stage look helpful, which is exactly the direction t116 is
testing, so the rule is pinned here rather than left to prose.

The pairs are taken from clean.py's own SAMPLES (clean.py:66-83) -- Jas's own corrections,
sourced from workspace/app-feedback/ per the comment at clean.py:64-65 -- not invented ones.
No model calls, no server, nothing written to disk.
"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLEAN = REPO / "projects" / "good-dictator-app" / "cleanup" / "clean.py"


def load():
    spec = importlib.util.spec_from_file_location("gd_clean", CLEAN)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load " + str(CLEAN))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def score(raw, out, pairs):
    """Call the scorer under test. Raises with the missing change named, not silently."""
    mod = load()
    fn = getattr(mod, "score_corrections", None)
    if fn is None:
        raise AssertionError("clean.py has no score_corrections() -- the t116 change adds it")
    return fn(raw, out, pairs)


def test_exposes_scorer():
    assert hasattr(load(), "score_corrections"), \
        "clean.py must expose score_corrections(raw, out, pairs)"


def test_the_two_real_corrections_from_the_corpus_are_repaired():
    # SAMPLES[1]: raw says "does not show they'll release notes ... on the checklist past",
    # Jas's own text says "... does not show the release notes ... on the checklist passed".
    s = load().SAMPLES[1]
    pairs = [("past", "passed"), ("they'll", "the")]
    assert score(s["raw"], s["his"], pairs) == {
        "repaired": pairs, "missed": [], "absent": []}, pairs


def test_the_other_real_pair_is_repaired_in_sample_one():
    # SAMPLES[0]: raw says "when i press send a wall send off", Jas wrote "it will send off".
    s = load().SAMPLES[0]
    assert score(s["raw"], s["his"], [("wall", "will")]) == {
        "repaired": [("wall", "will")], "missed": [], "absent": []}


def test_a_pair_is_repaired_only_when_the_bad_token_is_gone():
    # "passed" contains "past" as a substring, AND this output still contains the whole word
    # "past" as well -- so the correction was NOT applied. A substring scorer calls it repaired.
    got = score("I past the test", "I passed the test and past tense is fine", [("past", "passed")])
    assert got == {"repaired": [], "missed": [("past", "passed")], "absent": []}, got


def test_substring_is_not_a_repair():
    # "the" is a substring of "they'll"; the bad token is the only whole word present, so this
    # is a miss. Verified: "the" in "they'll be there" is True -- an in-based scorer says repaired.
    got = score("they'll be there", "they'll be there", [("they'll", "the")])
    assert got == {"repaired": [], "missed": [("they'll", "the")], "absent": []}, got


def test_a_pair_is_absent_where_the_raw_text_has_no_bad_token():
    # The same real pair, in the sample where it is not evidence of anything: sample 1's raw
    # text has no "wall", so the pair can only be absent -- not repaired, not dropped.
    s = load().SAMPLES
    assert score(s[0]["raw"], s[0]["his"], [("wall", "will")]) == {
        "repaired": [("wall", "will")], "missed": [], "absent": []}
    assert score(s[1]["raw"], s[1]["his"], [("wall", "will")]) == {
        "repaired": [], "missed": [], "absent": [("wall", "will")]}


def test_direction_of_a_pair_matters():
    # Reversing the real pair: the raw text has 'past', not 'passed', so ('passed', 'past')
    # is evidence of nothing. A scorer that matches either side reports repaired.
    s = load().SAMPLES[1]
    assert score(s["raw"], s["his"], [("passed", "past")]) == {
        "repaired": [], "missed": [], "absent": [("passed", "past")]}


def test_empty_pairs_is_three_empty_lists():
    assert score("anything", "anything", []) == {
        "repaired": [], "missed": [], "absent": []}


def test_buckets_are_lists_of_pairs_not_counts():
    # A count loses WHICH correction was repaired, and t116 compares pipelines per pair.
    s = load().SAMPLES[1]
    got = score(s["raw"], s["his"], [("past", "passed")])
    assert isinstance(got["repaired"], list), \
        "repaired must list WHICH pairs, not a count: " + str(type(got["repaired"]))
    assert got["repaired"] == [("past", "passed")], got
    assert isinstance(got["missed"], list) and isinstance(got["absent"], list), got


def test_existing_substring_score_still_works():
    # clean.py:132 -- cmd_samples() calls score(out, s["traps"]) for `clean.py --samples`.
    # The new function ADDS a path; it must not break the one that is running today.
    mod = load()
    # score() returns (how many traps appear, how many traps there are) -- clean.py:133.
    assert mod.score("then when i press send it will send off and check the checklist",
                     ["it will send", "checklist"]) == (2, 2)
    assert mod.score("nothing here at all", ["checklist"]) == (0, 1)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("ok   " + fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL " + fn.__name__ + ": " + type(e).__name__ + ": " + str(e))
    if failed:
        print(str(failed) + " of " + str(len(fns)) + " failed")
        raise SystemExit(1)
    print("all " + str(len(fns)) + " passed")
