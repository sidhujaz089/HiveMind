#!/usr/bin/env python3
"""t1521: ask_q16.write_answer() must not lose a finished answer to a vanished --out parent.

Both RCA attempts on 1c483b6e8897 (2026-10-20) finished: the dispatch logs say
done_reason=stop 8 min 9316 chars and done_reason=stop 17 min 13536 chars. Both then died at
scripts/ask_q16.py:517 `o.write_text(r["text"])` with FileNotFoundError on
'.../.cache/hivemind-tmp/session-review-<rand>/answer.md' -- the temp directory the caller made with
tempfile.mkdtemp was removed while the model was still generating. A complete answer existed, was
written nowhere, and session_review recorded 'no answer (timed out or empty)'.

Red on main for that reason: test 1 raises FileNotFoundError inside write_answer today.
No model call, no server, no live state, no subprocess: tempfile only.
"""
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("ask_q16", ROOT / "scripts" / "ask_q16.py")
ask_q16 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ask_q16)

ANSWER = "## root cause\nThe lane never dispatched: scope_queue.py returns early."


def test_vanished_parent_is_recreated_and_the_answer_is_written():
    with tempfile.TemporaryDirectory(prefix="t1521-") as td:
        out = Path(td) / "session-review-abc123" / "answer.md"
        out.parent.mkdir()
        out.parent.rmdir()                      # what a tmp cleaner does to a long-running job
        ret = ask_q16.write_answer(str(out), {"text": ANSWER, "thinking": "THINK", "done_reason": "stop"})
        assert out.is_file(), f"the finished answer was not written: {out}"
        assert out.read_text(encoding="utf-8") == ANSWER, "the answer text must be written whole"
        assert str(ret) == str(out), f"write_answer must return the path it wrote, got {ret!r}"
        meta = Path(str(out) + ".meta.json")
        assert meta.is_file(), "the run's .meta.json must be written beside the answer"
        assert json.loads(meta.read_text(encoding="utf-8"))["done_reason"] == "stop"
        assert Path(str(out) + ".thinking.md").read_text(encoding="utf-8") == "THINK"


def test_empty_answer_still_writes_no_empty_markdown():
    # Regression pin, written against today's behaviour (ask_q16.py:505-516): an empty run must NOT
    # create the answer file. A zero-byte .md blocks every commit -- the pre-commit hook refuses
    # empty markdown (t311). The fix must not "fix" the lost-answer case by writing empty files.
    with tempfile.TemporaryDirectory(prefix="t1521-") as td:
        out = Path(td) / "answer.md"
        ret = ask_q16.write_answer(str(out), {"text": "", "done_reason": "length"})
        assert not out.exists(), "an empty run must NOT create the answer file"
        assert Path(str(ret)).name.startswith("answer.md.failed-"), f"got {ret!r}"
        assert Path(str(ret)).is_file(), "the empty run must be recorded in its .failed- sidecar"


if __name__ == "__main__":
    fails = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"PASS {_name}")
            except Exception as _exc:
                fails += 1
                print(f"FAIL {_name}: {type(_exc).__name__}: {_exc}")
    print(f"{fails} failure(s)")
    raise SystemExit(1 if fails else 0)
