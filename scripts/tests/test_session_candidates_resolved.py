#!/usr/bin/env python3
"""t719 fail-first oracle: a promise the session itself PERFORMS in its next turns must not be
flagged MISSED.

Run: python3 scripts/tests/test_session_candidates_resolved.py   (no pytest; exits non-zero on failure)

The scripted review keeps every assistant candidate the session "never came back to": dropped()
keeps a row unless mentioned_later finds a later sentence sharing half its topic words, and that
scan runs over the WHOLE rest of the session. On the hand-graded audit set that flags 544 MISSED
rows for 10 real misses (workspace/session-reviews/trials/triage-swift/RESULTS.md). The noise is
questions and promises settled on the spot: "I'll add the NAS prune to the nightly sweep later, once
the audit of the archive lands." ... "Filed as t687 - the NAS prune is in the nightly sweep now."
That answer shares 3 of the promise's 7 topic words -- 3/7 = 0.43, under the 0.5 bar -- so a filter
that copies the 0.5 bar into a narrow window filters nothing: a window is a SUBSET of the sentences
mentioned_later already scans, so it can only drop rows already dropped (recorded build 2026-10-07:
576 -> 572, and that movement is second-order through group(), not through the filter).

Red on the current tree for the missing change: sc.resolved_in_session does not exist (AttributeError).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_candidates as sc

sc.SEMANTIC = False    # the documented word-only switch: the oracle never imports semantic / Ollama

# fixture A: a promise filed two lines later. 7 topic words, 3 shared = 0.43 < 0.5.
PROMISED_A = "I'll add the NAS prune to the nightly sweep later, once the audit of the archive lands."
ANSWER_A = "Filed as t687 - the NAS prune is in the nightly sweep now."
A = [("user", "What is open on the NAS audit?"),
     ("assistant", PROMISED_A),
     ("assistant", ANSWER_A)]

# fixture B: a short row (3 topic words) settled in the next assistant turn -- adjacent, and gapped
# by a user turn so the window itself is what is being measured.
PROMISED_B = "I'll queue the settings.local.json rules as a task."
ANSWER_B = "Done -- queued the settings.local.json rules as t687."
ADJACENT = [("user", "Queue the settings.local.json rules, please."),
            ("assistant", PROMISED_B),
            ("assistant", ANSWER_B)]
GAPPED = ADJACENT[:2] + [("user", "Thanks. What is left on the queue?"), ("assistant", ANSWER_B)]

# fixture C: DONE words about the same subject, on an OBSERVATION -- must NOT be silenced.
NOTICED_C = "I'd flag the scheduler bar as a gap worth a task."
DONE_NEAR_C = "Done -- the scheduler bar gap is filed as t693."
C = [("user", "Is the scheduler bar missing anything?"),
     ("assistant", NOTICED_C),
     ("assistant", DONE_NEAR_C)]

# fixture D (composite): three settled rows, one observation, one item the session never filed.
D = [("user", "Can you look at the patch layer?"),
     ("assistant", "I'll write the dsh-local-ai patch-layer lesson later."),
     ("assistant", "Done -- the dsh-local-ai patch-layer lesson is in LESSONS.md as t696."),
     ("user", "Should I delete the September snapshots?"),
     ("assistant", "I can add a nightly prune for the September snapshots."),
     ("assistant", "Done -- the nightly prune for the September snapshots is added as t690."),
     ("user", "Is the scheduler bar missing anything?"),
     ("assistant", NOTICED_C),
     ("user", "What is left for the front page?"),
     ("assistant", "Want me to add the front-page banner?"),
     ("assistant", "Done -- the front-page banner is added as t700."),
     ("user", "What about the seven folders nobody has opened?"),
     ("assistant", "I'll identify the seven NAS folders I have never opened.")]
UNFILED = "I'll identify the seven NAS folders I have never opened."


def rows_of(ts):
    """The assistant rows the scripted review would flag (dropped() filters by role already)."""
    return [r for r in sc.dropped(ts) if r["role"] == "assistant"]


def row_for(ts, needle):
    """The one row whose quote contains this full phrase. Every needle is a full phrase unique to one
    sentence, so a drift in the extractor's types cannot silently retarget a test (t719, 2026-10-07:
    an oracle that asserted row COUNTS went red on a correct build)."""
    got = rows_of(ts)
    hit = [r for r in got if needle in r["quote"]]
    assert len(hit) == 1, f"needle {needle!r} matched {len(hit)} of {[r['quote'] for r in got]}"
    return hit[0]


def test_a_filed_promise_is_resolved_in_the_session():
    r = row_for(A, "I'll add the NAS prune")
    assert r["type"] == "PROMISED", f"fixture drift: type was {r['type']!r}"
    assert sc.resolved_in_session(A, r) is True, "the session filed the promise; it is not dropped work"
    assert r["quote"] not in [x["quote"] for x in rows_of(A)], "a settled row must leave dropped()"


def test_the_filter_is_not_a_copy_of_the_topic_share_bar():
    r = row_for(A, "I'll add the NAS prune")
    q = sc.topic_words(r["quote"])
    shared = len(q & sc.topic_words(ANSWER_A))
    assert len(q) == 7, f"fixture drift: 7 topic words expected, got {sorted(q)}"
    assert shared == 3, f"fixture drift: 3 shared words expected, got {shared}"
    assert shared / len(q) < 0.5, f"the arithmetic the filter must beat: {shared}/7 = {shared/7:.2f}"
    assert sc.mentioned_later(A, r, 0.5) is False, "the 0.5 bar must not reach this row"
    assert sc.resolved_in_session(A, r) is True
    r2 = row_for(ADJACENT, "I'll queue the settings.local.json rules")
    assert sc.resolved_in_session(ADJACENT, r2) is True, "the relaxation must work on a short row too"


def test_a_short_row_needs_one_shared_word_not_two():
    r = row_for(ADJACENT, "I'll queue the settings.local.json rules")
    assert r["type"] == "PROMISED", f"fixture drift: type was {r['type']!r}"
    q = sc.topic_words(r["quote"])
    assert len(q) < 5, f"fixture drift: this must be a short row (RESOLVE_MIN_WORDS=5), got {sorted(q)}"
    assert len(q & sc.topic_words(ANSWER_B)) >= 1, "RESOLVE_ANY must carry a row with few words to spare"
    assert sc.resolved_in_session(ADJACENT, r) is True
    assert r["quote"] not in [x["quote"] for x in rows_of(ADJACENT)]


def test_a_resolution_after_jas_speaks_is_outside_the_window():
    r = row_for(GAPPED, "I'll queue the settings.local.json rules")
    assert sc.resolved_in_session(GAPPED, r) is False, "the item died the moment Jas spoke again"
    assert r["quote"] in [x["quote"] for x in rows_of(GAPPED)], "the row must stay flagged"


def test_done_words_about_something_else_keep_a_noticed_row():
    r = row_for(C, "I'd flag the scheduler bar")
    assert r["type"] == "NOTICED", f"fixture drift: expected NOTICED, got {r['type']!r}"
    shared = sc.topic_words(r["quote"]) & sc.topic_words(DONE_NEAR_C)
    assert "scheduler" in shared, f"fixture drift: the DONE line must share a topic word, got {sorted(shared)}"
    assert sc.resolved_in_session(C, r) is False, "an unrelated DONE line must not silence a NOTICED row"
    assert r["quote"] in [x["quote"] for x in rows_of(C)], "the 10 real misses look like this one"


def test_settled_rows_fall_and_the_unfiled_item_survives():
    kept = [x["quote"] for x in rows_of(D)]
    for settled in ("I'll write the dsh-local-ai patch-layer lesson",
                    "I can add a nightly prune for the September snapshots",
                    "Want me to add the front-page banner"):
        assert not any(settled in k for k in kept), f"settled row still flagged: {settled}"
    assert any(UNFILED in k for k in kept), "the item the session never filed must stay flagged"
    assert len(kept) == 2, f"expected exactly the observation and the unfiled item, got {kept}"


TESTS = [test_a_filed_promise_is_resolved_in_the_session,
         test_the_filter_is_not_a_copy_of_the_topic_share_bar,
         test_a_short_row_needs_one_shared_word_not_two,
         test_a_resolution_after_jas_speaks_is_outside_the_window,
         test_done_words_about_something_else_keep_a_noticed_row,
         test_settled_rows_fall_and_the_unfiled_item_survives]

if __name__ == "__main__":
    bad = []
    for t in TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            bad.append(t.__name__)
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"{len(TESTS) - len(bad)}/{len(TESTS)} passed")
    sys.exit(1 if bad else 0)
