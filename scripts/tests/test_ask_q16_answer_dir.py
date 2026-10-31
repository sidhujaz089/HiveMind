"""t1579: a model answer must survive a caller whose temp directory is gone.

The RCA for session-review finding c8482e186f43 answered TWICE on 2026-10-24. The two
dispatch logs under workspace/run-logs/dispatch/ show:

    c8482e186f43.json--rca--swift-junior_latest--1792863562.log
      -> done_reason=stop 22 min 20504 chars (11 tool round(s))
    c8482e186f43.json--rca--swift--1792868826.log
      -> done_reason=stop 26 min 26964 chars (11 tool round(s))

and both end in the same crash, the last frame of which is scripts/ask_q16.py:517, inside
write_answer:

    FileNotFoundError: [Errno 2] No such file or directory:
      '/home/user/.cache/hivemind-tmp/session-review-h2pjce2k/answer.md'

The model produced the answer; the writer lost it. The caller (scripts/session_review.py)
hands ask_q16 an OUT inside ~/.cache/hivemind-tmp/session-review-<rand>/, and a long run
outlives that directory. ask_q16.write_answer opens OUT with o.write_text(r["text"]) and
never creates the parent, so a 22-minute answer is destroyed at the last step and the job
is recorded as "no answer".

RED on main for that reason and nothing else: a write into a directory that has been
removed raises FileNotFoundError (test 1) and leaves no .failed-<utc> sidecar behind for a
truncated run (test 2). It asserts the file on disk, never the return value, because the
tail of write_answer is not modelled here.
"""
import importlib.util
import json
import pathlib
import tempfile


def _ask_q16():
    root = pathlib.Path(__file__).resolve().parents[2]
    p = root / "scripts" / "ask_q16.py"
    if not p.exists():
        raise AssertionError(f"missing file: {p} (repo root resolved to {root})")
    spec = importlib.util.spec_from_file_location("ask_q16_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "write_answer"):
        raise AssertionError("scripts/ask_q16.py has no write_answer to test")
    return mod


def test_answer_written_when_the_callers_directory_is_gone():
    """The incident: the caller's temp dir is deleted while the model is still generating."""
    m = _ask_q16()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        out = root / "session-review-h2pjce2k" / "answer.md"
        assert not out.parent.exists(), "fixture must start with the directory gone"
        r = {"text": "THE RCA ANSWER BODY",
             "thinking": "its reasoning, hours of model time",
             "done_reason": "stop", "seconds": 1320}
        m.write_answer(str(out), r)
        assert out.exists(), (
            "the answer file was not written when its parent directory was missing -- "
            "this is the loss recorded in the t1579 dispatch logs")
        assert out.read_text(encoding="utf-8") == "THE RCA ANSWER BODY", \
            "the answer on disk is not what the model produced"
        thinking = out.parent / "answer.md.thinking.md"
        assert thinking.exists(), "the reasoning of a long answer was not kept"
        assert thinking.read_text(encoding="utf-8") == "its reasoning, hours of model time", \
            "the reasoning file does not carry the model's thinking"
        meta_path = out.parent / "answer.md.meta.json"
        assert meta_path.exists(), "the run's outcome was not recorded next to the answer"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta.get("done_reason") == "stop", f"meta.json lost done_reason: {meta!r}"
        assert "text" not in meta, f"meta.json must not carry the answer body: {meta!r}"


def test_truncated_run_records_a_sidecar_in_a_missing_directory():
    """An empty answer leaves no empty .md (t311) -- it must still leave a record."""
    m = _ask_q16()
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        out = root / "session-review-j261d_7h" / "answer.md"
        r = {"text": "", "done_reason": "length", "seconds": 90}
        m.write_answer(str(out), r)
        assert not out.exists(), \
            "an empty answer must not be written as an empty .md (t311)"
        assert out.parent.exists(), \
            "a truncated run left no directory at all -- the outcome has nowhere to live"
        sides = sorted(p for p in out.parent.glob("answer.md.failed-*"))
        assert len(sides) == 1, (
            f"a truncated run must leave exactly one .failed-<utc> sidecar beside the "
            f"answer, found {[p.name for p in sides]}")
        rec = json.loads(sides[0].read_text(encoding="utf-8"))
        assert rec.get("done_reason") == "length", f"the sidecar lost the reason: {rec!r}"
        assert "text" not in rec, f"the sidecar must not carry an answer body: {rec!r}"


def test_the_normal_path_still_writes_the_answer_and_nothing_else():
    """The existing behaviour the fix must not disturb: an existing directory, full text."""
    m = _ask_q16()
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "answer.md"
        r = {"text": "PLAIN ANSWER", "done_reason": "stop"}
        m.write_answer(str(out), r)
        assert out.read_text(encoding="utf-8") == "PLAIN ANSWER", \
            "the normal write path changed the answer text"
        strays = sorted(p.name for p in pathlib.Path(td).iterdir())
        assert strays == ["answer.md", "answer.md.meta.json"], \
            f"unexpected files in the answer directory: {strays}"


if __name__ == "__main__":
    tests = [
        test_answer_written_when_the_callers_directory_is_gone,
        test_truncated_run_records_a_sidecar_in_a_missing_directory,
        test_the_normal_path_still_writes_the_answer_and_nothing_else,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as e:                      # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
