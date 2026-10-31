"""t1312 -- a COVERED verdict must be proved by code, whichever way its id was chosen.

AUDIT-Q4-GRADE-2026-10-05.md: Q4 called 5 of the 9 real misses it kept COVERED and named a
wrong cover every time (t328, t206, t243, d04048e2). A model's COVERED is an assertion.
scripts/session_review.py:758 `prove_cover` is the check that turns it into a fact, but
verify_cover (scripts/session_review.py:724) runs it only for a SEMANTIC pick:
`if m.group(1) and pick not in by_words:` -- so a task id that reached the option list
through the word scan is returned accepted with no check. These tests pin the missing case:
the proof must run for every task-id cover, and a cover whose task text really does share
the item's words must still be accepted (so "reject everything" is not a passing build).

No disk, no /tmp, no subprocess, no model, no live server: everything verify_cover looks up
is patched, and every patch is restored.
"""
import sys
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import session_review as sr           # noqa: E402
import session_review_trial as trial  # noqa: E402

# Words used only here, so topic_words() (unread) cannot matter: the "far" task shares NONE of
# them (share 0.0 -> unproven), the "near" task shares ALL of them (share 1.0 -> proven).
QUOTE = "the zqx wivnel must glint the plinth ledger before it writes a row"
WHAT = "glint the plinth ledger"
FAR = "flange gasket torque bearing shim"
NEAR = "title: glint the zqx wivnel plinth ledger\n"
FAR_ID, NEAR_ID = "t931", "t932"

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}: {label}\n        got={got!r}")
    if not ok:
        FAILURES.append(label)


@contextmanager
def fixtures(pick, texts, word_matched=True):
    """Replace every lookup verify_cover makes. `word_matched` puts the pick in the set
    built by task_candidates (by_words, scripts/session_review.py:698) -- the path that
    today skips prove_cover."""
    ids = [(pick, "open", "an unrelated widget", ["zqx"])] if word_matched else []
    old_tc, old_tt, old_cc, old_sem = sr.task_candidates, sr.task_text, sr.commit_candidates, trial._semantic_cover
    sr.task_candidates = lambda q, limit=5: list(ids)
    sr.task_text = lambda tid: texts.get(tid, "")
    sr.commit_candidates = lambda q, since, limit=3: []      # no git, no subprocess
    trial._semantic_cover = lambda q, as_of="": None         # no embeddings server
    try:
        yield
    finally:
        sr.task_candidates, sr.task_text, sr.commit_candidates = old_tc, old_tt, old_cc
        trial._semantic_cover = old_sem


def test_a_word_matched_cover_is_checked_by_code():
    """The case the audit died on: a task the quote shares words with, whose body is not the work."""
    with fixtures(FAR_ID, {FAR_ID: FAR}):
        got = sr.verify_cover({"quote": QUOTE, "context": "", "what": WHAT, "kind": "unresolved"},
                              "2026-10-05", lambda p, purpose: FAR_ID)
    check("an unproven word-matched cover returns NO cover id", got[0], "")
    check("and says it was unproven, so the ledger can see why",
          "unproven" in (got[1] or "").lower(), True)


def test_a_proven_cover_is_still_accepted():
    """Guards against satisfying the fix by rejecting every cover: real overlap still counts."""
    with fixtures(NEAR_ID, {NEAR_ID: NEAR}):
        got = sr.verify_cover({"quote": QUOTE, "context": "", "what": WHAT, "kind": "unresolved"},
                              "2026-10-05", lambda p, purpose: NEAR_ID)
    check("a cover whose task text shares the item's words is kept", got[0], NEAR_ID)


def test_a_semantic_cover_is_still_checked():
    """The path that already runs prove_cover must not regress: not word-matched, no overlap."""
    with fixtures(FAR_ID, {FAR_ID: FAR}, word_matched=False):
        got = sr.verify_cover({"quote": QUOTE, "context": "", "what": WHAT, "kind": "unresolved"},
                              "2026-10-05", lambda p, purpose: FAR_ID)
    check("an unproven semantic cover returns no cover id", got[0], "")


def test_a_rejected_cover_keeps_the_models_claim_on_the_row():
    """prove_cover keeps the model's id on the row (session_review.py:778) -- the record the
    per-verdict ledger (this RCA's item 3) writes. Flipping the verdict must not erase it."""
    with fixtures(FAR_ID, {FAR_ID: FAR}):
        r = sr.prove_cover({"verdict": "covered", "cover": FAR_ID, "quote": QUOTE, "what": WHAT})
    check("an unproven cover is not left as covered", r.get("verdict"), "missed")
    check("the model's original claim is recorded", r.get("model_cover"), FAR_ID)
    check("and the reason is recorded", "unproven" in str(r.get("overruled", "")).lower(), True)


def test_a_cover_the_model_never_named_is_not_invented():
    """An empty cover must not be filled in: 'covered' with no id is not a verified verdict."""
    with fixtures(FAR_ID, {FAR_ID: NEAR}):
        got = sr.verify_cover({"quote": QUOTE, "context": "", "what": WHAT, "kind": "unresolved"},
                              "2026-10-05", lambda p, purpose: "none")
    check("a 'none' answer leaves the finding uncovered", got[0], "")


if __name__ == "__main__":
    for fn in [test_a_word_matched_cover_is_checked_by_code,
               test_a_proven_cover_is_still_accepted,
               test_a_semantic_cover_is_still_checked,
               test_a_rejected_cover_keeps_the_models_claim_on_the_row,
               test_a_cover_the_model_never_named_is_not_invented]:
        print(f"--- {fn.__name__}")
        fn()
    print(f"\n{len(FAILURES)} failure(s)" + (": " + ", ".join(FAILURES) if FAILURES else ""))
    sys.exit(1 if FAILURES else 0)
