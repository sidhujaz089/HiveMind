#!/usr/bin/env python3
"""t1534: ask_q16.write_answer must not lose a real answer when its temp dir was swept.

The incident (workspace/run-logs/dispatch/, both attempts, 2026-10-20):
  d6370c7b183c.json--rca--qwen3.8_27b-mtp-q4_K_M-56k--1792535793.log
      -> done_reason=stop 12 min 17840 chars (11 tool round(s))
      FileNotFoundError: ... 'session-review-e7oxpw2l/answer.md' at ask_q16.py:517
  d6370c7b183c.json--rca--swift--1792536919.log
      -> done_reason=stop 28 min 24305 chars (11 tool round(s))
      FileNotFoundError: ... 'session-review-h6xuyc2v/answer.md'

The model ANSWERED both times (done_reason=stop). The answer died at the write: the job's
temp dir under ~/.cache/hivemind-tmp was deleted between the last tool round and the write --
scripts/sweep.py:527 sweeps that root -- and a 12-28 minute dispatch is long enough to be
swept mid-flight. ask_q16.write_answer never creates the parent directory of OUT, so
o.write_text raises FileNotFoundError and the whole answer is lost. The ledger then recorded
"no answer (timed out or empty)", which is not what happened.

Red on main for that reason and no other: the swept-parent tests raise FileNotFoundError
from the write statements; the empty-answer tests pass on main (they pin behaviour that must
survive) and the swept-empty test fails because side.write_text has the same exposure.
"""
import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

IMPORT_ERROR = ""
ask_q16 = None
try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "ask_q16_under_test", ROOT / "scripts" / "ask_q16.py")
    ask_q16 = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(ask_q16)
except Exception as exc:  # named in every failure message, never swallowed
    IMPORT_ERROR = "cannot import scripts/ask_q16.py: %s: %s" % (type(exc).__name__, exc)

# DISTINCT real strings throughout: one repeated placeholder cannot tell answer from thinking
# from a prior answer (failure class oracle-weak, t297/t370).
ANSWER_TEXT = (
    "RCA for finding d6370c7b183c: the model answered; the write lost it.\n"
    + ("evidence line %d: the parent directory is gone\n" % i for i in range(600)).__iter__().__next__()
    + ("x" * 20000) + "\nTAIL-MARKER-9f31\n")
THINKING_TEXT = "thinking: the parent directory is gone; recreate it before writing.\n"
PRIOR_ANSWER = "PRIOR-ANSWER-SENTINEL-4471\n"


def _require_module():
    if IMPORT_ERROR:
        raise AssertionError(IMPORT_ERROR)


def _job_dir(base, name):
    """A job dir of the incident's shape: <tmp>/session-review-<8 chars>/answer.md."""
    d = base / ("session-review-" + name)
    d.mkdir(parents=True)
    return d / "answer.md"


