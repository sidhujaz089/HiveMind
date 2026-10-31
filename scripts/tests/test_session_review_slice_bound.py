"""t1076 -- a deferral must not be stateable from a truncated session slice.

WHY THIS EXISTS. Finding 92ad8dac0a15 (workspace/rca/92ad8dac0a15.md) was emitted from a
review bounded to turns 0-839 of a 7524-record session and said "Assistant, final turn ...
left unfinished". Inside that slice the sentence is true; of the session it is false -- the
guard it promised was written 35 seconds after the quote (record 1661), shipped as commit
a1e8347a, tested (17 assertions), mutation-guarded (40 caught / 0 missed) and recorded as
L-116. The reviewer had no field that said "I did not read the rest", so it wrote a fact
about a part of the record it never read. That is never-truncate-a-record's one line:
"A truncated record looks complete. Every check passes ... and the one fact you needed was
in the part that was thrown away."

This suite pins the decision the session-review writer must consult before it may write
kind="deferral": a slice that stops before the end of the transcript cannot yield one. It
calls scripts/session_review.py:deferral_gate, which does not exist on main -- that is why
this file is red today, and it is the only reason it is red.

No live state: nothing is written to the repository, no subprocess, no model call, no fixed
temp path. Run it as: python3 scripts/tests/test_session_review_slice_bound.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # scripts/tests/ -> repo root
sys.path.insert(0, str(ROOT / "scripts"))

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("FAIL:", msg)


def report():
    print(f"\n{len(FAILS)} assertion(s) failed" if FAILS else "\nall assertions passed")
    return 1 if FAILS else 0


def test_the_gate_exists():
    """The single reason this file is red on main."""
    import session_review
    return getattr(session_review, "deferral_gate", None)


def test_deferral_from_a_truncated_slice_is_refused(gate):
    """The incident itself: 839 turns read of a 7524-turn session.

    The numbers are the real ones from the finding's own request row
    (workspace/session-reviews/ledger.jsonl, queued 2026-09-28T18:02:41Z, from_turn 0,
    to_turn 839) and the transcript length the RCA measured (7,524 records).
    """
    verdict, rec = gate("deferral", 0, 839, 7524)
    check(verdict == "refused",
          f"a deferral on a 0-839 slice of a 7524-turn session must be refused, got {verdict!r}")
    check(rec.get("kind") == "observation",
          f"a refused deferral must be recorded as an observation, not dropped and not kept "
          f"as a deferral: got kind={rec.get('kind')!r}")
    check(rec.get("from_turn") == 0, f"from_turn must be recorded as 0, got {rec.get('from_turn')!r}")
    check(rec.get("to_turn") == 839, f"to_turn must be recorded as 839, got {rec.get('to_turn')!r}")
    check(rec.get("turn_count_total") == 7524,
          f"turn_count_total must be recorded as 7524, got {rec.get('turn_count_total')!r}")
    check(rec.get("complete") is False,
          f"complete must be False for a slice of 839/7524, got {rec.get('complete')!r}")
    for key in ("from_turn", "to_turn", "turn_count_total", "complete"):
        check(key in rec, f"the slice bound record must carry {key!r}; keys were {sorted(rec)}")


def test_the_notice_names_the_cut(gate):
    """never-truncate-a-record: the bound must announce itself, say how much, say what to do."""
    verdict, rec = gate("deferral", 0, 839, 7524)
    notice = rec.get("truncation_notice")
    check(isinstance(notice, str) and notice,
          f"a truncated slice must carry a truncation_notice string, got {notice!r}")
    if isinstance(notice, str):
        for token in ("TRUNCATED", "839", "7524", "say so", "absent"):
            check(token in notice,
                  f"the notice must name {token!r} (cut / bound / guidance to report rather "
                  f"than infer absence); notice was {notice!r}")
        check(not notice.lstrip().startswith("{"),
              f"the notice must be human text, not a JSON blob: {notice!r}")


def test_the_rule_is_a_rule_not_the_incident(gate):
    """A second, distinct pair of numbers: a gate that special-cases 839/7524 fails here."""
    verdict, rec = gate("deferral", 0, 100, 250)
    check(verdict == "refused",
          f"a 100-of-250 slice must also be refused, got {verdict!r}")
    check(rec.get("complete") is False,
          f"complete must be False for 100/250, got {rec.get('complete')!r}")
    notice = rec.get("truncation_notice") or ""
    check("100" in notice and "250" in notice,
          f"the notice must name the slice's own bounds 100 and 250, got {notice!r}")
    check("7524" not in notice and "839" not in notice,
          f"the notice must not carry numbers from an unrelated slice, got {notice!r}")


def test_a_whole_record_may_still_defer(gate):
    """NEGATIVE assertion: the gate must not refuse everything. A complete read keeps deferral."""
    verdict, rec = gate("deferral", 0, 7524, 7524)
    check(verdict == "allowed", f"a slice reaching the end of the transcript must be allowed, got {verdict!r}")
    check(rec.get("kind") == "deferral",
          f"kind must stay 'deferral' on a complete slice, got {rec.get('kind')!r}")
    check(rec.get("complete") is True, f"complete must be True at 7524/7524, got {rec.get('complete')!r}")
    check("truncation_notice" not in rec,
          f"a complete slice must carry NO truncation_notice key, got {rec.get('truncation_notice')!r}")


def test_an_observation_from_a_truncated_slice_is_allowed(gate):
    """NEGATIVE assertion: only a deferral is unstateable; an observation keeps the loud bound."""
    verdict, rec = gate("observation", 0, 839, 7524)
    check(verdict == "allowed",
          f"an observation from a bounded slice is legal and must be allowed, got {verdict!r}")
    check(rec.get("kind") == "observation", f"kind must be 'observation', got {rec.get('kind')!r}")
    check(rec.get("complete") is False, f"complete must be False, got {rec.get('complete')!r}")
    notice = rec.get("truncation_notice")
    check(isinstance(notice, str) and "839" in notice and "7524" in notice,
          f"an observation from a bounded slice keeps the bound inline naming 839 of 7524, got {notice!r}")


def test_unknown_length_is_not_complete(gate):
    """A slice of unknown total length is not a known-complete read.

    (RCA declared-open U1: the turn-index -> transcript-record mapping was never verified, so
    a caller that cannot state the total must not get to claim completeness.)
    """
    verdict, rec = gate("deferral", 0, 839, 0)
    check(verdict == "refused",
          f"a slice of unknown total length must not be treated as complete, got {verdict!r}")
    check(rec.get("complete") is False, f"complete must be False when turn_count_total is 0, got {rec.get('complete')!r}")


def test_the_returned_shape_is_the_record_the_writer_stamps(gate):
    """The tuple the write site consumes: a verdict string and a dict carrying the slice bound."""
    with tempfile.TemporaryDirectory() as td:      # proves the module imports cleanly off-repo
        Path(td)
        verdict, rec = gate("deferral", 0, 7524, 7524)
        check(isinstance(verdict, str), f"verdict must be a str, got {type(verdict).__name__}")
        check(isinstance(rec, dict), f"record must be a dict, got {type(rec).__name__}")
        for key in ("from_turn", "to_turn", "turn_count_total", "complete"):
            check(key in rec, f"the record a writer stamps must carry {key!r}; keys were {sorted(rec)}")


def main():
    gate = test_the_gate_exists()
    if gate is None:
        check(False, "scripts/session_review.py has no deferral_gate(): a deferral can still be "
                     "written from a slice that stops before the end of the transcript")
        return report()
    if not callable(gate):
        check(False, f"deferral_gate exists but is not callable: {gate!r}")
        return report()
    test_deferral_from_a_truncated_slice_is_refused(gate)
    test_the_notice_names_the_cut(gate)
    test_the_rule_is_a_rule_not_the_incident(gate)
    test_a_whole_record_may_still_defer(gate)
    test_an_observation_from_a_truncated_slice_is_allowed(gate)
    test_unknown_length_is_not_complete(gate)
    test_the_returned_shape_is_the_record_the_writer_stamps(gate)
    return report()


if __name__ == "__main__":
    sys.exit(main())
