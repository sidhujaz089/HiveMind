#!/usr/bin/env python3
"""t1423 -- a session review whose transcript is gone retires itself into failed/ at once.

EVIDENCE (the task note, 2026-10-18): workspace/session-reviews/pending/
claude-fa510770-2369-48d8-9f82-e4ee869f8554.json named a transcript under
~/.claude/projects/-home-user-ai-workspace/ that had been deleted. BOTH extract entry points
read the transcript before doing anything else -- scripts/session_review.py:490 (the model path,
`extract`) and scripts/session_review.py:908 (the scripted path, `extract_scripted`, which is the
one main() runs, since SCRIPTED is True at scripts/session_review.py:790) -- and `turns` opens the
path inside the call (scripts/session_review.py:128). So a deleted transcript raises
FileNotFoundError out of the extract call: exit 1 in about one second, the request left in
pending/, and the dispatcher offers it again -- 8 times, by the note.

RED ON MAIN FOR THE MISSING CHANGE: the first two tests call the two extract entry points on a
request whose transcript file does not exist and assert a clean (0, 0) return plus a record in
failed/. There is no missing-transcript handling in scripts/session_review.py, so both calls raise
FileNotFoundError today. The last two tests pass today and must stay passing: they pin the
DESTINATION (failed/, not a new directory) and that a transcript which IS present is still reviewed
and retired to done/.

NO LIVE STATE: every path lives inside a tempfile.TemporaryDirectory; the module's queue
directories, both ledgers and FOR_JAS are repointed at it; `sr._log` is replaced with a no-op so
nothing reaches workspace/session-reviews/ledger.jsonl; `file_task` is a local callable so no task
is minted; the fake asker records calls and returns a canned string, so no model is called; no
subprocess; no fixed /tmp paths.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "scripts" / "tests")]
import session_review as sr  # noqa: E402

SID = "fa510770-2369-48d8-9f82-e4ee869f8554"
TRANSCRIPT_NAME = "fa510770-2369-48d8-9f82-e4ee869f8554.jsonl"


def _sandbox(tmp):
    """Point every queue directory, both ledgers, FOR_JAS and the ledger writer at tmp."""
    tmp = Path(tmp)
    sr.BASE = tmp
    sr.PENDING, sr.DONE, sr.FINDINGS = tmp / "pending", tmp / "done", tmp / "findings"
    sr.FAILED = tmp / "failed"
    sr.FOR_JAS = tmp / "for-jas"
    sr.LEDGER = tmp / "ledger.jsonl"
    sr.PROMISE_LEDGER = tmp / "promise-ledger.jsonl"
    sr._log = lambda row: None          # a red run must not append to the real ledger
    return tmp


def _dead_request(tmp):
    """A pending request naming a transcript that is NOT on disk. -> (name, req_file, gone)."""
    gone = tmp / "claude-projects" / TRANSCRIPT_NAME        # deliberately never created
    name = sr._req_name("claude", SID)
    (tmp / "pending").mkdir(parents=True, exist_ok=True)
    req_file = tmp / "pending" / name
    req_file.write_text(json.dumps({
        "harness": "claude", "id": SID, "path": str(gone), "queued": "2026-10-18T05:32:11Z",
        "from_turn": 0, "to_turn": 2, "ended_epoch": 1792200000}, indent=1) + "\n", encoding="utf-8")
    return name, req_file, gone


def _transcript(tmp, name, n=2):
    """A readable Claude-format transcript of n turns, on disk."""
    lines = [json.dumps({"type": "user" if i % 2 == 0 else "assistant",
                         "message": {"content": f"turn {i} says something plain"}})
             for i in range(n)]
    path = tmp / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _no_model(asked):
    def asker(*a, **k):
        asked.append(a[1] if len(a) > 1 else "")
        return "[]"
    return asker


def _record_reasons(filed):
    def file_task(title, body):
        filed.append((title, body))
        return "tTEST"
    return file_task


def _assert_dead_request_handled(tmp, name, req_file, gone, out, asked, filed):
    """The shared contract: quiet, reason recorded, request out of the queue, one task for Claude."""
    assert out == (0, 0), f"expected the extract call to return (0, 0) so the job exits 0, got {out!r}"
    assert asked == [], (f"the model was asked about a transcript that is not on disk "
                         f"({len(asked)} call(s): {asked!r}) -- 8 restarts of a full model call is the cost")
    assert not req_file.exists(), (f"{name} is still in {tmp / 'pending'} -- the dispatcher offers "
                                   f"pending/ items, so the request would be retried forever")
    dest = tmp / "failed" / name
    assert dest.is_file(), f"{name} did not land in {tmp / 'failed'} (reviewed_to scans THAT directory)"
    rec = json.loads(dest.read_text(encoding="utf-8"))
    assert rec.get("id") == SID, f"the moved record lost the session id: {rec!r}"
    blob = json.dumps(rec)
    assert str(gone) in blob, f"the record does not say WHICH transcript is gone: {rec!r}"
    assert "transcript" in blob.lower(), f"the moved record carries no reason anyone can act on: {rec!r}"
    assert filed, "nothing was filed for Claude: the failure left no trace in the task system"
    assert any(str(gone) in body for _, body in filed), \
        f"no filed task names the missing transcript: {filed!r}"
    assert all("transcript" in title.lower() for title, _ in filed), \
        f"a filed task title that does not say 'transcript': {filed!r}"


def test_the_scripted_path_moves_a_dead_request_to_failed_at_once():
    with tempfile.TemporaryDirectory() as d:
        tmp = _sandbox(d)
        name, req_file, gone = _dead_request(tmp)
        asked, filed = [], []
        out = sr.extract_scripted(req_file, asker=_no_model(asked), file_task=_record_reasons(filed))
        _assert_dead_request_handled(tmp, name, req_file, gone, out, asked, filed)


def test_the_model_path_moves_a_dead_request_to_failed_at_once():
    with tempfile.TemporaryDirectory() as d:
        tmp = _sandbox(d)
        name, req_file, gone = _dead_request(tmp)
        asked, filed = [], []
        out = sr.extract(req_file, asker=_no_model(asked), file_task=_record_reasons(filed))
        _assert_dead_request_handled(tmp, name, req_file, gone, out, asked, filed)


def test_a_given_up_session_is_not_queued_again():
    with tempfile.TemporaryDirectory() as d:
        tmp = _sandbox(d)
        name, req_file, gone = _dead_request(tmp)
        sr.extract_scripted(req_file, asker=_no_model([]), file_task=_record_reasons([]))
        assert list((tmp / "pending").glob("*.json")) == [], \
            "the dead request was not taken out of pending/"
        live = _transcript(tmp, "resumed.jsonl", 2)
        again = sr.enqueue("claude", SID, live)
        assert again is None, (f"the session was queued again as {again!r} -- the give-up record is "
                              "not in the directory reviewed_to() scans (it must be FAILED, BASE/'failed')")


def test_a_bad_model_answer_still_gets_two_attempts():
    with tempfile.TemporaryDirectory() as d:
        tmp = _sandbox(d)
        live = _transcript(tmp, "live.jsonl", 2)
        name, req_file, _ = _dead_request(tmp)
        req_file.write_text(json.dumps({**json.loads(req_file.read_text(encoding="utf-8")),
                                       "path": str(live)}, indent=1) + "\n", encoding="utf-8")
        filer = _record_reasons([])
        assert sr._unusable(req_file, "part 1/1: no JSON list in the answer", "review the session",
                           filer) is False, \
            "an unusable ANSWER moved to failed/ after one try: the one-strike rule is for a transcript "\
            "that is gone, not for a model that replied badly"
        rec = json.loads(req_file.read_text(encoding="utf-8"))
        assert len(rec.get("unusable", [])) == 1, f"expected one recorded attempt, got {rec.get('unusable')!r}"
        assert sr._unusable(req_file, "part 1/1: no JSON list in the answer", "review the session",
                           filer) is True, "the second unusable answer did not give up on the item"
        assert not req_file.exists() and (tmp / "failed" / name).is_file()


def test_a_transcript_that_is_present_is_still_reviewed_and_retired():
    with tempfile.TemporaryDirectory() as d:
        tmp = _sandbox(d)
        live = _transcript(tmp, TRANSCRIPT_NAME, 2)
        name, req_file, _ = _dead_request(tmp)
        req_file.write_text(json.dumps({**json.loads(req_file.read_text(encoding="utf-8")),
                                       "path": str(live)}, indent=1) + "\n", encoding="utf-8")
        out = sr.extract_scripted(req_file, asker=_no_model([]), file_task=_record_reasons([]))
        assert isinstance(out, tuple) and len(out) == 2, f"the contract is (new, covered), got {out!r}"
        assert not req_file.exists(), "a request whose transcript was present was left in pending/"
        dest = tmp / "done" / name
        assert dest.is_file(), f"a reviewed request did not land in {tmp / 'done'}: {name}"
        assert json.loads(dest.read_text(encoding="utf-8")).get("id") == SID


if __name__ == "__main__":
    _tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    _bad = []
    for _name, _fn in _tests:
        try:
            _fn()
            print(f"  ok   {_name}")
        except Exception as _e:      # noqa: BLE001 -- a failing test is a finding, not a crash
            _bad.append(_name)
            print(f"  FAIL {_name}: {type(_e).__name__}: {_e}")
    print(f"{len(_tests) - len(_bad)}/{len(_tests)} passed")
    sys.exit(1 if _bad else 0)
