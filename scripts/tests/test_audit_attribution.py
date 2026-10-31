#!/usr/bin/env python3
"""Fail-first oracle for t2112(b): the audit must not report "the review missed it" when it means
"the review worded it differently".

MEASURED 2026-10-25 on the s22 gold session (353 turns, 56 gold items, 43 review rows):
`grade()` matched a gold item to a row ONLY when one of the item's anchors -- a single hand-picked
sentence -- appeared verbatim in the row. Result: 46 of 56 "not found", of which 41 shared subject
with a row that ALREADY said `verdict: missed`. And one row ("When the change comes back, I'll test it
against that wider scope before merging.") was the first match for TWO different gold items, so a
single row answered two items.

Both errors are in the instrument, and both make the review look worse than it is:
  - a false negative, because it found the thing and worded it differently;
  - a double credit, because the same row is spent twice.

So `grade()` attributes by three bases in this order: `anchor` (a sentence matched, strongest),
`topic` (enough shared distinctive words -- weaker, and reported as such), or nothing. Each row is
spent at most ONCE, best score first. The caller can then report how much of the number rests on the
strong basis and how much on the weak one -- which is the difference between a measurement and an
alibi.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "app-repo"))

import session_review_trial as trial  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def gold_item(iid, anchor, about, missed="yes"):
    return {"id": iid, "about": about, "anchors": [anchor], "missed": missed, "cover": ""}


def row(quote, verdict="missed"):
    return {"quote": quote, "context": "", "verdict": verdict}


def by_id(graded):
    return {g["id"]: g for g in graded}


def main():
    # 1. THE ANCHOR STILL WINS when it is present: unchanged behaviour for a clean match.
    g = {"items": [gold_item("M1", "the dispatcher was not restarted", "deferral: restart")]}
    r = by_id(trial.grade(g, [row("note: the dispatcher was not restarted before the merge")]))
    check("an anchor match is still found", r["M1"]["found"], True)
    check("  and is attributed to the strong basis", r["M1"]["basis"], "anchor")

    # 2. THE FALSE NEGATIVE THAT STARTED THIS: the review found it, worded differently.
    g = {"items": [gold_item("M1", "Not checked live: I haven't watched it in production.",
                             "deferral: live observation was deferred to after the restart")]}
    # two distinctive words in common ("deferral", "verification") is what the real s22 rows carry
    r = by_id(trial.grade(g, [row("the deferral stands: verification of the restart comes back with the change",
                                  "missed")]))
    check("a differently-worded finding is now attributed", r["M1"]["found"], True)
    check("  but on the WEAK basis, so the report can say so", r["M1"]["basis"], "topic")
    check("  and the verdict it carries is the review's", r["M1"]["said"], "missed")

    # 3. THE DOUBLE CREDIT: one row must never answer two gold items.
    g = {"items": [gold_item("M2", "I haven't watched a live tick yet.", "deferral: verification"),
                   gold_item("M3", "Not checked live at all.", "deferral: verification of the fix")]}
    one = row("the deferral on verification stands until the change comes back")
    r = by_id(trial.grade(g, [one]))
    check("one row cannot be spent on two items", sum(1 for x in r.values() if x["found"]), 1)

    # 4. A genuine miss stays a miss: no shared subject, no row.
    g = {"items": [gold_item("M9", "the backup bundle was never verified", "deferral: backup")]}
    r = by_id(trial.grade(g, [row("the favicon was blue", "missed")]))
    check("an unrelated row is not attributed", r["M9"]["found"], False)
    check("  and its basis is none", r["M9"]["basis"], None)

    # 5. `right` still means the verdict agrees with the gold label, on either basis.
    g = {"items": [gold_item("M4", "nothing was pushed", "deferral: pushing", missed="yes")]}
    r = by_id(trial.grade(g, [row("deferral noted: it is committed and nothing was pushed", "missed")]))
    check("an agreed verdict counts as right", r["M4"]["right"], True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
