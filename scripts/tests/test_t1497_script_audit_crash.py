#!/usr/bin/env python3
# t1497: a scripts/script_audit.py run() pass that dies inside the loop window (run_loop's
# score, or the worktree/commit block) must leave a `step: "crash"` ledger row carrying the
# error and the edits it was proposing, and must retire the request -- instead of losing both
# and silently re-running (pass 1 of claude-a750672f: 10 edits proposed, then no loop, no end).
#
# Fail-first: on main there is no `step: "crash"` row and the request is still in pending.
# Run: python3 scripts/tests/test_t1497_script_audit_crash.py   (no pytest; __main__ exits 1 on failure)
# No model call, no live server, no real git worktree (subprocess.run is faked), no process left.
import json
import subprocess
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "scripts")]

import script_audit
import session_review
import session_review_trial as trial


FAKE_ASKER = ["{\"quote\": \"the review left the queue task open\", \"kind\": \"deferral\", \"why_missed\": \"no task was minted\"}"]


def _fake_asker(prompt, purpose):
    return json.dumps(FAKE_ASKER)


def _raise_score(gold, root=None):
    raise RuntimeError("score child failed: boom")


FAKE_EDIT = {"file": "scripts/semantic.py", "old": "the-old-text", "new": "the-new-text",
             "why": "close the only difference"}


def _fake_propose(*a, **k):
    return [dict(FAKE_EDIT)]


def _fake_run(cmd, **kw):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_crash_leaves_row_and_retires_request():
    saved = {n: getattr(script_audit, n) for n in
             ("LEDGER", "GOLD", "DONE_REQ", "PENDING", "session_score", "set_score",
              "propose_edits", "score_here", "tempfile")}
    saved_trial = trial.request_path
    saved_sub = subprocess.run
    saved_sr = {n: getattr(session_review, n) for n in
                ("turns", "chunks", "flow_summary", "open_tasks", "done_tasks")}
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        pending, done, gold_dir = td / "pending", td / "done", td / "gold"
        for d in (pending, done, gold_dir):
            d.mkdir()
        ledger = td / "script-audits.jsonl"
        tmp_req = td / "session-request.json"
        tmp_req.write_text(json.dumps({"harness": "claude", "id": "deadbeef00", "path": "x"}) + "\n", encoding="utf-8")
        req_path = pending / "20260101T000000Z-fake.json"
        req_path.write_text(json.dumps({"at": "20260101", "session_request": "workspace/session-reviews/done/fake-session.json",
                                        "requested_by": "oracle"}) + "\n", encoding="utf-8")
        try:
            script_audit.LEDGER = ledger
            script_audit.GOLD = gold_dir
            script_audit.DONE_REQ = done
            script_audit.PENDING = pending
            script_audit.session_score = _raise_score
            script_audit.set_score = _raise_score
            script_audit.propose_edits = _fake_propose
            script_audit.score_here = lambda g: {"real": 1, "real_surfaced": 0, "flagged": 1, "noise": 1,
                                                 "unsurfaced": [{"id": "M1", "about": "a", "found": "", "said": "unresolved"}],
                                                 "noise_rows": [{"quote": "n", "type": "t"}]}
            script_audit.tempfile = types.SimpleNamespace(mkdtemp=lambda prefix="": str(td / "sandbox"))
            trial.request_path = lambda s: tmp_req
            subprocess.run = _fake_run
            session_review.turns = lambda harness, path: [{}]
            session_review.chunks = lambda ts: ["part one"]
            session_review.flow_summary = lambda: "flow"
            session_review.open_tasks = lambda: "none"
            session_review.done_tasks = lambda: "none"
            error = None
            try:
                script_audit.run(req_path, "oracle-model", asker=_fake_asker)
            except Exception as e:  # noqa: BLE001 -- the crash must propagate, not be swallowed
                error = e
            assert error is not None, "run() must propagate the loop-window crash, not swallow it"
            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
            steps = [r.get("step") for r in rows]
            assert "read" in steps and "compare" in steps and "edits" in steps, f"setup rows missing: {steps}"
            assert "loop" not in steps, f"the crash is simulated before the first loop row; got: {steps}"
            crash_rows = [r for r in rows if r.get("step") == "crash"]
            assert crash_rows, f"no step:crash row after the crash; steps={steps}; error={error}"
            assert "boom" in crash_rows[0].get("error", ""), f"crash row missing the error text: {crash_rows[0]}"
            got_edits = crash_rows[0].get("edits")
            assert got_edits and got_edits[0].get("why") == FAKE_EDIT["why"], f"crash row did not record the edits: {crash_rows[0]}"
            assert not req_path.exists(), "the request was left in pending after a crash"
            assert (done / req_path.name).exists(), "the request was not retired to done after a crash"
        finally:
            for n, v in saved.items():
                setattr(script_audit, n, v)
            trial.request_path = saved_trial
            subprocess.run = saved_sub
            for n, v in saved_sr.items():
                setattr(session_review, n, v)


def main():
    failures = 0
    for fn in (test_crash_leaves_row_and_retires_request,):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    if failures:
        print(f"{failures} test(s) failed")
        sys.exit(1)
    print("ok")


if __name__ == "__main__":
    main()