def test_real_answer_survives_a_swept_parent():
    """The defect: a completed answer written into a dir that was swept mid-dispatch."""
    _require_module()
    base = Path(tempfile.mkdtemp(prefix="t1534-a-"))
    try:
        out = _job_dir(base, "abc12345")
        shutil.rmtree(out.parent)                      # swept, exactly as on 2026-10-20
        r = {"text": ANSWER_TEXT, "thinking": THINKING_TEXT,
             "done_reason": "stop", "elapsed_s": 737, "tool_rounds": 11}
        got = ask_q16.write_answer(str(out), r)
        assert got == str(out), "real answer redirected to %r, not the requested path" % (got,)
        on_disk = Path(out).read_text()
        assert on_disk == ANSWER_TEXT, "answer truncated: %d chars written, %d produced" % (
            len(on_disk), len(ANSWER_TEXT))
        assert on_disk.endswith("TAIL-MARKER-9f31\n"), "the tail of the answer is missing"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_meta_records_the_reason_that_actually_happened():
    """The record must say stop, not 'no answer (timed out or empty)'."""
    _require_module()
    base = Path(tempfile.mkdtemp(prefix="t1534-b-"))
    try:
        out = _job_dir(base, "def56789")
        shutil.rmtree(out.parent)
        ask_q16.write_answer(str(out), {"text": ANSWER_TEXT, "thinking": THINKING_TEXT,
                                        "done_reason": "stop", "elapsed_s": 737})
        meta = Path(str(out) + ".meta.json")
        assert meta.exists(), "no .meta.json: the run left no record of what it did"
        rec = json.loads(meta.read_text())
        assert rec.get("done_reason") == "stop", "meta says %r, not 'stop'" % (rec.get("done_reason"),)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_thinking_is_kept_when_the_parent_is_swept():
    """ask_q16 keeps the reasoning always (2026-09-23); a swept dir must not undo that."""
    _require_module()
    base = Path(tempfile.mkdtemp(prefix="t1534-c-"))
    try:
        out = _job_dir(base, "ghi78901")
        shutil.rmtree(out.parent)
        ask_q16.write_answer(str(out), {"text": ANSWER_TEXT, "thinking": THINKING_TEXT,
                                        "done_reason": "stop"})
        th = Path(str(out) + ".thinking.md")
        assert th.exists(), "no .thinking.md: model time written and then thrown away"
        assert th.read_text() == THINKING_TEXT, "the reasoning on disk is not the reasoning produced"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_no_failure_sidecar_for_a_real_answer():
    """A run that answered must not be recorded as a failure -- that is the false record."""
    _require_module()
    base = Path(tempfile.mkdtemp(prefix="t1534-d-"))
    try:
        out = _job_dir(base, "jkl90123")
        shutil.rmtree(out.parent)
        ask_q16.write_answer(str(out), {"text": ANSWER_TEXT, "thinking": THINKING_TEXT,
                                        "done_reason": "stop"})
        side = sorted(out.parent.glob("answer.md.failed-*"))
        assert side == [], "a real answer produced failure record(s): %s" % ([str(s) for s in side],)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_empty_answer_keeps_the_prior_answer_and_writes_no_zero_byte_file():
    """Behaviour that must survive (t311, 2026-09-25): never overwrite a good answer with nothing."""
    _require_module()
    base = Path(tempfile.mkdtemp(prefix="t1534-e-"))
    try:
        out = _job_dir(base, "mno23456")
        out.write_text(PRIOR_ANSWER)
        ask_q16.write_answer(str(out), {"text": "", "thinking": THINKING_TEXT,
                                        "done_reason": "length"})
        assert out.read_text() == PRIOR_ANSWER, "an empty result overwrote the prior answer"
        assert out.stat().st_size > 0, "zero-byte answer.md blocks every commit (t311)"
        side = sorted(out.parent.glob("answer.md.failed-*"))
        assert len(side) == 1, "expected exactly one failure sidecar, got %d" % (len(side),)
        assert json.loads(side[0].read_text()).get("done_reason") == "length"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_the_failure_sidecar_survives_a_swept_parent_too():
    """The failure RECORD has the same exposure: a swept dir must not erase the evidence."""
    _require_module()
    base = Path(tempfile.mkdtemp(prefix="t1534-f-"))
    try:
        out = base / "session-review-pqr45678" / "answer.md"   # parent never created
        assert not out.parent.exists()
        got = ask_q16.write_answer(str(out), {"text": "", "thinking": "", "done_reason": "length"})
        p = Path(got)
        assert p.exists(), "empty result produced no record at all: %s" % (got,)
        assert json.loads(p.read_text()).get("done_reason") == "length"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _tests():
    return [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


if __name__ == "__main__":
    fns = _tests()
    bad = 0
    for fn in fns:
        try:
            fn()
            print("ok   %s" % fn.__name__)
        except Exception as exc:
            bad += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
    print("%d/%d passed" % (len(fns) - bad, len(fns)))
    raise SystemExit(1 if bad else 0)
