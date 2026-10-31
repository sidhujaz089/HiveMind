"""t1526 -- the RCA for session-review finding 81a5ac063858 answered empty twice.

The RCA prompt is built by json.dumps of the WHOLE finding record (in rca(), scripts/session_review.py,
around line 1517), so every key on the record goes into the prompt -- including candidates_consulted,
which on workspace/session-reviews/failed/81a5ac063858.json is ~1075 task ids, one per line. With a
24000-token output budget the model spends it all reasoning and returns no content: briefing-the-local-model
records this class, done_reason=length, as a shared-budget failure (reasoning and the answer draw on one
num_predict, so a long reasoning pass comes back EMPTY). rca()'s empty-answer guard records exactly that as
"no answer (timed out or empty)" -- both attempts on this finding.

Red on main because session_review has no rca_prompt_text: that builder is exactly the change the brief
asks for. Runs standalone: python3 scripts/tests/test_session_review_rca_prompt_size.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import session_review

QUOTE = "The supervision change is in the plan for the next session, after the push and Parts A and B."
CONTEXT = "Because it puts rules before the model, most failed builds won't need a model call."
WHERE = "assistant, turn 139 of 250"
REQUEST = "claude-a750672f-5cd5-4916-8164-b31195ae69aa--from-103.json"
SKILL_TEXT = "RCA-SKILL-MARKER: name the cause, then the smallest fix."
IDS = ["t%d" % (1000 + i) for i in range(1075)]


def _finding(ids):
    """A session-review finding record of the shape extract() writes, with a caller-supplied list."""
    return {
        "kind": "deferral",
        "quote": QUOTE,
        "context": CONTEXT,
        "where": WHERE,
        "why_missed": "Supervision change (rules before model) planned for next session",
        "disposition": "deferred",
        "key": "81a5ac063858",
        "request": REQUEST,
        "candidates_consulted": ids,
    }


def test_prompt_builder_exists_and_returns_a_string():
    p = session_review.rca_prompt_text(_finding(IDS), SKILL_TEXT)
    assert isinstance(p, str), f"the RCA prompt must be a str, got {type(p).__name__}"
    assert p.strip(), "the RCA prompt must not be empty"


def test_prompt_carries_what_the_rca_works_from():
    p = session_review.rca_prompt_text(_finding(IDS), SKILL_TEXT)
    for needed, label in ((QUOTE, "quote"), (CONTEXT, "context"),
                          (WHERE, "where"), (REQUEST, "request file"),
                          (SKILL_TEXT, "RCA skill")):
        assert needed in p, f"the prompt lost the finding's {label}: {needed!r}"
    assert "deferral" in p, "the prompt lost the finding's kind"
    assert "81a5ac063858" in p, "the prompt lost the finding's key"
    assert "Supervision change (rules before model)" in p, "the prompt lost why_missed"


def test_candidate_ids_are_not_in_the_prompt():
    p = session_review.rca_prompt_text(_finding(IDS), SKILL_TEXT)
    for tid in ("t1000", "t1001", "t1526", "t2000", "t2074"):
        assert tid not in p, f"candidates_consulted leaked into the RCA prompt: {tid}"


def test_candidate_list_does_not_inflate_the_prompt():
    with_ids = session_review.rca_prompt_text(_finding(IDS), SKILL_TEXT)
    without = session_review.rca_prompt_text(_finding([]), SKILL_TEXT)
    delta = len(with_ids) - len(without)
    assert delta <= 1000, (
        f"a 1075-entry candidates_consulted list added {delta} characters to the RCA prompt "
        f"({len(without)} -> {len(with_ids)}); the prompt must not carry it")


if __name__ == "__main__":
    _tests = sorted((k, v) for k, v in globals().items()
                     if k.startswith("test_") and callable(v))
    _fails = []
    for _name, _fn in _tests:
        try:
            _fn()
            print(f"ok   {_name}")
        except Exception as _e:                        # noqa: BLE001 - report, never swallow
            print(f"FAIL {_name}: {type(_e).__name__}: {_e}")
            _fails.append(_name)
    print(f"\n{len(_fails)} failed" if _fails else f"\nall {len(_tests)} passed")
    sys.exit(1 if _fails else 0)
