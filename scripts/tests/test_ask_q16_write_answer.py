"""test_ask_q16_write_answer.py -- a completed model answer must reach the disk.

t1523 (2026-10-20): the RCA for session-review finding 4764b5910ecc was generated TWICE
(done_reason=stop, 23895 chars in 41 min and 16003 chars in 34 min -- see
workspace/run-logs/dispatch/4764b5910ecc.json--rca--swift--1792525210.log and
--1792528128.log) and both times ask_q16.write_answer() raised FileNotFoundError
writing it, because the temp directory its caller made (session_review.py:320,
tempfile.mkdtemp under TMPDIR=~/.cache/hivemind-tmp) had been swept away during the
call. The answer was destroyed, the record said only "no answer (timed out or empty)",
and the finding was pushed out of the queue as unusable.

write_answer must create a missing parent directory instead of raising, so a finished
generation cannot be lost to a vanished scratch dir.

Run: python3 scripts/tests/test_ask_q16_write_answer.py   (no pytest installed)
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import ask_q16  # noqa: E402


def _meta_of(answer_path):
    return json.loads(Path(str(answer_path) + ".meta.json").read_text(encoding="utf-8"))


def test_answer_survives_a_vanished_directory():
    """The one scenario this module lost. The caller's mkdtemp dir is gone by the time the
    answer comes back 41 minutes later; the answer must land on disk, whole."""
    with tempfile.TemporaryDirectory() as td:
        gone = Path(td) / "session-review-xyz"
        target = gone / "answer.md"
        assert not gone.exists(), "fixture must start with the directory absent"
        got = ask_q16.write_answer(str(target), {"text": "RCA ANSWER BODY",
                                                "done_reason": "stop",
                                                "rounds": 11})
        assert target.is_file(), (
            f"no answer file at {target}: a completed generation was thrown away "
            "(t1523: 23895 and 16003 chars, done_reason=stop, lost at ask_q16.py:517)")
        assert target.read_text(encoding="utf-8") == "RCA ANSWER BODY", "answer text altered"
        assert str(got) == str(target), f"returned {got!r}, expected the answer path"
        # the OUTCOME is recorded beside the answer, on the same write that the answer needs
        meta = _meta_of(target)
        assert meta.get("done_reason") == "stop", f"meta lost done_reason: {meta!r}"
        assert meta.get("rounds") == 11, f"meta lost rounds: {meta!r}"


def test_nested_missing_parents_are_created():
    """--out is free-form, so the answer can sit below the temp root: the fix must create
    every missing level, not just one."""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "session-review-abc" / "sub" / "answer.md"
        ask_q16.write_answer(str(target), {"text": "NESTED", "done_reason": "stop"})
        assert target.is_file(), f"{target} was not created"
        assert target.read_text(encoding="utf-8") == "NESTED"


def test_empty_result_still_never_buries_a_real_answer():
    """The guarantee write_answer was written for (2026-09-25, its docstring) must survive
    this fix: an empty result goes to a .failed-<utc> sidecar and leaves the earlier real
    answer exactly as it was."""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "answer.md"
        ask_q16.write_answer(str(target), {"text": "REAL ANSWER", "done_reason": "stop"})
        side = ask_q16.write_answer(str(target), {})
        assert target.read_text(encoding="utf-8") == "REAL ANSWER", \
            "an empty run overwrote a real answer"
        sp = Path(str(side))
        assert sp.name.startswith("answer.md.failed-"), f"sidecar is {sp.name!r}"
        assert sp.is_file(), f"no .failed- sidecar at {sp}"


if __name__ == "__main__":
    tests = [test_answer_survives_a_vanished_directory,
             test_nested_missing_parents_are_created,
             test_empty_result_still_never_buries_a_real_answer]
    fails = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as e:
            fails += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"{len(tests) - fails}/{len(tests)} passed")
    sys.exit(1 if fails else 0)
