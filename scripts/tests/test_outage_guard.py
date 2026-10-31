#!/usr/bin/env python3
"""A model-server outage must not destroy answers or hide tasks (2026-09-25 reboot).

The reboot failed 22 manager runs in a minute: each wrote an empty file over a real answer,
and each left a session that made its task read "worked in the last 24h". No model is called.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
import ask_q16  # noqa: E402
import manager_next  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


REFUSED = {"text": "", "complete": False, "done_reason": "timeout",
           "trail": [{"error": "<urlopen error [Errno 111] Connection refused>"}]}


def session(d, answer, minutes):
    f = d / "s.jsonl"
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - minutes * 60))
    f.write_text(json.dumps({"kind": "start", "at": at}) + "\n" + json.dumps(answer) + "\n")
    return f


def main():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        out = d / "t1.md"
        out.write_text("a real answer")
        (d / "t1.md.meta.json").write_text("{}")
        written = ask_q16.write_answer(str(out), REFUSED)
        report("empty run keeps the existing answer", out.read_text(), "a real answer")
        report("empty run keeps the existing meta", (d / "t1.md.meta.json").read_text(), "{}")
        report("empty run is recorded beside it", ".failed-" in written and Path(written).exists(), True)
        fresh = d / "t2.md"
        ask_q16.write_answer(str(fresh), REFUSED)
        report("empty run with no prior answer writes no empty .md", fresh.exists(), False)
        report("  but the run is still recorded beside it",
               any(d.glob("t2.md.failed-*")), True)
        ask_q16.write_answer(str(out), {"text": "new", "complete": True})
        report("a real answer does replace the old one", out.read_text(), "new")

        report("connection refused reads as unreachable", ask_q16.unreachable(REFUSED), True)
        report("HTTP 500 reads as unreachable", ask_q16.unreachable(
            {"text": "", "trail": [{"error": "HTTP Error 500: Internal Server Error"}]}), True)
        report("a cut-off model run is NOT unreachable", ask_q16.unreachable(
            {"text": "", "done_reason": "length", "complete": False}), False)
        report("an answer is never unreachable", ask_q16.unreachable(
            dict(REFUSED, text="x")), False)

        s = d / "sess"
        s.mkdir()
        f = session(s, {"kind": "answer", "text": "", "done_reason": "timeout"}, 1)
        report("instant server failure is not 'worked'", manager_next.server_failure(f), True)
        f = session(s, {"kind": "answer", "text": "", "done_reason": "timeout"}, 60)
        os.utime(f)
        report("a full-length timeout still counts as worked", manager_next.server_failure(f), False)
        f = session(s, {"kind": "answer", "text": "", "done_reason": "length"}, 1)
        report("a model cut-off still counts as worked", manager_next.server_failure(f), False)
        f = session(s, {"kind": "answer", "text": "done", "done_reason": "stop"}, 1)
        report("a real answer counts as worked", manager_next.server_failure(f), False)

    with tempfile.TemporaryDirectory() as tmp:
        s = Path(tmp)
        f = s / "s.jsonl"
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 30))
        f.write_text(json.dumps({"kind": "start", "at": at}) + "\n")
        report("a run that died before asking is not 'worked'", manager_next.server_failure(f), True)
        f = session(s, {"kind": "answer", "text": "", "done_reason": "held"}, 1)
        report("a run that yielded a held lease is not 'worked'", manager_next.server_failure(f), True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
