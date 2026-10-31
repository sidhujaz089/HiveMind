"""t1522: an answer a model finished must reach disk even if its out directory was swept.

Evidence (read 2026-10-21):
  workspace/run-logs/dispatch/2415d75fed2e.json--rca--swift--1792523575.log
  workspace/run-logs/dispatch/2415d75fed2e.json--rca--qwen3.8_27b-mtp-q4_K_M-56k--1792525210.log
Both: `-> done_reason=stop 26 min 18587 chars` / `17 min 8105 chars`, then
  File "scripts/ask_q16.py", line 603, in main -> out = write_answer(a.out, r)
  File "scripts/ask_q16.py", line 517, in write_answer -> o.write_text(r["text"])
  FileNotFoundError: [Errno 2] No such file or directory:
  '/home/user/.cache/hivemind-tmp/session-review-<8 chars>/answer.md'
The model ANSWERED (done_reason=stop; not length, not a timeout). The answer died at the last
write, into a directory its caller created in a temp root that a cleaner removes minutes later.

Pinned (no model, no live server, no writes outside tempfile.mkdtemp):
  T1 a finished answer is written when --out's parent directory is gone   (write_answer)
  T2 its .thinking.md and .meta.json land beside it, with the done_reason (write_answer)
  T3 an EMPTY answer still writes its .failed-<utc> sidecar there         (write_answer)
  T4 a normal --out path still writes the answer verbatim                 (neighbour guard)
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import ask_q16


def _fresh_dir():
    return Path(tempfile.mkdtemp(prefix="t1522-wa-"))


def test_1_a_swept_parent_directory_does_not_lose_the_answer():
    d = _fresh_dir()
    try:
        out = d / "session-review-abc12345" / "answer.md"   # parent does not exist: the swept shape
        got = ask_q16.write_answer(str(out), {"text": "the RCA answer", "done_reason": "stop",
                                              "complete": True})
        assert out.is_file(), (
            f"the answer was not on disk after write_answer returned {got!r}: a finished answer "
            f"was destroyed because its parent directory was gone -- this is how t1522 lost "
            f"18,587 characters (ask_q16.py:517)")
        assert out.read_text(encoding="utf-8") == "the RCA answer", (
            f"the answer text on disk must be the model's answer verbatim: "
            f"{out.read_text(encoding='utf-8')!r}")
        assert str(out) == got, (
            f"write_answer returns the path written (ask_q16.py:500): {got!r} != {str(out)!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_2_the_reasoning_and_the_ledger_row_land_with_the_answer():
    d = _fresh_dir()
    try:
        out = d / "session-review-xyz98765" / "answer.md"
        ask_q16.write_answer(str(out), {"text": "the RCA answer",
                                        "thinking": "the reasoning 26 minutes of tool rounds cost",
                                        "done_reason": "stop", "complete": True})
        th = out.parent / "answer.md.thinking.md"
        assert th.is_file(), (
            "the reasoning must be kept beside the answer (ask_q16.py:520-521) -- seven cut-off "
            "runs' reasoning was discarded, and hours of model time was unrecoverable")
        assert th.read_text(encoding="utf-8") == "the reasoning 26 minutes of tool rounds cost"
        meta = json.loads((out.parent / "answer.md.meta.json").read_text(encoding="utf-8"))
        assert meta.get("done_reason") == "stop", f"the meta row must carry the done_reason: {meta}"
        assert "text" not in meta, f"the meta row must not carry the answer text: {meta}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_3_an_empty_answer_still_records_its_sidecar():
    d = _fresh_dir()
    try:
        out = d / "session-review-ghi45678" / "answer.md"
        got = ask_q16.write_answer(str(out), {"text": "", "done_reason": "length",
                                              "complete": False})
        side = Path(got)
        assert side.name.startswith("answer.md.failed-"), (
            f"an empty answer is recorded in a .failed-<utc> sidecar (ask_q16.py:507-516), "
            f"not in answer.md: {got!r}")
        assert side.is_file(), f"the sidecar of a swept run's empty answer must exist: {side}"
        row = json.loads(side.read_text(encoding="utf-8"))
        assert row.get("done_reason") == "length", f"the sidecar says what happened: {row}"
        assert not out.exists(), (
            "an empty answer must NOT fabricate an answer.md (ask_q16.py:505-506): "
            "a file on disk saying something that did not happen")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_4_a_normal_out_path_still_writes_the_answer():
    d = _fresh_dir()
    try:
        out = d / "answer.md"                 # parent exists: today's normal case
        got = ask_q16.write_answer(str(out), {"text": "the answer text", "done_reason": "stop",
                                              "complete": True})
        assert got == str(out), f"return the path written: {got!r}"
        assert out.read_text(encoding="utf-8") == "the answer text", (
            f"the normal path must be unchanged: {out.read_text(encoding='utf-8')!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    fails = 0
    for _n in sorted(k for k in globals() if k.startswith("test_")):
        try:
            globals()[_n]()
            print("ok", _n)
        except AssertionError as e:
            fails += 1
            print(f"FAIL {_n}: {e}")
    print("FAILURES:", fails)
    sys.exit(1 if fails else 0)
