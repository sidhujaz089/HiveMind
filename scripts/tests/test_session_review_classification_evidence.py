import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import session_review  # noqa: E402


def _deferral_row(turn=7):
    return {"kind": "deferral", "quote": "I'll verify the MTP model's real limit first",
            "context": "output cap", "turn": turn,
            "what": "never mentioned again", "cover": "", "verdict": "missed"}


def _other_row():
    return {"kind": "for-jas", "quote": "assign to the embedder when t1112 lands",
            "context": "handoff", "turn": 240,
            "what": "waiting on Jas", "cover": "", "verdict": "missed"}


def test_deferral_carries_classification_evidence():
    found = session_review.build_findings([_deferral_row(turn=19), _other_row()], list(range(1, 22)))
    defs = [f for f in found if f["kind"] == "deferral"]
    assert len(defs) == 1, "the deferral row must survive the build"
    ev = defs[0].get("classification_evidence")
    assert isinstance(ev, dict), "deferral finding must carry a classification_evidence object, got %r" % (ev,)
    turns_examined = ev.get("turns_examined")
    assert isinstance(turns_examined, list) and turns_examined, "turns_examined must be a non-empty list"
    assert all(isinstance(t, int) and not isinstance(t, bool) for t in turns_examined), "turns_examined must be ints"
    assert 19 in turns_examined, "turns_examined must include the finding's own turn (19)"
    rule = ev.get("rule_fired")
    assert isinstance(rule, str) and rule.strip(), "rule_fired must be a non-empty string naming the matcher"
    assert isinstance(ev.get("same_turn_check"), bool), "same_turn_check must be a real bool (performed or not)"


def test_non_deferral_has_no_classification_evidence():
    found = session_review.build_findings([_other_row()], list(range(1, 22)))
    other = [f for f in found if f["kind"] == "for-jas"]
    assert len(other) == 1
    assert "classification_evidence" not in other[0], "a for-jas finding must not gain the deferral field"


def test_drop_rows_still_filtered():
    drop = {"kind": "deferral", "quote": "x", "context": "x", "turn": 3, "what": "", "cover": "", "verdict": "drop"}
    assert session_review.build_findings([drop], list(range(1, 5))) == []


if __name__ == "__main__":
    failures = []
    for _fn in (test_deferral_carries_classification_evidence,
                test_non_deferral_has_no_classification_evidence,
                test_drop_rows_still_filtered):
        try:
            _fn()
        except AssertionError as e:
            failures.append((_fn.__name__, str(e)))
    if failures:
        for _n, _m in failures:
            print("FAIL", _n, "--", _m)
        raise SystemExit(1)
    print("ok")
