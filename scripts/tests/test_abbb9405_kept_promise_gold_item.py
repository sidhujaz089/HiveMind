#!/usr/bin/env python3
"""t1396: the abbb9405 audit gold set must carry the kept promise, so a run that files it as a
deferral scores a MISS.

Red on main: workspace/session-reviews/trials/audit/s17.json has exactly three gold items (O1 at
:4-12, O2 at :13-21, O3 at :22-30) and no kept-promise entry, so session_review_trial.grade()
(:206, which iterates gold["items"]) never produces a row for it and a run that mints this exact
false deferral scores clean. rca b05f4aa268dd.md:55 (row C9) records the same for both trials.

No pytest, no model, no writes: reads the real gold file and calls the real scorer
(session_review_trial.grade / .matches, scripts/session_review_trial.py:199-214).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review_trial as trial

GOLD = ROOT / "workspace" / "session-reviews" / "trials" / "audit" / "s17.json"
GOLD_ID = "P1"

# The finding's own sentence, verbatim (rca b05f4aa268dd.md:1, :21 -- transcript seq 17).
KEPT_QUOTE = ("I'll start by locating the DSH session records, since that's where the crash "
              "happened, and check the environment facts.")
# A sentence carrying O1's real anchor (s17.json:8): the genuine miss that must stay right.
OPEN_QUOTE = "The harness compacts *before* the window is full on every lane."


def _gold():
    return json.loads(GOLD.read_text(encoding="utf-8"))


def _item(gold, gid):
    return next((g for g in gold["items"] if g.get("id") == gid), None)


def test_gold_file_is_the_abbb9405_one():
    gold = _gold()
    assert gold.get("session") == "dsh-abbb9405", f"session is {gold.get('session')!r}"
    ids = [x.get("id") for x in gold["items"]]
    assert len(ids) == len(set(ids)), f"duplicate gold ids: {ids}"


def test_kept_promise_has_a_gold_item():
    gold = _gold()
    g = _item(gold, GOLD_ID)
    assert g is not None, (
        f"no gold item {GOLD_ID} in {GOLD.relative_to(ROOT)}: item n=1 (the kept promise) has no "
        f"gold entry, so grade() never scores it and a false deferral scores clean")
    assert g.get("missed") == "no", (
        f"gold {GOLD_ID} must be missed='no' -- the promise was discharged in session; got "
        f"{g.get('missed')!r}")
    assert isinstance(g.get("anchors"), list) and g["anchors"], f"gold {GOLD_ID} needs anchors"
    assert trial.matches(g, KEPT_QUOTE), (
        f"gold {GOLD_ID} anchors {g['anchors']!r} do not match the finding's own sentence (anchors "
        f"are case-insensitive substrings, session_review_trial.py:199-201)")


def test_a_deferral_on_the_kept_promise_scores_a_miss():
    gold = _gold()
    rows = [{"quote": KEPT_QUOTE, "verdict": "missed", "cover": ""}]
    entry = next((x for x in trial.grade(gold, rows) if x["id"] == GOLD_ID), None)
    assert entry is not None, f"grade() returned no row for gold {GOLD_ID}"
    assert entry["found"] is True, (
        f"the kept promise was not matched by a row quoting it (found={entry['found']!r}): its "
        f"anchors do not match the finding's own sentence")
    assert entry["right"] is False, (
        f"a deferral filed against a kept promise scored right={entry['right']!r}; it must be a miss")


def test_a_genuine_miss_is_still_right():
    """NEGATIVE assertion: the fix must not become 'every deferral on a gold item is a miss'.
    O1 -- a real open item, missed: "yes" (s17.json:10) -- said MISSED must still score right, and
    it must be the ONLY item right for these two rows."""
    gold = _gold()
    o1 = _item(gold, "O1")
    assert o1 is not None, "O1 is missing from s17.json"
    assert trial.matches(o1, OPEN_QUOTE), f"O1 anchors {o1['anchors']!r} do not match the fixture line"
    rows = [{"quote": KEPT_QUOTE, "verdict": "missed", "cover": ""},
            {"quote": OPEN_QUOTE, "verdict": "missed", "cover": ""}]
    gr = trial.grade(gold, rows)
    assert len(gr) == len(gold["items"]), f"grade() graded {len(gr)} of {len(gold['items'])} gold items"
    e1 = next((x for x in gr if x["id"] == "O1"), None)
    assert e1 is not None and e1["found"] is True and e1["right"] is True, (
        f"a genuine miss on a real open item (O1) must score right; got {e1!r} -- the rule was "
        f"widened past the gold-discharged item")
    assert sum(1 for x in gr if x["right"]) == 1, (
        f"expected exactly one item right (O1) for these two rows; "
        f"got {sum(1 for x in gr if x['right'])}")


def test_the_assertions_above_can_fail():
    """THE DECAY PROOF (t3578). A suite that has only ever passed cannot be told from one that
    cannot fail (docs/CHECKS.md rule 1), and check_workspace named this one under "Every test suite
    proves a failure". Two breaks, each of which must move a verdict an assertion above insists on:

      1. strip O1's anchors -> the quote this suite says O1 matches no longer matches
      2. flip O1's "missed" from yes to no -> its genuine miss stops scoring right

    Both are built on O1, the open item that IS in the gold set, and not on the kept-promise item:
    P1 is what t1396 exists to add and is not there yet, so a decay proof resting on it would have
    no row to break -- which is exactly how this suite came to have an unprovable assertion.

    (1) is asserted on matches() rather than on grade()["found"] on purpose: grade() also scores on
    shared TOPIC words (t2112), so a worded-close row can still be attributed after the anchors go.
    The anchor match is what line 80 pins, so that is what the break must break.
    """
    o1 = _item(_gold(), "O1")
    assert o1 is not None, "O1 is missing from s17.json"
    assert trial.matches(dict(o1, anchors=[]), OPEN_QUOTE) is False, (
        "an item with NO anchors still matched its own quote, so the match assertion above is a "
        "tautology that can never fail")

    unfiled = json.loads(json.dumps(_gold()))
    _item(unfiled, "O1")["missed"] = "no"
    e1 = next((x for x in trial.grade(unfiled, [{"quote": OPEN_QUOTE, "verdict": "missed",
                                                 "cover": ""}]) if x["id"] == "O1"), None)
    assert e1 is not None and e1["found"] is True and e1["right"] is False, (
        f"with 'missed' flipped to no, a row calling O1 missed was still graded right ({e1!r}): "
        f"the 'right is True' above does not depend on the gold item saying it was missed")


def main():
    tests = [test_gold_file_is_the_abbb9405_one, test_kept_promise_has_a_gold_item,
             test_a_deferral_on_the_kept_promise_scores_a_miss, test_a_genuine_miss_is_still_right,
             test_the_assertions_above_can_fail]
    bad = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            bad += 1
            # "FAIL" as its own argument, not inside an f-string: check_workspace's decay check
            # looks for the literal token and could not see the f-string form (t3578).
            print("FAIL", fn.__name__, ":", e)
    print(f"{len(tests) - bad}/{len(tests)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
