#!/usr/bin/env python3
"""t1399: the supervising role must have a TERMINAL state for "no legal model left".

RCA workspace/rca/bb9f66ff7673.md (finding bb9f66ff7673, sections 2 and 5): p42/p54/p57 parked
unboundedly because SWITCH had no target and the vocabulary {retry, switch, park} has no
"no legal move" state. The fix is a terminal ROSTER-EXHAUSTED that parks WITH a re-dispatch
trigger -- a release stated as a dated note in the brief is not a recorder.

Real constants taken from the record, not invented:
  chunk/brief/model/at   rca/bb9f66ff7673.md:11  (supervision.jsonl:38)
  MAX_SWITCHES = 1       dispatch_loop.py:298
  the release dependency rca/bb9f66ff7673.md:15  ("Retry once t350 and t400 are in")

No live state is touched: every row passed to is_parked/execute is a literal built here.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import supervise

CHUNK = "p42-comply-chatui-model-call-outside-contract"
SHA = "3f1c0d9e4b7a25680c1f9e4d7b0a5382c61f0e4d9a7b235c08d1e6f4a90b7c25"
EDITED_SHA = "0000000000000000000000000000000000000000000000000000000000000000"
MODEL = "qwen3.8:27b-mtp-q4_K_M"
AT = "2026-09-24T16:42:44Z"
RELEASE = "release when t350 and t400 are merged and recorded with dispatch_loop.py --harness-fixed"
FAIL = {"at": AT, "chunk": CHUNK, "model": MODEL, "brief": CHUNK + ".md",
        "brief_sha256": SHA, "exit": 1, "seconds": 6770, "pid": 4242}
DECISION = {"decision": "ROSTER-EXHAUSTED",
            "why": "gemma4:26b and qwen3.8:27b-mtp-q4_K_M have both failed this exact brief, "
                   "so SWITCH has no target (MAX_SWITCHES is 1)",
            "release_when": RELEASE}


def test_roster_exhausted_is_a_legal_decision():
    raw = ('{"decision": "ROSTER-EXHAUSTED", "why": "both builders have failed this brief", '
           '"release_when": "' + RELEASE + '"}')
    d = supervise.parse_decision(raw)
    assert d is not None, (
        "parse_decision refused ROSTER-EXHAUSTED: it accepts a decision only if it is in "
        "supervise.DECISIONS (supervise.py:256) and DECISIONS has no 'no legal model left' "
        "state (supervise.py:63) -- a supervisor that cannot name the true state can only "
        "stay silent, which is how p42/p54/p57 parked forever (rca/bb9f66ff7673.md:20).")
    assert d.get("decision") == "ROSTER-EXHAUSTED"
    # The trigger the supervisor names must survive the parse and reach the executor.
    assert d.get("release_when") == RELEASE, (
        "the re-dispatch trigger was dropped by the parse: a terminal state that parks without "
        "naming what releases it is a park with no exit (rca/bb9f66ff7673.md:38).")


def test_the_vocabulary_is_still_closed():
    # Negative guard: the fix adds ONE word, it does not open the vocabulary. A parser that
    # accepts any string turns every future typo into a recorded decision.
    assert supervise.parse_decision('{"decision": "RETRY-FOREVER", "why": "x"}') is None
    assert supervise.parse_decision('{"why": "no decision at all"}') is None
    assert supervise.parse_decision("not json at all") is None


def test_roster_exhausted_parks_the_brief():
    rows = [{"at": AT, "chunk": CHUNK, "brief_sha256": SHA, "decision": "ROSTER-EXHAUSTED",
             "why": "both builders have failed this brief", "release_when": RELEASE}]
    assert supervise.is_parked(CHUNK, SHA, rows) is True, (
        "ROSTER-EXHAUSTED is not honoured as a park: is_parked (supervise.py:357-360) matches "
        "only the literal 'PARK', so the scheduler would keep re-dispatching the chunk onto "
        "the model that already failed it -- the unbounded retry this state exists to stop.")


def test_a_switch_is_still_not_a_park():
    # Negative guard on the new state: it must not swallow SWITCH.
    rows = [{"at": AT, "chunk": CHUNK, "brief_sha256": SHA, "decision": "SWITCH",
             "why": "another model should try"}]
    assert supervise.is_parked(CHUNK, SHA, rows) is False


def test_the_park_is_scoped_to_the_brief_it_was_decided_on():
    rows = [{"at": AT, "chunk": CHUNK, "brief_sha256": SHA, "decision": "ROSTER-EXHAUSTED",
             "release_when": RELEASE}]
    assert supervise.is_parked("p54-comply-t418-coder-no-timeout", SHA, rows) is False
    # Editing the brief IS the release (dispatch_loop.py:37: the park is keyed on the brief sha).
    assert supervise.is_parked(CHUNK, EDITED_SHA, rows) is False


def test_executing_the_decision_names_what_releases_it():
    out = supervise.execute(dict(FAIL), dict(DECISION))
    assert isinstance(out, str) and out.strip(), (
        "execute() returned nothing for ROSTER-EXHAUSTED; a decision with no stated action is "
        "indistinguishable from one that was never carried out.")
    assert out.strip() != "no action", (
        "ROSTER-EXHAUSTED fell through to execute()'s 'no action' (supervise.py:330): the "
        "terminal state is nameable but does nothing -- the RCA asks that it parks WITH a "
        "re-dispatch trigger.")
    assert CHUNK in out, f"the action does not name the chunk it acted on: {out!r}"
    assert RELEASE in out, (
        f"the action does not carry the release trigger the supervisor named ({RELEASE!r}): "
        f"{out!r} -- a terminal state with no recorded trigger is the 'dated note' defect in "
        "new clothes (rca/bb9f66ff7673.md:31, D2).")


def test_executing_the_decision_writes_no_brief_file():
    # REWRITE-BRIEF writes into workspace/briefs (supervise.py:287-300). ROSTER-EXHAUSTED must
    # not: it is a record, not a rewrite. Read-only check of the live directory.
    target = supervise.BRIEFS / (CHUNK + ".md")
    existed = target.is_file()
    supervise.execute(dict(FAIL), dict(DECISION))
    assert target.is_file() == existed, (
        "executing ROSTER-EXHAUSTED changed the brief on disk -- a terminal record must not "
        "edit the thing it is parking, and editing the brief is the act that RELEASES a park.")


def test_the_decision_is_attributable():
    # main() feeds DECISION_CLASS into scope_quality.record with a default of "unclassified"
    # (supervise.py:416-421); a missing key loses the scope defect's class.
    assert supervise.DECISION_CLASS.get("ROSTER-EXHAUSTED") == "harness", (
        "DECISION_CLASS (supervise.py:66-67) has no entry for ROSTER-EXHAUSTED, so its record "
        "goes to scope_quality as 'unclassified' and the scope-quality ledger cannot count it.")


if __name__ == "__main__":
    tests = sorted((n, f) for n, f in list(globals().items())
                   if n.startswith("test_") and callable(f))
    failed, ran = [], 0
    for name, fn in tests:
        ran += 1
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:                                       # noqa: BLE001
            failed.append(name)
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n  {ran} test(s) run, {len(failed)} failed")
    # A suite that collects nothing must not be green: the builder cannot satisfy this by
    # deleting tests (the reason an oracle is frozen in its own dispatch, D-35).
    if ran < 8:
        print(f"  FAIL: expected 8 tests, ran {ran} -- tests must not be removed from this oracle")
        sys.exit(1)
    sys.exit(1 if failed else 0)
