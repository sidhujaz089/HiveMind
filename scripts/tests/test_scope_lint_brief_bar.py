#!/usr/bin/env python3
"""t756: a brief whose own worked example cannot clear the numeric bar it states is caught
upstream, by scope_lint (class brief-wrong).

The real defect (t719, model swift, 2026-10-07): an implementation brief set a topic-share bar of
0.5 for a resolved-in-session filter in the same prose field that quotes its motivating example --
a settlement that "shares 3 of 7 topic words (0.43)". Built verbatim over 19 audit sessions the
filter moved the MISSED count from 576 to 572, and the example cannot clear the bar: 3/7 is 0.429.
Nothing in the pipeline compared the two numbers; a human reading the brief after the build was
the only catch.

RED on main: scope_lint has no bar_vs_example, so the four unit cases fail with AttributeError and
the two end-to-end cases fail because lint reports no brief-wrong finding. GREEN once static()
calls the new function: the t719 text is flagged with an evidence line carrying both numbers, and
a brief whose example clears its bar is not flagged.

The fixture text is copied from the real artefact: workspace/run-logs/scoping-queue/out/t719.md
lines 198 and 199, the implementation brief's single_change and what_is_wrong. Quote style around
dict keys is normalised to single quotes -- the check reads digits, operators and fractions, not
quote style. No subprocess, no urlopen, no model call, no live server, nothing left running, no
fixed /tmp path, no write outside a tempfile directory: sl.lint is called with execute=False.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import scope_lint as sl  # noqa: E402

FAILS = []

# Verbatim (minus quote style) from the real defect's implementation brief. The two numbers are
# the defect: 3/7 = 0.429, and the bar the same brief sets for the same quantity is 0.5.
T719_BAR = ("at least ONE sentence of sc.sentences(txt) satisfies BOTH DONE_WORDS.search(sentence) "
            "AND len(topic_words(row['quote']) & topic_words(sentence)) / "
            "max(1, len(topic_words(row['quote']))) >= 0.5")
T719_EXAMPLE = ("so a promise the session performs in other words -- quote 'I'll add the NAS prune "
                "to the nightly sweep later, once the sweep lands.' settled by 'Filed as t687 -- "
                "the NAS prune is in the nightly sweep now.' -- shares 3 of 7 topic words (0.43), "
                "is not 'mentioned', and is surfaced as a MISSED flag")
CLEARED = T719_EXAMPLE.replace("3 of 7 topic words (0.43)", "4 of 7 topic words (0.57)")

FIXTURE_ORACLE = "import sys\nassert hasattr(sys, 'argv'), 'fixture'\n"


def block_for(prose):
    return ("answer\n```chunks\n" + json.dumps([
        {"kind": "oracle", "file": "scripts/tests/test_mod.py", "oracle_text": FIXTURE_ORACLE,
         "red_because": "fixture inside the lint's own fixture"},
        {"kind": "implementation", "file": "lib/mod.py", "target": "scripts/tests/test_mod.py",
         "single_change": prose,
         "what_is_wrong": "the filter's stated bar excludes the example that motivated it",
         "done_when": "python3 scripts/tests/test_mod.py exits 0",
         "related_entries": ["brief-wrong"]}]) + "\n```\n")


def repo_with_mod(tmp):
    repo = Path(tmp) / "repo"
    (repo / "lib").mkdir(parents=True)
    (repo / "scripts" / "tests").mkdir(parents=True)
    (repo / "lib" / "mod.py").write_text("def g():\n    return 1\n")
    return repo


def flagged(repo, prose):
    return sorted(ev for cls, _i, _f, ev in sl.lint(block_for(prose), repo, execute=False)[1]
                  if cls == "brief-wrong")


def test_the_t719_brief_text_is_flagged():
    ev = sl.bar_vs_example(T719_BAR + " " + T719_EXAMPLE)
    assert ev, ("the t719 brief text must be flagged: it states a bar of 0.5 for a share and "
                "quotes a worked example of 3 of 7 (0.43), which cannot clear it")
    assert "0.43" in ev, "the finding must carry the example's value, got %r" % (ev,)
    assert "0.5" in ev, "the finding must carry the bar the brief states, got %r" % (ev,)
    assert "3 of 7" in ev, "the finding must quote the example as the brief wrote it, got %r" % (ev,)


def test_a_brief_whose_example_clears_its_bar_is_clean():
    ev = sl.bar_vs_example(T719_BAR + " " + CLEARED)
    assert ev == "", (
        "4 of 7 = 0.571 clears a bar of 0.5: a brief whose numbers reconcile must pass, got %r"
        % (ev,))


def test_a_decimal_that_is_not_a_share_bar_does_not_fire():
    noise = ("The host is 127.0.0.1:11434 and the fallback is 11435; the output cap is 20000 "
             "tokens; 4 of 64 gates passed today.")
    ev = sl.bar_vs_example(noise)
    assert ev == "", (
        "an IP, a port, a token cap and a bare count are not a share bar: 4/64 = 0.0625 must not "
        "be measured against them, got %r" % (ev,))


def test_a_count_measured_against_a_share_bar_is_flagged():
    ev = sl.bar_vs_example("the gate requires >= 0.5 accuracy on the labelled set; today it "
                           "scores 14 of 64.")
    assert ev, "14 of 64 = 0.219 cannot clear a bar of 0.5 stated in the same brief"
    assert "0.219" in ev, "the finding must state the computed ratio, got %r" % (ev,)


def test_the_lint_flags_the_real_brief_end_to_end(tmp):
    found = flagged(repo_with_mod(tmp), T719_BAR + " " + T719_EXAMPLE)
    assert len(found) == 1, (
        "the t719 brief must produce exactly one brief-wrong finding, got %r" % (found,))
    assert "0.43" in found[0] and "0.5" in found[0], (
        "the finding the bridge records must carry both numbers, got %r" % (found[0],))


def test_the_lint_passes_a_brief_whose_numbers_reconcile(tmp):
    found = flagged(repo_with_mod(tmp), T719_BAR + " " + CLEARED)
    assert found == [], "a brief whose numbers reconcile must not be flagged, got %r" % (found,)


def _run_case(name, fn, needs_tmp):
    tmp = tempfile.mkdtemp(prefix="t756-bar-")
    try:
        fn(tmp) if needs_tmp else fn()
        print("PASS", name)
    except Exception as e:  # noqa: BLE001 -- a red case is a finding: report the type and message
        print("FAIL %s: %s: %s" % (name, type(e).__name__, e))
        FAILS.append(name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _run_case("t719-brief-text-is-flagged", test_the_t719_brief_text_is_flagged, False)
    _run_case("example-clearing-its-bar-is-clean",
              test_a_brief_whose_example_clears_its_bar_is_clean, False)
    _run_case("non-bar-decimals-do-not-fire", test_a_decimal_that_is_not_a_share_bar_does_not_fire, False)
    _run_case("count-against-a-bar-is-flagged", test_a_count_measured_against_a_share_bar_is_flagged, False)
    _run_case("lint-flags-the-real-brief-end-to-end", test_the_lint_flags_the_real_brief_end_to_end, True)
    _run_case("lint-passes-a-reconciled-brief", test_the_lint_passes_a_brief_whose_numbers_reconcile, True)
    if FAILS:
        print("t756 brief-bar-vs-example oracle: %d failed: %s" % (len(FAILS), FAILS))
        sys.exit(1)
    print("t756 brief-bar-vs-example oracle: all passed")
    sys.exit(0)
